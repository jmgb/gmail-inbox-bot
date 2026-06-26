"""Tests for CalendarClient — auth, request building and event normalisation."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from gmail_inbox_bot.calendar_client import (
    CalendarClient,
    _extract_meet_link,
    _is_resource,
    _my_response,
    _normalise_attendee,
    _normalise_event,
)

USER = "jesus82c@gmail.com"

RAW_TIMED_EVENT = {
    "id": "evt1",
    "iCalUID": "uid-evt1@google.com",
    "status": "confirmed",
    "summary": "1:1 con Ana",
    "location": "Oficina",
    "hangoutLink": "https://meet.google.com/abc-defg-hij",
    "start": {"dateTime": "2026-06-26T10:00:00+02:00", "timeZone": "Europe/Madrid"},
    "end": {"dateTime": "2026-06-26T10:30:00+02:00", "timeZone": "Europe/Madrid"},
    "organizer": {"email": USER, "displayName": "Jesus", "self": True},
    "attendees": [
        {
            "email": USER,
            "displayName": "Jesus",
            "responseStatus": "accepted",
            "self": True,
            "organizer": True,
        },
        {"email": "ana@example.com", "displayName": "Ana", "responseStatus": "needsAction"},
    ],
}

RAW_ALLDAY_EVENT = {
    "id": "evt2",
    "iCalUID": "uid-evt2@google.com",
    "status": "confirmed",
    "summary": "Cumpleaños",
    "start": {"date": "2026-06-26"},
    "end": {"date": "2026-06-27"},
    "organizer": {"email": USER, "self": True},
    "attendees": [],
}


# ------------------------------------------------------------------
# _normalise_attendee
# ------------------------------------------------------------------


class TestNormaliseAttendee:
    def test_basic_fields(self):
        att = _normalise_attendee(
            {"email": "ana@example.com", "displayName": "Ana", "responseStatus": "accepted"},
            USER,
        )
        assert att["email"] == "ana@example.com"
        assert att["name"] == "Ana"
        assert att["response"] == "accepted"
        assert att["is_self"] is False
        assert att["is_resource"] is False

    def test_is_self_by_flag(self):
        att = _normalise_attendee({"email": "x@y.com", "self": True}, USER)
        assert att["is_self"] is True

    def test_is_self_by_email_match_case_insensitive(self):
        att = _normalise_attendee({"email": "JESUS82C@gmail.com"}, USER)
        assert att["is_self"] is True

    def test_resource_flag(self):
        att = _normalise_attendee(
            {"email": "room@resource.calendar.google.com", "resource": True}, USER
        )
        assert att["is_resource"] is True


class TestIsResource:
    def test_true(self):
        assert _is_resource({"resource": True}) is True

    def test_false_when_absent(self):
        assert _is_resource({"email": "a@b.com"}) is False


# ------------------------------------------------------------------
# _extract_meet_link
# ------------------------------------------------------------------


class TestExtractMeetLink:
    def test_hangout_link_preferred(self):
        raw = {"hangoutLink": "https://meet.google.com/abc"}
        assert _extract_meet_link(raw) == "https://meet.google.com/abc"

    def test_conference_data_video_entry_point(self):
        raw = {
            "conferenceData": {
                "entryPoints": [
                    {"entryPointType": "phone", "uri": "tel:+1234"},
                    {"entryPointType": "video", "uri": "https://meet.google.com/xyz"},
                ]
            }
        }
        assert _extract_meet_link(raw) == "https://meet.google.com/xyz"

    def test_none_returns_empty(self):
        assert _extract_meet_link({"summary": "x"}) == ""


# ------------------------------------------------------------------
# _my_response
# ------------------------------------------------------------------


class TestMyResponse:
    def test_from_self_attendee(self):
        attendees = [
            {"email": USER, "response": "tentative", "is_self": True, "is_resource": False},
            {"email": "a@b.com", "response": "accepted", "is_self": False, "is_resource": False},
        ]
        assert _my_response(RAW_TIMED_EVENT, attendees, USER) == "tentative"

    def test_organizer_without_self_attendee_defaults_accepted(self):
        raw = {"organizer": {"email": USER, "self": True}, "attendees": []}
        attendees = [
            {"email": "a@b.com", "response": "accepted", "is_self": False, "is_resource": False}
        ]
        assert _my_response(raw, attendees, USER) == "accepted"

    def test_not_attendee_not_organizer_returns_empty(self):
        raw = {"organizer": {"email": "someone@else.com"}, "attendees": []}
        attendees = [
            {"email": "a@b.com", "response": "accepted", "is_self": False, "is_resource": False}
        ]
        assert _my_response(raw, attendees, USER) == ""


# ------------------------------------------------------------------
# _normalise_event
# ------------------------------------------------------------------


class TestNormaliseEvent:
    def test_basic_fields(self):
        ev = _normalise_event(RAW_TIMED_EVENT, USER)
        assert ev["id"] == "evt1"
        assert ev["ical_uid"] == "uid-evt1@google.com"
        assert ev["status"] == "confirmed"
        assert ev["summary"] == "1:1 con Ana"
        assert ev["location"] == "Oficina"
        assert ev["meet_link"] == "https://meet.google.com/abc-defg-hij"

    def test_timed_event_has_aware_start(self):
        ev = _normalise_event(RAW_TIMED_EVENT, USER)
        assert ev["all_day"] is False
        assert isinstance(ev["start"], datetime)
        assert ev["start"].tzinfo is not None

    def test_my_response_resolved(self):
        ev = _normalise_event(RAW_TIMED_EVENT, USER)
        assert ev["my_response"] == "accepted"

    def test_attendees_normalised(self):
        ev = _normalise_event(RAW_TIMED_EVENT, USER)
        assert len(ev["attendees"]) == 2
        me = [a for a in ev["attendees"] if a["is_self"]]
        assert len(me) == 1
        assert me[0]["email"] == USER

    def test_all_day_event(self):
        ev = _normalise_event(RAW_ALLDAY_EVENT, USER)
        assert ev["all_day"] is True
        assert ev["start"] is None

    def test_organizer_extracted(self):
        ev = _normalise_event(RAW_TIMED_EVENT, USER)
        assert ev["organizer"]["email"] == USER


# ------------------------------------------------------------------
# CalendarClient.list_events_for_day — request building
# ------------------------------------------------------------------


def _make_client(**overrides) -> CalendarClient:
    defaults = {
        "client_id": "cid",
        "client_secret": "secret",
        "refresh_token": "rt",
        "user_email": USER,
    }
    defaults.update(overrides)
    return CalendarClient(**defaults)


class TestListEventsForDay:
    @pytest.fixture
    def mock_http(self):
        c = _make_client()
        mock = MagicMock()
        c._http = mock
        c._access_token = "fake-token"
        return c, mock

    def test_builds_request_params(self, mock_http):
        from datetime import date

        client, mock = mock_http
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"items": [RAW_TIMED_EVENT]}
        resp.raise_for_status = MagicMock()
        mock.request.return_value = resp

        events = client.list_events_for_day(date(2026, 6, 26), "Europe/Madrid")

        call = mock.request.call_args
        params = call.kwargs.get("params") or call[1].get("params")
        assert params["singleEvents"] == "true"
        assert params["orderBy"] == "startTime"
        # timeMin is start of day, timeMax start of next day, both in Madrid offset
        assert params["timeMin"].startswith("2026-06-26T00:00:00")
        assert params["timeMax"].startswith("2026-06-27T00:00:00")
        assert len(events) == 1
        assert events[0]["id"] == "evt1"
