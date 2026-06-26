"""Calendar reminders — daily job that emails attendees of small meetings.

At 09:00 Europe/Madrid each day, for every mailbox that opts in via the
``calendar_reminders`` config block, read today's calendar events, keep the
meetings with 1-2 human guests besides the owner, and send each guest a fixed
template reminder. Reuses the Gmail OAuth credentials and the Gmail send path.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Template

from .actions import _load_signature
from .calendar_client import CalendarClient
from .config import load_env, load_mailbox_configs
from .gmail_client import GmailClient
from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.calendar_reminders", "logs/app.log")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE_FILE = _PROJECT_ROOT / "templates" / "calendar_reminder.html"
STATE_PATH = _PROJECT_ROOT / "logs" / "calendar_reminders_state.json"
MADRID = ZoneInfo("Europe/Madrid")
_template_cache: dict[str, Template] = {}

_SPANISH_MONTHS = [
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
]


# ------------------------------------------------------------------
# Persistent state (idempotency)
# ------------------------------------------------------------------


class ReminderState:
    """JSON-backed idempotency state.

    Structure::

        {
          "last_run_date": {"<mailbox>": "YYYY-MM-DD"},
          "sent": {"YYYY-MM-DD": [{"key": ..., "mailbox": ..., ...}]}
        }

    Dedupe of recipients is global (across all dates and mailboxes) so the same
    meeting appearing in two calendars only triggers one email per invitee.
    """

    def __init__(self, data: dict) -> None:
        self.data = data
        self.data.setdefault("last_run_date", {})
        self.data.setdefault("sent", {})

    # -- construction ------------------------------------------------

    @classmethod
    def load_data(cls, data: dict) -> ReminderState:
        return cls(dict(data))

    @classmethod
    def load(cls, path: str | Path, *, strict: bool = True) -> ReminderState:
        p = Path(path)
        if not p.exists():
            return cls({})
        try:
            return cls(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, ValueError) as exc:
            log.error("Corrupt reminder state at %s: %s", p, exc)
            if strict:
                raise ValueError(f"Corrupt reminder state at {p}") from exc
            return cls({})

    # -- persistence -------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Atomically write the state (write to .tmp then replace)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    # -- queries / mutations ----------------------------------------

    def already_sent(self, key: str) -> bool:
        for entries in self.data["sent"].values():
            for entry in entries:
                if entry.get("key") == key:
                    return True
        return False

    def record_sent(
        self,
        day: date,
        key: str,
        mailbox: str,
        event_id: str,
        invitee: str,
        sent_at: str,
    ) -> None:
        self.data["sent"].setdefault(day.isoformat(), []).append(
            {
                "key": key,
                "mailbox": mailbox,
                "event_id": event_id,
                "invitee": invitee,
                "sent_at": sent_at,
            }
        )

    def ran_today(self, mailbox: str, day: date) -> bool:
        return self.data["last_run_date"].get(mailbox) == day.isoformat()

    def mark_ran(self, mailbox: str, day: date) -> None:
        self.data["last_run_date"][mailbox] = day.isoformat()

    def purge_old(self, keep_from: date) -> None:
        """Drop ``sent`` buckets for dates strictly before *keep_from*."""
        cutoff = keep_from.isoformat()
        self.data["sent"] = {d: e for d, e in self.data["sent"].items() if d >= cutoff}


# ------------------------------------------------------------------
# Filtering (pure functions)
# ------------------------------------------------------------------


def human_guests(event: dict) -> list[dict]:
    """Attendees that are real people other than the owner (no self, no resource)."""
    return [
        a
        for a in event.get("attendees", [])
        if a.get("email") and not a["is_self"] and not a["is_resource"]
    ]


def event_qualifies(event: dict, max_attendees: int) -> bool:
    """True if the event should trigger reminders.

    Requires: timed (not all-day), not cancelled, owner has not declined, a
    complete attendee list, and between 1 and ``max_attendees`` human guests.
    """
    if event.get("all_day"):
        return False
    if event.get("status") == "cancelled":
        return False
    if event.get("my_response") == "declined":
        return False
    if event.get("attendees_omitted"):
        return False
    guests = human_guests(event)
    return 1 <= len(guests) <= max_attendees


def reminder_recipients(event: dict) -> list[dict]:
    """Human guests (not the owner, not resources) who have not declined."""
    return [a for a in human_guests(event) if a["response"] != "declined"]


def dedupe_key(event: dict, invitee_email: str, day: date, tz: str) -> str:
    """Stable per-recipient key, independent of which mailbox produced it.

    Prefers ``ical_uid`` (stable across calendars) over the event ``id``.
    """
    zone = ZoneInfo(tz)
    uid = event.get("ical_uid") or event.get("id", "")
    start = event.get("start") or event.get("original_start")
    start_iso = start.astimezone(zone).isoformat() if start else ""
    return f"{day.isoformat()}:{uid}:{start_iso}:{invitee_email.lower()}"


# ------------------------------------------------------------------
# Rendering
# ------------------------------------------------------------------


def _get_template() -> Template:
    key = str(_TEMPLATE_FILE)
    if key not in _template_cache:
        # autoescape=True so untrusted calendar fields (summary, location,
        # attendee names) are HTML-escaped when substituted.
        _template_cache[key] = Template(_TEMPLATE_FILE.read_text(encoding="utf-8"), autoescape=True)
    return _template_cache[key]


def _format_date_es(day: datetime) -> str:
    return f"{day.day} de {_SPANISH_MONTHS[day.month - 1]} de {day.year}"


def render_reminder(
    event: dict,
    invitee: dict,
    sender_name: str,
    config: dict,
    tz: str,
) -> tuple[str, str]:
    """Return ``(subject, html_body)`` for a reminder to *invitee*."""
    zone = ZoneInfo(tz)
    start_local = event["start"].astimezone(zone)
    meeting_time = start_local.strftime("%H:%M")
    location = event.get("location") or event.get("meet_link") or ""
    invitee_name = invitee.get("name") or invitee.get("email", "")

    # str() so the escaped Markup result does not re-escape the trusted
    # signature HTML appended below.
    html = str(
        _get_template().render(
            invitee_name=invitee_name,
            meeting_title=event.get("summary", ""),
            meeting_date=_format_date_es(start_local),
            meeting_time=meeting_time,
            location=location,
            sender_name=sender_name,
        )
    )
    html += _load_signature(config)
    subject = f"Recordatorio: {event.get('summary', '')} hoy a las {meeting_time}"
    return subject, html


# ------------------------------------------------------------------
# Orchestration (one mailbox)
# ------------------------------------------------------------------


def process_mailbox(
    gmail,
    calendar,
    config: dict,
    state: ReminderState,
    *,
    day: date,
    sent_at: str,
    dry_run: bool = False,
    after_record_sent=None,
) -> list[dict]:
    """Read today's events for one mailbox and send reminders. Returns results."""
    settings = config.get("calendar_reminders", {})
    tz = settings.get("timezone", "Europe/Madrid")
    max_attendees = settings.get("max_attendees", 2)
    user_email = config["email"]
    sender_name = config.get("send_as") or user_email
    mailbox = config.get("name", user_email)

    events = calendar.list_events_for_day(day, tz)
    results: list[dict] = []

    for event in events:
        if not event_qualifies(event, max_attendees):
            continue
        for invitee in reminder_recipients(event):
            email = invitee["email"]
            key = dedupe_key(event, email, day, tz)
            if state.already_sent(key):
                results.append({"status": "skipped", "invitee": email, "event": event["id"]})
                continue
            if dry_run:
                log.info(
                    "[dry-run] would remind %s about '%s' (%s)",
                    email,
                    event.get("summary", ""),
                    mailbox,
                )
                results.append({"status": "dry-run", "invitee": email, "event": event["id"]})
                continue
            try:
                subject, html = render_reminder(event, invitee, sender_name, config, tz)
                gmail.send_email(user_email, email, subject, html)
            except Exception:
                log.exception("Failed to prepare or send reminder to %s (%s)", email, mailbox)
                results.append({"status": "error", "invitee": email, "event": event["id"]})
                continue
            state.record_sent(day, key, mailbox, event["id"], email, sent_at)
            if after_record_sent is not None:
                after_record_sent()
            results.append({"status": "sent", "invitee": email, "event": event["id"]})

    return results


# ------------------------------------------------------------------
# Top-level orchestration, scheduler and CLI
# ------------------------------------------------------------------


def enabled_mailboxes(configs: list[dict]) -> list[dict]:
    """Configs that opt in via ``calendar_reminders.enabled``."""
    return [c for c in configs if c.get("calendar_reminders", {}).get("enabled")]


def should_send(now_local: datetime, send_time: str, ran_today: bool) -> bool:
    """True when the local time has reached *send_time* and we haven't run today."""
    if ran_today:
        return False
    hour, minute = (int(x) for x in send_time.split(":"))
    target = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now_local >= target


def _build_clients(env: dict, configs: list[dict]) -> list[tuple]:
    """Build (GmailClient, CalendarClient, config) tuples for enabled mailboxes."""
    clients: list[tuple] = []
    for config in enabled_mailboxes(configs):
        name = config.get("name", "")
        token_var = config.get("refresh_token_env", "")
        refresh_token = os.environ.get(token_var, "") if token_var else ""
        if not config.get("email") or not refresh_token:
            log.error("Skipping mailbox '%s': missing email or refresh token", name)
            continue
        gmail = GmailClient(
            client_id=env["GOOGLE_CLIENT_ID"],
            client_secret=env["GOOGLE_CLIENT_SECRET"],
            refresh_token=refresh_token,
            send_as=config.get("send_as") or None,
        )
        calendar = CalendarClient(
            client_id=env["GOOGLE_CLIENT_ID"],
            client_secret=env["GOOGLE_CLIENT_SECRET"],
            refresh_token=refresh_token,
            user_email=config["email"],
        )
        clients.append((gmail, calendar, config))
    return clients


def run_once(
    *,
    dry_run: bool = False,
    day: date | None = None,
    sent_at: str | None = None,
    clients: list[tuple] | None = None,
    state_path: str | Path = STATE_PATH,
) -> dict[str, list[dict]]:
    """Run the reminder job once for every enabled mailbox.

    ``clients`` can be injected for testing; otherwise they are built from the
    environment and YAML configs. State is only persisted when not ``dry_run``.
    """
    if clients is None:
        env = load_env()
        configs = load_mailbox_configs()
        clients = _build_clients(env, configs)
    if day is None:
        day = datetime.now(MADRID).date()
    if sent_at is None:
        sent_at = datetime.now(MADRID).isoformat()

    state = ReminderState.load(state_path, strict=not dry_run)
    all_results: dict[str, list[dict]] = {}

    for gmail, calendar, config in clients:
        mailbox = config.get("name", config.get("email", ""))
        try:

            def persist_progress() -> None:
                state.purge_old(keep_from=day - timedelta(days=1))
                state.save(state_path)

            results = process_mailbox(
                gmail,
                calendar,
                config,
                state,
                day=day,
                sent_at=sent_at,
                dry_run=dry_run,
                after_record_sent=None if dry_run else persist_progress,
            )
            all_results[mailbox] = results
            sent = sum(1 for r in results if r["status"] == "sent")
            had_error = any(r["status"] == "error" for r in results)
            log.info("Calendar reminders for %s: %d sent", mailbox, sent)
            if not dry_run:
                # Persist successful sends always (dedupe), but only mark the day
                # complete when every recipient succeeded — otherwise the
                # scheduler retries the failed ones on the next tick.
                if not had_error:
                    state.mark_ran(mailbox, day)
                persist_progress()
        except Exception:
            log.exception("Reminder job failed for mailbox %s", mailbox)

    return all_results


def run_scheduler(
    *,
    dry_run: bool = False,
    poll_seconds: int = 60,
    stop=None,
) -> None:
    """Loop forever, triggering ``run_once`` per mailbox at its ``send_time``."""
    env = load_env()
    configs = load_mailbox_configs()
    clients = _build_clients(env, configs)
    if not clients:
        log.info("No mailboxes with calendar_reminders enabled — scheduler idle")
    log.info("Calendar reminder scheduler started (%d mailbox(es))", len(clients))

    while stop is None or not stop.is_set():
        try:
            now = datetime.now(MADRID)
            day = now.date()
            state = ReminderState.load(STATE_PATH, strict=not dry_run)
            for gmail, calendar, config in clients:
                mailbox = config.get("name", config.get("email", ""))
                send_time = config.get("calendar_reminders", {}).get("send_time", "09:00")
                if should_send(now, send_time, state.ran_today(mailbox, day)):
                    log.info("Triggering calendar reminders for %s", mailbox)
                    run_once(
                        clients=[(gmail, calendar, config)],
                        day=day,
                        sent_at=now.isoformat(),
                        dry_run=dry_run,
                    )
        except Exception:
            log.exception("Reminder scheduler iteration failed")

        if stop is not None:
            stop.wait(poll_seconds)
        else:
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Calendar reminders")
    parser.add_argument("--once", action="store_true", help="Run a single job now and exit")
    parser.add_argument(
        "--dry-run", action="store_true", help="Log without sending or saving state"
    )
    args = parser.parse_args()

    if args.once:
        results = run_once(dry_run=args.dry_run)
        for mailbox, items in results.items():
            for item in items:
                log.info("[%s] %s → %s", mailbox, item["status"], item["invitee"])
    else:
        run_scheduler(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
