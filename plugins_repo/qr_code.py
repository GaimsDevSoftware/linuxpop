"""Generate a QR code for the selected text and show it.

Uses a rolling cache directory under ~/.cache/linuxpop/qr/ - keeps the
last 20 QR images and trims older ones, so /tmp doesn't accumulate forever.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

from classifier import ContentType
from plugin_base import Plugin
from xdg_paths import CACHE_DIR

QR_CACHE = CACHE_DIR / "qr"
MAX_CACHED = 20


def _cleanup_cache() -> None:
    if not QR_CACHE.is_dir():
        return
    pngs = sorted(QR_CACHE.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in pngs[MAX_CACHED:]:
        try:
            old.unlink()
        except OSError:
            pass


def _qr(text: str) -> None:
    if not shutil.which("qrencode"):
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error", "QR plugin missing dependency",
             "Install with: sudo apt-get install -y qrencode"],
            check=False,
        )
        return
    QR_CACHE.mkdir(parents=True, exist_ok=True)
    _cleanup_cache()
    out_path = QR_CACHE / f"qr-{int(time.time() * 1000):x}.png"
    try:
        subprocess.run(
            ["qrencode", "-s", "8", "-o", str(out_path), text],
            check=True,
        )
        subprocess.Popen(["xdg-open", str(out_path)], start_new_session=True)
    except subprocess.CalledProcessError as exc:
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error", "QR error", str(exc)[:200]],
            check=False,
        )


def register(register_plugin) -> None:
    register_plugin(Plugin(
        name="qr-code",
        icon="linuxpop-qr-symbolic",
        tooltip="Make QR code",
        handler=_qr,
        content_types=(ContentType.URL, ContentType.PLAIN_TEXT),
        priority=90,
    ))
