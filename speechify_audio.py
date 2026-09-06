"""Speechify text-to-speech helpers for French Anki audio."""

import base64
import html

from speechify import Speechify


SPEECHIFY_MODEL = "simba-3.0"
SPEECHIFY_LANGUAGE = "fr-FR"
SPEECHIFY_RATE = "-30%"


def _value(item, name, default=None):
    """Read a field from either an SDK model or a plain dictionary."""
    value = (
        item.get(name, default)
        if isinstance(item, dict)
        else getattr(item, name, default)
    )
    return getattr(value, "value", value)


def list_french_voices(api_key):
    """Return all account voices compatible with French on Simba 3."""
    client = Speechify(token=api_key)
    # The SDK's pager follows every API cursor as it is iterated.
    response = client.voices.list(locale="fr", model=SPEECHIFY_MODEL, limit=200)
    return [
        {
            "id": _value(voice, "id"),
            "display_name": _value(voice, "display_name", "Unnamed voice"),
            "gender": _value(voice, "gender", "not specified"),
            "locale": _value(voice, "locale", SPEECHIFY_LANGUAGE),
            "type": _value(voice, "type", "voice"),
        }
        for voice in response
    ]


def synthesize_french_audio(api_key, voice_id, text):
    """Generate a French MP3 at 0.7x speed using Speechify's Simba 3 model."""
    escaped_text = html.escape(text, quote=True)
    ssml = (
        f'<speak><prosody rate="{SPEECHIFY_RATE}">'
        f"{escaped_text}</prosody></speak>"
    )
    response = Speechify(token=api_key).audio.speech(
        input=ssml,
        voice_id=voice_id,
        model=SPEECHIFY_MODEL,
        language=SPEECHIFY_LANGUAGE,
        audio_format="mp3",
    )
    return base64.b64decode(response.audio_data, validate=True)
