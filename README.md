# Gmail Inbox Bot

Bot de automatización de email **single-user**, construido sobre la **Gmail API**. Lee los correos no
leídos del inbox, los **clasifica con un LLM** y ejecuta acciones (etiquetar, archivar, responder,
reenviar, crear borradores…) según reglas declarativas por cuenta. Además envía **recordatorios
diarios de reuniones** leyendo Google Calendar.

La entrada de correo llega vía **Cloudflare Email Routing → Gmail inbox**. Gmail es la **fuente de
verdad** (no hay base de datos de correos): el bot lee y escribe sobre el mismo buzón con labels.

> Origen: el repo nace como separación de `pacto-mundial-bot` para tener una versión Gmail-native sin
> acoplamiento a Microsoft Graph / Outlook.

---

## Índice

- [Arquitectura](#arquitectura)
- [Pipeline de procesamiento de email](#pipeline-de-procesamiento-de-email)
- [Categorías y enrutado del inbox](#categorías-y-enrutado-del-inbox)
- [Pre-filtros](#pre-filtros)
- [Acciones disponibles](#acciones-disponibles)
- [Clasificador (LLM)](#clasificador-llm)
- [Plantillas de respuesta y firma](#plantillas-de-respuesta-y-firma)
- [Recordatorios de Google Calendar](#recordatorios-de-google-calendar)
- [Idempotencia y prevención de bucles](#idempotencia-y-prevención-de-bucles)
- [Notificaciones (Telegram) e Interactive Brokers](#notificaciones-telegram-e-interactive-brokers)
- [Métricas (Supabase)](#métricas-supabase)
- [Panel de administración](#panel-de-administración)
- [Configuración](#configuración)
- [OAuth2 y scopes](#oauth2-y-scopes)
- [Archivo local y limpieza de adjuntos](#archivo-local-y-limpieza-de-adjuntos)
- [Comandos](#comandos)
- [Despliegue](#despliegue)
- [Estructura del proyecto](#estructura-del-proyecto)

---

## Arquitectura

```text
Cloudflare Email Routing ─► Gmail inbox
                              │
        (cada poll, ~10 min)  ▼
                     Bot de polling ──► Gmail API
                       1. lee no leídos (is:unread in:inbox)
                       2. idempotencia (PROCESSED_TAGS)
                       3. pre-filtros (por remitente/asunto)
                       4. clasifica con LLM (OpenAI/Groq)
                       5. ejecuta acción (tag/move/reply/forward/…)
                       6. registra métrica (Supabase)

        (cada día 09:16)      ▼
                     Scheduler de recordatorios ──► Google Calendar API
                       reuniones de hoy con 1-2 invitados → email a cada asistente
```

Todo corre en un único proceso (`--server`): un servidor **FastAPI** (admin UI + health) con **dos
daemon threads** en background — el bot de polling y el scheduler de recordatorios.

---

## Pipeline de procesamiento de email

Por cada email no leído del inbox (`gmail_inbox_bot/bot.py::_process_email`):

1. **Idempotencia** — si el email ya tiene algún tag de `PROCESSED_TAGS`, se salta.
2. **Pre-filtros** — reglas rápidas por remitente/asunto que cortocircuitan la clasificación
   (`mail_processing.py::apply_pre_filters`). Si alguna coincide, se ejecuta su acción y se termina.
3. **Detección de reenvíos** — si el remitente coincide con `forwarded_from`, se intenta extraer el
   remitente original del cuerpo para responder a la persona correcta (o forzar borrador con aviso si
   no se puede extraer).
4. **Clasificación LLM** — devuelve `categoria`, `idioma` y `razon_clasificacion`. Si falla → tag
   `ERROR IA` (queda sin leer en el inbox).
5. **Notificación** — si la categoría está en `NOTIFY_CATEGORIES`, avisa por Telegram (actualmente
   desactivado, ver más abajo).
6. **Acción** — según `routing[categoria]` se ejecuta el handler correspondiente
   (`actions.py::execute`).
7. **Métrica** — registro fire-and-forget en Supabase (categoría, acción, modelo, tokens, coste…).

Los fallos de un email se aíslan: se etiqueta `ERROR IA` y se continúa con el siguiente.

---

## Categorías y enrutado del inbox

**Premisa**: lo que requiere acción del usuario se queda **sin leer en el inbox** con una etiqueta del
bot; el resto se mueve fuera del inbox a su carpeta (label).

| Categoría | Destino | ¿En INBOX? | ¿Unread? | Motivo |
|---|---|:---:|:---:|---|
| `personal` | tag `REVISAR IA` | Sí | Sí | Requiere acción/respuesta |
| `finanzas` | tag `REVISAR IA` | Sí | Sí | Verificación/acción financiera |
| `otros` | tag `REVISAR IA` | Sí | Sí | Fallback seguro — revisión manual |
| `compras` | carpeta `Compras` | No | No | Informativo |
| `notificaciones` | carpeta `Notificaciones` | No | No | Alertas de apps |
| `automatico` | carpeta `Automatico` | No | No | Out-of-office, noreply |
| `newsletters` | carpeta `Newsletters` | No | **Sí** | Se conserva sin leer para lectura eventual |
| `spam` | papelera | No | — | Basura (recuperable 30 días) |
| error clasificador | tag `ERROR IA` | Sí | Sí | Fallo técnico pre-clasificación |
| error config/acción | tag `PENDIENTE GESTIONAR` | Sí | Sí | Sin template/routing/acción desconocida |

> **Nunca** se borra permanentemente nada: `delete` mueve a papelera (recuperable 30 días).

---

## Pre-filtros

Se evalúan **antes** de clasificar, en orden, y la primera coincidencia gana. Útiles para silenciar o
enrutar remitentes conocidos sin gastar una llamada al LLM. Criterios de match:
`sender_contains`, `sender_not_contains`, `subject_contains`, `subject_not_contains` (string o lista).

Acciones de pre-filtro: `silent`, `tag`, `tag_and_move`, `delete`, `ib_trade`.

```yaml
pre_filters:
  - name: GitHub notifications (archivar)
    match:
      sender_contains: notifications@github.com
    action: tag_and_move
    tag: Notificaciones
    folder: Notificaciones
```

---

## Acciones disponibles

Definidas en `routing[categoria].action` (`actions.py`):

| Acción | Efecto |
|---|---|
| `tag` | Añade una etiqueta; respeta `is_read` (puede dejarlo sin leer en el inbox) |
| `move` | Marca leído + mueve a carpeta (quita `INBOX`) |
| `silent` | Marca leído, sin más |
| `delete` | Mueve a papelera |
| `reply` | Responde con una plantilla fija (por categoría e idioma) |
| `reply_with_attachment` | Responde con plantilla + adjuntos |
| `dynamic_reply` | Genera la respuesta con el LLM (`response_prompt_file`) |
| `forward` | Reenvía a un destinatario fijo (`destination`) |
| `tag_and_move` | Etiqueta y mueve |
| `reply_and_move` | Responde y mueve |

Todos los salientes (`reply`, `dynamic_reply`, `reply_with_attachment`, `forward`) incluyen la **firma
HTML** (ver abajo). Los borradores añaden un banner con el motivo de clasificación de la IA (solo en
borrador, nunca en emails enviados).

---

## Clasificador (LLM)

`classifier.py` usa la **Responses API** de OpenAI con salida `json_object`. El prompt vive en
`gmail_inbox_bot/prompts/clasificador_inbox.txt` (referenciado por `classifier.prompt_file`).

- **Modelo por defecto**: `openai/gpt-oss-120b` vía **Groq** (`GROQ_API_KEY`).
- **Fallback automático**: si Groq falla (quota/caída/rate-limit) reintenta con `gpt-5.6-terra` vía
  **OpenAI** (`OPENAI_API_KEY`).
- **Override por cuenta**: `classifier.model` en el YAML.

El prompt tiene dos bloques: **reglas generales** (definiciones de categoría) y **reglas aprendidas de
producción** (refinamientos por dominio/remitente a partir de errores reales). Para mejorar la
clasificación se añaden reglas concretas a este último bloque (preferir reglas por remitente/dominio
sobre contenido del body).

---

## Plantillas de respuesta y firma

- **Plantillas por categoría/idioma** en `templates` del YAML (`esp` / `pt`), con soporte de
  **variantes por fecha** (`valid_from` / `valid_until` + `default`).
- **Firma** (`templates/signature.html`): footer HTML de marketing de **aiship.co**, siempre en
  inglés, añadido a todos los salientes. Personalizable por cuenta con `signature_file:` o
  desactivable con `signature_file: ""`.
  - **Excepción**: los recordatorios de Calendar **no** llevan este footer (ver abajo).

---

## Recordatorios de Google Calendar

Un **scheduler interno** (`calendar_reminders.py`, segundo daemon thread) revisa cada mañana el
calendario de cada cuenta y envía a los asistentes un recordatorio de las reuniones del día.

- **A quién**: solo reuniones con **1 o 2 invitados** además del titular (1:1 y tríos). Se excluyen
  eventos all-day, cancelados, los que el titular rechazó, los invitados que rechazaron, los recursos
  /salas y los eventos sin invitados humanos.
- **Cuándo**: a la hora `send_time` de cada mailbox (por defecto **09:16 Europe/Madrid**). La hora
  "rota" (no en punto) es deliberada: busca que el mensaje parezca escrito a mano en un momento
  cualquiera, no un cron disparando a las 9:00.
- **Qué envía**: email en **prosa natural**, firmado con `sender_name`, **sin** footer de marketing.
  El saludo usa el nombre real del invitado y nunca muestra su email. HTML con autoescape (los campos
  de Calendar se escapan).
- **Idempotencia**: estado JSON en `logs/calendar_reminders_state.json` (volumen docker). Dedupe
  **global entre cuentas** por `iCalUID` + invitado → cada persona recibe un único recordatorio por
  reunión. Si un envío falla, ese día no se marca completado y se reintenta en el siguiente tick.
- **Credenciales**: reutiliza el OAuth de Gmail (mismo refresh token, ampliado con `calendar.readonly`).
- **Limitación**: solo se lee el calendario **`primary`** de cada cuenta; las reuniones en calendarios
  secundarios (compartidos/de equipo) no generan recordatorio.

Configuración opt-in por mailbox:

```yaml
calendar_reminders:
  enabled: true
  send_time: "09:16"
  timezone: Europe/Madrid
  max_attendees: 2        # invitados además del titular
  sender_name: Miguel     # nombre con el que se firma
```

Ejecución manual / pruebas:

```bash
uv run python -m gmail_inbox_bot.calendar_reminders --once --dry-run   # lista sin enviar ni guardar estado
uv run python -m gmail_inbox_bot.calendar_reminders --once             # envía una vez ahora
```

---

## Idempotencia y prevención de bucles

Dos mecanismos evitan reprocesar o entrar en bucle:

1. **Acciones que quitan `INBOX`** (`move`, `tag_and_move`…) → el query `is:unread in:inbox` no vuelve
   a encontrar el email.
2. **`already_processed()`** → si el email tiene un tag de `PROCESSED_TAGS` (`RESPONDIDO IA`,
   `REVISAR IA`, `ERROR IA`, `PENDIENTE GESTIONAR`…), se salta.

Para categorías que se quedan en el inbox (`personal`, `finanzas`, `otros`): el email queda **sin leer
con `REVISAR IA`**; en el siguiente poll `already_processed()` lo detecta y lo salta. El usuario lo ve;
el bot no lo reprocesa.

---

## Notificaciones (Telegram) e Interactive Brokers

- **Telegram** (`telegram.py`, `notifications.py`): infraestructura para avisar de emails importantes.
  Actualmente **desactivado** (`NOTIFY_CATEGORIES` vacío) — se reactiva añadiendo categorías al
  frozenset. Requiere `TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`.
- **Interactive Brokers** (`ib_trades.py`): el pre-filtro `ib_trade` parsea el asunto de los emails de
  ejecución de IB (`SOLD 1,511 VEEA @ 0.5722 (Uxxx)`) y envía una notificación de trade por Telegram.

---

## Métricas (Supabase)

`metrics.py` hace un **upsert fire-and-forget** a la tabla `email_metrics` por cada email procesado
(categoría, acción, modelo, tokens, coste USD, remitente, asunto…). Cualquier error se loguea pero
**nunca** propaga al bot. Requiere `SUPABASE_URL` y `SUPABASE_SECRET_KEY`.

---

## Panel de administración

Servido por FastAPI cuando se arranca con `--server`:

| Ruta | Descripción |
|---|---|
| `/health` | Healthcheck |
| `/admin/dashboard` | Dashboard de métricas |
| `/admin/logs` | Visor de logs (protegido con `LOGS_VIEWER_PASSWORD`) |
| `/admin/api/metrics` | API JSON de métricas (consumida por el dashboard) |

---

## Configuración

### Por cuenta — `config/<cuenta>.yml`

Cada YAML del directorio `config/` es una cuenta que el bot monitoriza. Campos principales:

```yaml
name: jesus82c
email: jesus82c@gmail.com
refresh_token_env: GOOGLE_REFRESH_TOKEN_JESUS82C   # variable .env con el refresh token
# send_as: alias@midominio.com                     # opcional
query: is:unread in:inbox
max_emails_per_poll: 50
poll_interval_seconds: 600

classifier:
  prompt_file: gmail_inbox_bot/prompts/clasificador_inbox.txt
  # model: gpt-5.6-terra                  # override opcional

calendar_reminders:                                 # opt-in (ver sección)
  enabled: true
  send_time: "09:16"
  timezone: Europe/Madrid
  max_attendees: 2
  sender_name: Miguel

pre_filters: [ ... ]                                # ver sección
routing: { categoria: { action: ..., ... } }        # ver secciones
templates: { categoria: { esp: "...", pt: "..." } } # respuestas fijas
```

### Variables de entorno — `.env`

| Variable | Uso |
|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Cliente OAuth (compartido) |
| `GOOGLE_REFRESH_TOKEN_<CUENTA>` | Refresh token por cuenta (referenciado en el YAML) |
| `OPENAI_API_KEY` | LLM (clasificación, dynamic_reply, fallback) |
| `GROQ_API_KEY` | LLM por defecto (`gpt-oss-120b`) |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | Notificaciones (opcional) |
| `SUPABASE_URL` / `SUPABASE_SECRET_KEY` | Métricas (opcional) |
| `LOGS_VIEWER_PASSWORD` | Password del visor de logs |
| `SENTRY_DSN` | Observabilidad (opcional) |
| `LOG_LEVEL` / `ENVIRONMENT` | Runtime |
| `DISABLE_BOT` | Si truthy, solo admin UI (sin polling ni scheduler) |
| `DRY_RUN` | Si truthy, los background threads operan en seco |

---

## OAuth2 y scopes

- App OAuth **External + In production** (proyecto GCP). Tokens permanentes (no expiran).
- **Scopes**: `gmail.modify` + `calendar.readonly` (+ `documents`, `presentations`, `drive.file`).
- Google deprecó el flujo OOB → se usa **localhost redirect**: `redirect_uri = http://localhost`;
  el navegador redirige a `http://localhost/?code=XXXX` (no carga, se copia el `code=`).
- Añadir/renovar una cuenta: `uv run python scripts/get_refresh_token.py` → pega el refresh token en
  `.env` con el nombre que referencia el YAML.
- La **Google Calendar API** debe estar habilitada en el proyecto GCP para los recordatorios.

---

## Archivo local y limpieza de adjuntos

El exportador one-off `scripts/download_attachments.py` sirve para archivar una cuenta Gmail antes de
limpiarla y liberar espacio. Es una operación **solo de lectura** sobre Gmail: descarga cada mensaje
seleccionado como `.eml`, extrae sus adjuntos, PDF e imágenes inline, calcula hashes SHA-256 y genera
índices CSV. No mueve ni borra mensajes.

Los ficheros quedan físicamente en una carpeta plana para revisión manual:

```text
attachments_dump/
  <mailbox>/
    attachments/       # todos los ficheros extraídos, visibles directamente
    messages/          # respaldo .eml completo por mensaje
  messages.csv         # una fila por mensaje
  index.csv            # una fila por fichero; ruta_local apunta al archivo real
  .state.sqlite3       # estado reanudable, no editar a mano
```

El directorio contiene correo personal y está excluido de Git mediante `.gitignore`. El CSV es solo
un índice: los binarios deben abrirse desde `<mailbox>/attachments/`.

### Piloto y escalado seguro

```bash
# Piloto inicial: 10 mensajes de una sola cuenta, sin escrituras en Gmail
uv run python scripts/download_attachments.py \
  --output-dir attachments_dump \
  --mailbox jesus82c \
  --query 'has:attachment' \
  --max-messages 10 \
  --workers 1

# Fase de alto ahorro ya ejecutada: mensajes mayores de 1 MB
uv run python scripts/download_attachments.py \
  --output-dir attachments_dump \
  --mailbox jesus82c \
  --query 'has:attachment larger:1M' \
  --workers 1

# Ampliación posterior, solo tras revisar esta fase: mensajes mayores de 700 KB
uv run python scripts/download_attachments.py \
  --output-dir attachments_dump \
  --mailbox jesus82c \
  --query 'has:attachment larger:700K' \
  --workers 1

# Repetir después con la segunda cuenta
uv run python scripts/download_attachments.py \
  --output-dir attachments_dump \
  --mailbox miguelgutierrezbarquin \
  --query 'has:attachment larger:1M' \
  --workers 1
```

El piloto actual está archivado en `attachments_dump/jesus82c/`. `--max-messages` cuenta solo
mensajes nuevos; relanzar el comando no redescarga los que ya tienen estado `completed`. Para una
iteración futura que también busque emails con imágenes inline no indexadas por Gmail, usar
`--all-messages` tras validar cuotas, espacio local y cobertura de la primera iteración.

La segunda muestra dejó 60 mensajes completados y 65 ficheros (54 adjuntos, 3 PDF y 8 imágenes
inline), con todos los hashes verificados. El descubrimiento de `has:attachment` de esta cuenta
devuelve 18.485 mensajes; un barrido completo requiere aproximadamente 369.885 unidades mínimas de
API (`messages.list` + `messages.get`), por lo que se mantiene el escalado por fases.

La fase de alto ahorro (`has:attachment larger:1M`) ya está completada: 1.252 mensajes nuevos, que
dejan 1.312 mensajes archivados en total, 3.440 ficheros extraídos y 0 hashes inválidos. El archivo
local ocupa aproximadamente 4 GB (`.eml` + ficheros extraídos); no se ha modificado Gmail.

El exportador aplica un máximo de 3 solicitudes por segundo, reintenta errores transitorios de cuota
(429/403 de rate limit/5xx) y respeta `Retry-After`. Antes de empezar exige 100 MiB libres (se puede
ajustar con `--min-free-bytes`). La fase actual sigue siendo secuencial (`--workers 1`) para que el
estado sea fácil de auditar.

`scripts/migrate_archive_layout.py` solo se necesita para convertir un archivo antiguo a la carpeta
plana `attachments/`; no llama a Gmail.

### Revisar y marcar mensajes

Revisa los binarios directamente en `<mailbox>/attachments/` y escribe `x` en la columna `borrar` de
`messages.csv`. El comando siguiente es siempre dry-run: valida el EML, todos los hashes y el estado
SQLite, pero no crea ningún cliente Gmail ni hace escrituras.

```bash
uv run python scripts/trash_marked.py --messages attachments_dump/messages.csv
```

Para una tanda aprobada, añade `--execute` desde una terminal interactiva y escribe exactamente
`TRASH N` (donde `N` es el número de filas marcadas). Solo usa `messages.trash`, nunca borrado
permanente, y deja la auditoría en `attachments_dump/trash_results.csv`:

```bash
uv run python scripts/trash_marked.py \
  --messages attachments_dump/messages.csv \
  --execute
```

---

## Comandos

```bash
uv sync                                       # instalar dependencias
uv run python -m gmail_inbox_bot              # bot de polling (loop)
uv run python -m gmail_inbox_bot --once       # un solo ciclo de poll
uv run python -m gmail_inbox_bot --dry-run    # sin ejecutar acciones
uv run python -m gmail_inbox_bot --server     # FastAPI + bot + scheduler en background
uv run python -m gmail_inbox_bot.calendar_reminders --once --dry-run  # recordatorios (prueba)

uv run pytest                                 # tests
uv run ruff check . && uv run ruff format .   # lint + formato
```

**Pre-push checklist** (lo mismo que valida la GitHub Action):

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

---

## Despliegue

- **VPS**: `158.69.215.223` (usuario `ubuntu`), ruta `/home/ubuntu/services/gmail-inbox-bot`.
- **Autodeploy**: push a `main` → GitHub Action (`deploy-vps.yml`) → `git pull` +
  `docker compose up -d --build`.
- **Docker**: imagen `python:3.13-slim`, entrypoint `python -m gmail_inbox_bot --server`. Puerto
  **8007 → 8000**. Volúmenes: `./logs` y `./config` (el estado de recordatorios persiste en `logs/`).
- **Endpoints prod**: `https://email.pymechat.com/health`, `/admin/dashboard`, `/admin/logs`.

---

## Estructura del proyecto

```text
gmail_inbox_bot/
  __main__.py          # entrypoint CLI (--once/--dry-run/--server/--port)
  app.py               # FastAPI + daemon threads (polling + reminders)
  bot.py               # loop de polling y pipeline _process_email
  config.py            # carga de .env y YAMLs de mailbox
  gmail_client.py      # cliente Gmail API (leer, labels, responder, enviar)
  calendar_client.py   # cliente Google Calendar API (eventos del día)
  calendar_reminders.py# filtrado, render, estado/idempotencia, scheduler, CLI
  classifier.py        # clasificación y respuesta dinámica (OpenAI/Groq)
  actions.py           # router de acciones (tag/move/reply/forward/…)
  mail_processing.py   # pre-filtros, detección de reenvíos, strip_html
  notifications.py     # avisos de email importante (Telegram)
  ib_trades.py         # parser de trades de Interactive Brokers
  metrics.py           # métricas a Supabase (fire-and-forget)
  llm_costs.py         # cálculo de coste por tokens
  telegram.py          # envío de mensajes a Telegram
  admin_dashboard.py   # UI de métricas (/admin/dashboard)
  admin_logs.py        # visor de logs (/admin/logs)
  attachment_archive.py# parseo MIME y escritura segura de adjuntos
  attachment_manifest.py # SQLite + índices CSV del archivo local
  prompts/             # prompt del clasificador
config/                # un YAML por cuenta
templates/             # signature.html, calendar_reminder.html
scripts/               # OAuth, exportador de adjuntos y trash_marked.py (dry-run seguro)
tests/                 # pytest
docs/                  # documentación y specs
```

---

## Estado

En **producción** (VPS, autodeploy desde `main`): cliente Gmail funcional (lectura, clasificación,
respuestas/reenvíos/labels/borradores), recordatorios diarios de Google Calendar, panel de admin y
métricas en Supabase.
