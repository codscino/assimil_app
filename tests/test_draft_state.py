import json
import unittest

from draft_state import build_draft_json, restore_draft_json


class DraftStateTests(unittest.TestCase):
    def setUp(self):
        self.card = {
            "fr_word": "bonjour",
            "fr_phrase": "Bonjour, Marie !",
            "en_word": "hello",
            "en_phrase": "Hello, Marie!",
            "extra_notes": "greeting",
        }

    def build(self, **overrides):
        values = {
            "cards_data": [self.card],
            "card_regeneration_baselines": [self.card],
            "target_language_code": "en",
            "selected_lesson": "Lesson 1",
            "shared_tag": "assimil_lesson_01",
            "no_assimil_mode": False,
        }
        values.update(overrides)
        return build_draft_json(**values)

    def test_round_trip_restores_the_draft(self):
        restored = restore_draft_json(self.build(), "en", {"Lesson 1": []})

        self.assertEqual(restored["cards_data"], [self.card])
        self.assertEqual(restored["selected_lesson"], "Lesson 1")
        self.assertEqual(restored["shared_tag"], "assimil_lesson_01")

    def test_no_cards_does_not_create_a_browser_draft(self):
        self.assertIsNone(self.build(cards_data=None))

    def test_draft_for_another_language_is_ignored(self):
        self.assertIsNone(
            restore_draft_json(self.build(), "it", {"Lesson 1": []})
        )

    def test_unknown_lesson_is_ignored_but_free_practice_is_restored(self):
        self.assertIsNone(restore_draft_json(self.build(), "en", {}))

        raw = self.build(no_assimil_mode=True, selected_lesson="French Practice")
        restored = restore_draft_json(raw, "en", {})
        self.assertTrue(restored["no_assimil_mode"])
        self.assertEqual(restored["selected_lesson"], "French Practice")

    def test_free_practice_without_a_saved_tag_restores_without_a_tag(self):
        payload = json.loads(
            self.build(no_assimil_mode=True, selected_lesson="French Practice")
        )
        del payload["shared_tag"]

        restored = restore_draft_json(json.dumps(payload), "en", {})

        self.assertEqual(restored["shared_tag"], "")

    def test_legacy_free_practice_tag_is_migrated_to_no_tag(self):
        raw = self.build(
            no_assimil_mode=True,
            selected_lesson="French Practice",
            shared_tag="french_practice",
        )

        restored = restore_draft_json(raw, "en", {})

        self.assertEqual(restored["shared_tag"], "")

    def test_invalid_or_tampered_values_are_rejected(self):
        self.assertIsNone(restore_draft_json("not json", "en", {}))

        payload = json.loads(self.build())
        payload["cards"] = ["not a card"]
        self.assertIsNone(
            restore_draft_json(json.dumps(payload), "en", {"Lesson 1": []})
        )

    def test_missing_baselines_are_rebuilt_from_cards(self):
        payload = json.loads(self.build())
        payload["baselines"] = []
        restored = restore_draft_json(
            json.dumps(payload), "en", {"Lesson 1": []}
        )

        self.assertEqual(restored["card_regeneration_baselines"], [self.card])


if __name__ == "__main__":
    unittest.main()
