#!/usr/bin/env python3
"""Regression tests for the minimum-selection-length filter boundary.

The "skip short selections" knob (min_selection_length_enabled +
min_selection_length) must only ever gate the *auto-popup on selection*
path. The two double-click-in-empty-field and hotkey paths show the
edit menu directly and must NOT be affected by it.

These tests pin that boundary so a future refactor can't quietly leak
the filter into the no-selection popup path.

They exercise the App methods in isolation against a stub `self`, so no
X11/GTK main loop or real backend is needed.
"""
import sys
import types

import pytest

# The app imports GTK via gobject-introspection; skip cleanly where that
# (or a display) isn't available, same as the other GUI-touching test.
pytest.importorskip("gi")
sys.argv = ["test"]
try:
    import main
except Exception as exc:  # pragma: no cover - environment without GTK/display
    pytest.skip(f"cannot import main ({exc})", allow_module_level=True)


class FakeSettings:
    """Minimal stand-in for the settings singleton."""

    def __init__(self, **values):
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


# Settings that would suppress every short selection on the watcher path.
AGGRESSIVE_MIN = dict(
    min_selection_length_enabled=True,
    min_selection_length=100,
    blocklist_patterns=[],
    clipboard_history_enabled=True,
    selection_debounce_ms=0,
)


def _double_click_stub():
    return types.SimpleNamespace(
        _show_no_selection_popup=_Recorder(),
    )


class _Recorder:
    """Callable that records the calls made to it."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


# --- the actual regression: empty-field double-click ignores the filter ----

def test_double_click_empty_field_fires_despite_min_length(monkeypatch):
    """ctrl+double-click in an editable empty field must still pop the edit
    menu even with an aggressive minimum-character setup."""
    monkeypatch.setattr(main, "is_focus_editable", lambda *a, **k: True)
    stub = _double_click_stub()

    main.App._on_global_double_click(stub, 11, 22)

    assert stub._show_no_selection_popup.calls == [((11, 22), {})], \
        "empty-field double-click must open the no-selection popup"


def test_double_click_non_editable_does_nothing(monkeypatch):
    """The only gate on the double-click path is 'is the focus editable',
    never the minimum-length setting."""
    monkeypatch.setattr(main, "is_focus_editable", lambda *a, **k: False)
    stub = _double_click_stub()

    main.App._on_global_double_click(stub, 11, 22)

    assert stub._show_no_selection_popup.calls == []


def test_no_selection_popup_builds_menu_with_min_length_on(monkeypatch):
    """_show_no_selection_popup never consults the min-length filter: it
    shows the edit menu whenever the active window isn't blocklisted."""
    monkeypatch.setattr(main, "_active_window_blocked", lambda patterns: False)
    popup = types.SimpleNamespace(show_actions=_Recorder())
    stub = types.SimpleNamespace(
        settings=FakeSettings(**AGGRESSIVE_MIN),
        popup=popup,
        _on_clipboard_hotkey=lambda *a, **k: None,
    )

    main.App._show_no_selection_popup(stub, 5, 6)

    assert len(popup.show_actions.calls) == 1, "the edit menu must be shown"
    (items, x, y), _ = popup.show_actions.calls[0]
    assert (x, y) == (5, 6)
    assert items, "the edit menu must contain at least one action"


def test_no_selection_popup_still_respects_blocklist(monkeypatch):
    """Contrast: the blocklist IS allowed to suppress the popup. This keeps
    the test honest about what does and doesn't gate this path."""
    monkeypatch.setattr(main, "_active_window_blocked", lambda patterns: True)
    popup = types.SimpleNamespace(show_actions=_Recorder())
    stub = types.SimpleNamespace(
        settings=FakeSettings(**AGGRESSIVE_MIN),
        popup=popup,
    )

    main.App._show_no_selection_popup(stub, 5, 6)

    assert popup.show_actions.calls == []


# --- the other side of the boundary: the watcher path DOES filter ----------

def _capture_watcher_callback(monkeypatch, stub):
    """Run _start_watcher against a fake backend and return the on_selection
    callback it registered."""
    captured = {}

    class FakeWatcher:
        def __init__(self, cb):
            captured["cb"] = cb

        def start(self):
            pass

        def stop(self):
            pass

    fake_backend = types.SimpleNamespace(
        make_selection_watcher=lambda cb, debounce: FakeWatcher(cb),
    )
    monkeypatch.setattr(main, "get_backend", lambda: fake_backend)
    main.App._start_watcher(stub)
    return captured["cb"]


def test_watcher_path_filters_short_selection(monkeypatch):
    scheduled = []
    monkeypatch.setattr(main.GLib, "idle_add", lambda *a, **k: scheduled.append(a))
    stub = types.SimpleNamespace(
        watcher=None,
        _watcher_active=False,
        min_len=100,
        settings=FakeSettings(**AGGRESSIVE_MIN),
        _show_for_text=lambda *a, **k: None,
    )

    on_selection = _capture_watcher_callback(monkeypatch, stub)
    on_selection("ab", 1, 2)  # 2 chars < 100, filter enabled

    assert scheduled == [], "short selection must be filtered on the watcher path"


def test_watcher_path_shows_when_filter_disabled(monkeypatch):
    scheduled = []
    monkeypatch.setattr(main.GLib, "idle_add", lambda *a, **k: scheduled.append(a))
    settings = dict(AGGRESSIVE_MIN)
    settings["min_selection_length_enabled"] = False
    stub = types.SimpleNamespace(
        watcher=None,
        _watcher_active=False,
        min_len=100,
        settings=FakeSettings(**settings),
        _show_for_text=lambda *a, **k: None,
    )

    on_selection = _capture_watcher_callback(monkeypatch, stub)
    on_selection("ab", 1, 2)

    assert len(scheduled) == 1, "with the filter off, the popup must be scheduled"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
