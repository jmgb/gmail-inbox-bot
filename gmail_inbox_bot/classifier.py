"""Email classification and reply generation through the neutral LLM gateway."""

import json
from pathlib import Path

from llm_gateway import (
    AllAttemptsFailed,
    FallbackPolicy,
    LLMRequest,
    Message,
    ResponseFormat,
    RetryPolicy,
)

from .llm_costs import build_cost_metadata
from .llm_gateway_client import SynchronousLLMGateway
from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.classifier", "logs/app.log")

# Default model — overridden by YAML config per mailbox
GPT_5 = "gpt-6-astra"
GPT_5_LUNA = "gpt-5.6-luna"
GPT_OSS_120B = "openai/gpt-oss-120b"
DEFAULT_MODEL = GPT_OSS_120B

# Si Groq falla (quota, caída, rate limit), reintentar con OpenAI.
FALLBACK_MODEL_MAP = {
    GPT_OSS_120B: GPT_5_LUNA,
}


def load_prompt(prompt_file: str) -> str:
    return Path(prompt_file).read_text(encoding="utf-8")


def _available_model_plan(
    client: SynchronousLLMGateway, requested_model: str
) -> tuple[str, tuple[str, ...]]:
    fallback = FALLBACK_MODEL_MAP.get(requested_model)
    candidates = (requested_model, *((fallback,) if fallback else ()))
    available = tuple(model for model in candidates if client.supports_model(model))
    if not available:
        raise RuntimeError(f"No LLM client available for model={requested_model}")
    return available[0], available[1:]


def _request(
    client: SynchronousLLMGateway,
    *,
    model: str,
    system_prompt: str,
    user_content: str,
    response_format: ResponseFormat,
    source: str,
) -> LLMRequest:
    primary_model, fallback_models = _available_model_plan(client, model)
    return LLMRequest(
        model=primary_model,
        system_prompt=system_prompt,
        messages=(Message(role="user", content=user_content),),
        response_format=response_format,
        reasoning_effort="max" if primary_model == GPT_5_LUNA else None,
        retry_policy=RetryPolicy.disabled(),
        fallback_policy=FallbackPolicy.models_in_order(*fallback_models),
        source=source,
    )


def _log_fallback_if_used(response, *, source: str) -> None:
    """Registra el motivo cuando el modelo pedido falló y otro respondió.

    ``Execution.fallback_cause`` puede faltar en payloads de despliegues
    antiguos, así que se accede con ``getattr`` para conservar la compatibilidad
    y aprovechar el detalle cuando esté disponible.
    """
    if not response.execution.fallback_used:
        return
    cause = getattr(response.execution, "fallback_cause", None)
    if cause is None:
        log.info(
            "🔁 Fallback usado en %s: %s -> %s (motivo no disponible)",
            source,
            response.execution.requested_model,
            response.execution.model_used,
        )
        return
    log.info(
        "🔁 Fallback usado en %s: %s -> %s | motivo=%s: %s",
        source,
        response.execution.requested_model,
        response.execution.model_used,
        cause.error_type,
        getattr(cause, "error_message", None),
    )


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
    client: SynchronousLLMGateway,
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
        request = _request(
            client,
            model=model,
            system_prompt=system_prompt,
            user_content=user_content,
            response_format=ResponseFormat.JSON_OBJECT,
            source="email_classification",
        )
        response = client.generate(request)
        _log_fallback_if_used(response, source="classify_email")
        result = dict(response.output)
        categoria = result.get("categoria", "")
        idioma = result.get("idioma", "")
        razon = _sanitize_reason(result.get("razon_clasificacion", ""))
        result["razon_clasificacion"] = razon
        result["model_used"] = response.execution.model_used
        metadata = build_cost_metadata(response)
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
    except AllAttemptsFailed as exc:
        log.warning(
            "Classification failed (%s): %s | last_error=%s last_error_message=%s",
            type(exc).__name__,
            str(exc)[:300],
            exc.last_error,
            getattr(exc, "last_error_message", None),
            exc_info=True,
        )
        return None
    except Exception as exc:
        log.warning(
            "Classification failed (%s): %s",
            type(exc).__name__,
            str(exc)[:300],
            exc_info=True,
        )
        return None


def generate_response(
    client: SynchronousLLMGateway,
    system_prompt: str,
    email_text: str,
    sender_name: str,
    model: str = DEFAULT_MODEL,
) -> dict | None:
    """Generate a free-text reply through the configured LLM cascade."""
    user_content = f"Remitente: {sender_name}\n\nEmail:\n{email_text}"
    try:
        request = _request(
            client,
            model=model,
            system_prompt=system_prompt,
            user_content=user_content,
            response_format=ResponseFormat.TEXT,
            source="dynamic_reply",
        )
        response = client.generate(request)
        _log_fallback_if_used(response, source="generate_response")
        text = response.text.strip()
        result = {"text": text, "model_used": response.execution.model_used}
        metadata = build_cost_metadata(response)
        if metadata:
            result.update(metadata)
        log.info(
            "✍️ Respuesta dinámica generada (%d chars): %.200s%s",
            len(text),
            text,
            "..." if len(text) > 200 else "",
        )
        return result
    except AllAttemptsFailed as exc:
        log.warning(
            "Response generation failed (%s): %s | last_error=%s last_error_message=%s",
            type(exc).__name__,
            str(exc)[:300],
            exc.last_error,
            getattr(exc, "last_error_message", None),
            exc_info=True,
        )
        return None
    except Exception as exc:
        log.warning(
            "Response generation failed (%s): %s",
            type(exc).__name__,
            str(exc)[:300],
            exc_info=True,
        )
        return None
