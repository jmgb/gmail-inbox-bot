# CLAUDE.md

## Proyecto

Gmail Inbox Bot es un bot de automatización de emails centrado en **Gmail API** para un escenario de
uso personal o single-user.

## Decisión técnica base

- Entrada: **Cloudflare Email Routing -> Gmail inbox**
- Automatización: **Gmail API**
- Clasificación: **OpenAI**
- Fuente de verdad: **Gmail**, no una base de datos externa

## Cuentas Gmail conectadas

OAuth2 configurado (proyecto GCP: `ai-setter-443613`, scope: `gmail.modify`, app publicada).
Tokens permanentes (no expiran). Para añadir más cuentas: `uv run python scripts/get_refresh_token.py`.

| Cuenta | Variable refresh token |
|---|---|
| `jesus82c@gmail.com` | `GOOGLE_REFRESH_TOKEN_JESUS82C` |
| `miguelgutierrezbarquin@gmail.com` | `GOOGLE_REFRESH_TOKEN_MIGUELGUTIERREZBARQUIN` |

Cada mailbox YAML en `config/` referencia su token vía `refresh_token_env: GOOGLE_REFRESH_TOKEN_XXXXX`.

## OAuth2 — método de autorización

Google deprecó el flujo OOB (`urn:ietf:wg:oauth:2.0:oob`). Usar **localhost redirect**:
- `redirect_uri`: `http://localhost`
- El navegador redirige a `http://localhost/?code=XXXX` (no carga, copiar `code=` de la URL)

## Reglas de diseño

### Gmail como fuente de verdad

El bot debe leer y escribir sobre el mismo sistema donde viven los emails.

### Scope inicial mínimo

Usar `gmail.modify` salvo que exista una razón concreta para ampliar scopes.

### Labels, no carpetas

Gmail usa labels. No modelar Outlook folders como si fueran equivalentes exactos.

### Single-user primero

No introducir complejidad multi-tenant o colas externas salvo requisito claro.

### Idempotencia y prevención de bucles

El bot solo procesa emails que cumplan el query `is:unread in:inbox`. La idempotencia se garantiza
porque **toda acción quita el email del INBOX** (vía `move_email` que remueve el label `INBOX`).

Flujo para categorías con `is_read: false` (ej. `personal`, `finanzas`, `otros` → REVISAR):

1. Email llega → labels: `INBOX`, `UNREAD`
2. `move_email` → añade label destino (ej. `REVISAR`), quita `INBOX`, marca leído
3. Override `is_read: false` → vuelve a poner `UNREAD`
4. Estado final → labels: `REVISAR`, `UNREAD` (sin `INBOX`)
5. Siguiente poll (`is:unread in:inbox`) → **no lo encuentra** → sin bucle

El email queda sin leer bajo REVISAR para que el usuario lo note, pero es invisible para el bot.

Para categorías sin `is_read: false` (ej. `compras`, `notificaciones`): el email queda leído y
fuera del inbox — doblemente protegido.

### Firma / footer de emails

Todos los emails salientes (reply, dynamic_reply, reply_with_attachment, forward) incluyen un footer
HTML de marketing desde `templates/signature.html`. El footer es un **caption promocional de aiship.co**
que busca generar efecto llamada: el destinatario debe percibir que un asistente personal IA redactó
el email con precisión. **No desalentar respuestas** — el footer es branding, no disclaimer.

- Idioma del footer: **siempre en inglés**.
- Se puede personalizar por mailbox con `signature_file: ruta/al/archivo.html` en el YAML.
- Se puede desactivar con `signature_file: ""`.
- Default: `templates/signature.html`.

## Comandos

- `uv sync`
- `uv run python -m gmail_inbox_bot`
- `uv run pytest`
- `uv run ruff check .`
- `uv run ruff format .`

## Documentación clave

- `docs/EMAIL_INTEGRATION_OPTIONS.md`
- `docs/MIGRATION_PLAN.md`
- `docs/PACTO_BUSINESS_RULES_REFERENCE.md`
- `ROADMAP.md`
- Skill `/gmail` — referencia completa de la Gmail API y setup OAuth2
