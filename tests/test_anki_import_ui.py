import json
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from anki_helpers import APP_PATH, exporter_namespace, rewrite_package, sample_cards, sample_package
from anki_import import import_anki_apkg


class AnkiImportUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exporter = exporter_namespace()
        cls.packages = {
            code: sample_package(code, cls.exporter)
            for code in cls.exporter["TARGET_LANGUAGES"]
        }

    def setUp(self):
        self.storage = {}
        self.storage_calls = []

        def storage_component(**props):
            if "storage_key" not in props:
                return None
            self.storage_calls.append(props)
            key = props.get("storage_key")
            if props["action"] == "write":
                self.storage[key] = props["draft"]
            elif props["action"] == "clear":
                self.storage.pop(key, None)
            value = self.storage.get(key)
            return {"status": "ready", "value": value} if props.get("structured_result") else value

        for name, kwargs in (
            ("streamlit.components.v1.declare_component", {"return_value": storage_component}),
            ("speechify_audio.list_french_voices", {"return_value": [{
                "id": "test-voice", "display_name": "Test voice", "locale": "fr-FR", "gender": "female",
            }]}),
            ("speechify_audio.synthesize_french_audio", {"return_value": b"new-audio"}),
            ("google.genai.Client", {}),
        ):
            mock = patch(name, **kwargs)
            setattr(self, name.split(".")[-1], mock.start())
            self.addCleanup(mock.stop)
        self.app = self.new_app()

    def new_app(self, **state):
        app = AppTest.from_file(str(APP_PATH), default_timeout=15)
        app.secrets["GEMINI_API_KEY"] = ""
        app.secrets["SPEECHIFY_API_KEY"] = ""
        for key, value in state.items():
            app.session_state[key] = value
        app.run()
        self.assertFalse(app.exception)
        return app

    def assert_healthy(self):
        self.assertFalse(self.app.exception)

    def open_import(self):
        self.app.button(key="import_flashcards").click().run()
        self.assert_healthy()
        self.assertEqual(len(self.app.file_uploader), 1)

    def upload(self, code="it", data=None):
        self.open_import()
        self.app.file_uploader[0].set_value(("sample.apkg", self.packages[code] if data is None else data,
                                              "application/octet-stream")).run()
        self.assert_healthy()

    def language_toasts(self):
        return [toast for toast in self.app.get("toast") if "Target language changed" in toast.proto.body]

    def test_initial_button_opens_apkg_picker_without_api_keys(self):
        self.assertEqual(self.app.button(key="import_flashcards").label, "📥 Import Flashcards")
        self.assertEqual(self.app.button(key="import_flashcards").proto.type, "secondary")
        self.open_import()
        self.assertEqual(list(self.app.file_uploader[0].proto.type), [".apkg"])

    def test_import_all_languages_updates_editor_without_network_calls(self):
        for code in self.packages:
            with self.subTest(language=code):
                self.storage.clear()
                self.app = self.new_app()
                self.upload(code)
                self.assertEqual(self.app.selectbox(key="target_language").value, code)
                self.assertEqual(self.app.session_state.cards_target_language, code)
                self.assertEqual(len(self.app.session_state.cards_data), 2)
                self.assertEqual(len(self.app.expander), 2)
                self.assertEqual(len(self.app.file_uploader), 0)
                self.assertNotIn("import_flashcards", [button.key for button in self.app.button])
                self.assertIn(self.exporter["TARGET_LANGUAGES"][code]["name"] + " Word",
                              [field.label for field in self.app.text_input])
                self.assertEqual(self.app.session_state.cards_data[0]["en_word"], sample_cards(code)[0]["en_word"])
        self.Client.assert_not_called()
        self.synthesize_french_audio.assert_not_called()
        self.list_french_voices.assert_not_called()

    def test_language_change_toast_disappears_and_does_not_repeat_on_edit(self):
        self.upload("it")
        toasts = self.language_toasts()
        self.assertEqual(len(toasts), 1)
        self.assertIn("English to Italian", toasts[0].proto.body)
        self.assertEqual(toasts[0].proto.duration, 6)
        next(field for field in self.app.text_input if field.label == "Notes").set_value("Edited").run()
        self.assert_healthy()
        self.assertFalse(self.language_toasts())
        self.assertEqual(self.app.session_state.cards_data[0]["extra_notes"], "Edited")

    def test_same_language_import_has_no_language_change_toast(self):
        self.upload("en")
        self.assertFalse(self.language_toasts())

    def test_switch_to_detected_language_does_not_clear_imported_cards_on_rerun(self):
        self.upload("ko")
        for _ in range(3):
            self.app.run()
            self.assert_healthy()
            self.assertEqual(len(self.app.session_state.cards_data), 2)
            self.assertEqual(self.app.session_state.target_language, "ko")
        self.assertFalse(any("Generate a new translated deck" in toast.proto.body for toast in self.app.get("toast")))

    def test_imported_preference_and_draft_survive_new_session(self):
        self.upload("ru")
        self.assertEqual(json.loads(self.storage["assimil-preferences-v1"])["target_language"], "ru")
        self.app = self.new_app()
        self.assertEqual(self.app.selectbox(key="target_language").value, "ru")
        self.assertEqual(len(self.app.session_state.cards_data), 2)
        self.assertTrue(self.app.session_state.cards_data[0]["anki_guid"])
        self.assertNotIn("import_flashcards", [button.key for button in self.app.button])

    def test_invalid_upload_preserves_state_and_allows_retry(self):
        self.app.selectbox(key="target_language").select("nl").run()
        self.upload(data=b"not a package")
        self.assertIsNone(self.app.session_state.cards_data)
        self.assertEqual(self.app.session_state.target_language, "nl")
        self.assertTrue(self.app.error)
        self.assertFalse(self.language_toasts())
        self.app.file_uploader[0].set_value(("italian.apkg", self.packages["it"], "application/octet-stream")).run()
        self.assert_healthy()
        self.assertEqual(self.app.session_state.target_language, "it")
        self.assertEqual(len(self.app.session_state.cards_data), 2)

    def test_import_into_empty_list_and_reappearance_after_last_card_deleted(self):
        self.app = self.new_app(cards_data=[])
        self.upload("it")
        for remaining in (1, 0):
            next(button for button in self.app.button if button.label == "🗑️ Delete card").click().run()
            next(button for button in self.app.button if button.label == "Delete").click().run()
            self.assert_healthy()
            self.assertEqual(len(self.app.session_state.cards_data), remaining)
            self.assertEqual("import_flashcards" in [button.key for button in self.app.button], remaining == 0)
        self.upload("es")
        self.assertEqual(len(self.app.session_state.cards_data), 2)
        self.assertEqual(self.app.session_state.target_language, "es")

    def test_reset_allows_same_package_to_be_imported_again(self):
        self.upload("it")
        next(button for button in self.app.button if button.label == "🗑️ Reset All Cards").click().run()
        self.assert_healthy()
        self.assertIsNone(self.app.session_state.cards_data)
        self.upload("it")
        self.assertEqual(len(self.app.session_state.cards_data), 2)

    def test_add_card_hides_import_when_list_was_empty(self):
        self.app = self.new_app(cards_data=[])
        self.assertIn("import_flashcards", [button.key for button in self.app.button])
        self.app.button(key="add_card").click().run()
        self.assert_healthy()
        self.assertNotIn("import_flashcards", [button.key for button in self.app.button])

    def test_existing_cards_cannot_be_overwritten_by_a_stale_import(self):
        cards = sample_cards()
        imported = import_anki_apkg(self.packages["it"], self.exporter["TARGET_LANGUAGES"])
        self.app = self.new_app(cards_data=cards, pending_anki_import=imported)
        self.assertEqual(self.app.session_state.cards_data, cards)
        self.assertEqual(self.app.session_state.target_language, "en")

    def test_tagless_import_overrides_lesson_defaults(self):
        self.upload(data=sample_package("it", shared_tag=""))
        self.assertEqual(self.app.session_state.shared_tag, "")
        self.assertEqual(self.app.text_input(key="shared_tag_editor").value, "")
        self.assertTrue(self.app.toggle(key="no_assimil_mode").value)

    def test_edit_export_reimport_keeps_content_and_audio_without_api_keys(self):
        self.upload("it")
        next(field for field in self.app.text_input if field.label == "Notes").set_value("Edited notes & accents é").run()
        export_button = next(button for button in self.app.button if button.label == "📦 Approve All & Download .apkg")
        self.assertFalse(export_button.disabled)
        export_button.click().run()
        self.assert_healthy()
        exported = self.app.session_state.prepared_apkg
        imported = import_anki_apkg(exported, self.exporter["TARGET_LANGUAGES"])
        self.assertEqual(imported.cards[0]["extra_notes"], "Edited notes & accents é")
        self.assertEqual(imported.language_code, "it")
        self.assertEqual(len(imported.audio), 2)
        self.synthesize_french_audio.assert_not_called()
        self.app.run()
        self.assert_healthy()
        self.assertEqual(self.app.session_state.cards_data[0]["extra_notes"], "Edited notes & accents é")
        next(button for button in self.app.button if button.label == "🗑️ Reset All Cards").click().run()
        self.assertIn("import_flashcards", [button.key for button in self.app.button])
        self.upload(data=exported)
        self.assertEqual(self.app.session_state.cards_data[0]["extra_notes"], "Edited notes & accents é")

    def test_export_preserves_work_across_reruns_and_session_restore(self):
        self.upload("it")
        self.app.text_area(key="target_words").set_value("bonjour (keep my input)").run()
        self.app.text_input(key="shared_tag_editor").set_value("custom-tag").run()
        next(field for field in self.app.text_input if field.label == "Notes").set_value("Keep this edit").run()
        preserved_keys = (
            "cards_data", "card_regeneration_baselines", "card_form_epoch",
            "card_form_versions", "imported_audio", "target_words",
            "shared_tag", "selected_lesson", "target_language", "no_assimil_mode",
        )
        before_export = deepcopy({key: self.app.session_state[key] for key in preserved_keys})
        saved_draft = self.storage["assimil-flashcard-draft-v1"]
        self.storage_calls.clear()

        next(button for button in self.app.button if button.label == "📦 Approve All & Download .apkg").click().run()
        for _ in range(3):
            self.assert_healthy()
            self.assertEqual(
                {key: self.app.session_state[key] for key in preserved_keys}, before_export
            )
            self.assertEqual(self.storage["assimil-flashcard-draft-v1"], saved_draft)
            self.assertEqual(self.storage["assimil-target-words-v1"], before_export["target_words"])
            self.assertNotIn("apkg_auto_download_signature", self.app.session_state)
            self.app.run()
        self.assertFalse(any(call["action"] == "clear" for call in self.storage_calls))

        self.app = self.new_app()
        for key in ("cards_data", "card_regeneration_baselines", "target_words",
                    "shared_tag", "target_language", "no_assimil_mode"):
            self.assertEqual(self.app.session_state[key], before_export[key])

    def test_edited_french_requires_fresh_audio_and_uses_selected_voice(self):
        self.upload("it")
        next(field for field in self.app.text_area if field.label == "French Sentence").set_value("Elle dit &bonjour&.").run()
        self.assert_healthy()
        export_button = next(button for button in self.app.button if button.label == "📦 Approve All & Download .apkg")
        self.assertTrue(export_button.disabled)
        self.assertTrue(any("SPEECHIFY_API_KEY" in item.value for item in self.app.error))
        self.app.secrets["SPEECHIFY_API_KEY"] = "test-key"
        self.app.run()
        self.assert_healthy()
        next(button for button in self.app.button if button.label == "📦 Approve All & Download .apkg").click().run()
        self.assert_healthy()
        self.synthesize_french_audio.assert_called_once_with("test-key", "test-voice", "Elle dit bonjour.")

    def test_regeneration_keeps_imported_guid_tags_and_audio_metadata(self):
        self.upload("it")
        original = dict(self.app.session_state.cards_data[0])
        self.app.secrets["GEMINI_API_KEY"] = "test-key"
        self.Client.return_value.models.generate_content.return_value = SimpleNamespace(
            text=json.dumps(sample_cards("it")[0])
        )
        next(button for button in self.app.button if button.label == "🔄 Regenerate").click().run()
        self.assert_healthy()
        current = self.app.session_state.cards_data[0]
        for field in ("anki_guid", "anki_tags", "fr_audio_filename", "fr_audio_text"):
            self.assertEqual(current[field], original[field])

    def test_missing_audio_import_shows_warning_and_previews_cards(self):
        self.upload(data=rewrite_package(self.packages["it"], remove=("0",)))
        self.assertEqual(len(self.app.expander), 2)
        self.assertTrue(any("audio files are missing" in warning.value for warning in self.app.warning))


if __name__ == "__main__":
    unittest.main()
