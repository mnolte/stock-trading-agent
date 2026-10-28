# Deploying Stock Trading Agent on a VPS

How to run Stock Trading Agent on a rented Linux VPS so it trades and
watches positions whether or not your computer is on. Development stays
local; `make run` and `make dev` are unchanged.

This file is a runbook: work top to bottom once for a new server, then
come back per section for deploys, restores and rotation. Every command
runs on the VPS unless it says "locally". No part of this file was run
against a real server from the PR that wrote it — what could not be
verified without one is listed at the bottom.

**Go-live checklist** — all of this before the first real deployment:

- [ ] PR 23 (sign-in) and PR 28 (security hardening) are merged
- [ ] sign-in works from your phone over HTTPS (password + authenticator)
- [ ] one restore test has passed (`deploy/restore.sh` on a backup, or
      the monthly test in Settings → Server)
- [ ] `make broker-guard` printed "guard confirmed" against the paper
      account
- [ ] `ops.heartbeat_url` and an external uptime check are live
- [ ] the offsite backup has succeeded once (`restic snapshots` shows it)

## Choosing a VPS and DNS

- Any small VPS works: 2 GB RAM, 20+ GB disk, Ubuntu 24.04 (the
  provision script targets it). The agent idles between checks; one
  vCPU is enough.
- Pick a provider you can reach over SSH from home. A second provider
  (or the same provider's object storage at a different account/region)
  hosts the offsite backup.
- DNS: an `A` record pointing a name you own (e.g.
  `stock.example.nl`) at the VPS address. Caddy needs it for automatic
  HTTPS; without DNS you get no certificate.

## Provisioning (once)

Locally, copy the repo to the server, then as root:

```bash
rsync -a --exclude .git --exclude node_modules ./ user@<vps>:/tmp/stock-agent/
ssh root@<vps> /tmp/stock-agent/deploy/provision.sh --ssh-key ~/.ssh/id_ed25519.pub
```

The script creates a `stockagent` user (uid 10001, matching the
container), authorizes your key, turns off password and root SSH login,
configures ufw (22/80/443 only), fail2ban, unattended security updates
with reboots only in the Sunday 05:00 Amsterdam window, chrony for time
sync, Docker, and `/srv/stock-agent/.env`.

Move the repo into place and fill in `.env`:

```bash
sudo mv /tmp/stock-agent /srv/stock-agent-src
sudo -u stockagent cp -r /srv/stock-agent-src/. /srv/stock-agent/
sudo -u stockagent editor /srv/stock-agent/.env
# SA_DOMAIN=stock.example.nl
# SA_PUBLIC_URL=https://stock.example.nl
```

`.env` holds no secrets — only the domain, image tag and deploy stamp.

## First deploy

Tag a release locally and push it; CI builds, tests and pushes the
image to the private registry. On the server, log the `stockagent` user
into the registry once:

```bash
docker login ghcr.io
```

Then deploy the tag — the script refuses inside 09:30–16:00 New York:

```bash
cd /srv/stock-agent && deploy/deploy.sh v1.0.0
```

It makes a backup first, pulls, runs migrations in a one-off container,
restarts and checks `/healthz` plus a clean startup reconciliation.
Failure inside two minutes rolls back to the previous image (restoring
the pre-deploy backup when a migration ran). The site briefly serves
nothing while the app restarts; PR 25's maintenance page plugs that gap
via the marked hooks.

## `make create-user`

One user exists on the server (you). Create it once:

```bash
cd /srv/stock-agent && docker compose exec app python -m app.auth.create_user
```

It prints the TOTP secret (for your authenticator app) and eight
one-time backup codes; store the codes somewhere safe. Re-running it
resets everything and signs out all sessions — it asks first.

## Entering keys

Open `https://<domain>` on your phone or laptop, sign in, and enter the
Alpaca paper keys plus the OpenAI key in Settings → API keys. Keys are
stored encrypted in the `secrets` table (`data/secret.key` unlocks them
— keep a copy of that file safe; it is included in every backup).

## `make broker-guard`

Confirm the paper account cannot short or use margin:

```bash
docker compose exec app python -m app.broker_guard
```

It sets `no_shorting` and `max_margin_multiplier="1"` at Alpaca,
re-reads the account and prints `guard confirmed`. The app also
re-checks this at every start and refuses to run entries when
unconfirmed.

## Offsite storage

Create a bucket and access keys at a *different* provider (e.g.
Backblaze B2, a second Hetzner account, any S3-compatible storage with
an https endpoint). Then on the server store them — in the GUI under
Settings → Server → "Backup credentials…", or in the API:

- `endpoint`: the https S3 endpoint (no credentials in it)
- `bucket`, `access_key_id`, `secret_access_key`
- `password`: a fresh, strong restic repository password — **write this
  one down too**; losing it loses the offsite backups.

The nightly job runs after the 03:00 local copy and keeps 30 daily and
12 weekly snapshots. Check Settings → Server → "Offsite copy".

## Heartbeat service

Create a dead-man's-switch check at an external service
(`healthchecks.io`, `uptimerobot` heartbeat, `betterstack`, …): it
should alert you when no ping arrives for ~2 hours. Take its
`https://…` URL into Settings → Server → Heartbeat → "Set…" — saving it
asks for your password and authenticator code because changing the
destination of stored monitoring is a sensitive action. The app pings
it after every cycle and position check.

## External uptime check

Point a second, ordinary uptime monitor at `https://<domain>/healthz`.
It returns only the version — no auth needed — so any public monitor
works. This covers Caddy and the network; the heartbeat covers the app
itself.

## Moving your local database

To move trading history from your laptop to the VPS:

1. **After the close** — outside a session — stop the local app with
   Ctrl+C and let it shut down cleanly (the log line says
   `stopped cleanly`).
2. Copy `data/stock-agent.db` **and `data/secret.key`** to the server's
   `/srv/stock-agent` data volume (or a local dir you bind over the
   volume while seeding).
3. Give each copy its own `SA_INSTANCE_ID`: the server keeps
   `sa-vps-1` (Compose sets it), the local copy something else, e.g.
   `sa-local` in its `.env`. Order ids carry it; a second copy trading
   the same account trips the kill switch.
4. **Never start the local copy against the same Alpaca account
   again.** Order a new paper account or keep local runs to
   `MODE=simulated`/a different account.

## Deploy and rollback

```bash
deploy/deploy.sh v1.2.3        # refuses inside 09:30–16:00 New York
deploy/deploy.sh v1.2.3 --emergency   # only when it can't wait
```

Rollback is automatic on a failed health check (previous image +
pre-deploy backup when a migration ran). To roll back by hand:

```bash
# set SA_VERSION=<previous tag> in .env, then:
docker compose pull app && docker compose up -d app
# if a migration ran in between, restore first:
deploy/restore.sh <backup-name>   # e.g. stock-agent.db.2026-11-02
```

## Restore

```bash
deploy/restore.sh stock-agent.db.2026-11-02   # a local backup name
deploy/restore.sh /data/backups/…             # a path inside the volume
deploy/restore.sh offsite                     # latest restic snapshot
```

The script checks `PRAGMA integrity_check` on the copy first, stops the
app, replaces the database with mode 0600 and restarts. After an
offsite restore the file lands under `/data/restore/latest/`; the
script picks it up from there.

## Rotating the backup password

The restic password encrypts every snapshot; you cannot rotate it on an
existing repository. To rotate: create a new bucket (or new restic repo
path), save the new credentials + new password through Settings →
Server → "Backup credentials…" (destination changed, so both are asked
in the same request), then delete the old repository when the new one
has ≥1 successful snapshot. Keep the old password until the old
repository is gone.

## What I could not verify without a real server

- Real Let's Encrypt issuance (Caddyfile compiles; tested locally only
  through Caddy's internal CA on `https://localhost`).
- The automatic HTTP→HTTPS redirect and HSTS against real browsers.
- restic against a real S3-compatible bucket — the wrapper, env and
  validation are unit-tested, the upload itself is not.
- The Sunday 05:00 Amsterdam reboot actually firing (the cron gate
  logic is tested, the reboot is not).
- DNS, UFW and fail2ban on real traffic.
- `docker compose` end-to-end on a host: `deploy/test-compose.sh` runs
  the whole stack (through-Caddy auth, headers, graceful stop) — run it
  in CI or once on the server itself.
