# GNOME / Mutter Wayland support

How LinuxPop makes its popup work on **Fedora GNOME (Mutter, Wayland)**, the same
way it already works on **Fedora KDE Plasma (KWin, Wayland)** and **X11/Mint
(Cinnamon)** — without regressing either of those paths.

## Why GNOME needs a different approach than KDE

The KDE backend (`platform_backend/wayland_kde.py`) positions the popup with two
KWin-specific mechanisms:

1. **Window placement** via `wlr-layer-shell` (`gtk-layer-shell`, anchor + margins).
2. **Pointer position** via a KWin JS script reading `workspace.cursorPos` over DBus.

Mutter implements neither. It has no `wlr-layer-shell` (a deliberate GNOME
decision) and no external cursor-position API, so the KDE positioning path cannot
work on GNOME.

## The solution: a hybrid XWayland backend

`platform_backend/xwayland_gnome.py` → `XWaylandGnomeBackend(X11Backend)`.

The X11 backend already solves the two hard problems when run under XWayland:
`Gtk.Window.move()` positions an XWayland toplevel at global coordinates, and
`XQueryPointer` returns the global pointer — both are DE-agnostic. So GNOME reuses
the proven X11 path for positioning and pointer, and swaps **only** the I/O that
X11 tools can't do for native Wayland apps.

| Capability | Source | Why |
|---|---|---|
| pointer_position | inherited `X11Backend` (XQueryPointer) | global coords under XWayland |
| popup placement (`move_popup_window`, `init_popup_window`) | inherited `X11Backend` (`Gtk.Window.move`) | works for XWayland toplevel; no layer-shell needed |
| `popup_uses_xlib = True`, `pointer_is_logical = False` | inherited | XWayland behaves like native X11 |
| selection watch | `WaylandSelectionWatcher` (`wl-paste --primary --watch`) | sees native Wayland apps; XFixes/xclip would only see XWayland apps |
| read_selection / set_clipboard | delegated to `WaylandKdeBackend` (`wl-clipboard`) | compositor-agnostic |
| key injection (send_key/type_text/can_paste/paste) | delegated to `WaylandKdeBackend` (ydotool/wtype) | `xdotool` can't reach native Wayland apps |
| double-click watcher | `WaylandDoubleClickWatcher` (kernel-level evdev) | sees native Wayland + XWayland |
| global hotkey | `EvdevHotkey` (`/dev/input`) | X11 grab via XWayland misses native-Wayland-focused windows |
| active window (blocklist) | AT-SPI + XWayland WM_CLASS | WM_CLASS unavailable for native Wayland apps |

The `WaylandKdeBackend` instance is created lazily and used only as a
compositor-agnostic I/O helper (clipboard + injection); never for pointer or
positioning. Its `__init__` only sets up a DBus main loop, which is harmless on
GNOME.

## Files

**New:**
- `platform_backend/xwayland_gnome.py` — the hybrid backend.
- `platform_backend/evdev_hotkey.py` — `EvdevHotkey`: kernel-level global hotkey.
  Modifier state via libxkbcommon (reuses `wayland_kde._XkbModifierState`), so
  `ctrl:swap_lalt_lctl` and other xkb options are honoured. Interface mirrors
  `hotkey.Hotkey` (start / stop(wait, timeout)).

**Changed:**
- `platform_backend/__init__.py` — `detect()` routes GNOME+Wayland →
  `xwayland_gnome`; KDE → `wayland_kde`; other Wayland → `wayland_kde`
  (best-effort); non-Wayland → `x11`. `get_backend()` wires the new backend.
  Override with `LINUXPOP_BACKEND=x11|wayland_kde|xwayland_gnome`.
- `main.py` — forces `GDK_BACKEND=x11` before importing gi/Gtk, but only when
  `detect() == "xwayland_gnome"` (KDE keeps native Wayland for layer-shell).
  Forces x11 even if the session presets `GDK_BACKEND=wayland`. Opt out with
  `LINUXPOP_GDK_BACKEND=<value>`.
- `editable_detect.py` — adds `active_window_atspi_haystacks()` for blocklist
  matching on Wayland (AT-SPI, since WM_CLASS is unavailable for native apps).

## Coordinates and fractional scaling

Under Mutter fractional scaling the GTK logical screen is the scaled-down size
(e.g. 3072×1728 at 125% on a 3840×2160 panel) and `monitor.get_scale_factor()`
reports the integer ceiling (2). `XQueryPointer` returns device coordinates at
that integer scale (≈2× the logical pointer), and `popup.py` divides global
coordinates by `monitor.get_scale_factor()` when `pointer_is_logical` is False,
recovering the correct logical position. The popup anchors above the selection
rect when one is available (`focused_selection_rect`), and falls back to the
pointer position otherwise.

## Running on Fedora GNOME

```bash
git clone https://github.com/GaimsDevSoftware/linuxpop.git
cd linuxpop

# prerequisites
sudo dnf install wl-clipboard ydotool python3-gobject gtk3 \
                 python3-xlib xdotool xclip tesseract
sudo usermod -aG input "$USER"          # for evdev hotkey + double-click; re-login

# Cut/Paste/Backspace injection on Wayland needs a per-user ydotoold +
# /dev/uinput access. See packaging/wayland/README.md for the one-time setup
# (udev rule + user service). Skip it if you only need selection-triggered
# popups; required for the keystroke actions.

python main.py                          # backend auto-selected; GDK_BACKEND auto-set
```

The backend is chosen automatically on GNOME — no env vars needed.
`check_session()` prints non-fatal warnings if `wl-clipboard` or a Wayland key
injector is missing.

Debugging:
- Force the backend: `LINUXPOP_BACKEND=xwayland_gnome python main.py --debug`
- If the popup lands top-left, the pointer query failed — check `DISPLAY` is set
  and XWayland is running.

## Caveats

- The evdev hotkey and double-click watcher need the user in the `input` group
  (same as ydotool — no new requirement beyond KDE).
- Primary selection isn't set identically by every app (some set it lazily). This
  is app-level and also true on X11, not a GNOME-specific regression.
- Blocklist matching is weaker on GNOME (AT-SPI app name instead of WM_CLASS).
