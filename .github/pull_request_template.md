Closes #

## Summary

<!-- The user-visible change, one bullet per behavior. -->

- 

## Upgrade notes

<!-- Migrations, OpenAPI snapshot, config, or operator action. Delete this section if none. -->

## Testing

<!-- Commands run and results. Say what you didn't run. -->

- 

## Checklist

<!-- Tick each item that applies. Delete the ones that don't. -->

- [ ] The checks from [Validate changes](https://github.com/nicoleman0/surgite/blob/main/CONTRIBUTING.md#validate-changes) that apply to this change pass
- [ ] Behavior changes have focused tests
- [ ] Route or endpoint description changed: ran `task openapi-snapshot` and committed the snapshot
- [ ] Schema changed: added an Alembic migration, checked it on a populated dev database, and left applied migrations untouched
- [ ] Operator action needed: described it under Upgrade notes and in `docs/migrations/`
- [ ] Public interface, config value, or deployment procedure changed: updated the docs
- [ ] Provider changed: credentials read at call time, no secrets logged, missing-key and failure paths tested
