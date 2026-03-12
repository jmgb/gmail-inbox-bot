"""Gmail Inbox Bot — registro de métricas de emails procesados en Supabase.

Fire-and-forget: cualquier error se loguea pero NUNCA propaga al bot.
El bot nunca debe fallar por un problema de métricas.
"""

import os

from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.metrics", "logs/app.log")

SUPABASE_TABLE = "email_metrics"


def _supabase_upsert(payload: dict) -> None:
    """Upsert via Supabase REST API. On msg_id conflict, updates the row."""
    import httpx

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")

    if not url or not key:
        log.warning("SUPABASE_URL o SUPABASE_SECRET_KEY no definidos — métrica ignorada")
        return

    endpoint = f"{url}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    params = {}
    if payload.get("msg_id"):
        params["on_conflict"] = "msg_id"

    resp = httpx.post(endpoint, json=payload, headers=headers, params=params, timeout=5)
    resp.raise_for_status()


def record_email(
    *,
    mailbox: str,
    category: str,
    action: str | None = None,
    msg_id: str | None = None,
    model: str | None = None,
    draft_mode: bool = False,
    classification_reason: str | None = None,
    error: bool = False,
    sender: str | None = None,
    subject: str | None = None,
    received_at: str | None = None,
) -> None:
    """Registra un email procesado en email_metrics.

    Parámetros:
        mailbox               Buzón que procesó el email
        category              Categoría del clasificador, 'pre_filter' o error
        action                Acción ejecutada (move, reply, forward, delete, etc.)
        msg_id                Gmail message ID (para deduplicación)
        model                 Modelo OpenAI usado en la clasificación
        draft_mode            True si el bot estaba en modo borrador
        classification_reason Razón textual de la clasificación
        error                 True si hubo un error durante el procesamiento
        sender                Dirección de email del remitente
        subject               Asunto del email
        received_at           Fecha de recepción del email
    """
    try:
        payload = {
            "mailbox": mailbox,
            "category": category,
            "draft_mode": draft_mode,
            "error": error,
        }
        if action:
            payload["action"] = action
        if msg_id:
            payload["msg_id"] = msg_id
        if model:
            payload["model"] = model
        if classification_reason:
            payload["classification_reason"] = classification_reason
        if sender:
            payload["sender"] = sender
        if subject:
            payload["subject"] = subject[:200]
        if received_at:
            payload["received_at"] = received_at

        _supabase_upsert(payload)
        log.debug(
            "Métrica registrada: mailbox=%s | category=%s | action=%s | msg_id=%s",
            mailbox,
            category,
            action,
            msg_id,
        )
    except Exception as exc:
        log.warning(
            "Error al registrar métrica (ignorando) — %s: %s",
            type(exc).__name__,
            str(exc)[:200],
        )
