#!/usr/bin/env bash
# Build the Firefox extension into dist/linuxpop-firefox.zip ready
# for upload to addons.mozilla.org.
#
# Steps:
#   1. Resolve shared/content.js into the firefox build dir (zip
#      can't follow symlinks across dirs without -y and even then
#      AMO's automated validator strips them).
#   2. Copy manifest + icons.
#   3. Zip the result.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SHARED="$HERE/../shared"
BUILD="$HERE/build"
DIST="$HERE/dist"
ZIP="$DIST/linuxpop-firefox.zip"

rm -rf "$BUILD"
mkdir -p "$BUILD" "$DIST"

cp "$HERE/manifest.json" "$BUILD/"
cp "$SHARED/content.js"  "$BUILD/"
# Ship PRIVACY.md inside the package so the install-time review and
# the unpacked extension both surface the same text.
cp "$HERE/../PRIVACY.md" "$BUILD/" 2>/dev/null || \
    echo "WARN: ../PRIVACY.md not found - extension will ship without it"
if [ -d "$HERE/icons" ] && [ "$(ls -A "$HERE/icons" 2>/dev/null)" ]; then
    cp -r "$HERE/icons" "$BUILD/"
else
    echo "WARN: icons/ is empty - AMO will reject the upload"
fi

# Run the privacy audit before producing the zip. Any regression that
# adds a permission or a non-loopback fetch fails the build.
"$HERE/privacy-audit.sh"
echo

rm -f "$ZIP"
(cd "$BUILD" && zip -qr "$ZIP" .)

echo "Built: $ZIP"
echo
echo "Next:"
echo "  Test locally:  open about:debugging in Firefox, 'Load Temporary Add-on',"
echo "                 pick $BUILD/manifest.json"
echo "  Lint:          web-ext lint --source-dir=$BUILD"
echo "  Submit:        upload $ZIP at https://addons.mozilla.org/developers/addon/submit/"
