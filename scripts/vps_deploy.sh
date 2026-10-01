#!/bin/bash
# scripts/vps_deploy.sh — Immutable deploy with GHCR + legacy fallback
# See: sofia-financial-reports/docs/adr/009-build-fuera-del-vps.md

set -euo pipefail

# ── Serialize deploys across ALL projects on this VPS ────────────────────────
LOCK_FILE="/tmp/vps-deploy.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "Another deploy is already running (lock: $LOCK_FILE). Waiting up to 5 minutes..."
  if ! flock -w 300 9; then
    echo "Timed out waiting for deploy lock. Aborting."
    exit 1
  fi
fi

PROJECT_DIR="/home/ubuntu/services/gmail-inbox-bot"
COMPOSE_FILE="docker-compose.production.yml"
CONTAINER_NAME="gmail-inbox-bot"
# /health devuelve 503 si el thread del bot o el del scheduler han muerto (ver app.py).
HEALTH_URL="http://127.0.0.1:8007/health"
SERVICE_NAME="gmail-inbox-bot"
# La imagen que corría antes del deploy, etiquetada para poder volver a ella: sin etiqueta,
# el `docker image prune` del final la borraba y no había a qué volver.
ROLLBACK_TAG="${SERVICE_NAME}:rollback"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
COMPOSE_IMAGE_DASHED="${PROJECT_NAME}-${SERVICE_NAME}:latest"
COMPOSE_IMAGE_UNDERSCORE="${PROJECT_NAME}_${SERVICE_NAME}:latest"

DEPLOY_IMAGE_REF="${DEPLOY_IMAGE_REF:-}"
DEPLOY_ALLOW_FALLBACK="${DEPLOY_ALLOW_FALLBACK:-true}"
# Commit que corría antes de este deploy (lo pasa el workflow antes del git merge). El
# rollback vuelve a él: config/ va montado desde este checkout, así que revertir solo la
# imagen dejaría la config nueva con el código viejo.
# Sin variable (deploy a mano, que hace el git pull antes), se usa el último commit que quedó
# sano, guardado al final de cada deploy correcto.
# Fuera de logs/: ese directorio es de root (lo escribe el contenedor) y el deploy corre
# como ubuntu.
LAST_DEPLOYED_FILE="$PROJECT_DIR/.last_deployed_sha"
DEPLOY_PREVIOUS_SHA="${DEPLOY_PREVIOUS_SHA:-$(cat "$LAST_DEPLOYED_FILE" 2>/dev/null || true)}"

print_header() { echo "==> $1"; }

wait_for_health() {
  print_header "Verificando health check del container"
  attempts=15
  delay=6
  for i in $(seq 1 "$attempts"); do
    health_status="$(docker inspect --format='{{.State.Health.Status}}' "$CONTAINER_NAME" 2>/dev/null | tr -d '[:space:]' || echo "none")"

    if [ "$health_status" = "healthy" ]; then
      echo "Container healthy (Docker healthcheck)"
      return 0
    fi

    # Fallback: if no Docker healthcheck configured, check container is running
    if [ "$health_status" = "none" ] || [ "$health_status" = "" ]; then
      container_running="$(docker inspect --format='{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || echo "false")"
      if [ "$container_running" = "true" ]; then
        if [ -n "$HEALTH_URL" ]; then
          curl -sf --connect-timeout 2 --max-time 5 "$HEALTH_URL" > /dev/null 2>&1 && echo "Container healthy (HTTP check)" && return 0
        else
          echo "Container running (no health endpoint)"
          return 0
        fi
      fi
    fi

    if [ "$i" -eq "$attempts" ]; then
      echo "Health check failed after ${attempts} attempts"
      docker logs "$CONTAINER_NAME" --tail 30 || true
      return 1
    fi
    echo "Retry $i/$attempts (status: $health_status)"
    sleep "$delay"
  done
}

print_runtime_status() {
  print_header "Estado del container"
  docker ps --filter "name=$CONTAINER_NAME" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
  echo
  docker stats "$CONTAINER_NAME" --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"
  echo
}

save_rollback_image() {
  local previous
  previous="$(docker inspect --format='{{.Image}}' "$CONTAINER_NAME" 2>/dev/null || true)"
  if [ -n "$previous" ]; then
    docker tag "$previous" "$ROLLBACK_TAG"
    echo "Imagen anterior guardada como $ROLLBACK_TAG"
  fi
}

rollback() {
  if ! docker image inspect "$ROLLBACK_TAG" > /dev/null 2>&1; then
    echo "No hay imagen anterior a la que volver"
    return 1
  fi
  print_header "Rollback a la imagen anterior ($ROLLBACK_TAG)"
  if [ -n "$DEPLOY_PREVIOUS_SHA" ] && [ "$(git rev-parse HEAD)" != "$DEPLOY_PREVIOUS_SHA" ]; then
    # --keep aborta si hubiera cambios locales que perder; el siguiente deploy vuelve a
    # avanzar con git merge --ff-only.
    git reset --keep "$DEPLOY_PREVIOUS_SHA" || echo "No se pudo volver el checkout a $DEPLOY_PREVIOUS_SHA"
  fi
  docker tag "$ROLLBACK_TAG" "$COMPOSE_IMAGE_DASHED" || return 1
  docker tag "$ROLLBACK_TAG" "$COMPOSE_IMAGE_UNDERSCORE" || return 1
  docker compose -f "$COMPOSE_FILE" up -d --no-build || return 1
  wait_for_health
}

fail_with_rollback() {
  echo "$1"
  rollback || echo "ROLLBACK FALLIDO: el servicio puede estar caído, revisar a mano"
  exit 1
}

deploy_legacy_build() {
  print_header "Deploy legacy (build en VPS)"
  docker compose -f "$COMPOSE_FILE" up -d --build || return 1
  wait_for_health
}

deploy_immutable_image() {
  if [ -z "$DEPLOY_IMAGE_REF" ]; then
    echo "DEPLOY_IMAGE_REF no definido"
    return 1
  fi
  print_header "Deploy inmutable: $DEPLOY_IMAGE_REF"
  if [[ "$DEPLOY_IMAGE_REF" == ghcr.io/* ]] && [ -n "${GHCR_USERNAME:-}" ] && [ -n "${GHCR_TOKEN:-}" ]; then
    echo "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
  fi
  # 2 = la imagen no se pudo descargar (solo ahí tiene sentido construir en el VPS);
  # 1 = la imagen se descargó pero no arranca sana: reconstruir el mismo código no lo arregla.
  docker pull "$DEPLOY_IMAGE_REF" || return 2
  docker tag "$DEPLOY_IMAGE_REF" "$COMPOSE_IMAGE_DASHED" || return 1
  docker tag "$DEPLOY_IMAGE_REF" "$COMPOSE_IMAGE_UNDERSCORE" || return 1
  docker compose -f "$COMPOSE_FILE" up -d --no-build || return 1
  wait_for_health || return 1
}

print_header "Iniciando deploy de $CONTAINER_NAME"
cd "$PROJECT_DIR"

save_rollback_image

if [ -n "$DEPLOY_IMAGE_REF" ]; then
  rc=0
  deploy_immutable_image || rc=$?
  if [ "$rc" -eq 0 ]; then
    print_header "Deploy inmutable completado"
  elif [ "$rc" -eq 2 ] && [ "$DEPLOY_ALLOW_FALLBACK" = "true" ]; then
    print_header "Fallback a deploy legacy (no se pudo descargar la imagen)"
    deploy_legacy_build || fail_with_rollback "Deploy legacy fallido"
  else
    fail_with_rollback "Deploy inmutable fallido (código $rc)"
  fi
else
  deploy_legacy_build || fail_with_rollback "Deploy legacy fallido"
fi

print_header "Verificando estado final"
if curl -sf --max-time 5 "$HEALTH_URL" > /dev/null; then
  echo "Health OK"
  git rev-parse HEAD > "$LAST_DEPLOYED_FILE" || echo "No se pudo guardar el commit desplegado"
else
  fail_with_rollback "Health no responde tras el deploy"
fi
echo
print_runtime_status


# ── Post-deploy cleanup ───────────────────────────────────────────────────────
# Remove old untagged images from ghcr.io (previous deploys leave <none> tags)
# Excepto la imagen de rollback: `rmi -f` por ID borra también su etiqueta.
ROLLBACK_ID="$(docker image inspect --format '{{.Id}}' "$ROLLBACK_TAG" 2>/dev/null || true)"
docker images --no-trunc --filter "reference=ghcr.io/jmgb/*" --format '{{.ID}} {{.Tag}}' \
  | grep '<none>' \
  | awk '{print $1}' \
  | grep -vxF "${ROLLBACK_ID:-none}" \
  | xargs -r docker rmi -f 2>/dev/null || true
docker image prune -f
docker builder prune -f --keep-storage=500mb
echo "Disco: $(df -h / | tail -1 | tr -s ' ' | cut -d' ' -f4) libres"

print_header "Deployment completado"
