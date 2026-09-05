import unittest

from flashcard_regeneration import (
    build_regeneration_prompt,
    changed_content_fields,
    regeneration_source,
)


class RegenerationContextTests(unittest.TestCase):
    def setUp(self):
        self.previous = {
            "fr_word": "bonjour",
            "fr_phrase": "Bonjour, Marie !",
            "en_word": "hello",
            "en_phrase": "Hello, Marie!",
            "extra_notes": "greeting",
        }

    def test_changed_fields_follow_conflict_priority(self):
        current = dict(
            self.previous,
            fr_word="salut",
            fr_phrase="Salut, Paul !",
            en_phrase="Hi, Paul!",
        )

        self.assertEqual(
            changed_content_fields(self.previous, current),
            ["fr_word", "fr_phrase", "en_phrase"],
        )
        self.assertEqual(regeneration_source(self.previous, current)[0], "fr_word")

    def test_lower_priority_edit_is_source_when_it_is_the_only_edit(self):
        current = dict(self.previous, en_word="hi")

        self.assertEqual(regeneration_source(self.previous, current)[0], "en_word")

    def test_prompt_contains_all_submitted_values_and_preserves_notes(self):
        current = dict(
            self.previous,
            fr_phrase="Bonjour tout le monde !",
            extra_notes="keep this exactly",
        )

        prompt = build_regeneration_prompt(
            "Lesson 2", "assimil_lesson_02", ["lesson context"], self.previous, current
        )

        for value in current.values():
            self.assertIn(value, prompt)
        self.assertIn("Primary source of truth: French phrase (`fr_phrase`)", prompt)
        self.assertIn("`fr_word` > `fr_phrase` > `en_word`", prompt)

    def test_free_practice_prompt_excludes_lesson_reference(self):
        prompt = build_regeneration_prompt(
            "French Practice",
            "french_practice",
            ["This lesson text must not be used"],
            self.previous,
            self.previous,
            no_assimil_mode=True,
        )

        self.assertIn("free French practice", prompt)
        self.assertNotIn("This lesson text must not be used", prompt)

    def test_prompt_uses_selected_target_language(self):
        current = dict(self.previous, en_word="ciao")

        prompt = build_regeneration_prompt(
            "Lesson 2",
            "assimil_lesson_02",
            ["lesson context"],
            self.previous,
            current,
            target_language="Italian",
        )

        self.assertIn("Primary source of truth: Italian word (`en_word`)", prompt)
        self.assertIn("exact Italian translation", prompt)


if __name__ == "__main__":
    unittest.main()
