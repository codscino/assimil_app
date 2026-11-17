import unittest

from audio_filenames import french_audio_filename


class AudioFilenameTests(unittest.TestCase):
    def test_french_word_and_tags_are_readable_and_portable(self):
        filename = french_audio_filename(
            "Non, je ne vais pas bien", "Pilsmeur_1-15", b"audio"
        )
        self.assertRegex(
            filename, r"^fr_non_je_ne_vais_pas_bien_pilsmeur_1-15_[a-f0-9]{12}\.mp3$"
        )
        self.assertRegex(
            french_audio_filename("Cœur, café & thé", "leçon::02", b"audio"),
            r"^fr_coeur_cafe_the_lecon_02_[a-f0-9]{12}\.mp3$",
        )

    def test_names_cannot_escape_media_folder_or_exceed_portable_length(self):
        for word, tags in (("../../foo[sound:bar]", "../tag"), ("é" * 500, "tag" * 500)):
            filename = french_audio_filename(word, tags, b"audio")
            self.assertRegex(filename, r"^fr_[a-z0-9_-]+\.mp3$")
            self.assertLessEqual(len(filename.encode()), 149)
        self.assertRegex(
            french_audio_filename("?!", "", b"audio"),
            r"^fr_phrase_untagged_[a-f0-9]{12}\.mp3$",
        )

    def test_changed_recording_gets_new_filename_and_tag_order_is_stable(self):
        original = french_audio_filename("bonjour", "lesson practice", b"first voice")
        self.assertNotEqual(
            original, french_audio_filename("bonjour", "lesson practice", b"second voice")
        )
        self.assertEqual(
            original, french_audio_filename("bonjour", "practice lesson lesson", b"first voice")
        )
        self.assertTrue(french_audio_filename("bonjour", "lesson", b"wav", ".wav").endswith(".wav"))
        with self.assertRaises(ValueError):
            french_audio_filename("bonjour", "lesson", b"audio", "/../../file")


if __name__ == "__main__":
    unittest.main()
