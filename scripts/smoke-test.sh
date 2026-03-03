#!/usr/bin/env bash
set -euo pipefail

WEB_PORT="${WEB_PORT:-18080}"
SIP_PORT="${SIP_PORT:-15060}"
BASE_URL="http://127.0.0.1:${WEB_PORT}"

passed=0
failed=0

pass() { echo "  PASS: $1"; (( ++passed )); }
fail() { echo "  FAIL: $1"; (( ++failed )); }

# ── Wait for web server ──────────────────────────────────────────────
echo "Waiting for web server on port ${WEB_PORT}..."
deadline=$((SECONDS + 60))
while ! curl -sf "${BASE_URL}/" >/dev/null 2>&1; do
  if ((SECONDS >= deadline)); then
    echo "ERROR: web server did not become ready within 60s"
    exit 1
  fi
  sleep 1
done
echo "Web server is up."

# ── Web checks ───────────────────────────────────────────────────────
echo ""
echo "=== Web checks ==="

# POST /extensions — seed audio extensions
status=$(curl -sf -o /dev/null -w '%{http_code}' \
  -X POST "${BASE_URL}/extensions" \
  -d 'audio_techno=200&audio_beeps=300')

if [[ "$status" == "303" || "$status" == "200" ]]; then
  pass "POST /extensions → ${status}"
else
  fail "POST /extensions → ${status} (expected 303 or 200)"
fi

# GET / — verify seeded values appear
body=$(curl -sf "${BASE_URL}/")

if echo "$body" | grep -q 'value="200"'; then
  pass "GET / contains value=\"200\""
else
  fail "GET / missing value=\"200\""
fi

if echo "$body" | grep -q 'value="300"'; then
  pass "GET / contains value=\"300\""
else
  fail "GET / missing value=\"300\""
fi

# ── SIP checks ───────────────────────────────────────────────────────
echo ""
echo "=== SIP checks ==="

python3 scripts/sip_smoke_client.py "$SIP_PORT"
sip_exit=$?
if [[ $sip_exit -eq 0 ]]; then
  pass "SIP smoke client"
else
  fail "SIP smoke client (exit ${sip_exit})"
fi

# ── Summary ──────────────────────────────────────────────────────────
echo ""
echo "=== Results: ${passed} passed, ${failed} failed ==="
if ((failed > 0)); then
  exit 1
fi
