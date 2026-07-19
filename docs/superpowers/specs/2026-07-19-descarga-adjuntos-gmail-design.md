# Archivo local y limpieza progresiva de Gmail

**Fecha**: 2026-07-19
**Estado**: fase inicial implementada y piloto ejecutado; escalado pendiente de revisión humana

**Resultado del piloto (2026-07-19)**: `jesus82c`, `has:attachment`, 10 mensajes procesados, 10
`.eml` íntegros, 10 artefactos extraídos, 0 errores y 0 escrituras en Gmail. La muestra contenía
4 `application/gzip` y 6 `application/zip`; no contenía PDF ni imágenes inline, por lo que todavía no
se considera validada la cobertura de esos dos casos.

## Objetivo

Reducir el espacio ocupado en Gmail sin perder contenido relevante. Primero se crea una copia local
verificable de cada mensaje seleccionado y de sus ficheros; después el usuario decide, mensaje a
mensaje, cuáles se pueden mover a papelera.

La adopción será deliberadamente progresiva:

1. piloto de 10 mensajes de `jesus82c@gmail.com`;
2. todos los mensajes `has:attachment` de esa cuenta;
3. repetición en `miguelgutierrezbarquin@gmail.com`;
4. en una iteración futura, barrido de todos los mensajes para localizar recursos inline que Gmail
   no incluya en `has:attachment`.

No se moverá ningún mensaje a papelera durante el piloto.

## Decisiones principales

| Dimensión | Decisión |
|---|---|
| Primera cuenta | `jesus82c@gmail.com` (`mailbox: jesus82c`) |
| Piloto | Primeros 10 mensajes devueltos por `has:attachment`, un worker |
| Primera iteración | Solo `has:attachment`; spam y papelera excluidos |
| Segunda iteración futura | Todos los mensajes, para cubrir inline-only y casos no indexados como adjunto |
| Copia de seguridad | Mensaje completo `.eml` más ficheros extraídos |
| Carpeta manual | `attachments_dump/<mailbox>/attachments/`, plana y navegable |
| Qué se extrae | Todos los adjuntos, todas las partes `image/*` embebidas y todos los PDF |
| Unidad de borrado | Mensaje completo, nunca un adjunto individual |
| Unidad de decisión | Una fila por mensaje en `messages.csv` |
| Inventario | Una fila por fichero extraído en `index.csv` |
| Estado reanudable | SQLite local transaccional |
| Runtime | Scripts locales one-off; sin integración con polling, dashboard o VPS |

## Archivos físicos frente al CSV

El CSV no contiene los binarios: es únicamente un índice para filtrar y decidir. Los ficheros se
guardan físicamente en una carpeta plana por cuenta para que se puedan abrir y revisar manualmente
sin recorrer una carpeta por mensaje:

```text
attachments_dump/
  jesus82c/
    attachments/       # todos los ZIP, PDF, imágenes, etc. de esta cuenta
    messages/          # un .eml por mensaje
  messages.csv         # decisión de borrar, una fila por mensaje
  index.csv            # inventario, una fila por fichero
```

Cada nombre de `attachments/` incorpora `message_id` y `part_key` antes del nombre original, por
ejemplo `19f..._3_factura.pdf`, para evitar colisiones. La columna `ruta_local` apunta a ese fichero
real; `nombre_fichero` solo es su nombre legible.

## Por qué se guarda también el `.eml`

La Gmail API no permite borrar un adjunto individual. `messages/{id}/trash` mueve a papelera el
mensaje entero, incluido su cuerpo, cabeceras y todos sus adjuntos.

Guardar solo los ficheros no cumpliría el objetivo de “no perder nada relevante”: se perderían el
texto, el contexto y las cabeceras cuando Gmail purgase la papelera. Por eso cada mensaje se descarga
con `messages.get(format=raw)` y se conserva como `.eml`. Desde esos mismos bytes se extraen los
adjuntos, imágenes embebidas y PDF usando el parser MIME de la biblioteca estándar.

El `.eml` es el respaldo canónico del contenido. El manifiesto conserva además metadatos específicos
de Gmail que no forman parte del RFC822: `message_id`, `thread_id`, `internalDate`, `labelIds`,
`historyId` y `sizeEstimate`.

Esta decisión duplica parte del contenido en disco local —el fichero existe dentro del `.eml` y
también extraído—, pero facilita revisar y reutilizar los adjuntos y ofrece una garantía de archivo
mucho más fuerte antes de limpiar Gmail.

## Cobertura por iteraciones

### Iteración A — `has:attachment`

Es el mejor punto de partida porque concentra los mensajes que Gmail considera que tienen adjuntos y
probablemente ofrece más ahorro por mensaje. De cada mensaje coincidente se guarda el `.eml` entero;
por tanto también se extraen las imágenes inline y PDF presentes dentro de esos mensajes.

Límite conocido: Gmail puede no devolver con `has:attachment` un mensaje que solo contenga una imagen
inline o una estructura MIME atípica. El resultado de esta iteración no se describirá como una copia
completa de todo el buzón.

### Iteración B futura — todos los mensajes

Se pagina el buzón sin query y se procesan los mensajes todavía no archivados. Esto permite capturar
emails con recursos inline que no aparecieron en la iteración A. Reutiliza el mismo manifiesto y no
vuelve a descargar mensajes ya verificados.

Las imágenes remotas enlazadas desde HTML no están embebidas en el email y no pueden recuperarse de
su MIME. Descargar URLs externas —incluidos píxeles de tracking— queda fuera de alcance.

## Rollout y gates

| Fase | Alcance | Modifica Gmail | Gate para avanzar |
|---|---|:---:|---|
| 0 | Tests y fixtures locales | No | Suite verde |
| 1 | 10 mensajes `has:attachment` de `jesus82c` | No | Revisión manual de EML, ficheros y CSV |
| 2 | Todos los `has:attachment` de `jesus82c` | No | 0 errores, hashes válidos y recuentos revisados |
| 3 | Triaje/borrado controlado de `jesus82c` | Sí, solo tras confirmación | Auditoría de una tanda pequeña |
| 4 | Todos los `has:attachment` de `miguelgutierrezbarquin` | No | Mismas validaciones que fase 2 |
| 5 | Triaje/borrado controlado de la segunda cuenta | Sí, solo tras confirmación | Auditoría completa |
| 6 | Barrido futuro sin query de ambas cuentas | No inicialmente | Evaluar cobertura y coste con lo aprendido |

Cada gate exige decisión humana. El script nunca encadena automáticamente descarga y papelera.

## Piloto de 10 mensajes

Comando previsto:

```bash
uv run python scripts/download_attachments.py \
  --output-dir attachments_dump \
  --mailbox jesus82c \
  --query 'has:attachment' \
  --max-messages 10 \
  --workers 1
```

`--mailbox` acepta el `name` del YAML o el email exacto. `--max-messages` limita mensajes nuevos
procesados en esa invocación, no define un estado distinto. Al retirarlo, el barrido completo puede
continuar en el mismo `output-dir` y saltar los 10 ya verificados.

La API no documenta el orden como contrato de estabilidad; el piloto procesa los primeros 10 IDs
devueltos y guarda exactamente cuáles fueron. No se presupone que formen una muestra estadística.

### Checklist manual del piloto

Para cada uno de los 10 mensajes:

- abrir el mensaje original desde `gmail_url`;
- abrir el `.eml` local y comprobar remitente, asunto, cuerpo y adjuntos;
- comparar los adjuntos visibles en Gmail con las filas de `index.csv`;
- comprobar PDF e imágenes inline cuando existan;
- confirmar que los tamaños y SHA-256 están presentes;
- verificar que el mensaje de Gmail sigue intacto.

Además se revisan nombres generados, rutas, caracteres Unicode, mensajes sin ficheros extraíbles y el
resumen total. Si la muestra no contiene PDF o inline, se ejecuta otra tanda pequeña antes de escalar.

## Arquitectura

### 1. API pública mínima de `GmailClient`

El exportador no debe llamar a `_request()` directamente. Se añaden:

```python
def iter_message_stubs(
    self,
    *,
    query: str | None,
    include_spam_trash: bool = False,
    page_size: int = 500,
) -> Iterator[dict]:
    """Pagina messages.list y produce id/threadId."""

def get_raw_message(self, message_id: str) -> dict:
    """Devuelve messages.get(format=raw), incluidos metadatos y raw base64url."""
```

El segundo método valida y decodifica `raw` con base64url tolerante a padding omitido. Devuelve tanto
los bytes RFC822 como los metadatos necesarios. No hace falta `messages.attachments.get`: el mensaje
raw ya contiene todas las partes y reduce las llamadas por mensaje.

`GmailClient._request()` ampliará su retry común a `429`, `500`, `502`, `503`, `504` y a `403` solo
cuando Gmail informe `rateLimitExceeded` o `userRateLimitExceeded`. Respeta `Retry-After`; en otro
caso usa backoff exponencial truncado con jitter, máximo 5 reintentos. Un `403` de permisos no se
reintenta y un `401` solo refresca token una vez.

### 2. Exportador MIME

`scripts/download_attachments.py`:

1. Carga `.env` y `config/*.yml` con las funciones existentes.
2. Valida cuenta, token, output y espacio libre mínimo.
3. Descubre IDs mediante `messages.list` paginado, hasta 500 por página.
4. Respeta `--max-messages` contando solo mensajes todavía no completados.
5. Obtiene cada mensaje en raw y escribe primero su `.eml` de forma atómica.
6. Parsea los mismos bytes con `email.parser.BytesParser(policy=default)`.
7. Extrae las partes objetivo, escribe cada fichero de forma atómica y calcula SHA-256.
8. Persiste resultados en SQLite y regenera los CSV.

Una parte MIME se extrae si cumple al menos una condición:

- tiene `Content-Disposition: attachment`;
- tiene un `filename` no vacío;
- su MIME es `image/*`, aunque sea `inline` y tenga `Content-ID`;
- su MIME es `application/pdf`, aunque no tenga nombre o disposición.

Para partes sin nombre se genera uno estable con `part_id` y una extensión derivada de MIME, por
ejemplo `inline_2_1.png` o `document_3.pdf`. Los adjuntos `message/rfc822` se conservan como `.eml`.
Una parte cifrada u opaca que no pueda descomponerse se conserva dentro del `.eml`, se señala en el
índice y bloquea el borrado automático hasta revisión.

### 3. Estado transaccional

`attachments_dump/.state.sqlite3` es la fuente de verdad operativa. Esquema mínimo:

```text
runs(
  id, schema_version, account, query, max_messages, started_at, finished_at, status
)

messages(
  account, message_id, thread_id, internal_date, label_ids, history_id,
  gmail_size_estimate, eml_path, eml_size, eml_sha256, status, last_error,
  PRIMARY KEY(account, message_id)
)

artifacts(
  account, message_id, part_key, kind, disposition, content_id, original_filename,
  local_path, mime_type, size_bytes, sha256, status, last_error,
  PRIMARY KEY(account, message_id, part_key)
)
```

Estados de mensaje: `discovered`, `processing`, `completed`, `partial_error`. Solo `completed` es
elegible para triaje. Un error parcial se reintenta al relanzar y nunca se convierte silenciosamente
en completado.

SQLite evita corrupción con varios workers. Los workers hacen red, parseo, hash y escritura; un
coordinador único confirma transacciones. Los CSV son vistas regenerables, no estado primario.

### 4. Escritura segura y layout

```text
attachments_dump/
  jesus82c/
    attachments/
      <message_id>_<part_key>_<nombre_saneado>
    messages/
      <message_id>.eml
  miguelgutierrezbarquin/
    ...
  messages.csv
  index.csv
  trash_results.csv
  .state.sqlite3
```

Los ficheros se escriben como `.part`, se hace `flush` + `fsync` y se publican con `os.replace`. Las
rutas incorporan claves estables, se normalizan a Unicode NFC y deben resolver siempre dentro del
output. Se eliminan separadores, NUL, controles, `.` y `..`; el nombre se trunca conservando extensión.
La carpeta plana no sacrifica trazabilidad: `message_id` y `part_key` permiten volver al mensaje y a
la fila exacta del índice.

Un fichero existente solo se reutiliza si coincide con tamaño y SHA-256. El exportador comprueba
espacio libre antes de cada mensaje y mantiene una reserva configurable. `attachments_dump/`, SQLite
y los CSV se añaden a `.gitignore` antes del piloto porque contienen información personal.

## Índices para revisión

### `messages.csv` — decisión de limpieza

Una fila por mensaje. Es el único CSV que lee `trash_marked.py`.

| Columna | Contenido |
|---|---|
| `cuenta` | Cuenta propietaria |
| `mailbox` | Nombre estable del YAML |
| `thread_id` | ID del hilo |
| `message_id` | ID del mensaje |
| `gmail_url` | Enlace al hilo en la cuenta correcta |
| `fecha` | `internalDate` ISO 8601 UTC |
| `de` | Remitente |
| `asunto` | Asunto |
| `labels` | Snapshot de labels Gmail |
| `tamano_gmail_estimado` | `sizeEstimate`, útil para priorizar ahorro |
| `numero_ficheros` | Ficheros extraídos |
| `tamano_ficheros` | Suma de bytes extraídos |
| `ruta_eml` | Ruta relativa al respaldo completo |
| `sha256_eml` | Integridad del respaldo |
| `estado_archivo` | `completed` o error |
| `error` | Error sanitizado |
| `borrar` | Vacío; el usuario escribe `x` |

### `index.csv` — inventario de ficheros

Una fila por adjunto, imagen embebida o PDF extraído.

| Columna | Contenido |
|---|---|
| `cuenta`, `message_id`, `part_key` | Clave estable |
| `gmail_url`, `fecha`, `de`, `asunto` | Contexto para revisión |
| `tipo` | `attachment`, `inline_image`, `pdf` o `attached_message` |
| `disposition`, `content_id` | Metadatos MIME |
| `nombre_fichero` | Nombre original o generado |
| `ruta_local` | Ruta relativa al fichero físico dentro de `attachments/` |
| `tamano_bytes`, `mime_type`, `sha256` | Integridad y tipo |
| `estado`, `error` | Resultado de extracción |

Ambos CSV usan `utf-8-sig`. Los campos procedentes del email se neutralizan ante formula injection
de hojas de cálculo. Al regenerar `messages.csv` se conserva `borrar` por `(cuenta, message_id)`;
duplicados, columnas inválidas o valores distintos de vacío/`x` abortan para no perder decisiones.

## Reanudación y escalado

El piloto y el barrido completo comparten manifiesto:

```bash
# Fase 1: piloto
uv run python scripts/download_attachments.py \
  --output-dir attachments_dump --mailbox jesus82c \
  --query 'has:attachment' --max-messages 10 --workers 1

# Fase 2: continuar hasta completar la primera cuenta
uv run python scripts/download_attachments.py \
  --output-dir attachments_dump --mailbox jesus82c \
  --query 'has:attachment' --workers 4

# Fase 4: segunda cuenta, solo cuando se apruebe el gate anterior
uv run python scripts/download_attachments.py \
  --output-dir attachments_dump --mailbox miguelgutierrezbarquin \
  --query 'has:attachment' --workers 4

# Fase 6 futura: ampliar a mensajes no cubiertos por has:attachment
uv run python scripts/download_attachments.py \
  --output-dir attachments_dump --mailbox jesus82c --all-messages
```

Cambiar de `has:attachment` a `--all-messages` amplía el conjunto: los IDs ya completados se saltan.
El manifiesto guarda cada ejecución y su scope para que el resumen no confunda una fase parcial con
una cuenta completamente escaneada.

Al final de un barrido sin límite se repite una vez el descubrimiento para incorporar mensajes
llegados mientras corría. En el piloto no se hace ese segundo pase.

## Cuota y concurrencia

Según la cuota publicada a 2026-07-19, hay 6.000 unidades/minuto por usuario;
`messages.list` cuesta 5 y `messages.get` cuesta 20. El diseño procesa una sola cuenta cada vez, usa
un limitador compartido de 3 solicitudes/s y permite entre 1 y 8 workers.

El piloto fuerza un worker para facilitar el diagnóstico. El barrido completo propone 4 workers para
solapar latencia, sin desactivar el limitador. El resumen muestra llamadas, reintentos, mensajes,
bytes y duración antes de usar la segunda cuenta.

## Triaje y movimiento a papelera

El comando es dry-run por defecto:

```bash
uv run python scripts/trash_marked.py --messages attachments_dump/messages.csv

# Ejecución real, solo después de revisar el dry-run
uv run python scripts/trash_marked.py \
  --messages attachments_dump/messages.csv \
  --execute
```

Antes de incluir un mensaje en el resumen valida:

- `borrar` es exactamente `x`, sin distinguir mayúsculas y recortando espacios;
- cuenta y message ID coinciden con el manifiesto;
- estado del mensaje es `completed`;
- `message.eml` existe y coincide en tamaño y SHA-256;
- todos los artifacts esperados existen y coinciden en tamaño y SHA-256;
- no hay errores MIME pendientes.

`--execute` exige TTY y escribir exactamente `TRASH <numero_de_mensajes>`. Luego agrupa por cuenta y
llama al método existente `GmailClient.delete_email()`, que usa `messages.trash`. Continúa ante fallos
y añade una fila por mensaje a `trash_results.csv`. Si ya está en papelera registra
`already_in_trash`.

Nunca usa `messages.delete`, `batchDelete`, vaciado de papelera, `--yes` ni ejecución no interactiva.
La primera ejecución real debe ser una tanda pequeña elegida por el usuario después de completar la
cuenta `jesus82c`.

## Manejo de errores y privacidad

- Salida `0`: scope solicitado completado sin errores; `1`: barrido terminado con errores;
  `2`: configuración o estado inválido.
- `SIGINT` termina el mensaje en curso, hace checkpoint y deja pendientes los trabajos no confirmados.
- `404`, base64 inválido, MIME no parseable o discrepancia de hash quedan explícitos y bloquean
  papelera.
- No se imprimen cuerpos, destinatarios, asuntos, nombres de fichero ni tokens por defecto.
- Los errores persistidos se limitan y sanean para no almacenar credenciales.
- El directorio se crea con permisos restrictivos cuando el sistema lo permita.

## Testing

La implementación seguirá TDD y mockeará HTTP en `GmailClient._request`.

### Cliente Gmail

- paginación, `pageToken`, límite de página e `includeSpamTrash`;
- `format=raw` y base64url sin padding;
- retries de `429`, `403` retryable, `5xx`, `Retry-After` y límite de intentos;
- no retry de permisos y un único refresh tras `401`.

### Exportador

- parser MIME con multipart anidado;
- adjuntos con/sin filename, inline con CID, `image/*`, PDF sin nombre y `message/rfc822`;
- `.eml` exacto, metadatos Gmail, nombres generados y prevención de path traversal;
- escritura atómica, tamaño, SHA-256 y falta de espacio;
- estados `partial_error`/retry, reanudación y ausencia de duplicados;
- `--max-messages 10` cuenta solo mensajes nuevos y se puede continuar sin límite;
- ampliación `has:attachment` → todos los mensajes sin redescargar completados;
- regeneración de ambos CSV preservando `borrar` y evitando formula injection;
- igualdad de claves/hashes con 1 y 4 workers.

### Papelera

- dry-run hace cero escrituras Gmail;
- agrupación por cuenta + mensaje;
- rechazo de CSV alterado, valores desconocidos, error parcial, EML/fichero ausente o hash distinto;
- rechazo sin TTY o con frase incorrecta;
- solo `messages.trash`, resultados parciales y auditoría.

## Plan de implementación

1. Añadir outputs sensibles a `.gitignore` y crear fixtures MIME representativos.
2. Escribir tests rojos del cliente raw, parser, estado e índices.
3. Implementar exportador secuencial, reanudable y atómico.
4. Ejecutar el piloto de 10 mensajes de `jesus82c`; no implementar/usar papelera para avanzar
   automáticamente.
5. Corregir hallazgos del piloto y repetir una tanda pequeña si faltan casos representativos.
6. Añadir concurrencia limitada y completar `has:attachment` de `jesus82c`.
7. Implementar y validar `trash_marked.py`; ejecutar solo una tanda aprobada manualmente.
8. Repetir la descarga completa en la segunda cuenta.
9. Evaluar la iteración futura de todos los mensajes con métricas reales de tiempo y espacio.

En cada cambio relevante: `ruff`, suite completa y gate de cross-review indicado en `CLAUDE.md`.

## Criterios de aceptación

- El piloto procesa como máximo 10 mensajes nuevos y modifica cero mensajes Gmail.
- Los ficheros extraídos quedan visibles directamente en `attachments_dump/<mailbox>/attachments/`;
  no se dejan solo dentro de un CSV ni ocultos en carpetas por mensaje.
- Cada mensaje completado tiene `.eml` verificable y snapshot de sus metadatos Gmail.
- Adjuntos, inline `image/*` y PDF presentes en esos mensajes aparecen en `index.csv`.
- Relanzar o ampliar el límite no crea duplicados.
- Quitar el límite continúa el mismo barrido de `jesus82c` hasta cero errores.
- La segunda cuenta no se toca antes de aprobar el resultado de la primera.
- `messages.csv` permite ordenar por ahorro estimado y decidir a nivel de mensaje.
- Ningún mensaje con archivo incompleto o alterado es elegible para papelera.
- Sin `--execute` y confirmación exacta se realizan cero escrituras Gmail.
- Una ejecución real solo llama a `messages.trash` y deja auditoría por mensaje.
- `uv run ruff check .`, `uv run ruff format --check .` y `uv run pytest` pasan.

## Fuera de alcance

- Borrar adjuntos individuales o borrar permanentemente mensajes.
- Descargar imágenes remotas referenciadas por HTML.
- Deduplicar binarios repetidos entre mensajes.
- Restauración automática del `.eml` a Gmail.
- UI web, scheduler, dashboard o despliegue al VPS.
- Barrido sin query en la primera entrega; queda como iteración futura.

## Referencias oficiales verificadas

- [Listar mensajes y paginar](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list)
- [Obtener mensajes, incluido formato raw](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get)
- [Cuotas y backoff de Gmail API](https://developers.google.com/workspace/gmail/api/reference/quota)
- [Mover un mensaje a papelera](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/trash)
