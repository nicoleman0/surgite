# Security support policy

This document says which versions get security patches, how fast we
respond to a report, and how fixes are published. It's the operational
companion to [`docs/security.md`](security.md) (the threat model) and
[`docs/api-stability.md`](api-stability.md) (the API contract).

## Supported versions

Security fixes land on the latest release. Specifically:

- **The latest minor** (e.g. 1.4.x) always gets security patches.
- **The previous minor** (e.g. 1.3.x) gets security patches for **6
  months after the next minor ships**. So when 1.5.0 ships, 1.4.x is
  supported for a further 6 months; after that, upgrade to stay covered.
- Older minors are end-of-life — no backports.

The surface is small enough that "upgrade to the latest minor" is almost
always the right answer. The 6-month window exists so an operator who
pinned a version isn't forced into a same-week upgrade to get a fix.

| Version | Status |
|---------|--------|
| 1.4.x | Supported (latest minor) |
| 1.3.x | Supported until 6 months after 1.5.0 |
| ≤ 1.2.x | End-of-life |

This table is updated as part of cutting a release; see
[`RELEASING.md`](../RELEASING.md).

## Reporting a vulnerability

Report privately — **do not open a public issue.** The channel and what
to include are in the repository's [`SECURITY.md`](../SECURITY.md):
email **nhcoleman@proton.me** with the affected component, reproduction
steps, and impact.

Our response commitment:

- **Acknowledge within 2 business days.**
- **Triage (confirm + assign a severity) within 5 business days.**
- **Patch in the next supported release** — or sooner, with an
  out-of-cycle release, if the severity warrants it.

## How fixes are published

We publish security advisories in the Forgejo / GitHub-style advisory
format on the canonical repository
(<https://github.com/nicoleman0/surgite>), alongside the CHANGELOG
entry for the fixing release. We do **not** request CVE assignment for
low-severity issues — the advisory plus the changelog is the record. A
high-severity issue that warrants broader notification gets a CVE/GHSA.

Every advisory links to the fixing release and credits the reporter
(unless they ask to stay anonymous).

## Operational note

A leaked credential is an operator action, not a code fix: rotate the
affected secret. SMTP credentials (`SMTP_PASSWORD`) and the Fernet master
(`SECRETS_ENCRYPTION_KEY` / `.secrets_key`) are independent — rotating one
doesn't touch the other. Audit-log entries from a leak window are
preserved (the audit writer is append-only), so post-incident review has
the trail. See [`docs/self-host.md`](self-host.md) for the rotation
runbook.
