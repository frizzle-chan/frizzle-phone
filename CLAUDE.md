# frizzle-phone

Discord bot using discord.py with aiosqlite/SQLite for persistence.

## Project Structure

- `src/frizzle_phone/` - main package source
- `tests/` - test files (`test_*.py` and `*_unit_test.py` patterns)
- `src/frizzle_phone/migrations/` - SQL migration files (SQLite-compatible)
- `src/frizzle_phone/__main__.py` - entrypoint (`frizzle-phone` console script / `python -m frizzle_phone`)

## Development

Package manager: **uv** (not pip). Always use `uv run` to execute tools.

### Devcontainer

The project uses a devcontainer. The justfile auto-detects whether you're inside the devcontainer or on the host:

- **Inside devcontainer**: commands run directly
- **On the host**: commands are wrapped with `devcontainer exec --workspace-folder .`

To start the devcontainer: `just up` (or `devcontainer up --workspace-folder .`)

Tests use in-memory SQLite and don't require any external database.

### Running the Server

Always use `./dev` to launch the server (Docker-based, uses `--network host`).

### Common Commands (via justfile)

- `just` - run all checks (lint, format, types, vulture)
- `just test` - run tests
- `just testq` - run tests quick (`-qx --tb=line`)
- `just coverage` - run tests with HTML coverage report
- `just lint` - ruff check
- `just format` - ruff format
- `just types` - ty type check
- `just vulture` - dead code check
- `just resetdb` - delete local SQLite database
- `just up` - start the devcontainer

### CI Checks (all must pass)

1. `uv run ruff check .` - lint
2. `uv run ruff format --check .` - format check
3. `uv run ty check` - type check
4. `uv run vulture` - dead code detection
5. `uv run pytest` - tests with coverage
6. Packaging test - builds wheel, installs in fresh venv, verifies imports/resources/console script
7. Docker smoke test - builds production image, starts container, runs web + SIP checks
8. CodeQL security scanning — `github-advanced-security[bot]` review comments block merge and must be resolved

### Logs

Application logs are written to `frizzle-phone.log` in the project root.

### Pre-commit Hooks (lefthook)

Lefthook runs ruff, ty, vulture, and pytest on pre-commit. Direct commits to `master` are blocked by a branch guard.

## Documentation

- `DESIGN.md` documents the architecture, call flow, and audio pipeline — keep it up to date when changing components, call flow, or audio bridge logic
- When adding new technical concepts to `DESIGN.md`, add a footnote reference (`[^key]`) on the first occurrence and a footnote definition at the bottom (keep definitions sorted alphabetically by key)

## Testing Philosophy

- **Strongly prefer E2E/integration tests over mocks.** Write code that is testable through real interfaces (protocols, in-memory DBs, real UDP sockets, etc.) rather than patching internals.
- **Mocking is a last resort** — only mock when the real dependency is truly unavailable in tests (e.g. Discord gateway, external APIs). If you can use a test double (fake/stub) that implements a protocol, prefer that over `unittest.mock`.
- **Don't add a unit test if the functionality is already covered by an integration test.** Redundant tests are maintenance burden. Check existing E2E tests before writing new ones.
- **Design for testability** — use dependency injection, protocols, and thin wrappers around external services so the real logic can be exercised without mocks.
- **Review plans for SOLID principles** — before implementing, check that the design uses dependency inversion (depend on protocols, not concretions), single responsibility, and interface segregation so that components are inherently testable without mocks.

## Conventions

- PR target branch: `main`
- Repo does not allow merge commits (use squash merge)
- GitHub Actions pins dependencies by SHA with version comments
- SIP code (`src/frizzle_phone/sip/`) is annotated with RFC section references — when modifying SIP logic, cite the relevant RFC section (e.g. `# RFC 3261 §17.2.1: ...`). Use the `/rfc-sip-lookup` skill to find the correct sections.

### PR Labels
When creating PRs with `gh pr create`, apply appropriate labels with `--label`:
- `breaking-change` — requires end-user action beyond updating the app version (e.g. config changes, manual migration, env var renames, Python version upgrades)
- `bug` — bug fixes
- `enhancement` — new user-facing features or improvements (not meta/infra changes like CI, tooling, or repo config)
- `documentation` — docs-only changes
- `ci` — CI/CD, Docker, devcontainer changes
- `sip` — SIP/RTP/SDP protocol changes
- `audio` — audio bridge, synthesis changes
- `database` — migrations, database.py changes

The `actions/labeler` workflow also auto-labels based on file paths and branch names, but explicit labels on PR creation are preferred for accuracy (especially `bug` vs `enhancement`).

## Database

- SQLite DB path: configurable via `DATABASE_PATH` env var (default: `frizzle-phone.db` in working dir)
- Migration runner (`database.py`): uses explicit `BEGIN`/`COMMIT` transactions — do NOT use `executescript()` (it issues an implicit COMMIT, breaking atomicity)

## Audio Bridge Diagnostics

`BridgeStats` emits periodic `"bridge stats"` lines to `frizzle-phone.log` every ~5s during active calls. Grep for `bridge stats` or `bridge d2p` / `bridge p2d` to find them.

| Symptom in logs | Likely cause |
|---|---|
| `d2p_dropped > 0` | RTP send loop falling behind |
| `rtp_silence_sent` high | Discord not delivering audio frames |
| `rtp_max_sleep_overshoot > 5ms` | Event loop congestion |
| `p2d_queue_overflow > 0` | Discord `read()` not keeping up |
| `p2d_silence_reads` high, `p2d_frames_in` normal | Phone audio arriving in bursts (jitter) |
| `p2d_silence_reads` high, `p2d_frames_in` low | Phone not sending RTP |
