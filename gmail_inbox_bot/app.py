"""FastAPI application — web admin + background polling bot."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from .admin_dashboard import router as admin_dashboard_router
from .admin_logs import router as admin_logs_router
from .logger import setup_logger
from .telegram_logger import setup_telegram_logging

log = setup_logger("gmail_inbox_bot.app", "logs/app.log")


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await start_bot_thread()
    yield


app = FastAPI(
    title="Gmail Inbox Bot",
    docs_url=None,
    redoc_url=None,
    lifespan=_lifespan,
)

app.include_router(admin_logs_router)
app.include_router(admin_dashboard_router)


def _thread_state(thread: threading.Thread | None, *, disabled: bool) -> str:
    if disabled:
        return "disabled"
    if thread is None:
        return "starting"
    return "alive" if thread.is_alive() else "dead"


# Margen sobre tres ciclos de poll: un ciclo con muchos emails (Jev + acciones) tarda.
_STALL_CYCLES = 3
_STALL_GRACE_SECONDS = 120
_stall_reported = False


def _bot_state(disabled: bool) -> str:
    """``stalled`` si el thread vive pero no hay un poll correcto en tres ciclos."""
    state = _thread_state(_bot_thread, disabled=disabled)
    if state != "alive":
        return state
    from . import bot

    reference = bot.last_successful_poll or bot.bot_started_at
    if reference is None or not bot.poll_interval_seconds:
        return state
    limit = _STALL_CYCLES * bot.poll_interval_seconds + _STALL_GRACE_SECONDS
    return "stalled" if time.time() - reference > limit else state


def _thread_states() -> dict[str, str]:
    disabled = _is_truthy("DISABLE_BOT")
    return {
        "bot": _bot_state(disabled),
        "calendar_reminders": _thread_state(_reminder_thread, disabled=disabled),
    }


@app.get("/health")
async def health() -> dict:
    """Estado del proceso *y* de los threads que hacen el trabajo.

    El bot y el scheduler son daemon threads: si uno muere, el proceso sigue en pie y
    este endpoint seguía devolviendo 200, así que el contenedor quedaba "Up" sin
    clasificar nada. Devolver 503 es lo que permite al HEALTHCHECK de Docker verlo.
    """
    global _stall_reported
    threads = _thread_states()
    stalled = threads["bot"] == "stalled"
    if stalled and not _stall_reported:
        # Docker no avisa de "unhealthy": este log.error llega a Telegram. Uno por atasco, no
        # uno por healthcheck (cada 60 s).
        log.error("Bot sin un poll correcto en %d ciclos: revisar buzones y logs", _STALL_CYCLES)
    _stall_reported = stalled
    if {"dead", "stalled"} & set(threads.values()):
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "service": "gmail-inbox-bot", "threads": threads},
        )
    return {"status": "ok", "service": "gmail-inbox-bot", "threads": threads}


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


async def start_bot_thread() -> None:
    """Start the polling bot and reminder scheduler as daemon threads."""
    global _bot_thread, _reminder_thread
    setup_telegram_logging(chat_id=os.getenv("TELEGRAM_CHAT_ID"))

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
