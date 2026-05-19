# LinuxPop

> A PopClip-inspired floating action popup for Linux.

Select any text on your screen — LinuxPop pops up a small bar of context-aware
actions right above the selection. Copy, open URLs, run shell commands, ask an
AI, encode/decode, calculate — all without leaving the keyboard or mouse where
your work is.

Works on X11 (Cinnamon, GNOME on X11, KDE, XFCE, MATE, ...). Free, open
source, no accounts, no telemetry.

---

## Features

- **Context-aware actions** — different buttons for URLs, shell commands, plain
  text, paths and emails
- **Global hotkey** — summon the popup on the current selection from any app
- **System tray icon** — quick access to settings, plugin manager and toggle
- **Plugin system** — drop a `.py` file in `~/.config/linuxpop/plugins/` or
  install from the built-in catalog
- **Bundled plugins** — Base64, JSON pretty-print, URL encode/decode,
  calculator, case conversion, slugify, QR codes, send-to-AI, local Ollama AI
- **No data leaves your machine** unless a plugin explicitly does so (e.g.
  "Send to Claude" opens a browser)

---

## Install

### 1. System dependencies

```sh
# Ubuntu / Linux Mint / Debian
sudo apt-get install -y python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
    gir1.2-ayatanaappindicator3-0.1 xclip xdotool xdg-utils python3-xlib

# Fedora
sudo dnf install -y python3 python3-gobject gtk3 \
    libayatana-appindicator-gtk3 xclip xdotool xdg-utils python3-xlib

# Arch
sudo pacman -S python python-gobject gtk3 libayatana-appindicator \
    xclip xdotool xdg-utils python-xlib
```

Optional: `qrencode` (for the QR plugin), `ollama` (for the local-AI plugin).

### 2. Install LinuxPop

```sh
git clone https://github.com/GaimsDevSoftware/linuxpop.git ~/linuxpop
cd ~/linuxpop
bash install.sh
```

`install.sh` sets up autostart and verifies dependencies. Start it now without
logging out:

```sh
python3 ~/linuxpop/main.py
```

To uninstall:

```sh
bash ~/linuxpop/install.sh --uninstall
```

---

## Usage

- **Select text in any X11 app** → popup appears above your selection
- **Hotkey** (default `Super+Shift+Y`) → popup appears at the cursor with the
  current selection
- **Esc**, **click outside**, or wait a few seconds → popup goes away
- **Tray icon** → toggle auto-popup, open Settings, manage Plugins

### Settings

Right-click the tray icon → **Settings**. Or hand-edit
`~/.config/linuxpop/settings.json`.

| Key | Default | Description |
|---|---|---|
| `hotkey` | `super+shift+y` | Combo to summon popup. Record via Settings dialog. |
| `hotkey_source` | `primary` | `primary` (highlighted) or `clipboard` |
| `show_on_selection` | `true` | Auto-popup when you select text |
| `auto_hide_initial_ms` | `8000` | Hide if mouse never reaches popup |
| `auto_hide_leave_ms` | `4000` | Hide this long after mouse leaves the safe zone |
| `min_selection_length` | `1` | Ignore selections shorter than this |
| `terminal_keep_open` | `true` | Keep terminal open after running a command |
| `ai_paste_delay_seconds` | `2.5` | Wait before auto-pasting into a chat AI |

### Plugins

Open **Plugins…** from the tray menu to install/remove built-in plugins, or
drop your own `.py` file into `~/.config/linuxpop/plugins/`.

A plugin file just needs a top-level `register(register_plugin)` function:

```python
from classifier import ContentType
from plugin_base import Plugin

def _handler(text: str) -> None:
    print("got:", text)

def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="my-plugin",
        icon="emblem-favorite",
        tooltip="Do something",
        handler=_handler,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=50,
    ))
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full plugin API.

---

## Limitations

- **X11 only** — Wayland session APIs don't expose selection or global hotkeys
  the same way. A Wayland port is planned.
- **HiDPI** — works, but tested mainly on 2× scaling. File an issue if
  positioning is off on your setup.
- **Some panel grabs** — if another app holds an X11 input grab (e.g. an open
  menu), the popup may not catch outside-clicks instantly.

---

## License

MIT — see [LICENSE](LICENSE).
