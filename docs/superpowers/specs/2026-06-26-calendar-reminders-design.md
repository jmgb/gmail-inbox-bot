# Recordatorios de Google Calendar a las 9:00 — Diseño

**Fecha**: 2026-06-26
**Estado**: Aprobado, pendiente de plan de implementación

## Objetivo

Cada día a las **09:00 Europe/Madrid**, revisar Google Calendar de las cuentas configuradas,
identificar las reuniones de **hoy** con **1 o 2 invitados además del titular**, y enviar a cada
invitado (que no haya rechazado) un email de recordatorio con plantilla fija. Se reutiliza el OAuth
existente (ampliando el scope a Calendar) y el envío de email vía Gmail API.

## Decisiones tomadas (brainstorming)

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

## Restricción previa: re-autorización OAuth (scope Calendar)

El refresh token actual NO incluye Calendar. Antes de que esto funcione en producción:

1. Añadir `https://www.googleapis.com/auth/calendar.readonly` al `SCOPE` de
   `scripts/get_refresh_token.py`.
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

## CalendarClient

Reutiliza `client_id` / `client_secret` / `refresh_token` (los mismos que `GmailClient`). Implementa
su propia lógica de refresh de access token contra `https://oauth2.googleapis.com/token` (idéntica a
la de `GmailClient._refresh_access_token`), para mantener el cliente autocontenido.

```
BASE_URL = "https://www.googleapis.com/calendar/v3/calendars/primary"
```

Método principal:

```python
def list_events_for_day(self, day: date, tz: str) -> list[dict]:
    # timeMin / timeMax = límites del día en la zona tz, en RFC3339
    # singleEvents=true (expande recurrentes), orderBy=startTime
    # GET /events?timeMin=...&timeMax=...&singleEvents=true&orderBy=startTime
```

Devuelve eventos **normalizados** a un dict interno:

```python
{
    "id": str,
    "summary": str,
    "start": datetime | None,      # None si all-day
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

## Lógica de filtrado (funciones puras)

Un evento de hoy **califica** si todas se cumplen:

1. No es all-day (`all_day is False`).
2. Mi `my_response` ≠ `"declined"`.
3. El nº de asistentes **humanos distintos de mí** (excluyendo recursos/salas) está entre 1 y 2
   (`1 <= n <= max_attendees`). El conteo de tamaño se hace sobre todos los invitados humanos,
   independientemente de su respuesta.
4. Hay al menos 1 invitado humano externo (si solo estoy yo → no califica).

**Destinatarios** del recordatorio = asistentes humanos con `is_self is False` y
`response != "declined"`. Nunca se auto-envía al titular.

Funciones objetivo de tests:
- `event_qualifies(event, max_attendees) -> bool`
- `reminder_recipients(event) -> list[dict]`
- helpers de normalización de asistentes / detección de recurso vs humano.

## Plantilla del email

`templates/calendar_reminder.html` (Jinja2, ya es dependencia). Variables:

- `invitee_name` — nombre del invitado (fallback al email si no hay nombre).
- `meeting_title` — `summary` del evento.
- `meeting_date` — fecha legible (Europe/Madrid).
- `meeting_time` — hora de inicio (Europe/Madrid).
- `location` — ubicación física o link de Meet (lo que haya).
- `organizer_name` — nombre/email del organizador (el titular).

Se le concatena el footer de `templates/signature.html` (branding de aiship.co, **en inglés**), igual
que los emails salientes actuales. El cuerpo del recordatorio puede ir en el idioma del titular
(español), pero el footer permanece en inglés.

Asunto sugerido: `Recordatorio: {meeting_title} hoy a las {meeting_time}`.

## Scheduling e idempotencia

### Scheduler interno

- Daemon thread arrancado en `app.py` startup (junto al de polling).
- Despierta cada ~60 s. Calcula la hora actual en `Europe/Madrid` (vía `zoneinfo`, stdlib en 3.13).
- Si la hora local ≥ `send_time` (09:00) **y** no se ha ejecutado hoy (según estado) → lanza
  `run_once(dry_run=False)` para cada mailbox con `calendar_reminders.enabled`.
- Tras ejecutar, marca el día como hecho en el estado.

### Fichero de estado

`logs/calendar_reminders_state.json` (la carpeta `logs/` ya está montada como volumen → persiste
entre reinicios del contenedor).

Estructura:

```json
{
  "last_run_date": { "jesus82c": "2026-06-26", "miguelgutierrezbarquin": "2026-06-26" },
  "sent": {
    "2026-06-26": ["<event_id>:<invitee_email>", "..."]
  }
}
```

- `last_run_date` por mailbox → no relanza el job entero el mismo día.
- `sent` por fecha → no reenvía a un invitado concreto aunque el contenedor reinicie a las 09:30.
- Se puede purgar entradas de fechas pasadas al escribir (mantener solo hoy y, opcionalmente, ayer).

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

## Modo prueba (CLI)

Entrypoint: `uv run python -m gmail_inbox_bot.calendar_reminders [--once] [--dry-run]`

- `--dry-run`: loguea a quién se enviaría (evento, invitado, asunto) **sin enviar** ni tocar el
  fichero de estado.
- `--once`: ejecuta el job inmediatamente **ignorando la hora** (validación en VPS).
- Sin flags: arranca el loop del scheduler de forma standalone (útil si se quiere correr como
  proceso/cron separado en el futuro, aunque el modo por defecto es el thread dentro del servidor).

## Tests (TDD)

Estilo `tests/test_gmail_client.py`, con respuestas de la Calendar API mockeadas:

- `event_qualifies`: tamaño 1 y 2 invitados (pasa), 0 y 3 (no pasa), all-day (no pasa), rechazado por
  mí (no pasa), solo yo (no pasa).
- `reminder_recipients`: excluye al titular, excluye invitados `declined`, incluye `accepted` /
  `tentative` / `needsAction`.
- Normalización de asistentes: distingue recurso/sala de humano, extrae nombre/email/responseStatus.
- Normalización de eventos: all-day vs con hora, extracción de Meet link (hangoutLink /
  conferenceData) y location.
- Render de plantilla: variables presentes, footer en inglés añadido.
- Estado/dedupe: no reenvía una clave ya registrada; purga fechas pasadas.
- Exclusión de auto-envío al titular.

## Docker / Deploy

- `docker-compose.production.yml` ya monta `./logs` y `./config` → no hace falta volumen nuevo (el
  estado vive en `logs/`).
- El scheduler corre dentro del proceso del servidor que ya está 24/7.
- Deploy estándar: push a `main` → GitHub Action autodeploy.
- **Paso manual extra (una vez)**: re-autorizar las dos cuentas con el scope `calendar.readonly` y
  actualizar `.env` en local y en el VPS.

## Fuera de alcance (YAGNI)

- Recordatorios para reuniones de >2 invitados o sin invitados.
- Contenido generado por IA.
- Configuración de horario distinto por evento o múltiples disparos al día.
- Soporte multi-calendario por cuenta (solo `primary`).
- Reintentos/colas externas para el envío (best-effort con logging, igual que el resto del bot).
