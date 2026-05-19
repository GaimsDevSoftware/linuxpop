"""Classifies selected text so the popup can show context-appropriate actions."""
from __future__ import annotations

import re
import unicodedata
from enum import Enum

# Invisible / formatting characters that often sneak in from rich-text copies
# (object replacement, zero-width spaces, BOM, soft hyphen, etc.)
_INVISIBLE_CHARS = "￼​‌‍⁠﻿­᠎"


class ContentType(Enum):
    COMMAND = "command"
    URL = "url"
    EMAIL = "email"
    PATH = "path"
    PLAIN_TEXT = "plain_text"


_URL_RE = re.compile(r"^(https?|ftp|file)://\S+$", re.IGNORECASE)
# Protocol-less URL: www.foo.com[/...] or foo.com[/...] with a TLD-looking tail
_NAKED_URL_RE = re.compile(
    r"^(www\.)?[\w-]+(\.[\w-]+)+(/\S*)?$",
    re.IGNORECASE,
)
# Common TLDs to reduce false positives like "foo.bar" (which could be code)
_LIKELY_TLDS = {
    "com", "no", "org", "net", "io", "dev", "app", "ai", "co", "edu", "gov",
    "uk", "de", "se", "dk", "fi", "fr", "it", "es", "nl", "be", "ch", "at",
    "pl", "cz", "ru", "jp", "cn", "in", "au", "ca", "br", "mx", "info", "biz",
    "me", "tv", "us", "eu", "xyz", "site", "online", "store", "shop", "blog",
}
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PATH_RE = re.compile(r"^(/|~/|\./)[^\s]*$")

# Tokens that strongly suggest a shell command on the first word
_COMMAND_TOKENS = {
    "sudo", "apt", "apt-get", "pacman", "dnf", "yum", "snap", "flatpak",
    "git", "docker", "kubectl", "make", "cmake", "ninja",
    "ls", "cd", "cp", "mv", "rm", "mkdir", "rmdir", "chmod", "chown",
    "cat", "grep", "sed", "awk", "find", "xargs", "tar", "gzip", "gunzip",
    "ssh", "scp", "rsync", "curl", "wget", "ping", "netstat", "ss",
    "ps", "kill", "killall", "top", "htop", "systemctl", "journalctl",
    "python", "python3", "pip", "pip3", "node", "npm", "yarn", "pnpm",
    "cargo", "rustc", "go", "java", "javac", "ruby", "gem", "bundle",
    "echo", "export", "source", "bash", "sh", "zsh", "fish",
}

_COMMAND_HINTS = re.compile(r"(\s&&\s|\s\|\|\s|\s\|\s|\s>\s|\s>>\s|\$\(|`)")


def _normalize(text: str) -> str:
    """Strip whitespace + invisible/formatting characters that pollute regex matches."""
    # Drop format-category chars (Cf) which includes BOM, ZWJ/ZWNJ, RLM/LRM, etc.
    cleaned = "".join(
        c for c in text
        if unicodedata.category(c) != "Cf" and c not in _INVISIBLE_CHARS
    )
    return cleaned.strip()


def classify(text: str) -> ContentType:
    """Best-effort classification of a single selection."""
    if not text:
        return ContentType.PLAIN_TEXT

    stripped = _normalize(text)
    if not stripped:
        return ContentType.PLAIN_TEXT

    if _URL_RE.match(stripped):
        return ContentType.URL
    # Naked URL like "www.example.com" or "github.com/torvalds/linux"
    if _NAKED_URL_RE.match(stripped) and "@" not in stripped:
        # Sanity-check the last token before any slash has a plausible TLD
        host = stripped.split("/", 1)[0]
        tld = host.rsplit(".", 1)[-1].lower()
        if tld in _LIKELY_TLDS:
            return ContentType.URL
    if _EMAIL_RE.match(stripped):
        return ContentType.EMAIL
    if "\n" not in stripped and _PATH_RE.match(stripped):
        return ContentType.PATH

    first_token = stripped.split(maxsplit=1)[0]
    # Strip a leading '$ ' or '# ' shell prompt marker
    if first_token in ("$", "#") and len(stripped.split()) > 1:
        first_token = stripped.split()[1]

    if first_token in _COMMAND_TOKENS:
        return ContentType.COMMAND
    if _COMMAND_HINTS.search(stripped) and len(stripped) < 500:
        return ContentType.COMMAND

    return ContentType.PLAIN_TEXT
