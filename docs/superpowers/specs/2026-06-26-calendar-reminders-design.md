# Recordatorios de Google Calendar a las 9:00 — Diseño

**Fecha**: 2026-06-26
**Estado**: Aprobado, listo para plan de implementación

## Objetivo

Cada día a las **09:00 Europe/Madrid**, revisar Google Calendar de las cuentas configuradas,
identificar las reuniones de **hoy** con **1 o 2 invitados además del titular**, y enviar a cada
invitado (que no haya rechazado) un email de recordatorio con plantilla fija. Se reutiliza el OAuth
existente (ampliando el scope a Calendar) y el envío de email vía Gmail API.

## Decisiones cerradas

| Decisión | Elección |
|---|---|
| Conteo de asistentes | 1-2 invitados **además del titular** (eventos con 1 o 2 personas distintas de mí) |
| Cuentas | **Ambas**: `jesus82c@gmail.com` y `miguelgutierrezbarquin@gmail.com` |
| Contenido del email | **Plantilla fija** (Jinja2), sin coste LLM |
| Scheduling | **Scheduler interno** en el proceso del bot (segundo daemon thread en `app.py`) |
| Ventana de "hoy" | A las 09:00 Madrid, todas las reuniones de hoy (00:00–23:59 Madrid), incluidas las de antes de las 9 |
| Exclusiones | Eventos rechazados por mí, eventos all-day, asistentes que rechazaron, eventos sin invitados externos |
| Idempotencia | **Fichero de estado local** JSON en `logs/` (volumen docker persistente) |
| Modo prueba | `--dry-run` y `--once` |

## Principios de implementación

- Mantener el enfoque **single-user primero** del proyecto: sin base de datos, colas externas ni
  infraestructura nueva.
- Gmail/Google Calendar son la fuente de verdad. El fichero JSON solo guarda idempotencia operativa,
  no datos de negocio.
- El feature es opt-in por mailbox. Sin bloque `calendar_reminders`, no hace nada.
- Los fallos de una mailbox no deben impedir procesar la otra. Se loguea el error con `log.exception`
  y el scheduler continúa.
- No se envían recordatorios sin destinatarios humanos válidos, ni al propio titular.

## Restricción previa: re-autorización OAuth (scope Calendar)

El refresh token actual NO incluye Calendar. Antes de que esto funcione en producción:

1. Añadir `https://www.googleapis.com/auth/calendar.readonly` al `SCOPE` de
   `scripts/get_refresh_token.py`, manteniendo los scopes ya existentes.
2. **Re-autorizar las dos cuentas** (volver a correr el script) y actualizar sus refresh tokens en
   `.env` (`GOOGLE_REFRESH_TOKEN_JESUS82C`, `GOOGLE_REFRESH_TOKEN_MIGUELGUTIERREZBARQUIN`) tanto en
   local como en el VPS.

Mismo `client_id`/`client_secret`, mismo flujo localhost-redirect. El access token derivado del
nuevo refresh token cubre Gmail **y** Calendar simultáneamente, así que el resto del bot sigue
funcionando sin cambios.

## Arquitectura

### Componentes nuevos

| Archivo | Responsabilidad |
|---|---|
| `gmail_inbox_bot/calendar_client.py` | `CalendarClient`: refresh de access token (mismo patrón que `GmailClient`), método `list_events_for_day(date, tz)` → eventos normalizados. |
| `gmail_inbox_bot/calendar_reminders.py` | Orquestación: filtrado, render de plantilla, dedupe con fichero de estado, envío. Expone `run_once(dry_run)`, el loop del scheduler, y un entrypoint CLI. |
| `templates/calendar_reminder.html` | Plantilla Jinja2 del recordatorio + footer de firma (inglés). |

### Cambios mínimos en código existente

- **`gmail_inbox_bot/gmail_client.py`**: añadir
  `send_email(user_email, to_address, subject, html_body, *, force_draft=False)` — email nuevo
  (MIMEText, sin headers de threading), reutilizando `_send_or_draft`. Hoy solo hay reply/forward.
- **`gmail_inbox_bot/app.py`**: arrancar un segundo daemon thread `_run_reminder_scheduler` en el
  evento `startup`, con el mismo patrón que `_run_bot_in_thread`.
- **`scripts/get_refresh_token.py`**: añadir el scope `calendar.readonly`.
- **`config/jesus82c.yml`** y **`config/miguelgutierrezbarquin.yml`**: bloque `calendar_reminders`.

### Contrato interno de mailbox

La orquestación recibe cada config YAML tal como la carga `load_mailbox_configs()`. Para cada mailbox
activa usa:

- `name`: clave estable para logs y estado local.
- `email`: cuenta titular del calendario y `From` por defecto.
- `send_as`: alias opcional ya soportado por `GmailClient`.
- `refresh_token_env`: variable de entorno del refresh token.
- `calendar_reminders`: configuración opt-in del feature.

Si falta `email`, `refresh_token_env` o el refresh token de entorno, esa mailbox se salta con error
logueado; no se debe romper el proceso completo.

## CalendarClient

Reutiliza `client_id` / `client_secret` / `refresh_token` (los mismos que `GmailClient`) y recibe
también `user_email` para detectar al titular aunque Google no marque un attendee con `self: true`.
Implementa su propia lógica de refresh de access token contra `https://oauth2.googleapis.com/token`
y una capa `_request()` equivalente a `GmailClient._request()`:

- refresca token y reintenta una vez ante `401`;
- reintenta `5xx` con backoff corto;
- usa timeout HTTP explícito;
- loguea errores con el logger del proyecto.

```
BASE_URL = "https://www.googleapis.com/calendar/v3/calendars/primary"
```

Método principal:

```python
def list_events_for_day(self, day: date, tz: str) -> list[dict]:
    # timeMin = inicio del día local; timeMax = inicio del día siguiente local
    # singleEvents=true (expande recurrentes), orderBy=startTime
    # GET /events?timeMin=...&timeMax=...&singleEvents=true&orderBy=startTime&timeZone=tz
```

Devuelve eventos **normalizados** a un dict interno:

```python
{
    "id": str,
    "ical_uid": str,
    "recurring_event_id": str,
    "original_start": datetime | None,
    "status": str,
    "summary": str,
    "start": datetime | None,      # None si all-day
    "end": datetime | None,
    "all_day": bool,
    "location": str,               # location del evento
    "meet_link": str,              # hangoutLink / conferenceData / "" 
    "organizer": {"name": str, "email": str},
    "my_response": str,            # responseStatus del asistente self (o "" )
    "attendees": [
        {"name": str, "email": str, "response": str, "is_self": bool, "is_resource": bool},
        ...
    ],
}
```

Notas de normalización:

- `timeMin` y `timeMax` se calculan con `zoneinfo.ZoneInfo(tz)` y se serializan en RFC3339 con offset.
  `timeMax` es exclusivo: inicio del día siguiente.
- Eventos con `start.date` son all-day y se normalizan con `start=None`, `end=None`, `all_day=True`.
- Eventos con hora se convierten a datetimes aware. Para mostrar y dedupe se usa la hora convertida a
  `timezone`.
- `my_response` se toma del attendee con `self: true`. Si no existe y el titular es organizador, se
  trata como `"accepted"` para no descartar eventos propios donde Google no lista al owner como
  attendee.
- `is_self` se calcula por `attendee.self is true` o por email normalizado igual a `user_email`.
- Si la API devuelve `attendeesOmitted: true`, el evento no califica: no tenemos una lista completa
  para contar ni elegir destinatarios con seguridad.
- Recursos/salas se excluyen cuando `attendee.resource is true`. No intentar inferir salas por nombre
  o dominio.
- `meet_link` se obtiene por prioridad: `hangoutLink`, luego `conferenceData.entryPoints` con
  `entryPointType == "video"`, luego `""`.

## Lógica de filtrado (funciones puras)

Un evento de hoy **califica** si todas se cumplen:

1. No es all-day (`all_day is False`).
2. No está cancelado (`status != "cancelled"`).
3. Mi `my_response` ≠ `"declined"`.
4. El nº de asistentes **humanos distintos de mí** (excluyendo recursos/salas) está entre 1 y 2
   (`1 <= n <= max_attendees`). El conteo de tamaño se hace sobre todos los invitados humanos,
   independientemente de su respuesta.
5. Hay al menos 1 invitado humano externo (si solo estoy yo → no califica).

**Destinatarios** del recordatorio = asistentes humanos con `is_self is False`, email distinto del
titular normalizado y `response != "declined"`. Nunca se auto-envía al titular.

Funciones objetivo de tests:
- `event_qualifies(event, max_attendees) -> bool`
- `reminder_recipients(event) -> list[dict]`
- helpers de normalización de asistentes / detección de recurso vs humano.

### Dedupe entre mailboxes

Si la misma reunión aparece en las dos cuentas configuradas, se evita enviar dos recordatorios al
mismo invitado. La clave global de envío debe incluir:

```text
<day>:<ical_uid_or_event_id>:<start_iso_or_original_start>:<invitee_email_lower>
```

`ical_uid` es preferente porque suele ser estable entre calendarios de distintos asistentes. Si no
existe, usar `id`. Para logs y diagnóstico se guarda también el `mailbox_name` que produjo el envío,
pero el dedupe de destinatario no debe depender de la mailbox.

La clave se calcula después de normalizar email a lowercase y start a ISO en `timezone`.

## Plantilla del email

`templates/calendar_reminder.html` (Jinja2, ya es dependencia, con `autoescape=True`). Variables:

- `invitee_name` — nombre real del invitado (Calendar `displayName`). Si no hay nombre o parece un
  email, se omite y el saludo cae a `¡Hola!`. **Nunca** se muestra el email en el saludo.
- `meeting_title` — `summary` del evento.
- `meeting_time` — hora de inicio (Europe/Madrid).
- `meet_link` / `location` — si hay Meet, una línea con el enlace; si no, la ubicación física; si no
  hay ninguno, se omite la línea (sin placeholders vacíos).
- `sender_name` — nombre con el que se firma, desde `calendar_reminders.sender_name` (fallback a
  `send_as` y luego al email). Configurado como `Miguel`.

Asunto: `Recordatorio: {meeting_title} hoy a las {meeting_time}`.

### Diseño de credibilidad (parece escrito a mano)

**Objetivo**: el destinatario debe percibir un mensaje **personal, escrito por Miguel esa misma
mañana**, no una automatización. Decisiones deliberadas:

1. **Sin footer de marketing.** A diferencia del resto de emails del bot, los recordatorios **no**
   llevan el footer de `aiship.co` (que delataría "AI assistant"). El cuerpo es prosa natural con
   despedida personal ("Un saludo, Miguel").
2. **Hora "rota" de envío: 09:16, no 09:00.** Una hora en punto parece un cron disparando; una hora
   impar parece que alguien se sentó a escribir en un momento cualquiera de la mañana. Configurable
   por mailbox vía `send_time`.
3. **Saludo con nombre real o genérico**, nunca el email crudo (que gritaría "mailmerge").

Esta es una excepción consciente a la regla global de footer de marketing en salientes (documentada
en `CLAUDE.md`), aprobada por el usuario para esta feature concreta.

## Scheduling e idempotencia

### Scheduler interno

- Daemon thread arrancado en `app.py` startup (junto al de polling).
- Despierta cada ~60 s. Calcula la hora actual en `Europe/Madrid` (vía `zoneinfo`, stdlib en 3.13).
- Si la hora local ≥ `send_time` (09:00) **y** no se ha ejecutado hoy (según estado) → lanza
  `run_once(dry_run=False)` para cada mailbox con `calendar_reminders.enabled`.
- Tras ejecutar, marca el día como hecho en el estado.
- Respetar `DISABLE_BOT`: si está activo, no arrancar ni polling ni scheduler de recordatorios.
- `DRY_RUN=1` en entorno debe forzar el scheduler a no enviar ni escribir estado, igual que el bot
  actual opera en modo seco.

### Fichero de estado

`logs/calendar_reminders_state.json` (la carpeta `logs/` ya está montada como volumen → persiste
entre reinicios del contenedor).

Estructura:

```json
{
  "last_run_date": { "jesus82c": "2026-06-26", "miguelgutierrezbarquin": "2026-06-26" },
  "sent": {
    "2026-06-26": [
      {
        "key": "<dedupe_key>",
        "mailbox": "jesus82c",
        "event_id": "<calendar_event_id>",
        "invitee": "person@example.com",
        "sent_at": "2026-06-26T09:00:05+02:00"
      }
    ]
  }
}
```

- `last_run_date` por mailbox → no relanza el job entero el mismo día.
- `sent` por fecha → no reenvía a un invitado concreto aunque el contenedor reinicie a las 09:30.
- Se puede purgar entradas de fechas pasadas al escribir (mantener solo hoy y, opcionalmente, ayer).
- Escritura atómica: escribir a `calendar_reminders_state.json.tmp` y reemplazar con
  `Path.replace()` para no corromper el JSON si el proceso cae durante la escritura.
- Si el estado no existe, se crea. Si existe pero está corrupto, loguear error y arrancar con estado
  vacío solo en `--dry-run`; en modo real, fallar el job de recordatorios para evitar duplicados.

### Semántica de ejecución parcial

- Una mailbox se marca como ejecutada solo después de terminar su procesamiento.
- Un envío se añade a `sent` justo después de que `GmailClient.send_email()` termine sin excepción.
- Si falla un invitado, se loguea y se continúa con el siguiente. Los envíos ya completados quedan
  persistidos.
- Si falla la lectura de Calendar de una mailbox, esa mailbox no se marca como ejecutada.

## Configuración por mailbox (opt-in)

```yaml
calendar_reminders:
  enabled: true
  send_time: "09:00"
  timezone: Europe/Madrid
  max_attendees: 2          # invitados además del titular
```

Sin el bloque, o con `enabled: false`, la cuenta no genera recordatorios. El `From` del email es el
email del mailbox (o su `send_as` si está configurado).

El fichero de estado es único y global (`logs/calendar_reminders_state.json`) para que el dedupe
entre mailboxes funcione.

## Modo prueba (CLI)

Entrypoint: `uv run python -m gmail_inbox_bot.calendar_reminders [--once] [--dry-run]`

- `--dry-run`: loguea a quién se enviaría (evento, invitado, asunto) **sin enviar** ni tocar el
  fichero de estado.
- `--once`: ejecuta el job inmediatamente **ignorando la hora**, pero respetando idempotencia salvo
  que se combine con `--dry-run`.
- Sin flags: arranca el loop del scheduler de forma standalone (útil si se quiere correr como
  proceso/cron separado en el futuro, aunque el modo por defecto es el thread dentro del servidor).

Comando recomendado para validar en VPS sin efectos:

```bash
uv run python -m gmail_inbox_bot.calendar_reminders --once --dry-run
```

## Tests (TDD)

Estilo `tests/test_gmail_client.py`, con respuestas de la Calendar API mockeadas:

- `event_qualifies`: tamaño 1 y 2 invitados (pasa), 0 y 3 (no pasa), all-day (no pasa), rechazado por
  mí (no pasa), cancelado (no pasa), solo yo (no pasa).
- `reminder_recipients`: excluye al titular, excluye invitados `declined`, incluye `accepted` /
  `tentative` / `needsAction`.
- Normalización de asistentes: distingue recurso/sala de humano, extrae nombre/email/responseStatus.
- Normalización de eventos: all-day vs con hora, extracción de Meet link (hangoutLink /
  conferenceData) y location.
- Normalización cuando el titular es organizador y no aparece como attendee `self`.
- Evento con `attendeesOmitted: true` no califica.
- Render de plantilla: variables presentes, footer en inglés añadido.
- Estado/dedupe: no reenvía una clave ya registrada; purga fechas pasadas.
- Dedupe global: misma `ical_uid`/hora/invitado desde dos mailboxes genera un solo envío.
- Escritura atómica del estado y recuperación de estado inexistente.
- Exclusión de auto-envío al titular.
- CLI `--dry-run --once`: no envía y no modifica estado.

Tests de integración ligera:

- `CalendarClient.list_events_for_day()` construye `timeMin`, `timeMax`, `singleEvents`, `orderBy`
  y `timeZone` correctamente con `httpx` mockeado.
- `GmailClient.send_email()` crea un MIME sin `In-Reply-To`, sin `References` y usa `_send_or_draft`
  con `thread_id=""`.

## Docker / Deploy

- `docker-compose.production.yml` ya monta `./logs` y `./config` → no hace falta volumen nuevo (el
  estado vive en `logs/`).
- El scheduler corre dentro del proceso del servidor que ya está 24/7.
- Deploy estándar: push a `main` → GitHub Action autodeploy.
- **Paso manual extra (una vez)**: re-autorizar las dos cuentas con el scope `calendar.readonly` y
  actualizar `.env` en local y en el VPS.

### Checklist de implementación

1. Añadir tests rojos de funciones puras, estado y `GmailClient.send_email()`.
2. Implementar `send_email()` en `GmailClient`.
3. Implementar `CalendarClient` con normalización y tests HTTP mockeados.
4. Implementar estado JSON atómico y dedupe global.
5. Implementar orquestación, plantilla y CLI.
6. Integrar scheduler en `app.py`.
7. Añadir configuración opt-in a las dos mailboxes.
8. Actualizar scope y re-autorizar cuentas manualmente.
9. Ejecutar `uv run ruff check . && uv run ruff format --check . && uv run pytest`.

## Fuera de alcance (YAGNI)

- Recordatorios para reuniones de >2 invitados o sin invitados.
- Contenido generado por IA.
- Configuración de horario distinto por evento o múltiples disparos al día.
- Soporte multi-calendario por cuenta (solo `primary`).
- Reintentos/colas externas para el envío (best-effort con logging, igual que el resto del bot).
