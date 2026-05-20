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
    # package managers
    "sudo", "apt", "apt-get", "aptitude", "pacman", "dnf", "yum", "zypper",
    "snap", "flatpak", "brew", "nix", "nix-env",
    # version control + build
    "git", "svn", "hg", "docker", "podman", "kubectl", "helm",
    "make", "cmake", "ninja", "meson", "gradle", "mvn",
    # file management
    "ls", "ll", "la", "cd", "pwd", "cp", "mv", "rm", "mkdir", "rmdir",
    "chmod", "chown", "chgrp", "ln", "touch", "stat", "file", "tree",
    # viewing / paging / text inspection
    "cat", "tac", "tail", "head", "less", "more", "view",
    "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "sed", "awk", "cut", "paste", "tr", "sort", "uniq", "wc",
    "tee", "column", "fold", "fmt", "nl",
    "find", "locate", "xargs", "tar", "gzip", "gunzip", "zip", "unzip",
    "diff", "patch", "cmp",
    # network
    "ssh", "scp", "sftp", "rsync", "curl", "wget", "httpie", "http",
    "ping", "traceroute", "mtr", "dig", "nslookup", "host",
    "netstat", "ss", "ip", "ifconfig", "iwconfig", "nmap",
    # process / system
    "ps", "kill", "killall", "pkill", "pgrep", "top", "htop", "btop",
    "systemctl", "journalctl", "service", "dmesg", "uptime",
    "df", "du", "free", "lsof", "fuser", "mount", "umount",
    "uname", "hostname", "whoami", "id", "who", "w", "groups",
    # editors
    "vim", "vi", "nano", "emacs", "code", "gedit", "kate", "nvim",
    # languages / runtimes
    "python", "python3", "pip", "pip3", "pipx", "uv", "poetry",
    "node", "npm", "yarn", "pnpm", "npx", "deno", "bun",
    "cargo", "rustc", "rustup", "go", "java", "javac", "kotlin",
    "ruby", "gem", "bundle", "rake", "rails", "perl", "php",
    # shell builtins / general
    "echo", "printf", "export", "source", "alias", "unalias",
    "bash", "sh", "zsh", "fish", "dash",
    "env", "which", "type", "whereis", "command",
    "date", "cal", "sleep", "watch", "time", "timeout",
    "man", "info", "help",
    "history", "exit", "logout",
    "nohup", "screen", "tmux",
    # X11 / desktop helpers commonly used in tutorials
    "xdotool", "xclip", "xsel", "wmctrl", "xrandr", "xprop", "xev",
    "notify-send", "xdg-open",
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
