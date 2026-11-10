# Reglas de clasificación, routing e idempotencia del inbox

> Movido desde `CLAUDE.md` el 2026-11-10 (allí queda un resumen con enlace aquí).

## Premisa de inbox: relevante visible, ruido fuera

**Principio**: los emails que requieren acción del usuario o son muy relevantes se quedan **sin leer
en el inbox** con una etiqueta del bot. Todo lo demás se mueve a su carpeta correspondiente fuera
del inbox.

| Categoría | Destino | En INBOX | Unread | Motivo |
|---|---|---|---|---|
| `personal` | tag `REVISAR IA` | **Sí** | **Sí** | Requiere acción/respuesta del usuario |
| `finanzas` | tag `REVISAR IA` | **Sí** | **Sí** | Verificación o acción financiera |
| `otros` | tag `REVISAR IA` | **Sí** | **Sí** | Fallback seguro — revisión manual |
| `compras` | carpeta `Compras` | No | No | Informativo, no requiere acción |
| `facturas` | carpeta `Facturas` | No | No | Factura/recibo de un cobro ya hecho: comprobante para archivar |
| `notificaciones` | carpeta `Notificaciones` | No | No | Alertas de apps, no urgente |
| `automatico` | carpeta `Automatico` | No | No | Out-of-office, noreply, sin acción |
| `spam` | papelera | No | — | Basura |
| `newsletters` | carpeta `Newsletters` | No | **Sí** | No requiere acción inmediata pero se conserva sin leer para lectura eventual |
| error clasificador | tag `ERROR IA` | **Sí** | **Sí** | Fallo técnico pre-clasificación — revisar manualmente |
| error config/acción | tag `PENDIENTE GESTIONAR` | **Sí** | **Sí** | Fallo post-clasificación (sin template, sin routing, acción desconocida) |

**Facturas**: facturas y recibos de cobros ya hechos o domiciliados (Anthropic, Cloudflare, GCP,
DIGI, la factura de un pedido…). Salen del inbox porque no piden nada. Las que sí piden acción (por
vencer, cobro fallido, factura que falta, proveedor que envía facturas para pagar a mano) siguen en
`finanzas` con `REVISAR IA`. Se marcan **leídas** (decisión del usuario, 2026-09-30): son un
comprobante, no algo pendiente. La etiqueta la crea el bot la primera vez que mueve un email. La
etiqueta no sustituye al cron mensual `scripts/download_invoice_emails.py`, que busca por palabras
clave a propósito para no depender de que el clasificador acierte.

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

## Idempotencia y prevención de bucles

El bot tiene tres mecanismos de idempotencia:

1. **La query del poll excluye los `PROCESSED_TAGS`**: `build_poll_query()` (`mail_processing.py`)
   añade un `-label:"…"` por tag a la `query` del buzón, así que Gmail no devuelve los emails ya
   procesados y el bot no descarga su cuerpo. Se deriva de `PROCESSED_TAGS`, no de una lista copiada.
2. **Acciones que quitan INBOX**: `move_email` remueve el label `INBOX`, así el query
   `is:unread in:inbox` no lo encuentra en el siguiente poll.
3. **`already_processed()`**: verifica si el email tiene algún tag de `PROCESSED_TAGS`
   (ej. `RESPONDIDO IA`, `REVISAR IA`, `ERROR IA`). Si lo tiene, lo salta. Se mantiene aunque el
   mecanismo 1 lo cubra: es la red de seguridad si se renombra un label o alguien sobreescribe la
   `query` del YAML sin las exclusiones.

**Flujo para categorías que se quedan en inbox** (`personal`, `finanzas`, `otros`):

1. Email llega → labels: `INBOX`, `UNREAD`
2. `_handle_tag` → añade label `REVISAR IA`, marca leído
3. Override `is_read: false` → vuelve a poner `UNREAD`
4. Estado final → labels: `INBOX`, `UNREAD`, `REVISAR IA`
5. Siguiente poll → la query lleva `-label:"REVISAR IA"`, así que Gmail **ya no lo devuelve** y no
   se descarga su cuerpo. Si llegara (label renombrado, query sobreescrita), `already_processed()`
   detecta `REVISAR IA` ∈ `PROCESSED_TAGS` → **skip** → sin bucle

El email queda **sin leer en el inbox** para que el usuario lo vea, pero el bot no lo reprocesa.

**Para categorías que salen del inbox** (ej. `compras`, `notificaciones`): el email queda leído y
fuera del inbox — doblemente protegido.

## Firma / footer de emails

Todos los emails salientes (reply, dynamic_reply, reply_with_attachment, forward) incluyen un footer
HTML de marketing desde `templates/signature.html`. El footer es un **caption promocional de aiship.co**
que busca generar efecto llamada: el destinatario debe percibir que un asistente personal IA redactó
el email con precisión. **No desalentar respuestas** — el footer es branding, no disclaimer.

- Idioma del footer: **siempre en inglés**.
- Se puede personalizar por mailbox con `signature_file: ruta/al/archivo.html` en el YAML.
- Se puede desactivar con `signature_file: ""`.
- Default: `templates/signature.html`.

**Excepción — recordatorios de Calendar**: los emails de `calendar_reminders` **NO** llevan este
footer. Deben parecer escritos a mano por el usuario (prosa natural, firma personal `sender_name`,
sin mención a IA ni a aiship.co). No re-añadir el footer a esos emails. Ver `calendar_reminders.py`.

## Clasificación — mejora continua

**Jev es el clasificador principal** (ver más abajo), así que las reglas viven en dos sitios, por
este orden:

1. **`gmail_inbox_bot/prompts/clasificador_jev.yml`** — lo que decide. Una entrada por categoría con
   `what` / `not_for` / `examples`. Aquí se escribe primero cada regla nueva.
2. **`gmail_inbox_bot/prompts/clasificador_inbox.txt`** — prompt del LLM, que solo clasifica si Jev
   falla. Tiene **reglas generales** (definiciones de categoría) y **reglas aprendidas de
   producción**. Se mantiene alineado con el YAML para que el fallback no clasifique distinto.

**Añadir una categoría** toca: el YAML de Jev, `routing` de **todos** los `config/*.yml` y un
`### <categoria>` en el prompt LLM. Dos tests de `tests/test_jev_classifier.py` fallan si alguno de
los tres se queda sin actualizar.

**Workflow para mejorar la clasificación:**
1. Revisar logs del VPS: `docker logs gmail-inbox-bot --tail 100` o `cat logs/app.log`, o el
   dashboard (`/admin/dashboard`)
2. Identificar clasificaciones incorrectas (ej. banco → `otros` en vez de `finanzas`)
3. Añadir la regla en `clasificador_jev.yml` (`not_for` / `examples` de las categorías implicadas)
   y su equivalente en "Reglas aprendidas de producción" del prompt LLM
4. Construir y solicitar promoción (push a main); aprobar y activar la imagen fuera de GitHub.

**Principios:**
- Las reglas deben ser concretas (dominios, patrones de asunto, tipos de remitente)
- Nunca borrar reglas que funcionan — solo añadir o refinar
- Preferir reglas por remitente/dominio (más fiables) sobre reglas por contenido del body
- Documentar el caso real que motivó cada regla

**Jev (TypeSafe.ai) es el clasificador principal desde el 2026-09-27** —
`gmail_inbox_bot/jev_classifier.py`. Si `JEV_API_KEY` está en el `.env`, **Jev decide la categoría** y
el routing actúa sobre ella; la cadena LLM (`gpt-oss-120b` → `gpt-6-luna`) queda como **fallback y
solo entra si Jev devuelve error**. Del 21 al 27 de septiembre corrió en sombra sin decidir nada.

- **Modelo `jev-latest`, explícito** (decisión del usuario, 2026-09-30): las nuevas versiones
  estables se adoptan automáticamente. No fijar una versión concreta ni heredar el modelo del
  entorno; las métricas conservan la versión real que respondió.
- **Aviso de versión**: `jev_model_monitor.py` observa `response.model` en segundo plano y
  notifica una vez por cambio. El estado en `logs/jev_model_version.txt` sobrevive al despliegue;
  la primera versión solo establece la referencia. Un bloqueo de archivo evita duplicados entre
  procesos. El aviso usa el transporte Telegram existente (con reintentos); un fallo del monitor
  se registra como warning y nunca cambia la clasificación.
- **Camino de vuelta sin tocar código**: vaciar `JEV_API_KEY` y reiniciar. `build_jev_classifier()`
  devuelve `None` y clasifica el LLM como antes.
- **La confianza filtra desde el 2026-10-03** (`CONFIDENCE_FLOORS` en `jev_classifier.py`): `spam`
  exige `>=0.95` y `newsletters` `>=0.70`; por debajo la categoría efectiva pasa a
  `spam_sin_confirmar` / `newsletters_sin_confirmar`. El resto no lleva umbral porque su destino ya
  es el inbox. El criterio es el coste del error en ese destino, no la pericia de Jev. **Nada cae al
  LLM por confianza baja**: la sombra medía desacuerdo, no quién acierta.
  ⚠️ Esas dos categorías **necesitan entrada en el `routing` de cada buzón**; sin ella caen en
  `PENDIENTE GESTIONAR` (inbox, sin leer) — nunca borra, pero no es el destino diseñado.
- **`razon_clasificacion`** la sintetiza `classification_from_jev()` a partir de la confianza y la
  segunda opción: Jev no devuelve prosa. Las filas decididas por Jev llevan `model=jev-<versión>`,
  y **no** llevan `usage` ni `cost` (Jev no factura por tokens).
- **La comparativa Jev vs LLM ya no crece**: si Jev decide, no hay clasificación LLM con la que
  comparar. El dashboard excluye esas filas del cálculo (`_decided_by_jev`) para no reportar un
  100 % falso, y las cuenta aparte en "Decididos por Jev".

Los criterios de Jev viven en `gmail_inbox_bot/prompts/clasificador_jev.yml` (una entrada por
categoría con `what` / `not_for` / `examples`, mismas claves que `routing`; un test comprueba la
paridad). Ambos clasificadores usan el mismo formato
(`email_format.format_email_for_classifier`), pero Jev limita el cuerpo a 6000 caracteres. **Cada
regla nueva se escribe ahora en el YAML de Jev**, que es quien decide; el prompt LLM solo gobierna
el fallback. Si el YAML falta o es inválido el bot falla al arrancar (error de despliegue, a
propósito). Diseño:
`docs/superpowers/specs/2026-09-21-jev-shadow-classification-design.md`.
