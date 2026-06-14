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


def _stage_text(text: str) -> None:
    """Put `text` on the clipboard (and PRIMARY) with the right tool for the
    session: wl-copy on Wayland, xclip on X11. The old code only knew xclip,
    which isn't installed on a Wayland box - so OCR'd text silently never
    reached the clipboard."""
    data = text.encode("utf-8")
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        for extra in ([], ["--primary"]):
            try:
                subprocess.run(["wl-copy", *extra], input=data,
                               check=False, timeout=2.0)
            except (OSError, subprocess.SubprocessError):
                pass
        return
    if shutil.which("xclip"):
        for sel in ("clipboard", "primary"):
            try:
                subprocess.run(["xclip", "-selection", sel], input=data,
                               check=False, timeout=2.0)
            except (OSError, subprocess.SubprocessError):
                pass


def _distro_id() -> str:
    """Read /etc/os-release ID + ID_LIKE so we can pick the right
    package manager. ID_LIKE is the fallback distro family (e.g.
    Pop_OS has ID=pop, ID_LIKE=ubuntu debian) so we don't need to
    enumerate every derivative."""
    try:
        text = Path("/etc/os-release").read_text()
    except OSError:
        return ""
    info: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        info[k.strip()] = v.strip().strip('"').strip("'")
    return f"{info.get('ID', '')} {info.get('ID_LIKE', '')}".lower()


def install_command() -> str:
    """Best-effort install command for the user's distro. Always covers
    tesseract, maim, and at least an English language pack so the
    feature is usable end-to-end after running it."""
    ids = _distro_id()

    def has(needles: tuple[str, ...]) -> bool:
        return any(n in ids for n in needles)

    if has(("fedora", "rhel", "centos", "rocky", "alma")):
        return "sudo dnf install -y tesseract tesseract-langpack-eng maim"
    if has(("arch", "manjaro", "endeavouros")):
        return ("sudo pacman -S --noconfirm tesseract tesseract-data-eng "
                "maim")
    if has(("opensuse", "suse")):
        return ("sudo zypper --non-interactive install tesseract-ocr "
                "tesseract-ocr-traineddata-english maim")
    # Default to apt: Debian, Ubuntu, Mint, Pop_OS, elementary,
    # Zorin, Kubuntu, Xubuntu, MX, Deepin, KDE Neon, ...
    return ("sudo apt install -y tesseract-ocr tesseract-ocr-eng "
            "tesseract-ocr-nor maim")


def _has_capture_tool() -> bool:
    """True if any supported region-capture tool is on PATH. spectacle and
    grim work natively on Wayland; maim/gnome-screenshot are the X11 path."""
    return bool(shutil.which("spectacle") or shutil.which("grim")
                or shutil.which("maim") or shutil.which("gnome-screenshot"))


def is_supported() -> tuple[bool, str]:
    """Return (ok, reason). ok=False means we can't run OCR right now;
    the reason is a SHORT human label of what's missing (the Settings row
    pairs it with an Install button, so it doesn't need to spell out a
    command)."""
    if not _has_capture_tool():
        return False, "screen-capture tool not installed"
    if not shutil.which("tesseract"):
        return False, "tesseract OCR engine not installed"
    return True, ""


def install_argv() -> "list[str] | None":
    """A pkexec argv that installs the missing OCR dependencies non-
    interactively (pkexec shows a graphical auth prompt). Returns None when
    we don't recognise the package manager. A capture tool is only added
    when none is present - KDE already ships spectacle, so on most Wayland
    desktops only tesseract is missing."""
    ids = _distro_id()

    def has(needles: tuple) -> bool:
        return any(n in ids for n in needles)

    need_capture = not _has_capture_tool()
    if has(("fedora", "rhel", "centos", "rocky", "alma")):
        pkgs = ["tesseract", "tesseract-langpack-eng"]
        if need_capture:
            pkgs.append("maim")
        return ["pkexec", "dnf", "install", "-y", *pkgs]
    if has(("arch", "manjaro", "endeavouros")):
        pkgs = ["tesseract", "tesseract-data-eng"]
        if need_capture:
            pkgs.append("maim")
        return ["pkexec", "pacman", "-S", "--noconfirm", *pkgs]
    if has(("opensuse", "suse")):
        pkgs = ["tesseract-ocr", "tesseract-ocr-traineddata-english"]
        if need_capture:
            pkgs.append("maim")
        return ["pkexec", "zypper", "--non-interactive", "install", *pkgs]
    if has(("debian", "ubuntu", "mint", "pop", "elementary", "zorin",
            "neon", "kali", "deepin", "mx")):
        pkgs = ["tesseract-ocr", "tesseract-ocr-eng", "tesseract-ocr-nor"]
        if need_capture:
            pkgs.append("maim")
        return ["pkexec", "apt-get", "install", "-y", *pkgs]
    return None


def _capture_region(out_path: Path) -> bool:
    """Use whichever region-capture tool is installed to grab a user-
    drawn rectangle and write it as a PNG. Returns False if the user
    cancelled or the tool errored out."""
    if shutil.which("spectacle"):
        # KDE's capture tool. Its rectangular-region selector works
        # natively on Wayland (maim is X11-only and grim needs wlroots),
        # so it's the right default on KWin. -r region, -b background (no
        # GUI window), -n no notification, -o write to file.
        try:
            subprocess.run(
                ["spectacle", "-r", "-b", "-n", "-o", str(out_path)],
                capture_output=True, timeout=120,
            )
            return out_path.is_file() and out_path.stat().st_size > 0
        except subprocess.TimeoutExpired:
            log.warning("[ocr] spectacle timed out")
            return False
    if shutil.which("grim") and shutil.which("slurp"):
        # wlroots compositors (sway, Hyprland): slurp picks the region,
        # grim captures it.
        try:
            geom = subprocess.run(["slurp"], capture_output=True,
                                  timeout=60, text=True)
            if geom.returncode != 0 or not geom.stdout.strip():
                return False
            res = subprocess.run(
                ["grim", "-g", geom.stdout.strip(), str(out_path)],
                capture_output=True, timeout=30,
            )
            return res.returncode == 0 and out_path.is_file()
        except subprocess.TimeoutExpired:
            return False
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
    # Park the text on the clipboard (and PRIMARY) so it's usable everywhere
    # and the popup can act on it like a real selection.
    _stage_text(payload)
    # Friendly confirmation - tail the recognised text so the user knows
    # OCR ran and roughly what came out.
    preview = payload.replace("\n", " ")[:120]
    subprocess.run(
        ["notify-send", "--hint=byte:transient:1", "-t", "3500",
         "-i", "edit-paste-symbolic", "OCR captured",
         f"{len(payload)} chars on clipboard - “{preview}”"],
        check=False,
    )
