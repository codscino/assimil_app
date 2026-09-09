import unittest

from highlight_markup import (
    ensure_target_marker,
    marker_matches_target,
    phrase_contains_target,
    phrase_highlight_error,
    repair_target_marker,
    strip_highlight_markers,
)


class HighlightMarkupTests(unittest.TestCase):
    def test_explicit_marker_disambiguates_le_from_table(self):
        phrase = "&Le& chat est sur la table"

        self.assertEqual(strip_highlight_markers(phrase), "Le chat est sur la table")
        self.assertTrue(marker_matches_target(phrase, "le"))

    def test_target_does_not_match_inside_a_larger_word(self):
        self.assertFalse(phrase_contains_target("La table est prête", "le"))
        self.assertFalse(phrase_contains_target("La table est prête", "able"))
        self.assertFalse(phrase_contains_target("La table est prête", "ble"))
        self.assertEqual(ensure_target_marker("La table est prête", "le"), "La table est prête")

    def test_containment_ignores_case_and_whitespace(self):
        self.assertTrue(phrase_contains_target("Est-ce que tu &viens& ?", "  VIENS "))
        self.assertTrue(phrase_contains_target("Je dis &au revoir&.", "au   revoir"))
        self.assertFalse(phrase_contains_target("Je vais &surlatable&.", "sur la table"))

    def test_unmarked_phrase_marks_only_first_target_occurrence(self):
        self.assertEqual(
            ensure_target_marker("Le chat est sur la table", "le"),
            "&Le& chat est sur la table",
        )

    def test_existing_explicit_marker_is_not_silently_replaced(self):
        phrase = "Le &chat& est sur la table"

        self.assertEqual(ensure_target_marker(phrase, "le"), phrase)
        self.assertIn("ampersands", phrase_highlight_error(phrase, "le"))

    def test_explicit_repair_moves_wrong_marker_to_the_target(self):
        self.assertEqual(
            repair_target_marker("&Le& chat boit du café", "café"),
            "Le chat boit du &café&",
        )


if __name__ == "__main__":
    unittest.main()
