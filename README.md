# Gmail Inbox Bot

Bot de automatización de emails orientado a **Gmail API** y **Cloudflare Email Routing**.

Este repositorio nace como separación del proyecto `pacto-mundial-bot` para implementar una versión
Gmail-native sin acoplamiento a Microsoft Graph / Outlook.

## Arquitectura elegida

```text
Cloudflare Email Routing -> Gmail inbox
                              |
                              v
                     Python bot -> Gmail API
                     - leer no leídos
                     - clasificar con OpenAI
                     - responder / reenviar / crear borrador
                     - aplicar labels / archivar / mantener unread

Scheduler diario -> Google Calendar API
                     - recordatorios de reuniones del día (ver abajo)
```

## Principios actuales

- Gmail es la **fuente de verdad**
- Uso **personal / single-user**
- OAuth2 con app **External + In production**
- Scopes: **`gmail.modify`** + **`calendar.readonly`** (este último para los recordatorios de Calendar)
- No usar Supabase ni Email Workers como cola principal de entrada

## Documentación importada

- `docs/EMAIL_INTEGRATION_OPTIONS.md`: decisión técnica actualizada y opción ganadora
- `docs/PACTO_BUSINESS_RULES_REFERENCE.md`: reglas de negocio del bot original de Pacto Mundial
- `docs/PACTO_SOURCE_CONTEXT.md`: contexto del repositorio origen
- `docs/MIGRATION_PLAN.md`: qué portar y qué adaptar del bot actual
- `ROADMAP.md`: siguientes pasos concretos para el desarrollo

## Recordatorios de Google Calendar

Cada mañana, un **scheduler interno** (segundo daemon thread junto al de polling) revisa el Google
Calendar de cada cuenta y envía a los asistentes un email de recordatorio de las reuniones del día.

- **A quién**: solo reuniones con **1 o 2 invitados** además del titular (1:1 y tríos). Se excluyen
  eventos all-day, cancelados, los que el titular rechazó, los invitados que rechazaron y los
  eventos sin invitados humanos.
- **Cuándo**: a la hora `send_time` de cada mailbox (por defecto **09:16 Europe/Madrid** — una hora
  "rota" a propósito, para que el mensaje parezca escrito a mano, no una automatización).
- **Qué envía**: un email en **prosa natural**, firmado con `sender_name`, **sin** footer de
  marketing. El saludo usa el nombre real del invitado y nunca muestra su email.
- **Idempotencia**: fichero de estado JSON en `logs/calendar_reminders_state.json` (volumen docker).
  Dedupe global entre cuentas: un invitado recibe un único recordatorio por reunión.
- **Credenciales**: reutiliza el OAuth de Gmail (mismo refresh token, ampliado con `calendar.readonly`).

Configuración opt-in por mailbox en `config/<cuenta>.yml`:

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
uv run python -m gmail_inbox_bot.calendar_reminders --once --dry-run   # lista sin enviar
uv run python -m gmail_inbox_bot.calendar_reminders --once             # envía una vez ahora
```

## Arranque local

```bash
uv sync
uv run python -m gmail_inbox_bot              # bot de polling
uv run python -m gmail_inbox_bot --server     # FastAPI (admin UI) + bot + scheduler en background
```

## Estado

En producción (VPS, autodeploy desde `main`). Cliente Gmail funcional (lectura, clasificación con
OpenAI, respuestas/reenvíos/labels) y recordatorios diarios de Google Calendar.
