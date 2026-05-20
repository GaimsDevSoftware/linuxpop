"""Base class for LinuxPop plugins.

A plugin exposes one action: a button in the popup. It declares which
content types it cares about, which icon and label to render, and what
to do when the user clicks it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from classifier import ContentType


@dataclass
class Plugin:
    name: str
    icon: str
    tooltip: str
    handler: Callable[[str], None]
    content_types: Iterable[ContentType] = field(default_factory=tuple)
    priority: int = 100
    # Optional fine-grained gate: if set, the button is only shown when the
    # predicate returns True for the actual selection text. Used to hide e.g.
    # 'URL decode' on text that contains no %-escapes. Exceptions in the
    # predicate are treated as False (skip the plugin) — predicates run on
    # every popup show, so they must be cheap.
    predicate: Optional[Callable[[str], bool]] = None

    def handles(self, content_type: ContentType) -> bool:
        if not self.content_types:
            return True
        return content_type in self.content_types

    def matches(self, text: str) -> bool:
        """Return True if the plugin should appear for this specific text.
        Called after handles() — content-type filtering is the coarse gate,
        the predicate is the fine-grained one. No predicate means 'always
        matches'."""
        if self.predicate is None:
            return True
        try:
            return bool(self.predicate(text))
        except Exception:
            return False

    def execute(self, text: str) -> None:
        self.handler(text)
