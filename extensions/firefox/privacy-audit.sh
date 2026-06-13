#!/usr/bin/env bash
# Privacy audit for the LinuxPop Firefox extension.
#
# Verifies that the manifest matches the claims in PRIVACY.md. Run
# this before every AMO upload so a regression that adds a permission
# can't slip through unnoticed. Exits non-zero on any failure.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
MANIFEST="$HERE/manifest.json"
fail=0

check() {
    local label="$1"; local cmd="$2"; local expected="$3"
    local actual
    actual="$(eval "$cmd")"
    if [ "$actual" = "$expected" ]; then
        echo "  ✓ $label"
    else
        echo "  ✗ $label"
        echo "    expected: $expected"
        echo "    got:      $actual"
        fail=1
    fi
}

echo "Auditing $MANIFEST against PRIVACY.md claims..."
echo

check "permissions: []" \
    "python3 -c 'import json; print(json.load(open(\"$MANIFEST\"))[\"permissions\"])'" \
    "[]"
check "optional_permissions: []" \
    "python3 -c 'import json; print(json.load(open(\"$MANIFEST\"))[\"optional_permissions\"])'" \
    "[]"
check "host_permissions limited to 127.0.0.1" \
    "python3 -c 'import json; hp=json.load(open(\"$MANIFEST\"))[\"host_permissions\"]; print(all(h.startswith(\"http://127.0.0.1:\") for h in hp))'" \
    "True"
check "content_scripts only on 4 chat hosts (Claude, ChatGPT, Gemini, Perplexity x2)" \
    "python3 -c 'import json; m=json.load(open(\"$MANIFEST\"))[\"content_scripts\"][0][\"matches\"]; print(len(m))'" \
    "5"

# Check the content script for forbidden APIs. Strips comments first
# so the privacy-stance prose at the top of content.js (which by
# necessity NAMES the APIs we're NOT using) doesn't false-positive.
echo
echo "Scanning content.js for forbidden APIs (executable code only)..."
CONTENT="$HERE/../shared/content.js"
STRIPPED="$(python3 - <<EOF
import re
with open("$CONTENT") as f:
    src = f.read()
# Drop /* ... */ blocks first, then // line comments.
src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
src = re.sub(r"//[^\n]*", "", src)
print(src)
EOF
)"

forbidden_patterns=(
    'chrome\.storage'
    'localStorage'
    'sessionStorage'
    'indexedDB'
    'document\.cookie'
    'chrome\.tabs'
    'chrome\.history'
    'chrome\.cookies'
    'chrome\.webRequest'
    'chrome\.scripting'
    'navigator\.sendBeacon'
    'fetch\([^)]*https://'
    'fetch\([^)]*http://(?!127\.0\.0\.1)'
)
for pat in "${forbidden_patterns[@]}"; do
    if echo "$STRIPPED" | grep -nP "$pat" >/dev/null 2>&1; then
        echo "  ✗ forbidden pattern present in executable code: $pat"
        echo "$STRIPPED" | grep -nP "$pat" | head -3 | sed 's/^/      /'
        fail=1
    else
        echo "  ✓ no $pat"
    fi
done

echo
if [ $fail -eq 0 ]; then
    echo "Audit passed. Manifest and content script match PRIVACY.md claims."
    exit 0
else
    echo "Audit FAILED. Fix the items above before submitting to AMO."
    exit 1
fi
