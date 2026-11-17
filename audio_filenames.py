"""Readable, portable filenames for generated and reused French audio."""

import hashlib
import re
import unicodedata


def _slug(text, fallback, limit=64):
    text = text.casefold().replace("œ", "oe").replace("æ", "ae")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9_-]+", "_", text).strip("_-")[:limit].rstrip("_-") or fallback


def french_audio_filename(french_word, tags, audio_bytes, extension=".mp3"):
    """Keep words/tags recognizable and distinguish different audio contents."""
    if extension not in {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}:
        raise ValueError("Unsupported audio filename extension.")
    word_slug = _slug(french_word, "phrase")
    tag_slug = _slug(" ".join(sorted(set(tags.casefold().split()))), "untagged")
    digest = hashlib.sha256(audio_bytes).hexdigest()[:12]
    return f"fr_{word_slug}_{tag_slug}_{digest}{extension}"
