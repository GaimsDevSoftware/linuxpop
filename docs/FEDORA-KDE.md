# LinuxPop on Fedora KDE Plasma (Wayland)

Fedora KDE Plasma 6 runs Wayland by default (Fedora 42 dropped the Plasma X11
session entirely). LinuxPop detects this and uses a native **Wayland/KDE
backend** instead of the X11 one - no Xorg session required.

How it maps each piece of the X11 design onto Wayland/KDE:

| Feature | Wayland/KDE implementation |
|---|---|
| Read selection / clipboard | `wl-clipboard` (`wl-paste` / `wl-copy`) |
| Auto-popup on selection | `wl-paste --primary --watch` |
| Popup placement at cursor | `gtk-layer-shell` (anchor top-left + margins) |
| Global cursor position | KWin scripting `workspace.cursorPos` over D-Bus |
| Keystroke / paste injection | `wdotool` (libei/RemoteDesktop portal) |
| Global hotkey | KGlobalAccel over D-Bus |
| Tray icon | StatusNotifierItem via libappindicator (KDE SNI) |

The backend is chosen automatically from `XDG_SESSION_TYPE`. Force it with
`LINUXPOP_BACKEND=x11` or `LINUXPOP_BACKEND=wayland_kde`.

## Install dependencies

```sh
sudo dnf install -y \
    python3 python3-gobject python3-dbus \
    gtk3 gtk-layer-shell libhandy libappindicator-gtk3 \
    wl-clipboard
# key injection: install wdotool (requires Rust/cargo):
#   cargo install wdotool
# or use the ghcr.io/cushycush/wdotool container
# optional: OCR + region capture
sudo dnf install -y tesseract
```

## Run

```sh
git clone https://github.com/GaimsDevSoftware/linuxpop.git ~/linuxpop
cd ~/linuxpop
python3 main.py            # auto-detects the Wayland/KDE backend
```

Logs: `~/.cache/linuxpop/linuxpop.log`.

## Known rough edges on Wayland/KDE

- **Global hotkey** registers via KGlobalAccel; confirm the binding appears in
  *System Settings → Shortcuts*. If a press does nothing, file an issue - the
  Qt-keysequence encoding may need tuning for your layout.
- **Esc-to-dismiss / click-outside** are softer than on X11. The popup
  auto-hides on a timer (and when the pointer leaves), but does not grab the
  keyboard (so it never steals focus from the app you're pasting into).
- **Active-window blocklist** is disabled on Wayland (no portable way to read
  the focused window's class yet).
- **Ctrl+double-click popup** is X11-only (no Wayland equivalent for global
  pointer interception).
- First popup after a selection has ~100-150 ms extra latency (one cursor-
  position round-trip to KWin). A resident helper to remove this is planned.

These are tracked in [FEDORA-KDE-BACKLOG.md](FEDORA-KDE-BACKLOG.md).
