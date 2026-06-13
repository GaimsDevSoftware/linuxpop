"""Toggle whether LinuxPop launches at login.

Writes / removes ~/.config/autostart/linuxpop.desktop. Honoured by every
freedesktop-spec desktop environment (Cinnamon, GNOME, KDE Plasma, XFCE,
MATE, Budgie, etc.).

Used from the Settings dialog (Settings -> Activation -> Start at login)
so the user doesn't have to know where the file lives or edit it by hand.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

AUTOSTART_DIR = Path(os.path.expanduser("~/.config/autostart"))
AUTOSTART_FILE = AUTOSTART_DIR / "linuxpop.desktop"


def _desktop_file_contents(exec_path: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=LinuxPop\n"
        "Comment=PopClip-inspired floating action popup\n"
        f"Exec={exec_path}\n"
        "Icon=linuxpop\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "X-GNOME-Autostart-enabled=true\n"
        "StartupNotify=false\n"
    )


def is_enabled() -> bool:
    """True if the autostart .desktop file exists."""
    return AUTOSTART_FILE.is_file()


def set_enabled(enabled: bool, main_py: Path | None = None) -> bool:
    """Create or remove the autostart .desktop file. Returns True on
    success. `main_py` defaults to the currently-running main.py path
    so a user who runs LinuxPop from a non-standard checkout still gets
    their copy on autostart, not /usr/bin/linuxpop."""
    try:
        if enabled:
            AUTOSTART_DIR.mkdir(parents=True, exist_ok=True)
            if main_py is None:
                # Resolve to the actual main.py we were invoked from
                main_py = Path(sys.argv[0]).resolve()
                if main_py.name != "main.py":
                    main_py = Path(__file__).resolve().parent / "main.py"
            # Quote the path so checkouts in directories with spaces
            # (e.g. ~/My Code/linuxpop/) don't produce a broken Exec line
            # that the DE silently fails to launch.
            from shlex import quote as _q
            exec_line = f"/usr/bin/python3 {_q(str(main_py))}"
            AUTOSTART_FILE.write_text(
                _desktop_file_contents(exec_line),
                encoding="utf-8",
            )
            # Make it executable so older DEs that check x-bit honour it
            AUTOSTART_FILE.chmod(0o755)
        else:
            if AUTOSTART_FILE.is_file():
                AUTOSTART_FILE.unlink()
        return True
    except OSError:
        return False
