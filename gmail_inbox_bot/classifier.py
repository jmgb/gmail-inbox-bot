"""Email classifier using OpenAI Responses API."""

import json
from pathlib import Path

from openai import OpenAI

from .llm_costs import build_cost_metadata
from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.classifier", "logs/app.log")

# Default model — overridden by YAML config per mailbox
GPT_5 = "gpt-5.4-2026-03-05"
GPT_5_MINI = "gpt-5.4-mini-2026-03-17"
GPT_5_NANO = "gpt-5.4-nano-2026-03-17"
GPT_OSS_120B = "openai/gpt-oss-120b"
DEFAULT_MODEL = GPT_OSS_120B


def load_prompt(prompt_file: str) -> str:
    return Path(prompt_file).read_text(encoding="utf-8")


def _sanitize_reason(value: object) -> str:
    if not isinstance(value, str):
        return ""

    reason = value.strip()
    if not reason:
        return ""

    lowered = reason.lower()
    if lowered in {"string", "razon_clasificacion"}:
        return ""

    for prefix in ("razon_clasificacion:", "classification_reason:"):
        if lowered.startswith(prefix):
            return reason[len(prefix) :].strip()

    return reason


def classify_email(
    client: OpenAI,
    system_prompt: str,
    subject: str,
    body_text: str,
    sender_name: str,
    sender_address: str,
    has_attachments: bool,
    model: str = DEFAULT_MODEL,
) -> dict | None:
    user_content = (
        f"Título del email: {subject}\n\n"
        f"¿Contiene archivo adjunto?: {has_attachments}\n\n"
        f"Remitente: {sender_name} <{sender_address}>\n\n"
        f"Contenido del email:\n{body_text}\n\n"
        "Responde en formato JSON."
    )

    try:
        resp = client.responses.create(
            model=model,
            instructions=system_prompt,
            input=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_content}],
                },
            ],
            text={
                "format": {"type": "json_object"},
            },
        )
        result = json.loads(resp.output_text)
        categoria = result.get("categoria", "")
        idioma = result.get("idioma", "")
        razon = _sanitize_reason(result.get("razon_clasificacion", ""))
        result["razon_clasificacion"] = razon
        metadata = build_cost_metadata(model, resp)
        if metadata:
            result.update(metadata)
        log.info(
            "📋 Clasificación: categoria=%s | idioma=%s | razón=%s",
            categoria,
            idioma,
            razon,
        )
        log.debug("Clasificación JSON completo: %s", json.dumps(result, ensure_ascii=False))
        return result
    except Exception as exc:
        log.warning(
            "Classification failed (%s): %s",
            type(exc).__name__,
            str(exc)[:300],
            exc_info=True,
        )
        return None


def generate_response(
    client: OpenAI,
    system_prompt: str,
    email_text: str,
    sender_name: str,
    model: str = DEFAULT_MODEL,
) -> dict | None:
    """Generate a free-text reply using OpenAI (for dynamic_reply action)."""
    user_content = f"Remitente: {sender_name}\n\nEmail:\n{email_text}"
    try:
        resp = client.responses.create(
            model=model,
            instructions=system_prompt,
            input=[
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_content}],
                },
            ],
        )
        text = resp.output_text.strip()
        result = {"text": text}
        metadata = build_cost_metadata(model, resp)
        if metadata:
            result.update(metadata)
        log.info(
            "✍️ Respuesta dinámica generada (%d chars): %.200s%s",
            len(text),
            text,
            "..." if len(text) > 200 else "",
        )
        return result
    except Exception as exc:
        log.warning(
            "Response generation failed (%s): %s",
            type(exc).__name__,
            str(exc)[:300],
            exc_info=True,
        )
        return None
