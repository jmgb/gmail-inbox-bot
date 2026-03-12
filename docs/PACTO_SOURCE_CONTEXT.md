# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Proyecto

Pacto Mundial Bot es un servicio de automatización de emails para la ONG Pacto Mundial de la ONU España. Clasifica emails entrantes con OpenAI, los enruta a carpetas, responde automáticamente según categoría, y notifica errores por Telegram. FastAPI + polling async + Docker.

**Origen**: Extraído como servicio standalone desde el monolito VirtualAssistant. Antes vivía en `VirtualAssistant/modules/pacto_mundial/`.

## Reglas críticas de negocio

### EMAILS INTOCABLES — NUNCA eliminar emails de los buzones de Pacto Mundial
Pacto Mundial es una ONG oficial de Naciones Unidas. Los emails son documentos institucionales que no pueden eliminarse bajo ninguna circunstancia.

### Solo plantillas en respuestas automáticas
Las únicas respuestas que el sistema puede enviar automáticamente son **plantillas predefinidas** en el YAML (`templates:`). NUNCA enviar texto generado por IA sin revisión humana.

### Categoría "otros" = gestión humana
En todos los buzones, "otros" debe: etiquetar como "PENDIENTE GESTIONAR", mover a carpeta correspondiente, y **no generar ningún email ni borrador**.

### `dynamic_reply` requiere `draft_mode: true`
Si se usa `dynamic_reply`, solo para crear borradores de revisión, nunca generar + enviar en un solo paso.

> Detalle completo en [`docs/BUSINESS_RULES.md`](docs/BUSINESS_RULES.md)

## Buzones monitorizados

| Buzón | Email | Categorías | Descripción |
|-------|-------|------------|-------------|
| Asociacion | `asociacion@pactomundial.org` | 18 | Gestión de asociados/membresía |
| Contabilidad | `contabilidad@pactomundial.org` | 7 | Finanzas y facturación |
| ProveedoresSostenibles | `proveedores_sostenibles@pactomundial.org` | 36 | Programa Proveedores Sostenibles |

## Comandos

- **Tests:** `uv run pytest`
- **Dev local:** `uv run python -m pacto_mundial`
- **Lint:** `uv run ruff check .`
- **Format:** `uv run ruff format .`
- **Docker build:** `docker compose -f docker-compose.production.yml up -d --build`

## VPS Production (Docker)

- **VPS**: `158.69.215.223` (Ubuntu 24.04, compartido con otros servicios)
- **SSH**: `ssh ubuntu@158.69.215.223`
- **Container**: `pacto-mundial` | **Puerto**: 3006 (solo localhost)
- **Path VPS**: `/home/ubuntu/services/pacto-mundial`
- **Dominio**: `pactomundial.pymechat.com`
- **Log Viewer**: `https://pactomundial.pymechat.com/admin/logs` (password en `LOGS_VIEWER_PASSWORD`)
- **Red Docker**: `pymechat-net` (externa, compartida)
- **Limits**: 256MB RAM max, 128MB reservado
- **Logs**: `docker logs pacto-mundial -f` | **Health**: `curl http://localhost:3006/health`
- **Deploy**: automático via GitHub Actions al hacer push a `main`
- **Stats**: `docker stats pacto-mundial`

### Deploy VPS

```bash
# Manual (desde el VPS)
cd /home/ubuntu/services/pacto-mundial && git pull origin main --ff-only && docker compose -f docker-compose.production.yml up -d --build

# Remoto (desde dev)
ssh ubuntu@158.69.215.223 "cd /home/ubuntu/services/pacto-mundial && git pull origin main --ff-only && docker compose -f docker-compose.production.yml up -d --build"
```

### Cuándo recrear el contenedor

`docker restart` solo reinicia el proceso — **NO relee `.env` ni reconstruye la imagen**.

| Cambio | Comando necesario |
|--------|-------------------|
| Código Python / Dockerfile | `docker compose -f docker-compose.production.yml up -d --build` |
| Variables en `.env` | `docker compose -f docker-compose.production.yml up -d` (sin `--build`, recrea el contenedor) |
| Solo reiniciar el proceso | `docker restart pacto-mundial` (no relee `.env`) |

### GitHub Secrets (para CI/CD)

| Secret | Descripción |
|--------|-------------|
| `VPS_HOST` | `158.69.215.223` |
| `VPS_SSH_KEY` | Clave SSH privada ed25519 (de `~/.ssh/id_ed25519` en la máquina dev) |

### Otros servicios en el mismo VPS

| Servicio | Puerto | Container |
|----------|--------|-----------|
| Tuli | 8001 | tuli-backend |
| VirtualAssistant | 3003 | virtual-assistant |
| ReservaGym | 9000 | reservagym-production |
| Presupuestor | — | presupuestor-backend |
| Licenciator | — | licenciator-backend |
| Finanzas | — | finanzas-dev |
| **Pacto Mundial** | **3006** | **pacto-mundial** |

## Arquitectura

```
pacto-mundial-bot/
├── pacto_mundial/           # Paquete principal
│   ├── app.py               # FastAPI entry point (puerto 3006)
│   ├── admin_logs.py        # Log viewer web (/admin/logs)
│   ├── bot.py               # Email polling loop (cada ~120s por buzón)
│   ├── graph_client.py      # Microsoft Graph API client (OAuth2)
│   ├── classifier.py        # OpenAI clasificación de emails
│   ├── actions.py           # Routing: reply, move, tag, forward
│   ├── metrics.py           # Supabase metrics tracking
│   ├── config/              # YAML configs por buzón
│   ├── prompts/             # Prompts de clasificación OpenAI
│   ├── signatures/          # Firmas HTML por buzón
│   ├── files/               # Adjuntos (logos, guías)
│   ├── templates/           # HTML templates (admin_logs.html)
│   └── tests/               # Unit tests
├── shared/                  # Utilidades compartidas (logger, telegram, constants)
├── docs/                    # Documentación del proyecto
│   └── BUSINESS_RULES.md    # ADR y reglas de negocio
├── Dockerfile               # Python 3.12-slim + uv
├── docker-compose.production.yml
├── ROADMAP.md               # Roadmap y features pendientes
└── .github/workflows/deploy-vps.yml
```

## Integraciones

| Servicio | Uso | Variables de entorno |
|----------|-----|---------------------|
| Microsoft Azure / Graph API | Acceso a buzones Office 365 (OAuth2) | `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` |
| OpenAI | Clasificación de emails + respuestas dinámicas | `OPENAI_API_KEY` |
| Supabase | Almacenamiento de métricas | `SUPABASE_URL`, `SUPABASE_SECRET_KEY` |
| Telegram | Notificaciones de errores | `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` |
| Sentry | Error tracking (opcional) | `SENTRY_DSN` |

## Flujo de procesamiento

```
Email llega → poll (cada 2 min) → pre-filtros → clasificación OpenAI → acción
                                       │                │                  │
                                       ├─ bounce/self   ├─ JSON:          ├─ reply (plantilla)
                                       │  → silent      │  {categoria,    ├─ forward
                                       ├─ interno       │   idioma,       ├─ tag + move
                                       │  → tag+move    │   razon}        ├─ silent
                                       └─ spam/OOO      │                 └─ tag PENDIENTE
                                          → silent      └─ error
                                                           → tag ERROR IA
```

## Tags de procesamiento (Outlook)

| Tag | Cuándo se usa |
|-----|---------------|
| `RESPONDIDO IA` | Reply enviado |
| `REENVIADO IA` | Forward enviado |
| `BORRADOR RESPUESTA IA` | Draft reply (draft_mode) |
| `BORRADOR REENVIO IA` | Draft forward (draft_mode) |
| `PENDIENTE ADJUNTO` | Necesita PDF manual |
| `PENDIENTE GESTIONAR` | Para gestión humana |
| `ERROR IA` | Clasificación falló |

## Manejo de errores

| Situación | Comportamiento |
|-----------|---------------|
| OpenAI falla | Tag "ERROR IA", no marcar leído → reintento en siguiente ciclo |
| Graph API 429 (rate limit) | Retry con backoff exponencial (2s, 4s, 8s) |
| Graph API 5xx | Retry con backoff exponencial |
| Categoría sin plantilla | Tag "PENDIENTE GESTIONAR" (fallback seguro) |
| Email ya procesado (tiene tag) | Skip silencioso |

## Convenciones

- Python >=3.11, gestor de dependencias: `uv`
- Linter/formatter: `ruff` (line-length 100)
- Tests: `pytest` en `pacto_mundial/tests/`
- Configs de buzones: YAML en `pacto_mundial/config/`
- Archivos .py idealmente por debajo de 2000 líneas
- `PACTO_MUNDIAL_DRAFT_MODE=true` para modo dry-run (no envía emails reales)

## Documentación

| Archivo | Contenido |
|---------|-----------|
| [`CLAUDE.md`](CLAUDE.md) | Guía principal para Claude Code |
| [`ROADMAP.md`](ROADMAP.md) | Features implementados y pendientes |
| [`docs/BUSINESS_RULES.md`](docs/BUSINESS_RULES.md) | ADR y reglas críticas de negocio |
| [`pacto_mundial/config/asociacion.yaml`](pacto_mundial/config/asociacion.yaml) | Config Asociación: 18 categorías, routing, templates |
| [`pacto_mundial/config/contabilidad.yaml`](pacto_mundial/config/contabilidad.yaml) | Config Contabilidad: 7 categorías, routing, templates |
| [`pacto_mundial/config/proveedores_sostenibles.yaml`](pacto_mundial/config/proveedores_sostenibles.yaml) | Config Proveedores Sostenibles: 36 categorías, 25 templates ESP/PT |
| [`pacto_mundial/prompts/asociacion.txt`](pacto_mundial/prompts/asociacion.txt) | System prompt clasificación Asociación |
| [`pacto_mundial/prompts/contabilidad.txt`](pacto_mundial/prompts/contabilidad.txt) | System prompt clasificación Contabilidad |
| [`pacto_mundial/prompts/proveedores_sostenibles.txt`](pacto_mundial/prompts/proveedores_sostenibles.txt) | System prompt clasificación Proveedores Sostenibles |
| [`.env.example`](.env.example) | Variables de entorno requeridas |
