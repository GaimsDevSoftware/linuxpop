# LinuxPop

> Like PopClip on the Mac, but for Linux. Highlight text, get a popup of actions. Works on X11 and KDE Plasma 6 Wayland.

![LinuxPop in action: select text, a popup of context actions appears, here translating the selection](docs/linuxpop-demo.gif)

Highlight some text and a little bar of buttons shows up right over it. Copy it, open it, search it, translate it, run it as a command, send it to an AI, encode it, do maths on it. Whatever makes sense for whatever you grabbed. You never have to move your hands away from where you were already working.

It runs on X11 (Cinnamon, GNOME-on-X11, KDE, XFCE, MATE, take your pick) and natively on KDE Plasma 6 / Wayland. Free, open source, and nothing phones home.

> **Heads up if you're on Linux Mint / Cinnamon (or another X11 desktop):** treat 0.9.0 as a beta. Most of the testing and polish this round went into KDE Plasma 6 / Wayland. The X11 side still works (it's where LinuxPop started, after all), but I haven't re-checked every feature on real hardware yet, so you might hit a rough spot or two. If you do, [open an issue](https://github.com/GaimsDevSoftware/linuxpop/issues). That's genuinely the fastest way to get your setup sorted.

---

## What it does

- **Reads what you selected** and shows the buttons that fit: links, shell commands, file paths, emails, or just plain text.
- **Global hotkey** to pop the bar on your current selection from any app.
- **Tray icon** for settings, the plugin manager, and a quick on/off toggle.
- **Plugins are just `.py` files.** Drop one in `~/.config/linuxpop/plugins/`, or grab one from the built-in list.
- **Plenty come bundled:** Base64, JSON pretty-print, URL encode/decode, a calculator, case conversion, slugify, QR codes, in-place translate, send-to-AI, local Ollama, and run-in-terminal for things that look like commands.
- **Double-click a word** (while holding a modifier) to pop the bar. Works on real Wayland, not just XWayland.
- **Your text stays on your machine** unless a plugin obviously sends it somewhere. The "Send to Claude" button, for example, hands off to Claude via its `claude://` link (or your browser if that isn't set up).

---

## Install

### Flatpak (recommended)

It's a GPG-signed Flatpak served from its own auto-updating repo, so there's no Flathub account or approval standing in the way. One command:

```sh
flatpak install --from https://gaimsdevsoftware.github.io/linuxpop-flatpak/linuxpop.flatpakref
flatpak run io.github.GaimsDevSoftware.LinuxPop
```

New to Flatpak? Add the Flathub runtimes first:
`flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo`

Want a single file instead? Grab the `.flatpak` bundle from the
[latest release](https://github.com/GaimsDevSoftware/linuxpop/releases/latest) and run
`flatpak install --bundle linuxpop-*.flatpak`. There's a full walkthrough at
<https://gaimsdevsoftware.github.io/linuxpop-flatpak/>.

In the Flatpak build, "open file/folder" works (it gets read-only access to your home). Screen OCR isn't bundled yet; that's coming in a later release.

### Native packages

**Debian, Ubuntu, Linux Mint:** grab `linuxpop_*_all.deb` from the
[latest release](https://github.com/GaimsDevSoftware/linuxpop/releases/latest):

```sh
sudo apt install ./linuxpop_0.9.2_all.deb
```

**Fedora (COPR):** a native RPM that updates through `dnf`:

```sh
sudo dnf copr enable gaimsdevsoftware/linuxpop
sudo dnf install linuxpop
```

### From source

#### 1. System dependencies

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

Optional: `qrencode` for the QR plugin, `ollama` for the local-AI plugin.

#### 2. Install LinuxPop

```sh
git clone https://github.com/GaimsDevSoftware/linuxpop.git ~/linuxpop
cd ~/linuxpop
bash install.sh
```

`install.sh` sets up autostart and checks your dependencies. To start it right away without logging out:

```sh
python3 ~/linuxpop/main.py
```

To uninstall:

```sh
bash ~/linuxpop/install.sh --uninstall
```

---

## Using it

- Select text in any app and the popup appears above your selection.
- Press the hotkey (default `Super+Shift+Y`) and the popup appears at the cursor with whatever's selected.
- Hit `Esc`, click somewhere else, or just wait a few seconds and it goes away.
- The tray icon toggles auto-popup, opens Settings, and manages Plugins.

### Settings

Right-click the tray icon and pick **Settings**, or edit
`~/.config/linuxpop/settings.json` by hand.

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
| `double_click_popup_enabled` | `false` | Hold a modifier + double-click a word to pop the bar |
| `double_click_modifier` | `ctrl` | Modifier for the double-click trigger (`ctrl`/`alt`/`super`/`shift`) |
| `translate_target_lang` | `en` | Target language for the Translate plugin (ISO code) |
| `editable_atspi_listener_enabled` | `false` | Use AT-SPI for editable detection + selection geometry |
| `popup_anchor_to_selection` | `true` | Anchor popup to the selection rect (needs AT-SPI enabled) |

### Plugins

Open **Plugins…** from the tray menu to add or remove the built-in ones, or
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

## Known rough edges

- **Mint / Cinnamon and X11 are beta in 0.9.0.** The recent work went into KDE Plasma 6 / Wayland. The X11 backend (Cinnamon, GNOME-on-X11, XFCE, MATE) still runs, but it didn't get a full pass on real hardware this cycle. Expect the odd glitch, and please file issues. It's the quickest path to a solid 1.0.
- **Wayland support means KDE Plasma for now.** LinuxPop has a real Wayland backend on KDE Plasma 6: selection through `wl-clipboard`, popup placement through `gtk-layer-shell`, cursor position through KWin's scripting API. Other compositors (GNOME, wlroots) drop back to the X11 path under XWayland. There's more detail in [docs/FEDORA-KDE.md](docs/FEDORA-KDE.md). On KDE Wayland the global hotkey runs through KGlobalAccel (check it actually bound under System Settings, Shortcuts), paste goes through `ydotool`, and the active-window blocklist isn't wired up yet. Popup placement, hover persistence, and click-outside dismissal all work natively. You can anchor the popup to the selected text instead of the mouse if you turn on desktop accessibility (AT-SPI).
- **HiDPI** works, but I've mostly tested it at 2× scaling. File an issue if the popup lands in the wrong spot on your setup.
- **The odd input grab.** If another app is holding an X11 input grab (an open menu, say), the popup might not catch your outside-click right away.

---

## FAQ

**Is there a PopClip for Linux?**
That's pretty much what LinuxPop is. Highlight some text and a popup of actions shows up over it. It's free, MIT-licensed, and runs on X11 and KDE Plasma 6 Wayland.

**Does it work on Wayland?**
On KDE Plasma 6, yes: selection goes through `wl-clipboard`, the popup is placed with `gtk-layer-shell`, and the cursor position comes from KWin's scripting API. Other Wayland compositors fall back to the X11 path under XWayland.

**Which desktops are supported?**
Cinnamon, GNOME-on-X11, KDE, XFCE and MATE on X11, plus KDE Plasma 6 on Wayland.

**Does it send my text anywhere?**
No, not unless a plugin clearly does (the "Send to Claude" button being the obvious one). Otherwise everything stays on your machine. No telemetry, no accounts.

**How do I uninstall it?**
`bash ~/linuxpop/install.sh --uninstall` for a source install, or remove the Flatpak or native package the usual way.

## License

MIT. See [LICENSE](LICENSE).
