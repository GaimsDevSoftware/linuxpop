# LinuxPop — Handoff (2026-06-14, session 2)

Continuation of the Fedora 44 KDE Plasma 6 / Wayland work. Read the prior
`HANDOVER.md` first for the broader project map. This file is **action-oriented**:
for each open item it says exactly what to do, where, and how to verify.

## ⚑ Use the existing research cache FIRST (don't re-research)
`~/.claude/research-cache/` already has notes that directly answer items 4 & 5.
**Read these before doing any new web search** (and per global instructions, read
`INDEX.md` first):
- **`plasma6-klipper-actions-and-global-command-shortcuts.md`** — has the EXACT,
  verified terminal-launcher command for KDE and the shell-quoting gotcha. → item 4.
- **`kwin-wayland-window-focus-typing.md`** — confirms wtype is dead on KWin and
  ydotool is the validated keystroke path, plus the KWin-script window-raise
  pattern. → item 5 (chord injection) and Wayland focus handoff.
- The new deep-research report (item 2, run `wf_31cee9ac-98f`) layers on top of
  these — once cached it should be cross-linked in `INDEX.md`.

---

## Two codebases (critical — don't mix them up)

| Path | Role | Git |
|---|---|---|
| `~/linuxpop-wl/` | **Deployed Fedora/Wayland build — this is what RUNS.** Edit here. | local repo, branch `master`, **no remote** |
| `~/src/linuxpop/` | Mint/upstream, on GitHub | `github.com/GaimsDevSoftware/linuxpop`, currently on branch `feature/platform-backend` |

Restart (kill + launch as **separate** Bash calls — combined gets reaped, exit 144):
```bash
kill $(ps -eo pid,args | awk '/[p]ython3 main.py/{print $1}')
cd ~/linuxpop-wl && (nohup python3 main.py >/tmp/linuxpop.log 2>&1 &)
ps -eo pid,args | grep -E '[p]ython3 main.py|[t]ray_qt.py'   # expect 1 each
```
Note: the live app loads plugins from `~/.config/linuxpop/plugins/` (user copies),
NOT `~/linuxpop-wl/plugins_repo/`. Edit the repo copy, then sync to the config dir
(or the installer does) before it takes effect.

---

## What already landed this session (context)

- **`~/linuxpop-wl` master `2de0be3`** — popup fixes (committed, awaiting user's
  visual OK): (1) off-screen-measure so the popup no longer lands at 0,0 and the
  click-watcher no longer kills it instantly (the "dead buttons" bug); (2)
  `_glyph_image()` recolours our `linuxpop-*-symbolic` glyphs at load time so they
  aren't near-white-on-light (the "faint glyphs" bug).
- **`~/src/linuxpop` PR #1** (`feature/platform-backend` → `design-port`, commit
  `f49f1c7`) — lifts the `platform_backend/` package (X11+Wayland unification) into
  the GitHub repo. **OPEN, not merged, and stale** (see item 3).

---

# OPEN ITEMS — how we take care of each

## 1. Confirm the popup fixes look right on screen  ⏳ needs the user
**Where:** running app (`~/linuxpop-wl`, already restarted).
**Do:** ask the user to select text in a couple of apps (Kate/native + a light-themed
page) and a command-like selection. Check: popup appears at the selection (not 0,0),
buttons respond to clicks, and the mono glyphs are clearly visible on the light bar.
**If glyphs still wrong:** check `~/.config/linuxpop/settings.json` `icon_style`
(color vs glyph) and `theme`; verify the installed SVGs at
`~/.local/share/icons/hicolor/scalable/apps/linuxpop-*-symbolic.svg`; reconsider the
`#2b2b2b→bg` hole mapping (may merge at 16px). `_glyph_colors()` /`_glyph_image()`
live in `popup.py`.
**Done when:** user confirms. Then this fix is safe to carry into the PR (item 3).

## 2. Retrieve + cache the deep-research report  📋
**What it covers:** robust gtk-layer-shell popup on KWin; **can we anchor the popup
to the actual selected-text rectangle** (AT-SPI bounds / text-input-v3
cursor_rectangle / what PopClip-likes do); broader Wayland-protocol opportunities
(ext-data-control, virtual-keyboard, xdg-activation, global-shortcuts &
RemoteDesktop/InputCapture portals, the correct KWin Ctrl+V chord injection).
**Do:**
- Run `/workflows` (or inspect transcript dir
  `…/subagents/workflows/wf_31cee9ac-98f/`) to get the final synthesized report.
  Workflow run ID: **`wf_31cee9ac-98f`** (agents finished ~16:43; completion
  notice may already be in the thread).
- **Save it** to `~/.claude/research-cache/<slug>.md` and add one line to
  `~/.claude/research-cache/INDEX.md` (per global instructions). Suggested slug:
  `wayland-popclip-popup-positioning-kwin`; tags:
  `wayland, gtk-layer-shell, kwin, atspi, text-input-v3, popclip, popup, plasma6`.
**Done when:** report is cached + indexed. It then feeds items 4 & 5.

## 3. De-stale PR #1 (carry the popup fixes into the merge)  📋
**Why:** PR commit `f49f1c7` predates popup fix `2de0be3`, so the PR's `popup.py`
still has the buggy positioning and no glyph recolour.
**Do (after item 1 confirms):**
```bash
cp ~/linuxpop-wl/popup.py /home/robert/src/linuxpop/popup.py     # verbatim copy
cd /home/robert/src/linuxpop && git add popup.py \
  && git commit -m "popup: carry Wayland 0,0 + glyph fixes into the merge" \
  && git push
```
Then re-check the PR diff. **Do NOT merge yet** — the user tests on their Mint/X11
machine first (the X11 backend path is unverified on real hardware).
**Merge path:** `feature/platform-backend` → `design-port` → (after Mint test) →
`main`. PR: https://github.com/GaimsDevSoftware/linuxpop/pull/1
**Done when:** PR diff is clean, user has tested on Mint, then merge.

## 4. NEW FEATURE — "Run in terminal" plugin for COMMAND text  📋 (user wants this)
**Why it's missing:** `classifier.ContentType.COMMAND` exists and tags shell
commands (e.g. `wpctl set-volume …`), but **no plugin registers for COMMAND**, so
command selections have no run action.
**Where:** new file `~/linuxpop-wl/plugins_repo/run_in_terminal.py` (then sync to
`~/.config/linuxpop/plugins/`). Mirror the `Plugin` dataclass + `register()` pattern
from `plugins_repo/calculator.py`.
**📓 Cached research to use:** `~/.claude/research-cache/plasma6-klipper-actions-and-global-command-shortcuts.md`.
Klipper's own verified "Run in terminal" action is literally
`konsole --hold -e /bin/sh -c %s` where `%s` is **auto shell-quoted** (KMacroExpander)
— i.e. don't add your own quotes. That's our reference invocation.
**Spec:**
- `Plugin(name="run-in-terminal", icon="utilities-terminal-symbolic",
  tooltip="Run in terminal", handler=_run, content_types=(ContentType.COMMAND,),
  priority=30)`.
- `_run(text)`: **show a confirmation dialog first** (RCE safety — reuse the guard
  shape `recipe_loader.py` already uses for `run_command` recipes; grep it for the
  notify-send/refuse pattern). On confirm, launch the terminal **without building a
  shell string yourself** — pass the command as a single argv element so the shell
  quotes it, mirroring Klipper: `["konsole", "--hold", "-e", "/bin/sh", "-c", text]`
  via `subprocess.Popen` (NOT `shell=True`). Fall back chain:
  `konsole` → `x-terminal-emulator` → `$TERMINAL` → `gnome-terminal`/`xterm`.
- Consider a setting to default-off (it's powerful) and a `predicate` to skip
  obviously-not-a-command selections.
**Verify:** select a harmless command (`echo hi`), click the new icon, confirm the
terminal opens and runs it; select multi-line and verify it doesn't mis-split.
**Done when:** appears only for COMMAND text, runs behind confirmation, ships in the
repo + config dir.

## 5. Research-driven follow-ups (do AFTER item 2 report is in hand)  📋
- **Anchor popup to the selection rectangle, not the mouse** (the user explicitly
  asked if this is possible). The report should say whether AT-SPI
  `getRangeExtents`/`getCharacterExtents` can give screen-coord selection bounds on
  KWin Wayland and across toolkits. NOTE: AT-SPI is currently **OFF**
  (`editable_atspi_listener_enabled=false` in settings) — turning it on is the
  likely prerequisite. Implement as a fallback chain: AT-SPI selection bounds →
  text-input cursor rect → current mouse-pointer behaviour. Positioning lives in
  `popup.py::_present_near`; the selection/pointer comes from
  `wayland_kde.WaylandSelectionWatcher` + `_kwin_cursor`.
- **Fix modifier-chord injection on KWin** (memory: "wtype+ydotool chords dead on
  KWin"). **📓 Cached research:** `~/.claude/research-cache/kwin-wayland-window-focus-typing.md`
  already establishes WHY: wtype uses `zwp_virtual_keyboard_v1` which **KWin does not
  implement**, so wtype simply fails on KDE; **ydotool** (kernel uinput, needs the
  `ydotoold` user daemon + `/dev/uinput` udev rule + user in `input` group + optional
  `YDOTOOL_SOCKET`) is the validated path. Enter = `ydotool key 28:1 28:0`. So the fix
  is likely "make ydotool/ydotoold the primary chord path and drop wtype for chords"
  in `wayland_kde.send_key`/`paste` — confirm against the new deep-research report
  (it may prefer the RemoteDesktop/InputCapture portal for a sandbox-clean route),
  then implement. Same note also has the KWin-script window-raise pattern if focus
  handoff before paste is needed.
**Done when:** each lands as its own commit in `linuxpop-wl`, then is ported to the
PR branch.

---

## Quick reference
- Plugin dataclass: `plugin_base.py` (fields: name, icon, tooltip, handler,
  content_types, priority, predicate, requires_editable). Plugins expose
  `register(register_plugin)`.
- Content types: `classifier.py::ContentType` = COMMAND, URL, EMAIL, PATH, PLAIN_TEXT.
- Popup render/positioning: `popup.py` (`_present_near`, `_make_icon_image`,
  `_glyph_image`, `_glyph_colors`, `_ClickWatcher`).
- Wayland backend: `platform_backend/wayland_kde.py` (`move_popup_window`,
  `pointer_position`, `send_key`, `make_selection_watcher`); cursor via
  `platform_backend/_kwin_cursor.py`.
- settings.json now: `theme:light`, `icon_style:color`, AT-SPI listener disabled.
- Restart gotcha: kill + launch as separate Bash calls (exit 144 otherwise).
