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

### Prohibido borrar emails sin confirmación

**NUNCA** borrar permanentemente emails, labels, ni datos de Gmail sin confirmación explícita del
usuario. Mover a papelera (`messages/{id}/trash`) es aceptable — los emails son recuperables 30 días.
Lo prohibido es: borrado permanente, vaciar papelera, borrar labels, o cualquier acción irreversible
sobre el buzón. Siempre preguntar antes de ejecutar acciones destructivas.

### Gmail como fuente de verdad

El bot debe leer y escribir sobre el mismo sistema donde viven los emails.

### Scope inicial mínimo

Usar `gmail.modify` salvo que exista una razón concreta para ampliar scopes.

### Labels, no carpetas

Gmail usa labels. No modelar Outlook folders como si fueran equivalentes exactos.

### Single-user primero

No introducir complejidad multi-tenant o colas externas salvo requisito claro.

### Premisa de inbox: relevante visible, ruido fuera

**Principio**: los emails que requieren acción del usuario o son muy relevantes se quedan **sin leer
en el inbox** con una etiqueta del bot. Todo lo demás se mueve a su carpeta correspondiente fuera
del inbox.

| Categoría | Destino | En INBOX | Unread | Motivo |
|---|---|---|---|---|
| `personal` | tag `REVISAR IA` | **Sí** | **Sí** | Requiere acción/respuesta del usuario |
| `finanzas` | tag `REVISAR IA` | **Sí** | **Sí** | Verificación o acción financiera |
| `otros` | tag `REVISAR IA` | **Sí** | **Sí** | Fallback seguro — revisión manual |
| `compras` | carpeta `Compras` | No | No | Informativo, no requiere acción |
| `notificaciones` | carpeta `Notificaciones` | No | No | Alertas de apps, no urgente |
| `automatico` | carpeta `Automatico` | No | No | Out-of-office, noreply, sin acción |
| `spam` | papelera | No | — | Basura |
| `newsletters` | carpeta `Newsletters` | No | **Sí** | No requiere acción inmediata pero se conserva sin leer para lectura eventual |
| error clasificador | tag `ERROR IA` | **Sí** | **Sí** | Fallo técnico pre-clasificación — revisar manualmente |
| error config/acción | tag `PENDIENTE GESTIONAR` | **Sí** | **Sí** | Fallo post-clasificación (sin template, sin routing, acción desconocida) |

**Newsletters**: se mueven fuera del inbox pero se mantienen sin leer (`is_read: false`). No son
urgentes ni requieren acción, pero el usuario quiere poder revisarlas a su ritmo. El estado unread
sirve como indicador de "pendiente de leer" dentro de la carpeta Newsletters.

**Errores del clasificador** (`ERROR IA`): cuando falla la clasificación (sin OpenAI client, sin
prompt, o error de la API), el email se etiqueta `ERROR IA` y se deja **sin leer en el inbox**.

**Errores de configuración/acción** (`PENDIENTE GESTIONAR`): cuando la clasificación funciona pero
falla la acción (sin template, sin routing, acción desconocida), se etiqueta `PENDIENTE GESTIONAR`
y se deja **sin leer en el inbox**.

Ambos tags están en `PROCESSED_TAGS`, así que el bot no los reprocesa. El usuario los ve y decide
qué hacer.

### Idempotencia y prevención de bucles

El bot tiene dos mecanismos de idempotencia:

1. **Acciones que quitan INBOX**: `move_email` remueve el label `INBOX`, así el query
   `is:unread in:inbox` no lo encuentra en el siguiente poll.
2. **`already_processed()`**: verifica si el email tiene algún tag de `PROCESSED_TAGS`
   (ej. `RESPONDIDO IA`, `REVISAR IA`, `ERROR IA`). Si lo tiene, lo salta.

**Flujo para categorías que se quedan en inbox** (`personal`, `finanzas`, `otros`):

1. Email llega → labels: `INBOX`, `UNREAD`
2. `_handle_tag` → añade label `REVISAR IA`, marca leído
3. Override `is_read: false` → vuelve a poner `UNREAD`
4. Estado final → labels: `INBOX`, `UNREAD`, `REVISAR IA`
5. Siguiente poll (`is:unread in:inbox`) → lo encuentra → `already_processed()` detecta
   `REVISAR IA` ∈ `PROCESSED_TAGS` → **skip** → sin bucle

El email queda **sin leer en el inbox** para que el usuario lo vea, pero el bot no lo reprocesa.

**Para categorías que salen del inbox** (ej. `compras`, `notificaciones`): el email queda leído y
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

### Clasificación — mejora continua

El prompt del clasificador (`gmail_inbox_bot/prompts/clasificador_inbox.txt`) tiene dos bloques:

1. **Reglas generales** — definiciones de categoría y criterios base. Rara vez cambian.
2. **Reglas aprendidas de producción** — refinamientos basados en errores reales observados en logs.

**Workflow para mejorar la clasificación:**
1. Revisar logs del VPS: `docker logs gmail-inbox-bot --tail 100` o `cat logs/app.log`
2. Identificar clasificaciones incorrectas (ej. banco → `otros` en vez de `finanzas`)
3. Añadir regla específica en la sección "Reglas aprendidas de producción" del prompt
4. Deploy (push a main → autodeploy)

**Principios:**
- Las reglas deben ser concretas (dominios, patrones de asunto, tipos de remitente)
- Nunca borrar reglas que funcionan — solo añadir o refinar
- Preferir reglas por remitente/dominio (más fiables) sobre reglas por contenido del body
- Documentar el caso real que motivó cada regla

## Despliegue

- **VPS**: `158.69.215.223` (usuario `ubuntu`)
- **Ruta en VPS**: `/home/ubuntu/services/gmail-inbox-bot`
- **Deploy automático**: push a `main` → GitHub Action (`deploy-vps.yml`) → `git pull` + `docker compose up -d --build`
- **Puerto**: `8007` (mapeado a `8000` interno)
- **Admin Dashboard**: https://email.pymechat.com/admin/dashboard
- **Log Viewer**: https://email.pymechat.com/admin/logs
- **Health**: https://email.pymechat.com/health
- **Password admin**: variable `LOGS_VIEWER_PASSWORD` en `.env`

## Métricas (Supabase)

- **Tabla**: `email_metrics` (mismo proyecto Supabase que pacto-mundial-bot)
- **Escritura**: fire-and-forget en cada email procesado (`metrics.py`)
- **Lectura**: dashboard vía `/admin/api/metrics`
- **SQL migrations**: `scripts/supabase_create_table.sql`
- **SQL runner**: `uv run python scripts/supabase_sql.py "SELECT ..."`

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
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

No hacer push si alguno falla.

## Documentación clave

- `docs/EMAIL_INTEGRATION_OPTIONS.md`
- `docs/MIGRATION_PLAN.md`
- `docs/PACTO_BUSINESS_RULES_REFERENCE.md`
- `ROADMAP.md`
- Skill `/gmail` — referencia completa de la Gmail API y setup OAuth2
