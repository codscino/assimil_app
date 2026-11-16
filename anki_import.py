"""Read compatible Anki packages without executing templates or extracting paths."""

from dataclasses import dataclass
from contextlib import closing
import hashlib
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import zipfile
import zlib

from highlight_markup import strip_highlight_markers
from draft_state import MAX_DRAFT_BYTES, build_draft_json


MAX_PACKAGE_BYTES = 50 * 1024 * 1024
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_NOTES = 5000


@dataclass
class ImportedDeck:
    cards: list[dict]
    language_code: str
    shared_tag: str
    audio: dict[str, bytes]
    warnings: list[str]


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif not self.hidden and tag in {"br", "div", "p"}:
            if tag == "br" or (self.parts and not self.parts[-1].endswith("\n")):
                self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        elif not self.hidden and tag in {"div", "p"}:
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _plain_text(value):
    parser = _PlainText()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts).strip("\n")


def _model_fields(model, supported_languages):
    if not isinstance(model, dict) or model.get("type", 0) != 0:
        raise ValueError("This package uses an incompatible Anki note type.")
    fields = model.get("flds")
    if not isinstance(fields, list) or not all(isinstance(f, dict) for f in fields):
        raise ValueError("The package has invalid note fields.")
    # The database field separator follows field ordinals, not JSON list order.
    if sorted(f.get("ord", -1) for f in fields) != list(range(len(fields))):
        raise ValueError("The package has invalid field ordering.")
    names = [f.get("name") for f in sorted(fields, key=lambda f: f["ord"])]
    if not all(isinstance(name, str) for name in names) or len(set(names)) != len(names):
        raise ValueError("The package has invalid or duplicate field names.")
    base = {"fr_word", "fr_phrase", "extra_notes"}
    languages = [
        code for code in supported_languages
        if {f"{code}_word", f"{code}_phrase"}.issubset(names)
    ]
    if not base.issubset(names) or len(languages) != 1:
        raise ValueError(
            "Incompatible flashcard structure. Expected fr_word, fr_phrase, "
            "extra_notes, and one supported target-language word/phrase pair."
        )
    language = languages[0]
    allowed = base | {f"{language}_word", f"{language}_phrase", "fr_audio"}
    if set(names) - allowed:
        raise ValueError("This note type has extra fields the editor cannot preserve.")
    return names, language


def _read_notes(database, supported_languages):
    with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro&immutable=1", uri=True)) as conn:
        conn.execute("PRAGMA trusted_schema = OFF")
        conn.execute("PRAGMA query_only = ON")
        # Only read actual tables, never a user-supplied view.
        tables = dict(conn.execute(
            "SELECT name, type FROM sqlite_master WHERE name IN ('col', 'notes')"
        ))
        if tables != {"col": "table", "notes": "table"}:
            raise ValueError("The package does not contain a compatible Anki collection.")
        row = conn.execute("SELECT models FROM col").fetchone()
        models = json.loads(row[0]) if row else None
        if not isinstance(models, dict):
            raise ValueError("The package has invalid Anki models.")
        rows = conn.execute(
            "SELECT guid, mid, flds, tags FROM notes ORDER BY id LIMIT ?",
            (MAX_NOTES + 1,),
        ).fetchall()
    if not rows:
        raise ValueError("This package contains no flashcards.")
    if len(rows) > MAX_NOTES:
        raise ValueError(f"Import up to {MAX_NOTES:,} flashcard entries at a time.")

    cards, languages, schemas = [], set(), {}
    for guid, model_id, raw_fields, tags in rows:
        if model_id not in schemas:
            schemas[model_id] = _model_fields(models.get(str(model_id)), supported_languages)
        names, language = schemas[model_id]
        if not isinstance(raw_fields, str) or not isinstance(tags, str) or not isinstance(guid, str):
            raise ValueError("The package has invalid note data.")
        values = raw_fields.split("\x1f")
        if len(values) != len(names):
            raise ValueError("A note's fields do not match its Anki model.")
        fields = dict(zip(names, values))
        card = {
            "fr_word": _plain_text(fields["fr_word"]),
            "fr_phrase": _plain_text(fields["fr_phrase"]),
            "en_word": _plain_text(fields[f"{language}_word"]),
            "en_phrase": _plain_text(fields[f"{language}_phrase"]),
            "extra_notes": _plain_text(fields["extra_notes"]),
            "anki_guid": guid,
            "anki_tags": " ".join(tags.split()),
        }
        if not card["fr_word"].strip() or not card["en_word"].strip():
            raise ValueError("Each imported note needs a French word and a translated word.")
        card["fr_audio_filename"] = fields.get("fr_audio", "").strip()
        cards.append(card)
        languages.add(language)
    if len(languages) != 1:
        raise ValueError("This package mixes target languages. Import one language at a time.")

    common_tags = set(cards[0]["anki_tags"].split())
    for card in cards[1:]:
        common_tags.intersection_update(card["anki_tags"].split())
    shared_tag = " ".join(sorted(common_tags))
    for card in cards:
        card["anki_tags"] = " ".join(
            tag for tag in card["anki_tags"].split() if tag not in common_tags
        )
    return cards, languages.pop(), shared_tag


def import_anki_apkg(data, supported_languages):
    """Validate the complete package before returning any state to the UI."""
    if not data or len(data) > MAX_PACKAGE_BYTES:
        raise ValueError("Choose a nonempty .apkg file up to 50 MB.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            entries = package.infolist()
            if len(entries) > 20_000 or sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
                raise ValueError("This package is too large when unpacked.")
            names = [entry.filename for entry in entries]
            if len(set(names)) != len(names):
                raise ValueError("The package contains duplicate archive entries.")
            # New Anki exports can include a legacy placeholder. Do not mistake
            # that placeholder for the user's real collection.
            if "collection.anki21b" in names:
                raise ValueError(
                    "This package uses Anki's newer compressed format. "
                    "Export it from Anki with support for older Anki versions enabled."
                )
            collection = next(
                (name for name in ("collection.anki21", "collection.anki2") if name in names),
                None,
            )
            if not collection:
                raise ValueError("This file does not contain an Anki collection.")
            if package.getinfo(collection).file_size > MAX_PACKAGE_BYTES:
                raise ValueError("The Anki collection is too large to import.")
            with tempfile.TemporaryDirectory() as directory:
                database = Path(directory) / "collection.sqlite"
                database.write_bytes(package.read(collection))
                cards, language, shared_tag = _read_notes(database, supported_languages)

            manifest = json.loads(package.read("media")) if "media" in names else {}
            if not isinstance(manifest, dict) or not all(
                isinstance(key, str) and re.fullmatch(r"[0-9]+", key)
                and isinstance(value, str) for key, value in manifest.items()
            ):
                raise ValueError("The package has an invalid media manifest.")
            if len(set(manifest.values())) != len(manifest):
                raise ValueError("The package has duplicate media filenames.")
            media_by_name = {filename: key for key, filename in manifest.items()}
            audio, warnings, loaded_media = {}, [], {}
            for card in cards:
                sound = card.pop("fr_audio_filename")
                if not sound:
                    continue
                match = re.fullmatch(r"\[sound:([^\]\r\n]+)\]", sound)
                if not match:
                    raise ValueError("A French audio field has an unsupported format.")
                original = match[1]
                media_key = media_by_name.get(original)
                if media_key not in loaded_media:
                    audio_bytes = package.read(media_key) if media_key in names else b""
                    suffix = Path(original).suffix.lower()
                    if suffix not in {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}:
                        suffix = ".mp3"
                    filename = f"imported_{hashlib.sha256(audio_bytes).hexdigest()}{suffix}"
                    loaded_media[media_key] = (filename, audio_bytes)
                filename, audio_bytes = loaded_media[media_key]
                if not audio_bytes:
                    if not warnings:
                        warnings.append(
                            "Some French audio files are missing. Fresh audio will be needed on export."
                        )
                    continue
                audio[filename] = audio_bytes
                card["fr_audio_filename"] = filename
                card["fr_audio_text"] = strip_highlight_markers(
                    card["fr_phrase"] or card["fr_word"]
                ).strip()
            # Imported drafts use the same browser recovery limit as generated
            # drafts. Reject an oversized draft before replacing the user's state.
            draft = build_draft_json(cards, cards, language, "French Practice", shared_tag, True)
            if len(draft.encode("utf-8")) > MAX_DRAFT_BYTES:
                raise ValueError("This package has too much editable text. Import a smaller deck.")
            return ImportedDeck(cards, language, shared_tag, audio, warnings)
    except (zipfile.BadZipFile, sqlite3.Error, json.JSONDecodeError, UnicodeError,
            KeyError, TypeError, RuntimeError, NotImplementedError, EOFError, zlib.error) as error:
        raise ValueError("This .apkg file is damaged or has an unsupported collection structure.") from error


def reusable_french_audio(card, imported_audio):
    """Only reuse audio while the spoken French text still matches."""
    text = strip_highlight_markers(card.get("fr_phrase") or card.get("fr_word", "")).strip()
    filename = card.get("fr_audio_filename", "")
    # Draft metadata is untrusted. Never allow a filename to escape the export directory.
    if not re.fullmatch(r"imported_[a-f0-9]{64}\.(mp3|wav|ogg|m4a|flac|aac)", filename):
        return None
    if card.get("fr_audio_text") != text:
        return None
    data = imported_audio.get(filename)
    return (filename, data) if isinstance(data, bytes) and data else None
