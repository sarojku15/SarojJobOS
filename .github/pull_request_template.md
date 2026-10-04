## Summary

<!-- One or two sentences: what does this PR do? -->

## Why

<!-- What problem does this solve, or what gap does it close? -->

## Changes

<!-- Bullet list of what actually changed -->

## Testing

<!-- How did you verify this? Which test files did you run/add? -->

## Screenshots (if UI)

<!-- Before/after screenshots for any web/*.html change -->

## Security/Privacy considerations

<!-- Does this touch candidate data, API keys, resumes, or the database?
     Confirm no real personal data or credentials are included anywhere
     in this PR. -->

## Breaking changes

<!-- Does this change any existing API response shape, DB schema, or
     documented behavior? If yes, describe the migration path. -->

## Checklist

- [ ] Tests added/updated
- [ ] Tests pass (`for f in scripts/test_*.py; do .venv/bin/python3 "$f"; done` and `scripts/test_frontend_integrity.py`)
- [ ] No credentials committed
- [ ] No personal data committed
- [ ] No production DB committed
- [ ] Documentation updated (if behavior changed)
- [ ] UI screenshots included when relevant
