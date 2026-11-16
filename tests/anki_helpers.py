"""Exercise the real app exporter without starting the Streamlit UI."""

import ast
from contextlib import closing
import io
from pathlib import Path
import sqlite3
import tempfile
from unittest.mock import Mock
import zipfile


APP_PATH = Path(__file__).parents[1] / "app.py"


def exporter_namespace():
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    definitions = {
        "language_anki_ids", "build_anki_model", "DirectionalDeck", "build_anki_apkg",
        "get_lesson_tag", "card_highlight_errors",
    }
    constants = {
        "MODEL_ID", "FR2EN_DECK_ID", "EN2FR_DECK_ID", "TARGET_LANGUAGES",
        "FRONT_FR2EN", "BACK_FR2EN", "FRONT_EN2FR", "BACK_EN2FR", "CARD_STYLE",
    }
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        or (isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in definitions)
        or (isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in constants for target in node.targets
        ))
    ]
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(APP_PATH), "exec"), namespace)
    namespace["synthesize_french_audio"] = Mock(side_effect=lambda *args: b"test-audio:" + args[-1].encode())
    return namespace


TRANSLATIONS = {
    "en": ("hello", "thanks"),
    "it": ("buongiorno", "grazie"),
    "es": ("hola", "gracias"),
    "pt": ("olá", "obrigado"),
    "ko": ("안녕하세요", "감사합니다"),
    "ru": ("здравствуйте", "спасибо"),
    "nl": ("hallo", "bedankt"),
}


def sample_cards(language="en"):
    return [
        {
            "fr_word": french,
            "fr_phrase": f"Je dis &{french}& à Chloé.",
            "en_word": translated,
            "en_phrase": f"&{translated}&",
            "extra_notes": "Accents: é, è, œ; literal <b>text</b> & a newline\nSecond line.",
        }
        for french, translated in zip(("bonjour", "merci"), TRANSLATIONS[language])
    ]


def sample_package(language="en", namespace=None, cards=None, shared_tag="lesson_01"):
    namespace = namespace or exporter_namespace()
    return namespace["build_anki_apkg"](
        sample_cards(language) if cards is None else cards,
        "French Practice", "test-key", "test-voice",
        shared_tag=shared_tag, target_language_code=language,
    ).getvalue()


def rewrite_package(data, mutate=None, replacements=None, remove=()):
    with zipfile.ZipFile(io.BytesIO(data)) as original:
        entries = {name: original.read(name) for name in original.namelist()}
    if mutate:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "collection.sqlite"
            database.write_bytes(entries["collection.anki2"])
            with closing(sqlite3.connect(database)) as conn, conn:
                mutate(conn)
            entries["collection.anki2"] = database.read_bytes()
    entries.update(replacements or {})
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for name, value in entries.items():
            if name not in remove:
                package.writestr(name, value)
    return output.getvalue()
