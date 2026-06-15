"""Where LinuxPop keeps its files.

This honours the XDG base-directory variables ($XDG_CONFIG_HOME,
$XDG_CACHE_HOME, $XDG_DATA_HOME) and only falls back to ~/.config,
~/.cache and ~/.local/share when they are unset.

On a normal desktop these resolve to exactly the paths LinuxPop has
always used, so nothing moves for existing installs. The reason this
module exists is the Flatpak build: there the sandbox points those
variables at the app's own private, persistent per-app folders. If we
hardcode ~/.config instead, the writes land in a throwaway tmpfs that
the sandbox wipes on exit, so settings, recipes, snippets and clipboard
history all vanish on the next launch. Reading the variables is what
makes the user's setup actually stick.
"""
from __future__ import annotations

import os
from pathlib import Path

APP = "linuxpop"


def _base(env_var: str, fallback: str) -> Path:
    val = (os.environ.get(env_var) or "").strip()
    if val and os.path.isabs(val):
        return Path(val)
    return Path(os.path.expanduser(fallback))


CONFIG_HOME = _base("XDG_CONFIG_HOME", "~/.config")
CACHE_HOME = _base("XDG_CACHE_HOME", "~/.cache")
DATA_HOME = _base("XDG_DATA_HOME", "~/.local/share")

# LinuxPop's own per-user folders.
CONFIG_DIR = CONFIG_HOME / APP
CACHE_DIR = CACHE_HOME / APP
DATA_DIR = DATA_HOME / APP
