"""Developer hashing and decoding utilities: SHA-256, MD5, JWT payload decode.
SHA and MD5 copy the digest to the clipboard. JWT decode pretty-prints the
payload and copies it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess

from classifier import ContentType
from plugin_base import Plugin

# JWT: three base64url segments separated by dots. Header decodes to JSON
# starting with '{' — we don't validate that here, just shape-match.
_JWT_SHAPE = re.compile(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*$")


def _looks_like_jwt(text: str) -> bool:
    stripped = text.strip()
    if not _JWT_SHAPE.match(stripped):
        return False
    # Reject short shape-matches (e.g. "a.b.c"). Real JWTs are 100+ chars.
    return len(stripped) >= 30


def _copy_and_notify(text: str, title: str) -> None:
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=text.encode("utf-8"), check=False,
    )
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "security-high-symbolic", title, text[:280]],
        check=False,
    )


def _sha256(text: str) -> None:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    _copy_and_notify(digest, "SHA-256")


def _md5(text: str) -> None:
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    _copy_and_notify(digest, "MD5")


def _b64url_decode(part: str) -> bytes:
    # JWT uses url-safe base64 without padding
    padded = part + "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _jwt_decode(text: str) -> None:
    parts = text.strip().split(".")
    if len(parts) < 2:
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error", "JWT decode",
             "Not a JWT (expected header.payload.signature)"],
            check=False,
        )
        return
    try:
        header = json.loads(_b64url_decode(parts[0]))
        payload = json.loads(_b64url_decode(parts[1]))
    except Exception as exc:
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error", "JWT decode", str(exc)[:200]],
            check=False,
        )
        return
    pretty = json.dumps(payload, indent=2, ensure_ascii=False)
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=pretty.encode("utf-8"), check=False,
    )
    alg = header.get("alg", "?")
    typ = header.get("typ", "?")
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "security-high-symbolic",
         f"JWT decoded (alg={alg}, typ={typ})", pretty[:400]],
        check=False,
    )


def register(register_plugin) -> None:
    types = (ContentType.PLAIN_TEXT,)
    register_plugin(Plugin(name="sha256", icon="security-high-symbolic",
        tooltip="SHA-256 hash", handler=_sha256, content_types=types, priority=180))
    register_plugin(Plugin(name="md5", icon="security-medium-symbolic",
        tooltip="MD5 hash", handler=_md5, content_types=types, priority=181))
    register_plugin(Plugin(name="jwt-decode", icon="dialog-password-symbolic",
        tooltip="Decode JWT payload", handler=_jwt_decode, content_types=types, priority=182,
        predicate=_looks_like_jwt))
