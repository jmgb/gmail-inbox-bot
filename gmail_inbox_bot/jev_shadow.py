"""Clasificación sombra con Jev (TypeSafe.ai).

Jev recibe el mismo email que el clasificador LLM y devuelve una categoría con
probabilidades y confianza. En esta fase NO decide nada: su resultado solo se
registra en métricas para comparar con el clasificador real.

Nunca propaga excepciones: cualquier fallo se convierte en ``jev_error``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml
from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient

from .email_format import format_email_for_classifier
from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.jev_shadow", "logs/app.log")

DEFAULT_CRITERIA_PATH = Path("gmail_inbox_bot/prompts/clasificador_jev.yml")
JEV_MAX_BODY_CHARS = 6000
# La sombra va en serie dentro de _process_email y no decide nada, así que su techo de
# latencia es el techo que le impone al poll. Medido en 201 clasificaciones: p50 340 ms,
# p90 517 ms, p99 5,5 s, máximo 16,6 s — ese máximo era 8 s de timeout por dos intentos.
# Con 4 s y sin reintento el peor caso es 4 s. Lo que se paga es alguna fila más con
# jev_error, que el dashboard ya excluye de la coincidencia (1 de 202 hasta ahora).
JEV_TIMEOUT_SECONDS = 4.0
JEV_MAX_RETRIES = 0
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
    return format_email_for_classifier(
        subject=subject,
        body_text=body_text[:JEV_MAX_BODY_CHARS],
        sender_name=sender_name,
        sender_address=sender_address,
        has_attachments=has_attachments,
    )


@dataclass(frozen=True)
class JevShadow:
    """Cliente Jev + pregunta ``Choice`` construida una vez por arranque."""

    client: TypeSafeClient
    question: Choice

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
        started = time.perf_counter()
        try:
            state = build_state(
                subject=subject,
                body_text=body_text,
                sender_name=sender_name,
                sender_address=sender_address,
                has_attachments=has_attachments,
            )
            response = self.client.system_one(state, {"categoria": self.question})
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
    """Construye el cliente Jev si hay ``JEV_API_KEY``; si no, ``None`` (sombra desactivada).

    Lanza si el YAML falta o no es válido para el SDK; es un error de despliegue, no de Jev.
    """
    api_key = env.get("JEV_API_KEY", "")
    if not api_key:
        log.info("JEV_API_KEY no definida — clasificación sombra con Jev desactivada")
        return None
    criteria = load_criteria(criteria_path)
    question = Choice(instructions=JEV_INSTRUCTIONS, criteria=criteria)
    client = TypeSafeClient(
        api_key=api_key,
        timeout=JEV_TIMEOUT_SECONDS,
        retry=RetryPolicy(max_retries=JEV_MAX_RETRIES),
    )
    # El SDK loguea cada reintento a INFO y propaga a root: solo queremos avisos.
    logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
    log.info("Clasificación sombra con Jev activada (%d categorías)", len(criteria))
    return JevShadow(client=client, question=question)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
