default: lint format types squawk vulture

test:
    uv run pytest

testq:
    uv run pytest -qx --tb=line

# Run tests and generate HTML coverage report in htmlcov/
coverage:
    uv run pytest --cov-report=html

lint:
    uv run ruff check .

format:
    uv run ruff format .

types:
    uv run ty check

squawk:
    uv run squawk migrations/*.sql

vulture:
    uv run vulture

devcontainer:
    gh auth login --with-token < .github-token.txt

# Reset dev database (drops and recreates schema, runs migrations)
resetdb:
    psql postgresql://frizzle_phone:frizzle_phone@db:5432/frizzle_phone -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

migratedb:
    echo TODO
