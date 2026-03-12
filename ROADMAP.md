# ROADMAP

## Propósito de este documento

Este archivo debe permitir que otro desarrollador continúe el proyecto sin contexto oral.

Aquí se deja documentado:

- qué existe ya
- qué decisiones están cerradas
- qué falta por construir
- en qué orden hacerlo
- qué riesgos y dudas siguen abiertos
- qué se debe evitar

## Resumen ejecutivo

Este repositorio es una separación limpia del bot original de Pacto Mundial para construir una versión
centrada en **Gmail API**.

La arquitectura decidida es:

```text
Cloudflare Email Routing -> Gmail inbox -> Gmail API -> bot Python
```

El caso de uso objetivo es:

- single-user
- inbox personal en Gmail
- coste cero
- OAuth privado, no app pública
- Gmail como fuente de verdad

## Estado actual del repositorio

### Ya existe

- `README.md`
- `CLAUDE.md`
- `docs/EMAIL_INTEGRATION_OPTIONS.md`
- `docs/MIGRATION_PLAN.md`
- `docs/PACTO_BUSINESS_RULES_REFERENCE.md`
- `docs/PACTO_SOURCE_CONTEXT.md`
- `gmail_inbox_bot/logger.py`
- `gmail_inbox_bot/classifier.py`
- `gmail_inbox_bot/actions.py`
- `gmail_inbox_bot/mail_processing.py`
- `gmail_inbox_bot/prompts/*`
- `tests/*`
- `.env` copiado del repo original
- `.env.example` inicial para el diseño Gmail

### Estado verificado

Comando ejecutado:

```bash
python3 -m pytest -q
```

Resultado actual:

- **107 tests pasando**

### Qué significa esto realmente

La base lógica reutilizable está ya portada, pero **todavía no existe el cliente Gmail real**.

El repo está listo para empezar la implementación Gmail sin depender del paquete `pacto_mundial`, pero
no está aún listo para procesar emails reales.

## Objetivo funcional

Construir un bot que pueda:

1. leer emails reales desde Gmail
2. aplicar pre-filtros
3. clasificar emails con OpenAI
4. responder, reenviar o dejar borradores
5. etiquetar para idempotencia
6. mantener el estado de procesamiento dentro de Gmail

## Decisiones cerradas

### Arquitectura

- usar **Cloudflare Email Routing** para recibir el correo del dominio
- usar **Gmail inbox** como bandeja real de trabajo
- usar **Gmail API** para lectura y escritura
- no introducir una segunda fuente de verdad

### OAuth

- app **External**
- estado **In production**
- uso privado / personal
- aceptar warning de app no verificada

### Scope inicial

- empezar con **`gmail.modify`**

Motivo:

- permite leer mensajes
- modificar labels
- enviar mensajes
- crear drafts
- evita pedir scopes adicionales sin necesidad

### Principio de diseño

Gmail debe ser la **fuente de verdad** del sistema:

- el email entra en Gmail
- el bot lee en Gmail
- el bot etiqueta en Gmail
- el bot responde desde Gmail

## Decisiones todavía abiertas

### 1. Semántica exacta de `move_email`

Gmail no tiene folders como Outlook.

Hay que fijar una convención estable.

Opción recomendada:

- `move_email(label)` = aplicar label de destino + quitar `INBOX`

Esta convención se parece más al comportamiento esperado de “mover fuera de bandeja”.

### 2. Campo interno canónico

Ahora mismo `actions.py` tolera semántica tipo Outlook (`categories`) y semántica Gmail (`labels`).

Hay que decidir si:

- se mantiene compatibilidad dual un tiempo
- o se migra todo el código a `labels`

Recomendación:

- mantener compatibilidad dual a corto plazo
- migrar progresivamente a `labels`

### 3. Soporte de `dynamic_reply`

Existe lógica heredada, pero el bot original tenía reglas de negocio estrictas.

Hay que decidir si en este repo:

- se elimina `dynamic_reply`
- o se mantiene solo en modo borrador

Recomendación:

- si se mantiene, **solo draft**

### 4. Parsing de reenviados

La lógica actual está pensada para HTML típico de Outlook.

Hay que validarla con emails reales que entren en Gmail.

No asumir que los reenviados de Gmail se parecerán a los de Outlook.

## Qué NO queremos hacer

- no usar Supabase como cola principal
- no portar `graph_client.py`
- no arrastrar dependencias Azure
- no rehacer el panel admin antes de tener el núcleo Gmail
- no meter multi-tenant en esta primera versión
- no sobreingenierizar con Workers o storage extra antes de validar el flujo simple

## Contrato técnico que debe construirse

### 1. `MailClient`

Crear un contrato neutral para desacoplar el proveedor de email del router.

Archivo esperado:

- `gmail_inbox_bot/mail_client.py`

Métodos mínimos:

- `get_unread_emails(user_email: str, top: int = 50) -> list[dict]`
- `update_email(user_email: str, message_id: str, is_read: bool = True, add_categories: list[str] | None = None) -> None`
- `move_email(user_email: str, message_id: str, folder_name: str, parent_folder: str | None = None) -> None`
- `delete_email(user_email: str, message_id: str) -> None`
- `reply_to_email(...) -> None`
- `reply_with_attachment(...) -> None`
- `forward_email(...) -> None`

Nota:

- aunque el nombre `add_categories` sea heredado, el cliente Gmail puede mapearlo internamente a labels
- más adelante conviene renombrar esto

### 2. Payload interno normalizado del mensaje

El resto del código espera un objeto parecido a esto:

```python
{
    "id": "msg-id",
    "threadId": "thread-id",
    "subject": "Asunto",
    "from": {"emailAddress": {"name": "Juan", "address": "juan@empresa.com"}},
    "sender": {"emailAddress": {"name": "Juan", "address": "juan@empresa.com"}},
    "body": {"content": "<html>...</html>"},
    "hasAttachments": False,
    "labels": ["INBOX", "UNREAD"],
    "categories": ["INBOX", "UNREAD"],
    "receivedDateTime": "2026-03-12T10:00:00Z",
    "internetMessageId": "<...>",
}
```

Decisión práctica:

- durante la transición, poblar tanto `labels` como `categories` con el mismo contenido
- así `actions.py` y tests siguen funcionando sin refactor grande inicial

### 3. Mapping Gmail -> modelo interno

Hay que implementar una capa de normalización que traduzca desde Gmail API:

- `id`
- `threadId`
- `payload.headers`
- `snippet`
- `labelIds`
- `internalDate`
- partes MIME

hacia el payload interno anterior.

## Orden exacto recomendado de implementación

### Fase 0. Limpieza mínima previa

Antes de tocar el cliente Gmail:

1. crear `mail_client.py`
2. revisar `actions.py` para identificar dependencias reales del cliente
3. decidir convenciones mínimas de payload y labels

### Fase 1. Autenticación Gmail

Crear `gmail_inbox_bot/gmail_client.py` con:

- refresh token
- client id
- client secret
- construcción del cliente Google

Requisitos:

- no depender de OAuth browser flow en runtime
- usar credenciales persistentes ya obtenidas

### Fase 2. Lectura de mensajes

Implementar:

- `list unread`
- `get message detail`
- normalización del payload

Salida esperada:

- `get_unread_emails(...)` devuelve mensajes compatibles con `mail_processing.py` y `actions.py`

### Fase 3. Labels y estado

Implementar:

- marcar leído / no leído
- crear labels si faltan
- aplicar labels
- quitar labels
- mapear idempotencia a labels Gmail

Labels esperados del sistema:

- `RESPONDIDO IA`
- `REENVIADO IA`
- `BORRADOR RESPUESTA IA`
- `BORRADOR REENVIO IA`
- `PENDIENTE ADJUNTO`
- `PENDIENTE GESTIONAR`
- `ERROR IA`

### Fase 4. Operaciones de escritura

Implementar en este orden:

1. `reply_to_email`
2. `forward_email`
3. `reply_with_attachment`
4. drafts

Requisitos críticos:

- mantener hilo correcto
- respetar alias `Send as`
- soportar `force_draft`
- soportar `override_to`

### Fase 5. `bot.py`

Crear un bot mínimo propio con:

- lectura de config
- polling
- pre-filtros
- clasificación
- ejecución
- logging

Primera versión:

- sin FastAPI
- sin dashboard
- sin métricas externas

### Fase 6. Configuración funcional real

Decidir entre dos caminos:

#### Camino A. Bot genérico Gmail

Crear config nueva, limpia y minimalista.

#### Camino B. Migración desde Pacto Mundial

Copiar después:

- `config/`
- `signatures/`
- `files/`

No copiar eso todavía hasta decidir el enfoque.

### Fase 7. Hardening

Cuando el flujo básico funcione:

- retries
- manejo de errores 429 / 5xx
- tests de integración mockeados
- limpieza de `.env`
- quizá healthcheck

## Backlog concreto

### Prioridad máxima

1. Crear `gmail_inbox_bot/mail_client.py`
2. Crear `gmail_inbox_bot/gmail_client.py`
3. Crear tests unitarios de `gmail_client.py`
4. Implementar normalización Gmail -> payload interno
5. Implementar labels e idempotencia
6. Implementar `reply_to_email`
7. Implementar `bot.py`

### Prioridad alta

1. Añadir `config.py`
2. Resolver carga de prompts y rutas relativas
3. Validar `Send as`
4. Confirmar que `gmail.modify` cubre el flujo real con la cuenta usada
5. Limpiar imports y nombres heredados de Outlook

### Prioridad media

1. Decidir destino de `dynamic_reply`
2. Revisar parser de reenviados con muestras reales de Gmail
3. Añadir fixtures de mensajes Gmail reales anonimizados
4. Mejorar `.env.example`

### Prioridad baja

1. FastAPI
2. logs viewer
3. Docker
4. despliegue

## Archivos que previsiblemente habrá que crear

- `gmail_inbox_bot/mail_client.py`
- `gmail_inbox_bot/gmail_client.py`
- `gmail_inbox_bot/bot.py`
- `gmail_inbox_bot/config.py`
- `tests/test_gmail_client.py`
- `tests/test_gmail_normalization.py`

## Archivos que no deben copiarse de momento

- `graph_client.py`
- `metrics.py`
- `admin_logs.py`
- `admin_dashboard.py`
- `app.py`
- `blueprints/*`

## Tratamiento del `.env`

### Situación actual

Se ha copiado el `.env` del repo original para no bloquear:

- OpenAI
- pruebas locales rápidas
- clasificación

### Problema

Ese `.env` arrastra variables que no pertenecen al diseño final del repo Gmail.

### Limpieza pendiente

Eliminar cuando exista el cliente Gmail real:

- `AZURE_*`
- variables de Supabase si no se usan
- cualquier secreto heredado irrelevante

Mantener / añadir:

- `OPENAI_API_KEY`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`
- `GMAIL_ADDRESS`
- `GMAIL_SEND_AS`
- `GMAIL_QUERY`

## Riesgos y puntos delicados

### 1. Reply threading

El mayor riesgo funcional es responder fuera del hilo correcto.

Hay que validar:

- `threadId`
- `Message-ID`
- `In-Reply-To`
- `References`

### 2. Alias de envío

No basta con tener el alias teóricamente configurado.

Hay que comprobar en práctica:

- que Gmail permite enviarlo por API
- que el `From` final es el esperado
- que no rompe deliverability

### 3. MIME y attachments

Los mensajes multipart son la parte más fácil de romper.

No construir esto “a ojo” sin tests.

### 4. Labels como fuente de idempotencia

Si la etiqueta no se aplica correctamente, el bot reprocesará emails.

La idempotencia debe quedar probada con tests.

## Criterio de éxito mínimo

El proyecto puede considerarse en una primera versión usable cuando haga esto:

1. autenticar con Gmail usando refresh token
2. listar emails no leídos reales
3. clasificar con OpenAI
4. aplicar labels de estado
5. crear reply o draft en el hilo correcto
6. evitar reprocesado posterior

## Criterio de éxito deseable

Además del mínimo:

1. soportar reply con attachment
2. soportar forward
3. soportar `override_to`
4. soportar `force_draft`
5. cargar config y prompts desde archivos

## Referencias a revisar antes de continuar

- `README.md`
- `CLAUDE.md`
- `docs/EMAIL_INTEGRATION_OPTIONS.md`
- `docs/MIGRATION_PLAN.md`
- `docs/PACTO_BUSINESS_RULES_REFERENCE.md`
- `gmail_inbox_bot/actions.py`
- `gmail_inbox_bot/mail_processing.py`

## Nota final para el siguiente desarrollador

No rehagas el proyecto desde cero.

La estrategia correcta es:

1. mantener el núcleo ya portado
2. introducir `MailClient`
3. implementar `GmailClient`
4. adaptar solo lo necesario

El mayor valor del repositorio ahora mismo es que ya conserva:

- la lógica de clasificación
- el router de acciones
- el procesamiento previo de emails
- una suite de tests verde

La prioridad no es añadir más capas, sino conectar esa base a Gmail de forma limpia.
