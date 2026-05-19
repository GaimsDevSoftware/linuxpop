"""Two practical converters: Unix timestamp ↔ ISO date, and color formats."""
from __future__ import annotations

import datetime as dt
import re
import subprocess

from classifier import ContentType
from plugin_base import Plugin


def _copy(text: str, label: str) -> None:
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode("utf-8"), check=False,
    )
    subprocess.run(
        ["notify-send", "-i", "preferences-system-time-symbolic", label, text[:200]],
        check=False,
    )


def _timestamp_convert(text: str) -> None:
    """Auto-detect: number → ISO 8601 in local time; ISO/date string → Unix epoch."""
    s = text.strip()
    # Try as numeric epoch
    try:
        value = float(s)
        # Heuristic: > 10^11 means milliseconds, else seconds
        if value > 1e11:
            value = value / 1000.0
        when = dt.datetime.fromtimestamp(value).astimezone()
        _copy(when.isoformat(), f"From epoch {s}")
        return
    except ValueError:
        pass
    # Try as ISO date / RFC-ish
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            when = dt.datetime.strptime(s, fmt)
            if when.tzinfo is None:
                when = when.astimezone()
            _copy(str(int(when.timestamp())), f"To epoch from {s}")
            return
        except ValueError:
            continue
    subprocess.run(
        ["notify-send", "-i", "dialog-error", "Timestamp converter",
         f"Couldn't parse {s!r} as a timestamp or epoch"],
        check=False,
    )


_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGB_RE = re.compile(r"^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$", re.IGNORECASE)


def _color_convert(text: str) -> None:
    """#hex ↔ rgb(r,g,b)."""
    s = text.strip()
    m = _HEX_RE.match(s)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        _copy(f"rgb({r}, {g}, {b})", f"#{h} → rgb")
        return
    m = _RGB_RE.match(s)
    if m:
        r, g, b = (max(0, min(255, int(v))) for v in m.groups())
        _copy(f"#{r:02x}{g:02x}{b:02x}", f"rgb → hex")
        return
    subprocess.run(
        ["notify-send", "-i", "dialog-error", "Color converter",
         "Couldn't recognise as #hex or rgb(r,g,b)"],
        check=False,
    )


def register(register_plugin) -> None:
    types = (ContentType.PLAIN_TEXT,)
    register_plugin(Plugin(name="timestamp-convert", icon="preferences-system-time-symbolic",
        tooltip="Timestamp ↔ ISO", handler=_timestamp_convert, content_types=types, priority=170))
    register_plugin(Plugin(name="color-convert", icon="preferences-color-symbolic",
        tooltip="Color hex ↔ rgb", handler=_color_convert, content_types=types, priority=171))
