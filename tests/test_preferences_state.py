import json
import unittest

from preferences_state import build_preferences_json, restore_preferences_json


class PreferencesStateTests(unittest.TestCase):
    def test_round_trip_restores_preferences(self):
        raw = build_preferences_json("it", True, "voice-123")

        restored = restore_preferences_json(raw, {"en", "it"})

        self.assertEqual(
            restored,
            {
                "target_language": "it",
                "no_assimil_mode": True,
                "speechify_voice_id": "voice-123",
            },
        )

    def test_voice_is_optional(self):
        raw = build_preferences_json("en", False)

        restored = restore_preferences_json(raw, {"en"})

        self.assertEqual(
            restored,
            {"target_language": "en", "no_assimil_mode": False},
        )

    def test_invalid_records_are_rejected(self):
        self.assertIsNone(restore_preferences_json("not json", {"en"}))
        self.assertIsNone(
            restore_preferences_json(
                json.dumps(
                    {
                        "version": 1,
                        "target_language": "unknown",
                        "no_assimil_mode": False,
                    }
                ),
                {"en"},
            )
        )
        self.assertIsNone(
            restore_preferences_json(
                json.dumps(
                    {
                        "version": 1,
                        "target_language": "en",
                        "no_assimil_mode": "false",
                    }
                ),
                {"en"},
            )
        )

    def test_invalid_voice_is_ignored(self):
        raw = json.dumps(
            {
                "version": 1,
                "target_language": "en",
                "no_assimil_mode": False,
                "speechify_voice_id": "x" * 501,
            }
        )

        restored = restore_preferences_json(raw, {"en"})

        self.assertNotIn("speechify_voice_id", restored)

if __name__ == "__main__":
    unittest.main()
