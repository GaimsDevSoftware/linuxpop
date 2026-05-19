"""Base class for LinuxPop plugins.

A plugin exposes one action: a button in the popup. It declares which
content types it cares about, which icon and label to render, and what
to do when the user clicks it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from classifier import ContentType


@dataclass
class Plugin:
    name: str
    icon: str
    tooltip: str
    handler: Callable[[str], None]
    content_types: Iterable[ContentType] = field(default_factory=tuple)
    priority: int = 100

    def handles(self, content_type: ContentType) -> bool:
        if not self.content_types:
            return True
        return content_type in self.content_types

    def execute(self, text: str) -> None:
        self.handler(text)
