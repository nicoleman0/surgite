# API stability policy

This document is the contract for the surgite HTTP API. It says what
we promise not to break, what we reserve the right to change, and how
you'll find out when something is going away. It applies from 0.6.0
onward — the release that froze the surface.

The machine-readable companion is [`docs/openapi.json`](openapi.json): a
snapshot-tested OpenAPI document regenerated from the running app on
every change. A CI gate (the `snapshot` job in
[`.github/workflows/ci.yml`](../.github/workflows/ci.yml)) fails any PR
that changes the surface without updating the snapshot, so "did this
break the API?" is a checkable question, not a judgement call.

Versioning is [SemVer](https://semver.org). "Minor" means the middle
number (1.3 → 1.4); "major" means the first (1.x → 2.0).

## What we promise not to break in a minor release

Within a minor series, and across minor bumps (1.3 → 1.4 → 1.5), these
stay stable:

- **Route paths and HTTP methods.** `GET /commits` stays `GET /commits`.
- **Request shapes.** Existing JSON request fields keep their name and
  type. We won't make an optional field required.
- **Response shapes.** Existing top-level JSON keys keep their name and
  type. New fields are additive — a client that ignores unknown fields
  keeps working.
- **Status codes.** A route that returns `200` / `201` / `204` keeps
  returning it for the same outcome. Documented error codes
  (`400`, `401`, `403`, `404`, `409`, `413`, `423`, `429`) keep their
  meaning.
- **The error envelope.** Every error is `{"detail": "<message>"}`. The
  `detail` key and its string type are stable (the human text inside is
  not — don't parse it). This is FastAPI's native shape, declared on the
  surface as the `ErrorResponse` schema.
- **Header semantics.** `Retry-After` on `423` / `429`, the
  `__Host-surgite_session` cookie attributes, and `Authorization: Bearer`
  for API keys keep working as documented in
  [`docs/security.md`](security.md).
- **`operationId`s.** The OpenAPI `operationId` for each route is stable,
  so generated SDK method names don't churn.

Removing or renaming any of the above requires a deprecation cycle (see
below) — we can't just drop a route or rename a field.

## What we reserve the right to change in a minor release

These are explicitly *not* part of the contract. Don't build on them:

- **Default values.** A default page size, a default TTL, or a default
  provider may change.
- **The human text inside `detail`.** Error messages are for people, not
  parsers. Match on status code, not on message text.
- **Internal IDs and ordering.** Slugs, key prefixes, and the ordering of
  list responses beyond what's documented (e.g. "newest first").
- **Log formats.** The structured log stream is operational output, not
  an API.
- **Undocumented `x-` headers** and anything in `/health/deep`'s
  `components` block — that body is informational for operators, and its
  per-component values (`ok` / `degraded` / `no_repos` / `missing_key`)
  may gain new states.
- **Routes tagged `ui`.** The SPA-shell routes (`/login`, `/signup`,
  `/s/{slug}`) serve HTML for the web app, not a programmatic API.

## What we promise for a major release (1.0.0+)

When a breaking change can't be avoided, it lands on a major bump with:

- **A continued-support window** for the previous release line, on the
  terms set out in [`docs/security-support.md`](security-support.md) —
  that document is the single source of truth for what is still patched.
- **A written migration guide**, in the style of
  [`docs/migrations/0.4.0-to-0.5.0.md`](migrations/0.4.0-to-0.5.0.md).

## How deprecation works

When a route or field has to change, it goes through a cycle rather than
disappearing:

1. It's announced in the `### Deprecated` section of
   [`CHANGELOG.md`](../CHANGELOG.md) for the release that deprecates it,
   with the planned removal version.
2. It keeps working for **at least one full minor release** after the one
   that deprecates it. Concretely: something deprecated in 1.4.0 is
   removed no earlier than 1.6.0.
3. The CHANGELOG is the source of truth — read the last few
   `### Deprecated` sections and you know exactly what's going away and
   when.

No deprecations are currently open. The last one to run this cycle was
the `STANDUP_*` → `SURGITE_*` environment-variable rename, completed in
1.0.0; nothing in the source refers to the old names any more.

## What we promise never

- No field removals in a patch release.
- No silent behaviour changes — if it's not in the CHANGELOG, it didn't
  change.
- No SemVer surprises: a breaking change is always a major bump, never
  slipped into a minor or patch.
