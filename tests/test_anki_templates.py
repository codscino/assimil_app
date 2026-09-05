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
    def test_forward_card_compares_against_target_language(self):
        template = template_constants()["BACK_FR2EN"]

        self.assertIn(
            '<span class="expected-answer">{{text:en_word}}</span>', template
        )
        self.assertIn('document.querySelector(".expected-answer")', template)

    def test_reverse_card_compares_against_french(self):
        template = template_constants()["BACK_EN2FR"]

        self.assertIn(
            '<span class="expected-answer">{{text:fr_word}}</span>', template
        )
        self.assertIn('document.querySelector(".expected-answer")', template)


if __name__ == "__main__":
    unittest.main()
