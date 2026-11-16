import unittest

from flashcard_regeneration import (
    build_regeneration_prompt,
    changed_content_fields,
    normalize_whole_phrase_translation,
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


class WholePhraseTranslationTests(unittest.TestCase):
    def test_whole_phrase_discards_invented_translation_context(self):
        for translation in ("There is nothing to eat", "Non c'è niente da mangiare"):
            with self.subTest(translation=translation):
                card = {
                    "fr_word": "il n'y a rien à manger",
                    "fr_phrase": "&il n'y a rien à manger&",
                    "en_word": translation,
                    "en_phrase": f"Today, &{translation}& in the house.",
                    "extra_notes": "keep this",
                }
                normalize_whole_phrase_translation(card)
                self.assertEqual(card["en_phrase"], f"&{translation}&")
                self.assertEqual(card["extra_notes"], "keep this")

    def test_partial_target_keeps_translated_sentence(self):
        card = {
            "fr_word": "à manger",
            "fr_phrase": "il n'y a rien &à manger&",
            "en_word": "to eat",
            "en_phrase": "There is nothing &to eat&",
        }
        original = dict(card)
        normalize_whole_phrase_translation(card)
        self.assertEqual(card, original)


if __name__ == "__main__":
    unittest.main()
