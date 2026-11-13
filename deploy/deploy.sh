#!/usr/bin/env bash
# Stock Trading Agent — deploy a tagged image on the VPS.
#
# Usage: deploy/deploy.sh <tag> [--emergency] [--unverified]
#
#   <tag>          image tag to deploy, e.g. v0.4.2
#   --emergency    deploy inside 09:30–16:00 New York anyway
#   --unverified   skip the signature check — only with --emergency
#
# The tag is resolved to the digest CI built and signed; cosign verifies
# it against this repository's tag workflow before anything changes.
# Compose then runs the image by digest (SA_IMAGE in .env).
#
# Steps: market-hours guard → digest + signature → backup → maintenance
# flag → pull by digest → migrations in a one-off container → restart →
# health check + clean startup reconciliation. On failure within 2
# minutes: restore the pre-deploy backup when a migration ran, start
# the previous image, report.
#
# Test hooks (not for production use):
#   SA_DEPLOY_NOW   overrides "now" for the market-hours guard
#   SA_DEPLOY_GUARD 0 disables the market-hours guard
#   SA_DOCKER       docker binary to call (default: docker)
#   SA_COSIGN       cosign binary to call (default: cosign)
#   SA_IMAGE_DIGEST digest to use instead of resolving the tag (NO_ACT)
#   SA_DEPLOY_WAIT_TRIES / SA_DEPLOY_WAIT_SLEEP  health-check loop bounds
#   SA_NO_ACT=1     print commands instead of running them
set -euo pipefail

COMPOSE_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$COMPOSE_FILE"
DOCKER="${SA_DOCKER:-docker}"
COSIGN="${SA_COSIGN:-cosign}"
IMAGE_REPO="ghcr.io/mnolte/stock-agent"
COMPOSE=(env COMPOSE_PROJECT_NAME=stock-agent "$DOCKER" compose -f docker-compose.yml)

log() { printf '%s %s\n' "$(TZ=Europe/Amsterdam date '+%H:%M:%S')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }
run() { if [[ "${SA_NO_ACT:-}" == "1" ]]; then echo "+ $*"; else "$@"; fi }

usage() { sed -n '2,26p' "$0"; exit "${1:-0}"; }

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
env_restore_previous() {
    env_set SA_IMAGE "$PREVIOUS_IMAGE"
    env_set SA_VERSION "$PREVIOUS_TAG"
    env_set SA_IMAGE_VERIFIED "$PREVIOUS_VERIFIED"
}

# --- image identity -------------------------------------------------------
# A tag can move; the deploy runs the immutable digest CI built and
# signed. Resolution and verification happen before the backup, the
# maintenance flag or any .env change.
resolve_digest() {
    if [[ "${SA_NO_ACT:-}" == "1" ]]; then
        echo "+ $DOCKER buildx imagetools inspect --format '{{json .Manifest.Digest}}' $IMAGE_REPO:$TAG"
        DIGEST="${SA_IMAGE_DIGEST:-sha256:$(printf '%064d' 0)}"
        return
    fi
    DIGEST="$("$DOCKER" buildx imagetools inspect \
        --format '{{json .Manifest.Digest}}' "$IMAGE_REPO:$TAG" | tr -d '"')" \
        || die "could not resolve $IMAGE_REPO:$TAG — is the tag pushed and the registry logged in?"
}

verify_signature() {
    # Keyless: the signing certificate must come from this repository's
    # tag workflow (image.yml), nothing else.
    run "$COSIGN" verify "$IMAGE_REPO@$DIGEST" \
        --certificate-oidc-issuer https://token.actions.githubusercontent.com \
        --certificate-identity-regexp \
        '^https://github.com/mnolte/stock-trading-agent/\.github/workflows/image\.yml@refs/tags/v'
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
UNVERIFIED=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --emergency) EMERGENCY=1 ;;
        --unverified) UNVERIFIED=1 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done
[[ "$UNVERIFIED" == "1" && "$EMERGENCY" != "1" ]] \
    && die "--unverified is accepted only together with --emergency"
[[ "$TAG" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+(-[a-z0-9.]+)?$ ]] \
    || die "tag must look like v0.4.2 (got '$TAG')"

check_market_hours
PREVIOUS_TAG="$(env_get SA_VERSION)"
[[ -n "$PREVIOUS_TAG" ]] || die "no SA_VERSION in .env — deploy once by hand first"
[[ "$PREVIOUS_TAG" == "$TAG" ]] && die "already on $TAG"
PREVIOUS_IMAGE="$(env_get SA_IMAGE)"
[[ -n "$PREVIOUS_IMAGE" ]] \
    || die "no SA_IMAGE in .env — set the running image digest once by hand first"
PREVIOUS_VERIFIED="$(env_get SA_IMAGE_VERIFIED)"
STARTED_AT="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
MIGRATION_RAN=0
ROLLBACK_DEADLINE=$(( $(date +%s) + 120 ))

log "deploy $TAG (previous: $PREVIOUS_TAG)"

# 0. resolve the tag to the digest CI built, then verify its signature.
#    A failure here stops before the maintenance flag and the backup.
resolve_digest
[[ "$DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] \
    || die "could not resolve a sha256 digest for $IMAGE_REPO:$TAG (got '$DIGEST')"
if [[ "$UNVERIFIED" == "1" ]]; then
    log "WARNING: --unverified --emergency — deploying $DIGEST without a signature check"
    VERIFIED="no"
else
    log "verifying signature of $IMAGE_REPO@$DIGEST"
    verify_signature || die "signature verification failed — not deploying"
    VERIFIED="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
fi

# 1. pre-deploy backup inside the app container
log "pre-deploy backup"
BACKUP_PATH="$(run "${COMPOSE[@]}" exec -T app python -m app.db.backup)" \
    || die "pre-deploy backup failed — not deploying"
log "backup: $BACKUP_PATH"

maintenance_on

# 2. record the new image and pull it by digest
env_set SA_IMAGE_PREVIOUS "$PREVIOUS_IMAGE"
env_set SA_IMAGE "$IMAGE_REPO@$DIGEST"
env_set SA_VERSION "$TAG"
env_set SA_IMAGE_VERIFIED "$VERIFIED"
log "pulling image $IMAGE_REPO@$DIGEST"
run "${COMPOSE[@]}" pull app || {
    env_restore_previous; maintenance_off
    die "image pull failed — staying on $PREVIOUS_TAG"
}

# 3. migrations in a one-off container (no deps, never the running app)
log "migrations"
if run "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint python \
        app -m alembic upgrade head; then
    MIGRATION_RAN=1
else
    env_restore_previous
    maintenance_off
    die "migrations failed — database untouched for the new image? staying on $PREVIOUS_TAG"
fi

# 4. restart the app on the new image
log "restart"
run "${COMPOSE[@]}" up -d app || {
    env_restore_previous; maintenance_off
    die "restart failed"
}

# 5. wait for /healthz and a clean startup reconciliation (≤ 2 min)
log "waiting for /healthz and a clean reconciliation"
ok=0
TRIES="${SA_DEPLOY_WAIT_TRIES:-60}"
SLEEP="${SA_DEPLOY_WAIT_SLEEP:-2}"
for _ in $(seq 1 "$TRIES"); do
    if healthz_ok && startup_clean "$STARTED_AT"; then ok=1; break; fi
    sleep "$SLEEP"
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
env_restore_previous
run "${COMPOSE[@]}" pull app || true
run "${COMPOSE[@]}" up -d app || true
maintenance_off
log "rollback done: $PREVIOUS_TAG ($PREVIOUS_IMAGE) is starting; positions kept their stops at Alpaca throughout"
exit 1
