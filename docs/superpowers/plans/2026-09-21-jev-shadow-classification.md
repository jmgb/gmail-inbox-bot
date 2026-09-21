# Clasificación sombra con Jev (TypeSafe.ai) — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que cada email clasificado por el LLM actual sea también clasificado por Jev (TypeSafe.ai) en modo sombra, guardando su categoría/confianza junto a la real en `email_metrics` y mostrando la comparativa en el dashboard admin, sin cambiar ninguna decisión de producción.

**Architecture:** Módulo nuevo `gmail_inbox_bot/jev_shadow.py` (cliente Jev + criterios YAML + método `classify` que nunca propaga errores). `bot.py` lo invoca justo después de `classify_email` y vuelca su dict en `record_email`. `metrics.py` gana 6 kwargs `jev_*`; `admin_dashboard.py` gana el endpoint `/admin/api/jev_shadow` con una agregación pura testeable, y la plantilla del dashboard una sección "Jev vs LLM".

**Tech Stack:** Python 3.13, `typesafe-sdk>=0.7.0` (usa `httpx2`, ya en el proyecto), PyYAML, FastAPI + Jinja2, Supabase REST (PostgREST), pytest, ruff. Spec: `docs/superpowers/specs/2026-09-21-jev-shadow-classification-design.md`.

**Comprobado contra el SDK real (0.7.0):**
- `from typesafe_sdk import TypeSafeClient, Choice, RetryPolicy, TypeSafeError`
- `TypeSafeClient(*, api_key, timeout: float, retry: RetryPolicy, ...)`; `RetryPolicy(max_retries=1)`
- `Choice(instructions=str, criteria=Mapping[str, JSONContent])` — acepta dicts anidados `{"what":..., "not_for":..., "examples":[...]}`
- `client.system_one(state: str, questions: dict) -> SystemOneResponse` con `.model: str`, `.usage`, `.choices["categoria"]` → `ChoiceAnswer(choice: str, confidence: float, probabilities: dict[str, float])`
- Todas las excepciones del SDK heredan de `TypeSafeError`.
- Llamada de prueba real: ~900 ms, `model="jev-1.13.0"`.

**Convenciones del repo que aplican:**
- Comandos: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format .`
- Archivos `.py` < 2000 líneas. Logs con `setup_logger(name, "logs/app.log")`.
- Commits pequeños. Antes del push: `bash scripts/ci-local.sh` y `/codex:review --wait`.
- Trabajar en rama `main` (el repo despliega al VPS al hacer push a `main`; **no hacer push hasta la Task 10**).
- Todo valor que llegue de la API al HTML del dashboard pasa por `escapeHtml` antes de insertarse con `innerHTML` (el dashboard ya usa `innerHTML` con plantillas; se mantiene el patrón pero escapando).

---

## Mapa de archivos

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `pyproject.toml`, `uv.lock` | modificar | dependencia `typesafe-sdk` |
| `gmail_inbox_bot/prompts/clasificador_jev.yml` | crear | criterios de las 8 categorías para Jev |
| `gmail_inbox_bot/jev_shadow.py` | crear | cliente Jev, carga de criterios, `build_state`, `classify` |
| `gmail_inbox_bot/config.py` | modificar | `load_env` expone `JEV_API_KEY` |
| `gmail_inbox_bot/metrics.py` | modificar | kwargs `jev_*` en `record_email` |
| `gmail_inbox_bot/bot.py` | modificar | construye `JevShadow`, lo pasa por `process_mailbox`/`_process_email`, llama a `classify`, loguea y registra |
| `scripts/supabase_create_table.sql` | modificar | migración de columnas `jev_*` |
| `gmail_inbox_bot/admin_dashboard.py` | modificar | `_fetch_metrics` parametrizable, `_aggregate_jev`, endpoint `/admin/api/jev_shadow` |
| `gmail_inbox_bot/templates/admin_dashboard.html` | modificar | sección "Jev vs LLM (sombra)" |
| `CLAUDE.md`, `.env.example`, `README.md` | modificar | documentación |
| `tests/test_jev_shadow.py` | crear | tests del módulo + paridad YAML/routing |
| `tests/test_metrics.py`, `tests/test_bot.py`, `tests/test_config.py` | modificar | tests de integración |
| `tests/test_admin_dashboard.py` | crear | tests de `_aggregate_jev` |

---

### Task 1: Dependencia `typesafe-sdk`

**Files:**
- Modify: `pyproject.toml` (bloque `dependencies`)
- Modify: `uv.lock` (lo regenera `uv`)

- [ ] **Step 1: Añadir la dependencia**

```bash
cd ~/ai_projects/gmail-inbox-bot && uv add "typesafe-sdk>=0.7.0"
```

Expected: `pyproject.toml` tiene la línea `"typesafe-sdk>=0.7.0",` dentro de `dependencies` y `uv.lock` cambia.

- [ ] **Step 2: Verificar que importa y que el gate de arquitectura sigue verde**

```bash
uv run python -c "from typesafe_sdk import TypeSafeClient, Choice, RetryPolicy, TypeSafeError; print('ok')"
uv run pytest -q tests/test_llm_architecture.py
```

Expected: `ok` y tests PASS (`typesafe_sdk` no está en `PROVIDER_SDKS`, así que no dispara el gate).

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): añade typesafe-sdk para clasificación sombra con Jev"
```

---

### Task 2: Criterios YAML de Jev

**Files:**
- Create: `gmail_inbox_bot/prompts/clasificador_jev.yml`
- Test: `tests/test_jev_shadow.py` (solo el test de paridad; el resto de tests del módulo llegan en Task 3)

- [ ] **Step 1: Escribir el test de paridad YAML ↔ routing**

Crear `tests/test_jev_shadow.py`:

```python
"""Tests for jev_shadow — clasificación sombra con Jev (TypeSafe.ai)."""

from pathlib import Path

import yaml

CRITERIA_PATH = Path("gmail_inbox_bot/prompts/clasificador_jev.yml")
CONFIG_DIR = Path("config")


class TestCriteriaFile:
    def test_every_category_has_what_not_for_examples(self):
        data = yaml.safe_load(CRITERIA_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and data, "YAML vacío"
        for category, spec in data.items():
            assert set(spec) == {"what", "not_for", "examples"}, category
            assert isinstance(spec["what"], str) and spec["what"].strip(), category
            assert isinstance(spec["not_for"], str) and spec["not_for"].strip(), category
            assert isinstance(spec["examples"], list) and spec["examples"], category

    def test_categories_match_routing_of_every_mailbox(self):
        criteria = yaml.safe_load(CRITERIA_PATH.read_text(encoding="utf-8"))
        for yml in sorted(CONFIG_DIR.glob("*.yml")):
            config = yaml.safe_load(yml.read_text(encoding="utf-8"))
            assert set(criteria) == set(config["routing"]), yml.name
```

- [ ] **Step 2: Ejecutar y ver que falla**

```bash
uv run pytest -q tests/test_jev_shadow.py
```

Expected: FAIL con `FileNotFoundError: ... clasificador_jev.yml`.

- [ ] **Step 3: Crear el YAML de criterios**

Crear `gmail_inbox_bot/prompts/clasificador_jev.yml` (traducción del prompt `clasificador_inbox.txt`; las "reglas aprendidas de producción" van como `examples`/`not_for` de la categoría afectada):

```yaml
# Criterios de clasificación para Jev (TypeSafe.ai) — modo sombra.
# Una entrada por categoría, mismas claves que `routing` en config/*.yml.
# Cada categoría: what (qué abarca), not_for (qué excluye), examples (casos reales).
# Mientras dure la sombra, cada regla nueva del prompt LLM se replica aquí.

personal:
  what: >
    Email directo de una persona real que espera una respuesta o acción del usuario:
    conversaciones, preguntas, propuestas, solicitudes personales o profesionales dirigidas
    a él. También avisos de servicios que exigen una acción urgente o importante del usuario
    ("Signature Required", "Action Required", "Please review").
  not_for: >
    Notificaciones automáticas aunque el remitente sea conocido; newsletters o boletines;
    prospección comercial de desconocidos ofreciendo servicios (eso es spam); avisos rutinarios
    de apps sin urgencia (eso es notificaciones). En caso de duda entre notificaciones y
    personal, elegir personal.
  examples:
    - "Un contacto escribe preguntando si podemos vernos la semana que viene"
    - "Un cliente pide presupuesto o responde a un hilo anterior"
    - "DocuSign: 'Signature Required' en un contrato que el usuario debe firmar hoy"

finanzas:
  what: >
    Emails de bancos, brokers, pasarelas de pago, facturas, recibos, movimientos de cuenta,
    transferencias, impuestos y seguros. Cualquier contenido relacionado con dinero que
    requiera verificación o acción. El remitente bancario o de broker es señal suficiente
    aunque el cuerpo esté vacío o solo tenga firma legal.
  not_for: >
    Publicidad de productos financieros (eso es spam); confirmaciones de compra en tiendas
    online (eso es compras).
  examples:
    - "Remitente bbva.es o santander con asunto 'Movimiento en tu cuenta' y cuerpo vacío"
    - "interactivebrokers.com o degiro: notificación de operación ejecutada"
    - "Factura mensual de un proveedor con PDF adjunto"
    - "Agencia Tributaria: notificación de liquidación"

compras:
  what: >
    Confirmaciones de pedido, seguimiento de envío, recibos de compra, devoluciones y
    notificaciones de e-commerce (Amazon, tiendas online, marketplaces).
  not_for: >
    Publicidad, ofertas o catálogos de una tienda (eso es spam); recibos bancarios o de
    tarjeta (eso es finanzas).
  examples:
    - "Amazon: 'Tu pedido ha sido enviado' con número de seguimiento"
    - "Tienda online: confirmación de devolución aceptada"

newsletters:
  what: >
    Newsletters, boletines, digests y resúmenes periódicos a los que el usuario se suscribió
    voluntariamente: contenido editorial o informativo recurrente de productos que usa o
    servicios contratados. Suelen tener enlace de unsubscribe.
  not_for: >
    Marketing no solicitado o de empresa desconocida aunque tenga enlace de unsubscribe
    (eso es spam). En caso de duda entre spam y newsletters, elegir spam.
  examples:
    - "Boletín semanal de un SaaS que el usuario tiene contratado"
    - "Digest diario de una comunidad a la que el usuario se apuntó"

notificaciones:
  what: >
    Alertas y notificaciones rutinarias de aplicaciones, redes sociales y servicios online:
    GitHub, LinkedIn, Google, calendarios, apps móviles. Avisos informativos que no exigen
    una acción urgente ni personal.
  not_for: >
    Avisos que requieren acción urgente o personal del usuario (eso es personal);
    respuestas automáticas sin ninguna acción (eso es automatico).
  examples:
    - "LinkedIn: 'Tienes 3 nuevas visitas a tu perfil'"
    - "Google Calendar: invitación aceptada"
    - "Servicio SaaS: 'Please review your monthly report' rutinario"

automatico:
  what: >
    Respuestas automáticas de fuera de oficina en cualquier idioma, emails desde direcciones
    noreply@ y confirmaciones de sistema que no requieren ninguna acción.
  not_for: >
    Cualquier email que requiera una acción del usuario, aunque venga de noreply@ (eso es
    personal o notificaciones según urgencia).
  examples:
    - "Out of office: 'Estaré ausente hasta el lunes'"
    - "noreply@servicio.com: 'Tu contraseña se ha cambiado correctamente'"

spam:
  what: >
    Publicidad no solicitada, promociones, marketing masivo, ofertas comerciales y
    prospección B2B de desconocidos ofreciendo servicios, partnerships, demos o pipeline.
    Patrón típico: nombre personal + dominio desconocido + asunto genérico dirigido al usuario.
  not_for: >
    Boletines a los que el usuario se suscribió voluntariamente (eso es newsletters);
    confirmaciones de pedidos reales (eso es compras).
  examples:
    - "'Question for Jesús' desde un dominio desconocido ofreciendo servicios de leads"
    - "'[nombre], quick question regarding your pipeline'"
    - "Oferta de tarjeta de crédito de una entidad con la que el usuario no tiene relación"

otros:
  what: >
    El email no encaja claramente en ninguna otra categoría. Es el fallback seguro cuando
    no hay certeza: remitente desconocido sin cuerpo útil, contenido ambiguo o incompleto.
  not_for: >
    Emails que sí encajan con claridad en otra categoría, incluso con cuerpo vacío si el
    remitente es reconocible (banco → finanzas, tienda → compras).
  examples:
    - "Remitente desconocido, asunto vacío y cuerpo con solo una firma legal"
    - "Email en un idioma no identificable sin contexto"
```

- [ ] **Step 4: Ejecutar y ver que pasa**

```bash
uv run pytest -q tests/test_jev_shadow.py
```

Expected: 2 PASS. (Si `test_categories_match_routing_of_every_mailbox` falla, comparar las claves del YAML con `routing:` de `config/jesus82c.yml` y `config/miguelgutierrezbarquin.yml` y corregir el YAML, nunca el routing.)

- [ ] **Step 5: Commit**

```bash
git add gmail_inbox_bot/prompts/clasificador_jev.yml tests/test_jev_shadow.py
git commit -m "feat(jev): criterios de clasificación para Jev con test de paridad con routing"
```

---

### Task 3: Módulo `jev_shadow.py`

**Files:**
- Create: `gmail_inbox_bot/jev_shadow.py`
- Test: `tests/test_jev_shadow.py` (añadir clases de test)

- [ ] **Step 1: Añadir los tests del módulo**

Añadir al principio de `tests/test_jev_shadow.py`, tras los imports existentes:

```python
from types import SimpleNamespace
from unittest.mock import MagicMock

from gmail_inbox_bot import jev_shadow
from gmail_inbox_bot.jev_shadow import (
    JEV_MAX_BODY_CHARS,
    JevShadow,
    build_jev_shadow,
    build_state,
    load_criteria,
)
```

Y al final del archivo:

```python
def _fake_response(choice="spam", confidence=0.93, probabilities=None, model="jev-1.13.0"):
    answer = SimpleNamespace(
        choice=choice,
        confidence=confidence,
        probabilities=probabilities or {"spam": 0.93, "otros": 0.07},
    )
    return SimpleNamespace(model=model, choices={"categoria": answer})


class TestLoadCriteria:
    def test_returns_dict_keyed_by_category(self):
        criteria = load_criteria(CRITERIA_PATH)
        assert "spam" in criteria
        assert set(criteria["spam"]) == {"what", "not_for", "examples"}


class TestBuildState:
    def test_contains_all_fields(self):
        state = build_state(
            subject="Hola",
            body_text="Cuerpo del email",
            sender_name="Juan",
            sender_address="juan@x.com",
            has_attachments=True,
        )
        assert "Título del email: Hola" in state
        assert "¿Contiene archivo adjunto?: True" in state
        assert "Remitente: Juan <juan@x.com>" in state
        assert "Contenido del email:\nCuerpo del email" in state
        assert "JSON" not in state

    def test_truncates_long_body(self):
        state = build_state(
            subject="s",
            body_text="x" * (JEV_MAX_BODY_CHARS + 500),
            sender_name="",
            sender_address="a@b.c",
            has_attachments=False,
        )
        assert state.count("x") == JEV_MAX_BODY_CHARS


class TestBuildJevShadow:
    def test_returns_none_without_key(self):
        assert build_jev_shadow({}, CRITERIA_PATH) is None
        assert build_jev_shadow({"JEV_API_KEY": ""}, CRITERIA_PATH) is None

    def test_builds_client_with_key(self, monkeypatch):
        captured = {}

        def fake_client(**kwargs):
            captured.update(kwargs)
            return MagicMock(name="jev-client")

        monkeypatch.setattr(jev_shadow, "TypeSafeClient", fake_client)
        shadow = build_jev_shadow({"JEV_API_KEY": "apikey_test"}, CRITERIA_PATH)
        assert isinstance(shadow, JevShadow)
        assert captured["api_key"] == "apikey_test"
        assert captured["timeout"] == jev_shadow.JEV_TIMEOUT_SECONDS
        assert "otros" in shadow.criteria


class TestClassify:
    def _shadow(self, client):
        return JevShadow(client=client, criteria={"spam": "x", "otros": "y"})

    def test_maps_response_to_dict(self):
        client = MagicMock()
        client.system_one.return_value = _fake_response()
        result = self._shadow(client).classify(
            subject="Oferta",
            body_text="Compra ya",
            sender_name="",
            sender_address="promo@x.com",
            has_attachments=False,
        )
        assert result["jev_category"] == "spam"
        assert result["jev_confidence"] == 0.93
        assert result["jev_probabilities"] == {"spam": 0.93, "otros": 0.07}
        assert result["jev_model"] == "jev-1.13.0"
        assert isinstance(result["jev_latency_ms"], int)
        assert "jev_error" not in result

    def test_sends_state_and_choice_question(self):
        client = MagicMock()
        client.system_one.return_value = _fake_response()
        self._shadow(client).classify(
            subject="Oferta",
            body_text="Compra ya",
            sender_name="",
            sender_address="promo@x.com",
            has_attachments=False,
        )
        state, questions = client.system_one.call_args.args
        assert "Título del email: Oferta" in state
        assert list(questions) == ["categoria"]
        assert questions["categoria"].criteria == {"spam": "x", "otros": "y"}

    def test_exception_becomes_jev_error(self):
        client = MagicMock()
        client.system_one.side_effect = RuntimeError("boom")
        result = self._shadow(client).classify(
            subject="s",
            body_text="b",
            sender_name="",
            sender_address="a@b.c",
            has_attachments=False,
        )
        assert result["jev_error"] == "RuntimeError: boom"
        assert isinstance(result["jev_latency_ms"], int)
        assert "jev_category" not in result

    def test_missing_answer_becomes_jev_error(self):
        client = MagicMock()
        client.system_one.return_value = SimpleNamespace(model="jev", choices={})
        result = self._shadow(client).classify(
            subject="s",
            body_text="b",
            sender_name="",
            sender_address="a@b.c",
            has_attachments=False,
        )
        assert result["jev_error"].startswith("KeyError")

    def test_error_message_is_truncated(self):
        client = MagicMock()
        client.system_one.side_effect = RuntimeError("x" * 500)
        result = self._shadow(client).classify(
            subject="s",
            body_text="b",
            sender_name="",
            sender_address="a@b.c",
            has_attachments=False,
        )
        assert len(result["jev_error"]) == 200
```

- [ ] **Step 2: Ejecutar y ver que falla**

```bash
uv run pytest -q tests/test_jev_shadow.py
```

Expected: FAIL con `ModuleNotFoundError: No module named 'gmail_inbox_bot.jev_shadow'`.

- [ ] **Step 3: Implementar el módulo**

Crear `gmail_inbox_bot/jev_shadow.py`:

```python
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
```

- [ ] **Step 4: Ejecutar y ver que pasa**

```bash
uv run pytest -q tests/test_jev_shadow.py && uv run ruff check gmail_inbox_bot/jev_shadow.py tests/test_jev_shadow.py && uv run ruff format --check gmail_inbox_bot/jev_shadow.py tests/test_jev_shadow.py
```

Expected: todos PASS, ruff sin avisos. Si `ruff format --check` falla, ejecutar `uv run ruff format gmail_inbox_bot/jev_shadow.py tests/test_jev_shadow.py`.

- [ ] **Step 5: Commit**

```bash
git add gmail_inbox_bot/jev_shadow.py tests/test_jev_shadow.py
git commit -m "feat(jev): módulo jev_shadow con cliente, criterios y classify sin propagar errores"
```

---

### Task 4: `load_env` expone `JEV_API_KEY`

**Files:**
- Modify: `gmail_inbox_bot/config.py:36-40`
- Test: `tests/test_config.py`

- [ ] **Step 1: Escribir el test**

Ver primero cómo están escritos los tests de `load_env` en `tests/test_config.py` (`grep -n "load_env" tests/test_config.py`) y añadir en la misma clase/estilo. Como `load_env` llama a `load_dotenv()` (que lee el `.env` local del desarrollador, donde ya existe `JEV_API_KEY`), neutralizarlo con `monkeypatch`:

```python
    def test_load_env_exposes_jev_api_key(self, monkeypatch):
        monkeypatch.setattr("gmail_inbox_bot.config.load_dotenv", lambda: None)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
        monkeypatch.setenv("JEV_API_KEY", "apikey_test")
        from gmail_inbox_bot.config import load_env

        assert load_env()["JEV_API_KEY"] == "apikey_test"

    def test_load_env_jev_api_key_defaults_to_empty(self, monkeypatch):
        monkeypatch.setattr("gmail_inbox_bot.config.load_dotenv", lambda: None)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
        monkeypatch.delenv("JEV_API_KEY", raising=False)
        from gmail_inbox_bot.config import load_env

        assert load_env()["JEV_API_KEY"] == ""
```

- [ ] **Step 2: Ejecutar y ver que falla**

```bash
uv run pytest -q tests/test_config.py -k jev
```

Expected: FAIL con `KeyError: 'JEV_API_KEY'`.

- [ ] **Step 3: Implementar**

En `gmail_inbox_bot/config.py`, tras `env["GROQ_API_KEY"] = os.environ.get("GROQ_API_KEY", "")`:

```python
    env["JEV_API_KEY"] = os.environ.get("JEV_API_KEY", "")
```

- [ ] **Step 4: Ejecutar y ver que pasa**

```bash
uv run pytest -q tests/test_config.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gmail_inbox_bot/config.py tests/test_config.py
git commit -m "feat(jev): load_env expone JEV_API_KEY"
```

---

### Task 5: `record_email` acepta campos `jev_*`

**Files:**
- Modify: `gmail_inbox_bot/metrics.py:44-131`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: Escribir los tests**

Añadir a `tests/test_metrics.py`, dentro de `class TestRecordEmail`:

```python
    @patch("gmail_inbox_bot.metrics._supabase_upsert")
    def test_jev_fields_are_included_in_payload(self, mock_upsert):
        record_email(
            mailbox="test",
            category="spam",
            msg_id="msg-2",
            jev_category="spam",
            jev_confidence=0.93,
            jev_probabilities={"spam": 0.93, "otros": 0.07},
            jev_latency_ms=901,
            jev_model="jev-1.13.0",
        )

        payload = mock_upsert.call_args.args[0]
        assert payload["jev_category"] == "spam"
        assert payload["jev_confidence"] == 0.93
        assert payload["jev_probabilities"] == {"spam": 0.93, "otros": 0.07}
        assert payload["jev_latency_ms"] == 901
        assert payload["jev_model"] == "jev-1.13.0"
        assert "jev_error" not in payload

    @patch("gmail_inbox_bot.metrics._supabase_upsert")
    def test_jev_error_is_included_and_other_jev_fields_omitted(self, mock_upsert):
        record_email(
            mailbox="test",
            category="spam",
            msg_id="msg-3",
            jev_error="TypeSafeAPITimeoutError: timeout",
            jev_latency_ms=8000,
        )

        payload = mock_upsert.call_args.args[0]
        assert payload["jev_error"] == "TypeSafeAPITimeoutError: timeout"
        assert payload["jev_latency_ms"] == 8000
        assert "jev_category" not in payload
        assert "jev_confidence" not in payload

    @patch("gmail_inbox_bot.metrics._supabase_upsert")
    def test_jev_fields_absent_when_not_provided(self, mock_upsert):
        record_email(mailbox="test", category="otros", msg_id="msg-4")

        payload = mock_upsert.call_args.args[0]
        assert not any(key.startswith("jev_") for key in payload)
```

- [ ] **Step 2: Ejecutar y ver que falla**

```bash
uv run pytest -q tests/test_metrics.py
```

Expected: FAIL con `TypeError: record_email() got an unexpected keyword argument 'jev_category'`.

- [ ] **Step 3: Implementar**

En `gmail_inbox_bot/metrics.py`, en la firma de `record_email`, tras `llm_provider: str | None = None,`:

```python
    jev_category: str | None = None,
    jev_confidence: float | None = None,
    jev_probabilities: dict[str, float] | None = None,
    jev_latency_ms: int | None = None,
    jev_model: str | None = None,
    jev_error: str | None = None,
```

En el docstring, tras la línea de `llm_provider`:

```
        jev_category          Categoría elegida por Jev (clasificación sombra)
        jev_confidence        Confianza 0-1 de Jev
        jev_probabilities     Distribución de probabilidad por categoría (JSONB)
        jev_latency_ms        Latencia de la llamada a Jev
        jev_model             Modelo Jev que respondió
        jev_error             Error de la llamada a Jev, si falló
```

En el cuerpo, justo antes de `_supabase_upsert(payload)`:

```python
        if jev_category:
            payload["jev_category"] = jev_category
        if jev_confidence is not None:
            payload["jev_confidence"] = jev_confidence
        if jev_probabilities is not None:
            payload["jev_probabilities"] = jev_probabilities
        if jev_latency_ms is not None:
            payload["jev_latency_ms"] = jev_latency_ms
        if jev_model:
            payload["jev_model"] = jev_model
        if jev_error:
            payload["jev_error"] = jev_error
```

- [ ] **Step 4: Ejecutar y ver que pasa**

```bash
uv run pytest -q tests/test_metrics.py && uv run ruff check gmail_inbox_bot/metrics.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gmail_inbox_bot/metrics.py tests/test_metrics.py
git commit -m "feat(jev): record_email acepta campos jev_* de la clasificación sombra"
```

---

### Task 6: Migración SQL de `email_metrics`

**Files:**
- Modify: `scripts/supabase_create_table.sql`

- [ ] **Step 1: Añadir el bloque idempotente**

Al final de la sección de migraciones (tras el `ALTER TABLE ... llm_provider TEXT;` y antes de los `CREATE INDEX`), añadir:

```sql
-- Migración idempotente: clasificación sombra con Jev (TypeSafe.ai).
-- Ejecutar ANTES de desplegar el código que las escribe; si no, PostgREST devuelve 400
-- y se pierde la fila entera de métricas de ese email.
ALTER TABLE email_metrics
    ADD COLUMN IF NOT EXISTS jev_category      TEXT,
    ADD COLUMN IF NOT EXISTS jev_confidence    DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS jev_probabilities JSONB,
    ADD COLUMN IF NOT EXISTS jev_latency_ms    INTEGER,
    ADD COLUMN IF NOT EXISTS jev_model         TEXT,
    ADD COLUMN IF NOT EXISTS jev_error         TEXT;
```

Y junto a los otros índices:

```sql
CREATE INDEX IF NOT EXISTS idx_email_metrics_jev_category ON email_metrics (jev_category);
```

- [ ] **Step 2: Aplicar la migración en Supabase (producción)**

```bash
uv run python scripts/supabase_sql.py "ALTER TABLE email_metrics ADD COLUMN IF NOT EXISTS jev_category TEXT, ADD COLUMN IF NOT EXISTS jev_confidence DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS jev_probabilities JSONB, ADD COLUMN IF NOT EXISTS jev_latency_ms INTEGER, ADD COLUMN IF NOT EXISTS jev_model TEXT, ADD COLUMN IF NOT EXISTS jev_error TEXT; CREATE INDEX IF NOT EXISTS idx_email_metrics_jev_category ON email_metrics (jev_category);"
```

- [ ] **Step 3: Verificar que las columnas existen**

```bash
uv run python scripts/supabase_sql.py "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='email_metrics' AND column_name LIKE 'jev_%' ORDER BY column_name"
```

Expected: 6 filas (`jev_category text`, `jev_confidence double precision`, `jev_error text`, `jev_latency_ms integer`, `jev_model text`, `jev_probabilities jsonb`).

- [ ] **Step 4: Commit**

```bash
git add scripts/supabase_create_table.sql
git commit -m "feat(jev): migración de columnas jev_* en email_metrics"
```

---

### Task 7: Integración en `bot.py`

**Files:**
- Modify: `gmail_inbox_bot/bot.py` (imports, `_process_email`, `process_mailbox`, `run`)
- Test: `tests/test_bot.py`

- [ ] **Step 1: Escribir los tests**

En `tests/test_bot.py`, añadir al final de la clase que contiene `test_successful_classification_and_execute` (buscar `class Test` sobre la línea 210). Comprobar primero cómo obtiene ese archivo `_make_email` (`grep -n "_make_email" tests/test_bot.py | head -1`) y seguir el mismo patrón.

```python
    @patch("gmail_inbox_bot.bot.record_email")
    @patch("gmail_inbox_bot.bot.execute", return_value="tagged")
    @patch(
        "gmail_inbox_bot.bot.classify_email",
        return_value={"categoria": "spam", "razon_clasificacion": "promo"},
    )
    @patch("gmail_inbox_bot.bot.load_prompt", return_value="system prompt")
    def test_jev_shadow_runs_after_successful_classification(
        self, _mock_load, _mock_classify, _mock_execute, mock_record, mock_gmail, config
    ):
        jev = MagicMock()
        jev.classify.return_value = {
            "jev_category": "spam",
            "jev_confidence": 0.9,
            "jev_probabilities": {"spam": 0.9, "otros": 0.1},
            "jev_latency_ms": 500,
            "jev_model": "jev-1.13.0",
        }
        msg = _make_email(subject="Oferta", body_html="<p>Compra ya</p>")

        _process_email(mock_gmail, MagicMock(), config, msg, jev=jev)

        jev.classify.assert_called_once()
        call = jev.classify.call_args.kwargs
        assert call["subject"] == "Oferta"
        assert call["body_text"] == "Compra ya"
        assert call["sender_address"] == "juan@empresa.com"
        recorded = mock_record.call_args.kwargs
        assert recorded["jev_category"] == "spam"
        assert recorded["jev_confidence"] == 0.9
        assert recorded["jev_latency_ms"] == 500

    @patch("gmail_inbox_bot.bot.record_email")
    @patch("gmail_inbox_bot.bot.classify_email", return_value=None)
    @patch("gmail_inbox_bot.bot.load_prompt", return_value="system prompt")
    def test_jev_shadow_skipped_when_classification_fails(
        self, _mock_load, _mock_classify, _mock_record, mock_gmail, config
    ):
        jev = MagicMock()
        _process_email(mock_gmail, MagicMock(), config, _make_email(), jev=jev)
        jev.classify.assert_not_called()

    @patch("gmail_inbox_bot.bot.record_email")
    @patch("gmail_inbox_bot.bot.execute", return_value="tagged")
    @patch(
        "gmail_inbox_bot.bot.classify_email",
        return_value={"categoria": "spam", "razon_clasificacion": ""},
    )
    @patch("gmail_inbox_bot.bot.load_prompt", return_value="system prompt")
    def test_jev_shadow_none_records_no_jev_fields(
        self, _mock_load, _mock_classify, _mock_execute, mock_record, mock_gmail, config
    ):
        _process_email(mock_gmail, MagicMock(), config, _make_email(), jev=None)
        recorded = mock_record.call_args.kwargs
        assert not any(key.startswith("jev_") for key in recorded)
```

Y en la sección `process_mailbox` del mismo archivo:

```python
    @patch("gmail_inbox_bot.bot._process_email", return_value="ok")
    def test_process_mailbox_forwards_jev(self, mock_process, mock_gmail, config):
        mock_gmail.get_unread_emails.return_value = [_make_email()]
        jev = MagicMock()
        process_mailbox(mock_gmail, None, config, jev=jev)
        assert mock_process.call_args.kwargs["jev"] is jev
```

- [ ] **Step 2: Ejecutar y ver que falla**

```bash
uv run pytest -q tests/test_bot.py -k jev
```

Expected: FAIL con `TypeError: _process_email() got an unexpected keyword argument 'jev'`.

- [ ] **Step 3: Implementar en `bot.py`**

Import (junto a los demás `from .`):

```python
from .jev_shadow import JevShadow, build_jev_shadow
```

Firma de `_process_email`:

```python
def _process_email(
    gmail: GmailClient,
    openai_client: SynchronousLLMGateway | None,
    config: dict,
    email_msg: dict,
    *,
    dry_run: bool = False,
    jev: JevShadow | None = None,
) -> str:
```

Justo después del bloque `if not classification: ... return "classification failed — tagged ERROR IA"` y antes de `# 5. Notify important emails`:

```python
    # 4b. Clasificación sombra con Jev (no decide nada, solo se registra)
    jev_result: dict = {}
    if jev is not None:
        jev_result = jev.classify(
            subject=subject,
            body_text=body_text,
            sender_name=sender_name,
            sender_address=sender,
            has_attachments=has_attachments,
        )
        if "jev_category" in jev_result:
            llm_category = classification.get("categoria", "")
            verdict = "coinciden" if jev_result["jev_category"] == llm_category else "DIFIEREN"
            log.info(
                "[%s] 🕶️ Jev sombra: jev=%s (conf=%.2f) | llm=%s | %s | %dms",
                msg_id,
                jev_result["jev_category"],
                jev_result["jev_confidence"],
                llm_category,
                verdict,
                jev_result["jev_latency_ms"],
            )
```

En la llamada final `record_email(...)` del paso `# 7. Record metric`, añadir como último argumento:

```python
        **jev_result,
```

Firma y cuerpo de `process_mailbox`:

```python
def process_mailbox(
    gmail: GmailClient,
    openai_client: SynchronousLLMGateway | None,
    config: dict,
    *,
    dry_run: bool = False,
    query: str = "is:unread in:inbox",
    jev: JevShadow | None = None,
) -> list[str]:
```

y dentro del bucle:

```python
            result = _process_email(
                gmail, openai_client, config, email_msg, dry_run=dry_run, jev=jev
            )
```

En `run()`, tras `openai_client = _build_llm_clients(env)`:

```python
    jev = build_jev_shadow(env)
```

y en el bucle de polling:

```python
            process_mailbox(gmail, openai_client, config, dry_run=dry_run, query=query, jev=jev)
```

- [ ] **Step 4: Ejecutar toda la suite**

```bash
uv run pytest -q && uv run ruff check . && uv run ruff format --check .
```

Expected: todo PASS. Si `ruff format --check` marca `bot.py`, ejecutar `uv run ruff format gmail_inbox_bot/bot.py tests/test_bot.py`.

- [ ] **Step 5: Commit**

```bash
git add gmail_inbox_bot/bot.py tests/test_bot.py
git commit -m "feat(jev): clasificación sombra en el pipeline, log comparativo y métricas"
```

---

### Task 8: Endpoint `/admin/api/jev_shadow` y agregación

**Files:**
- Modify: `gmail_inbox_bot/admin_dashboard.py`
- Create: `tests/test_admin_dashboard.py`

- [ ] **Step 1: Escribir los tests de la agregación pura**

Crear `tests/test_admin_dashboard.py`:

```python
"""Tests for admin_dashboard aggregations (sin red)."""

from gmail_inbox_bot.admin_dashboard import JEV_CATEGORIES, _aggregate_jev


def _row(category, jev_category, confidence, *, error=None, created="2026-09-21T10:00:00"):
    return {
        "category": category,
        "jev_category": jev_category,
        "jev_confidence": confidence,
        "jev_error": error,
        "sender": "a@b.c",
        "subject": "Asunto",
        "created_at": created,
        "mailbox": "jesus82c",
    }


class TestAggregateJev:
    def test_empty_rows(self):
        result = _aggregate_jev([])
        assert result["total"] == 0
        assert result["agreement_pct"] == 0.0
        assert result["errors"] == 0
        assert result["mismatches"] == []
        assert [bucket["label"] for bucket in result["by_confidence"]] == [
            "<0.5",
            "0.5-0.7",
            "0.7-0.9",
            ">=0.9",
        ]

    def test_agreement_and_confusion(self):
        rows = [
            _row("spam", "spam", 0.95),
            _row("spam", "newsletters", 0.6),
            _row("otros", "otros", 0.3),
            _row("finanzas", "finanzas", 0.8),
        ]
        result = _aggregate_jev(rows)
        assert result["total"] == 4
        assert result["agreement_pct"] == 75.0
        assert result["confusion"]["spam"]["spam"] == 1
        assert result["confusion"]["spam"]["newsletters"] == 1
        assert result["confusion"]["otros"]["otros"] == 1
        assert set(result["confusion"]) == set(JEV_CATEGORIES)

    def test_confidence_buckets(self):
        rows = [
            _row("spam", "spam", 0.95),
            _row("spam", "spam", 0.92),
            _row("spam", "otros", 0.75),
            _row("otros", "otros", 0.55),
            _row("otros", "spam", 0.1),
        ]
        buckets = {b["label"]: b for b in _aggregate_jev(rows)["by_confidence"]}
        assert buckets[">=0.9"] == {"label": ">=0.9", "n": 2, "agreement_pct": 100.0}
        assert buckets["0.7-0.9"] == {"label": "0.7-0.9", "n": 1, "agreement_pct": 0.0}
        assert buckets["0.5-0.7"] == {"label": "0.5-0.7", "n": 1, "agreement_pct": 100.0}
        assert buckets["<0.5"] == {"label": "<0.5", "n": 1, "agreement_pct": 0.0}

    def test_mismatches_newest_first_and_capped(self):
        rows = [
            _row("spam", "otros", 0.5, created=f"2026-09-{day:02d}T10:00:00")
            for day in range(1, 31)
        ] + [_row("spam", "otros", 0.5, created="2026-10-05T10:00:00")]
        result = _aggregate_jev(rows)
        assert len(result["mismatches"]) == 30
        assert result["mismatches"][0]["created_at"] == "2026-10-05T10:00:00"
        assert result["mismatches"][0]["category"] == "spam"
        assert result["mismatches"][0]["jev_category"] == "otros"

    def test_errors_counted_and_excluded_from_agreement(self):
        rows = [
            _row("spam", "spam", 0.9),
            _row("spam", None, None, error="TypeSafeAPITimeoutError: timeout"),
        ]
        result = _aggregate_jev(rows)
        assert result["total"] == 1
        assert result["errors"] == 1
        assert result["agreement_pct"] == 100.0

    def test_unknown_category_is_ignored_in_confusion_but_counted(self):
        rows = [_row("pre_filter:x", "spam", 0.9)]
        result = _aggregate_jev(rows)
        assert result["total"] == 1
        assert "pre_filter:x" not in result["confusion"]
```

- [ ] **Step 2: Ejecutar y ver que falla**

```bash
uv run pytest -q tests/test_admin_dashboard.py
```

Expected: FAIL con `ImportError: cannot import name 'JEV_CATEGORIES'`.

- [ ] **Step 3: Implementar en `admin_dashboard.py`**

Parametrizar `_fetch_metrics` para reutilizar filtros y paginación. Cambiar su firma y la construcción de `params`:

```python
async def _fetch_metrics(
    date_from: str | None,
    date_to: str | None,
    mailbox: str | None,
    *,
    select: str = "mailbox,category,created_at",
    extra_params: dict[str, str] | None = None,
) -> list[dict]:
    """Fetch all matching rows from Supabase, paginating as needed."""
```

y donde se define `params`:

```python
    params: dict[str, str] = {
        "select": select,
        "order": "created_at.asc",
        **(extra_params or {}),
    }
```

Añadir tras `_PAGE_SIZE = 1000`:

```python
JEV_CATEGORIES = (
    "personal",
    "finanzas",
    "compras",
    "newsletters",
    "notificaciones",
    "automatico",
    "spam",
    "otros",
)
_JEV_SELECT = "mailbox,category,jev_category,jev_confidence,jev_error,sender,subject,created_at"
_CONFIDENCE_BUCKETS = (
    ("<0.5", 0.0, 0.5),
    ("0.5-0.7", 0.5, 0.7),
    ("0.7-0.9", 0.7, 0.9),
    (">=0.9", 0.9, 1.01),
)
_MAX_MISMATCHES = 30
```

Añadir tras `_aggregate`:

```python
def _pct(part: int, whole: int) -> float:
    return round(100.0 * part / whole, 1) if whole else 0.0


def _aggregate_jev(rows: list[dict]) -> dict:
    """Compara la categoría del LLM (``category``) con la sombra de Jev (``jev_category``).

    Las filas con ``jev_error`` cuentan como errores y no entran en la coincidencia.
    """
    compared = [r for r in rows if r.get("jev_category")]
    errors = sum(1 for r in rows if r.get("jev_error"))

    agreed = sum(1 for r in compared if r.get("category") == r.get("jev_category"))

    by_confidence = []
    for label, low, high in _CONFIDENCE_BUCKETS:
        bucket = [
            r
            for r in compared
            if r.get("jev_confidence") is not None and low <= float(r["jev_confidence"]) < high
        ]
        bucket_agreed = sum(1 for r in bucket if r.get("category") == r.get("jev_category"))
        by_confidence.append(
            {"label": label, "n": len(bucket), "agreement_pct": _pct(bucket_agreed, len(bucket))}
        )

    confusion = {llm: dict.fromkeys(JEV_CATEGORIES, 0) for llm in JEV_CATEGORIES}
    for r in compared:
        llm, jev = r.get("category"), r.get("jev_category")
        if llm in confusion and jev in confusion[llm]:
            confusion[llm][jev] += 1

    mismatches = sorted(
        (r for r in compared if r.get("category") != r.get("jev_category")),
        key=lambda r: r.get("created_at", ""),
        reverse=True,
    )[:_MAX_MISMATCHES]
    mismatches = [
        {
            "created_at": r.get("created_at", ""),
            "mailbox": r.get("mailbox", ""),
            "sender": r.get("sender", ""),
            "subject": r.get("subject", ""),
            "category": r.get("category", ""),
            "jev_category": r.get("jev_category", ""),
            "jev_confidence": r.get("jev_confidence"),
        }
        for r in mismatches
    ]

    return {
        "total": len(compared),
        "agreement_pct": _pct(agreed, len(compared)),
        "errors": errors,
        "by_confidence": by_confidence,
        "confusion": confusion,
        "categories": list(JEV_CATEGORIES),
        "mismatches": mismatches,
    }
```

Añadir la ruta al final del archivo:

```python
@router.get("/api/jev_shadow")
async def api_jev_shadow(
    request: Request,
    date_from: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    mailbox: str | None = Query(None, description="Filter by mailbox name"),
) -> dict:
    """Comparativa Jev (sombra) vs clasificador LLM."""
    if not _is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        rows = await _fetch_metrics(
            date_from,
            date_to,
            mailbox,
            select=_JEV_SELECT,
            extra_params={"or": "(jev_category.not.is.null,jev_error.not.is.null)"},
        )
    except Exception as exc:
        logger.error("Error fetching Jev shadow metrics from Supabase: %s", exc)
        raise HTTPException(status_code=502, detail="Error fetching metrics") from exc

    result = _aggregate_jev(rows)
    result["filters"] = {"date_from": date_from, "date_to": date_to, "mailbox": mailbox}
    return result
```

Nota PostgREST: el filtro `or=(jev_category.not.is.null,jev_error.not.is.null)` trae tanto las filas comparables como las que fallaron. Si ya existe una clave `and` en `params` (rango de fechas), PostgREST admite `and` y `or` a la vez como parámetros distintos; no hay colisión.

- [ ] **Step 4: Ejecutar y ver que pasa**

```bash
uv run pytest -q tests/test_admin_dashboard.py && uv run pytest -q && uv run ruff check . && uv run ruff format --check .
```

Expected: PASS. (`tests/test_app_scheduler.py` y el resto siguen verdes; `_fetch_metrics` mantiene su comportamiento por defecto.)

- [ ] **Step 5: Commit**

```bash
git add gmail_inbox_bot/admin_dashboard.py tests/test_admin_dashboard.py
git commit -m "feat(jev): endpoint /admin/api/jev_shadow con agregación Jev vs LLM"
```

---

### Task 9: Sección "Jev vs LLM (sombra)" en el dashboard

**Files:**
- Modify: `gmail_inbox_bot/templates/admin_dashboard.html` (HTML tras `tablesContainer`, JS `renderDashboard` y nuevas funciones)

Regla: el dashboard construye tablas con plantillas de cadena + `innerHTML` (patrón existente). Todo valor procedente de la API (`sender`, `subject`, categorías, etiquetas de tramo, fechas) pasa por `escapeHtml` antes de interpolarse; los números pasan por `fmtNum`/`fmtPct`/`fmtConf`.

- [ ] **Step 1: Añadir el HTML**

En `gmail_inbox_bot/templates/admin_dashboard.html`, justo después de `<div class="tables-grid" id="tablesContainer"></div>` (línea ~694) y antes del cierre de `dashboardContent`:

```html
        <div class="section-header">
          <div class="section-title">Jev vs LLM (clasificación sombra)</div>
        </div>
        <div class="cards" id="jevCards"></div>
        <div class="tables-grid" id="jevTables"></div>
        <div class="table-card" id="jevMismatches"></div>
```

- [ ] **Step 2: Añadir el JS**

En `fetchMetrics()`, dentro del `try` y justo después de `renderDashboard(data);`:

```javascript
          fetchJevShadow(params);
```

Añadir estas funciones después de `renderTables` y antes de `applyFilters`:

```javascript
      function escapeHtml(value) {
        return String(value ?? "")
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;");
      }

      function fmtPct(p) {
        return `${Number(p).toFixed(1)}%`;
      }

      function fmtConf(c) {
        return c === null || c === undefined ? "—" : Number(c).toFixed(2);
      }

      async function fetchJevShadow(params) {
        const tables = document.getElementById("jevTables");
        try {
          const res = await fetch(`/admin/api/jev_shadow?${params.toString()}`, {
            cache: "no-store",
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          renderJev(await res.json());
        } catch (err) {
          document.getElementById("jevCards").replaceChildren();
          document.getElementById("jevMismatches").replaceChildren();
          const empty = document.createElement("div");
          empty.className = "empty-state";
          empty.textContent = `Comparativa Jev no disponible: ${err.message}`;
          tables.replaceChildren(empty);
        }
      }

      function renderJev(data) {
        const cards = document.getElementById("jevCards");
        const tables = document.getElementById("jevTables");
        const mismatches = document.getElementById("jevMismatches");

        if (!data.total && !data.errors) {
          cards.replaceChildren();
          mismatches.replaceChildren();
          const empty = document.createElement("div");
          empty.className = "empty-state";
          empty.textContent = "Sin clasificaciones sombra de Jev en este periodo";
          tables.replaceChildren(empty);
          return;
        }

        cards.innerHTML = `
          <div class="card">
            <div class="card-label">Emails comparados</div>
            <div class="card-value total">${fmtNum(data.total)}</div>
          </div>
          <div class="card">
            <div class="card-label">Coincidencia Jev / LLM</div>
            <div class="card-value">${fmtPct(data.agreement_pct)}</div>
          </div>
          <div class="card">
            <div class="card-label">Errores Jev</div>
            <div class="card-value">${fmtNum(data.errors)}</div>
          </div>`;

        let bucketRows = "";
        data.by_confidence.forEach((b) => {
          bucketRows += `<tr><td>${escapeHtml(b.label)}</td><td class="count-cell">${fmtNum(b.n)}</td><td class="count-cell">${fmtPct(b.agreement_pct)}</td></tr>`;
        });

        const cats = data.categories.map(escapeHtml);
        const header = "<th>LLM \\ Jev</th>" + cats.map((c) => `<th>${c}</th>`).join("");
        let matrixRows = "";
        data.categories.forEach((llm, i) => {
          matrixRows += `<tr><td><strong>${cats[i]}</strong></td>`;
          data.categories.forEach((jev) => {
            const n = data.confusion[llm][jev];
            const style = n && llm === jev ? ' style="font-weight:600"' : "";
            matrixRows += `<td class="count-cell"${style}>${n ? fmtNum(n) : ""}</td>`;
          });
          matrixRows += "</tr>";
        });

        tables.innerHTML = `
          <div class="table-card">
            <div class="table-card-header">Coincidencia por confianza de Jev</div>
            <div class="cat-table-wrap">
              <table class="cat-table">
                <thead><tr><th>Confianza</th><th style="text-align:right">N</th><th style="text-align:right">Coinciden</th></tr></thead>
                <tbody>${bucketRows}</tbody>
              </table>
            </div>
          </div>
          <div class="table-card">
            <div class="table-card-header">Matriz LLM × Jev</div>
            <div class="cat-table-wrap">
              <table class="cat-table">
                <thead><tr>${header}</tr></thead>
                <tbody>${matrixRows}</tbody>
              </table>
            </div>
          </div>`;

        if (!data.mismatches.length) {
          mismatches.replaceChildren();
          return;
        }
        let mmRows = "";
        data.mismatches.forEach((m) => {
          const when = escapeHtml((m.created_at || "").substring(0, 16).replace("T", " "));
          mmRows += `<tr>
            <td>${when}</td>
            <td>${escapeHtml(m.sender)}</td>
            <td>${escapeHtml(m.subject)}</td>
            <td>${escapeHtml(m.category)}</td>
            <td>${escapeHtml(m.jev_category)}</td>
            <td class="count-cell">${fmtConf(m.jev_confidence)}</td>
          </tr>`;
        });
        mismatches.innerHTML = `
          <div class="table-card-header">Últimas discrepancias (${fmtNum(data.mismatches.length)})</div>
          <div class="cat-table-wrap">
            <table class="cat-table">
              <thead><tr><th>Fecha</th><th>Remitente</th><th>Asunto</th><th>LLM</th><th>Jev</th><th style="text-align:right">Conf.</th></tr></thead>
              <tbody>${mmRows}</tbody>
            </table>
          </div>`;
      }
```

- [ ] **Step 3: Verificar en el navegador**

```bash
uv run python -m gmail_inbox_bot --server
```

Abrir `http://localhost:8000/admin/logs`, entrar con `LOGS_VIEWER_PASSWORD` del `.env`, ir a `/admin/dashboard`. Expected: aparece la sección "Jev vs LLM (clasificación sombra)"; con la tabla recién migrada y sin datos muestra "Sin clasificaciones sombra de Jev en este periodo". Para probar con datos, insertar una fila de prueba y refrescar:

```bash
uv run python scripts/supabase_sql.py "INSERT INTO email_metrics (mailbox, category, msg_id, jev_category, jev_confidence, sender, subject) VALUES ('jesus82c','spam','test-jev-1','newsletters',0.62,'test@x.com','Prueba <b>Jev</b>')"
```

Expected: 1 comparado, 0.0 % coincidencia, tramo `0.5-0.7` n=1, matriz `spam→newsletters = 1`, una discrepancia listada con el asunto mostrado literalmente como `Prueba <b>Jev</b>` (sin negrita → el escape funciona). Después borrar la fila:

```bash
uv run python scripts/supabase_sql.py "DELETE FROM email_metrics WHERE msg_id='test-jev-1'"
```

Parar el servidor con Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add gmail_inbox_bot/templates/admin_dashboard.html
git commit -m "feat(jev): sección Jev vs LLM en el dashboard admin"
```

---

### Task 10: Documentación, CI local, review y push

**Files:**
- Modify: `CLAUDE.md` (secciones "Clasificación — mejora continua" y "Métricas (Supabase)")
- Modify: `.env.example`
- Modify: `README.md` (donde se listan variables de entorno / features)

- [ ] **Step 1: `.env.example`**

Añadir junto a `OPENAI_API_KEY`/`GROQ_API_KEY`:

```
# TypeSafe.ai (Jev) — clasificación sombra. Opcional: sin clave, la sombra se desactiva.
JEV_API_KEY=
```

- [ ] **Step 2: `CLAUDE.md`**

En la sección `### Clasificación — mejora continua`, añadir al final:

```markdown
**Clasificación sombra con Jev (TypeSafe.ai)** — `gmail_inbox_bot/jev_shadow.py`. Si `JEV_API_KEY`
está en el `.env`, cada email clasificado por el LLM se clasifica también con Jev y el resultado
(`jev_category`, `jev_confidence`, `jev_probabilities`, `jev_latency_ms`, `jev_model`, `jev_error`)
se guarda en la misma fila de `email_metrics`. Jev **no decide nada** en esta fase; la comparativa
está en `/admin/dashboard` (sección "Jev vs LLM"). Los criterios de Jev viven en
`gmail_inbox_bot/prompts/clasificador_jev.yml` (una entrada por categoría con `what` / `not_for` /
`examples`, mismas claves que `routing`). **Mientras dure la sombra, cada regla nueva del prompt LLM
se replica en el YAML de Jev.** Diseño: `docs/superpowers/specs/2026-09-21-jev-shadow-classification-design.md`.
```

En `## Métricas (Supabase)`, añadir un bullet:

```markdown
- **Columnas `jev_*`**: clasificación sombra de Jev; migración en `scripts/supabase_create_table.sql`
  (aplicar antes de desplegar código que las escriba).
```

- [ ] **Step 3: `README.md`**

Buscar la tabla o lista de variables de entorno (`grep -n "GROQ_API_KEY" README.md`) y añadir en el mismo formato una línea para `JEV_API_KEY` ("Opcional. Activa la clasificación sombra con Jev (TypeSafe.ai); ver CLAUDE.md").

- [ ] **Step 4: CI local completa**

```bash
bash scripts/ci-local.sh
```

Expected: `uv sync`, versión 3.13, ruff check, ruff format --check, pytest y hooks — todo en verde. No continuar si algo falla.

- [ ] **Step 5: Commit de docs**

```bash
git add CLAUDE.md .env.example README.md
git commit -m "docs(jev): documenta la clasificación sombra con Jev y JEV_API_KEY"
```

- [ ] **Step 6: Cross-review con Codex**

Feature multi-archivo → gate obligatorio antes del push:

```
/codex:review --wait --scope branch --base 767104b
```

(`767104b` es el HEAD previo a esta feature; ajustar si difiere: `git log --oneline | grep "sube neutral-llm-gateway a 0.17.0"`.) Si hay hallazgos serios: `/codex:rescue --resume "aplica los fixes propuestos"`, re-ejecutar `bash scripts/ci-local.sh` y commitear como `fix(jev): address codex review`.

- [ ] **Step 7: Confirmar la migración antes del push**

```bash
uv run python scripts/supabase_sql.py "SELECT count(*) FROM information_schema.columns WHERE table_name='email_metrics' AND column_name LIKE 'jev_%'"
```

Expected: `6`. **Sin esto, no hacer push** (el deploy es automático y las métricas empezarían a fallar con 400).

- [ ] **Step 8: Push y verificación en producción**

```bash
git push origin main
```

Esperar a que termine la GitHub Action `deploy-vps.yml` (`gh run watch` o `gh run list --limit 1`). Después:

```bash
curl -s https://email.pymechat.com/health
ssh ubuntu@158.69.215.223 "docker logs gmail-inbox-bot --tail 50 2>&1 | grep -E 'Jev|jev'"
```

Expected: `health` OK y en logs `Clasificación sombra con Jev activada (8 categorías)`; tras el primer poll con emails nuevos, líneas `🕶️ Jev sombra: jev=... | llm=... | coinciden|DIFIEREN`. Comprobar `https://email.pymechat.com/admin/dashboard` → sección Jev con datos.

Si en el VPS no aparece "activada": el `.env` del VPS (`/home/ubuntu/services/gmail-inbox-bot/.env`) no tiene `JEV_API_KEY`; añadirla allí y `docker compose up -d` (el `.env` de producción no se despliega desde git).

---

## Self-review del plan

- **Cobertura de la spec**: §1 flujo → Task 7; §2 módulo → Task 3 (+ Task 1 dependencia, Task 4 env); §3 criterios → Task 2; §4 métricas y migración → Tasks 5-6; §5 dashboard → Tasks 8-9; §6 errores/límites → constantes en Task 3 y `jev_error` en Tasks 3/5/8; §7 tests → cada task; documentación y gates → Task 10.
- **Consistencia de nombres**: `JevShadow.classify(subject=, body_text=, sender_name=, sender_address=, has_attachments=)` igual en Tasks 3 y 7; claves `jev_category/jev_confidence/jev_probabilities/jev_latency_ms/jev_model/jev_error` iguales en Tasks 3, 5, 6, 7, 8 y 9; `_aggregate_jev` devuelve `total/agreement_pct/errors/by_confidence/confusion/categories/mismatches`, que es lo que consume `renderJev` en Task 9.
- **Sin placeholders**: todos los pasos llevan código o comando concreto.
