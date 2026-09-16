"""Persistent user settings for local-flow.

Stored as JSON in ~/Library/Application Support/LocalFlow/settings.json so
choices survive restarts of the LaunchAgent. Reads and writes are
deliberately forgiving — a missing or corrupt file just means defaults.

Keys:
  language    current dictation language code (e.g. "de")
  languages   codes shown in the menu bar's Language submenu
  quick_keys  {"q": "de", ...} letters that switch language while the
              dictation hotkey is held
"""

import json
import os
from pathlib import Path

import languages

DEFAULT_LANGUAGE = "en"
DEFAULT_LANGUAGES = ["en", "de", "fr"]
DEFAULT_QUICK_KEYS = {"q": "de", "w": "fr", "e": "en"}
QUICK_KEY_SLOTS = ["q", "w", "e", "r", "t"]   # assignable letters, left to right

# macOS virtual keycodes for the assignable letters
KEYCODES = {"q": 12, "w": 13, "e": 14, "r": 15, "t": 17}

SETTINGS_PATH = Path(
    os.environ.get("LOCALFLOW_SETTINGS")
    or Path.home() / "Library" / "Application Support" / "LocalFlow"
    / "settings.json"
)


def load() -> dict:
    try:
        with open(SETTINGS_PATH) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save(**changes) -> dict:
    data = load()
    data.update(changes)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS_PATH)
    return data


# ---------------------------------------------------------------- language

def get_language() -> str:
    lang = load().get("language", DEFAULT_LANGUAGE)
    return lang if lang in languages.CATALOG else DEFAULT_LANGUAGE


def set_language(code: str) -> str:
    if code not in languages.CATALOG:
        raise ValueError(f"unsupported language: {code}")
    enabled = get_languages()
    if code not in enabled:
        enabled.append(code)
        save(language=code, languages=enabled)
    else:
        save(language=code)
    return code


# ---------------------------------------------------------------- enabled list

def get_languages() -> list:
    """Codes shown in the Language menu, in menu order."""
    raw = load().get("languages", DEFAULT_LANGUAGES)
    if not isinstance(raw, list):
        raw = DEFAULT_LANGUAGES
    out = [c for c in raw if c in languages.CATALOG]
    return out or list(DEFAULT_LANGUAGES)


def add_language(code: str) -> list:
    if code not in languages.CATALOG:
        raise ValueError(f"unsupported language: {code}")
    enabled = get_languages()
    if code not in enabled:
        enabled.append(code)
        save(languages=enabled)
    return enabled


def remove_language(code: str) -> list:
    enabled = get_languages()
    if code in enabled and len(enabled) > 1:
        enabled.remove(code)
        changes = {"languages": enabled}
        if get_language() == code:
            changes["language"] = enabled[0]
        keys = {k: v for k, v in get_quick_keys().items() if v != code}
        if keys != get_quick_keys():
            changes["quick_keys"] = keys
        save(**changes)
    return enabled


# ---------------------------------------------------------------- quick keys

def get_quick_keys() -> dict:
    """{"q": "de", ...} — only valid slots and catalog codes survive."""
    raw = load().get("quick_keys", DEFAULT_QUICK_KEYS)
    if not isinstance(raw, dict):
        raw = DEFAULT_QUICK_KEYS
    return {k: v for k, v in raw.items()
            if k in QUICK_KEY_SLOTS and v in languages.CATALOG}


def set_quick_key(slot: str, code) -> dict:
    """Assign `code` to a letter, or clear it with code=None."""
    if slot not in QUICK_KEY_SLOTS:
        raise ValueError(f"unsupported quick key: {slot}")
    keys = get_quick_keys()
    if code is None:
        keys.pop(slot, None)
    else:
        if code not in languages.CATALOG:
            raise ValueError(f"unsupported language: {code}")
        keys = {k: v for k, v in keys.items() if v != code}  # one key per language
        keys[slot] = code
    save(quick_keys=keys)
    return keys


def quick_keys_by_keycode() -> dict:
    """{12: "de", ...} for the hotkey listener."""
    return {KEYCODES[k]: v for k, v in get_quick_keys().items()}


# ---------------------------------------------------------------- models

def model_for_language(model_name: str, language: str) -> str:
    """Pick the whisper model that can actually decode `language`.

    The `.en` models are English-only; for any other language fall back to
    the multilingual sibling of the same size (small.en → small). When the
    user configured a multilingual model already, leave it alone.
    """
    if language != "en" and model_name.endswith(".en"):
        return model_name[: -len(".en")]
    return model_name
