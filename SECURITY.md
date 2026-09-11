# Security policy

For deployment controls and trust boundaries, see the [security model](docs/security.md).

## Report a vulnerability

Email [nhcoleman@proton.me](mailto:nhcoleman@proton.me) with a description, affected versions, reproduction steps, and impact. Do not open a public issue for a suspected vulnerability.

We aim to acknowledge reports within two business days, complete initial triage within five, and issue a fix in the next supported release. High-severity issues may receive an out-of-cycle release. We publish material fixes in the changelog or an advisory and credit reporters when they agree.

## Supported versions

Security fixes target the latest minor release and the preceding minor release for six months after the newer minor release. The current window is:

| Version | Support |
| --- | --- |
| 1.7.x | Supported |
| 1.6.x | Supported through 2027-03-11 |
| 1.5.x and earlier | End of life |

Upgrade guidance is in [docs/self-host.md](docs/self-host.md) and [the migration guides](docs/migrations/).
