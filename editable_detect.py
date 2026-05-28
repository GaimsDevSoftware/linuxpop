"""Is the currently-focused widget editable?

LinuxPop uses this to hide actions like Cut / Paste / Backspace / Bold
when the user has selected text in a read-only context. Showing them
there would just be a tease.

Two-layer detection:
  1. AT-SPI - per-widget probe. Needed for apps where the same window
     has both editable and read-only areas (Claude desktop, Slack,
     Discord, web browsers). Time-bounded so it can't freeze the
     popup path. Returns three states: True / False / None (no answer).
  2. WM_CLASS blocklist - coarse but predictable. Catches pure-viewer
     apps that don't respond to AT-SPI (Evince, image viewers, file
     managers, media players). Reads via xprop because xdotool's
     getwindowclassname is missing on several distro builds.

Strategy: trust AT-SPI when it answers. Otherwise consult the blocklist.
Default to True (editable) when neither produces a signal - better to
show a button that does nothing than to hide one the user wanted.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time as _time

_log = logging.getLogger("linuxpop")

# Cache of the latest focus event from the AT-SPI listener. Updated
# asynchronously every time any accessible reports gaining STATE_FOCUSED.
# This is the ONLY way to get useful per-widget signal out of Chromium /
# Electron apps - their accessibility tree exposes the top-level frame
# without STATE_FOCUSED propagation, so synchronous tree-walks return
# 'no focused descendant' and we have to fall back to WM_CLASS. The
# event bridge does fire when the user clicks an editable element vs
# the page body, even though the tree doesn't reflect it cleanly.
_focus_cache: dict = {
    "editable": None,   # True / False / None (no event yet)
    "timestamp": 0.0,   # monotonic time of last update
    "role": "",         # for diagnostics
}
_focus_cache_lock = threading.Lock()
_focus_listener_started = False
_focus_listener_ref = None  # keep listener alive (GC would otherwise drop it)

# AT-SPI is optional. If gi bindings aren't installed we skip to the
# WM_CLASS fallback only - no hard dependency.
#
# Defensive probe: before importing Atspi, check that the user's a11y
# bus socket exists at the canonical XDG path AND we can read it.
# Without this guard, a stale root-owned at-spi-bus-launcher on the
# system can cause our Atspi calls to hard-crash via glib's
# dbind-ERROR (SIGTRAP, uncatchable from Python). The probe sees the
# unreachable socket and lets us silently fall back to WM_CLASS.
_HAS_ATSPI = False
try:
    import os as _os
    _runtime_dir = (_os.environ.get("XDG_RUNTIME_DIR")
                    or f"/run/user/{_os.getuid()}")
    _bus_socket = _os.path.join(_runtime_dir, "at-spi", "bus_0")
    _bus_reachable = (_os.path.exists(_bus_socket)
                      and _os.access(_bus_socket, _os.R_OK | _os.W_OK))
except Exception:
    _bus_reachable = False

if _bus_reachable:
    try:
        import gi
        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # type: ignore[attr-defined]
        _HAS_ATSPI = True
    except (ImportError, ValueError):
        _log.info("[editable] Atspi gi bindings unavailable - "
                  "will use WM_CLASS heuristic only")
else:
    _log.info("[editable] at-spi bus socket not reachable at %s - "
              "using WM_CLASS heuristic only (this is normal if a "
              "stale root-owned at-spi-bus-launcher is on the system)",
              _bus_socket if 'bus_socket' in dir() else "(unknown path)")


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


def _on_focus_event(event) -> None:
    """AT-SPI calls this every time an accessible reports gaining or
    losing STATE_FOCUSED. We only care about gains (detail1 == 1) and
    we cache the editable status of the newly-focused widget.

    Runs in the AT-SPI worker thread - _focus_cache_lock protects the
    cross-thread read in is_focus_editable().
    """
    try:
        if event is None or event.source is None:
            return
        # detail1 is 1 for 'focused gained', 0 for 'focused lost'
        if int(getattr(event, "detail1", 0)) != 1:
            return
        source = event.source
        state = source.get_state_set()
        editable = bool(state.contains(Atspi.StateType.EDITABLE))
        try:
            role = source.get_role_name()
        except Exception:
            role = "?"
        with _focus_cache_lock:
            _focus_cache["editable"] = editable
            _focus_cache["timestamp"] = _time.monotonic()
            _focus_cache["role"] = role
        _log.info("[editable] focus-event: role=%s editable=%s", role, editable)
    except Exception as exc:
        _log.info("[editable] focus-event handler error: %s", exc)


def _start_focus_listener() -> None:
    """Register the AT-SPI focus-change listener. Idempotent - first
    caller wins, the listener runs for the daemon's lifetime.

    Gated on the 'editable_atspi_listener_enabled' setting, which
    defaults off. Registration was correlated with a Cinnamon panel
    segfault on 2026-05-25 - see knowledge/linuxpop.md. Falling back
    to the synchronous tree-walk + WM_CLASS path is safe; users who
    want the Electron-specific smartness can opt in.
    """
    global _focus_listener_started, _focus_listener_ref
    if _focus_listener_started or not _HAS_ATSPI:
        return
    # Local import - avoids settings module circulars at module load.
    try:
        from settings import get_settings
        if not bool(get_settings().get("editable_atspi_listener_enabled")):
            _log.info("[editable] AT-SPI focus listener disabled by setting "
                      "(editable_atspi_listener_enabled=false)")
            return
    except Exception as exc:
        _log.info("[editable] could not read AT-SPI listener setting (%s) - "
                  "leaving listener off", exc)
        return
    _focus_listener_started = True

    def runner():
        try:
            # init() is mostly a no-op after first call but guarantees
            # the DBus connection is up before we register listeners.
            try:
                Atspi.init()
            except Exception:
                pass
            listener = Atspi.EventListener.new(_on_focus_event)
            listener.register("object:state-changed:focused")
            # Keep the Python wrapper alive so it isn't GC'd out
            # from under the C-side registration.
            global _focus_listener_ref
            _focus_listener_ref = listener
            _log.info("[editable] AT-SPI focus listener registered - "
                      "starting event loop")
            # Blocking call - runs the AT-SPI event dispatch loop.
            Atspi.event_main()
        except Exception as exc:
            _log.info("[editable] AT-SPI listener init failed: %s", exc)

    threading.Thread(target=runner, daemon=True,
                     name="linuxpop-atspi-listener").start()


def _cached_focus_editable(max_age_s: float = 30.0) -> bool | None:
    """Return the cached editable state if a focus event has fired
    recently enough. None means 'no usable cache value'.

    max_age_s caps how long we trust the cache - if the user hasn't
    interacted in a while, their last focus event might no longer
    reflect reality (they could have switched apps without us seeing).
    Recent focus events stay authoritative.
    """
    with _focus_cache_lock:
        editable = _focus_cache["editable"]
        ts = _focus_cache["timestamp"]
        role = _focus_cache["role"]
    if editable is None:
        return None
    age = _time.monotonic() - ts
    if age > max_age_s:
        return None
    _log.info("[editable] using cached focus event (%.1f s old): "
              "role=%s editable=%s", age, role, editable)
    return editable


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
    the WM-foreground window - exactly one across the whole desktop.
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
    - caller falls back to the WM_CLASS heuristic on None.
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

    Reads via xprop, NOT `xdotool getwindowclassname` - the latter doesn't
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
        # either field - apps with different instance vs class names
        # (Firefox: "Navigator", "firefox") fail with class-only checks.
        return out.lower()
    except (OSError, subprocess.SubprocessError):
        return ""


def is_focus_editable(extra_readonly_classes: tuple[str, ...] = ()) -> bool:
    """True if the focused widget accepts edits.

    Resolution order, most-precise first:
      1. AT-SPI focus-event cache (async listener) - works for Electron
         apps where the tree-walk fails. Recent events authoritative.
      2. AT-SPI tree walk (synchronous probe) - works for GTK/Qt/Firefox.
      3. WM_CLASS blocklist - coarse but predictable, catches pure-viewer
         apps that don't expose accessibility at all.
      4. Permissive default (True) - better to show a button that does
         nothing than to hide one the user wanted.

    Logs every decision so a tail of linuxpop.log shows which path fired.
    """
    # Make sure the async listener is up; first call kicks it off.
    _start_focus_listener()

    cached = _cached_focus_editable()
    if cached is not None:
        return cached

    atspi_answer = _atspi_focus_editable()
    if atspi_answer is not None:
        _log.info("[editable] AT-SPI tree-walk authoritative: editable=%s",
                  atspi_answer)
        return atspi_answer

    wm = _wm_class_lower()
    blocklist = _READONLY_APP_CLASSES + tuple(
        c.lower() for c in extra_readonly_classes
    )
    if not wm:
        _log.info("[editable] no WM_CLASS + no AT-SPI signal - defaulting to True")
        return True
    for needle in blocklist:
        if needle in wm:
            _log.info("[editable] WM_CLASS=%r matched read-only entry %r "
                      "(AT-SPI silent) - hiding edit-only plugins", wm, needle)
            return False
    _log.info("[editable] WM_CLASS=%r (AT-SPI silent, no cached event) "
              "- treating as editable=True", wm)
    return True
