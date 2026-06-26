"""FastAPI application — web admin + background polling bot."""

from __future__ import annotations

import asyncio
import os
import threading

from fastapi import FastAPI

from .admin_dashboard import router as admin_dashboard_router
from .admin_logs import router as admin_logs_router
from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.app", "logs/app.log")

app = FastAPI(
    title="Gmail Inbox Bot",
    docs_url=None,
    redoc_url=None,
)

app.include_router(admin_logs_router)
app.include_router(admin_dashboard_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "gmail-inbox-bot"}


# ---------------------------------------------------------------------------
# Background bot polling thread
# ---------------------------------------------------------------------------


def _is_truthy(var: str) -> bool:
    return os.getenv(var, "").lower() in ("1", "true", "yes")


_bot_thread: threading.Thread | None = None
_reminder_thread: threading.Thread | None = None


def _run_bot_in_thread() -> None:
    """Run the polling bot in a background thread."""
    from .bot import run

    try:
        run(dry_run=_is_truthy("DRY_RUN"))
    except Exception:
        log.exception("Bot thread crashed")


def _run_reminder_scheduler() -> None:
    """Run the daily calendar-reminder scheduler in a background thread."""
    from .calendar_reminders import run_scheduler

    try:
        run_scheduler(dry_run=_is_truthy("DRY_RUN"))
    except Exception:
        log.exception("Calendar reminder scheduler thread crashed")


@app.on_event("startup")
async def start_bot_thread() -> None:
    """Start the polling bot and reminder scheduler as daemon threads."""
    global _bot_thread, _reminder_thread
    if _is_truthy("DISABLE_BOT"):
        log.info("Bot disabled via DISABLE_BOT env var — only admin UI running")
        return

    # Small delay to let the web server bind first
    await asyncio.sleep(1)
    _bot_thread = threading.Thread(target=_run_bot_in_thread, daemon=True, name="gmail-bot")
    _bot_thread.start()
    log.info("Bot polling thread started")

    _reminder_thread = threading.Thread(
        target=_run_reminder_scheduler, daemon=True, name="calendar-reminders"
    )
    _reminder_thread.start()
    log.info("Calendar reminder scheduler thread started")
