# Contributing to LinuxPop

Thanks for your interest! LinuxPop is a small, hackable tool — most useful
contributions will be plugins.

## Writing a plugin

A plugin is a single Python file in `~/.config/linuxpop/plugins/` that defines
a top-level `register(register_plugin)` function. LinuxPop discovers it on
startup (and after install via the Plugin Manager).

### Minimal example

```python
"""Example plugin: shout the selected text into a notification."""
import subprocess

from classifier import ContentType
from plugin_base import Plugin


def _shout(text: str) -> None:
    subprocess.run(
        ["notify-send", "-i", "face-surprise", "SHOUT", text.upper()],
        check=False,
    )


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="shout",
        icon="face-surprise-symbolic",
        tooltip="SHOUT IT",
        handler=_shout,
        content_types=(ContentType.PLAIN_TEXT,),
        priority=50,
    ))
```

Save as `~/.config/linuxpop/plugins/shout.py` and restart LinuxPop. Select
text → popup shows your new button.

### Plugin API

`Plugin` is a dataclass from `plugin_base`:

| Field | Type | Meaning |
|---|---|---|
| `name` | `str` | Unique identifier (used in logs) |
| `icon` | `str` | Theme icon name (try the [freedesktop icon spec](https://specifications.freedesktop.org/icon-naming-spec/icon-naming-spec-latest.html)) |
| `tooltip` | `str` | Hover text on the popup button |
| `handler` | `Callable[[str], None]` | Called with the selected text when clicked |
| `content_types` | `Iterable[ContentType]` | `()` for all types, or specific values |
| `priority` | `int` | Lower numbers appear first in the popup (default 100) |

`ContentType` values: `COMMAND`, `URL`, `EMAIL`, `PATH`, `PLAIN_TEXT`. See
`classifier.py` for the classification rules.

### Tips

- **Run blocking work on a thread** — the handler is called on the GTK main
  loop. Spawn a `threading.Thread` for HTTP requests, subprocess calls that
  may hang, etc. Use `notify-send` to communicate results.
- **Copy results to the clipboard** with
  `subprocess.run(["xclip", "-selection", "clipboard"], input=text.encode())`
  so the user can paste them.
- **Use existing icons** — most desktops ship the [hicolor / Adwaita icon
  themes](https://developer.gnome.org/hig/reference/icons.html). Use
  `-symbolic` suffix for monochrome icons that pick up theme colors.
- **Don't depend on a specific path layout** — `classifier`, `plugin_base`,
  `actions` and `settings` are importable because plugin_loader puts the
  LinuxPop directory on `sys.path`. Don't add `sys.path.insert` to your
  plugin.

### Submitting a plugin to the built-in catalogue

Open a PR adding:
1. Your plugin file to `plugins_repo/your_plugin.py`
2. An entry in `plugins_repo/manifest.json`:
   ```json
   {
     "file": "your_plugin.py",
     "title": "Human-readable title",
     "description": "One-sentence summary shown in the manager",
     "tags": ["category", "more-tags"]
   }
   ```

External system dependencies (e.g. your plugin needs `imagemagick` installed)
should fail gracefully — `shutil.which("convert")` check and a `notify-send`
explaining what's missing.

## Bug reports & feature requests

Please file an issue. Include:
- Distro + desktop environment (`echo $XDG_CURRENT_DESKTOP`)
- Output of `python3 main.py --debug`
- Steps to reproduce

## Pull requests

- Keep PRs focused — one concern per PR
- Match the existing code style (PEP 8, type hints where possible)
- Don't add user-facing strings in languages other than English
- For new top-level features, update README.md
