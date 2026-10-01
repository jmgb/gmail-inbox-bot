"""Clasificación con Jev (TypeSafe.ai) — clasificador principal desde el 2026-09-27.

Jev recibe el email y devuelve una categoría con probabilidades y confianza. **Decide el
routing**: la cadena LLM (``gpt-oss-120b`` → ``gpt-6-luna``) queda como fallback y solo
entra si Jev devuelve error. Antes de esta fecha corría en sombra, sin decidir nada.

La confianza se registra pero **no** se usa como umbral todavía: una clasificación de Jev
con 0.40 decide igual que una de 0.99. El umbral por categoría es el siguiente paso
pendiente (ver ``TASKS.md``).

Nunca propaga excepciones: cualquier fallo se convierte en ``jev_error``, y eso es
exactamente lo que dispara el fallback al LLM en ``bot._process_email``.
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
from .jev_model_monitor import observe_jev_model
from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.jev_classifier", "logs/app.log")

# Relativa al paquete, no al cwd: arrancar desde otro directorio no debe tumbar el bot.
DEFAULT_CRITERIA_PATH = Path(__file__).resolve().parent / "prompts" / "clasificador_jev.yml"
JEV_MAX_BODY_CHARS = 6000
# La llamada va en serie dentro de _process_email, así que su techo de latencia es el
# techo que le impone al poll. Medido en 201 clasificaciones en sombra: p50 340 ms,
# p90 517 ms, p99 5,5 s, máximo 16,6 s — ese máximo era 8 s de timeout por dos intentos.
# Con 4 s y sin reintento el peor caso es 4 s. Lo que se paga es alguna fila más con
# jev_error, que el dashboard ya excluye de la coincidencia (1 de 202 hasta ahora).
JEV_TIMEOUT_SECONDS = 4.0
JEV_MAX_RETRIES = 0
JEV_ERROR_MAX_CHARS = 200
# Por debajo de esto la segunda opción no informa de nada y solo ensucia el banner del
# borrador y la métrica: con confianza 1.00 la alternativa sale a 0.00.
_MIN_PROB_ALTERNATIVA = 0.01

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
class JevClassifier:
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
            observe_jev_model(getattr(response, "model", None))
            return {
                "jev_category": answer.choice,
                "jev_confidence": float(answer.confidence),
                "jev_probabilities": dict(answer.probabilities),
                "jev_latency_ms": _elapsed_ms(started),
                "jev_model": getattr(response, "model", None),
            }
        except Exception as exc:
            log.warning("Jev falló (%s): %s", type(exc).__name__, str(exc)[:300], exc_info=True)
            return {
                "jev_error": f"{type(exc).__name__}: {exc}"[:JEV_ERROR_MAX_CHARS],
                "jev_latency_ms": _elapsed_ms(started),
            }


def classification_from_jev(jev_result: dict) -> dict:
    """Traduce la respuesta de Jev a la forma que consume el resto del pipeline.

    El routing, la notificación, ``execute()`` y las métricas esperan el dict del
    clasificador LLM (``categoria``, ``razon_clasificacion``, ``model_used``), así que Jev
    se adapta a ese contrato en vez de tocar todo lo de abajo. No lleva ``usage`` ni
    ``cost``: Jev no factura por tokens y registrar ceros falsearía el coste LLM.
    """
    return {
        "categoria": jev_result["jev_category"],
        "razon_clasificacion": _jev_reason(jev_result),
        "model_used": jev_result.get("jev_model") or "jev",
    }


def _jev_reason(jev_result: dict) -> str:
    """Motivo legible a partir de la confianza: Jev no devuelve prosa como el LLM.

    Se incluye la segunda opción porque es lo que permite juzgar una clasificación dudosa
    de un vistazo en el banner del borrador y en el dashboard.
    """
    confianza = jev_result.get("jev_confidence")
    elegida = jev_result.get("jev_category")
    razon = "Clasificado por Jev"
    if isinstance(confianza, (int, float)):
        razon += f" con confianza {confianza:.2f}"
    alternativas = sorted(
        (
            (categoria, probabilidad)
            for categoria, probabilidad in (jev_result.get("jev_probabilities") or {}).items()
            if categoria != elegida
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    if alternativas and alternativas[0][1] >= _MIN_PROB_ALTERNATIVA:
        segunda, probabilidad = alternativas[0]
        razon += f" (siguiente opción: {segunda}, {probabilidad:.2f})"
    return razon


def build_jev_classifier(
    env: Mapping[str, str], criteria_path: str | Path = DEFAULT_CRITERIA_PATH
) -> JevClassifier | None:
    """Construye el cliente Jev si hay ``JEV_API_KEY``; si no, ``None`` y clasifica el LLM.

    Lanza si el YAML falta o no es válido para el SDK; es un error de despliegue, no de Jev.
    """
    api_key = env.get("JEV_API_KEY", "")
    if not api_key:
        log.info("JEV_API_KEY no definida — clasifica el LLM (Jev desactivado)")
        return None
    criteria = load_criteria(criteria_path)
    question = Choice(instructions=JEV_INSTRUCTIONS, criteria=criteria)
    client = TypeSafeClient(
        api_key=api_key,
        model="jev-latest",
        timeout=JEV_TIMEOUT_SECONDS,
        retry=RetryPolicy(max_retries=JEV_MAX_RETRIES),
    )
    # El SDK loguea cada reintento a INFO y propaga a root: solo queremos avisos.
    logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
    log.info("Jev es el clasificador principal (%d categorías)", len(criteria))
    return JevClassifier(client=client, question=question)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
