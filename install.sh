#!/usr/bin/env bash
# LinuxPop installer - sets up autostart for the current user.
# Usage: bash install.sh           # install
#        bash install.sh --uninstall

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_ID="io.github.GaimsDevSoftware.LinuxPop"
AUTOSTART_DIR="$HOME/.config/autostart"
AUTOSTART_FILE="$AUTOSTART_DIR/linuxpop.desktop"
APPS_DIR="$HOME/.local/share/applications"
APP_FILE="$APPS_DIR/${APP_ID}.desktop"
LEGACY_APP_FILE="$APPS_DIR/linuxpop.desktop"   # old short-name file to clean up
METAINFO_DIR="$HOME/.local/share/metainfo"
METAINFO_FILE="$METAINFO_DIR/${APP_ID}.metainfo.xml"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
ICON_FILE="$ICON_DIR/${APP_ID}.svg"
PLUGIN_DIR="$HOME/.config/linuxpop/plugins"

uninstall() {
    if [[ -f "$AUTOSTART_FILE" ]]; then
        rm -v "$AUTOSTART_FILE"
        echo "[install] LinuxPop removed from autostart"
    fi
    for f in "$APP_FILE" "$LEGACY_APP_FILE"; do
        if [[ -f "$f" ]]; then
            rm -v "$f"
        fi
    done
    update-desktop-database "$APPS_DIR" 2>/dev/null || true
    if [[ -f "$METAINFO_FILE" ]]; then
        rm -v "$METAINFO_FILE"
    fi
    if [[ -f "$ICON_FILE" ]]; then
        rm -v "$ICON_FILE"
    fi
    pkill -f "python3 .*linuxpop/main.py" 2>/dev/null || true
    echo "[install] any running instances stopped"
}

if [[ "${1:-}" == "--uninstall" || "${1:-}" == "-u" ]]; then
    uninstall
    exit 0
fi

# Verify dependencies
echo "[install] checking dependencies..."
missing=()
for bin in python3 xclip xdg-open; do
    command -v "$bin" >/dev/null 2>&1 || missing+=("$bin")
done
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "[install] missing: ${missing[*]}"
    echo "[install] install with: sudo apt-get install -y ${missing[*]/xdg-open/xdg-utils}"
    exit 1
fi

python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk" 2>/dev/null \
    || { echo "[install] missing python3-gi: sudo apt-get install -y python3-gi gir1.2-gtk-3.0"; exit 1; }
python3 -c "import Xlib" 2>/dev/null \
    || { echo "[install] missing python-xlib: pip3 install python-xlib --break-system-packages"; exit 1; }

# Write the autostart .desktop with the absolute path to main.py
mkdir -p "$AUTOSTART_DIR" "$APPS_DIR" "$METAINFO_DIR" "$ICON_DIR" "$PLUGIN_DIR"

# Clean up the old short-name launcher so we don't end up with two
# duplicate entries in the menu after upgrading.
if [[ -f "$LEGACY_APP_FILE" ]]; then
    rm -f "$LEGACY_APP_FILE"
fi
cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=LinuxPop
Comment=PopClip-inspired floating action popup
Exec=/usr/bin/python3 ${REPO_DIR}/main.py
Icon=linuxpop
Terminal=false
Categories=Utility;
X-GNOME-Autostart-enabled=true
StartupNotify=false
EOF
chmod +x "$AUTOSTART_FILE"

# Write the launcher .desktop using the reverse-DNS app ID so Mint
# Software Manager / GNOME Software / KDE Discover can cross-reference
# it with the AppStream metainfo installed below.
cat > "$APP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=LinuxPop
GenericName=Text action popup
Comment=PopClip-inspired floating popup of context-aware actions on selected text
Exec=/usr/bin/python3 ${REPO_DIR}/main.py
Icon=${APP_ID}
Terminal=false
Categories=Utility;TextTools;
Keywords=popup;text;clipboard;snippets;ai;productivity;popclip;
StartupNotify=false
StartupWMClass=linuxpop
X-GNOME-UsesNotifications=true
EOF
chmod +x "$APP_FILE"
update-desktop-database "$APPS_DIR" 2>/dev/null || true

# AppStream metainfo so software centres can see the app, its description,
# screenshots and donation URL. Without this LinuxPop only shows up by
# name; with it, it has a proper listing.
if [[ -f "$REPO_DIR/packaging/${APP_ID}.metainfo.xml" ]]; then
    install -Dm644 \
        "$REPO_DIR/packaging/${APP_ID}.metainfo.xml" \
        "$METAINFO_FILE"
    echo "[install] AppStream metainfo written to $METAINFO_FILE"
fi

# Mirror the icon under the canonical reverse-DNS filename so AppStream
# can resolve <icon type="stock">io.github.GaimsDevSoftware.LinuxPop</icon>.
if [[ -f "$REPO_DIR/icons/linuxpop.svg" ]]; then
    install -Dm644 "$REPO_DIR/icons/linuxpop.svg" "$ICON_FILE"
fi
gtk-update-icon-cache -f "$ICON_DIR/.." 2>/dev/null || true

echo "[install] autostart written to $AUTOSTART_FILE"
echo "[install] launcher entry written to $APP_FILE"
echo "[install] LinuxPop will start automatically next login,"
echo "[install]   and shows up in Synapse / Mint menu / GNOME Activities."
echo "[install] to start now: python3 ${REPO_DIR}/main.py"
echo "[install] uninstall: bash $0 --uninstall"
