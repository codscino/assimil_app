import unittest

from notes_input import parse_user_input


class NotesInputTests(unittest.TestCase):
    def test_marked_sentence_derives_word_and_keeps_notes(self):
        sentence = "Non, &en fait& je préfère un café."
        self.assertEqual(parse_user_input(sentence + " (contrast)"), [{
            "raw_word": "en fait", "fr_phrase": sentence, "user_notes": "contrast",
        }])

    def test_mixed_words_sentences_and_lists(self):
        self.assertEqual(parse_user_input(
            "- bonjour (greeting)\r\n"
            "2. Non, &en fait& je préfère un café.\r\n"
            "merci"
        ), [
            {"raw_word": "bonjour", "user_notes": "greeting"},
            {"raw_word": "en fait", "user_notes": "",
             "fr_phrase": "Non, &en fait& je préfère un café."},
            {"raw_word": "merci", "user_notes": ""},
        ])

    def test_multiline_parenthetical_notes(self):
        self.assertEqual(parse_user_input("Je dis &bonjour&. (formal\ngreeting)"), [{
            "raw_word": "bonjour", "fr_phrase": "Je dis &bonjour&.",
            "user_notes": "formal greeting",
        }])


if __name__ == "__main__":
    unittest.main()
