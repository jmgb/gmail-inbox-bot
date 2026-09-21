# Clasificación sombra con Jev (TypeSafe.ai) — diseño

Fecha: 2026-09-21
Estado: aprobado por el usuario, pendiente de plan de implementación

## Objetivo

Evaluar el modelo **Jev** de TypeSafe.ai como clasificador de emails de gmail-inbox-bot
**sin cambiar el comportamiento en producción**. Jev corre en modo sombra: clasifica cada
email que ya clasificó el LLM actual, y su resultado se guarda junto al real para comparar
coincidencia y calibrar un umbral de confianza. La decisión de sustituir el clasificador
LLM se toma en una fase 2, con datos.

## Contexto

- Clasificador actual: `gmail_inbox_bot/classifier.py:classify_email` → `neutral-llm-gateway`
  (`openai/gpt-oss-120b` en Groq, fallback `gpt-5.6-luna`), prompt
  `gmail_inbox_bot/prompts/clasificador_inbox.txt`, respuesta JSON
  `{"categoria", "razon_clasificacion"}`.
- Categorías (fuente: `routing` del YAML de cada mailbox): `personal`, `finanzas`, `compras`,
  `newsletters`, `notificaciones`, `automatico`, `spam`, `otros`.
- Métricas: fila por `msg_id` en `email_metrics` (Supabase), escritas por
  `metrics.record_email` con upsert fire-and-forget.
- Jev: API "System One". Se envía `state` (texto) + preguntas tipadas; la primitiva `Choice`
  devuelve `choice`, `probabilities` (por opción, suman 1) y `confidence` (0-1). No genera
  texto libre, por lo que no hay equivalente de `razon_clasificacion`.
- SDK: `typesafe-sdk>=0.7.0` (`TypeSafeClient`, `Choice`, `RetryPolicy`,
  `TypeSafeAPIError`). Usa `httpx2`, ya presente en el proyecto. La clave se pasa
  explícitamente desde `JEV_API_KEY` (el SDK por defecto lee `TYPESAFE_API_KEY`; no se usa).

## Decisiones tomadas con el usuario

1. Modo sombra primero (no sustitución ni fallback).
2. Resultado sombra en columnas nuevas de `email_metrics` + sección comparativa en el
   dashboard admin.
3. Criterios de Jev en un archivo propio `prompts/clasificador_jev.yml`, traducidos a mano del
   prompt LLM. El prompt LLM sigue siendo la fuente de verdad en producción.

## Diseño

### 1. Flujo

En `bot.py`, tras `classify_email(...)`, y **solo si devolvió un resultado**, se llama a
`jev_shadow.classify(...)` con el mismo asunto, remitente, flag de adjuntos y body en texto
plano. El dict que devuelve se fusiona en la llamada a `record_email` del mismo `msg_id`.

Jev no participa en routing, notificaciones ni acciones. Los emails resueltos por pre-filtro y
los que fallan en el clasificador real no pasan por Jev.

Activación: global, si `JEV_API_KEY` está definida en el entorno. Si falta, el cliente es
`None`, se loguea una vez al arrancar (`log.info`) y el resto del flujo no cambia. No hay flag
por mailbox.

En `dry_run` Jev se ejecuta (es solo lectura). Las métricas se persisten igual que hoy
(`record_email` no distingue `dry_run`), así que los campos `jev_*` también.

### 2. Módulo `gmail_inbox_bot/jev_shadow.py`

Responsabilidad única: hablar con Jev y devolver un dict plano listo para métricas. Sin
dependencias del gateway LLM.

```python
def build_jev_client(env: Mapping[str, str]) -> TypeSafeClient | None
def load_criteria(path: str | Path) -> dict[str, dict]
def build_state(subject, body_text, sender_name, sender_address, has_attachments) -> str
def classify(client, criteria, *, subject, body_text, sender_name, sender_address,
             has_attachments) -> dict
```

- `build_jev_client`: `TypeSafeClient(api_key=env["JEV_API_KEY"], timeout=8.0,
  retry=RetryPolicy(max_retries=1))`. Devuelve `None` si la clave está vacía o ausente. Se
  construye una sola vez en `bot._build_llm_clients` (o función hermana) y se pasa a
  `process_email` junto al gateway.
- `load_criteria`: lee el YAML y devuelve `{categoria: {"what": str, "not_for": str,
  "examples": [str, ...]}}` tal cual lo espera `Choice(criteria=...)`. Se carga una vez por
  arranque, no por email.
- `build_state`: mismo contenido que el `user_content` actual del clasificador
  (`Título del email`, `¿Contiene archivo adjunto?`, `Remitente`, `Contenido del email`) sin la
  frase "Responde en formato JSON". Body truncado a `JEV_MAX_BODY_CHARS = 6000` caracteres.
- `classify`: `client.system_one(state, {"categoria": Choice(instructions=..., criteria=criteria)})`
  con `instructions` = "¿En qué categoría encaja este email recibido en la bandeja de entrada
  personal del usuario?". Mide latencia con `time.perf_counter()`. Devuelve:

  ```python
  {
    "jev_category": str,            # answer.choice
    "jev_confidence": float,        # answer.confidence
    "jev_probabilities": dict,      # answer.probabilities, {categoria: float}
    "jev_latency_ms": int,
    "jev_model": str | None,        # response.model si existe
  }
  ```

  Ante **cualquier** excepción (`TypeSafeAPIError`, timeout, red, KeyError por respuesta
  inesperada) hace `log.warning(..., exc_info=True)` y devuelve
  `{"jev_error": f"{type(exc).__name__}: {exc}"[:200], "jev_latency_ms": int}`. Nunca propaga.
- Log por email en `INFO`: `🕶️ Jev sombra: jev=<cat> (conf=0.93) | llm=<cat> | <coinciden|DIFIEREN>`.
  El mensaje se emite desde `bot.py`, que conoce ambas categorías.

Dependencia nueva en `pyproject.toml`: `typesafe-sdk>=0.7.0` (`uv add`, `uv.lock` actualizado).

### 3. Criterios: `gmail_inbox_bot/prompts/clasificador_jev.yml`

Formato:

```yaml
personal:
  what: >
    Email directo de una persona real que espera respuesta o acción del usuario...
  not_for: >
    Notificaciones automáticas aunque el remitente sea conocido; newsletters; cold outreach
    comercial de desconocidos (eso es spam).
  examples:
    - "Asunto: 'Signature Required' de un servicio que exige acción urgente del usuario"
    - ...
finanzas:
  ...
```

Las 8 categorías del prompt actual, con `what` y `not_for` tomados de las definiciones del
`.txt` y las "Reglas aprendidas de producción" repartidas como `examples`/`not_for` en la
categoría afectada (bancos y brokers → `finanzas`; cold outreach → `spam`; unsubscribe con
suscripción voluntaria → `newsletters`; body vacío con remitente desconocido → `otros`;
"Action Required" urgente → `personal`, rutinario → `notificaciones`). Las reglas de
desempate del prompt ("duda spam/newsletters → spam", "duda notificaciones/personal →
personal", "otros es el fallback") van en el `not_for` de la opción perdedora.

Invariante (test): `set(criteria) == set(config["routing"])` para cada YAML de `config/`.

Mantenimiento durante la sombra: cada regla nueva que se añada al prompt LLM se replica en
el YAML de Jev. Se documenta en `CLAUDE.md`, sección "Clasificación — mejora continua".

### 4. Métricas y migración

`metrics.record_email` gana kwargs opcionales, todos `None` por defecto:

| kwarg | tipo Python | columna Supabase |
|---|---|---|
| `jev_category` | `str` | `TEXT` |
| `jev_confidence` | `float` | `DOUBLE PRECISION` |
| `jev_probabilities` | `dict[str, float]` | `JSONB` |
| `jev_latency_ms` | `int` | `INTEGER` |
| `jev_model` | `str` | `TEXT` |
| `jev_error` | `str` | `TEXT` |

Solo se incluyen en el payload los que no son `None` (mismo patrón que el resto de campos
opcionales). `bot.py` hace `record_email(..., **jev_result)` donde `jev_result` es el dict de
`jev_shadow.classify` (o `{}` si el cliente es `None`).

`scripts/supabase_create_table.sql`: bloque idempotente

```sql
ALTER TABLE email_metrics
    ADD COLUMN IF NOT EXISTS jev_category      TEXT,
    ADD COLUMN IF NOT EXISTS jev_confidence    DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS jev_probabilities JSONB,
    ADD COLUMN IF NOT EXISTS jev_latency_ms    INTEGER,
    ADD COLUMN IF NOT EXISTS jev_model         TEXT,
    ADD COLUMN IF NOT EXISTS jev_error         TEXT;
CREATE INDEX IF NOT EXISTS idx_email_metrics_jev_category ON email_metrics (jev_category);
```

Orden de despliegue obligatorio: **primero** la migración (`uv run python
scripts/supabase_sql.py "..."`), **después** el push a `main`. Si el código escribe columnas
inexistentes, PostgREST devuelve 400 y se pierde la fila entera de métricas de ese email.

### 5. Dashboard: sección "Jev vs LLM"

Endpoint nuevo en `admin_dashboard.py`: `GET /admin/api/jev_shadow?days=7` (misma
autenticación que `/admin/api/metrics`). Consulta a Supabase:
`select=category,jev_category,jev_confidence,jev_error,sender,subject,created_at&jev_category=not.is.null&created_at=gte.<fecha>`
(más una cuenta de filas con `jev_error` no nulo para reportar tasa de fallo).

Agregación (función pura `_aggregate_jev(rows) -> dict`, testeable sin red):

- `total`: filas con `jev_category`; `agreement_pct`: `category == jev_category`.
- `by_confidence`: tramos `<0.5`, `0.5-0.7`, `0.7-0.9`, `>=0.9` con `n` y `agreement_pct`.
- `confusion`: matriz LLM × Jev sobre las 8 categorías (dict de dicts con conteos).
- `mismatches`: últimas 30 discrepancias con `created_at`, `sender`, `subject`, `category`,
  `jev_category`, `jev_confidence`.
- `errors`: nº de filas con `jev_error`.

Render: bloque nuevo en la plantilla del dashboard existente (Jinja + tablas HTML, mismo CSS,
sin librerías de gráficos). Se carga por `fetch` como el resto de métricas.

### 6. Errores y límites

- Timeout 8 s + 1 reintento → peor caso ~16 s extra por email. Con polling cada 600 s y
  ≤50 emails por poll es asumible en sombra.
- Fallos de Jev nunca afectan al routing, a las notificaciones Telegram ni al `ERROR IA`.
- Sin alertas Telegram por fallos de Jev; se ven en `jev_error` y en el dashboard.
- Coste: una llamada Jev por email clasificado. No se estima coste USD en esta fase (no se
  conoce el pricing desde el SDK); `usage` de Jev no se persiste.

### 7. Tests

- `tests/test_jev_shadow.py`
  - `build_jev_client` devuelve `None` sin clave y un cliente con clave.
  - `build_state` incluye asunto, remitente, adjuntos y body; trunca a 6000 chars.
  - `classify` mapea una respuesta simulada (`choice`, `confidence`, `probabilities`) al dict
    esperado y mide latencia.
  - `classify` captura excepciones del cliente y devuelve `jev_error` sin propagar.
  - `load_criteria` produce `what/not_for/examples` por categoría.
  - Paridad: claves del YAML == claves de `routing` en cada `config/*.yml`.
- `tests/test_bot.py`: Jev se invoca solo cuando `classify_email` devuelve resultado; con
  cliente `None` no se invoca; sus campos llegan a `record_email`.
- `tests/test_metrics.py`: los kwargs `jev_*` entran en el payload y se omiten si son `None`.
- `tests/test_admin_dashboard.py` (o el existente): `_aggregate_jev` sobre filas sintéticas
  (coincidencia, tramos, matriz, discrepancias, errores).

Gate antes de commit: `bash scripts/ci-local.sh` (ruff + pytest) y `/codex:review --wait`
(feature multi-archivo).

### 8. Fuera de alcance (fase 2)

- Jev como clasificador real y routing por `confidence` (p. ej. baja confianza → `otros`).
- Uso de `probabilities` para segunda opinión o categoría alternativa.
- Retirar el prompt LLM de clasificación o el fallback Groq/OpenAI.
- Flag por mailbox, alertas de Jev, estimación de coste.
