# CLAUDE.md

## Proyecto

Gmail Inbox Bot es un bot de automatización de emails centrado en **Gmail API** para un escenario de
uso personal o single-user.

## Decisión técnica base

- Entrada: **Cloudflare Email Routing -> Gmail inbox**
- Automatización: **Gmail API**
- Clasificación: **OpenAI**
- Fuente de verdad: **Gmail**, no una base de datos externa

## Reglas de diseño

### Gmail como fuente de verdad

El bot debe leer y escribir sobre el mismo sistema donde viven los emails.

### Scope inicial mínimo

Usar `gmail.modify` salvo que exista una razón concreta para ampliar scopes.

### Labels, no carpetas

Gmail usa labels. No modelar Outlook folders como si fueran equivalentes exactos.

### Single-user primero

No introducir complejidad multi-tenant o colas externas salvo requisito claro.

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
