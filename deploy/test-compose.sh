#!/usr/bin/env bash
# Stock Trading Agent — the through-Caddy stack test (CI only; needs
# docker). Boots the local stack on https://localhost (Caddy internal
# CA) and checks the PR 24 guarantees end to end:
#
#   * app runs non-root, read-only root, no capabilities
#   * /healthz says only the version
#   * every /api route answers 401 without a session (incl. writes)
#   * sign-in over HTTPS sets __Host-sa_session with Secure, HttpOnly,
#     SameSite=Strict, Path=/
#   * a spoofed X-Forwarded-Proto is ignored (Caddy overwrites it)
#   * the public Host serves the GUI; a foreign Host is not served
#   * every spec header appears exactly once; an app-sent CSP wins
#     (verified against a fake upstream behind the same header block)
#   * docker stop finishes inside the grace period, integrity_check ok
#   * peak container memory is printed for the compose memory limit
#
#   deploy/test-compose.sh          run the checks
#   deploy/test-compose.sh --down   stop and remove the stack
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DOCKER="${SA_DOCKER:-docker}"
# The production compose file refuses to start without SA_PUBLIC_URL /
# SA_DOMAIN — the local override supplies the values, the variables are
# only needed because compose interpolates the base file first.
export SA_PUBLIC_URL="${SA_PUBLIC_URL:-https://localhost}"
export SA_DOMAIN="${SA_DOMAIN:-localhost}"
export SA_VERSION="${SA_VERSION:-test}"
export SA_DEPLOYED_AT="${SA_DEPLOYED_AT:-}"
COMPOSE=(env COMPOSE_PROJECT_NAME=stock-agent "$DOCKER" compose
    -f docker-compose.yml -f docker-compose.local.yml)
BASE=https://localhost:8443
FAILED=0

log()  { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail() { FAILED=1; log "FAIL: $*"; }
ok()   { log "ok: $*"; }
check() { # check <name> <expected-substring> <command...>
    local name="$1" want="$2"; shift 2
    local got; got="$("$@" 2>/dev/null || true)"
    if grep -q "$want" <<<"$got"; then ok "$name"; else fail "$name — got: ${got:0:200}"; fi
}

if [[ "${1:-}" == "--down" ]]; then
    "${COMPOSE[@]}" down -v --remove-orphans
    "$DOCKER" rm -f stock-agent-fakeapp 2>/dev/null || true
    exit 0
fi

log "building and starting the stack"
"${COMPOSE[@]}" up -d --build

cleanup() { "${COMPOSE[@]}" logs --no-color >&2 || true; }
trap cleanup ERR

log "waiting for /healthz through Caddy"
for _ in $(seq 1 90); do
    if curl -fsSk "$BASE/healthz" >/dev/null 2>&1; then break; fi
    sleep 2
done

# Peak-memory sampling runs alongside the whole test.
: > /tmp/sa-mem-samples.txt
(
    while true; do
        "$DOCKER" stats --no-stream --format '{{.MemUsage}}' stock-agent-app 2>/dev/null \
            | awk '{u=$2;v=$1; gsub(/[^0-9.]/,"",v); if(u=="GiB")v=v*1024; print v}' \
            >> /tmp/sa-mem-samples.txt || true
        sleep 2
    done
) & STATS_PID=$!

# --- the container itself ------------------------------------------------
check "app runs as non-root" "stockagent" \
    "${COMPOSE[@]}" exec -T app id -un
check "read-only root filesystem" "true" \
    "${COMPOSE[@]}" exec -T app sh -c 'touch /x 2>/dev/null && echo wrote || echo true'
capeff="$("${COMPOSE[@]}" exec -T app grep CapEff /proc/self/status || true)"
if grep -qE "CapEff:[[:space:]]*0+$" <<<"$capeff"; then
    ok "no capabilities"
else
    fail "capabilities: $capeff"
fi

# --- healthz says only the version ----------------------------------------
body="$(curl -fsSk "$BASE/healthz")"
[[ "$(python3 -c "import json,sys; d=json.loads(sys.argv[1]); print(sorted(d))" "$body")" == "['version']" ]] \
    && ok "/healthz version only" || fail "/healthz leaks more than version: $body"

# --- session guard through Caddy -------------------------------------------
for route in /api/health /api/header /api/positions /api/settings /api/banners; do
    code="$(curl -sk -o /dev/null -w '%{http_code}' "$BASE$route")"
    [[ "$code" == "401" ]] && ok "$route 401" || fail "$route answered $code"
done
# an unauthenticated write is refused too
code="$(curl -sk -o /dev/null -w '%{http_code}' -X POST "$BASE/api/settings" \
    -H 'Content-Type: application/json' -d '{}')"
[[ "$code" == "401" ]] && ok "POST /api/settings 401 without session" \
    || fail "unauth write answered $code"

# --- sign-in through Caddy: __Host- cookie, Secure -------------------------
"${COMPOSE[@]}" exec -T app python - <<'PY'
from app.auth.create_user import create_or_reset_user
from app.config import AppConfig
from app.db.base import get_session_maker
from app.market.clock import RealClock

with get_session_maker()() as session:
    create_or_reset_user(
        session, AppConfig(), RealClock(),
        username="marc", password="compose test password 1!",
    )
PY
SECRET="$("${COMPOSE[@]}" exec -T app python -c "
from app.db.base import get_session_maker
from app.db.models import User
from sqlalchemy import select
from app.secrets_service import _load_or_create_key
from app.config import get_config
from pathlib import Path
with get_session_maker()() as s:
    u = s.execute(select(User)).scalars().first()
f = _load_or_create_key(Path(get_config().env.sa_data_dir) / 'secret.key')
print(f.decrypt(u.totp_secret_encrypted.encode()).decode())
")"
CODE="$("${COMPOSE[@]}" exec -T app python -c "
from app.auth.passwords import totp_code
from datetime import UTC, datetime
print(totp_code('$SECRET', datetime.now(UTC)))")"
pending="$(curl -fsSk -D - -o /dev/null -X POST "$BASE/api/auth/sign-in" \
    -H 'Content-Type: application/json' \
    -d '{"password":"compose test password 1!"}' \
    | grep -io 'sa_pending=[^;]*' | head -1)"
cookie_line="$(curl -fsSk -D - -o /dev/null -X POST "$BASE/api/auth/code" \
    -H 'Content-Type: application/json' -H "Cookie: $pending" \
    -d "{\"code\":\"$CODE\"}" | grep -i '__Host-sa_session' || true)"
[[ -n "$cookie_line" ]] || fail "no __Host-sa_session cookie set"
for attr in "Secure" "HttpOnly" "SameSite=Strict" "Path=/"; do
    grep -qi "$attr" <<<"$cookie_line" \
        && ok "session cookie $attr" || fail "session cookie missing $attr: $cookie_line"
done
session="$(grep -io '__Host-sa_session=[^;]*' <<<"$cookie_line" | head -1)"

# --- X-Forwarded-Proto spoofing is ignored ---------------------------------
# The client claims http; Caddy rewrites XFP with the real scheme
# (https), so the pending-ticket cookie must still be Secure.
pending2_line="$(curl -fsSk -D - -o /dev/null -X POST "$BASE/api/auth/sign-in" \
    -H 'Content-Type: application/json' -H 'X-Forwarded-Proto: http' \
    -d '{"password":"compose test password 1!"}' \
    | grep -i 'sa_pending' || true)"
grep -qi "Secure" <<<"$pending2_line" \
    && ok "spoofed X-Forwarded-Proto ignored (cookie still Secure)" \
    || fail "spoofed XFP changed the cookie: $pending2_line"

# --- GUI loads on the public Host; another Host gets nothing ----------------
check "GUI loads" "Stock Trading Agent" \
    curl -fsSk "$BASE/"
code="$(curl -sk -o /dev/null -w '%{http_code}' --resolve "evil.example:8443:127.0.0.1" "https://evil.example:8443/" || true)"
[[ "$code" != "200" ]] && ok "foreign Host not served ($code)" || fail "evil.example got a 200"

# --- headers: exactly once each --------------------------------------------
hdrs="$(curl -fsSkD - -o /dev/null "$BASE/" | tr -d '\r')"
for h in strict-transport-security permissions-policy content-security-policy \
         x-frame-options x-content-type-options referrer-policy cross-origin-opener-policy; do
    n="$(grep -ci "^$h:" <<<"$hdrs")"
    [[ "$n" == "1" ]] && ok "header $h once" || fail "header $h seen $n times"
done

# --- a CSP sent by the app wins (Caddy's ?-default must not duplicate it) ---
log "fake upstream behind the same header block"
"$DOCKER" run -d --name stock-agent-fakeapp --rm \
    --network stock-agent_internal \
    python:3.12-slim python -c "
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        body=b'ok'
        self.send_response(200)
        self.send_header('Content-Security-Policy', \"default-src 'none'\")
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self,*a): pass
HTTPServer(('0.0.0.0',8000),H).serve_forever()
" >/dev/null
# Same header directives as the app site — extracted from the real
# Caddyfile so the test tracks it — on a second site with the internal
# CA; port 8445 stays inside the internal network, probed from the
# caddy container itself.
"${COMPOSE[@]}" exec -T caddy sh -c '
    cp /etc/caddy/Caddyfile /tmp/Caddyfile
    awk "/header {/,/^[[:space:]]*}/" /etc/caddy/Caddyfile > /tmp/hdr
    {
        echo "localhost:8445 {"
        echo "    tls internal"
        echo "    reverse_proxy stock-agent-fakeapp:8000"
        cat /tmp/hdr
        echo "}"
    } >> /tmp/Caddyfile
    caddy reload --config /tmp/Caddyfile >/dev/null 2>&1
' >/dev/null 2>&1 || true
sleep 3
fake_hdrs="$("${COMPOSE[@]}" exec -T caddy sh -c \
    "wget -q --no-check-certificate -S -O /dev/null https://localhost:8445/ 2>&1" || true)"
n_csp="$(grep -ci "Content-Security-Policy:" <<<"$fake_hdrs")"
app_csp="$(grep -i "Content-Security-Policy:" <<<"$fake_hdrs" | head -1 | cut -d: -f2-)"
if [[ "$n_csp" == "1" ]] && grep -q "default-src .none." <<<"$app_csp"; then
    ok "app-sent CSP wins ($app_csp)"
else
    fail "app CSP: count=$n_csp value=$app_csp"
fi
"$DOCKER" rm -f stock-agent-fakeapp >/dev/null 2>&1 || true
# put the original Caddyfile back
"${COMPOSE[@]}" restart caddy >/dev/null 2>&1 || true

# --- peak memory under a backtest + a live check ----------------------------
log "measuring container memory under load"
# a backtest via the API with the session we have
curl -fsSk -X POST "$BASE/api/backtests" -H "Cookie: $session" \
    -H 'Content-Type: application/json' \
    -H "X-CSRF-Token: $(curl -fsSk -c - -o /dev/null "$BASE/api/auth/status" | grep sa_csrf | awk '{print $NF}')" \
    -d '{"symbols":["AAPL"],"days":5}' >/dev/null 2>&1 || true
sleep 8
peak="$(sort -rn /tmp/sa-mem-samples.txt 2>/dev/null | head -1)"
log "peak app memory sampled: ${peak:-?} MiB (set mem_limit ≥ 130% of peak)"
kill "$STATS_PID" 2>/dev/null || true

# --- graceful stop inside the grace period -----------------------------------
log "docker stop during a cycle must finish gracefully"
"${COMPOSE[@]}" up -d app >/dev/null
start=$(date +%s)
"${COMPOSE[@]}" stop app >/dev/null
elapsed=$(( $(date +%s) - start ))
(( elapsed < 90 )) && ok "stop took ${elapsed}s (<90s)" || fail "stop took ${elapsed}s"
integ="$("${COMPOSE[@]}" run --rm --no-deps -T app python -c \
    "import sqlite3;print(sqlite3.connect('/data/stock-agent.db').execute('PRAGMA integrity_check').fetchone()[0])" 2>/dev/null | tail -1)"
[[ "$integ" == "ok" ]] && ok "integrity_check ok after stop" || fail "integrity_check: $integ"
"${COMPOSE[@]}" up -d app >/dev/null

if (( FAILED )); then log "FAILED"; exit 1; fi
log "all checks passed"
