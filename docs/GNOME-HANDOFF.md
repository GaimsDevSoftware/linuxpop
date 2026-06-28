# GNOME / Mutter Wayland support — handoff

Status as of the `feature/gnome-wayland-support` branch. This note exists so the
work can be continued on a fresh Fedora GNOME machine (or by a fresh Claude
session) without the original chat transcript. The code travels with this
branch; this file travels with it.

## Goal

Make LinuxPop's popup appear at the selected text / mouse pointer on **Fedora
GNOME (Mutter, Wayland)**, the same way it already works on **Fedora KDE Plasma
(KWin, Wayland)** — without regressing KDE or the X11/Mint (Cinnamon) path.

## Why GNOME needed a different approach than KDE

The KDE backend (`platform_backend/wayland_kde.py`) positions the popup with two
KWin-specific mechanisms:

1. **Window placement** via `wlr-layer-shell` (`gtk-layer-shell`, anchor + margins).
2. **Pointer position** via a KWin JS script reading `workspace.cursorPos` over DBus.

**Mutter implements neither.** It has no `wlr-layer-shell` (deliberate GNOME
decision) and no external cursor-position API. So the KDE positioning path
cannot work on GNOME.

## The solution: a hybrid XWayland backend

`platform_backend/xwayland_gnome.py` → `XWaylandGnomeBackend(X11Backend)`.

Key insight: the **X11 backend already solves the two hard problems** when run
under XWayland. `Gtk.Window.move()` positions an XWayland toplevel at global
coordinates, and `XQueryPointer` returns the global pointer — both DE-agnostic.
So GNOME reuses the proven X11 path for positioning/pointer and swaps **only**
the I/O that X11 tools can't do for native Wayland apps.

| Capability | Source | Why |
|---|---|---|
| pointer_position | inherited `X11Backend` (XQueryPointer) | global coords under XWayland |
| popup placement (`move_popup_window`, `init_popup_window`) | inherited `X11Backend` (`Gtk.Window.move`) | works for XWayland toplevel; no layer-shell needed |
| `popup_uses_xlib = True`, `pointer_is_logical = False` | inherited | XWayland behaves like native X11 |
| selection watch | `WaylandSelectionWatcher` (`wl-paste --primary --watch`) | sees native Wayland apps; XFixes/xclip would only see XWayland apps |
| read_selection / set_clipboard | delegated to `WaylandKdeBackend` (`wl-clipboard`) | compositor-agnostic |
| key injection (send_key/type_text/can_paste/paste) | delegated to `WaylandKdeBackend` (ydotool/wtype) | `xdotool` can't reach native Wayland apps |
| double-click watcher | `WaylandDoubleClickWatcher` (kernel-level evdev) | sees native Wayland + XWayland |
| global hotkey | **new** `EvdevHotkey` (`/dev/input`) | X11 grab via XWayland misses native-Wayland-focused windows |
| active window (blocklist) | AT-SPI + XWayland WM_CLASS | WM_CLASS unavailable for native Wayland apps |

The `WaylandKdeBackend` instance is created lazily and used **only** as a
compositor-agnostic I/O helper (clipboard + injection); never for pointer or
positioning. Its `__init__` only sets up a DBus main loop — harmless on GNOME.

## Files in this branch

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
- `main.py` — forces `GDK_BACKEND=x11` **before** importing gi/Gtk, but **only**
  when `detect() == "xwayland_gnome"` (KDE keeps native Wayland for layer-shell).
  Forces x11 even if the session presets `GDK_BACKEND=wayland`. Opt out with
  `LINUXPOP_GDK_BACKEND=<value>`.
- `editable_detect.py` — adds `active_window_atspi_haystacks()` for blocklist
  matching on Wayland (AT-SPI, since WM_CLASS is unavailable for native apps).

## What was verified (on KDE-under-XWayland; positioning/pointer are DE-agnostic)

- `detect()` correct for GNOME / KDE / X11 / forced override.
- Backend instantiates; method resolution correct (positioning inherited from
  `X11Backend`, I/O overridden).
- `pointer_position()` returns real global coords (XWayland).
- `read_selection("primary")` returns real selection (wl-paste).
- `can_paste()` True (ydotool present).
- GDK bootstrap forces x11 for gnome backend; leaves KDE on `wayland`.
- `test_popup.py` cycled 3 popups with no positioning crash.
- `EvdevHotkey` arms, opens 5 input devices, and the xkb state correctly
  reflected the user's `layout=no variant=mac options=ctrl:swap_lalt_lctl`.
- KDE backend confirmed unchanged.

## What still needs a REAL GNOME session to verify

1. **Placement accuracy** — popup actually appears at the selection / pointer,
   not the top-left corner.
2. **Fractional scaling (125%/150%)** — the main open risk. XWayland coordinates
   may be offset/blurry under Mutter's fractional scaling; if so, adjust the
   scale handling in `popup.py` (it divides physical coords by monitor scale
   when `pointer_is_logical` is False).
3. **Cut/Paste/Backspace** via ydotool into native Wayland apps.
4. **Global hotkey** firing while a native Wayland app is focused.
5. **AT-SPI selection-rect anchoring** (`focused_selection_rect`) on GNOME apps.

## How to run/test on Fedora GNOME

```bash
git clone https://github.com/GaimsDevSoftware/linuxpop.git
cd linuxpop
git checkout feature/gnome-wayland-support

# prerequisites
sudo dnf install wl-clipboard ydotool python3-gobject gtk3 \
                 python3-xlib xdotool xclip tesseract
sudo usermod -aG input "$USER"          # for evdev hotkey + double-click; re-login
systemctl --user enable --now ydotoold  # or start ydotoold manually

python main.py                          # backend auto-selected; GDK_BACKEND auto-set
```

The backend is chosen automatically on GNOME — no env vars needed.
`check_session()` prints non-fatal warnings if `wl-clipboard` or a Wayland key
injector is missing.

Debugging:
- Force the backend: `LINUXPOP_BACKEND=xwayland_gnome python main.py --debug`
- Confirm XWayland: the app should set `GDK_BACKEND=x11` itself; verify with
  `--debug` output.
- If the popup lands top-left: pointer query failed — check `DISPLAY` is set and
  XWayland is running.

## Remaining work (deferred until GNOME verification)

- **Flatpak packaging for GNOME**: `finish-args` need `/dev/input` access (evdev
  hotkey + double-click) and ydotool, mirroring the KDE work in commit
  `e92c36e` ("Bundle ydotool in Flatpak…"). See `packaging/` and `PACKAGING.md`.
- **Docs/README**: mark GNOME as supported once visually confirmed.
- **Fractional-scaling fix** in `popup.py` if step 2 above shows offset.

## Known caveats

- evdev hotkey + double-click need the user in the `input` group (same as
  ydotool — no new requirement beyond KDE).
- Primary selection isn't set identically by every app (some lazily); app-level,
  also true on X11 — not a GNOME-specific regression.
- Blocklist matching is weaker on GNOME (AT-SPI app name instead of WM_CLASS).
