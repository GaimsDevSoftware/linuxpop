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
    "sudo", "doas", "apt", "apt-get", "aptitude", "pacman", "paru", "yay",
    "dnf", "yum", "zypper", "snap", "flatpak", "brew", "nix", "nix-env",
    "nix-build", "nix-shell", "nix-store", "apk", "xbps-install", "xbps-query",
    "eopkg", "emerge",
    # version control + build
    "git", "svn", "hg", "lazygit", "tig", "gh", "glab",
    "docker", "podman", "nerdctl", "lima", "kubectl", "helm", "kustomize",
    "terraform", "ansible", "ansible-playbook", "vagrant", "packer",
    "make", "cmake", "ninja", "meson", "gradle", "mvn", "just", "bazel",
    # file management
    "ls", "ll", "la", "eza", "exa", "cd", "pwd", "cp", "mv", "rm", "mkdir", "rmdir",
    "chmod", "chown", "chgrp", "ln", "touch", "stat", "file", "tree",
    "mc", "ranger", "lf", "nnn", "vifm",
    # viewing / paging / text inspection
    "cat", "bat", "tac", "tail", "head", "less", "more", "view",
    "grep", "egrep", "fgrep", "rg", "ag", "ack",
    "sed", "awk", "cut", "paste", "tr", "sort", "uniq", "wc",
    "tee", "column", "fold", "fmt", "nl",
    "find", "fd", "locate", "xargs", "tar", "gzip", "gunzip", "zip", "unzip",
    "7z", "unrar",
    "diff", "patch", "cmp", "jq", "yq",
    # network
    "ssh", "mosh", "scp", "sftp", "rsync", "curl", "wget", "httpie", "http",
    "ping", "traceroute", "tracepath", "mtr", "dig", "nslookup", "host", "whois",
    "netstat", "ss", "ip", "ifconfig", "iwconfig", "iwctl",
    "nmap", "ncat", "nc", "socat",
    "nmcli", "networkctl", "wpa_cli", "ufw", "iptables", "nftables", "firewall-cmd",
    # process / system
    "ps", "kill", "killall", "pkill", "pgrep", "top", "htop", "btop", "atop",
    "systemctl", "journalctl", "service", "dmesg", "uptime",
    "df", "du", "duf", "ncdu", "free", "lsof", "fuser", "mount", "umount",
    "uname", "hostname", "whoami", "id", "who", "w", "groups",
    "crontab", "at", "lsblk", "blkid", "fdisk", "parted", "dd", "mkfs",
    "swapon", "swapoff",
    # editors
    "vim", "vi", "nano", "emacs", "code", "codium", "gedit", "kate", "nvim",
    "helix", "hx", "micro", "subl",
    # languages / runtimes
    "python", "python3", "pip", "pip3", "pipx", "uv", "poetry", "pdm", "rye",
    "pytest", "black", "ruff", "mypy", "flake8", "isort", "tox", "twine",
    "node", "nodejs", "npm", "yarn", "pnpm", "npx", "deno", "bun", "tsx",
    "tsc", "eslint", "prettier", "jest", "vitest", "mocha", "vite", "webpack",
    "cargo", "rustc", "rustup", "rustfmt", "clippy",
    "go", "java", "javac", "kotlin", "scala", "sbt",
    "ruby", "gem", "bundle", "rake", "rails", "irb",
    "perl", "cpan", "php", "composer",
    "dotnet", "mono", "swift", "lua", "luarocks", "julia",
    "crystal", "shards", "mix", "iex",
    # databases
    "psql", "mysql", "sqlite3", "redis-cli", "mongo", "mongosh", "duckdb",
    # cloud / devops CLIs
    "aws", "gcloud", "az", "doctl", "linode-cli", "fly", "flyctl",
    "kubectl", "k9s", "stern",  # kubectl deliberately duplicated
    # shell builtins / general
    "echo", "printf", "export", "source", "alias", "unalias",
    "bash", "sh", "zsh", "fish", "dash", "nu", "elvish",
    "env", "which", "type", "whereis", "command",
    "date", "cal", "sleep", "watch", "time", "timeout",
    "man", "info", "help", "tldr",
    "history", "exit", "logout", "clear", "reset",
    "nohup", "screen", "tmux", "tmate",
    # media / conversion
    "ffmpeg", "ffprobe", "magick", "convert", "sox", "pandoc",
    "yt-dlp", "youtube-dl",
    # backup / sync
    "restic", "borg", "rclone", "rsnapshot", "duplicity",
    # X11 / desktop helpers commonly used in tutorials
    "xdotool", "xclip", "xsel", "wmctrl", "xrandr", "xprop", "xev",
    "notify-send", "xdg-open",
    # env tools
    "direnv", "asdf", "mise", "nvm", "pyenv", "rbenv", "fnm", "volta",
    # KDE / Plasma / Qt desktop tools (the user runs Plasma 6, so these
    # show up constantly in restart/config snippets)
    "kquitapp6", "kquitapp5", "kquitapp", "kstart6", "kstart5", "kstart",
    "kwriteconfig6", "kwriteconfig5", "kreadconfig6", "kreadconfig5",
    "qdbus", "qdbus6", "qdbus-qt6", "kcmshell6", "kcmshell5", "systemsettings",
    "kded6", "kbuildsycoca6", "kioclient6", "kscreen-doctor", "krunner",
    "plasmashell", "kwin_wayland", "kwin_x11", "kwin", "kactivities-cli",
    "balooctl6", "balooctl", "kdialog", "kdeconnect-cli", "kompare",
    "loginctl", "busctl", "gdbus", "gtk-launch",
    # audio (PipeWire / PulseAudio / ALSA)
    "wpctl", "pactl", "pacmd", "pw-cli", "pw-play", "pw-record", "pw-dump",
    "pw-metadata", "amixer", "alsamixer", "speaker-test", "aplay", "arecord",
    # desktop / wayland helpers
    "gsettings", "dconf", "xdg-settings", "xdg-mime", "wl-copy", "wl-paste",
    "wdotool", "ydotool", "wtype", "grim", "slurp", "spectacle", "okular", "dolphin",
}

# Shell metacharacters / patterns that strongly suggest the text is
# meant to be executed. Relaxed in 2026-05 after several real shell
# pastes were classified as plain text: the old version required
# spaces around `|`/`>`/`&&`, missed flag-only lines like `--dry-run`,
# and didn't catch executable paths or heredoc syntax.
_COMMAND_HINTS = re.compile(
    r"(?:"
    r"&&|\|\||"                   # logical and/or - shell-only operators
    r"\s>+\s|\s>>\s|"             # redirection with surrounding space
    r"\$\(|\$\{|"                 # command substitution / brace expansion
    r"`[^`]+`|"                    # backtick expansion
    r"<<\w+|<<-|"                  # heredoc
    r"(?:^|\s)[-]{1,2}[A-Za-z][\w-]*(?:\s|=|$)|"  # flags like -v, --dry-run, --foo=bar
    r"(?:^|\s)(?:\./|/usr/|/bin/|/sbin/|~/)[\w./-]+(?:\s|$)|"  # executable path
    # Hyphenated all-lowercase command name followed by space + arg.
    # Catches xdg-settings, gnome-terminal, apt-get, gtk-update-icon-cache,
    # systemd-run, dpkg-reconfigure, etc. Plain English prose
    # essentially never starts a line with this shape.
    r"(?:^|\n)\s*[a-z]+(?:-[a-z][\w-]*)+\s+\S"
    r")"
)
# Bare single ' | ' (pipe with whitespace on either side) WAS in
# _COMMAND_HINTS but produced too many false positives - any human-
# readable title with a separator pipe (YouTube "Channel Name |
# Category", song titles, breadcrumbs) would suppress the Send-to-AI
# buttons. Real shell pipes are caught by _looks_like_command()
# checking the first token of each line against _COMMAND_TOKENS, so
# `cat foo | grep bar` still classifies correctly.


def _line_first_token(line: str) -> str:
    """Return the first 'real' token of a line - peel off a leading
    shell prompt ('$ ', '> ', '% ', '# ') or shebang ('#!') first."""
    line = line.strip()
    if not line:
        return ""
    # Two-char prompt prefix (prompt + space)
    if len(line) >= 2 and line[0] in "$>%#" and line[1] in (" ", "\t"):
        line = line[2:].strip()
    elif line.startswith("#!"):
        # Shebang line - '#!/bin/bash' etc. The interpreter path itself
        # is enough signal that this is a script.
        return "#!"
    if not line:
        return ""
    return line.split(maxsplit=1)[0]


# Shell statement separators. A known command token after any of these
# also counts ('kquitapp6 foo ; kstart6 foo', 'cat x | grep y',
# 'mkdir d && cd d') - _line_first_token only ever saw the first segment.
_SHELL_SEP_RE = re.compile(r";|&&|\|\||\|")


def _looks_like_command(stripped: str) -> bool:
    """Scan EVERY non-empty line for a known command token at the start.

    The old logic only checked the first token of the whole selection,
    which missed the very common 'comment header + command' shape:

        # Install foo
        sudo apt install foo

    A shebang on any line also counts (the user clearly pasted a script).
    Each line is also split on shell separators (; && || |) so a command
    that only appears after the separator still classifies.
    """
    for raw in stripped.splitlines():
        first = _line_first_token(raw)
        if first == "#!":
            return True
        if first and first in _COMMAND_TOKENS:
            return True
        # Peel a leading prompt, then check the first token of every
        # shell-separated segment against the command whitelist.
        line = raw.strip()
        if len(line) >= 2 and line[0] in "$>%#" and line[1] in (" ", "\t"):
            line = line[2:].strip()
        for segment in _SHELL_SEP_RE.split(line):
            seg = segment.strip()
            if seg and seg.split(maxsplit=1)[0] in _COMMAND_TOKENS:
                return True
    return False


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

    # Multi-line aware: scan every line so a leading comment / blank /
    # shell-prompt prefix doesn't hide the actual command underneath.
    if _looks_like_command(stripped):
        return ContentType.COMMAND
    # Robert preferred "fires one time too many" over "misses real
    # commands". Cap raised to 4000 chars (was 500) so multi-line
    # snippets and pasted scripts with shell metacharacters still
    # classify as COMMAND. Pure prose almost never hits these
    # patterns - the only false positives come from technical writing
    # quoting actual shell, which is fine to surface a Run button for.
    if _COMMAND_HINTS.search(stripped) and len(stripped) < 4000:
        return ContentType.COMMAND

    return ContentType.PLAIN_TEXT


# Extensions that strongly imply "this path is a script you might want
# to execute". Used by main.py / plugin_loader to also offer the
# "Run in terminal" button on PATH-classified selections like
# './build.sh' alongside the default 'Open path'.
_RUNNABLE_PATH_EXTS = (
    ".sh", ".bash", ".zsh", ".fish",
    ".py", ".pl", ".rb", ".js", ".ts", ".lua", ".tcl",
    ".php", ".awk", ".sed",
    ".out", ".bin",
)


def is_runnable_path(text: str) -> bool:
    """True when a PATH-typed selection points at something the user
    probably wants the option to execute (script extensions, or any
    './foo' / '~/bin/foo' style)."""
    s = _normalize(text)
    if not s or "\n" in s:
        return False
    if not _PATH_RE.match(s):
        return False
    low = s.lower()
    if low.endswith(_RUNNABLE_PATH_EXTS):
        return True
    # ./foo or ~/bin/foo - likely an executable even without an extension
    if s.startswith(("./", "~/bin/")):
        return True
    return False
