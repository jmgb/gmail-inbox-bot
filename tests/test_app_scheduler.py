"""Tests for the calendar-reminder scheduler glue in app.py."""

from threading import Event

import gmail_inbox_bot.calendar_reminders as cr


def test_run_reminder_scheduler_passes_dry_run(monkeypatch):
    captured = {}
    monkeypatch.setattr(cr, "run_scheduler", lambda **kw: captured.update(kw))
    monkeypatch.setenv("DRY_RUN", "1")

    from gmail_inbox_bot.app import _run_reminder_scheduler

    _run_reminder_scheduler()
    assert captured.get("dry_run") is True


def test_run_reminder_scheduler_default_not_dry(monkeypatch):
    captured = {}
    monkeypatch.setattr(cr, "run_scheduler", lambda **kw: captured.update(kw))
    monkeypatch.delenv("DRY_RUN", raising=False)

    from gmail_inbox_bot.app import _run_reminder_scheduler

    _run_reminder_scheduler()
    assert captured.get("dry_run") is False


def test_scheduler_loads_state_strictly_when_not_dry_run(monkeypatch):
    stop = Event()
    captured = []

    monkeypatch.setattr(cr, "load_env", lambda: {})
    monkeypatch.setattr(cr, "load_mailbox_configs", lambda: [])
    monkeypatch.setattr(cr, "_build_clients", lambda env, configs: [])

    def fake_load(path, *, strict=True):
        captured.append(strict)
        stop.set()
        return cr.ReminderState.load_data({})

    monkeypatch.setattr(cr.ReminderState, "load", fake_load)

    cr.run_scheduler(dry_run=False, poll_seconds=0, stop=stop)

    assert captured == [True]
