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

# Si Groq falla (quota, caída, rate limit), reintentar con OpenAI.
FALLBACK_MODEL_MAP = {
    GPT_OSS_120B: GPT_5_MINI,
}


def load_prompt(prompt_file: str) -> str:
    return Path(prompt_file).read_text(encoding="utf-8")


def _select_client(client_or_clients, model: str):
    """Return the provider client for ``model``, or the single client passed."""
    if not isinstance(client_or_clients, dict):
        return client_or_clients

    if model.startswith("openai/gpt-oss-"):
        return client_or_clients.get("groq")

    return client_or_clients.get("openai")


def _create_response_with_fallback(client_or_clients, *, model: str, **kwargs):
    """Ejecuta la Responses API y reintenta una vez con el modelo de fallback."""
    attempted: list[str] = []
    current_model = model
    last_exc: Exception | None = None

    while current_model and current_model not in attempted:
        attempted.append(current_model)
        client = _select_client(client_or_clients, current_model)
        if client is None:
            log.warning("No LLM client available for model=%s", current_model)
            current_model = FALLBACK_MODEL_MAP.get(current_model)
            continue

        try:
            response = client.responses.create(model=current_model, **kwargs)
            return current_model, response
        except Exception as exc:
            fallback_model = FALLBACK_MODEL_MAP.get(current_model)
            if fallback_model and fallback_model not in attempted:
                log.warning(
                    "LLM request failed for model=%s (%s). Retrying with fallback model=%s",
                    current_model,
                    type(exc).__name__,
                    fallback_model,
                )
                last_exc = exc
                current_model = fallback_model
                continue
            raise

    if last_exc:
        raise last_exc
    raise RuntimeError(f"No LLM client available for model={model}")


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
        used_model, resp = _create_response_with_fallback(
            client,
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
        result["model_used"] = used_model
        metadata = build_cost_metadata(used_model, resp)
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
        used_model, resp = _create_response_with_fallback(
            client,
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
        result = {"text": text, "model_used": used_model}
        metadata = build_cost_metadata(used_model, resp)
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
