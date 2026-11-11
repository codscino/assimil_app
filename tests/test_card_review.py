import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


APP_PATH = Path(__file__).parents[1] / "app.py"


class CardReviewTests(unittest.TestCase):
    def setUp(self):
        # AppTest has no browser to answer the localStorage component handshake.
        self.components = patch(
            "streamlit.components.v1.declare_component",
            side_effect=lambda *args, **kwargs: (
                lambda **props: {"status": "ready", "value": None}
                if props.get("structured_result") else None
            ),
        )
        self.components.start()
        self.addCleanup(self.components.stop)
        self.voices = patch(
            "speechify_audio.list_french_voices",
            return_value=[{
                "id": "test-voice", "display_name": "Test voice",
                "locale": "fr-FR", "gender": "female",
            }],
        )
        self.voices.start()
        self.addCleanup(self.voices.stop)
        self.gemini = patch("google.genai.Client")
        self.client = self.gemini.start()
        self.addCleanup(self.gemini.stop)
        self.cards = [
            {
                "fr_word": word, "fr_phrase": f"Je dis &{word}&.",
                "en_word": translation, "en_phrase": f"I say &{translation}&.",
                "extra_notes": "",
            }
            for word, translation in (("bonjour", "hello"), ("merci", "thanks"))
        ]
        self.app = AppTest.from_file(str(APP_PATH), default_timeout=15)
        self.app.secrets["GEMINI_API_KEY"] = "test-key"
        self.app.secrets["SPEECHIFY_API_KEY"] = "test-key"
        for key, value in {
            "cards_data": [dict(card) for card in self.cards],
            "card_regeneration_baselines": [dict(card) for card in self.cards],
            "target_language": "en",
            "no_assimil_mode": True,
            "last_no_assimil_mode": True,
        }.items():
            self.app.session_state[key] = value
        self.app.run()
        self.assertFalse(self.app.exception)

    def button(self, label):
        return next(button for button in self.app.button if button.label == label)

    def test_delete_requires_confirmation_and_preserves_other_edits(self):
        notes = [field for field in self.app.text_input if field.label == "Notes"]
        notes[1].set_value("Keep this note").run()
        self.app.button(key="card_0_0_0_delete").click().run()
        self.assertEqual(len(self.app.session_state.cards_data), 2)
        self.button("Cancel").click().run()
        self.assertEqual(len(self.app.session_state.cards_data), 2)
        self.app.button(key="card_0_0_0_delete").click().run()
        self.button("Delete").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.session_state.cards_data), 1)
        remaining = self.app.session_state.cards_data[0]
        self.assertEqual(remaining["fr_word"], "merci")
        self.assertEqual(remaining["extra_notes"], "Keep this note")
        self.assertEqual(
            self.app.session_state.card_regeneration_baselines[0]["fr_word"], "merci"
        )
        self.assertEqual(
            next(field.value for field in self.app.text_input if field.label == "Notes"),
            "Keep this note",
        )

    def test_add_generate_and_return_to_regenerate(self):
        self.app.button(key="add_card").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.session_state.cards_data), 3)
        self.assertTrue(self.app.expander[-1].proto.expanded)
        self.assertTrue(self.button("📦 Approve All & Download .apkg").disabled)
        self.button("✨ Generate").click().run()
        self.client.assert_not_called()
        self.assertTrue(any("Write your French word" in item.value for item in self.app.warning))
        self.app.text_input(key="card_0_2_0_fr_word").set_value("salut").run()
        response = self.client.return_value.models.generate_content
        response.side_effect = RuntimeError("Temporary generation failure")
        self.button("✨ Generate").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(self.app.session_state.cards_data[-1]["needs_generation"])
        self.assertEqual(self.app.session_state.cards_data[-1]["fr_word"], "salut")
        response.side_effect = None
        generated = {
            "fr_word": "salut", "fr_phrase": "Je dis &salut&.",
            "en_word": "hi", "en_phrase": "I say &hi&.", "extra_notes": "",
        }
        response.return_value = SimpleNamespace(
            text=json.dumps(generated)
        )
        self.button("✨ Generate").click().run()
        self.assertFalse(self.app.exception)
        self.assertNotIn("needs_generation", self.app.session_state.cards_data[-1])
        self.assertEqual(self.app.session_state.cards_data[-1]["fr_word"], "salut")
        self.assertEqual(self.app.button(key="card_0_2_1_regenerate").label, "🔄 Regenerate")
        self.assertTrue(self.app.session_state["card_expander_0_2"])
        self.assertFalse(self.button("📦 Approve All & Download .apkg").disabled)

    def test_regenerate_preserves_expansion_and_manual_collapse(self):
        self.client.return_value.models.generate_content.return_value = SimpleNamespace(
            text=json.dumps({
                **self.cards[0], "fr_phrase": "Elle dit &bonjour&.",
                "en_phrase": "She says &hello&.",
            })
        )
        self.app.button(key="card_0_0_0_regenerate").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(self.app.session_state["card_expander_0_0"])
        self.assertEqual(
            self.app.session_state.cards_data[0]["fr_phrase"], "Elle dit &bonjour&."
        )
        # Simulate the state sent by the browser when a user closes a card.
        self.app.session_state["card_expander_0_0"] = False
        self.app.run()
        self.app.text_input(key="card_0_1_0_notes").set_value("Keep open").run()
        self.assertFalse(self.app.session_state["card_expander_0_0"])
        self.assertTrue(self.app.session_state["card_expander_0_1"])

    def test_collapse_all_includes_unfinished_cards_and_preserves_edits(self):
        self.app.text_input(key="card_0_0_0_notes").set_value("Keep this note").run()
        self.app.button(key="add_card").click().run()
        self.app.button(key="collapse_all_cards").click().run()
        self.assertFalse(self.app.exception)
        self.assertTrue(all(
            not self.app.session_state[f"card_expander_0_{idx}"]
            for idx in range(3)
        ))
        self.assertEqual(self.app.session_state.cards_data[0]["extra_notes"], "Keep this note")
        self.assertTrue(self.app.session_state.cards_data[2]["needs_generation"])
        self.app.run()
        self.assertFalse(self.app.session_state["card_expander_0_2"])
        self.app.button(key="add_card").click().run()
        self.assertTrue(self.app.session_state["card_expander_0_3"])
        self.assertFalse(self.app.session_state["card_expander_0_2"])

    def test_last_card_can_be_deleted_and_replaced(self):
        for _ in range(2):
            next(button for button in self.app.button if button.label == "🗑️ Delete card").click().run()
            self.button("Delete").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state.cards_data, [])
        self.assertTrue(self.button("📦 Approve All & Download .apkg").disabled)
        self.app.button(key="add_card").click().run()
        self.assertFalse(self.app.exception)
        self.assertEqual(len(self.app.session_state.cards_data), 1)
        self.assertEqual(self.app.session_state.cards_data[0]["fr_word"], "")
        self.assertEqual(self.button("✨ Generate").label, "✨ Generate")

    def test_italian_selection_reaches_generation_and_regeneration(self):
        self.app.selectbox(key="target_language").select("it").run()
        self.assertIsNone(self.app.session_state.cards_data)
        self.app.text_area(key="target_words").set_value("bonjour").run()
        italian_card = {
            "fr_word": "bonjour", "fr_phrase": "Je dis &bonjour&.",
            "en_word": "buongiorno", "en_phrase": "Dico &buongiorno&.",
            "extra_notes": "",
        }
        response = self.client.return_value.models.generate_content
        response.return_value = SimpleNamespace(text=json.dumps([italian_card]))
        self.app.button(key="generate_initial_flashcards").click().run()
        self.assertFalse(self.app.exception)
        initial_request = response.call_args.kwargs
        self.assertIn("exclusively in Italian", initial_request["contents"])
        self.assertIn("exclusively in Italian", initial_request["config"].system_instruction)
        self.assertEqual(self.app.session_state.cards_target_language, "it")

        response.return_value = SimpleNamespace(text=json.dumps(italian_card))
        next(button for button in self.app.button if button.label == "🔄 Regenerate").click().run()
        self.assertFalse(self.app.exception)
        regeneration_request = response.call_args.kwargs
        self.assertIn("exclusively in Italian", regeneration_request["contents"])
        self.assertIn("exclusively in Italian", regeneration_request["config"].system_instruction)
        self.assertIn("even if not edited", regeneration_request["contents"])
        self.assertEqual(self.app.session_state.cards_data[0]["en_word"], "buongiorno")


if __name__ == "__main__":
    unittest.main()
