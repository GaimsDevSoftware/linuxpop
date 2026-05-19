#!/usr/bin/env bash
# LinuxPop installer — sets up autostart for the current user.
# Usage: bash install.sh           # install
#        bash install.sh --uninstall

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTOSTART_DIR="$HOME/.config/autostart"
AUTOSTART_FILE="$AUTOSTART_DIR/linuxpop.desktop"
PLUGIN_DIR="$HOME/.config/linuxpop/plugins"

uninstall() {
    if [[ -f "$AUTOSTART_FILE" ]]; then
        rm -v "$AUTOSTART_FILE"
        echo "[install] LinuxPop removed from autostart"
    else
        echo "[install] no autostart file to remove"
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
mkdir -p "$AUTOSTART_DIR" "$PLUGIN_DIR"
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
echo "[install] autostart written to $AUTOSTART_FILE"
echo "[install] LinuxPop will start automatically next login."
echo "[install] to start now: python3 ${REPO_DIR}/main.py"
echo "[install] uninstall: bash $0 --uninstall"
