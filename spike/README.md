# Fase 0 spike — popup at cursor on KWin Wayland

**Throwaway prototype.** Its only purpose is a go/no-go decision for the
Fedora KDE plan: on KDE Plasma 6 / Wayland, can LinuxPop show its popup at the
text cursor, instantly, the way it does on X11?

This proves nothing on X11 — **run it on a real Fedora KDE Plasma 6 Wayland
session** (Fedora 41/42 KDE is Wayland by default; F42 dropped the Plasma X11
session entirely).

## What it tests

| Capability | X11 today | This spike's Wayland/KDE replacement |
|---|---|---|
| Position window at coords | Xlib free positioning ([popup.py](../popup.py)) | `gtk-layer-shell`, anchored top-left, margins = (x, y) |
| Read global cursor position | Xlib pointer query ([main.py](../main.py)) | KWin script `workspace.cursorPos` → DBus callback ([cursor_pos.js](cursor_pos.js)) |
| Detect selection change | Xlib XFIXES ([watcher.py](../watcher.py)) | `wl-paste --primary --watch` |
| Feels instant | n/a | measured round-trip latency, printed per event |

## Run it

```sh
cd spike
bash setup-fedora.sh                 # installs deps on Fedora
python3 spike.py --check layer       # 1. positioning: popup at fixed (600,400)?
python3 spike.py --check cursor      # 2. cursorPos read + latency in ms
python3 spike.py --check full        # 3. the real thing: select text -> popup at cursor
```

## How to read the results (the go/no-go)

- **layer** — popup must appear at the top-left-ish (600,400) region. If it
  snaps to a screen edge or ignores the coords, layer-shell positioning is the
  blocker → fall back to "popup at focused window/screen" (the alternative you
  flagged).
- **cursor** — must print real coordinates. The **latency** is the key number:
  - `< ~50 ms` → great, popup-at-cursor will feel instant. **GO.**
  - `~50–150 ms` → usable but noticeable; consider caching / a resident KWin
    script instead of load-run-per-event.
  - `> ~150 ms` or TIMEOUT → the KWin-script round-trip is too slow/fragile for
    auto-popup → **fall back** to focused-window placement, or make popup
    hotkey-only on Wayland.
- **full** — the end-to-end experience. Does the popup land near the selection
  and feel responsive?

Record the three outcomes (placed correctly y/n, latency ms, full-flow feel)
and that decides Fase 1–2's positioning strategy.

## Known sharp edges (already researched, verify on target)

- `workspace.cursorPos` is the **only** practical cursor source on KDE Wayland;
  uinput / RemoteDesktop portals can move the pointer but not read it.
- Loading + running the KWin script per event may accumulate script instances
  or be rate-limited; if latency is bad, the production fix is a **resident**
  KWin script that pushes positions on demand, not load-run-per-event.
- `wl-paste --watch` covers PRIMARY selection; matches the X11 auto-popup
  trigger. The global hotkey path (KGlobalAccel / XDG GlobalShortcuts portal)
  is **not** part of this spike — it's lower-risk and handled in Fase 2.
