# Opciones de integración de email para nuevos clientes

> Decisión actualizada para el escenario ganador: inbox personal en Gmail, usado solo por un único operador, con requisito de coste cero.

## Sistema actual (Pacto Mundial - Microsoft Graph API)

El bot actual usa **Microsoft Graph API** con OAuth2 `client_credentials`:

1. `bot.py` hace polling cada ~120s por buzón
2. `graph_client.py` lista emails no leídos y ejecuta reply / forward / move / tag / drafts
3. `actions.py` decide la acción según la clasificación IA y la config YAML

**Flujo actual:** poll -> pre-filter -> clasificar -> ejecutar acción -> etiquetar / mover

---

## Contexto del nuevo cliente

- Dominio y DNS en **Cloudflare**
- Recepción del dominio custom vía **Cloudflare Email Routing**
- Email operativo en **Gmail gratuito**
- Alias / `Send as` del dominio custom ya configurado en Gmail
- **Solo lo usará una persona**
- **Requisito: coste cero**

Este detalle cambia la decisión técnica: al ser un caso **personal** y no una app pública para terceros, la mejor opción no es la arquitectura más desacoplada, sino la más simple y estable.

---

## Opción ganadora

### Solo Gmail API (sin Email Worker, sin Supabase)

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

### Por qué gana esta opción

- **Gmail es la fuente de verdad**: el email entra y vive en el mismo sistema donde luego se responde.
- **Coste cero real**: no añade Workers, DB ni infraestructura extra.
- **Menos puntos de fallo**: sin colas manuales, sin sincronización Gmail <-> Supabase, sin Worker JS.
- **Encaja con uso personal**: no hace falta optimizar para multiusuario ni para un producto público.
- **Evita sobreingeniería**: para un único inbox controlado por ti, la resiliencia adicional de una segunda copia estructurada no compensa la complejidad extra.

### Decisión práctica

Para este escenario, **no** se recomienda:

- Cloudflare Email Worker como capa principal de ingesta
- Supabase como cola o fuente principal de polling
- Resend inbound
- IMAP + SMTP

La recomendación es:

1. Cloudflare reenvía al inbox Gmail
2. El bot consulta Gmail API
3. Gmail API se usa también para reply / draft / labels

---

## OAuth2 y tokens

### Configuración recomendada

- Tipo de app: **External**
- Estado del proyecto OAuth: **In production**
- Usuario autorizado real: **tu propia cuenta**
- Cliente OAuth: **Desktop app** (el flujo más simple para este caso)

### Lo importante del refresh token

#### `External + Testing`

- El refresh token puede caducar a los **7 días**
- **No usar este modo en producción**, aunque seas tú el único usuario

#### `External + In production`

- El refresh token **no requiere renovación semanal**
- Puede durar indefinidamente mientras no se invalide por causas normales

### Cuándo puede invalidarse un refresh token

- Si revocas manualmente el acceso a la app
- Si el token no se usa durante **6 meses**
- Si cambias la contraseña de la cuenta y el token tiene scopes de Gmail
- Si generas demasiados refresh tokens para el mismo cliente / usuario

### Consecuencia práctica

Para un uso personal bien configurado:

- haces el consentimiento **una sola vez**
- guardas el refresh token
- el bot lo reutiliza
- **no** tienes que renovar el token cada semana

---

## Verificación, auditoría y "app no verificada"

Este punto era la principal duda y cambia bastante la recomendación.

### Qué aplica en este caso

Como la app es de **uso personal** y la vas a usar tú mismo:

- **no necesitas verificación OAuth obligatoria**
- **no necesitas auditoría externa**
- sí aparecerá el warning de **"unverified app"** en el consentimiento inicial
- puedes continuar manualmente y autorizarla

### Cuándo sí cambiaría esto

Tendrías que entrar en verificación seria si:

- conviertes esto en una app para terceros
- superas el límite de uso personal
- necesitas quitar el warning de app no verificada
- Google te exige revisión por convertirlo en integración pública

### Conclusión operativa

Para el escenario actual, el warning de app no verificada es **aceptable** y no bloquea la solución.

---

## Scopes recomendados

### Scope mínimo recomendado: `gmail.modify`

Usar solo:

```text
https://www.googleapis.com/auth/gmail.modify
```

### Por qué este scope

Con `gmail.modify` puedes cubrir todo lo necesario para este bot:

- listar mensajes
- leer subject / body / metadata
- quitar `UNREAD`
- aplicar y quitar labels
- crear labels si faltan
- enviar respuestas
- crear borradores

### Verificado contra métodos concretos de Gmail API

Google documenta que `gmail.modify` es suficiente para:

- `users.labels.create`
- `users.messages.send`
- `users.drafts.create`

Es decir: aunque esos métodos también acepten otros scopes como `gmail.labels`, `gmail.send` o
`gmail.compose`, **no necesitas pedirlos** si `gmail.modify` ya cubre el caso completo.

### Qué no pedir por defecto

No pediría inicialmente:

- `gmail.send`
- `gmail.labels`
- `mail.google.com`

No porque sean imposibles, sino porque **no hacen falta** si `gmail.modify` ya cubre el caso.

### Matiz importante

`gmail.modify` sigue siendo un scope potente y Google lo trata como scope sensible / restringido en el flujo OAuth. Eso significa:

- warning de app no verificada
- límite de app personal / menos de 100 usuarios si no verificas

Pero para tu caso eso es suficiente.

---

## Corrección de la lógica anterior

La propuesta anterior priorizaba:

```text
Email Worker + Supabase + Gmail API
```

Eso **no** es la mejor opción para este caso.

### Problemas de esa lógica

#### 1. Duplica la fuente de verdad sin necesidad

Si el bot poll-ea Supabase pero responde en Gmail, necesitas correlacionar de forma fiable:

- el email capturado por el Worker
- la copia real del email que existe en Gmail
- el `threadId` correcto para responder en hilo

Eso introduce complejidad innecesaria.

#### 2. Sobrestima el riesgo de "perder emails si cae el token"

Con Cloudflare Email Routing -> Gmail:

- si el token falla, el bot deja de automatizar
- pero el email **sigue estando en Gmail**
- no se pierde el mensaje; queda pendiente de procesar

Es un problema operativo, no una pérdida de datos.

#### 3. Añade más piezas de las que aporta

Para un inbox personal:

- Worker JS
- parseo MIME propio
- persistencia extra
- polling adicional

no mejoran suficiente el sistema como para justificar el coste mental y de mantenimiento.

#### 4. Mezcla bien entrada y salida, pero mal el estado

La entrada estaría en Cloudflare / Supabase, pero el estado real del buzón seguiría estando en Gmail:

- leído / no leído
- labels
- drafts
- threading
- enviados

Eso vuelve a Gmail el sistema real de trabajo, así que tiene más sentido leer también desde Gmail.

---

## Impacto real en el código

La idea original de "crear `gmail_client.py` y reutilizar casi todo intacto" era optimista.

### Qué sí se puede reutilizar bien

- `classifier.py`
- gran parte de `actions.py`
- YAML de categorías, templates y routing
- `bot.py` como estructura de polling

### Qué hay que adaptar de verdad

#### 1. Abstraer el cliente de correo

Ahora `bot.py` importa `GraphClient` directamente. Lo correcto es introducir una interfaz más neutra, por ejemplo `MailClient`, y luego implementar:

- `GraphClient`
- `GmailClient`

#### 2. Sustituir carpetas por labels

Gmail no tiene carpetas estilo Outlook. Habrá que mapear:

- `move` -> aplicar label y posiblemente archivar
- `tag_and_move` -> aplicar labels equivalentes
- carpetas anidadas -> labels tipo `2026/Pendiente Gestionar`

#### 3. Adaptar la idempotencia

Hoy la idempotencia depende de `categories`. En Gmail debe apoyarse en labels equivalentes:

- `RESPONDIDO IA`
- `REENVIADO IA`
- `BORRADOR RESPUESTA IA`
- `PENDIENTE GESTIONAR`
- `ERROR IA`

#### 4. Reply / forward / drafts

En Gmail:

- reply requiere MIME + `threadId` + headers correctos
- forward no tiene endpoint equivalente directo
- drafts existen, pero con otra API

#### 5. Emails reenviados

La detección actual de reenviados está muy orientada a HTML típico de Outlook. Si el nuevo inbox trabaja en Gmail, hay que revisar esa extracción para no asumir el mismo formato.

### Resumen honesto del refactor

No es una reescritura completa, pero tampoco es "cambiar un cliente y listo".

Es un **refactor medio**, razonable, con estas dos fases:

1. introducir `MailClient`
2. implementar `GmailClient` y adaptar semántica de labels / drafts / reply

---

## Alternativa robusta futura

### Cloudflare Email Worker + almacenamiento adicional

Esta opción sigue teniendo sentido, pero **no** como primera elección aquí.

Se valoraría más adelante si aparece alguno de estos requisitos:

- varios operadores
- trazabilidad fuerte fuera de Gmail
- pipeline de ingesta desacoplado
- retry de eventos de entrada independiente del inbox
- procesamiento multi-tenant para muchos clientes

Mientras el caso sea:

- un solo usuario
- un solo inbox
- coste cero
- bajo riesgo operativo

la opción simple sigue siendo mejor.

---

## Recomendación final

### Elegir

**Cloudflare Email Routing -> Gmail inbox -> Gmail API**

### Configuración concreta

- Proyecto OAuth como **External**
- Estado **In production**
- Consentimiento hecho una vez por ti
- Guardar un único refresh token
- Scope único inicial: **`gmail.modify`**

### No elegir ahora

- Email Worker + Supabase como arquitectura principal
- Testing mode para el proyecto OAuth
- scopes extra si `gmail.modify` ya cubre el caso

### Motivo final

Es la opción más simple, más barata, más alineada con el uso real y suficientemente segura para una integración privada de un único usuario.
