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

_bot_thread: threading.Thread | None = None


def _run_bot_in_thread() -> None:
    """Run the polling bot in a background thread."""
    from .bot import run

    dry_run = os.getenv("DRY_RUN", "").lower() in ("1", "true", "yes")
    try:
        run(dry_run=dry_run)
    except Exception:
        log.exception("Bot thread crashed")


@app.on_event("startup")
async def start_bot_thread() -> None:
    """Start the polling bot in a daemon thread when the FastAPI app starts."""
    global _bot_thread
    if os.getenv("DISABLE_BOT", "").lower() in ("1", "true", "yes"):
        log.info("Bot disabled via DISABLE_BOT env var — only admin UI running")
        return

    # Small delay to let the web server bind first
    await asyncio.sleep(1)
    _bot_thread = threading.Thread(target=_run_bot_in_thread, daemon=True, name="gmail-bot")
    _bot_thread.start()
    log.info("Bot polling thread started")
