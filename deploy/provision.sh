#!/usr/bin/env bash
# Stock Trading Agent — provision a fresh Ubuntu 24.04 VPS.
# Run as root, once:  provision.sh --ssh-key /path/to/id_ed25519.pub
#
# Idempotent: every step checks before changing anything.
#   --ssh-key FILE   public key to authorize for the deploy user (required on first run)
#   --user NAME      deploy user (default: stockagent, uid 10001 — matches the container user)
set -euo pipefail

log() { printf '%s %s\n' "$(date '+%H:%M:%S')" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }
usage() { sed -n '2,10p' "$0"; exit "${1:-0}"; }

SSH_KEY_FILE=""
DEPLOY_USER="stockagent"
DEPLOY_UID=10001
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ssh-key) SSH_KEY_FILE="${2:?--ssh-key needs a file}"; shift ;;
        --user) DEPLOY_USER="${2:?--user needs a name}"; DEPLOY_UID=10001; shift ;;
        --help|-h) usage 0 ;;
        *) die "unknown argument: $1" ;;
    esac
    shift
done

[[ "$(id -u)" == "0" ]] || die "run as root"

# --- deploy user + SSH keys ----------------------------------------------
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    log "creating user $DEPLOY_USER (uid $DEPLOY_UID)"
    useradd --uid "$DEPLOY_UID" --create-home --shell /bin/bash "$DEPLOY_USER"
    usermod --lock "$DEPLOY_USER"   # key-only sign-in, never a password
fi
if [[ -n "$SSH_KEY_FILE" ]]; then
    [[ -f "$SSH_KEY_FILE" ]] || die "no such key file: $SSH_KEY_FILE"
    install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
    install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$SSH_KEY_FILE" \
        "/home/$DEPLOY_USER/.ssh/authorized_keys"
    log "authorized $(wc -l < "$SSH_KEY_FILE") key(s) for $DEPLOY_USER"
fi
[[ -f "/home/$DEPLOY_USER/.ssh/authorized_keys" ]] \
    || die "no authorized_keys for $DEPLOY_USER — pass --ssh-key"

# --- sshd: keys only, no root login ---------------------------------------
install -d -m 0755 /etc/ssh/sshd_config.d
cat >/etc/ssh/sshd_config.d/60-stock-agent.conf <<'EOF'
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
EOF
if command -v sshd >/dev/null; then sshd -t && systemctl reload ssh; fi
log "sshd: keys only, root login off"

# --- packages --------------------------------------------------------------
export DEBIAN_FRONTEND=noninteractive
need_pkgs=()
for pkg in ufw fail2ban unattended-upgrades chrony ca-certificates curl gnupg; do
    dpkg -s "$pkg" >/dev/null 2>&1 || need_pkgs+=("$pkg")
done
if ((${#need_pkgs[@]})); then
    log "installing ${need_pkgs[*]}"
    apt-get update -qq
    apt-get install -y -qq "${need_pkgs[@]}"
fi

# --- firewall: 22, 80, 443 only -------------------------------------------
ufw --force default deny incoming >/dev/null
ufw --force default allow outgoing >/dev/null
for port in 22 80 443; do ufw allow "$port"/tcp >/dev/null; done
ufw --force enable >/dev/null
log "ufw: 22/80/443 only"

# --- fail2ban on sshd ------------------------------------------------------
cat >/etc/fail2ban/jail.d/stock-agent.conf <<'EOF'
[sshd]
enabled = true
maxretry = 5
findtime = 15m
bantime = 1h
EOF
systemctl enable --now fail2ban
systemctl restart fail2ban
log "fail2ban on sshd (5 tries in 15m → 1h ban)"

# --- automatic security updates, Sunday 05:00 Amsterdam reboot window ------
cat >/etc/apt/apt.conf.d/51-stock-agent-unattended <<'EOF'
Unattended-Upgrade::Allowed-Origins { "${distro_id}:${distro_codename}-security"; };
Unattended-Upgrade::Automatic-Reboot "false";
EOF
# The reboot window is Sunday 05:00 Europe/Amsterdam — the box runs UTC
# and Amsterdam shifts with DST, so a small script checks Amsterdam time
# instead of a fixed UTC offset (hard rule: never a fixed offset).
cat >/usr/local/sbin/sa-reboot-window <<'EOF'
#!/usr/bin/env bash
# Reboot only inside the Sunday 05:00 Amsterdam window and only when an
# update asked for it — never during a US session or the 03:00 backup.
set -euo pipefail
[ -f /var/run/reboot-required ] || exit 0
[ "$(TZ=Europe/Amsterdam date '+%u %H')" = "7 05" ] || exit 0
/sbin/reboot
EOF
chmod 0755 /usr/local/sbin/sa-reboot-window
# Check hourly; the script itself gates on the Amsterdam window.
cat >/etc/cron.d/sa-reboot-window <<'EOF'
7 * * * * root /usr/local/sbin/sa-reboot-window
EOF
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
log "unattended security updates; reboot only Sunday 05:00 Amsterdam when needed"

# --- time sync -------------------------------------------------------------
systemctl enable --now chronyd || systemctl enable --now chrony
log "chrony on"

# --- Docker -----------------------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
    log "installing Docker"
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    # shellcheck source=/dev/null
    . /etc/os-release
    cat >/etc/apt/sources.list.d/docker.list <<EOF
deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable
EOF
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi
usermod -aG docker "$DEPLOY_USER"
log "docker on; $DEPLOY_USER in the docker group"

# --- layout -----------------------------------------------------------------
install -d -m 0700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /srv/stock-agent
if [[ ! -f /srv/stock-agent/.env ]]; then
    install -m 0600 -o "$DEPLOY_USER" -g "$DEPLOY_USER" /dev/stdin /srv/stock-agent/.env <<'EOF'
# Fill in before the first deploy — no secrets in this file.
MODE=paper
SA_PUBLIC_URL=https://stock.example.nl
SA_DOMAIN=stock.example.nl
SA_VERSION=dev
SA_DEPLOYED_AT=
EOF
    log "wrote /srv/stock-agent/.env — edit SA_DOMAIN/SA_PUBLIC_URL"
fi

log "done. Next: copy the repo to /srv/stock-agent, log in to the registry, deploy a tag."
