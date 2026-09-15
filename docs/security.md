# Security model

This document describes operational security for a self-hosted Surgite deployment. For vulnerability reporting and supported releases, see the root [security policy](../SECURITY.md).

## Trust boundaries

Surgite accepts repository remotes, Git history, user input, and (when enabled) provider credentials. Treat the application host, database, TLS terminator, backup storage, and configured LLM providers as trusted operational dependencies.

Repository ingestion runs Git against registered remotes. Limit registration to users and remotes you trust, and review repository hooks and credentials available to the process.

## Authentication modes

| Mode | Intended use |
| --- | --- |
| `off` | Private, trusted deployments with no application authentication. |
| `single_user` | A single trusted operator. |
| `multi_user` | Separate user accounts, sessions, API keys, invitations, and owner administration. |

Invitations exist only in `multi_user` mode. `POST /admin/invites` answers `409` in `off` and `single_user` mode rather than minting a token that `POST /auth/redeem-invite` — a `multi_user`-only route — would reject with `404`.

`multi_user` is the appropriate mode for shared deployments. It uses password hashing, signed HTTP-only session cookies, CSRF checks on cookie-authenticated writes, scoped API keys, and owner-only administration. Set the owner email in `.env`, then run `./scripts/bootstrap-admin.sh` from the deployment host. The password is read from the terminal and is never accepted as a command-line argument. Startup logs contain only this instruction, not an invitation token or redemption URL.

## Controls

- Provider keys saved through the application are encrypted at rest and audited; raw keys are never returned.
- Summary endpoints have per-user and per-IP rate limits. The limits are process-local and reset when a process restarts, so they are not a substitute for network-level abuse controls.
- HTTP security headers, request-size limits, and structured audit events reduce common exposure, but do not protect a compromised host or database.
- The API validates request shapes and repository paths. It does not make an untrusted repository safe to ingest.

## Operator requirements

- Use HTTPS for browser sessions. Keep `DEBUG` disabled outside local development.
- When a proxy forwards `X-Forwarded-For`, set `TRUSTED_PROXIES` only to its direct source addresses or CIDRs. Leaving it unset is safer than trusting headers from clients.
- Back up the database and the `SECRETS_ENCRYPTION_KEY` or generated `SECRETS_KEY_FILE` separately. Without the key, encrypted provider credentials cannot be recovered.
- Protect environment files, database dumps, Git credentials, logs, and backups as secrets. Rotate credentials after a suspected disclosure.
- Create email-pinned user invitations from `/admin`. Surgite does not send them automatically; share each single-use link privately with its intended recipient.
- Keep the host, container image, dependencies, and database patched; review [upgrade guidance](self-host.md#upgrades) before release-boundary upgrades.

## Known limitations

- A host, database, backup, TLS-terminator, or encryption-key compromise can expose data and credentials available to that component.
- Authentication does not isolate untrusted repositories from the Git subprocess used for ingestion.
- Rate limits are not shared between application processes and do not replace an external traffic-control policy.
- The project does not provide enterprise identity federation or a general-purpose authorization system.
