import ast
import unittest
from pathlib import Path


def template_constants():
    app_path = Path(__file__).parents[1] / "app.py"
    tree = ast.parse(app_path.read_text(encoding="utf-8"))
    constants = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            constants[node.targets[0].id] = node.value.value
    return constants


class TypedAnswerTemplateTests(unittest.TestCase):
    def test_all_cards_use_ankis_native_ignore_diacritics_filter(self):
        templates = template_constants()

        self.assertIn("{{type:nc:en_word}}", templates["FRONT_FR2EN"])
        self.assertIn("{{type:nc:en_word}}", templates["BACK_FR2EN"])
        self.assertIn("{{type:nc:fr_word}}", templates["FRONT_EN2FR"])
        self.assertIn("{{type:nc:fr_word}}", templates["BACK_EN2FR"])

    def test_forward_card_compares_against_target_language(self):
        template = template_constants()["BACK_FR2EN"]

        self.assertIn(
            '<span class="expected-answer">{{text:en_word}}</span>', template
        )
        self.assertIn('document.querySelector(".expected-answer")', template)
        self.assertIn('answer.querySelector("#typearrow")', template)
        self.assertNotIn("sessionStorage", template)

    def test_reverse_card_compares_against_french(self):
        template = template_constants()["BACK_EN2FR"]

        self.assertIn(
            '<span class="expected-answer">{{text:fr_word}}</span>', template
        )
        self.assertIn('document.querySelector(".expected-answer")', template)
        self.assertIn('answer.querySelector("#typearrow")', template)
        self.assertNotIn("sessionStorage", template)

    def test_custom_comparison_ignores_case_spaces_and_diacritics(self):
        template = template_constants()["BACK_EN2FR"]

        self.assertIn('.replace(/\\p{M}/gu, "")', template)
        self.assertIn(".toLowerCase()", template)
        self.assertIn('.replace(/\\s+/gu, "")', template)

    def test_highlights_are_driven_only_by_explicit_phrase_markers(self):
        templates = template_constants()

        for name in ("FRONT_FR2EN", "BACK_FR2EN", "FRONT_EN2FR", "BACK_EN2FR"):
            template = templates[name]
            self.assertIn('const marker = /&([^&]*?)&/g;', template)
            self.assertIn('document.querySelectorAll(".marked-phrase")', template)
            self.assertNotIn("normalizeForMatch", template)


if __name__ == "__main__":
    unittest.main()
