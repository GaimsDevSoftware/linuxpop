"""Screen-region OCR for LinuxPop.

User holds a hotkey, drags a rectangle, the rectangle's contents land
on the clipboard as text. Backs onto `maim -s` (interactive region
selection) for capture and `tesseract` for recognition - both apt-
installable on every mainstream Linux distro.

Why this matters beyond a one-off screenshot tool: the LinuxPop popup
runs on X11 PRIMARY selection. Anywhere the user can't make a real
selection (PDF viewers' rasterized text, video frames, OS chrome,
CodeMirror / Monaco editors that don't propagate to PRIMARY, error
dialogs that block selection) becomes unreachable. OCR turns the
*pixels* into a PRIMARY-equivalent selection - the rest of LinuxPop's
pipeline lights up automatically.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("linuxpop")


def is_supported() -> tuple[bool, str]:
    """Return (ok, reason). ok=False means we can't run OCR right now;
    the reason names which dependency is missing so the user can fix
    it themselves."""
    if not shutil.which("maim") and not shutil.which("gnome-screenshot"):
        return False, ("install 'maim' (or 'gnome-screenshot') for "
                       "region capture: sudo apt install maim")
    if not shutil.which("tesseract"):
        return False, ("install 'tesseract-ocr' for text recognition: "
                       "sudo apt install tesseract-ocr")
    return True, ""


def _capture_region(out_path: Path) -> bool:
    """Use whichever region-capture tool is installed to grab a user-
    drawn rectangle and write it as a PNG. Returns False if the user
    cancelled or the tool errored out."""
    if shutil.which("maim"):
        # `-s` puts maim in interactive region-select mode; output goes
        # to stdout if we don't pass a filename. We use a filename so
        # tesseract can read it back.
        try:
            res = subprocess.run(
                ["maim", "-s", str(out_path)],
                capture_output=True, timeout=60,
            )
            if res.returncode != 0:
                log.info("[ocr] maim exited %d (user cancelled?)",
                         res.returncode)
                return False
            return out_path.is_file() and out_path.stat().st_size > 0
        except subprocess.TimeoutExpired:
            log.warning("[ocr] maim timed out after 60 s")
            return False
    if shutil.which("gnome-screenshot"):
        # gnome-screenshot --area is interactive too. Older versions
        # don't accept a target path on stdout, so use --file.
        try:
            res = subprocess.run(
                ["gnome-screenshot", "--area", "--file", str(out_path)],
                capture_output=True, timeout=60,
            )
            return res.returncode == 0 and out_path.is_file()
        except subprocess.TimeoutExpired:
            return False
    return False


def _run_tesseract(image_path: Path, lang: str = "eng") -> str | None:
    """Run tesseract against the captured PNG and return the recognised
    text. Returns None on failure."""
    try:
        res = subprocess.run(
            ["tesseract", str(image_path), "-", "-l", lang],
            capture_output=True, text=True, timeout=20,
        )
        if res.returncode != 0:
            log.warning("[ocr] tesseract returncode=%d stderr=%s",
                        res.returncode, res.stderr[:200])
            return None
        return (res.stdout or "").strip()
    except subprocess.TimeoutExpired:
        log.warning("[ocr] tesseract timed out")
        return None


def capture_and_recognize(lang: str = "eng+nor") -> tuple[bool, str]:
    """Run the full capture -> OCR pipeline.

    Returns (ok, text_or_message). On success text_or_message is the
    recognised text; on failure it's a short message suitable for a
    notify-send body.

    `lang` is passed through to tesseract's -l flag. Defaults to
    English + Norwegian since this is built for a Norwegian user; if
    those languages aren't installed tesseract complains and we
    fall back to its default language.
    """
    ok, reason = is_supported()
    if not ok:
        return False, reason

    with tempfile.NamedTemporaryFile(
            suffix=".png", prefix="linuxpop-ocr-",
            delete=False) as tmp:
        png_path = Path(tmp.name)

    try:
        if not _capture_region(png_path):
            return False, "Region capture cancelled."
        text = _run_tesseract(png_path, lang=lang)
        if text is None:
            # Retry with default language pack if user-specified one
            # isn't installed.
            text = _run_tesseract(png_path, lang="eng")
        if not text:
            return False, "Tesseract returned no text."
        return True, text
    finally:
        try:
            png_path.unlink()
        except OSError:
            pass


def run_ocr_to_clipboard() -> None:
    """User-facing entry point. Triggered by the OCR hotkey or by the
    tray menu. Captures a region, OCRs it, puts the result on the
    clipboard, and shows the result text in the popup (so it lands as
    a selection the rest of LinuxPop's actions can pick up)."""
    ok, payload = capture_and_recognize()
    if not ok:
        subprocess.run(
            ["notify-send", "--hint=byte:transient:1", "-t", "4000",
             "-i", "dialog-information", "LinuxPop OCR", payload],
            check=False,
        )
        return
    # Park the text on the clipboard so it's usable everywhere.
    subprocess.run(
        ["xclip", "-selection", "clipboard"],
        input=payload.encode("utf-8"), check=False, timeout=2.0,
    )
    # Also park it on PRIMARY so the popup can immediately act on it
    # the same way it would on a real selection.
    subprocess.run(
        ["xclip", "-selection", "primary"],
        input=payload.encode("utf-8"), check=False, timeout=2.0,
    )
    # Friendly confirmation - tail the recognised text so the user knows
    # OCR ran and roughly what came out.
    preview = payload.replace("\n", " ")[:120]
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "3500",
         "-i", "edit-paste-symbolic", "OCR captured",
         f"{len(payload)} chars on clipboard - “{preview}”"],
        check=False,
    )
