#!/usr/bin/env bash
# Verify frizzle-phone installs cleanly from a wheel and all imports work.
# Runs inside a Docker container with no source tree access.
set -euo pipefail

echo "=== Building wheel ==="
pip install --quiet build
cp -r /src /tmp/build-src
cd /tmp/build-src
python -m build --wheel --outdir /tmp/wheels --quiet
echo "  Built: $(ls /tmp/wheels/*.whl)"

echo ""
echo "=== Installing wheel into fresh venv ==="
python -m venv /tmp/test-venv
/tmp/test-venv/bin/pip install --quiet /tmp/wheels/*.whl

# Run all checks from outside the source tree
cd /tmp

echo ""
echo "=== Verifying imports ==="
/tmp/test-venv/bin/python -c "
import frizzle_phone
from frizzle_phone.bot import create_bot
from frizzle_phone.sip.server import get_server_ip
from frizzle_phone.rtp.pcmu import pcm_to_ulaw
from frizzle_phone.web import create_app
from frizzle_phone.database import run_migrations, _MIGRATIONS_DIR
from frizzle_phone.synth import generate_beeps_pcm
print('  All imports OK')
"

echo ""
echo "=== Verifying migrations are packaged ==="
/tmp/test-venv/bin/python -c "
from frizzle_phone.database import _MIGRATIONS_DIR
assert _MIGRATIONS_DIR.exists(), f'migrations dir not found: {_MIGRATIONS_DIR}'
sql_files = list(_MIGRATIONS_DIR.glob('*.sql'))
assert len(sql_files) > 0, 'No SQL migration files found'
print(f'  {len(sql_files)} migration file(s): {[f.name for f in sql_files]}')
"

echo ""
echo "=== Verifying templates are packaged ==="
/tmp/test-venv/bin/python -c "
import importlib.resources as resources
templates = resources.files('frizzle_phone') / 'templates' / 'extensions.html'
assert templates.is_file(), 'Template not found as resource'
print('  templates/extensions.html OK')
"

echo ""
echo "=== Verifying console script ==="
/tmp/test-venv/bin/frizzle-phone --help > /dev/null
echo "  frizzle-phone --help OK"

echo ""
echo "=== ALL PACKAGING CHECKS PASSED ==="
