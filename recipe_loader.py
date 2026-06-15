"""Load JSON "recipes" from ~/.config/linuxpop/recipes/ as Plugin objects.

A recipe is a no-code plugin definition: name, icon, content types, and an
action declaration. Four action types cover ~90% of real plugin ideas:

  - open_url:        xdg-open a URL built from a template
  - run_command:     bash -c a command built from a template
  - notify:          notify-send with a templated body
  - copy_transformed: render template, put result on clipboard

Available substitution variables in templates:
  {text}        - raw selection
  {text_url}    - percent-encoded (safe for URLs)
  {text_shell}  - shlex.quoted (safe for shell)
  {text_upper}  - uppercased
  {text_lower}  - lowercased
  {text_strip}  - whitespace-trimmed
"""
from __future__ import annotations

import json
import os
import random as _random
import shlex
import shutil
import subprocess
import urllib.parse
from pathlib import Path
from typing import Callable

from classifier import ContentType
from plugin_base import Plugin

RECIPES_DIR = Path(os.path.expanduser("~/.config/linuxpop/recipes"))

_CTYPE_BY_NAME = {
    "plain_text": ContentType.PLAIN_TEXT,
    "url":        ContentType.URL,
    "email":      ContentType.EMAIL,
    "path":       ContentType.PATH,
    "command":    ContentType.COMMAND,
}

VALID_ACTION_TYPES = (
    "open_url", "run_command", "notify", "copy_transformed",
    # Chain runs N sub-actions in sequence, piping the rendered output
    # of each transform step into the next as the {text} variable.
    # Terminal step actions (copy, paste, open_url, notify, run_command)
    # consume the current value and stop the chain.
    "chain",
)


def _mock_case(text: str) -> str:
    """Random upper/lower per character - the "mocking SpongeBob" meme
    format. Seeded with the text itself so the same input always gives
    the same result (so previews and clipboard contents are stable;
    re-clicking won't produce a different jumble each time)."""
    rng = _random.Random(text)
    return "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in text)


def _apply_snippet_placeholders(text: str) -> str:
    """Bridge to the snippet placeholder engine in clipboard_history.

    Lets recipes use {date}, {time}, {datetime}, {weekday}, {date:FORMAT},
    {date:+7d}, {name}, {clipboard}, {selection}, {shell:CMD} - the same
    dynamic tokens snippets support. {ask:} resolves to an empty string
    here because recipes fire from popup clicks where blocking on a
    dialog is jarring; users who need fill-ins should make a snippet.

    Lookup is best-effort: if clipboard_history isn't loaded (it's
    user-installed and could be removed), recipes fall back to the
    text passing through unchanged.
    """
    if "{" not in text:
        return text
    try:
        import sys
        ch = (sys.modules.get("linuxpop_user_clipboard_history")
              or sys.modules.get("clipboard_history"))
        if ch is None or not hasattr(ch, "render_placeholders"):
            return text
        rendered, _, _ = ch.render_placeholders(
            text, lambda fields: {label: "" for label, _ in fields},
        )
        return rendered
    except Exception as exc:
        print(f"[recipe] snippet-placeholder bridge failed: {exc}")
        return text


def _safe_b64_decode(text: str) -> str:
    """Best-effort base64 decode. Returns the original text on any
    parse failure, so a chain step that hands it non-b64 still produces
    a usable value."""
    import base64 as _b64
    try:
        cleaned = text.strip()
        padded = cleaned + "=" * (-len(cleaned) % 4)
        return _b64.b64decode(padded, validate=False).decode("utf-8", "replace")
    except Exception:
        return text


def _try_json_format(text: str) -> str:
    """Pretty-print text if it parses as JSON; return original otherwise.
    Used by the {text_json} transform - never raises so a chain step
    that hands it non-JSON still produces a usable value."""
    try:
        parsed = json.loads(text)
    except Exception:
        return text
    try:
        return json.dumps(parsed, indent=2, ensure_ascii=False)
    except Exception:
        return text


def _try_json_minify(text: str) -> str:
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        return text


def _render(template: str, text: str) -> str:
    """Substitute template variables. Unknown placeholders are left as-is."""
    import base64 as _b64
    safe = {
        "text":          text,
        "text_url":      urllib.parse.quote(text, safe=""),
        "text_shell":    shlex.quote(text),
        "text_upper":    text.upper(),
        "text_lower":    text.lower(),
        "text_strip":    text.strip(),
        "text_mock":     _mock_case(text),  # jEg eR sJeFeN - for mocking quotes
        # Workflow-chain transforms - safe to use anywhere a template
        # is rendered, since each renders the current value.
        "text_json":     _try_json_format(text),
        "text_json_min": _try_json_minify(text),
        "text_b64":      _b64.b64encode(text.encode("utf-8")).decode("ascii"),
        "text_url_decode": urllib.parse.unquote(text),
        # text_b64_decode is bytes-correct only if input was valid b64;
        # falls back to the input if not, mirroring the json helpers.
        "text_b64_decode": _safe_b64_decode(text),
    }
    try:
        first = template.format_map(_DefaultMissing(safe))
    except Exception as exc:  # noqa: BLE001
        print(f"[recipe] template render failed: {exc}")
        return template
    # Second pass: snippet-style dynamic placeholders (date, name,
    # clipboard, etc.). Adds the snippet engine's vocabulary to recipes
    # without duplicating the code.
    return _apply_snippet_placeholders(first)


class _DefaultMissing(dict):
    """str.format_map dict that returns '{name}' for missing keys instead of
    raising KeyError - keeps the template usable even with typos."""
    def __missing__(self, key):
        return "{" + key + "}"


def _build_handler(recipe: dict) -> Callable[[str], None]:
    action = recipe.get("action") or {}
    atype = action.get("type", "")
    template = action.get("template", "")
    title = action.get("title") or recipe.get("tooltip") or recipe.get("name", "LinuxPop")
    icon = recipe.get("icon") or "applications-other"

    if atype == "open_url":
        def handler(text: str) -> None:
            url = _render(template, text).strip()
            from platform_backend import get_backend
            get_backend().open_url(url)
        return handler

    if atype == "run_command":
        # Reject templates that embed the selection unquoted. Only
        # {text_shell} and {text_url} survive shell evaluation safely;
        # {text}, {text_upper}, {text_lower}, {text_strip} pass shell
        # metacharacters through and turn the recipe into trivial RCE
        # if the user ever clicks the button on attacker-controlled text.
        # Refuse to register so the bug surfaces in logs instead of as
        # a quiet wormhole.
        import re as _re
        unsafe = {"text", "text_upper", "text_lower", "text_strip", "text_mock"}
        placeholders = set(_re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", template))
        bad = placeholders & unsafe
        if bad:
            name = recipe.get("name") or recipe.get("tooltip") or "unnamed"
            print(f"[recipe] REFUSED to load run_command recipe {name!r}: "
                  f"template uses unsafe placeholder(s) {sorted(bad)} - "
                  f"replace with {{text_shell}} (shell-quoted) or "
                  f"{{text_url}} (URL-encoded).")
            def disabled_handler(_text: str) -> None:
                subprocess.run(
                    ["notify-send", "--hint=byte:transient:1", "-t", "5000",  "-u", "critical",
                     "-i", "dialog-warning", "LinuxPop recipe disabled",
                     f"{name}: unsafe template - see ~/.cache/linuxpop/linuxpop.log"],
                    check=False,
                )
            return disabled_handler

        def handler(text: str) -> None:
            cmd = _render(template, text)
            try:
                subprocess.Popen(["bash", "-c", cmd], start_new_session=True)
            except OSError as exc:
                subprocess.run(
                    ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", "dialog-error",
                     "Recipe error", f"Could not run: {exc}"],
                    check=False,
                )
        return handler

    if atype == "notify":
        def handler(text: str) -> None:
            body = _render(template, text)
            subprocess.run(
                ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", icon, title, body[:600]],
                check=False,
            )
        return handler

    if atype == "copy_transformed":
        def handler(text: str) -> None:
            out = _render(template, text)
            # Backend-aware: wl-copy on Wayland, xclip on X11. Plain xclip
            # only reaches the X11/XWayland clipboard and silently misses
            # native-Wayland apps.
            from platform_backend import get_backend
            get_backend().set_clipboard(out)
            subprocess.run(
                ["notify-send", "--hint=byte:transient:1", "-t", "3000",  "-i", icon, title, out[:200]],
                check=False,
            )
        return handler

    if atype == "chain":
        # Workflow: pipe the selection through a list of transforms,
        # then run the last step as a terminator (copy / paste / notify /
        # open_url / run_command). Each transform step renders its
        # template against the *current* value (initially the selection,
        # afterwards the previous transform's output) and re-binds the
        # result as {text} for the next step.
        steps = action.get("steps") or []
        if not steps:
            def handler(_text: str) -> None:
                subprocess.run(
                    ["notify-send", "--hint=byte:transient:1", "-t", "3000",
                     "-i", "dialog-warning", "LinuxPop chain",
                     f"{recipe.get('name', 'chain')}: no steps defined"],
                    check=False,
                )
            return handler

        def handler(text: str) -> None:
            current = text
            import logging as _logging
            log = _logging.getLogger("linuxpop")
            for i, step in enumerate(steps):
                stype = (step.get("type") or "transform").strip()
                stemplate = step.get("template", "{text}")
                if stype == "transform":
                    current = _render(stemplate, current)
                    log.info("[chain] %s step %d transform -> %d chars",
                             recipe.get("name"), i, len(current))
                    continue
                # Terminal steps: consume `current`, then stop.
                rendered = _render(stemplate, current) if stemplate else current
                if stype == "copy":
                    from platform_backend import get_backend
                    get_backend().set_clipboard(current)
                    subprocess.run(
                        ["notify-send", "--hint=byte:transient:1", "-t", "2500",
                         "-i", icon, title or "Copied", current[:200]],
                        check=False,
                    )
                elif stype == "paste":
                    # Put text on clipboard, then send Ctrl+V to whichever
                    # window currently holds focus. Same pattern the
                    # editing_actions plugin uses.
                    subprocess.run(
                        ["xclip", "-selection", "clipboard"],
                        input=current.encode("utf-8"), check=False, timeout=2.0,
                    )
                    import time as _t
                    _t.sleep(0.05)
                    if shutil.which("xdotool"):
                        subprocess.run(
                            ["xdotool", "key", "--clearmodifiers", "ctrl+v"],
                            check=False,
                        )
                elif stype == "notify":
                    subprocess.run(
                        ["notify-send", "--hint=byte:transient:1", "-t", "4000",
                         "-i", icon, title or "Chain", (rendered or current)[:600]],
                        check=False,
                    )
                elif stype == "open_url":
                    url = (rendered or current).strip()
                    from platform_backend import get_backend
                    get_backend().open_url(url)
                elif stype == "run_command":
                    cmd = rendered or current
                    try:
                        subprocess.Popen(["bash", "-c", cmd], start_new_session=True)
                    except OSError as exc:
                        log.warning("[chain] run_command failed: %s", exc)
                else:
                    log.warning("[chain] unknown step type %r in %s",
                                stype, recipe.get("name"))
                # Any terminal step ends the chain.
                return
            # If we fell off the loop with only transforms, push the
            # final value onto the clipboard as a sensible default.
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=current.encode("utf-8"), check=False, timeout=2.0,
            )
            subprocess.run(
                ["notify-send", "--hint=byte:transient:1", "-t", "2500",
                 "-i", icon, title or "Chain", current[:200]],
                check=False,
            )
        return handler

    # Unknown type - visible no-op so the popup button still appears
    def handler(text: str) -> None:
        print(f"[recipe] unknown action type for {recipe.get('name')!r}: {atype!r}")
    return handler


def _content_types(recipe: dict) -> tuple:
    names = recipe.get("content_types") or []
    if not names:
        return ()  # empty = universal
    return tuple(
        _CTYPE_BY_NAME[n] for n in names if n in _CTYPE_BY_NAME
    )


def validate(recipe: dict) -> list[str]:
    """Return a list of human-readable validation errors (empty = OK)."""
    errors = []
    name = (recipe.get("name") or "").strip()
    if not name:
        errors.append("'name' is required")
    elif not all(c.isalnum() or c in "-_" for c in name):
        errors.append("'name' must be alphanumeric / '-' / '_'")
    action = recipe.get("action") or {}
    atype = action.get("type")
    if atype not in VALID_ACTION_TYPES:
        errors.append(f"action.type must be one of {VALID_ACTION_TYPES}")
    # Chain actions use `steps` instead of `template`; every other
    # action type still needs a top-level template to render.
    if atype == "chain":
        steps = action.get("steps")
        if not isinstance(steps, list) or not steps:
            errors.append("chain.steps must be a non-empty list")
        else:
            valid_step_types = {
                "transform", "copy", "paste", "notify",
                "open_url", "run_command",
            }
            for i, step in enumerate(steps):
                if not isinstance(step, dict):
                    errors.append(f"chain.steps[{i}] must be an object")
                    continue
                stype = step.get("type", "transform")
                if stype not in valid_step_types:
                    errors.append(
                        f"chain.steps[{i}].type {stype!r} not in "
                        f"{sorted(valid_step_types)}")
                if stype == "transform" and not step.get("template"):
                    errors.append(
                        f"chain.steps[{i}] (transform) needs a template")
    else:
        if not action.get("template", ""):
            errors.append("action.template is required")
    return errors


_DEFAULT_RECIPE_SEEDS = (
    "google-search.json",
    "define.json",
    "wikipedia.json",
    "google-translate.json",
    "youtube-search.json",
)
_SEED_MARKER = RECIPES_DIR.parent / ".default-recipes-seeded"


def _seed_default_recipes() -> None:
    """First-run only: if the user has no recipes yet, copy a small curated
    set from the repo's plugins_repo/recipes/ so a fresh install shows
    useful buttons (Wikipedia, YouTube) right away. Touches a marker so
    this never runs twice - users who later delete a default recipe
    don't get it re-installed."""
    if _SEED_MARKER.is_file():
        return
    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    # If the user has already curated this dir, respect it - don't seed
    # on top of an existing setup.
    has_existing = any(RECIPES_DIR.glob("*.json"))
    if has_existing:
        _SEED_MARKER.touch()
        return
    repo_dir = Path(__file__).resolve().parent / "plugins_repo" / "recipes"
    if not repo_dir.is_dir():
        _SEED_MARKER.touch()
        return
    import shutil
    for filename in _DEFAULT_RECIPE_SEEDS:
        src = repo_dir / filename
        if not src.is_file():
            continue
        dst = RECIPES_DIR / filename
        if dst.is_file():
            continue
        try:
            shutil.copy2(src, dst)
            print(f"[recipe_loader] seeded default recipe: {filename}")
        except OSError as exc:
            print(f"[recipe_loader] could not seed {filename}: {exc}")
    _SEED_MARKER.touch()


def load_recipes(register) -> int:
    """Walk RECIPES_DIR; register each valid recipe as a Plugin. Returns count."""
    _seed_default_recipes()
    count = 0
    if not RECIPES_DIR.is_dir():
        return 0
    for path in sorted(RECIPES_DIR.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                recipe = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[recipe] could not read {path.name}: {exc}")
            continue
        errors = validate(recipe)
        if errors:
            print(f"[recipe] {path.name} has errors: {', '.join(errors)}")
            continue
        # Recipes can be turned off without deleting: 'enabled': false in
        # the JSON skips registration. Missing key defaults to True for
        # backwards compatibility with older recipes.
        if not recipe.get("enabled", True):
            continue
        try:
            register(Plugin(
                name=recipe["name"],
                icon=recipe.get("icon") or "applications-other",
                tooltip=recipe.get("tooltip") or recipe["name"],
                handler=_build_handler(recipe),
                content_types=_content_types(recipe),
                priority=int(recipe.get("priority", 200)),
            ))
            count += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[recipe] failed to register {path.name}: {exc}")
    if count:
        print(f"[recipe_loader] loaded {count} recipe(s)")
    return count


def list_recipes() -> list[tuple[Path, dict]]:
    """Return [(path, recipe_dict), ...] for the UI."""
    out = []
    if not RECIPES_DIR.is_dir():
        return out
    for path in sorted(RECIPES_DIR.glob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                out.append((path, json.load(f)))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def save_recipe(recipe: dict, target_path: Path | None = None) -> Path:
    """Persist a recipe as <name>.json (or to the supplied path). Atomic write."""
    RECIPES_DIR.mkdir(parents=True, exist_ok=True)
    if target_path is None:
        target_path = RECIPES_DIR / f"{recipe['name']}.json"
    tmp = target_path.with_suffix(target_path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(recipe, f, indent=2, ensure_ascii=False)
        f.write("\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, target_path)
    return target_path


def delete_recipe(path: Path) -> None:
    try:
        path.unlink()
    except OSError as exc:
        print(f"[recipe] could not delete {path}: {exc}")


def set_recipe_enabled(path: Path, enabled: bool) -> bool:
    """Flip the 'enabled' field on the recipe at `path` and save atomically.
    Returns True on success."""
    try:
        with path.open("r", encoding="utf-8") as f:
            recipe = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[recipe] could not read {path} for toggle: {exc}")
        return False
    recipe["enabled"] = bool(enabled)
    try:
        save_recipe(recipe, target_path=path)
        return True
    except OSError as exc:
        print(f"[recipe] could not save toggle for {path}: {exc}")
        return False
