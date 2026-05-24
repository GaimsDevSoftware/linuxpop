"""Is the currently-focused widget editable?

LinuxPop uses this to hide actions like Cut / Paste / Backspace / Bold
when the user has selected text in a read-only context. Showing them
there would just be a tease.

Two-layer detection:
  1. AT-SPI — per-widget probe. Needed for apps where the same window
     has both editable and read-only areas (Claude desktop, Slack,
     Discord, web browsers). Time-bounded so it can't freeze the
     popup path. Returns three states: True / False / None (no answer).
  2. WM_CLASS blocklist — coarse but predictable. Catches pure-viewer
     apps that don't respond to AT-SPI (Evince, image viewers, file
     managers, media players). Reads via xprop because xdotool's
     getwindowclassname is missing on several distro builds.

Strategy: trust AT-SPI when it answers. Otherwise consult the blocklist.
Default to True (editable) when neither produces a signal — better to
show a button that does nothing than to hide one the user wanted.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading

_log = logging.getLogger("linuxpop")

# AT-SPI is optional. If gi bindings aren't installed we skip to the
# WM_CLASS fallback only — no hard dependency.
_HAS_ATSPI = False
try:
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi  # type: ignore[attr-defined]
    _HAS_ATSPI = True
except (ImportError, ValueError):
    _log.info("[editable] Atspi gi bindings unavailable — "
              "will use WM_CLASS heuristic only")


# WM_CLASS substrings (case-insensitive) for apps where the focused
# widget is virtually always read-only.
_READONLY_APP_CLASSES = (
    # PDF / document viewers
    "evince", "okular", "atril", "qpdfview", "mupdf", "xpdf", "zathura",
    # Image viewers
    "feh", "geeqie", "gthumb", "eog", "gpicview", "qiv", "nomacs",
    # Media players
    "mpv", "vlc", "smplayer", "totem", "celluloid",
    # File managers (rename in-place exists but it's a tiny corner of usage)
    "nautilus", "nemo", "caja", "thunar", "pcmanfm", "dolphin",
    # Read-only ebook readers
    "calibre", "fbreader",
)


def _find_focused_in(node, max_depth: int = 10):
    """Depth-limited DFS for an accessible with STATE_FOCUSED. Bounded
    so a huge accessibility tree can't outrun our timeout budget."""
    if node is None or max_depth <= 0:
        return None
    try:
        state = node.get_state_set()
        if state.contains(Atspi.StateType.FOCUSED):
            return node
        for i in range(node.get_child_count()):
            child = node.get_child_at_index(i)
            found = _find_focused_in(child, max_depth - 1)
            if found is not None:
                return found
    except Exception:
        return None
    return None


def _atspi_focus_editable(timeout: float = 0.15) -> bool | None:
    """Probe AT-SPI for the editable state of the currently-focused
    accessible widget.

    Strategy (matters!): walk every app on the desktop, but only look
    inside its windows that have STATE_ACTIVE set. STATE_ACTIVE marks
    the WM-foreground window — exactly one across the whole desktop.
    Inside it, find the descendant with STATE_FOCUSED; that's the
    widget the user is typing/selecting in.

    The previous implementation looked for STATE_FOCUSED anywhere in
    any app's tree and returned the FIRST hit. On Cinnamon the shell's
    own panel/desktop window is permanently marked FOCUSED (it's the
    GNOME-shell-equivalent root accessible), so we always returned
    'cinnamon: editable=False' regardless of which real app the user
    was in. Filtering on STATE_ACTIVE first skips that trap.

    Returns True / False on an authoritative AT-SPI answer, None when
    we couldn't reach one (no AT-SPI, timeout, no active window, etc.)
    — caller falls back to the WM_CLASS heuristic on None.
    """
    if not _HAS_ATSPI:
        return None

    result: list[bool | None] = [None]
    diag: list[str] = []

    def worker() -> None:
        try:
            desktop = Atspi.get_desktop(0)
            if desktop is None:
                diag.append("desktop=None")
                return
            n_apps = desktop.get_child_count()
            for i in range(n_apps):
                try:
                    app = desktop.get_child_at_index(i)
                except Exception:
                    continue
                if app is None:
                    continue
                # Iterate the app's top-level windows; pick the one
                # whose window has STATE_ACTIVE (= WM-frontmost).
                try:
                    n_win = app.get_child_count()
                except Exception:
                    continue
                for j in range(n_win):
                    try:
                        win = app.get_child_at_index(j)
                    except Exception:
                        continue
                    if win is None:
                        continue
                    try:
                        win_state = win.get_state_set()
                    except Exception:
                        continue
                    if not win_state.contains(Atspi.StateType.ACTIVE):
                        continue
                    # Active window found. Find focused descendant.
                    focused = _find_focused_in(win, max_depth=10)
                    if focused is None:
                        # Window is active but accessibility tree doesn't
                        # expose a STATE_FOCUSED widget. Common with
                        # Electron apps (Claude desktop, VSCode, Slack)
                        # whose Chromium layer can't always be drilled
                        # into via at-spi2. The window-frame itself isn't
                        # 'editable' even when its embedded input is, so
                        # checking the frame would lie. Return None to
                        # punt to WM_CLASS heuristic, which defaults to
                        # editable=True for unknown apps and lets the
                        # buttons show.
                        diag.append(
                            f"app={app.get_name()!r} "
                            f"win.role={win.get_role_name()} "
                            f"-- no focused descendant (likely Electron) "
                            f"-- punting to WM_CLASS"
                        )
                        return  # result[0] stays None
                    try:
                        fstate = focused.get_state_set()
                        editable = bool(
                            fstate.contains(Atspi.StateType.EDITABLE))
                        result[0] = editable
                        diag.append(
                            f"app={app.get_name()!r} "
                            f"win.role={win.get_role_name()} "
                            f"focused.role={focused.get_role_name()} "
                            f"editable={editable}"
                        )
                    except Exception as exc:
                        diag.append(f"state-fetch failed: {exc}")
                    return
            diag.append(f"no active window across {n_apps} apps")
        except Exception as exc:
            diag.append(f"atspi error: {exc}")

    t = threading.Thread(target=worker, daemon=True,
                         name="linuxpop-atspi-probe")
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        _log.info("[editable] AT-SPI probe timed out (>%.0f ms)",
                  timeout * 1000)
        return None
    if diag:
        _log.info("[editable] AT-SPI: %s", " | ".join(diag))
    return result[0]


def _wm_class_lower() -> str:
    """Return the focused window's WM_CLASS in lower-case, or '' on failure.

    Reads via xprop, NOT `xdotool getwindowclassname` — the latter doesn't
    exist on all xdotool builds (older Debian/Mint ship a version that
    refuses the command, returning empty silently and breaking every
    'is the focused widget editable?' check). xprop is part of x11-utils
    and present on every X11 desktop we care about.
    """
    if not shutil.which("xdotool") or not shutil.which("xprop"):
        return ""
    try:
        wid = subprocess.run(
            ["xdotool", "getactivewindow"],
            capture_output=True, text=True, timeout=0.3,
        ).stdout.strip()
        if not wid:
            return ""
        out = subprocess.run(
            ["xprop", "-id", wid, "WM_CLASS"],
            capture_output=True, text=True, timeout=0.3,
        ).stdout.strip()
        # xprop format: WM_CLASS(STRING) = "instance", "Class"
        # Lower-case everything and let the blocklist substring-match
        # either field — apps with different instance vs class names
        # (Firefox: "Navigator", "firefox") fail with class-only checks.
        return out.lower()
    except (OSError, subprocess.SubprocessError):
        return ""


def is_focus_editable(extra_readonly_classes: tuple[str, ...] = ()) -> bool:
    """True if the focused widget accepts edits.

    Resolution order:
      1. AT-SPI says editable=False   → hide edit buttons
      2. AT-SPI says editable=True    → show edit buttons
      3. AT-SPI silent + WM_CLASS in blocklist → hide
      4. AT-SPI silent + WM_CLASS unknown      → show (permissive)
      5. AT-SPI silent + WM_CLASS not in blocklist → show

    Logs every decision so the user can tail
    ~/.cache/linuxpop/linuxpop.log and pinpoint which path fired.
    """
    atspi_answer = _atspi_focus_editable()
    if atspi_answer is not None:
        _log.info("[editable] AT-SPI authoritative: editable=%s", atspi_answer)
        return atspi_answer

    wm = _wm_class_lower()
    blocklist = _READONLY_APP_CLASSES + tuple(
        c.lower() for c in extra_readonly_classes
    )
    if not wm:
        _log.info("[editable] no WM_CLASS + no AT-SPI signal — defaulting to True")
        return True
    for needle in blocklist:
        if needle in wm:
            _log.info("[editable] WM_CLASS=%r matched read-only entry %r "
                      "(AT-SPI silent) — hiding edit-only plugins", wm, needle)
            return False
    _log.info("[editable] WM_CLASS=%r (AT-SPI silent) — treating as editable=True", wm)
    return True
