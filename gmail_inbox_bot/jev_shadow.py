"""Clasificación sombra con Jev (TypeSafe.ai).

Jev recibe el mismo email que el clasificador LLM y devuelve una categoría con
probabilidades y confianza. En esta fase NO decide nada: su resultado solo se
registra en métricas para comparar con el clasificador real.

Nunca propaga excepciones: cualquier fallo se convierte en ``jev_error``.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml
from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient

from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.jev_shadow", "logs/app.log")

DEFAULT_CRITERIA_PATH = Path("gmail_inbox_bot/prompts/clasificador_jev.yml")
JEV_MAX_BODY_CHARS = 6000
JEV_TIMEOUT_SECONDS = 8.0
JEV_MAX_RETRIES = 1
JEV_ERROR_MAX_CHARS = 200
JEV_INSTRUCTIONS = (
    "¿En qué categoría encaja este email recibido en la bandeja de entrada personal del usuario?"
)


def load_criteria(path: str | Path = DEFAULT_CRITERIA_PATH) -> dict[str, dict]:
    """Lee el YAML de criterios y lo devuelve tal cual lo espera ``Choice(criteria=...)``."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not data:
        raise ValueError(f"Criterios Jev vacíos o inválidos en {path}")
    return data


def build_state(
    *,
    subject: str,
    body_text: str,
    sender_name: str,
    sender_address: str,
    has_attachments: bool,
) -> str:
    """Mismo contenido que el ``user_content`` del clasificador LLM, sin la orden de JSON."""
    return (
        f"Título del email: {subject}\n\n"
        f"¿Contiene archivo adjunto?: {has_attachments}\n\n"
        f"Remitente: {sender_name} <{sender_address}>\n\n"
        f"Contenido del email:\n{body_text[:JEV_MAX_BODY_CHARS]}"
    )


@dataclass(frozen=True)
class JevShadow:
    """Cliente Jev + criterios cargados una vez por arranque."""

    client: TypeSafeClient
    criteria: Mapping[str, object]

    def classify(
        self,
        *,
        subject: str,
        body_text: str,
        sender_name: str,
        sender_address: str,
        has_attachments: bool,
    ) -> dict:
        """Devuelve ``{jev_category, jev_confidence, jev_probabilities, jev_latency_ms, jev_model}``
        o ``{jev_error, jev_latency_ms}``. Nunca lanza."""
        state = build_state(
            subject=subject,
            body_text=body_text,
            sender_name=sender_name,
            sender_address=sender_address,
            has_attachments=has_attachments,
        )
        question = Choice(instructions=JEV_INSTRUCTIONS, criteria=self.criteria)
        started = time.perf_counter()
        try:
            response = self.client.system_one(state, {"categoria": question})
            answer = response.choices["categoria"]
            return {
                "jev_category": answer.choice,
                "jev_confidence": float(answer.confidence),
                "jev_probabilities": dict(answer.probabilities),
                "jev_latency_ms": _elapsed_ms(started),
                "jev_model": getattr(response, "model", None),
            }
        except Exception as exc:
            log.warning(
                "Jev sombra falló (%s): %s", type(exc).__name__, str(exc)[:300], exc_info=True
            )
            return {
                "jev_error": f"{type(exc).__name__}: {exc}"[:JEV_ERROR_MAX_CHARS],
                "jev_latency_ms": _elapsed_ms(started),
            }


def build_jev_shadow(
    env: Mapping[str, str], criteria_path: str | Path = DEFAULT_CRITERIA_PATH
) -> JevShadow | None:
    """Construye el cliente Jev si hay ``JEV_API_KEY``; si no, ``None`` (sombra desactivada)."""
    api_key = env.get("JEV_API_KEY", "")
    if not api_key:
        log.info("JEV_API_KEY no definida — clasificación sombra con Jev desactivada")
        return None
    client = TypeSafeClient(
        api_key=api_key,
        timeout=JEV_TIMEOUT_SECONDS,
        retry=RetryPolicy(max_retries=JEV_MAX_RETRIES),
    )
    criteria = load_criteria(criteria_path)
    log.info("Clasificación sombra con Jev activada (%d categorías)", len(criteria))
    return JevShadow(client=client, criteria=criteria)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
