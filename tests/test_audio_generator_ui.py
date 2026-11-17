import base64
import json
from pathlib import Path
import unittest
from unittest.mock import call, patch

import streamlit as st
from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).parents[1] / "app.py"
VOICES = [
    {"id": voice_id, "display_name": name, "locale": "fr-FR", "gender": gender}
    for voice_id, name, gender in (
        ("amelie", "Amélie", "female"), ("henri", "Henri", "male")
    )
]


class AudioGeneratorUITests(unittest.TestCase):
    def setUp(self):
        st.cache_data.clear()
        self.addCleanup(st.cache_data.clear)
        self.storage = {}

        def storage_component(**props):
            key = props.get("storage_key")
            if props.get("action") == "write":
                self.storage[key] = props["draft"]
            elif props.get("action") == "clear":
                self.storage.pop(key, None)
            value = self.storage.get(key)
            return {"status": "ready", "value": value} if props.get("structured_result") else value

        for name, kwargs in (
            ("streamlit.components.v1.declare_component", {"return_value": storage_component}),
            ("speechify_audio.list_french_voices", {"return_value": VOICES}),
            ("speechify_audio.synthesize_french_audio", {
                "side_effect": lambda api_key, voice, text: f"MP3:{voice}:{text}".encode(),
            }),
            ("google.genai.Client", {}),
            ("streamlit.download_button", {"wraps": st.download_button}),
        ):
            mock = patch(name, **kwargs)
            setattr(self, name.split(".")[-1], mock.start())
            self.addCleanup(mock.stop)
        self.app = self.new_app()

    def new_app(self, **state):
        app = AppTest.from_file(str(APP_PATH), default_timeout=15)
        app.secrets["GEMINI_API_KEY"] = ""
        app.secrets["SPEECHIFY_API_KEY"] = "audio-test-key"
        for key, value in state.items():
            app.session_state[key] = value
        app.run()
        self.assertFalse(app.exception)
        return app

    def open_mode(self, text="Bonjour !"):
        self.app.text_area(key="target_words").set_value(text).run()
        self.app.button(key="generate_fix_audio").click().run()
        self.assertFalse(self.app.exception)

    def preview_buttons(self):
        return [button for button in self.app.button if button.label == "▶ Generate preview"]

    def test_mode_available_only_without_cards_and_can_be_closed(self):
        self.assertEqual(self.app.button(key="generate_fix_audio").label, "🔊 Generate/fix audio")
        self.open_mode()
        self.synthesize_french_audio.assert_not_called()
        self.app.button(key="close_audio_mode").click().run()
        self.assertFalse(self.app.exception)
        self.assertFalse(self.app.get("audio"))
        self.assertFalse(self.preview_buttons())
        self.assertEqual(self.app.text_area(key="target_words").value, "Bonjour !")

        self.app = self.new_app(cards_data=[])
        self.open_mode()
        self.app.button(key="add_card").click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn("generate_fix_audio", [button.key for button in self.app.button])
        self.assertNotIn("audio_mode", self.app.session_state)
        self.assertFalse(self.preview_buttons())

    def test_every_nonempty_line_is_spoken_literally_and_download_matches_preview(self):
        phrases = ["Bonjour (à tous) !", "- Café & thé : c’est bon."]
        self.open_mode("  " + phrases[0] + "  \r\n\n" + phrases[1] + "\n  ")
        self.download_button.reset_mock()
        self.app.button(key="generate_all_audio").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.synthesize_french_audio.call_args_list, [
            call("audio-test-key", "amelie", phrase) for phrase in phrases
        ])
        self.assertIsNone(self.app.session_state.cards_data)
        self.Client.assert_not_called()
        self.assertEqual(len(self.app.get("audio")), 2)
        downloads = self.download_button.call_args_list
        self.assertEqual(len(downloads), 2)
        for phrase, player, download in zip(phrases, self.app.get("audio"), downloads):
            expected = f"MP3:amelie:{phrase}".encode()
            self.assertEqual(base64.b64decode(player.proto.url.split(",", 1)[1]), expected)
            self.assertEqual(download.kwargs["data"], expected)
            self.assertEqual(download.kwargs["mime"], "audio/mpeg")
            self.assertTrue(download.kwargs["file_name"].endswith(".mp3"))
            self.assertNotIn("/", download.kwargs["file_name"])
            self.assertEqual(download.kwargs["on_click"], "ignore")
        self.assertNotEqual(downloads[0].kwargs["file_name"], downloads[1].kwargs["file_name"])

        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.synthesize_french_audio.call_count, 2)
        self.assertEqual(len(self.app.get("audio")), 2)
        self.assertTrue(self.app.button(key="generate_all_audio").disabled)

    def test_selected_phrase_only_generates_on_request_and_voice_change_invalidates_audio(self):
        self.open_mode("Bonjour !\nMerci beaucoup.")
        self.preview_buttons()[1].click().run()
        self.assertFalse(self.app.exception)
        self.synthesize_french_audio.assert_called_once_with(
            "audio-test-key", "amelie", "Merci beaucoup."
        )
        self.assertEqual(len(self.app.get("audio")), 1)
        old_filename = self.download_button.call_args.kwargs["file_name"]

        self.app.selectbox(key="speechify_voice_id").select("henri").run()
        self.assertFalse(self.app.exception)
        self.assertFalse(self.app.get("audio"))
        self.assertFalse(self.app.get("download_button"))
        self.assertEqual(self.synthesize_french_audio.call_count, 1)
        self.preview_buttons()[1].click().run()
        self.assertFalse(self.app.exception)
        self.synthesize_french_audio.assert_called_with(
            "audio-test-key", "henri", "Merci beaucoup."
        )
        self.assertNotEqual(old_filename, self.download_button.call_args.kwargs["file_name"])
        self.assertEqual(
            json.loads(self.storage["assimil-preferences-v1"])["speechify_voice_id"], "henri"
        )

    def test_editing_input_preserves_matching_audio_and_removes_obsolete_downloads(self):
        self.open_mode("Bonjour !\nMerci.")
        self.app.button(key="generate_all_audio").click().run()
        self.app.text_area(key="target_words").set_value("Bonjour !\nAu revoir.").run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.get("audio")), 1)
        self.assertEqual(len(self.app.get("download_button")), 1)
        self.assertEqual(self.synthesize_french_audio.call_count, 2)
        self.app.button(key="generate_all_audio").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.get("audio")), 2)
        self.assertEqual(self.synthesize_french_audio.call_count, 3)
        self.synthesize_french_audio.assert_called_with("audio-test-key", "amelie", "Au revoir.")

    def test_download_tag_updates_readable_filename_without_regenerating_audio(self):
        self.open_mode("Non, je ne vais pas bien.")
        self.app.text_input(key="standalone_audio_tag").set_value("Pilsmeur_1-15").run()
        self.preview_buttons()[0].click().run()
        self.assertFalse(self.app.exception)
        self.assertRegex(
            self.download_button.call_args.kwargs["file_name"],
            r"^fr_non_je_ne_vais_pas_bien_pilsmeur_1-15_[a-f0-9]{12}\.mp3$",
        )
        self.app.text_input(key="standalone_audio_tag").set_value("mp3fix").run()
        self.assertFalse(self.app.exception)
        self.assertRegex(
            self.download_button.call_args.kwargs["file_name"],
            r"^fr_non_je_ne_vais_pas_bien_mp3fix_[a-f0-9]{12}\.mp3$",
        )
        self.synthesize_french_audio.assert_called_once()

    def test_failure_keeps_successful_mp3s_and_retries_only_missing_phrases(self):
        self.synthesize_french_audio.side_effect = [RuntimeError("Try again"), b"merci"]
        self.open_mode("Bonjour.\nMerci.")
        self.app.button(key="generate_all_audio").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any("phrase 1" in error.value for error in self.app.error))
        self.assertEqual(len(self.app.get("audio")), 1)
        self.synthesize_french_audio.side_effect = None
        self.synthesize_french_audio.return_value = b"bonjour"
        self.app.button(key="generate_all_audio").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.synthesize_french_audio.call_count, 3)
        self.synthesize_french_audio.assert_called_with("audio-test-key", "amelie", "Bonjour.")
        self.assertEqual(len(self.app.get("download_button")), 2)

    def test_repeated_phrase_reuses_audio_but_has_separate_downloads(self):
        self.open_mode("Merci.\nMerci.")
        self.app.button(key="generate_all_audio").click().run()
        self.assertFalse(self.app.exception)
        self.synthesize_french_audio.assert_called_once()
        self.assertEqual(len(self.app.get("audio")), 2)
        self.assertEqual(len(self.app.get("download_button")), 2)

    def test_empty_input_missing_key_and_voice_service_errors_are_actionable(self):
        self.open_mode(" \n ")
        self.assertTrue(any("one French phrase per line" in info.value for info in self.app.info))
        self.list_french_voices.assert_not_called()
        self.app.secrets["SPEECHIFY_API_KEY"] = ""
        self.app.text_area(key="target_words").set_value("Bonjour.").run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any("SPEECHIFY_API_KEY" in error.value for error in self.app.error))
        self.app.secrets["SPEECHIFY_API_KEY"] = "audio-test-key"
        self.list_french_voices.side_effect = RuntimeError("Voice service unavailable")
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any("Could not load Speechify voices" in error.value for error in self.app.error))
        self.list_french_voices.side_effect = None
        self.list_french_voices.return_value = []
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertTrue(any("No French Speechify voices" in warning.value for warning in self.app.warning))
        self.synthesize_french_audio.assert_not_called()


if __name__ == "__main__":
    unittest.main()
