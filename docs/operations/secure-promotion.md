# Promoción de Gmail Inbox Bot fuera de GitHub

GitHub construye la imagen y solicita su promoción. La cuenta `deploy-gmail` solo registra
`request SOURCE_SHA sha256:DIGEST`; no tiene sudo, Docker ni acceso a secretos de ejecución.
La respuesta `awaiting_admin_approval` no significa que la imagen esté desplegada.

`VPS_HOST` y `VPS_SSH_HOST_KEY` son variables de Actions; `VPS_SSH_KEY` es el único secret de
solicitud propio (además del `GITHUB_TOKEN` integrado que publica la imagen en GHCR). El workflow
no manda avisos de fallo a Telegram. La monitorización de Root fuera de CI está pendiente; hasta
entonces, revisar el run en GitHub. No se reenvían fallos de Actions por Telegram.

## Aprobar y activar

1. Revisar el cambio y verificar el artefacto exacto desde un entorno de confianza independiente.
   El SHA declarado, los tests y una etiqueta de una imagen generada por una cuenta comprometida
   no prueban por sí solos que el contenido corresponda al código revisado. Reproducir el build
   o validar el artefacto fuera de esa autoridad antes de aprobar su digest.
2. Revisar la configuración de los buzones. Preparar un snapshot root-owned en
   `/opt/gmail-promotion/configs/<config-digest>`; se puede reutilizar el actual si no cambia.
   Los nombres permitidos son archivos YAML simples, sin enlaces ni subdirectorios. El digest
   se calcula con `gmail_promotion.promotion.config_digest`, instalado fuera del checkout.
3. Entrar como `prod-admin` usando la clave y su frase guardadas en Google Password Manager.
4. Ejecutar los dos comandos siguientes con hashes completos. La aprobación permanece en el VPS
   y vincula el digest de imagen, el snapshot de configuración y el hash del entorno actual.

```bash
sudo gmail-promote approve SOURCE_SHA sha256:IMAGE_DIGEST CONFIG_DIGEST
sudo gmail-promote apply APPROVAL_ID
```

El primer comando devuelve `approved_not_deployed` y un ID de aprobación. El segundo acepta
exclusivamente ese ID. La identidad de CI no puede crear o cambiar las decisiones. La cola de
peticiones no es una entrada privilegiada del promotor: el administrador aporta los hashes
explícitamente tras la revisión.

```mermaid
flowchart LR
  CI[GitHub: build] --> Q[Solicitud con hashes]
  Q -. Revisión independiente .-> A[Administrador prod-admin]
  A --> D[Decisión root: imagen/config/entorno]
  D --> P[Promotor fijo]
  P --> B[Gmail Inbox Bot]
```

El perfil fijo utiliza `ghcr.io/jmgb/gmail-inbox-bot@sha256:...`, puerto localhost 8007,
redes existentes, logs existentes y configuración de buzones en solo lectura. No hace git pull
ni ejecuta scripts del checkout, compila imágenes en producción o poda imágenes globalmente.
El proxy de logs existente continúa por separado y la aplicación no monta el socket Docker.

## Configuración y credenciales

El código instalado vive en `/usr/local/libexec/gmail-promotion-v1`; el ejecutable es
`/usr/local/sbin/gmail-promote`. Ambos son root-owned y deben actualizarse por promoción
administrativa de una versión revisada, nunca con auto-pull.

El entorno efectivo se conserva en `/opt/gmail-promotion/runtime-env.json`, accesible solo por
root. Se capturó el entorno de la aplicación activa para preservar literalmente sus 24 valores
de configuración; el `.env` original no se modificó. El perfil Compose escapa `$` y la verificación
real comprobó que el resultado coincide con los valores efectivos, sin mostrarlos en logs.

Una rotación debe actualizar el snapshot de entorno y sus otros consumidores de forma coordinada,
crear un backup cifrado nuevo y emitir otra aprobación. Editar el `.env` heredado del checkout
no actualiza el snapshot activo. Incluir este JSON y `/opt/gmail-promotion/docker/config.json`
en las copias cifradas de la operación: el inventario diario anterior no los incluye todavía.
La primera copia cifrada de ambos archivos se creó y su descifrado se verificó antes de activar.

La credencial de lectura del registro queda en el directorio Docker root-only; no se admiten
credential helpers ni otros registros. Se comprobó una lectura real del manifiesto de la imagen.
El alcance del token en el proveedor no se ha reducido ni demostrado mínimo; queda pendiente
de su inventario/rotación compartidos. CI construye con `GITHUB_TOKEN`; ya no necesita ese PAT.

## Fallos y recuperación

El promotor espera a health de Docker y HTTP. Si falla la activación, intenta restaurar el perfil
anterior y guarda `/opt/gmail-promotion/last-failure.json`, incluyendo `rollback_healthy`.
Un error no implica que el rollback haya tenido éxito: comprobar ese campo y el servicio.
Reaplicar un ID activo comprueba también la salud y no declara éxito para un contenedor unhealthy.

La primera recuperación usa `baseline.json`, generado desde la imagen/configuración existentes;
las siguientes usan el perfil aprobado anterior. Cada imagen aprobada conserva una etiqueta
`approved-<digest>` para que las podas de imágenes sin etiqueta de los despliegues heredados
no borren la recuperación. El promotor no poda imágenes; supervisar el espacio y contemplar
un espejo independiente. Un administrador aún puede forzar el borrado de imágenes etiquetadas.

## Límites de esta fase

Las identidades antiguas que aún conservan sudo/Docker pueden saltarse este mecanismo.
`scripts/vps_deploy.sh` sigue como código heredado y no debe ser el procedimiento operativo
de la migración. El cierre global depende de retirar esos accesos y separar los agentes de G02/G09.

La nueva identidad solo recibe una clave request-only. La clave anterior almacenada en Actions
se sustituye; eso no demuestra que copias antiguas ya extraídas hayan sido revocadas en todos los
hosts ni que no se compartieran con otros repositorios. Mantener abierto el inventario global.

La adopción de este workflow cambia el significado de un push: se construye y solicita, y el
despliegue queda pendiente de aprobación. Los cron/jobs de negocio del bot siguen funcionando.

## Despliegue: notas operativas (VPS, URLs, promotor)

> Movido desde `CLAUDE.md` el 2026-11-10 (allí queda un resumen con enlace aquí).

- **VPS**: `158.69.215.223`. Administración: `prod-admin`; CI: `deploy-gmail`, solo solicitudes.
- **Promotor instalado**: `/usr/local/sbin/gmail-promote`, código root-owned fuera del checkout.
  Configuración, decisiones y secretos de ejecución en `/opt/gmail-promotion`.
- **Tras integrar el workflow nuevo**, cada push construye una imagen en GHCR y envía únicamente
  `request <sha completo> <sha256:digest>` al servidor. La respuesta `awaiting_admin_approval`
  significa que la imagen está pendiente; no reinicia ni despliega el bot. CI no recibe sudo,
  Docker ni credenciales de ejecución. `VPS_SSH_KEY` se reemplaza por una clave de solicitudes;
  `VPS_SSH_HOST_KEY` contiene la clave pública SSH del host, obtenida por administración confiable.
- **Promoción manual**, después de revisar/verificar independientemente el artefacto y su
  configuración: `sudo gmail-promote approve <source-sha> <image-digest> <config-digest>` y
  `sudo gmail-promote apply <approval-id>`. La aprobación queda fuera del control de GitHub;
  vincula imagen, snapshot de configuración y entorno. Un SHA declarado por CI no demuestra
  que una imagen corresponda a ese código. Ver `docs/operations/secure-promotion.md`.
- **Configuración de Actions**: `VPS_HOST` y `VPS_SSH_HOST_KEY` son variables de repositorio;
  `VPS_SSH_KEY` es el único secret propio de la solicitud. El `GITHUB_TOKEN` integrado solo
  publica la imagen en GHCR.
- **Avisos de CI**: los fallos se consultan en Actions. No se envían ni reenvían por Telegram;
  la monitorización de Root fuera de CI está pendiente. Las notificaciones Telegram de la app
  siguen siendo independientes.
- **Health y rollback**: el promotor comprueba Docker y HTTP; ante fallo intenta volver al perfil
  anterior y guarda `last-failure.json`, con el resultado del health de rollback. Revisarlo ante
  cualquier error. Los logs y el proxy Docker existentes se conservan.
- **Credenciales**: la ejecución promovida usa `runtime-env.json` root-only, capturado del entorno
  efectivo y respaldado cifrado. Cambiar el `.env` del checkout no actualiza ese snapshot. Las
  rotaciones requieren actualizar sus consumidores, backup cifrado y reaprobación del entorno.
- **Estado transitorio**: `scripts/vps_deploy.sh` es una ruta heredada. Las identidades antiguas
  que conservan sudo/Docker todavía pueden saltarse la promoción; su migración sigue en G02.
  La nueva identidad de CI no tiene esos permisos. El checkout de
  `/home/ubuntu/services/gmail-inbox-bot` conserva fuentes/histórico y el directorio de logs.
- **Puerto**: `8007` (mapeado a `8000` interno)
- **Admin Dashboard**: https://email.pymechat.com/admin/dashboard
- **Log Viewer**: https://email.pymechat.com/admin/logs
- **Health**: https://email.pymechat.com/health — devuelve **503** si el thread del bot o el del scheduler han muerto, o si el bot lleva tres ciclos de poll sin leer bien todos los buzones (`stalled`, avisa una vez por Telegram); el `HEALTHCHECK` del Dockerfile lo consulta y el crash se avisa por Telegram (Docker no reinicia por `unhealthy`)
- **Password admin**: variable `LOGS_VIEWER_PASSWORD` en `.env`
- **Docker socket**: el contenedor no monta `/var/run/docker.sock`; el visor habla con el sidecar `docker-socket-proxy` por TCP (`DOCKER_API_TCP`), restringido por `-allowfrom` y `-allowGET` a los logs de este único contenedor. Sin la variable, el código vuelve al socket unix
