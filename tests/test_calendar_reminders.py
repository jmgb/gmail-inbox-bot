"""Tests for calendar_reminders — filtering, recipients and dedupe keys."""

from datetime import date, datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from gmail_inbox_bot.calendar_reminders import (
    ReminderState,
    dedupe_key,
    enabled_mailboxes,
    event_qualifies,
    process_mailbox,
    reminder_recipients,
    render_reminder,
    run_once,
    should_send,
)

MADRID = ZoneInfo("Europe/Madrid")
USER = "jesus82c@gmail.com"


def _att(email, response="accepted", is_self=False, is_resource=False, name=""):
    return {
        "email": email,
        "name": name or email,
        "response": response,
        "is_self": is_self,
        "is_resource": is_resource,
    }


def _ev(**overrides):
    base = {
        "id": "evt1",
        "ical_uid": "uid-evt1@google.com",
        "status": "confirmed",
        "summary": "1:1 con Ana",
        "start": datetime(2026, 6, 26, 10, 0, tzinfo=MADRID),
        "all_day": False,
        "my_response": "accepted",
        "attendees_omitted": False,
        "attendees": [
            _att(USER, is_self=True),
            _att("ana@example.com", response="needsAction"),
        ],
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------------
# event_qualifies
# ------------------------------------------------------------------


class TestEventQualifies:
    def test_one_guest_qualifies(self):
        assert event_qualifies(_ev(), max_attendees=2) is True

    def test_two_guests_qualify(self):
        ev = _ev(
            attendees=[
                _att(USER, is_self=True),
                _att("ana@example.com"),
                _att("bob@example.com"),
            ]
        )
        assert event_qualifies(ev, max_attendees=2) is True

    def test_three_guests_do_not_qualify(self):
        ev = _ev(
            attendees=[
                _att(USER, is_self=True),
                _att("a@x.com"),
                _att("b@x.com"),
                _att("c@x.com"),
            ]
        )
        assert event_qualifies(ev, max_attendees=2) is False

    def test_only_me_does_not_qualify(self):
        ev = _ev(attendees=[_att(USER, is_self=True)])
        assert event_qualifies(ev, max_attendees=2) is False

    def test_no_attendees_does_not_qualify(self):
        ev = _ev(attendees=[])
        assert event_qualifies(ev, max_attendees=2) is False

    def test_all_day_does_not_qualify(self):
        assert event_qualifies(_ev(all_day=True, start=None), max_attendees=2) is False

    def test_cancelled_does_not_qualify(self):
        assert event_qualifies(_ev(status="cancelled"), max_attendees=2) is False

    def test_my_declined_does_not_qualify(self):
        assert event_qualifies(_ev(my_response="declined"), max_attendees=2) is False

    def test_attendees_omitted_does_not_qualify(self):
        assert event_qualifies(_ev(attendees_omitted=True), max_attendees=2) is False

    def test_resource_does_not_count_as_guest(self):
        ev = _ev(
            attendees=[
                _att(USER, is_self=True),
                _att("room@resource.calendar.google.com", is_resource=True),
            ]
        )
        # Only a room besides me → no human guests → does not qualify
        assert event_qualifies(ev, max_attendees=2) is False


# ------------------------------------------------------------------
# reminder_recipients
# ------------------------------------------------------------------


class TestReminderRecipients:
    def test_excludes_self(self):
        recipients = reminder_recipients(_ev())
        assert all(not r["is_self"] for r in recipients)

    def test_excludes_declined(self):
        ev = _ev(
            attendees=[
                _att(USER, is_self=True),
                _att("ana@example.com", response="accepted"),
                _att("bob@example.com", response="declined"),
            ]
        )
        emails = {r["email"] for r in reminder_recipients(ev)}
        assert emails == {"ana@example.com"}

    def test_excludes_resources(self):
        ev = _ev(
            attendees=[
                _att(USER, is_self=True),
                _att("ana@example.com"),
                _att("room@resource.calendar.google.com", is_resource=True),
            ]
        )
        emails = {r["email"] for r in reminder_recipients(ev)}
        assert emails == {"ana@example.com"}

    def test_excludes_attendees_without_email(self):
        ev = _ev(
            attendees=[
                _att(USER, is_self=True),
                _att(""),
                _att("ana@example.com"),
            ]
        )
        emails = {r["email"] for r in reminder_recipients(ev)}
        assert emails == {"ana@example.com"}

    def test_includes_tentative_and_needs_action(self):
        ev = _ev(
            attendees=[
                _att(USER, is_self=True),
                _att("a@x.com", response="tentative"),
                _att("b@x.com", response="needsAction"),
            ]
        )
        emails = {r["email"] for r in reminder_recipients(ev)}
        assert emails == {"a@x.com", "b@x.com"}


# ------------------------------------------------------------------
# dedupe_key
# ------------------------------------------------------------------


class TestDedupeKey:
    def test_uses_ical_uid(self):
        key = dedupe_key(_ev(), "ana@example.com", date(2026, 6, 26), "Europe/Madrid")
        assert "uid-evt1@google.com" in key
        assert key.startswith("2026-06-26:")

    def test_falls_back_to_id(self):
        ev = _ev(ical_uid="")
        key = dedupe_key(ev, "ana@example.com", date(2026, 6, 26), "Europe/Madrid")
        assert "evt1" in key

    def test_lowercases_invitee(self):
        key = dedupe_key(_ev(), "ANA@Example.com", date(2026, 6, 26), "Europe/Madrid")
        assert "ana@example.com" in key
        assert "ANA@Example.com" not in key

    def test_same_meeting_same_invitee_same_key(self):
        k1 = dedupe_key(_ev(), "ana@example.com", date(2026, 6, 26), "Europe/Madrid")
        k2 = dedupe_key(_ev(id="other-id"), "ana@example.com", date(2026, 6, 26), "Europe/Madrid")
        # ical_uid is stable across calendars → same key even if event id differs
        assert k1 == k2


# ------------------------------------------------------------------
# ReminderState
# ------------------------------------------------------------------

DAY = date(2026, 6, 26)


class TestReminderState:
    def test_load_missing_returns_empty(self, tmp_path):
        state = ReminderState.load(tmp_path / "nope.json")
        assert state.already_sent("any-key") is False
        assert state.ran_today("jesus82c", DAY) is False

    def test_record_and_already_sent(self):
        state = ReminderState.load_data({})
        state.record_sent(
            DAY, "k1", "jesus82c", "evt1", "ana@example.com", "2026-06-26T09:00:00+02:00"
        )
        assert state.already_sent("k1") is True
        assert state.already_sent("other") is False

    def test_already_sent_is_global_across_dates(self):
        state = ReminderState.load_data(
            {"sent": {"2026-06-25": [{"key": "old-key"}]}, "last_run_date": {}}
        )
        assert state.already_sent("old-key") is True

    def test_mark_ran_and_ran_today(self):
        state = ReminderState.load_data({})
        assert state.ran_today("jesus82c", DAY) is False
        state.mark_ran("jesus82c", DAY)
        assert state.ran_today("jesus82c", DAY) is True
        # other mailbox unaffected
        assert state.ran_today("miguel", DAY) is False

    def test_save_is_atomic_and_roundtrips(self, tmp_path):
        path = tmp_path / "state.json"
        state = ReminderState.load_data({})
        state.record_sent(
            DAY, "k1", "jesus82c", "evt1", "ana@example.com", "2026-06-26T09:00:00+02:00"
        )
        state.mark_ran("jesus82c", DAY)
        state.save(path)

        # No leftover temp file
        assert not (tmp_path / "state.json.tmp").exists()

        reloaded = ReminderState.load(path)
        assert reloaded.already_sent("k1") is True
        assert reloaded.ran_today("jesus82c", DAY) is True

    def test_corrupt_file_strict_raises(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError):
            ReminderState.load(path, strict=True)

    def test_corrupt_file_non_strict_returns_empty(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{not valid json", encoding="utf-8")
        state = ReminderState.load(path, strict=False)
        assert state.already_sent("x") is False

    def test_purge_keeps_recent_dates(self):
        state = ReminderState.load_data(
            {
                "sent": {
                    "2026-06-20": [{"key": "old"}],
                    "2026-06-26": [{"key": "today"}],
                },
                "last_run_date": {},
            }
        )
        state.purge_old(keep_from=date(2026, 6, 25))
        assert state.already_sent("today") is True
        assert state.already_sent("old") is False


# ------------------------------------------------------------------
# render_reminder
# ------------------------------------------------------------------

CONFIG = {
    "name": "jesus82c",
    "email": "jesus82c@gmail.com",
    "calendar_reminders": {"enabled": True, "max_attendees": 2, "timezone": "Europe/Madrid"},
}


class TestRenderReminder:
    def _invitee(self):
        return _att("ana@example.com", name="Ana")

    def test_subject_has_title_and_time(self):
        subject, _ = render_reminder(_ev(), self._invitee(), "Jesus", CONFIG, "Europe/Madrid")
        assert "1:1 con Ana" in subject
        assert "10:00" in subject

    def test_body_has_core_fields(self):
        _, html = render_reminder(
            _ev(location="Sala A"), self._invitee(), "Jesus", CONFIG, "Europe/Madrid"
        )
        assert "Ana" in html
        assert "1:1 con Ana" in html
        assert "10:00" in html
        assert "Sala A" in html

    def test_no_marketing_footer(self):
        # Reminders should read as a personal message, not an automation.
        _, html = render_reminder(_ev(), self._invitee(), "Miguel", CONFIG, "Europe/Madrid")
        assert "aiship.co" not in html
        assert "AI assistant" not in html

    def test_greeting_uses_real_name(self):
        _, html = render_reminder(
            _ev(), _att("ana@example.com", name="Ana"), "Miguel", CONFIG, "Europe/Madrid"
        )
        assert "Hola Ana" in html

    def test_greeting_without_name_avoids_showing_email(self):
        invitee = _att("bob@example.com")  # no display name → falls back to email
        _, html = render_reminder(_ev(), invitee, "Miguel", CONFIG, "Europe/Madrid")
        assert "bob@example.com" not in html

    def test_signoff_uses_sender_name(self):
        _, html = render_reminder(_ev(), self._invitee(), "Miguel", CONFIG, "Europe/Madrid")
        assert "Miguel" in html

    def test_omits_location_line_when_empty(self):
        ev = _ev(location="", meet_link="")
        _, html = render_reminder(ev, self._invitee(), "Jesus", CONFIG, "Europe/Madrid")
        # No placeholder labels when there is nothing to show
        assert "None" not in html

    def test_escapes_untrusted_html_in_calendar_fields(self):
        ev = _ev(summary="<script>alert(1)</script>", location="<b>x</b>")
        invitee = _att("ana@example.com", name="<i>Ana</i>")
        _, html = render_reminder(ev, invitee, "Jesus", CONFIG, "Europe/Madrid")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "<b>x</b>" not in html


# ------------------------------------------------------------------
# process_mailbox (orchestration)
# ------------------------------------------------------------------


class TestProcessMailbox:
    def _calendar(self, events):
        cal = MagicMock()
        cal.list_events_for_day.return_value = events
        return cal

    def test_sends_to_qualifying_event(self):
        gmail = MagicMock()
        cal = self._calendar([_ev()])
        state = ReminderState.load_data({})

        process_mailbox(gmail, cal, CONFIG, state, day=DAY, sent_at="2026-06-26T09:00:00+02:00")

        gmail.send_email.assert_called_once()
        _, kwargs = gmail.send_email.call_args
        args = gmail.send_email.call_args.args
        # to_address is ana
        assert "ana@example.com" in args or "ana@example.com" in kwargs.values()

    def test_records_state_after_send(self):
        gmail = MagicMock()
        cal = self._calendar([_ev()])
        state = ReminderState.load_data({})
        process_mailbox(gmail, cal, CONFIG, state, day=DAY, sent_at="t")
        key = dedupe_key(_ev(), "ana@example.com", DAY, "Europe/Madrid")
        assert state.already_sent(key) is True

    def test_skips_already_sent(self):
        gmail = MagicMock()
        cal = self._calendar([_ev()])
        state = ReminderState.load_data({})
        key = dedupe_key(_ev(), "ana@example.com", DAY, "Europe/Madrid")
        state.record_sent(DAY, key, "jesus82c", "evt1", "ana@example.com", "t")

        process_mailbox(gmail, cal, CONFIG, state, day=DAY, sent_at="t")
        gmail.send_email.assert_not_called()

    def test_dry_run_does_not_send_or_record(self):
        gmail = MagicMock()
        cal = self._calendar([_ev()])
        state = ReminderState.load_data({})
        process_mailbox(gmail, cal, CONFIG, state, day=DAY, sent_at="t", dry_run=True)
        gmail.send_email.assert_not_called()
        key = dedupe_key(_ev(), "ana@example.com", DAY, "Europe/Madrid")
        assert state.already_sent(key) is False

    def test_non_qualifying_event_skipped(self):
        gmail = MagicMock()
        cal = self._calendar([_ev(all_day=True, start=None)])
        state = ReminderState.load_data({})
        process_mailbox(gmail, cal, CONFIG, state, day=DAY, sent_at="t")
        gmail.send_email.assert_not_called()

    def test_email_signed_with_config_sender_name(self):
        gmail = MagicMock()
        cal = self._calendar([_ev()])
        state = ReminderState.load_data({})
        cfg = {
            **CONFIG,
            "calendar_reminders": {**CONFIG["calendar_reminders"], "sender_name": "Miguel"},
        }
        process_mailbox(gmail, cal, cfg, state, day=DAY, sent_at="t")
        html = gmail.send_email.call_args.args[3]
        assert "Miguel" in html

    def test_two_recipients_two_sends(self):
        gmail = MagicMock()
        ev = _ev(
            attendees=[
                _att(USER, is_self=True),
                _att("a@x.com"),
                _att("b@x.com"),
            ]
        )
        cal = self._calendar([ev])
        state = ReminderState.load_data({})
        process_mailbox(gmail, cal, CONFIG, state, day=DAY, sent_at="t")
        assert gmail.send_email.call_count == 2


# ------------------------------------------------------------------
# enabled_mailboxes
# ------------------------------------------------------------------


class TestEnabledMailboxes:
    def test_filters_by_enabled_flag(self):
        configs = [
            {"name": "a", "calendar_reminders": {"enabled": True}},
            {"name": "b", "calendar_reminders": {"enabled": False}},
            {"name": "c"},  # no block
        ]
        names = [c["name"] for c in enabled_mailboxes(configs)]
        assert names == ["a"]


# ------------------------------------------------------------------
# should_send (scheduler decision)
# ------------------------------------------------------------------


class TestShouldSend:
    def test_before_send_time(self):
        now = datetime(2026, 6, 26, 8, 59, tzinfo=MADRID)
        assert should_send(now, "09:00", ran_today=False) is False

    def test_at_send_time(self):
        now = datetime(2026, 6, 26, 9, 0, tzinfo=MADRID)
        assert should_send(now, "09:00", ran_today=False) is True

    def test_after_send_time(self):
        now = datetime(2026, 6, 26, 9, 30, tzinfo=MADRID)
        assert should_send(now, "09:00", ran_today=False) is True

    def test_already_ran_today(self):
        now = datetime(2026, 6, 26, 9, 30, tzinfo=MADRID)
        assert should_send(now, "09:00", ran_today=True) is False


# ------------------------------------------------------------------
# run_once (top-level, injected clients)
# ------------------------------------------------------------------


class TestRunOnce:
    def test_sends_and_persists_state(self, tmp_path):
        gmail = MagicMock()
        cal = MagicMock()
        cal.list_events_for_day.return_value = [_ev()]
        path = tmp_path / "state.json"

        run_once(
            clients=[(gmail, cal, CONFIG)],
            day=DAY,
            sent_at="2026-06-26T09:00:00+02:00",
            state_path=path,
        )

        gmail.send_email.assert_called_once()
        assert path.exists()
        reloaded = ReminderState.load(path)
        assert reloaded.ran_today("jesus82c", DAY) is True

    def test_dry_run_does_not_persist(self, tmp_path):
        gmail = MagicMock()
        cal = MagicMock()
        cal.list_events_for_day.return_value = [_ev()]
        path = tmp_path / "state.json"

        run_once(
            clients=[(gmail, cal, CONFIG)],
            day=DAY,
            sent_at="t",
            state_path=path,
            dry_run=True,
        )

        gmail.send_email.assert_not_called()
        assert not path.exists()

    def test_does_not_mark_ran_when_a_send_fails(self, tmp_path):
        gmail = MagicMock()
        gmail.send_email.side_effect = RuntimeError("boom")
        cal = MagicMock()
        cal.list_events_for_day.return_value = [_ev()]
        path = tmp_path / "state.json"

        run_once(clients=[(gmail, cal, CONFIG)], day=DAY, sent_at="t", state_path=path)

        # Day not marked done → scheduler retries; successful recipients stay deduped
        reloaded = ReminderState.load(path)
        assert reloaded.ran_today("jesus82c", DAY) is False

    def test_persists_successful_send_before_later_recipient_error(self, tmp_path, monkeypatch):
        gmail = MagicMock()
        first = _ev(id="evt1", ical_uid="uid-evt1@google.com")
        second = _ev(id="evt2", ical_uid="uid-evt2@google.com")
        cal = MagicMock()
        cal.list_events_for_day.return_value = [first, second]
        path = tmp_path / "state.json"

        def fake_render(event, invitee, sender_name, config, tz):
            if event["id"] == "evt2":
                raise RuntimeError("template error")
            return "Subject", "<p>body</p>"

        monkeypatch.setattr("gmail_inbox_bot.calendar_reminders.render_reminder", fake_render)

        run_once(clients=[(gmail, cal, CONFIG)], day=DAY, sent_at="t", state_path=path)

        reloaded = ReminderState.load(path)
        first_key = dedupe_key(first, "ana@example.com", DAY, "Europe/Madrid")
        assert reloaded.already_sent(first_key) is True
        assert reloaded.ran_today("jesus82c", DAY) is False
