# frizzle-phone

Discord bot using discord.py with asyncpg/PostgreSQL for persistence.

## Project Structure

- `src/frizzle_phone/` - main package source
- `tests/` - test files (`test_*.py` and `*_unit_test.py` patterns)
- `migrations/` - SQL migration files (checked by squawk)
- `main.py` - entrypoint (used by Dockerfile CMD)

## Development

Package manager: **uv** (not pip). Always use `uv run` to execute tools.

### Common Commands (via justfile)

- `just` - run all checks (lint, format, types, squawk, vulture)
- `just test` - run tests
- `just testq` - run tests quick (`-qx --tb=line`)
- `just coverage` - run tests with HTML coverage report
- `just lint` - ruff check
- `just format` - ruff format
- `just types` - ty type check
- `just vulture` - dead code check
- `just resetdb` - drop and recreate dev database schema
- `just squawk` - lint SQL migrations

### CI Checks (all must pass)

1. `uv run ruff check .` - lint
2. `uv run ruff format --check .` - format check
3. `uv run ty check` - type check
4. `uv run vulture` - dead code detection
5. `uv run pytest` - tests with coverage
6. `uv run squawk migrations/*.sql` - SQL migration lint (skipped if no files)

### Pre-commit Hooks (lefthook)

Lefthook runs ruff, ty, vulture, squawk, and pytest on pre-commit. Direct commits to `master` are blocked by a branch guard.

## Conventions

- PR target branch: `main`
- Repo does not allow merge commits (use squash merge)
- GitHub Actions pins dependencies by SHA with version comments
