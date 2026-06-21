"""Single source of truth for the LinuxPop version string.

Resolution order (first hit wins):

  1. A baked ``_version.py`` (``VERSION = "x.y.z"``) sitting next to this file.
     It is generated from the git tag at *package build time*
     (see ``packaging/gen-version.sh``) and is what installed / Flatpak / .deb
     copies use, since those have no ``.git`` to inspect.
  2. ``git describe --tags`` when running straight from a git checkout, i.e.
     during development. Gives ``"0.9.7"`` on a tagged commit, or
     ``"0.9.7-3-gabc1234"`` a few commits later so dev builds are identifiable.
  3. The newest ``<release>`` in the AppStream metainfo, if reachable.
  4. ``"0.0.0"`` as a last resort.

Because (1) is generated from the git tag and (2) reads the tag directly, the
About dialog, ``--version`` output and the crash-log header can no longer drift
from the actual release: bump the tag and everything follows.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _from_baked() -> str | None:
    try:
        from _version import VERSION  # type: ignore
    except Exception:
        return None
    return VERSION or None


def _from_git() -> str | None:
    if not (_HERE / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=_HERE,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    tag = out.stdout.strip()
    if not tag:
        return None
    return tag[1:] if tag.startswith("v") else tag


def _from_metainfo() -> str | None:
    import re

    candidates = (
        _HERE / "packaging" / "io.github.GaimsDevSoftware.LinuxPop.metainfo.xml",
        _HERE.parent / "metainfo" / "io.github.GaimsDevSoftware.LinuxPop.metainfo.xml",
    )
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r'<release[^>]*\bversion="([^"]+)"', text)
        if match:
            return match.group(1)
    return None


def get_version() -> str:
    return _from_baked() or _from_git() or _from_metainfo() or "0.0.0"


__version__ = get_version()
