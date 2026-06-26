"""CalendarClient — Google Calendar API client reusing the Gmail OAuth credentials.

Reads events for a given day and normalises them to an internal dict format.
Shares the same OAuth client (client_id/secret/refresh_token) as ``GmailClient``;
the refresh token must be authorised with the ``calendar.readonly`` scope.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.calendar_client", "logs/app.log")

BASE_URL = "https://www.googleapis.com/calendar/v3/calendars/primary"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class CalendarClient:
    """Read-only Google Calendar client.

    Parameters
    ----------
    client_id, client_secret, refresh_token:
        OAuth2 credentials (same as ``GmailClient``).
    user_email:
        Owner of the calendar — used to detect "self" even when Google does not
        flag an attendee with ``self: true``.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        user_email: str,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.user_email = user_email

        self._access_token: str | None = None
        self._http = httpx.Client(timeout=30.0)

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _refresh_access_token(self) -> str:
        resp = self._http.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        token = resp.json()["access_token"]
        self._access_token = token
        return token

    def _headers(self) -> dict[str, str]:
        if not self._access_token:
            self._refresh_access_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        _retries: int = 3,
        _backoff: float = 1.0,
        **kwargs,
    ) -> httpx.Response:
        """Authenticated request: refresh on 401, retry on 5xx with backoff."""
        url = f"{BASE_URL}{path}" if path.startswith("/") else path
        resp = self._http.request(method, url, headers=self._headers(), **kwargs)
        if resp.status_code == 401:
            self._refresh_access_token()
            resp = self._http.request(method, url, headers=self._headers(), **kwargs)

        attempt = 0
        while resp.status_code >= 500 and attempt < _retries:
            delay = _backoff * (2**attempt)
            log.warning(
                "Calendar API %s %s returned %s, retrying in %.1fs (%d/%d)",
                method,
                path,
                resp.status_code,
                delay,
                attempt + 1,
                _retries,
            )
            time.sleep(delay)
            resp = self._http.request(method, url, headers=self._headers(), **kwargs)
            attempt += 1

        resp.raise_for_status()
        return resp

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_events_for_day(self, day: date, tz: str) -> list[dict]:
        """Return normalised events that occur on *day* in timezone *tz*."""
        zone = ZoneInfo(tz)
        start = datetime(day.year, day.month, day.day, tzinfo=zone)
        end = start + timedelta(days=1)
        resp = self._request(
            "GET",
            "/events",
            params={
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeZone": tz,
            },
        )
        items = resp.json().get("items", [])
        return [_normalise_event(item, self.user_email) for item in items]


# ------------------------------------------------------------------
# Normalisation helpers (pure functions)
# ------------------------------------------------------------------


def _is_resource(att: dict) -> bool:
    """True if the attendee is a room/resource rather than a person."""
    return bool(att.get("resource", False))


def _normalise_attendee(att: dict, user_email: str) -> dict:
    email = att.get("email", "")
    is_self = bool(att.get("self", False)) or email.lower() == user_email.lower()
    return {
        "email": email,
        "name": att.get("displayName", ""),
        "response": att.get("responseStatus", ""),
        "is_self": is_self,
        "is_resource": _is_resource(att),
    }


def _extract_meet_link(raw: dict) -> str:
    """Prefer hangoutLink, then a video conferenceData entry point, else ''."""
    if raw.get("hangoutLink"):
        return raw["hangoutLink"]
    for entry in raw.get("conferenceData", {}).get("entryPoints", []):
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            return entry["uri"]
    return ""


def _event_datetime(node: dict) -> datetime | None:
    """Parse a Calendar start/end node into an aware datetime, or None if all-day."""
    dt_value = node.get("dateTime")
    if not dt_value:
        return None
    return datetime.fromisoformat(dt_value)


def _my_response(raw: dict, attendees: list[dict], user_email: str) -> str:
    """Resolve the owner's responseStatus.

    Falls back to ``accepted`` when the owner is the organizer but is not listed
    as an attendee (Google sometimes omits the organizer from ``attendees``).
    """
    for att in attendees:
        if att["is_self"]:
            return att["response"]
    organizer = raw.get("organizer", {})
    if organizer.get("self") or organizer.get("email", "").lower() == user_email.lower():
        return "accepted"
    return ""


def _normalise_event(raw: dict, user_email: str) -> dict:
    start_node = raw.get("start", {})
    end_node = raw.get("end", {})
    all_day = "date" in start_node and "dateTime" not in start_node

    attendees = [_normalise_attendee(a, user_email) for a in raw.get("attendees", [])]
    organizer = raw.get("organizer", {})

    return {
        "id": raw.get("id", ""),
        "ical_uid": raw.get("iCalUID", ""),
        "recurring_event_id": raw.get("recurringEventId", ""),
        "original_start": _event_datetime(raw.get("originalStartTime", {})),
        "status": raw.get("status", ""),
        "summary": raw.get("summary", ""),
        "start": None if all_day else _event_datetime(start_node),
        "end": None if all_day else _event_datetime(end_node),
        "all_day": all_day,
        "location": raw.get("location", ""),
        "meet_link": _extract_meet_link(raw),
        "organizer": {
            "name": organizer.get("displayName", ""),
            "email": organizer.get("email", ""),
        },
        "my_response": _my_response(raw, attendees, user_email),
        "attendees": attendees,
        "attendees_omitted": bool(raw.get("attendeesOmitted", False)),
    }
