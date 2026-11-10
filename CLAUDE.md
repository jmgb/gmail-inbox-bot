> **Acceso SSH de los agentes desde este equipo (2026-11-02).** Para los agentes de trabajo que Jesús lance en su Ubuntu/WSL, con el usuario local `ubuntu`: `ssh alfredo-admin` y `ssh finanzas-admin` conectan como `prod-admin`, con `sudo -n` disponible.
>
> El acceso es permanente, sin passphrase ni caducidad de cuatro horas: SSH carga las claves locales automáticamente, también tras reiniciar. No pedir desbloqueos, ejecutar `ssh-add` ni buscar credenciales SSH en `.env`. Comprobación sin cambios: `ssh -o BatchMode=yes -o ConnectTimeout=10 alfredo-admin 'sudo -n true'` (o `finanzas-admin`).
>
> Esta nota prevalece sobre referencias SSH antiguas de esta guía para **esas sesiones locales**. En otros equipos, Javier y los demás colaboradores necesitan su propia cuenta/clave. No compartir privadas ni copiarlas a CI. Se conservan las restricciones de CI, runners y jobs autónomos; disponer de acceso no valida un comando de deploy ni autoriza publicaciones o reactivaciones. Seguir el procedimiento vigente del proyecto.

# CLAUDE.md

## Proyecto

Gmail Inbox Bot es un bot de automatización de emails centrado en **Gmail API** para un escenario de
uso personal o single-user.

## Decisión técnica base

- Entrada: **Cloudflare Email Routing -> Gmail inbox**
- Automatización: **Gmail API**
- Clasificación: **neutral-llm-gateway** con adapters oficiales de Groq/OpenAI
- Fuente de verdad: **Gmail**, no una base de datos externa

## Cómo encontrar la documentación

Este archivo solo trae lo que aplica a casi cualquier tarea; el detalle vive en su `.md`. Si el tema tiene línea-disparador aquí, sigue su enlace; si no, busca antes de actuar: `grep -ril "<tema>" docs README.md ROADMAP.md TASKS.md`.

## Cuentas, OAuth y métricas

Al añadir una cuenta Gmail, tocar tokens/scopes OAuth o la tabla `email_metrics`: cuentas `jesus82c@gmail.com` y `miguelgutierrezbarquin@gmail.com`, cada mailbox YAML de `config/` referencia su token con `refresh_token_env`; OAuth con **localhost redirect** (el flujo OOB está deprecado); aplicar las columnas `jev_*` de `scripts/supabase_create_table.sql` **antes** de desplegar código que las escriba. Detalle: [README.md §Referencia movida desde CLAUDE.md](README.md#referencia-movida-desde-claudemd).

Un YAML roto, sin `email` o con una acción desconocida en `pre_filters`/`routing` **para el
arranque** (`load_mailbox_configs` + `bot.validate_mailbox_config`): antes se saltaba en silencio.

## Reglas de diseño

### Prohibido borrar emails sin confirmación

**NUNCA** borrar permanentemente emails, labels, ni datos de Gmail sin confirmación explícita del
usuario. Mover a papelera (`messages/{id}/trash`) es aceptable — los emails son recuperables 30 días.
Lo prohibido es: borrado permanente, vaciar papelera, borrar labels, o cualquier acción irreversible
sobre el buzón. Siempre preguntar antes de ejecutar acciones destructivas.

### Gmail como fuente de verdad

El bot debe leer y escribir sobre el mismo sistema donde viven los emails.

### Scope inicial mínimo

Usar `gmail.modify` como base. Ya se amplió con `calendar.readonly` para la feature de recordatorios
de Google Calendar (ver `gmail_inbox_bot/calendar_reminders.py` y el README). No ampliar más scopes
sin una razón concreta.

### Labels, no carpetas

Gmail usa labels. No modelar Outlook folders como si fueran equivalentes exactos.

### Single-user primero

No introducir complejidad multi-tenant o colas externas salvo requisito claro.
### Premisa de inbox y routing

Antes de tocar categorías, destinos, tags (`REVISAR IA`, `ERROR IA`, `PENDIENTE GESTIONAR`), idempotencia o el footer de emails: los emails que requieren acción o son muy relevantes se quedan **sin leer en el inbox** con etiqueta del bot; todo lo demás sale del inbox. Los `PROCESSED_TAGS` evitan bucles: el bot no los reprocesa. Los emails de `calendar_reminders` **NO** llevan el footer de aiship.co (deben parecer escritos a mano; no re-añadirlo). Tabla de destinos, flujos y firma: [`docs/CLASSIFICATION_RULES.md`](docs/CLASSIFICATION_RULES.md).

### Clasificación (Jev + LLM de reserva)

Al cambiar reglas de clasificación o categorías: **Jev (`clasificador_jev.yml`) decide**; el prompt LLM (`clasificador_inbox.txt`) solo es fallback y debe quedar alineado. Cada regla nueva se escribe primero en el YAML de Jev. Añadir una categoría toca el YAML de Jev, `routing` de **todos** los `config/*.yml` y un `### <categoria>` en el prompt LLM (dos tests fallan si falta). Nunca borrar reglas que funcionan; modelo `jev-latest` explícito, no fijar versión; `spam` y `newsletters` tienen umbral de confianza y necesitan entrada `*_sin_confirmar` en el `routing`. Camino de vuelta: vaciar `JEV_API_KEY`. Detalle: [`docs/CLASSIFICATION_RULES.md`](docs/CLASSIFICATION_RULES.md#clasificación--mejora-continua).

## Despliegue

Al desplegar o tocar CI/VPS: cada push construye imagen en GHCR y solo **solicita** promoción; la aprobación y activación son manuales fuera de GitHub (`sudo gmail-promote approve …` / `apply …`). CI no tiene sudo, Docker ni secretos de ejecución. Cambiar el `.env` del checkout no actualiza el snapshot `runtime-env.json`. `scripts/vps_deploy.sh` es ruta heredada. Puerto `8007`; health en https://email.pymechat.com/health. Detalle (VPS, URLs, rollback, credenciales): [`docs/operations/secure-promotion.md`](docs/operations/secure-promotion.md#despliegue-notas-operativas-vps-urls-promotor).

## Comandos

- `uv sync`
- `uv run python -m gmail_inbox_bot`
- `uv run python -m gmail_inbox_bot --server` (FastAPI + bot en background)
- `uv run pytest`
- `uv run ruff check .`
- `uv run ruff format .`

## Pre-push checklist

Antes de hacer `git push`, ejecutar los mismos checks que correrá la GitHub Action y asegurar que pasan limpios:

```bash
bash scripts/ci-local.sh
```

No hacer push si alguno falla.

## Documentación clave

- `docs/EMAIL_INTEGRATION_OPTIONS.md`
- `docs/MIGRATION_PLAN.md`
- `docs/PACTO_BUSINESS_RULES_REFERENCE.md`
- `ROADMAP.md`
- Skill `/gmail` — referencia completa de la Gmail API y setup OAuth2
- [`docs/CLASSIFICATION_RULES.md`](docs/CLASSIFICATION_RULES.md)
- [`docs/operations/secure-promotion.md`](docs/operations/secure-promotion.md)

## 🔍 Cross-review con Codex (segundo par de ojos)

Tras una feature/cambio **relevante**, lanzar automáticamente `/codex:review --wait` como gate de IA antes del primer commit.

**Posición en el flujo**: tests/lint verdes → `/codex:review --wait` → (aplicar fixes) → `git add/commit/push`. Pre-commit (no pre-push) para que los fixes entren en el mismo commit y la historia git quede limpia.

**Lanzar SÍ**: features nuevas, refactors multi-archivo, cambios en lógica crítica/seguridad/auth, infra/deploy, migraciones DB, o cualquier cambio donde el coste de un bug sea alto.
**Lanzar NO**: typos, comentarios, logging, formateo, cambios de 1 línea o exploración.
**En duda**: lanzar (coste bajo, upside alto).

**Si hay hallazgos serios**: `/codex:rescue --resume "aplica los fixes propuestos"` antes de commit.

**Features grandes con commits incrementales**: una sola review al cerrar la feature con `--scope branch --base main`; los fixes van en un commit final "address codex review" antes del push.

## Reglas transversales

- Al terminar y superar las pruebas pertinentes, haz commit local sin pedir permiso, solo de los archivos de la tarea. Push solo con autorización explícita.
- Puede haber varias sesiones de agentes trabajando a la vez en este repo: comprueba `git status` y que el HEAD es tuyo antes de stagear, commitear o reescribir historia.
- Aplica siempre KISS: la solución más pequeña y simple que resuelva el caso actual.

## Secretos

Leer o ver un secreto en una sesión no obliga a rotarlo. Se rotan cada 90 días para minimizar riesgos, salvo que se expongan en un sitio público o a terceros o queden invalidados: entonces, en el momento.
