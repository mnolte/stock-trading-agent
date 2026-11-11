# 084 · ops · Supply chain and containers

Kickoff for Devin. Start the session with: "Build docs/kickoffs/084-ops-supply-chain-and-containers.md."

CI, Dockerfile, Compose, the deploy scripts, `docs/DEPLOY.md`, one small backend check and one Settings line. No trading behaviour changes. Spec: `docs/SPEC.md` → Security hardening → Audit of 11 November 2026 → **The image that runs is the image CI built**, **Containers**, **Backups can't be erased by the server alone**, **Development dependencies are audited too**; VPS deployment. Report: `docs/analysis/080-security-audit.md` (S5, S8, S11, S12). Design: `design/screens/Settings.dc.html` → Server (`Settings-server.png`: Image, Delete protection). Issues: #242 (S5), #245 (S8), #248 (S11), #249 (S12).

```text
[084] Supply chain and containers. Goal: the server runs exactly the image
CI built and signed, by digest; base images are pinned; both containers
are locked down; the weekly audit covers development packages; backups
can't be erased with the server's own key.
Start from the latest main.

Read first:
1. AGENTS.md (rule 14: no deploys in session hours) and docs/README.md.
2. docs/SPEC.md → VPS deployment, Security hardening → Audit of
   11 November 2026; docs/DEPLOY.md; docs/CHANGES.md [080].
3. Dockerfile, docker-compose.yml, docker-compose.local.yml,
   deploy/*.sh, .github/workflows/image.yml and ci.yml,
   .github/dependabot.yml, security/run-audits.sh,
   backend/tests/test_compose.py and test_deploy_scripts.py.

Move this kickoff to docs/kickoffs/archive/ with git mv in your first PR.
Two PRs: steps 1–3 (image chain), then 4–7. Put "Closes #242" in the
first and "Closes #245, closes #248, closes #249" in the second.

1. Sign in CI (image.yml, push-image job only):
   - permissions add id-token: write (keyless signing); keep contents:
     read, packages: write;
   - after build-push: cosign sign --yes ghcr.io/…@${digest} with
     sigstore/cosign-installer pinned by commit SHA;
   - write tag and digest to $GITHUB_STEP_SUMMARY.

2. Deploy by digest (deploy/deploy.sh, restore.sh, compose):
   - docker-compose.yml: image: ${SA_IMAGE:?set SA_IMAGE in .env};
     docker-compose.local.yml keeps building locally (image:
     stock-agent:local) so the laptop and CI stack need no registry;
   - deploy.sh <tag>: resolve the digest (docker buildx imagetools
     inspect --format '{{json .Manifest.Digest}}'), then
       cosign verify ghcr.io/mnolte/stock-agent@$DIGEST \
         --certificate-oidc-issuer https://token.actions.githubusercontent.com \
         --certificate-identity-regexp '^https://github.com/mnolte/stock-trading-agent/\.github/workflows/image\.yml@refs/tags/v'
     and only then set SA_IMAGE=…@$DIGEST and SA_VERSION=<tag> in .env.
     A failed verification stops before the maintenance flag and before
     the backup. Keep SA_IMAGE_PREVIOUS for rollback.
   - --unverified is accepted only together with --emergency, prints a
     warning and writes SA_IMAGE_VERIFIED=no; otherwise
     SA_IMAGE_VERIFIED=<UTC time>.
   - provision.sh installs cosign from its release with a pinned version
     and checksum (not curl | sh).
   - Compose passes SA_IMAGE and SA_IMAGE_VERIFIED to the app;
     GET /api/server returns the digest and the verification time;
     Settings → Server shows the Image row as the design (amber
     "not verified" when no).
   - test_deploy_scripts.py: SA_NO_ACT runs show verify before pull and
     backup; a failing fake cosign stops the script with nothing
     changed; rollback restores the previous digest.

3. Pins:
   - Dockerfile: FROM …@sha256:… for node and python (both stages);
     compose: caddy by digest; uv pinned to one version (==);
   - dependabot.yml: package-ecosystem "docker" for "/" (weekly, the
     7-day cooldown), which covers the Dockerfile and compose files.

4. Containers:
   - caddy: read_only: true with tmpfs for /tmp, cap_drop: [ALL],
     cap_add: [NET_BIND_SERVICE], security_opt no-new-privileges;
     pids_limit 128. app: pids_limit 256.
   - runtime image: copy backend without tests/ and eval/ (a
     .dockerignore can't split stages: copy the tree in a builder stage
     and remove them, or COPY the needed folders); /srv owned by root,
     world-readable, not writable by uid 10001. The test image keeps
     tests.
   - .dockerignore: backend/config.yaml, **/secret.key, **/*.db,
     **/*.db-wal, **/*.db-shm, backups.
   - provision.sh: add Docker's origin to
     Unattended-Upgrade::Allowed-Origins.
   - deploy/test-compose.sh and test_compose.py assert the new keys.

5. Audits (security/run-audits.sh, ci.yml):
   - a second mode, `--all`, audits the dev sets too (uv export with
     --extra dev; pnpm audit without --prod) and prints a table; it
     never fails the job;
   - the weekly scheduled run calls it and writes the table to the job
     summary;
   - bump minimatch past GHSA-g3hv-75wg-m249 (pnpm override if eslint's
     range blocks it; say which).

6. Backup delete protection:
   - docs/DEPLOY.md: versioning or object lock on the bucket, retention
     30 days or more, with the commands for the common S3-compatible
     providers; the app's key needs no permission to delete versions.
   - the monthly restore test asks the bucket (GetBucketVersioning, via
     restic's backend is not possible: use one signed S3 request with
     the stored credentials, through the address check of item 085 if
     merged) and stores on / off / unknown;
   - Settings → Server → Backups shows "Delete protection" as the design
     (green on, amber "not confirmed"). It never blocks a backup.

7. Screenshots in docs/screenshots/084/: Settings → Server.

Rules: no deploy or restart of the live VPS as part of this PR; actions
pinned by commit SHA; no secret in a workflow log; tests never reach a
registry (fake cosign and docker through SA_DOCKER / SA_NO_ACT).

Done when: a tag build signs its image; deploy.sh refuses an unsigned
digest in the script tests; compose-test and test-image pass with the
hardened services; the weekly audit prints the dev table; make test and
make security pass.
```

## Why

Audit of 11 November. S5: `deploy.sh` pulls a tag, which can be moved, and nothing ties the image to CI; base images and uv float. S12: Caddy runs without the limits the app has, the image carries tests, and a local build can bake local files in. S11: development packages are never audited. S8: the key that writes backups can erase them.

## Assumptions

- Keyless cosign needs outbound access from the VPS to Sigstore's public services at deploy time; `--emergency --unverified` covers the day that fails.
- The first deploy after this change is done by hand once (set `SA_IMAGE`), as the first deploy was.

## Open questions for Marc

None. Decided without asking: cosign rather than GitHub's attestation CLI (no GitHub token needed on the server).
