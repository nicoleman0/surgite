# Self-hosting surgite

surgite is a small, self-hostable standup summary tool. You give it a Postgres
and a handful of env vars and it runs: a CLI for local use, a FastAPI + Postgres
backend that ingests git history on a schedule, and a SvelteKit web UI for repo
management and summary generation. First-party sessions and email + password login
— no third-party tracking, no telemetry, no phone-home.

This page is the end-to-end guide. The deep dives live in
[`docs/security.md`](security.md) (threat model) and
[`docs/migrations/0.4.0-to-0.5.0.md`](migrations/0.4.0-to-0.5.0.md)
(upgrading a 0.4.0 install).

## Hardware sizing

A small team (under 20 users, under 100 repos, daily summaries) runs fine on
**1 vCPU, 512 MB RAM, 2 GB disk** — that's a $4/month VPS. Postgres is the only
real consumer; the API itself is mostly idle. No GPU, no exotic hardware.

Step up to **2 vCPU, 1 GB RAM** for a larger team (50+ users, more repos, AI
summaries running frequently) and you won't need to think about it again. Disk
grows with the `commits` table; budget ~50 KB per commit and you'll be over-
provisioned.

Single shared Postgres is fine. Don't bother with read replicas until you have
hundreds of users actively hitting `/summary?ai=true` at once.

## Quickstart

Five minutes, three commands, one env var:

```bash
git clone https://github.com/nicoleman0/surgite.git
cd surgite
cp .env.example .env
```

Edit `.env`: set `BOOTSTRAP_OWNER_EMAIL=you@example.com`. The first start with no
admin account logs an invite token for that address; you'll redeem it in a
moment. Leave everything else at its default.

```bash
docker compose up -d
docker compose logs -f app   # watch for the invite token
```

When you see the log line `No admin account exists. Bootstrap an admin by
redeeming this invite for you@example.com:  surgite --redeem-invite <token>`,
redeem it from the same checkout:

```bash
uv run surgite --redeem-invite <token> --email you@example.com
```

That's it. The app created the database, ran migrations, generated a Fernet
master key for at-rest encryption, wrote it to the `surgite-data` volume at
`/var/surgite/data/.secrets_key` (chmod 600, owned by the API process — back it
up, or set `SECRETS_ENCRYPTION_KEY` in `.env` and skip the generated key
entirely), minted the admin invite, and on redeem created
your account with a password you set in the prompt. You're logged in; invite
the rest of your team from the web UI (admins land in slice 2; until then the
API endpoints are in `docs/security.md`).

### Running the published image

`docker compose up -d` builds from the checkout. Every release also publishes a
prebuilt image, so you can skip the build:

```bash
echo "APP_IMAGE=ghcr.io/nicoleman0/surgite:latest" >> .env
docker compose pull && docker compose up -d
```

Tags are `latest`, the minor line (`1.3`), and the exact version (`1.3.0`) —
pin the exact one if you'd rather choose your upgrades. Note that the `docker
compose pull` in the upgrade instructions below only does anything with
`APP_IMAGE` set; without it, compose rebuilds from the checkout and you upgrade
with `git pull` instead. amd64 only for now.

## Using your own LLM

Summaries can run against a model you host, with no call leaving your network.
Any server speaking the OpenAI chat API works — vLLM, Ollama, LM Studio,
llama.cpp, or a LiteLLM proxy in front of something else:

```bash
LLM_PROVIDER=local
LOCAL_BASE_URL=http://llm.internal:8000/v1   # API root, not /chat/completions
LOCAL_API_KEY=<token>                        # whatever your server expects
# LOCAL_MODEL=<id>                          # optional -- asked for if omitted
```

`LOCAL_BASE_URL` has no default and must be reachable **from inside the
container** — `localhost` there is the container, not the Docker host. For
Ollama the root is `http://host:11434/v1`; for vLLM, usually
`http://host:8000/v1`.

On a network that must not reach a hosted provider at all, add:

```bash
LLM_LOCAL_ONLY=1
```

That drops anthropic, groq and deepseek from the provider registry when the
app starts. They then can't be selected, can't have per-user keys stored
against them, and aren't contacted by `/health/deep`'s reachability probe.
`GET /providers` returning only `local` is the check that it took effect.

`LOCAL_MODEL` is optional. Left unset, the app asks your server what it
serves (`GET /models`) on the first summary and uses that, caching the answer
for the life of the process. A box serving one model therefore needs no model
config at all — which is the common self-hosted case, and it avoids anyone
having to know that vLLM reports the full `--model` path
(`Qwen/Qwen3.6-32B`) while Ollama reports a tag like `qwen3.6:32b`.

Set `LOCAL_MODEL` explicitly in two cases: your endpoint serves **more than
one** model (a LiteLLM proxy, say), which is ambiguous enough that the app
refuses to guess and asks you to name one; or you want to skip the lookup.
Nothing validates the name, so a typo surfaces as a 404 from your server.

### A private CA

If your internal endpoint uses a certificate from your own CA, point
`SSL_CERT_FILE` at the CA bundle:

```yaml
    environment:
      SSL_CERT_FILE: /etc/ssl/internal/ca-bundle.pem
    volumes:
      - /etc/ssl/internal:/etc/ssl/internal:ro
```

The file has to be mounted into the container, not just present on the host.

**`SSL_CERT_FILE` replaces the trust store, it does not add to it.** httpx
builds its SSL context from that file *instead of* the bundled public CA list,
so while it is set, the hosted providers' certificates no longer verify. That
is harmless alongside `LLM_LOCAL_ONLY=1` — nothing public is contacted anyway.
But if you want the self-hosted model *and* a hosted provider on the same
deployment, `SSL_CERT_FILE` must point at a **combined** bundle: your CA
concatenated with a public root list (`cat internal-ca.pem "$(python -c 'import
certifi;print(certifi.where())')" > ca-bundle.pem`).

One other thing to know if summaries come back wrong: the OpenAI request body
sends no `max_tokens`, so a server that defaults to a short completion will
truncate mid-sentence.

## TLS

`multi_user` mode **refuses to start over plain HTTP** — the session cookie uses
the `__Host-` prefix, which the browser will only accept on a `Secure` cookie,
which requires HTTPS. Pick one:

**Traefik + step-ca (homelab).** The step-ca ACME issuer hands out real
certificates to anything on your tailnet; Traefik terminates TLS and forwards
plain HTTP to the app. Configure the router for the surgite hostname and
the `X-Forwarded-For` header is set correctly out of the box.

Set `TRUSTED_PROXIES` to the proxy's address (or its subnet, e.g.
`172.16.0.0/12` for a docker network) whichever proxy you pick. The rate
limiter ignores `X-Forwarded-For` from untrusted peers, so leaving it unset
behind a proxy buckets every client under the proxy's own IP — one shared
budget for the whole internet.

**Caddy + Let's Encrypt (external).** The shortest Caddyfile that works:

```caddyfile
surgite.example.com {
    reverse_proxy localhost:8000
}
```

Caddy fetches and renews the cert automatically.

**Local dev.** `caddy trust` (Caddy's local CA) or `mkcert` for a browser-
trusted localhost cert. As a last resort, `DEBUG=true` drops the `__Host-` prefix
and `Secure` flag on the cookie so the app runs over plain HTTP — but only in
`off` or `single_user` mode, and never in production.

If you serve this on the public internet over plain HTTP, your session cookies
travel in cleartext. Don't.

## Backup and restore

`scripts/backup.sh` dumps the database to a gzip file in `BACKUP_DIR` (default
`./backups`), rotating the last `BACKUP_KEEP` (default 14) days of dumps.
`scripts/restore.sh <file>` drops the database and loads a dump back in.

```bash
scripts/backup.sh                          # one dump, keep last 14
BACKUP_KEEP=30 scripts/backup.sh           # keep last 30
scripts/restore.sh backups/surgite-…sql.gz # point-in-time restore
```

Both default to running `pg_dump` / `psql` inside the compose `db` container
(`BACKUP_MODE=docker`). Set `BACKUP_MODE=local` to run them against a host-side
Postgres. Wire this into a daily cron on a Proxmox Backup Server (PBS) — the
named volume `pgdata` plus the dated dumps in `BACKUP_DIR` is the whole
recovery story.

Back up the Fernet master key **separately** — it decrypts `provider_keys`, and
the backup script does not include it. If you lose it, the ciphertext in any DB
backup is unreadable. Under compose it lives on the `surgite-data` volume:

```bash
docker compose exec app cat /var/surgite/data/.secrets_key
```

Better still, set `SECRETS_ENCRYPTION_KEY` in `.env` so the key is something you
chose and already store elsewhere, rather than something the app generated.

## Upgrade

### Rescue your master key before upgrading past 1.0.1

Compose deployments up to and including 1.0.1 did not pass
`SECRETS_ENCRYPTION_KEY` into the container, and the app's generated fallback
key was written to `/app/.secrets_key` — inside the image layer, with no volume
behind it. That key was already being destroyed every time the container was
replaced, silently orphaning every provider key stored in the database.

This release moves the fallback onto the `surgite-data` volume. That fixes it
going forward, but the upgrade itself replaces the container, so **do this
before you pull**:

```bash
# 1. Read the key out of the RUNNING container, before it is replaced.
docker compose exec app cat /app/.secrets_key

# 2. Put it in .env so the new container keeps using it.
echo "SECRETS_ENCRYPTION_KEY=<the value from step 1>" >> .env

# 3. Now upgrade.
docker compose pull && docker compose up -d
```

Skip step 1 and the new container generates a fresh key, at which point every
stored provider key is unreadable. Nothing else is lost — repos, users,
sessions, summaries and share links are all in Postgres and unaffected — and
each user can re-enter their provider key from the web UI. But you cannot
recover the old ones, so it is worth the two minutes.

If the container is already gone, that is the situation: have each user
re-enter their provider key, and set `SECRETS_ENCRYPTION_KEY` in `.env` now so
it cannot happen again.

Coming from 0.4.0? Read [`docs/migrations/0.4.0-to-0.5.0.md`](migrations/0.4.0-to-0.5.0.md).
The short version: the migration is one-shot, it backfills every existing row
to one bootstrap owner, and on the first start with the new image the app mints
an admin invite for `BOOTSTRAP_OWNER_EMAIL`. `scripts/upgrade-from-0.4.sh` runs
the whole thing interactively (or non-interactively with
`SURGITE_BOOTSTRAP_PASSWORD` in the environment).

Coming from 0.6.0? Read [`docs/migrations/0.6.0-to-1.0.0.md`](migrations/0.6.0-to-1.0.0.md).
1.0.0 adds organisations to the data model: `alembic upgrade head` gives every
existing user a personal org and backfills `org_id` on every per-user row. No
API change, no manual schema steps — but 1.0.0 also renames the project to
`surgite`, which does touch configuration. That part follows.

### Upgrading from standup-gen (pre-rename)

1.0.0 renames the project to `surgite` (the PyPI name `standup-gen` belongs to
an unrelated project). A one-time, few-minute migration:

1. **Env vars:** rename `STANDUP_*` → `SURGITE_*` in `.env` / compose
   (`SURGITE_USER`, `SURGITE_ROLE`, and the CLI's `SURGITE_API_URL`,
   `SURGITE_API_KEY`, `SURGITE_EMAIL`, `SURGITE_PASSWORD`). The deprecated
   `STANDUP_API_TOKEN` alias is gone.
2. **Database:** the Postgres user/db defaults are now `surgite`; an existing
   volume holds a database named `standup`. Either rename it once
   (`ALTER DATABASE standup RENAME TO surgite; ALTER USER standup RENAME TO
   surgite;` as the postgres admin) or pin `POSTGRES_USER` / `POSTGRES_DB`
   to the old values in `.env`.
3. **Volumes:** if the compose project name changes (a renamed checkout dir,
   or the Komodo stack re-registering as `surgite`), `docker volume rename`
   `…_pgdata` to the new project prefix before the first `up` — or set
   `COMPOSE_PROJECT_NAME` to keep the old prefix. The repo-cache volume
   (`…_standup-repos` → `…_surgite-repos`) is self-healing: it re-clones
   on demand if you'd rather let it re-create.
4. **Sessions:** the cookie renamed, so every browser user logs in again
   once. CLI sessions migrate automatically on the first `surgite
   --registered` call (the old `standup-gen` keyring entry and the old
   `~/.config/standup/session` file both move forward); worst case,
   `surgite --login` again.
5. **Backups:** new dumps are named `surgite-*.sql.gz`; old
   `standup-*.sql.gz` files no longer match the rotation glob — prune them
   manually once.

Upgrading within a minor line (e.g. 0.5.x → 0.5.y) is a normal
`docker compose pull && docker compose up -d` followed by
`uv run alembic upgrade head` (the API runs it on startup, so this is usually
implicit). Migrations are forward-only.

## CLI session storage

`surgite --login` / `--redeem-invite` save a session cookie so later
`surgite --registered <name>` calls reuse it. Where that cookie lives:

- **By default, the OS keyring** — the login keychain on macOS, the Secret
  Service on Linux (GNOME Keyring, KWallet, KeePassXC — whatever you have),
  the Credential Manager on Windows. Nothing to configure.
- **A 0600 file** at `$XDG_CONFIG_HOME/surgite/session` (default
  `~/.config/surgite/session`) when no keyring backend is available — a
  headless server or CI runner with no D-Bus / Secret Service falls back to
  this automatically.
- **Forced file mode** with `surgite --login --keyring-file`, for headless
  boxes where you'd rather not depend on keyring detection, or for scripted
  setups.

Upgrading from 0.5.x: an existing 0600 session file is migrated into the
keyring the first time the CLI reads it (then the file is overwritten and
removed). No action needed. The 0600 file remains fully supported in 0.6.0;
per [`docs/api-stability.md`](api-stability.md) any future change to this
behaviour goes through the deprecation cycle.

## Email configuration

Email is used for self-serve password reset (`POST /auth/password-reset`).
You don't have to configure it:

- **Unconfigured (default).** With `SMTP_HOST` unset, the app uses the
  *logging mailer*: the reset email — including the reset link — is written
  to the structured log stream instead of being sent. A self-hoster who
  hasn't set up mail still gets working password reset; the link lands where
  they already look for operational signals (`docker compose logs`).
- **SMTP.** Set the `SMTP_*` vars to send real mail:

  ```bash
  SMTP_HOST=smtp.example.com
  SMTP_PORT=587                 # 587 for STARTTLS, 465 for implicit TLS
  SMTP_USERNAME=apikey
  SMTP_PASSWORD=...
  SMTP_FROM="surgite <no-reply@example.com>"
  SMTP_TLS=starttls             # starttls | ssl | none
  PUBLIC_URL=https://surgite.example.com   # used to build the reset link
  ```

  A transactional provider (Mailgun, Postmark, SES) is the right choice for
  a real deployment; a personal Gmail app-password works for a tiny team.
  `PUBLIC_URL` defaults to the request's own origin, so a single-host
  deployment behind one hostname needs no extra config.

If your SMTP credentials leak, rotate `SMTP_PASSWORD` — it's independent of
the Fernet master key, so `provider_keys` are unaffected. See
[`docs/security-support.md`](security-support.md) for the broader
incident-response policy.

## Threat model

The full threat model is in [`docs/security.md`](security.md) — what we
defend against, what we explicitly don't, the rate-limit matrix, the cookie
format, the encryption-at-rest story, and the audit log. The short version:
argon2id passwords, `__Host-` cookies with `HttpOnly` + `SameSite=Lax`, CSRF
defence in depth, per-user rate limits on AI summaries, per-user account
lockout on login failures, Fernet-encrypted provider keys, and an admin-only
audit log. Designed to be safe to expose to the public internet behind TLS.

## Operational runbook

**Revoke a user's sessions.** Connect to Postgres and delete their session rows
— the next request with their cookie is rejected and they have to log in again:

```sql
DELETE FROM sessions WHERE user_id = 'the-user-uuid';
```

**Revoke all sessions globally** (suspected cookie theft, after a deploy, etc.):

```sql
DELETE FROM sessions;
```

Everyone logs in again. The lifespan scheduler will recreate the cleanup pass
on the next tick.

**Rotate the Fernet master key.** `scripts/rotate-secrets.sh` re-encrypts every
active `provider_keys` row in place under a new key, atomically. The running
API keeps using the old key until you restart it. Back up the new key file and
delete the old one from wherever you stored it.

```bash
scripts/rotate-secrets.sh                    # generates a new key for you
scripts/rotate-secrets.sh 'my-new-passphrase'  # or use a specific passphrase
```

The script rotates `SECRETS_KEY_FILE` when set, falling back to `.secrets_key`
next to the project root. Under compose the key lives on a volume, so point the
script at it — otherwise it re-encrypts every row under a key the API will never
load:

```bash
SECRETS_KEY_FILE=/var/surgite/data/.secrets_key scripts/rotate-secrets.sh
```

**Read the audit log.** Admin-only, paginated, filterable by `action` (exact
match) and `since` (timestamp, inclusive):

```
GET /admin/audit?action=auth.login.fail&since=2026-06-20T00:00:00
```

`auth.login.fail`, `auth.login.lockout`, `admin.user.create`, `secrets.rotate`
— see `surgite/audit.py` for the full list of action strings.

**Unlock a user** after a lockout (admin only):

```
POST /admin/users/{id}/unlock
```

**Reset a user's password** (admin only; there is no email delivery in 0.5.0):

```
POST /admin/users/{id}/reset-password
```

The response is a one-time token with a 15-minute expiry. Deliver it to the
user out of band; they redeem it at `/password-reset?token=...` in the browser
or `POST /auth/password-reset/confirm` from the CLI. Email-delivered reset is
0.6.0.

## What 0.5.0 is not

Deliberate omissions, all on the 0.6.0+ roadmap:

- **No OIDC / SSO.** The auth backend is designed so an OIDC provider can
  replace the password branch in a future slice.
- **No orgs, teams, or billing.** 0.5.0 is user-scoped.
- **No email-delivered invites.** Invites are admin-mediated
  (`POST /admin/invites`).
- **No email-delivered password reset.** Admin-mediated reset, above.
- **No PWA / mobile UI.** The web UI is desktop-first.

If any of those are showstoppers, stay on 0.4.0 and wait.
