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
```

## Principios actuales

- Gmail es la **fuente de verdad**
- Uso **personal / single-user**
- OAuth2 con app **External + In production**
- Scope inicial mínimo: **`gmail.modify`**
- No usar Supabase ni Email Workers como cola principal de entrada

## Documentación importada

- `docs/EMAIL_INTEGRATION_OPTIONS.md`: decisión técnica actualizada y opción ganadora
- `docs/PACTO_BUSINESS_RULES_REFERENCE.md`: reglas de negocio del bot original de Pacto Mundial
- `docs/PACTO_SOURCE_CONTEXT.md`: contexto del repositorio origen
- `docs/MIGRATION_PLAN.md`: qué portar y qué adaptar del bot actual
- `ROADMAP.md`: siguientes pasos concretos para el desarrollo

## Arranque local

```bash
uv sync
uv run python -m gmail_inbox_bot
```

## Estado

Scaffold inicial preparado. Aún no hay implementación funcional del cliente Gmail.
