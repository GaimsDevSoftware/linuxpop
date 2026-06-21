#!/usr/bin/env bash
# Build a .deb for LinuxPop (Debian / Ubuntu / Linux Mint). Run on a Debian-based
# system (needs dpkg-deb), or let .github/workflows/build-deb.yml do it on CI.
#
#   bash packaging/deb/build-deb.sh [VERSION]
#
set -euo pipefail
VERSION="${1:-0.9.2}"
APPID=io.github.GaimsDevSoftware.LinuxPop
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$(mktemp -d)/linuxpop"

mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/usr/share/linuxpop" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/metainfo" \
         "$STAGE/usr/share/icons/hicolor/scalable/apps"

cp -a "$ROOT"/*.py "$ROOT"/platform_backend "$ROOT"/plugins_repo "$ROOT"/icons \
      "$STAGE/usr/share/linuxpop/"

# Bake the version so the installed copy (which has no .git) reports it
# correctly. VERSION comes from the release tag in CI; gen-version.sh just
# echoes it back (or resolves it itself when run by hand without an arg).
BAKED_VERSION="$(bash "$ROOT/packaging/gen-version.sh" "$VERSION")"
printf 'VERSION = "%s"\n' "$BAKED_VERSION" > "$STAGE/usr/share/linuxpop/_version.py"

cat > "$STAGE/usr/bin/linuxpop" <<'EOF'
#!/usr/bin/env bash
exec /usr/bin/python3 /usr/share/linuxpop/main.py "$@"
EOF
chmod 0755 "$STAGE/usr/bin/linuxpop"

install -m644 "$ROOT/packaging/$APPID.desktop"      "$STAGE/usr/share/applications/$APPID.desktop"
install -m644 "$ROOT/packaging/$APPID.metainfo.xml" "$STAGE/usr/share/metainfo/$APPID.metainfo.xml"
install -m644 "$ROOT/icons/linuxpop.svg"            "$STAGE/usr/share/icons/hicolor/scalable/apps/$APPID.svg"

cat > "$STAGE/DEBIAN/control" <<EOF
Package: linuxpop
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Maintainer: GaimsDev <raakanin@gmail.com>
Depends: python3, python3-gi, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, python3-xlib, xdotool, xclip, xdg-utils
Recommends: wl-clipboard, ydotool, qrencode, espeak-ng
Homepage: https://github.com/GaimsDevSoftware/linuxpop
Description: PopClip-style popup of context-aware actions for selected text
 Floating popup of context-aware actions above selected text - search, open
 links/paths, send to an AI assistant, encode/decode, translate, run shell
 commands - plus a clipboard manager and a no-code button wizard.
 Runs on KDE Plasma 6 / Wayland and on X11 desktops.
EOF

OUT="$ROOT/linuxpop_${VERSION}_all.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
echo "built: $OUT"
