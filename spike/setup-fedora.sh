#!/usr/bin/env bash
# Install the dependencies the Fase 0 spike needs on Fedora KDE Plasma 6.
# Run on the TARGET machine (a real KDE Wayland session), not the dev box.
set -euo pipefail

echo "[spike] installing Fedora packages..."
sudo dnf install -y \
    python3 python3-gobject python3-dbus \
    gtk3 gtk-layer-shell \
    wl-clipboard \
    wtype \
    qt6-qttools          # provides qdbus6 for manual cursorPos poking

echo
echo "[spike] sanity checks:"
printf '  session : %s / %s\n' "${XDG_SESSION_TYPE:-?}" "${XDG_CURRENT_DESKTOP:-?}"
python3 -c "import gi; gi.require_version('GtkLayerShell','0.1'); print('  GtkLayerShell: OK')" \
    || echo "  GtkLayerShell: MISSING"
python3 -c "import dbus; print('  dbus-python  : OK')" || echo "  dbus-python  : MISSING"
command -v wl-paste >/dev/null && echo "  wl-paste     : OK" || echo "  wl-paste     : MISSING"
command -v wtype   >/dev/null && echo "  wtype        : OK" || echo "  wtype        : MISSING"

echo
echo "[spike] now run, in order:"
echo "  python3 spike.py --check layer    # positioning works?"
echo "  python3 spike.py --check cursor    # cursorPos + latency?"
echo "  python3 spike.py --check full      # popup at cursor on selection?"
