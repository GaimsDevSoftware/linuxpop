# LinuxPop - Handover

PopClip-style text-selection action popup. This note is the state after the
2026-06-13/14 session on Fedora 44 KDE Plasma 6 / Wayland.

## Two codebases (important)

| Path | Role | Git |
|---|---|---|
| `~/linuxpop-wl/` | **Deployed Fedora/Wayland build - edit HERE.** This is what runs. | local repo, branch `master`, **no remote** |
| `~/src/linuxpop/` | Mint/upstream codebase | `github.com/GaimsDevSoftware/linuxpop`, work on branch **`design-port`** (pushed); `main` is untouched |

They have **diverged** and do NOT share git history. Fedora work is committed
to `~/linuxpop-wl` (master). Portable changes are hand-ported to
`~/src/linuxpop` `design-port` and pushed. After a functional test on a real
Mint/Cinnamon/X11 box, merge `design-port` → `main`.

## Run / restart / verify

```bash
cd ~/linuxpop-wl
# stop any running instance (flock guards single-instance; tray dies with it)
kill $(ps -eo pid,args | awk '/[p]ython3 main.py/{print $1}')
# start (detached)
(nohup python3 main.py >/tmp/linuxpop.log 2>&1 &)
# verify: exactly one main.py + one tray_qt.py child
ps -eo pid,ppid,args | grep -E '[p]ython3 main.py|[t]ray_qt.py'
```
Note: the kill+relaunch-in-one-command pattern gets reaped (exit 144) - run
kill and launch as **separate** commands. `pgrep -f main.py` matches its own
cmdline; count with `ps -eo args | grep -c '[p]ython3 main.py'`.

Hotkeys: popup `super+shift+y`, clipboard `ctrl+super+v`, OCR `ctrl+alt+o`.

## What this session changed (Fedora, `~/linuxpop-wl` master)

Recent commits (newest first):
- `13e5870` popup: dismiss on **outside click** (Wayland) - evdev mouse watcher
- `435fc15` ocr: **frictionless region selector** (drag → done, no Accept)
- `9a139e5` icons: **full colour+glyph set + Settings style toggle**
- `2c32244` plugin: **Send to Mentor**
- `06f464b` onboarding: **snippets** beat in scene 2
- `9952ffb` ocr: wl-copy result + bind hotkey after install + textarea placeholder fix
- `339b50c` snippets: **native Wayland trigger expansion** (evdev + xkb + ydotool)
- `81c1a35` ocr: spectacle capture + one-click pkexec **Install** button
- `517c3f1` icons: redesign colour brand icons (bold flat-gradient)
- `f0d2583` wayland: anchor popup to selection-release point (no drift)
- `61de144` popup: thin dark contrast rim on colour icons
- `3f2bf7c` tray: kill Qt tray when daemon dies (no ghost tray icons)
- `994a572` fix egg-shaped badges + redesign window-close button

Mint `design-port` head: `9e20ea7` (icon system + placeholder fix + Mentor
ported; Wayland-only pieces intentionally left out).

## Key subsystems / where things live

- **Icon system** - `icon_style.py` maps each plugin's declared icon name to a
  concept that has BOTH `linuxpop-<concept>.svg` (colour gradient tile) and
  `linuxpop-<concept>-symbolic.svg` (mono glyph, GTK-recolourable, `#f0f0f0`).
  `icon_style.resolve(name)` returns the right variant for the `icon_style`
  setting (`color` default | `glyph`). Hooked in `popup._make_icon_image` and
  `plugin_manager._badge`. Settings combo + live preview in `settings_gui.py`
  next to popup-button-size. 44 SVGs in `icons/`; installed to
  `~/.local/share/icons/hicolor/scalable/apps/` by `plugin_loader._install_all_icons`.
  To regenerate icons, the generator lives only in shell history - glyph defs
  are inline; re-run from the session transcript if needed.
- **Colour icons render crisp** because they're SVG; earlier "blurry" reports
  were just detail-at-small-size, fixed by the bold redesign. A `load_surface`
  device-scale experiment was tried and REVERTED (it was a no-op: layer-shell
  windows already report scale 2).
- **Snippets / text expansion** (`plugins_repo/clipboard_history.py`, also the
  live copy at `~/.config/linuxpop/plugins/clipboard_history.py`):
  - X11 path: XRecord watch + xdotool inject (unchanged; used on Mint).
  - **Wayland path** (`_WaylandTriggerWatcher`): reads `/dev/input` keyboards
    (user is in `input` group), maps keycodes via **libxkbcommon ctypes**
    (`_XkbMapper`, honours the active layout - Norwegian `no(mac)` reads øæå
    right), injects via **ydotool**: backspace the trigger + `wl-copy` the
    expansion + **Shift+Insert** to paste. NB: `ydotool type` drops non-ASCII
    and ydotool **Ctrl chords don't register on KWin** - Shift+Insert does.
    Branches on `WAYLAND_DISPLAY`. ydotoold runs at `/run/user/1000/.ydotool_socket`.
- **OCR** (`screen_ocr.py` + `ocr_selector.py`):
  - `is_supported()` accepts spectacle/grim/maim + tesseract. `install_argv()`
    returns a `pkexec <pkgmgr> install …` argv; Settings shows an **Install**
    button (replaces the old xclip "copy command", which was broken on Wayland).
  - `ocr_selector.select_and_capture()` = grab full screen silently
    (`spectacle -f -b`), dim full-screen layer-shell overlay, drag one rect,
    crop on mouse-up. `run_ocr_to_clipboard` prefers it, falls back to
    `spectacle -r`. Result staged with **wl-copy** on Wayland (`_stage_text`),
    xclip on X11.
- **Popup dismissal** (`popup.py`): X11 uses an Xlib pointer-button poll in
  `_tick`. Wayland (`self._xdpy is None`) starts `_ClickWatcher` (evdev mouse)
  on show; an outside click hides immediately. Position is anchored to the
  selection-release point (cursor captured at the last selection-change event
  in `wayland_kde.py`, not after the settle delay).
- **Tray** (`tray.py` + `tray_qt.py`): Qt SNI subprocess. `_tray_preexec` sets
  `PR_SET_PDEATHSIG` so the tray dies with the daemon; tray_qt also polls its
  parent PID. Fixes the "multiple ghost tray icons" bug. (Mint uses an
  in-process GTK tray - this fix is Fedora-only.)
- **Mentor plugin** (`plugins_repo/send_to_mentor.py`): opens
  `http://127.0.0.1:<APP_PORT|7000>/?ask=<text>` - Mentor's own `?ask=` hook
  drops text in the composer and submits to the active chat. Health-checks
  `/api/health` first. Icon = Mentor's eclipse logo (`icons/linuxpop-mentor.svg`,
  copied from `~/odysseus/static/odysseus-icon.svg`). Mentor = the user's
  Odysseus fork at `~/odysseus` (FastAPI, port 7000).
- **Onboarding** (`onboarding.py`): Cairo animation, mascot "Pip". Scene 2
  ("Summon it your way") now cycles popup-hotkey → clipboard → **snippets**
  (types `;sig` → "Best regards, Alex"). Preview: `python3 onboarding.py`.

## Environment facts that matter

- Wayland/KWin: keystroke **injection** of modifier chords (Ctrl+V) does NOT
  reach apps; single keys + Shift+Insert + `ydotool type` (ASCII) do.
- User is in the `input` group → `/dev/input` readable (snippets, outside-click)
  and `/dev/uinput` writable (ydotool).
- HiDPI: monitor scale factor 2. Keyboard layout `no(mac)`, pc105.
- spectacle 6.6.5; grim present but KWin lacks wlr-screencopy (grim fails);
  `wl-copy` present, `xclip`/`maim`/`tesseract` were installed via the OCR
  Install button.
- SVG previews in the chat `show_widget` tool **strip `<polyline>` and some
  stroke paths** and corrupt large base64 - preview via small rsvg-rendered
  base64 PNGs, and trust rsvg/`Gtk.IconTheme` as ground truth for the app.

## Pending / next

- Functional smoke-test on real Mint/Cinnamon/X11, then `git checkout main &&
  git merge design-port && git push origin main` in `~/src/linuxpop`.
- Optional: brand-accurate refinement of a couple glyphs (chatgpt bloom).
- Verify-on-Mint loop closed: OCR ✓, snippets ✓, onboarding ✓ confirmed on
  Fedora by the user; Mint still untested.
