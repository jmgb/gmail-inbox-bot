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
HEALTH_URL=""  # No HTTP health endpoint — bot uses polling, not a web server
SERVICE_NAME="gmail-inbox-bot"
PROJECT_NAME="$(basename "$PROJECT_DIR")"
COMPOSE_IMAGE_DASHED="${PROJECT_NAME}-${SERVICE_NAME}:latest"
COMPOSE_IMAGE_UNDERSCORE="${PROJECT_NAME}_${SERVICE_NAME}:latest"

DEPLOY_IMAGE_REF="${DEPLOY_IMAGE_REF:-}"
DEPLOY_ALLOW_FALLBACK="${DEPLOY_ALLOW_FALLBACK:-true}"

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

deploy_legacy_build() {
  print_header "Deploy legacy (build en VPS)"
  docker compose -f "$COMPOSE_FILE" up -d --build
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
  docker pull "$DEPLOY_IMAGE_REF" || return 1
  docker tag "$DEPLOY_IMAGE_REF" "$COMPOSE_IMAGE_DASHED" || return 1
  docker tag "$DEPLOY_IMAGE_REF" "$COMPOSE_IMAGE_UNDERSCORE" || return 1
  docker compose -f "$COMPOSE_FILE" up -d --no-build || return 1
  wait_for_health || return 1
}

print_header "Iniciando deploy de $CONTAINER_NAME"
cd "$PROJECT_DIR"

if [ -n "$DEPLOY_IMAGE_REF" ]; then
  if deploy_immutable_image; then
    print_header "Deploy inmutable completado"
  else
    if [ "$DEPLOY_ALLOW_FALLBACK" != "true" ]; then
      echo "Deploy inmutable fallido y fallback deshabilitado"
      exit 1
    fi
    print_header "Fallback a deploy legacy"
    deploy_legacy_build
  fi
else
  deploy_legacy_build
fi

print_header "Verificando estado final"
if [ -n "$HEALTH_URL" ]; then
  curl -sf "$HEALTH_URL" > /dev/null && echo "Health OK" || echo "Health no responde; revisar manualmente"
else
  docker ps --filter "name=$CONTAINER_NAME" --format '{{.Status}}' | grep -q 'Up' && echo "Container running" || echo "Container not running"
fi
echo
print_runtime_status
print_header "Deployment completado"
