#!/usr/bin/env bash
# Stock Trading Agent — restore the database from a backup copy.
#
# Usage: deploy/restore.sh <snapshot> [--yes]
#
#   <snapshot>   a backup file name (stock-agent.db.2026-10-28), an
#                absolute path, or "offsite" for the latest restic
#                snapshot on the offsite storage
#   --yes        skip the confirmation prompt
#
# The restore stops the app (with the PR 25 maintenance flag), checks
# PRAGMA integrity_check on the copy, puts it in place with mode 0600
# and starts the app again. Positions keep their stops at Alpaca while
# the app is down.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER="${SA_DOCKER:-docker}"
COMPOSE=(env COMPOSE_PROJECT_NAME=stock-agent "$DOCKER" compose -f docker-compose.yml)

log() { printf '%s %s\n' "$(TZ=Europe/Amsterdam date '+%H:%M:%S')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }
run() { if [[ "${SA_NO_ACT:-}" == "1" ]]; then echo "+ $*"; else "$@"; fi }

usage() { sed -n '2,16p' "$0"; exit "${1:-0}"; }

maintenance_on()  { run "${COMPOSE[@]}" exec -T caddy sh -c 'touch /flags/updating' 2>/dev/null || true; }
maintenance_off() { run "${COMPOSE[@]}" exec -T caddy sh -c 'rm -f /flags/updating' 2>/dev/null || true; }

SNAPSHOT="${1:-}"; [[ "$SNAPSHOT" == "--help" || "$SNAPSHOT" == "-h" ]] && usage 0
[[ -z "$SNAPSHOT" ]] && usage 1
shift
YES=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes) YES=1 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done

# Resolve the snapshot to a file inside the data volume.
# Whitelist the name (hard rule 19): only the dated backup shape or an
# absolute path is accepted — never a path built from request input.
if [[ "$SNAPSHOT" == "offsite" ]]; then
    log "restoring the latest offsite snapshot into a temp folder"
    run "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint python \
        -e SA_OFFSITE_RESTORE=1 app -m app.ops.offsite_restore \
        || die "offsite restore failed — see the app log"
    SOURCE="/data/restore/latest/stock-agent.db"
elif [[ "$SNAPSHOT" =~ ^stock-agent\.db\.[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    SOURCE="/data/backups/$SNAPSHOT"
elif [[ "$SNAPSHOT" =~ ^/data/[a-zA-Z0-9._/-]+$ ]]; then
    SOURCE="$SNAPSHOT"
else
    die "snapshot must be a dated backup name, a /data path or 'offsite' (got '$SNAPSHOT')"
fi

# One-off container: verify the copy exists and passes integrity_check.
check=$(cat <<'PY'
import sqlite3, sys
src = sys.argv[1]
try:
    with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as conn:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
except sqlite3.Error as exc:
    print(f"integrity_check failed: {exc}")
    raise SystemExit(1)
result = "; ".join(str(r[0]) for r in rows)
print(result)
raise SystemExit(0 if result == "ok" else 1)
PY
)

if (( YES == 0 )); then
    read -r -p "Restore the database from $SOURCE? This replaces the current database. [y/N] " answer
    [[ "$answer" =~ ^[yY] ]] || die "aborted"
fi

maintenance_on
log "integrity check on $SOURCE"
run "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint python app -c "$check" "$SOURCE" \
    || { maintenance_off; die "integrity_check failed — not restoring"; }

log "stopping app"
run "${COMPOSE[@]}" stop app

log "restoring $SOURCE"
restore_cmd=$(cat <<'SH'
set -eu
cp "$1" /data/stock-agent.db.new
chmod 0600 /data/stock-agent.db.new
mv -f /data/stock-agent.db.new /data/stock-agent.db
rm -f /data/stock-agent.db-wal /data/stock-agent.db-shm
SH
)
run "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh app -c "$restore_cmd" -- "$SOURCE" \
    || { maintenance_off; die "copy failed — the previous database is untouched (stock-agent.db.new)"; }

log "starting app"
run "${COMPOSE[@]}" up -d app
maintenance_off
log "restored $SOURCE — the app is starting; check the banners"
