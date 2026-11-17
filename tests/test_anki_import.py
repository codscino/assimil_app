import io
import json
import unittest
from unittest.mock import patch
import zipfile

from anki_helpers import exporter_namespace, rewrite_package, sample_cards, sample_package
from anki_import import import_anki_apkg, reusable_french_audio
from draft_state import build_draft_json, restore_draft_json


class AnkiImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exporter = exporter_namespace()
        cls.languages = cls.exporter["TARGET_LANGUAGES"]
        cls.packages = {code: sample_package(code, cls.exporter) for code in cls.languages}

    def read(self, data=None):
        return import_anki_apkg(self.packages["en"] if data is None else data, self.languages)

    def change_models(self, edit, data=None):
        def mutate(conn):
            models = json.loads(conn.execute("SELECT models FROM col").fetchone()[0])
            edit(models)
            conn.execute("UPDATE col SET models=?", (json.dumps(models),))
        return rewrite_package(self.packages["en"] if data is None else data, mutate)

    def test_every_supported_language_detected_and_fields_decoded_once(self):
        for code, package in self.packages.items():
            with self.subTest(language=code):
                imported = self.read(package)
                self.assertEqual(imported.language_code, code)
                self.assertEqual(len(imported.cards), 2)  # Four directional Anki cards.
                self.assertEqual(imported.shared_tag, "lesson_01")
                self.assertFalse(imported.warnings)
                for actual, expected in zip(imported.cards, sample_cards(code)):
                    for field, value in expected.items():
                        self.assertEqual(actual[field], value)

    def test_round_trip_after_edit_preserves_guid_tags_audio_and_directions(self):
        for code, package in self.packages.items():
            with self.subTest(language=code):
                imported = self.read(package)
                imported.cards[0]["extra_notes"] = "Edited & preserved <notes>"
                imported.cards[0]["anki_tags"] = "personal::tag"
                self.exporter["synthesize_french_audio"].reset_mock()
                exported = self.exporter["build_anki_apkg"](
                    imported.cards, "French Practice", "", None,
                    shared_tag="replacement shared", target_language_code=code,
                    imported_audio=imported.audio,
                ).getvalue()
                self.exporter["synthesize_french_audio"].assert_not_called()
                with zipfile.ZipFile(io.BytesIO(exported)) as package:
                    media = json.loads(package.read("media"))
                    self.assertTrue(any(name.startswith("fr_bonjour_personal_tag_replacement_shared_")
                                        for name in media.values()))
                again = self.read(exported)
                self.assertEqual(again.cards[0]["anki_guid"], imported.cards[0]["anki_guid"])
                self.assertEqual(again.cards[0]["extra_notes"], "Edited & preserved <notes>")
                self.assertEqual(again.shared_tag, "replacement shared")
                self.assertEqual(again.cards[0]["anki_tags"], "personal::tag")
                self.assertEqual(again.audio, imported.audio)
                def verify(conn):
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0], 2)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0], 4)
                    deck_ids = self.exporter["language_anki_ids"](code)[1:]
                    self.assertEqual(set(conn.execute("SELECT DISTINCT ord,did FROM cards")),
                                     {(0, deck_ids[0]), (1, deck_ids[1])})
                rewrite_package(exported, verify)

    def test_readable_audio_names_match_note_references_and_include_all_tags(self):
        cards = sample_cards()
        cards[0]["anki_tags"] = "personal::food"
        data = sample_package(cards=cards, shared_tag="Pilsmeur_1-15")
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            media = json.loads(package.read("media"))
            self.assertEqual(len(media), 2)
            self.assertTrue(any(name.startswith("fr_bonjour_personal_food_pilsmeur_1-15_")
                                for name in media.values()))
            self.assertTrue(any(name.startswith("fr_merci_pilsmeur_1-15_")
                                for name in media.values()))

        def verify(conn):
            references = [row[0].split("\x1f")[-1] for row in conn.execute("SELECT flds FROM notes")]
            self.assertEqual(set(references), {f"[sound:{name}]" for name in media.values()})
        rewrite_package(data, verify)
        imported = self.read(data)
        self.assertFalse(imported.warnings)
        self.assertEqual(len(imported.audio), 2)

    def test_repeated_phrase_with_different_words_and_tags_is_synthesized_once(self):
        cards = [
            dict(sample_cards()[0], fr_word=word, fr_phrase=phrase, anki_tags=tag)
            for word, phrase, tag in (
                ("bonjour", "Je dis &bonjour& à Chloé.", "first"),
                ("Chloé", "Je dis bonjour à &Chloé&.", "second"),
            )
        ]
        self.exporter["synthesize_french_audio"].reset_mock()
        data = sample_package(namespace=self.exporter, cards=cards, shared_tag="lesson")
        self.exporter["synthesize_french_audio"].assert_called_once_with(
            "test-key", "test-voice", "Je dis bonjour à Chloé."
        )
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            media = json.loads(package.read("media"))
            self.assertEqual(len(media), 2)
            self.assertTrue(any(name.startswith("fr_bonjour_first_lesson_") for name in media.values()))
            self.assertTrue(any(name.startswith("fr_chloe_lesson_second_") for name in media.values()))

    def test_changed_french_phrase_generates_only_its_audio_and_keeps_guid(self):
        imported = self.read()
        guid = imported.cards[0]["anki_guid"]
        imported.cards[0]["fr_phrase"] = "Elle dit &bonjour&."
        self.exporter["synthesize_french_audio"].reset_mock()
        exported = self.exporter["build_anki_apkg"](
            imported.cards, "French Practice", "key", "voice",
            shared_tag=imported.shared_tag, imported_audio=imported.audio,
        ).getvalue()
        self.exporter["synthesize_french_audio"].assert_called_once_with("key", "voice", "Elle dit bonjour.")
        again = self.read(exported)
        self.assertEqual(again.cards[0]["anki_guid"], guid)
        self.assertEqual(again.cards[0]["fr_phrase"], "Elle dit &bonjour&.")

    def test_moving_highlight_or_editing_translation_reuses_french_audio(self):
        imported = self.read()
        card = imported.cards[0]
        card["fr_phrase"] = card["fr_phrase"].replace("&", "")
        card["en_word"] = "changed"
        self.assertIsNotNone(reusable_french_audio(card, imported.audio))

    def test_browser_draft_preserves_import_metadata_but_excludes_audio_bytes(self):
        imported = self.read()
        raw = build_draft_json(imported.cards, imported.cards, "en", "French Practice",
                               imported.shared_tag, True)
        restored = restore_draft_json(raw, "en", {})
        self.assertEqual(restored["cards_data"], imported.cards)
        self.assertNotIn("test-audio", raw)
        self.assertIsNone(reusable_french_audio(restored["cards_data"][0], {}))

    def test_per_note_tags_and_multiple_common_tags_are_preserved(self):
        def mutate(conn):
            ids = [row[0] for row in conn.execute("SELECT id FROM notes ORDER BY id")]
            conn.execute("UPDATE notes SET tags=' shared1 shared2 unique1 ' WHERE id=?", (ids[0],))
            conn.execute("UPDATE notes SET tags=' shared2 shared1 unique2 ' WHERE id=?", (ids[1],))
        imported = self.read(rewrite_package(self.packages["en"], mutate))
        self.assertEqual(imported.shared_tag, "shared1 shared2")
        self.assertEqual([card["anki_tags"] for card in imported.cards], ["unique1", "unique2"])

    def test_untagged_deck_does_not_gain_a_lesson_tag(self):
        imported = self.read(sample_package(shared_tag=""))
        self.assertEqual(imported.shared_tag, "")
        self.assertTrue(all(card["anki_tags"] == "" for card in imported.cards))

    def test_field_ordinals_used_even_if_model_list_is_reversed(self):
        package = self.change_models(lambda models: [model["flds"].reverse() for model in models.values()])
        self.assertEqual(self.read(package).cards, self.read().cards)

    def test_reordered_fields_in_anki_use_names_instead_of_fixed_positions(self):
        def mutate(conn):
            models = json.loads(conn.execute("SELECT models FROM col").fetchone()[0])
            for model in models.values():
                model["flds"] = list(reversed(model["flds"]))
                for ordinal, field in enumerate(model["flds"]):
                    field["ord"] = ordinal
            conn.execute("UPDATE col SET models=?", (json.dumps(models),))
            for note_id, fields in conn.execute("SELECT id,flds FROM notes").fetchall():
                conn.execute("UPDATE notes SET flds=? WHERE id=?", ("\x1f".join(reversed(fields.split("\x1f"))), note_id))
        imported = self.read(rewrite_package(self.packages["en"], mutate))
        self.assertEqual(imported.cards, self.read().cards)

    def test_old_five_field_model_without_audio_is_compatible(self):
        def mutate(conn):
            models = json.loads(conn.execute("SELECT models FROM col").fetchone()[0])
            for model in models.values():
                model["flds"].pop()
            conn.execute("UPDATE col SET models=?", (json.dumps(models),))
            for note_id, fields in conn.execute("SELECT id,flds FROM notes").fetchall():
                conn.execute("UPDATE notes SET flds=? WHERE id=?", ("\x1f".join(fields.split("\x1f")[:-1]), note_id))
        imported = self.read(rewrite_package(self.packages["en"], mutate))
        self.assertFalse(imported.audio)
        self.assertFalse(imported.warnings)

    def test_html_from_anki_editor_becomes_plain_text_with_line_breaks(self):
        def mutate(conn):
            note_id, fields = conn.execute("SELECT id,flds FROM notes ORDER BY id LIMIT 1").fetchone()
            values = fields.split("\x1f")
            values[4] = '<div>First <b>line</b> &amp; café</div><div>Second<br>third</div><script>bad()</script>'
            conn.execute("UPDATE notes SET flds=? WHERE id=?", ("\x1f".join(values), note_id))
        imported = self.read(rewrite_package(self.packages["en"], mutate))
        self.assertEqual(imported.cards[0]["extra_notes"], "First line & café\nSecond\nthird")

    def test_collection_anki21_supported_and_preferred_over_placeholder(self):
        with zipfile.ZipFile(io.BytesIO(self.packages["it"])) as package:
            database = package.read("collection.anki2")
        data = rewrite_package(self.packages["en"], replacements={"collection.anki21": database})
        self.assertEqual(self.read(data).language_code, "it")

    def test_new_compressed_anki_format_gets_actionable_error(self):
        data = rewrite_package(self.packages["en"], replacements={"collection.anki21b": b"compressed"})
        with self.assertRaisesRegex(ValueError, "older Anki versions"):
            self.read(data)

    def test_missing_audio_warns_and_leaves_cards_editable(self):
        imported = self.read(rewrite_package(self.packages["en"], remove=("0",)))
        self.assertEqual(len(imported.cards), 2)
        self.assertEqual(len(imported.warnings), 1)
        self.assertEqual(len(imported.audio), 1)
        self.assertNotIn("fr_audio_filename", imported.cards[0])

    def test_repeated_audio_file_is_loaded_once(self):
        cards = [sample_cards()[0], dict(sample_cards()[0], extra_notes="second entry")]
        package = sample_package(cards=cards)
        reads = []
        original_read = zipfile.ZipFile.read
        def read_once(package, name, *args, **kwargs):
            reads.append(name)
            return original_read(package, name, *args, **kwargs)
        with patch.object(zipfile.ZipFile, "read", read_once):
            imported = self.read(package)
        self.assertEqual(len(imported.cards), 2)
        self.assertEqual(len(imported.audio), 1)
        self.assertEqual(reads.count("0"), 1)

    def test_archive_paths_are_never_extracted_and_audio_names_are_normalized(self):
        with zipfile.ZipFile(io.BytesIO(self.packages["en"])) as package:
            manifest = json.loads(package.read("media"))
        old_name = manifest["0"]
        manifest["0"] = "../../escape.mp3"
        def mutate(conn):
            conn.execute("UPDATE notes SET flds=replace(flds, ?, ?)", (old_name, manifest["0"]))
        imported = self.read(rewrite_package(self.packages["en"], mutate, {
            "media": json.dumps(manifest), "../../not-extracted": b"ignored",
        }))
        self.assertNotIn("/", imported.cards[0]["fr_audio_filename"])
        self.assertIsNotNone(reusable_french_audio(imported.cards[0], imported.audio))

    def test_untrusted_saved_audio_path_cannot_escape_export_directory(self):
        card = {"fr_word": "bonjour", "fr_audio_text": "bonjour", "fr_audio_filename": "../../escape.mp3"}
        self.assertIsNone(reusable_french_audio(card, {"../../escape.mp3": b"audio"}))

    def test_invalid_archives_and_database_are_rejected(self):
        for data in (b"", b"not a zip", self.packages["en"][:100],
                     rewrite_package(self.packages["en"], remove=("collection.anki2",)),
                     rewrite_package(self.packages["en"], replacements={"collection.anki2": b"not sqlite"})):
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                self.read(data)

    def test_empty_collection_and_note_limit_rejected(self):
        with self.assertRaisesRegex(ValueError, "no flashcards"):
            self.read(rewrite_package(self.packages["en"], lambda conn: conn.execute("DELETE FROM notes")))
        with patch("anki_import.MAX_NOTES", 1), self.assertRaisesRegex(ValueError, "entries at a time"):
            self.read()

    def test_package_size_limits_checked(self):
        with patch("anki_import.MAX_PACKAGE_BYTES", 10), self.assertRaisesRegex(ValueError, "50 MB"):
            self.read()
        with patch("anki_import.MAX_EXPANDED_BYTES", 10), self.assertRaisesRegex(ValueError, "unpacked"):
            self.read()
        with patch("anki_import.MAX_DRAFT_BYTES", 10), self.assertRaisesRegex(ValueError, "editable text"):
            self.read()

    def test_hundreds_of_entries_reuse_media_and_recover_as_a_draft(self):
        cards = [dict(sample_cards()[index % 2], extra_notes=f"Entry {index}") for index in range(500)]
        imported = self.read(sample_package(cards=cards))
        self.assertEqual(len(imported.cards), 500)
        self.assertEqual(len(imported.audio), 2)
        draft = build_draft_json(imported.cards, imported.cards, "en", "French Practice", "", True)
        self.assertEqual(len(restore_draft_json(draft, "en", {})["cards_data"]), 500)

    def test_malformed_field_metadata_shapes_fail_with_readable_errors(self):
        values = (None, True, 3, [], {}, "string", [None], [{"name": "fr_word", "ord": []}])
        for value in values:
            with self.subTest(fields=value), self.assertRaises(ValueError):
                self.read(self.change_models(lambda models: [model.update(flds=value) for model in models.values()]))

    def test_missing_manifest_and_empty_audio_warn_without_crashing(self):
        for kwargs in ({"remove": ("media",)}, {"replacements": {"0": b"", "1": b""}}):
            with self.subTest(kwargs=kwargs):
                imported = self.read(rewrite_package(self.packages["en"], **kwargs))
                self.assertEqual(len(imported.cards), 2)
                self.assertFalse(imported.audio)
                self.assertEqual(len(imported.warnings), 1)

    def test_duplicate_archive_entries_rejected(self):
        output = io.BytesIO(self.packages["en"])
        with zipfile.ZipFile(output, "a") as package:
            with self.assertWarns(UserWarning):
                package.writestr("media", "{}")
        with self.assertRaisesRegex(ValueError, "duplicate archive"):
            self.read(output.getvalue())

    def test_incompatible_models_are_rejected_including_unsupported_language(self):
        edits = [
            lambda model: model.update(type=1),
            lambda model: model["flds"][0].update(name="Front"),
            lambda model: model["flds"][2].update(name="ja_word"),
            lambda model: model["flds"][0].update(ord=99),
            lambda model: model["flds"][1].update(name="fr_word"),
            lambda model: model["flds"][5].update(name="unknown_extra"),
            lambda model: model.update(flds="invalid"),
        ]
        for edit in edits:
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.read(self.change_models(lambda models: [edit(model) for model in models.values()]))

    def test_malformed_model_json_and_media_manifest_rejected(self):
        for raw in ("not json", "[]", "null", '{"0":42}', '{"0":"a.mp3","1":"a.mp3"}', '{"../0":"a.mp3"}'):
            with self.subTest(manifest=raw), self.assertRaises(ValueError):
                self.read(rewrite_package(self.packages["en"], replacements={"media": raw}))
        for raw in ("not json", "[]", "null", "{}"):
            with self.subTest(models=raw), self.assertRaises(ValueError):
                self.read(rewrite_package(self.packages["en"], lambda conn: conn.execute("UPDATE col SET models=?", (raw,))))

    def test_incorrect_field_count_and_blank_word_rejected(self):
        def mutate(conn, value):
            conn.execute("UPDATE notes SET flds=?", (value,))
        for fields in ("one\x1ftwo", "\x1f".join(["", "phrase", "word", "phrase", "notes", ""])):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.read(rewrite_package(self.packages["en"], lambda conn: mutate(conn, fields)))

    def test_mixed_language_package_rejected_as_a_whole(self):
        def mutate(conn):
            models = json.loads(conn.execute("SELECT models FROM col").fetchone()[0])
            italian = self.exporter["build_anki_model"]("it").to_json(0, 0)
            models[str(italian["id"])] = italian
            conn.execute("UPDATE col SET models=?", (json.dumps(models),))
            conn.execute("UPDATE notes SET mid=? WHERE id=(SELECT max(id) FROM notes)", (italian["id"],))
        with self.assertRaisesRegex(ValueError, "mixes target languages"):
            self.read(rewrite_package(self.packages["en"], mutate))

    def test_unsupported_note_among_compatible_notes_prevents_partial_import(self):
        def mutate(conn):
            conn.execute("UPDATE notes SET mid=999 WHERE id=(SELECT max(id) FROM notes)")
        with self.assertRaisesRegex(ValueError, "incompatible Anki note type"):
            self.read(rewrite_package(self.packages["en"], mutate))

    def test_database_views_are_not_evaluated(self):
        def mutate(conn):
            conn.execute("ALTER TABLE notes RENAME TO actual_notes")
            conn.execute("CREATE VIEW notes AS SELECT * FROM actual_notes")
        with self.assertRaisesRegex(ValueError, "compatible Anki collection"):
            self.read(rewrite_package(self.packages["en"], mutate))


if __name__ == "__main__":
    unittest.main()
