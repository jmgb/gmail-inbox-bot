# Migration Plan

## Objetivo

Portar la lógica útil de `pacto-mundial-bot` a un proyecto nuevo centrado en Gmail API, sin arrastrar
acoplamientos a Microsoft Graph / Outlook.

## Qué merece la pena portar

### 1. Estructura de polling

El patrón general de `bot.py` sigue siendo válido:

- listar emails no leídos
- aplicar pre-filtros
- clasificar con OpenAI
- ejecutar acción
- etiquetar para idempotencia

### 2. Clasificación y prompts

La capa de clasificación de `classifier.py` se puede reutilizar casi completa.

### 3. Router de acciones

La mayor parte de `actions.py` es reutilizable si se abstrae el cliente de correo.

### 4. YAML / templates / signatures

Las configuraciones por categoría, plantillas y firmas siguen siendo una base útil.

## Qué no debe copiarse tal cual

### 1. `graph_client.py`

Debe sustituirse por un `gmail_client.py`.

### 2. Acoplamiento directo a Graph

Hay que introducir una interfaz más neutra, por ejemplo:

- `MailClient`
- `GmailClient`

y dejar `GraphClient` fuera de este repo nuevo.

### 3. Semántica Outlook

En Gmail hay que adaptar:

- `categories` -> labels
- `move` -> label + archive / inbox state
- reply threading -> MIME + `threadId`
- forward -> reconstrucción explícita

### 4. Parsing de reenviados

La lógica actual estaba pensada para HTML típico de Outlook. Debe revisarse para Gmail.

## Mapeo propuesto

### Cliente actual -> cliente nuevo

- `get_unread_emails` -> `users.messages.list` + `users.messages.get`
- `update_email` -> `users.messages.modify`
- `reply_to_email` -> MIME + `users.messages.send`
- `forward_email` -> MIME + `users.messages.send`
- `reply_with_attachment` -> draft / send con MIME multipart
- `move_email` -> label + `removeLabelIds=["INBOX"]` cuando aplique

### Idempotencia

Mantener labels equivalentes a:

- `RESPONDIDO IA`
- `REENVIADO IA`
- `BORRADOR RESPUESTA IA`
- `BORRADOR REENVIO IA`
- `PENDIENTE ADJUNTO`
- `PENDIENTE GESTIONAR`
- `ERROR IA`

## Orden recomendado de implementación

1. Crear `MailClient` como interfaz del dominio
2. Implementar autenticación Gmail con refresh token
3. Implementar lectura de mensajes y normalización del payload Gmail
4. Implementar labels / unread / archive
5. Implementar drafts y replies
6. Portar `actions.py`
7. Portar `bot.py`
8. Añadir tests de parsing, idempotencia y routing
