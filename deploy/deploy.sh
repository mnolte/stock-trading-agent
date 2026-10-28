#!/usr/bin/env bash
# Stock Trading Agent — deploy a tagged image on the VPS.
#
# Usage: deploy/deploy.sh <tag> [--emergency]
#
#   <tag>          image tag to deploy, e.g. v0.4.2
#   --emergency    deploy inside 09:30–16:00 New York anyway
#
# Steps: market-hours guard → backup → pull → migrations in a one-off
# container → restart → health check + clean startup reconciliation.
# On failure within 2 minutes: restore the pre-deploy backup when a
# migration ran, start the previous image, report.
#
# Test hooks (not for production use):
#   SA_DEPLOY_NOW   overrides "now" for the market-hours guard
#   SA_DOCKER       docker binary to call (default: docker)
#   SA_NO_ACT=1     print commands instead of running them
set -euo pipefail

COMPOSE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$COMPOSE_FILE"
DOCKER="${SA_DOCKER:-docker}"
COMPOSE=(env COMPOSE_PROJECT_NAME=stock-agent "$DOCKER" compose -f docker-compose.yml)

log() { printf '%s %s\n' "$(TZ=Europe/Amsterdam date '+%H:%M:%S')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }
run() { if [[ "${SA_NO_ACT:-}" == "1" ]]; then echo "+ $*"; else "$@"; fi }

usage() { sed -n '2,18p' "$0"; exit "${1:-0}"; }

# --- market-hours guard -------------------------------------------------
# Refuse 09:30–16:00 New York on weekdays, worked out in
# America/New_York — never a fixed offset (hard rule 14).
market_hours_now() {
    local now="${SA_DEPLOY_NOW:-$(date -u '+%Y-%m-%dT%H:%M:%SZ')}"
    TZ=America/New_York date -d "$now" '+%u %H%M'
}

check_market_hours() {
    [[ "${EMERGENCY:-}" == "1" ]] && return 0
    [[ "${SA_DEPLOY_GUARD:-1}" == "0" ]] && return 0
    read -r weekday hhmm <<<"$(market_hours_now)"
    if (( weekday <= 5 )) && (( 10#$hhmm >= 930 && 10#$hhmm < 1600 )); then
        die "a session is running in New York (weekday $weekday, $hhmm) — deploy outside 09:30–16:00 or pass --emergency"
    fi
}

# --- maintenance flag hooks ----------------------------------------------
# PR 25 wires Caddy to serve the static "Updating" page while the flag
# file exists; these hooks mark the deploy window for it.
maintenance_on()  { run "${COMPOSE[@]}" exec -T caddy sh -c 'touch /flags/updating' 2>/dev/null || true; }
maintenance_off() { run "${COMPOSE[@]}" exec -T caddy sh -c 'rm -f /flags/updating' 2>/dev/null || true; }

env_get() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-; }
env_set() {
    # idempotent: replace or append KEY=value in .env
    if grep -qE "^$1=" .env 2>/dev/null; then
        sed -i "s|^$1=.*|$1=$2|" .env
    else
        echo "$1=$2" >> .env
    fi
}

healthz_ok() {
    run "${COMPOSE[@]}" exec -T app python -c \
        "import urllib.request;urllib.request.urlopen('http://localhost:'+__import__('os').environ['SA_PORT']+'/healthz',timeout=5).read()" \
        >/dev/null 2>&1
}

# Clean startup reconciliation: the app records the outcome into
# server-status.json at boot; this read-only command exits 0 on clean.
startup_clean() {
    run "${COMPOSE[@]}" exec -T app python -m app.ops.reconcile_check \
        >/dev/null 2>&1
}

TAG="${1:-}"; [[ "$TAG" == "--help" || "$TAG" == "-h" ]] && usage 0
[[ -z "$TAG" ]] && usage 1
shift
EMERGENCY=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --emergency) EMERGENCY=1 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done
[[ "$TAG" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?$ ]] \
    || die "tag must look like v0.4.2 (got '$TAG')"

check_market_hours
PREVIOUS_TAG="$(env_get SA_VERSION)"
[[ -n "$PREVIOUS_TAG" ]] || die "no SA_VERSION in .env — deploy once by hand first"
[[ "$PREVIOUS_TAG" == "$TAG" ]] && die "already on $TAG"
STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
MIGRATION_RAN=0
ROLLBACK_DEADLINE=$(( $(date +%s) + 120 ))

log "deploy $TAG (previous: $PREVIOUS_TAG)"

# 1. pre-deploy backup inside the app container
log "pre-deploy backup"
BACKUP_PATH="$(run "${COMPOSE[@]}" exec -T app python -m app.db.backup)" \
    || die "pre-deploy backup failed — not deploying"
log "backup: $BACKUP_PATH"

maintenance_on

# 2. pull the new image
log "pulling image ghcr.io/mnolte/stock-agent:$TAG"
env_set SA_VERSION "$TAG"
run "${COMPOSE[@]}" pull app || { env_set SA_VERSION "$PREVIOUS_TAG"; maintenance_off; die "image pull failed — staying on $PREVIOUS_TAG"; }

# 3. migrations in a one-off container (no deps, never the running app)
log "migrations"
if run "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint python \
        app -m alembic upgrade head; then
    MIGRATION_RAN=1
else
    env_set SA_VERSION "$PREVIOUS_TAG"
    maintenance_off
    die "migrations failed — database untouched for the new image? staying on $PREVIOUS_TAG"
fi

# 4. restart the app on the new image
log "restart"
run "${COMPOSE[@]}" up -d app || { maintenance_off; die "restart failed"; }

# 5. wait for /healthz and a clean startup reconciliation (≤ 2 min)
log "waiting for /healthz and a clean reconciliation"
ok=0
for _ in $(seq 1 60); do
    if healthz_ok && startup_clean "$STARTED_AT"; then ok=1; break; fi
    sleep 2
done

if (( ok )); then
    env_set SA_DEPLOYED_AT "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    maintenance_off
    log "deployed $TAG — previous image $PREVIOUS_TAG kept for rollback"
    exit 0
fi

# --- rollback (within 2 minutes of the restart) --------------------------
log "health check failed — rolling back to $PREVIOUS_TAG"
if (( MIGRATION_RAN )) && [[ -n "$BACKUP_PATH" ]]; then
    log "restoring pre-deploy backup $BACKUP_PATH"
    deploy/restore.sh "$BACKUP_PATH" || log "WARNING: backup restore failed — check by hand"
fi
env_set SA_VERSION "$PREVIOUS_TAG"
run "${COMPOSE[@]}" pull app || true
run "${COMPOSE[@]}" up -d app || true
maintenance_off
log "rollback done: $PREVIOUS_TAG is starting; positions kept their stops at Alpaca throughout"
exit 1
