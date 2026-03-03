# Run commands inside devcontainer automatically when on host
_run := if path_exists("/.dockerenv") == "true" { "" } else { "devcontainer exec --workspace-folder ." }

default: lint format types vulture test

test:
    {{_run}} uv run pytest

testq:
    {{_run}} uv run pytest -qx --tb=line

# Run tests and generate HTML coverage report in htmlcov/
coverage:
    {{_run}} uv run pytest --cov-report=html

lint:
    {{_run}} uv run ruff check .

format:
    {{_run}} uv run ruff format .

types:
    {{_run}} uv run ty check

vulture:
    {{_run}} uv run vulture

# Start the devcontainer (host only)
up:
    devcontainer up --workspace-folder .

# Stop the devcontainer (host only)
down:
    docker compose -f .devcontainer/docker-compose.yml down

devcontainer:
    gh auth login --with-token < .github-token.txt

# Delete the local SQLite database
resetdb:
    rm -f frizzle-phone.db
