"""Serialization and validation for browser-local app preferences."""

import json


PREFERENCES_VERSION = 1
MAX_PREFERENCES_BYTES = 10_000
MAX_VOICE_ID_LENGTH = 500


def build_preferences_json(target_language, no_assimil_mode, speechify_voice_id=None):
    """Return the versioned preferences record stored in browser localStorage."""
    preferences = {
        "version": PREFERENCES_VERSION,
        "target_language": target_language,
        "no_assimil_mode": bool(no_assimil_mode),
    }
    if isinstance(speechify_voice_id, str) and speechify_voice_id:
        preferences["speechify_voice_id"] = speechify_voice_id
    return json.dumps(preferences, ensure_ascii=False, separators=(",", ":"))


def restore_preferences_json(raw_value, valid_language_codes):
    """Validate an untrusted localStorage value and return known preferences."""
    if not isinstance(raw_value, str) or not raw_value:
        return None
    if len(raw_value.encode("utf-8")) > MAX_PREFERENCES_BYTES:
        return None

    try:
        preferences = json.loads(raw_value)
    except (TypeError, ValueError):
        return None

    if not isinstance(preferences, dict):
        return None
    if preferences.get("version") != PREFERENCES_VERSION:
        return None
    if preferences.get("target_language") not in valid_language_codes:
        return None
    if not isinstance(preferences.get("no_assimil_mode"), bool):
        return None

    restored = {
        "target_language": preferences["target_language"],
        "no_assimil_mode": preferences["no_assimil_mode"],
    }
    voice_id = preferences.get("speechify_voice_id")
    if isinstance(voice_id, str) and 0 < len(voice_id) <= MAX_VOICE_ID_LENGTH:
        restored["speechify_voice_id"] = voice_id
    return restored
