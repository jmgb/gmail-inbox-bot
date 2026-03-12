"""Telegram transport — send messages with chunking and retries (httpx-based)."""

from __future__ import annotations

import logging
import os
import re
import time

import httpx

log = logging.getLogger("gmail_inbox_bot.telegram")

TELEGRAM_MAX_MESSAGE_LEN = 3500
DEFAULT_RETRY_DELAY_SECONDS = 2
MAX_RETRY_DELAY_SECONDS = 30
INTER_CHUNK_DELAY_SECONDS = 1
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def escapar_caracteres(texto: str) -> str:
    """Escape special chars for Telegram HTML parse mode (preserves b, i, u, a, pre tags)."""
    texto = texto.replace("&", "&amp;")
    texto = texto.replace('"', "&quot;")
    protected_tags = {
        "<b>": "§§BO§§",
        "<i>": "§§IO§§",
        "<u>": "§§UO§§",
        "</b>": "§§BC§§",
        "</i>": "§§IC§§",
        "</u>": "§§UC§§",
        "</a>": "§§AC§§",
        "<pre>": "§§PO§§",
        "</pre>": "§§PC§§",
    }
    for tag, marker in protected_tags.items():
        texto = texto.replace(tag, marker)
    links: list[str] = []

    def save_link(match: re.Match) -> str:
        links.append(match.group(0))
        return f"§§LINK_{len(links) - 1}§§"

    texto = re.sub(r"<a\s+href=&quot;[^&]*&quot;>", save_link, texto)
    texto = re.sub(r'<a\s+href="[^"]*">', save_link, texto)
    texto = texto.replace("<", "&lt;").replace(">", "&gt;")
    for tag, marker in protected_tags.items():
        texto = texto.replace(marker, tag)
    for i, link in enumerate(links):
        texto = texto.replace(f"§§LINK_{i}§§", link.replace("&quot;", '"'))
    return texto


def _split_message(escaped: str) -> list[str]:
    parts = [
        escaped[i : i + TELEGRAM_MAX_MESSAGE_LEN]
        for i in range(0, len(escaped), TELEGRAM_MAX_MESSAGE_LEN)
    ]
    for i in range(len(parts) - 1):
        parts[i] += "\n\n<b>…</b>"
    return parts


def _retry_delay(attempt: int, response: httpx.Response | None = None) -> int:
    if response is not None and response.status_code == 429:
        try:
            retry_after = response.json().get("parameters", {}).get("retry_after")
            if isinstance(retry_after, int | float) and retry_after > 0:
                return int(min(retry_after, MAX_RETRY_DELAY_SECONDS))
        except Exception:
            pass
    delay = DEFAULT_RETRY_DELAY_SECONDS * (2 ** (attempt - 1))
    return min(delay, MAX_RETRY_DELAY_SECONDS)


def _send_chunk(
    *,
    url: str,
    payload: dict,
    referencia: str,
    max_attempts: int,
) -> tuple[bool, str]:
    last_error = "Unknown error"
    for attempt in range(1, max_attempts + 1):
        try:
            resp = httpx.post(url, json=payload, timeout=10)
        except httpx.HTTPError as exc:
            last_error = str(exc)
            if attempt == max_attempts:
                return False, last_error
            delay = _retry_delay(attempt)
            log.warning(
                "%s — request failed (%s). Retry in %ss (%s/%s).",
                referencia,
                exc,
                delay,
                attempt,
                max_attempts,
            )
            time.sleep(delay)
            continue

        if resp.status_code == 200:
            return True, ""

        try:
            last_error = resp.json().get("description", resp.text)
        except Exception:
            last_error = resp.text or "no response body"

        if resp.status_code in RETRYABLE_STATUS_CODES and attempt < max_attempts:
            delay = _retry_delay(attempt, resp)
            log.warning(
                "%s — Telegram %s (%s). Retry in %ss (%s/%s).",
                referencia,
                resp.status_code,
                last_error,
                delay,
                attempt,
                max_attempts,
            )
            time.sleep(delay)
            continue

        return False, f"status={resp.status_code} — {last_error}"
    return False, last_error


def enviar_mensaje_telegram(
    mensaje: str,
    chat_id: str | None = None,
    *,
    referencia: str = "",
    max_retries: int = 2,
) -> None:
    """Send a Telegram message with chunking and retries."""
    token = os.getenv("TELEGRAM_TOKEN", "")
    if not token:
        log.warning("%s — TELEGRAM_TOKEN not set, skipping.", referencia)
        return
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
    if not chat_id:
        log.warning("%s — chat_id empty, skipping.", referencia)
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    escaped = f"<b>[Gmail Bot]</b> {escapar_caracteres(mensaje)}"
    chunks = _split_message(escaped)

    for idx, chunk in enumerate(chunks, 1):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        ok, err = _send_chunk(
            url=url,
            payload=payload,
            referencia=referencia,
            max_attempts=max(1, max_retries),
        )
        if not ok:
            log.error(
                "%s — Failed to send Telegram chunk %s/%s: %s",
                referencia,
                idx,
                len(chunks),
                err,
            )
            break
        if idx < len(chunks):
            time.sleep(INTER_CHUNK_DELAY_SECONDS)
