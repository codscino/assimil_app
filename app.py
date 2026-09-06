import streamlit as st
import genanki
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import json
import re
import io
import html
import hashlib
import base64
import tempfile
import unicodedata
from pathlib import Path

import streamlit.components.v1 as components

from draft_state import build_draft_json, restore_draft_json
from flashcard_regeneration import build_regeneration_prompt
from preferences_state import build_preferences_json, restore_preferences_json
from speechify_audio import list_french_voices, synthesize_french_audio

# -----------------------------------------------------------------------------
# 1. ANKI MODEL DEFINITION (Raw Strings)
# -----------------------------------------------------------------------------
# Changed because the model now has an embedded French audio field.
MODEL_ID = 1607392320
FR2EN_DECK_ID = 2059500001
EN2FR_DECK_ID = 2059500002

TARGET_LANGUAGES = {
    "en": {"name": "English", "flag": "🇬🇧", "deck_code": "EN"},
    "it": {"name": "Italian", "flag": "🇮🇹", "deck_code": "IT"},
    "es": {"name": "Spanish", "flag": "🇪🇸", "deck_code": "ES"},
    "pt": {"name": "Portuguese", "flag": "🇵🇹", "deck_code": "PT"},
    "ko": {"name": "Korean", "flag": "🇰🇷", "deck_code": "KO"},
    "ru": {"name": "Russian", "flag": "🇷🇺", "deck_code": "RU"},
    "nl": {"name": "Dutch", "flag": "🇳🇱", "deck_code": "NL"},
}
PREFERENCES_STORAGE_KEY = "assimil-preferences-v1"
TARGET_WORDS_STORAGE_KEY = "assimil-target-words-v1"
MAX_TARGET_WORDS_LENGTH = 100_000

_draft_storage = components.declare_component(
    "assimil_draft_storage",
    path=str(Path(__file__).parent / "components" / "draft_storage"),
)

FRONT_FR2EN = r"""
{{#fr_phrase}}
<div class="phrase">{{fr_phrase}}</div>
<span class="target-word">{{text:fr_word}}</span>
{{fr_audio}}
{{/fr_phrase}}

{{^fr_phrase}}
<div>{{fr_word}}</div>
{{fr_audio}}
{{/fr_phrase}}

<br><br>
{{type:nc:en_word}}

<script>
const phrase = document.querySelector(".phrase");
const word = document.querySelector(".target-word")?.textContent.trim();

if (phrase && word && !phrase.querySelector(".highlight")) {
  const variants = {
    a: "[aàáâäãå]", c: "[cç]", e: "[eèéêë]", i: "[iìíîï]",
    n: "[nñ]", o: "[oòóôöõø]", u: "[uùúûü]", y: "[yÿ]"
  };
  const foldedWord = word.normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/œ/g, "oe").replace(/æ/g, "ae");
  const pattern = Array.from(foldedWord).map((character) => {
    if (/\s/.test(character)) return "\\s+";
    if (variants[character]) return variants[character];
    if (/[’‘‛'`]/.test(character)) return "[’‘‛'`]";
    if (/[‐‑‒–—-]/.test(character)) return "[‐‑‒–—-]";
    return character.replace(/[-/\\^$*+?.()|[\]{}]/g, "\\$&");
  }).join("");
  phrase.innerHTML = phrase.textContent.replace(
    new RegExp(pattern, "gi"), "<span class='highlight'>$&</span>"
  );
}
</script>
"""

BACK_FR2EN = r"""
{{#fr_phrase}}
<div class="phrase">{{fr_phrase}}</div>
<span class="target-word">{{text:fr_word}}</span>
{{/fr_phrase}}

{{^fr_phrase}}
<div>{{fr_word}}</div>
{{/fr_phrase}}

<hr id="answer">
{{type:nc:en_word}}
<span class="expected-answer">{{text:en_word}}</span>

<script>
// Make the typed-answer display ignore casing, whitespace, and diacritics.
(() => {
  const answer = document.getElementById("typeans");
  const expected = document.querySelector(".expected-answer")?.textContent ?? "";
  if (!answer || !expected) return;

  // Anki puts the typed response before the first <br> in its comparison.
  // Reading it here is reliable across desktop and mobile webviews and avoids
  // depending on storage surviving the front-to-back card render.
  let entered = "";
  const hasDiff = Boolean(answer.querySelector("#typearrow"));
  if (hasDiff) {
    for (const node of answer.childNodes) {
      if (node.nodeName === "BR") break;
      entered += node.textContent ?? "";
    }
  } else if (answer.querySelector(".typeGood")) {
    entered = answer.textContent ?? "";
  }

  const normalize = (value) => String(value).normalize("NFKD")
    .replace(/\p{M}/gu, "").toLowerCase().replace(/\s+/gu, "");
  const correct = normalize(entered) === normalize(expected);
  answer.replaceChildren();
  const response = document.createElement("code");
  response.className = correct ? "typeGood" : "typeBad";
  response.textContent = entered || "(no answer)";
  answer.append(response);
  if (!correct) {
    const solution = document.createElement("div");
    solution.className = "typeMissed";
    solution.textContent = expected;
    answer.append(solution);
  }
})();
</script>

<div class="translation">
  {{#en_phrase}}{{en_phrase}}{{/en_phrase}}
  {{^en_phrase}}{{en_word}}{{/en_phrase}}
</div>

{{#en_phrase}}
{{/en_phrase}}
{{^en_phrase}}
{{/en_phrase}}

{{#extra_notes}}
<div class="notes">Note: {{extra_notes}}</div>
{{/extra_notes}}

<script>
const phrase = document.querySelector(".phrase");
const word = document.querySelector(".target-word")?.textContent.trim();

if (phrase && word && !phrase.querySelector(".highlight")) {
  const normalizeForMatch = (input) => {
    let normalized = "";
    const positions = [];
    for (let start = 0; start < input.length;) {
      const character = String.fromCodePoint(input.codePointAt(start));
      const end = start + character.length;
      if (/\s/u.test(character)) {
        if (normalized && !normalized.endsWith(" ")) {
          normalized += " "; positions.push({ start, end });
        } else if (normalized.endsWith(" ")) positions[positions.length - 1].end = end;
      } else {
        const folded = character.normalize("NFKD").replace(/\p{M}/gu, "")
          .toLowerCase().replace(/[’‘‛`]/g, "'").replace(/[‐‑‒–—]/g, "-")
          .replace(/œ/g, "oe").replace(/æ/g, "ae");
        for (const foldedCharacter of folded) {
          normalized += foldedCharacter; positions.push({ start, end });
        }
      }
      start = end;
    }
    if (normalized.endsWith(" ")) { normalized = normalized.slice(0, -1); positions.pop(); }
    return { normalized, positions };
  };
  const original = phrase.textContent;
  const phraseMatch = normalizeForMatch(original);
  const targetMatch = normalizeForMatch(word);
  const matchIndex = phraseMatch.normalized.indexOf(targetMatch.normalized);
  if (targetMatch.normalized && matchIndex !== -1) {
    const start = phraseMatch.positions[matchIndex].start;
    const end = phraseMatch.positions[matchIndex + targetMatch.normalized.length - 1].end;
    const highlight = document.createElement("span");
    highlight.className = "highlight";
    highlight.textContent = original.slice(start, end);
    phrase.replaceChildren(document.createTextNode(original.slice(0, start)), highlight,
      document.createTextNode(original.slice(end)));
  }
}
</script>
"""

FRONT_EN2FR = r"""
{{#en_phrase}}
<div class="phrase">{{en_phrase}}</div>
<span class="target-word">{{text:en_word}}</span>
{{/en_phrase}}

{{^en_phrase}}
<div>{{en_word}}</div>
{{/en_phrase}}

{{#extra_notes}}
<div class="notes">Note: {{extra_notes}}</div>
{{/extra_notes}}

<br><br>
{{type:nc:fr_word}}

<script>
const phrase = document.querySelector(".phrase");
const word = document.querySelector(".target-word")?.textContent.trim();

if (phrase && word && !phrase.querySelector(".highlight")) {
  const normalizeForMatch = (input) => {
    let normalized = "";
    const positions = [];
    for (let start = 0; start < input.length;) {
      const character = String.fromCodePoint(input.codePointAt(start));
      const end = start + character.length;
      if (/\s/u.test(character)) {
        if (normalized && !normalized.endsWith(" ")) {
          normalized += " "; positions.push({ start, end });
        } else if (normalized.endsWith(" ")) positions[positions.length - 1].end = end;
      } else {
        const folded = character.normalize("NFKD").replace(/\p{M}/gu, "")
          .toLowerCase().replace(/[’‘‛`]/g, "'").replace(/[‐‑‒–—]/g, "-")
          .replace(/œ/g, "oe").replace(/æ/g, "ae");
        for (const foldedCharacter of folded) {
          normalized += foldedCharacter; positions.push({ start, end });
        }
      }
      start = end;
    }
    if (normalized.endsWith(" ")) { normalized = normalized.slice(0, -1); positions.pop(); }
    return { normalized, positions };
  };
  const original = phrase.textContent;
  const phraseMatch = normalizeForMatch(original);
  const targetMatch = normalizeForMatch(word);
  const matchIndex = phraseMatch.normalized.indexOf(targetMatch.normalized);
  if (targetMatch.normalized && matchIndex !== -1) {
    const start = phraseMatch.positions[matchIndex].start;
    const end = phraseMatch.positions[matchIndex + targetMatch.normalized.length - 1].end;
    const highlight = document.createElement("span");
    highlight.className = "highlight";
    highlight.textContent = original.slice(start, end);
    phrase.replaceChildren(document.createTextNode(original.slice(0, start)), highlight,
      document.createTextNode(original.slice(end)));
  }
}
</script>
"""

BACK_EN2FR = r"""
{{#en_phrase}}
<div class="phrase">{{en_phrase}}</div>
<span class="target-word">{{text:en_word}}</span>
{{/en_phrase}}

{{^en_phrase}}
<div>{{en_word}}</div>
{{/en_phrase}}

{{#extra_notes}}
<div class="notes">Note: {{extra_notes}}</div>
{{/extra_notes}}

<hr id="answer">
{{type:nc:fr_word}}
<span class="expected-answer">{{text:fr_word}}</span>

<script>
// Make the typed-answer display ignore casing, whitespace, and diacritics.
(() => {
  const answer = document.getElementById("typeans");
  const expected = document.querySelector(".expected-answer")?.textContent ?? "";
  if (!answer || !expected) return;

  // Anki puts the typed response before the first <br> in its comparison.
  // Reading it here is reliable across desktop and mobile webviews and avoids
  // depending on storage surviving the front-to-back card render.
  let entered = "";
  const hasDiff = Boolean(answer.querySelector("#typearrow"));
  if (hasDiff) {
    for (const node of answer.childNodes) {
      if (node.nodeName === "BR") break;
      entered += node.textContent ?? "";
    }
  } else if (answer.querySelector(".typeGood")) {
    entered = answer.textContent ?? "";
  }

  const normalize = (value) => String(value).normalize("NFKD")
    .replace(/\p{M}/gu, "").toLowerCase().replace(/\s+/gu, "");
  const correct = normalize(entered) === normalize(expected);
  answer.replaceChildren();
  const response = document.createElement("code");
  response.className = correct ? "typeGood" : "typeBad";
  response.textContent = entered || "(no answer)";
  answer.append(response);
  if (!correct) {
    const solution = document.createElement("div");
    solution.className = "typeMissed";
    solution.textContent = expected;
    answer.append(solution);
  }
})();
</script>

<div class="translation">
  {{#fr_phrase}}{{fr_phrase}}{{/fr_phrase}}
  {{^fr_phrase}}{{fr_word}}{{/fr_phrase}}
</div>

{{#fr_phrase}}
{{fr_audio}}
{{/fr_phrase}}
{{^fr_phrase}}
{{fr_audio}}
{{/fr_phrase}}

<script>
const phrase = document.querySelector(".phrase");
const word = document.querySelector(".target-word")?.textContent.trim();

if (phrase && word && !phrase.querySelector(".highlight")) {
  const normalizeForMatch = (input) => {
    let normalized = "";
    const positions = [];
    for (let start = 0; start < input.length;) {
      const character = String.fromCodePoint(input.codePointAt(start));
      const end = start + character.length;
      if (/\s/u.test(character)) {
        if (normalized && !normalized.endsWith(" ")) {
          normalized += " "; positions.push({ start, end });
        } else if (normalized.endsWith(" ")) positions[positions.length - 1].end = end;
      } else {
        const folded = character.normalize("NFKD").replace(/\p{M}/gu, "")
          .toLowerCase().replace(/[’‘‛`]/g, "'").replace(/[‐‑‒–—]/g, "-")
          .replace(/œ/g, "oe").replace(/æ/g, "ae");
        for (const foldedCharacter of folded) {
          normalized += foldedCharacter; positions.push({ start, end });
        }
      }
      start = end;
    }
    if (normalized.endsWith(" ")) { normalized = normalized.slice(0, -1); positions.pop(); }
    return { normalized, positions };
  };
  const original = phrase.textContent;
  const phraseMatch = normalizeForMatch(original);
  const targetMatch = normalizeForMatch(word);
  const matchIndex = phraseMatch.normalized.indexOf(targetMatch.normalized);
  if (targetMatch.normalized && matchIndex !== -1) {
    const start = phraseMatch.positions[matchIndex].start;
    const end = phraseMatch.positions[matchIndex + targetMatch.normalized.length - 1].end;
    const highlight = document.createElement("span");
    highlight.className = "highlight";
    highlight.textContent = original.slice(start, end);
    phrase.replaceChildren(document.createTextNode(original.slice(0, start)), highlight,
      document.createTextNode(original.slice(end)));
  }
}
</script>
"""

CARD_STYLE = r"""
.card {
  font-family: Arial, sans-serif;
  font-size: 20px;
  text-align: center;
  color: black;
  background: white;
}
.phrase {
  margin-bottom: 4px;
}
.target-word, .expected-answer {
  display: none;
}
.translation {
  margin-top: 8px;
  font-size: 0.9em;
  color: grey;
}
.highlight {
  background: yellow;
  color: black;
  font-weight: bold;
}
.notes {
  margin-top: 10px;
  font-size: 0.8em;
  color: grey;
}
"""

def language_anki_ids(language_code):
    """Return stable, distinct Anki IDs for a target language."""
    if language_code == "en":
        return MODEL_ID, FR2EN_DECK_ID, EN2FR_DECK_ID

    def stable_id(value):
        digest = hashlib.sha256(value.encode("utf-8")).digest()
        return 1_000_000_000 + int.from_bytes(digest[:4], "big") % 1_000_000_000

    return (
        stable_id(f"assimil-model-{language_code}"),
        stable_id(f"assimil-fr-to-{language_code}"),
        stable_id(f"assimil-{language_code}-to-fr"),
    )


def build_anki_model(language_code):
    """Build a model whose target fields and templates match the language."""
    language = TARGET_LANGUAGES[language_code]
    deck_code = language["deck_code"]
    model_id, fr_to_target_id, target_to_fr_id = language_anki_ids(language_code)
    target_word_field = f"{language_code}_word"
    target_phrase_field = f"{language_code}_phrase"

    def target_fields(template):
        return template.replace("en_word", target_word_field).replace(
            "en_phrase", target_phrase_field
        )

    return genanki.Model(
        model_id,
        f"Assimil French Model {deck_code}",
        fields=[
            {"name": "fr_word"},
            {"name": "fr_phrase"},
            {"name": target_word_field},
            {"name": target_phrase_field},
            {"name": "extra_notes"},
            {"name": "fr_audio"},
        ],
        templates=[
            {
                "name": f"FR -> {deck_code}",
                "qfmt": target_fields(FRONT_FR2EN),
                "afmt": target_fields(BACK_FR2EN),
                "did": fr_to_target_id,
            },
            {
                "name": f"{deck_code} -> FR",
                "qfmt": target_fields(FRONT_EN2FR),
                "afmt": target_fields(BACK_EN2FR),
                "did": target_to_fr_id,
            },
        ],
        css=CARD_STYLE,
    )

# -----------------------------------------------------------------------------
# 2. HELPER FUNCTIONS & SCHEMAS
# -----------------------------------------------------------------------------
class FlashcardItem(BaseModel):
    fr_word: str = Field(description="Cleaned target French word or expression")
    fr_phrase: str = Field(description="Natural French sentence featuring the target word matching Assimil style")
    # These legacy JSON keys keep existing saved cards compatible. Their values
    # contain whichever target language the user selected.
    en_word: str = Field(description="Direct target-language translation of fr_word")
    en_phrase: str = Field(description="Target-language translation of fr_phrase")
    extra_notes: str = Field(description="User notes combined with brief grammar tips if useful")

def load_lessons():
    """Read the current lesson file on every rerun.

    The JSON is small, and caching this no-argument function can otherwise keep
    serving an earlier version after lessons.json is updated on a deployment.
    """
    try:
        with open("lessons.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        # Free-practice mode deliberately does not need an Assimil lesson file.
        return {}

def get_lesson_tag(lesson_name):
    lesson_num = re.sub(r'\D', '', lesson_name) or "01"
    lesson_num_padded = lesson_num.zfill(2)
    return f"assimil_lesson_{lesson_num_padded}"


def get_lesson_number(lesson_name):
    """Return the numeric part of a lesson key, e.g. ``Lesson 12`` -> 12."""
    match = re.search(r'\d+', lesson_name)
    return int(match.group()) if match else None


def normalize_with_positions(text):
    """Fold text for matching while retaining offsets into the original text."""
    normalized = []
    positions = []
    punctuation = str.maketrans({
        "’": "'", "‘": "'", "‛": "'", "`": "'",
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    })

    for index, character in enumerate(text):
        if character.isspace():
            if normalized and normalized[-1] != " ":
                normalized.append(" ")
                positions.append([index, index + 1])
            elif normalized and normalized[-1] == " ":
                positions[-1][1] = index + 1
            continue

        replacement = {"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE"}.get(
            character, character
        )
        folded = unicodedata.normalize("NFKD", replacement).translate(punctuation)
        base_characters = [
            folded_character.lower()
            for folded_character in folded
            if not unicodedata.combining(folded_character)
        ]
        if not base_characters and positions:
            # Include a decomposed accent in the preceding highlighted character.
            positions[-1][1] = index + 1
        for folded_character in base_characters:
            normalized.append(folded_character)
            positions.append([index, index + 1])

    if normalized and normalized[-1] == " ":
        normalized.pop()
        positions.pop()
    return "".join(normalized), positions


def highlight_target(phrase, target):
    """Return safe HTML with accent/case/spacing-insensitive target highlights."""
    if not phrase or not target:
        return html.escape(phrase or "")

    normalized_phrase, positions = normalize_with_positions(phrase)
    normalized_target, _ = normalize_with_positions(target)
    if not normalized_target:
        return html.escape(phrase)

    parts = []
    original_cursor = 0
    search_cursor = 0
    while True:
        match_index = normalized_phrase.find(normalized_target, search_cursor)
        if match_index == -1:
            break
        match_start = positions[match_index][0]
        match_end = positions[match_index + len(normalized_target) - 1][1]
        if match_start >= original_cursor:
            parts.append(html.escape(phrase[original_cursor:match_start]))
            parts.append(
                f'<span class="highlight">{html.escape(phrase[match_start:match_end])}</span>'
            )
            original_cursor = match_end
        search_cursor = match_index + len(normalized_target)

    if not parts:
        return html.escape(phrase)
    parts.append(html.escape(phrase[original_cursor:]))
    return "".join(parts)


def flashcard_summary(phrase, target):
    """Create a compact, safely escaped expander label for a flashcard.

    Streamlit expanders support its colour-background Markdown directive, so the
    target can remain visible even while the card editor is collapsed.
    """
    def escape_label_markdown(value):
        # Escape the Markdown constructs that could otherwise change the label.
        return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|])", r"\\\1", value)

    if not phrase or not target:
        return escape_label_markdown(phrase or "New Card")

    normalized_phrase, positions = normalize_with_positions(phrase)
    normalized_target, _ = normalize_with_positions(target)
    if not normalized_target:
        return escape_label_markdown(phrase)

    parts = []
    original_cursor = 0
    search_cursor = 0
    while True:
        match_index = normalized_phrase.find(normalized_target, search_cursor)
        if match_index == -1:
            break
        match_start = positions[match_index][0]
        match_end = positions[match_index + len(normalized_target) - 1][1]
        parts.append(escape_label_markdown(phrase[original_cursor:match_start]))
        highlighted_text = escape_label_markdown(phrase[match_start:match_end])
        parts.append(f":orange-background[{highlighted_text}]")
        original_cursor = match_end
        search_cursor = match_index + len(normalized_target)

    if not parts:
        return escape_label_markdown(phrase)
    parts.append(escape_label_markdown(phrase[original_cursor:]))
    return "".join(parts)


def parse_input_line(line):
    match = re.match(r'^(.*?)\s*\((.*)\)\s*$', line)
    if match:
        return match.group(1).strip(), match.group(2).strip()

    word, separator, notes = line.partition("(")
    return word.strip(), notes.rstrip(") ").strip() if separator else ""


def parse_user_input(raw_text):
    items = []
    pending_line = ""
    lines = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    for line in lines:
        has_list_marker = bool(re.match(r'^\s*(?:[•●▪◦☐☑-]|\d+[.)])\s+', line))
        line = re.sub(r'^\s*(?:[•●▪◦☐☑-]|\d+[.)])\s+', "", line).strip()
        if not line:
            continue

        if pending_line and (has_list_marker or "(" in line):
            word, notes = parse_input_line(pending_line)
            if word:
                items.append({"raw_word": word, "user_notes": notes})
            pending_line = ""

        pending_line = f"{pending_line} {line}".strip()
        if pending_line.count("(") > pending_line.count(")"):
            continue

        word, notes = parse_input_line(pending_line)
        if word:
            items.append({"raw_word": word, "user_notes": notes})
        pending_line = ""

    if pending_line:
        word, notes = parse_input_line(pending_line)
        if word:
            items.append({"raw_word": word, "user_notes": notes})

    return items

def generate_flashcards_with_gemini(
    api_key,
    model_name,
    lesson_name,
    lesson_data,
    parsed_items,
    no_assimil_mode=False,
    target_language="English",
):
    client = genai.Client(api_key=api_key)
    lesson_tag_main = "" if no_assimil_mode else get_lesson_tag(lesson_name)

    if no_assimil_mode:
        context = """
    Practice context:
    - This is free French practice, with no Assimil lesson reference.
    - Invent a pleasant, useful sentence suitable for a French learner around each
      target word or expression. Keep it natural, clear, and memorable.
        """
    else:
        context = f"""
    Lesson context:
    - Lesson name: {lesson_name}
    - Shared tag: `{lesson_tag_main}`

    Reference sentences from this lesson:
    {json.dumps(lesson_data, ensure_ascii=False, indent=2)}
        """

    prompt = f"""
    You are an expert French tutor creating Anki flashcards for the Assimil method.

    {context}

    User target words/phrases:
    {json.dumps(parsed_items, ensure_ascii=False, indent=2)}

    Instructions for each card:
    1. Preserve the French target as written for `fr_word`; only correct clear spelling mistakes.
    2. Write a natural French sentence for `fr_phrase` that matches the conversational Assimil style.
       - The cleaned `fr_word` must appear inside `fr_phrase` (case-insensitive).
    3. Write the {target_language} translation in the legacy JSON fields
       `en_word` and `en_phrase`.
       - The cleaned `en_word` must appear inside `en_phrase` (case-insensitive).
    4. Keep `extra_notes` exactly as provided by the user when present, or leave empty.
    5. Keep the output JSON valid and matching the schema.
    """

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=list[FlashcardItem]
        )
    )
    
    parsed_cards = json.loads(response.text)
    
    for idx, item in enumerate(parsed_cards):
        if idx < len(parsed_items):
            item["raw_word"] = parsed_items[idx]["raw_word"]
            item["user_notes"] = parsed_items[idx]["user_notes"]
            
    return parsed_cards

def regenerate_single_card(
    api_key,
    model_name,
    lesson_name,
    lesson_data,
    previous_card,
    current_card,
    no_assimil_mode=False,
    target_language="English",
):
    client = genai.Client(api_key=api_key)
    lesson_tag_main = "" if no_assimil_mode else get_lesson_tag(lesson_name)

    prompt = build_regeneration_prompt(
        lesson_name,
        lesson_tag_main,
        lesson_data,
        previous_card,
        current_card,
        no_assimil_mode=no_assimil_mode,
        target_language=target_language,
    )

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=FlashcardItem
        )
    )
    
    new_card = json.loads(response.text)
    new_card["extra_notes"] = current_card.get("extra_notes", "")
    new_card["raw_word"] = current_card.get("fr_word", "")
    new_card["user_notes"] = current_card.get("extra_notes", "")
    return new_card

class DirectionalDeck(genanki.Deck):
    """Put the reverse card of every note in a fixed second deck.

    genanki initially writes every card from a note to the deck that owns the
    note. Anki's template deck override is also stored in the model, but the
    exported card records need their deck IDs set explicitly as well.
    """

    def __init__(self, deck_id, name, reverse_deck_id):
        super().__init__(deck_id, name)
        self.reverse_deck_id = reverse_deck_id

    def write_to_db(self, cursor, timestamp, id_gen):
        super().write_to_db(cursor, timestamp, id_gen)
        cursor.execute(
            "UPDATE cards SET did = ? WHERE did = ? AND ord = 1",
            (self.reverse_deck_id, self.deck_id),
        )


@st.cache_data(ttl=3600, show_spinner=False)
def cached_french_voices(_speechify_api_key):
    """Return compatible Speechify voices, cached for one hour."""
    return list_french_voices(_speechify_api_key)


def build_anki_apkg(
    cards_data,
    lesson_name,
    speechify_api_key,
    speechify_voice_id,
    shared_tag=None,
    target_language_code="en",
):
    # An empty string intentionally means "no tag" (used by free practice).
    # Only an omitted value should fall back to the selected lesson tag.
    tag_name = (
        get_lesson_tag(lesson_name)
        if shared_tag is None
        else shared_tag.strip()
    )
    
    language = TARGET_LANGUAGES[target_language_code]
    deck_code = language["deck_code"]
    _, fr_to_target_id, target_to_fr_id = language_anki_ids(target_language_code)
    anki_model = build_anki_model(target_language_code)

    # The forward deck owns each note; DirectionalDeck moves template ordinal 1
    # to the fixed reverse deck for this target language.
    deck = DirectionalDeck(
        fr_to_target_id, f"FR2{deck_code}", target_to_fr_id
    )
    reverse_deck = genanki.Deck(target_to_fr_id, f"{deck_code}2FR")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        media_files = []
        generated_audio = {}

        for item in cards_data:
            french_word = item.get("fr_word", "")
            french_phrase = item.get("fr_phrase", "")
            target_word = item.get("en_word", "")
            target_phrase = item.get("en_phrase", "")
            french_text = (french_phrase or french_word).strip()
            audio_field = ""
            if french_text:
                # Reuse audio when the exact same phrase occurs on multiple cards.
                filename = (
                    f"fr_{hashlib.sha256(french_text.encode('utf-8')).hexdigest()[:16]}.mp3"
                )
                audio_path = Path(temp_dir) / filename
                if filename not in generated_audio:
                    audio_path.write_bytes(
                        synthesize_french_audio(
                            speechify_api_key, speechify_voice_id, french_text
                        )
                    )
                    generated_audio[filename] = audio_path
                    media_files.append(str(audio_path))
                audio_field = f"[sound:{filename}]"

            note = genanki.Note(
                model=anki_model,
                fields=[
                    french_word,
                    highlight_target(french_phrase, french_word),
                    target_word,
                    highlight_target(target_phrase, target_word),
                    item.get("extra_notes", ""),
                    audio_field,
                ],
                tags=[tag_name] if tag_name else [],
            )
            deck.add_note(note)

        buffer = io.BytesIO()
        package = genanki.Package([deck, reverse_deck])
        package.media_files = media_files
        package.write_to_file(buffer)
        buffer.seek(0)
        return buffer

# -----------------------------------------------------------------------------
# 3. STREAMLIT APP UI & SESSION STATE
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Assimil Anki Generator", page_icon="🇫🇷", layout="wide")

preferences_result = _draft_storage(
    action="read",
    draft=None,
    storage_key=PREFERENCES_STORAGE_KEY,
    structured_result=True,
    key="assimil_preferences_reader",
    default={"status": "loading", "value": None},
)
if preferences_result.get("status") == "loading":
    st.caption("Loading saved preferences…")
    st.stop()
if preferences_result.get("status") == "error":
    st.warning(
        "Browser storage is unavailable, so preferences will only last for this session."
    )

saved_preferences = restore_preferences_json(
    preferences_result.get("value"), TARGET_LANGUAGES
) or {}
saved_target_language = saved_preferences.get("target_language")
saved_language_is_valid = saved_target_language in TARGET_LANGUAGES
saved_no_assimil_mode = saved_preferences.get("no_assimil_mode")
saved_speechify_voice_id = saved_preferences.get("speechify_voice_id")


def mark_preferences_changed():
    """Write preferences only after a real widget interaction."""
    st.session_state.preferences_dirty = True

if "target_language" not in st.session_state:
    st.session_state.target_language = (
        saved_target_language if saved_language_is_valid else "en"
    )

header_col1, header_col_language, header_col_model = st.columns([3, 1.15, 1.15])

with header_col1:
    st.title("🇫🇷 Assimil French Anki Generator")

with header_col_language:
    target_language_code = st.selectbox(
        "Translate to",
        options=list(TARGET_LANGUAGES),
        key="target_language",
        on_change=mark_preferences_changed,
        format_func=lambda code: (
            f"{TARGET_LANGUAGES[code]['flag']} {TARGET_LANGUAGES[code]['name']}"
        ),
    )

with header_col_model:
    model_choice = st.selectbox(
        "Model",
        ["gemini-3.5-flash-lite", "gemini-3.5-flash"],
        index=0
    )

target_language = TARGET_LANGUAGES[target_language_code]
target_language_name = target_language["name"]

# Existing translations cannot safely be relabelled as another language.
previous_language = st.session_state.get("cards_target_language", target_language_code)
if previous_language != target_language_code and st.session_state.get("cards_data"):
    st.session_state.cards_data = None
    st.session_state.card_regeneration_baselines = []
    st.session_state.card_form_versions = {}
    st.session_state.clear_browser_draft = True
    st.toast("Language changed. Generate a new translated deck.", icon="🌐")
st.session_state.cards_target_language = target_language_code

api_key = st.secrets.get("GEMINI_API_KEY", "")
if not api_key:
    api_key = st.text_input("Enter Gemini API Key", type="password")

lessons = load_lessons()
lesson_numbers = {
    number: lesson_name
    for lesson_name in lessons
    if (number := get_lesson_number(lesson_name)) is not None
}
lesson_numbers = dict(sorted(lesson_numbers.items()))

if "cards_data" not in st.session_state:
    st.session_state.cards_data = None
if "no_assimil_mode" not in st.session_state:
    st.session_state.no_assimil_mode = (
        saved_no_assimil_mode
        if isinstance(saved_no_assimil_mode, bool)
        else not bool(lesson_numbers)
    )
if lesson_numbers and (
    "selected_lesson" not in st.session_state
    or st.session_state.selected_lesson not in lessons
):
    st.session_state.selected_lesson = next(iter(lesson_numbers.values()))
if lesson_numbers and "lesson_number_picker" not in st.session_state:
    st.session_state.lesson_number_picker = get_lesson_number(st.session_state.selected_lesson)
if "shared_tag" not in st.session_state:
    st.session_state.shared_tag = (
        get_lesson_tag(st.session_state.selected_lesson)
        if lesson_numbers
        else ""
    )
if "last_no_assimil_mode" not in st.session_state:
    st.session_state.last_no_assimil_mode = st.session_state.no_assimil_mode
if "card_form_epoch" not in st.session_state:
    st.session_state.card_form_epoch = 0
if "card_form_versions" not in st.session_state:
    st.session_state.card_form_versions = {}
if "card_regeneration_baselines" not in st.session_state:
    st.session_state.card_regeneration_baselines = []

# The browser keeps a validated copy of the current draft. If Safari returns
# after Streamlit has discarded its WebSocket session, reloading the page
# restores the cards into the new session.
clear_browser_draft = st.session_state.pop("clear_browser_draft", False)
clear_target_words = st.session_state.pop("clear_target_words", False)
if st.session_state.pop("cleanup_after_successful_export", False):
    st.session_state.cards_data = None
    st.session_state.card_regeneration_baselines = []
    st.session_state.card_form_versions = {}
    st.session_state.target_words = ""
    st.session_state.target_words_storage_applied = False
    clear_browser_draft = True
    clear_target_words = True
stored_draft = _draft_storage(
    action="clear" if clear_browser_draft else "read",
    draft=None,
    storage_key="assimil-flashcard-draft-v1",
    key="assimil_draft_reader",
    default=None,
)
if not clear_browser_draft and not st.session_state.cards_data:
    restored_draft = restore_draft_json(
        stored_draft,
        target_language_code,
        lessons,
    )
    if restored_draft:
        st.session_state.update(restored_draft)
        st.session_state.last_no_assimil_mode = restored_draft["no_assimil_mode"]
        st.session_state.shared_tag_editor = restored_draft["shared_tag"]
        if not restored_draft["no_assimil_mode"]:
            st.session_state.lesson_number_picker = get_lesson_number(
                restored_draft["selected_lesson"]
            )
        st.session_state.card_form_epoch += 1
        st.session_state.card_form_versions = {}
        st.toast("Recovered your saved card draft.", icon="↩️")

stored_target_words = _draft_storage(
    action="clear" if clear_target_words else "read",
    draft=None,
    storage_key=TARGET_WORDS_STORAGE_KEY,
    key="assimil_target_words_reader",
    default=None,
)
if "target_words" not in st.session_state:
    st.session_state.target_words = ""
if (
    not clear_target_words
    and not st.session_state.get("target_words_storage_applied")
    and isinstance(stored_target_words, str)
    and len(stored_target_words) <= MAX_TARGET_WORDS_LENGTH
):
    st.session_state.target_words = stored_target_words
    st.session_state.target_words_storage_applied = True


def save_card_field(card_index, field_name, widget_key):
    """Persist an editor value as soon as Streamlit reports that it changed."""
    if st.session_state.cards_data and widget_key in st.session_state:
        st.session_state.cards_data[card_index][field_name] = st.session_state[widget_key]


def card_from_widgets(card_index, widget_keys):
    """Synchronize and return all currently available values for one card."""
    card = st.session_state.cards_data[card_index]
    for field_name, widget_key in widget_keys.items():
        if widget_key in st.session_state:
            card[field_name] = st.session_state[widget_key]
    return dict(card)


def save_all_card_widgets(all_widget_keys):
    """Final synchronization used immediately before exporting the deck."""
    for card_index, widget_keys in all_widget_keys.items():
        card_from_widgets(card_index, widget_keys)


def clear_voice_preview():
    st.session_state.pop("speechify_preview_audio", None)


def mark_voice_preferences_changed():
    clear_voice_preview()
    mark_preferences_changed()


def mark_target_words_changed():
    """Allow writes after the user, rather than initial rendering, changed input."""
    st.session_state.target_words_storage_applied = True

# --- STEP 1: INPUT FORM ---
st.subheader("1. Input Words & Select Lesson")
c1, c2 = st.columns([1, 2])

with c1:
    no_assimil_mode = st.toggle(
        "No Assimil",
        key="no_assimil_mode",
        on_change=mark_preferences_changed,
        help="Create free-practice phrases not linked to Assimil book lessons.",
    )
    if no_assimil_mode != st.session_state.last_no_assimil_mode:
        new_tag = (
            ""
            if no_assimil_mode
            else get_lesson_tag(st.session_state.selected_lesson)
        )
        st.session_state.shared_tag = new_tag
        st.session_state.shared_tag_editor = new_tag
        st.session_state.last_no_assimil_mode = no_assimil_mode

    if lesson_numbers:
        selected_lesson_number = st.select_slider(
            "Select Assimil Lesson",
            options=list(lesson_numbers),
            key="lesson_number_picker",
            disabled=no_assimil_mode,
            help="Drag the selector or use the arrow keys to move quickly between lessons.",
        )
        selected_lesson = lesson_numbers[selected_lesson_number]
        # A disabled picker retains its last value. Do not let that stale lesson
        # overwrite the intentionally blank free-practice tag.
        if not no_assimil_mode and selected_lesson != st.session_state.selected_lesson:
            st.session_state.selected_lesson = selected_lesson
            new_lesson_tag = get_lesson_tag(selected_lesson)
            st.session_state.shared_tag = new_lesson_tag
            # The text input has its own keyed widget state. Keep it in sync here;
            # otherwise its value from the previous lesson overwrites shared_tag
            # when the editor is rendered later in this run.
            st.session_state.shared_tag_editor = new_lesson_tag
    elif not no_assimil_mode:
        st.error("No numbered lessons were found in lessons.json.")
        st.stop()

    if no_assimil_mode:
        selected_lesson = "French Practice"
        lesson_data = None
        st.caption("Free practice: Gemini will invent a useful French-learning phrase.")
    else:
        lesson_data = lessons[selected_lesson]

with c2:
    st.markdown("""
    **Enter target words/phrases (one per line):**<br>
    Add extra notes in parentheses `()`.<br>
    *Example:* `comment allez vous (formal way to ask how someone is)`
    """, unsafe_allow_html=True)
    user_input = st.text_area(
        "Target Words",
        key="target_words",
        height=120,
        placeholder="bonjour\ncomment ça va\ns'il vous plaît (please)\nmerci beaucoup",
        on_change=mark_target_words_changed,
    )

if st.button("✨ Generate Initial Flashcards", type="primary"):
    if not api_key:
        st.error("Please provide a Gemini API Key.")
    elif not user_input.strip():
        st.warning("Please enter at least one word.")
    else:
        parsed_items = parse_user_input(user_input)
        with st.spinner("Gemini is crafting flashcards..."):
            try:
                cards = generate_flashcards_with_gemini(
                    api_key, 
                    model_choice, 
                    selected_lesson, 
                    lesson_data,
                    parsed_items,
                    no_assimil_mode=no_assimil_mode,
                    target_language=target_language_name,
                )
                st.session_state.cards_data = cards
                st.session_state.cards_target_language = target_language_code
                st.session_state.card_regeneration_baselines = [
                    dict(card) for card in cards
                ]
                st.session_state.card_form_epoch += 1
                st.session_state.card_form_versions = {}
                st.success(f"Generated {len(cards)} cards! Review them below.")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {str(e)}")

# --- STEP 2: REVIEW & EDIT SECTION ---
if st.session_state.cards_data:
    st.markdown(
        """
        <style>
        /* Keep the review list dense, while retaining comfortable tap targets. */
        div[data-testid="stExpander"] details > summary {
            padding-top: 0.35rem;
            padding-bottom: 0.35rem;
        }
        /* Make the card action obvious: down means it can be opened; up means
           the editor is already showing. Streamlit normally uses right/down. */
        div[data-testid="stExpander"] details > summary svg {
            transform: rotate(90deg);
        }
        div[data-testid="stExpander"] details[open] > summary svg {
            transform: rotate(180deg);
        }
        @media (max-width: 640px) {
            /* Streamlit columns do not automatically stack on small screens. */
            [class*="st-key-card-editor-"] [data-testid="stHorizontalBlock"] {
                flex-direction: column;
                gap: 0;
            }
            [class*="st-key-card-editor-"] [data-testid="stColumn"] {
                width: 100% !important;
                flex: 1 1 100% !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    if len(st.session_state.card_regeneration_baselines) != len(
        st.session_state.cards_data
    ):
        st.session_state.card_regeneration_baselines = [
            dict(card) for card in st.session_state.cards_data
        ]

    st.divider()
    st.subheader("2. Review, Edit & Regenerate Cards")
    st.caption(
        "Cards start collapsed. Select Edit card to edit or regenerate it; edits save automatically."
    )

    # Keep this control aligned with the half-width dropdowns used elsewhere.
    tag_col, _ = st.columns(2)
    with tag_col:
        st.session_state.shared_tag = st.text_input(
            "Shared tag for all cards",
            value=st.session_state.shared_tag,
            help="This tag will be applied to every flashcard in the deck.",
            key="shared_tag_editor",
        )
    
    col_actions1, col_actions2 = st.columns([1, 1])
    with col_actions2:
        if st.button("🗑️ Reset All Cards", use_container_width=True):
            st.session_state.cards_data = None
            st.session_state.card_regeneration_baselines = []
            st.session_state.card_form_epoch += 1
            st.session_state.card_form_versions = {}
            st.session_state.pop("prepared_apkg", None)
            st.session_state.pop("prepared_apkg_signature", None)
            st.session_state.clear_browser_draft = True
            st.rerun()

    cards_list = st.session_state.cards_data
    all_widget_keys = {}

    for idx, card in enumerate(cards_list):
        with st.expander(
            "Edit card · "
            + flashcard_summary(
                card.get("fr_phrase", ""), card.get("fr_word", "")
            ),
            expanded=False,
        ):
            with st.container(key=f"card-editor-{idx}"):
                form_version = st.session_state.card_form_versions.get(idx, 0)
                widget_prefix = f"card_{st.session_state.card_form_epoch}_{idx}_{form_version}"
                widget_keys = {
                    "fr_word": f"{widget_prefix}_fr_word",
                    "fr_phrase": f"{widget_prefix}_fr_phrase",
                    "en_word": f"{widget_prefix}_en_word",
                    "en_phrase": f"{widget_prefix}_en_phrase",
                    "extra_notes": f"{widget_prefix}_notes",
                }
                all_widget_keys[idx] = widget_keys
                col_fr, col_target, col_opt = st.columns([2, 2, 1])

                with col_fr:
                    st.text_input(
                        "French Word",
                        value=card.get("fr_word", ""),
                        key=widget_keys["fr_word"],
                        on_change=save_card_field,
                        args=(idx, "fr_word", widget_keys["fr_word"]),
                    )
                    st.text_area(
                        "French Sentence",
                        value=card.get("fr_phrase", ""),
                        key=widget_keys["fr_phrase"],
                        height=60,
                        on_change=save_card_field,
                        args=(idx, "fr_phrase", widget_keys["fr_phrase"]),
                    )

                with col_target:
                    st.text_input(
                        f"{target_language_name} Word",
                        value=card.get("en_word", ""),
                        key=widget_keys["en_word"],
                        on_change=save_card_field,
                        args=(idx, "en_word", widget_keys["en_word"]),
                    )
                    st.text_area(
                        f"{target_language_name} Sentence",
                        value=card.get("en_phrase", ""),
                        key=widget_keys["en_phrase"],
                        height=60,
                        on_change=save_card_field,
                        args=(idx, "en_phrase", widget_keys["en_phrase"]),
                    )

                with col_opt:
                    st.text_input(
                        "Notes",
                        value=card.get("extra_notes", ""),
                        key=widget_keys["extra_notes"],
                        on_change=save_card_field,
                        args=(idx, "extra_notes", widget_keys["extra_notes"]),
                    )
                    regenerate_clicked = st.button(
                        "🔄 Regenerate",
                        key=f"{widget_prefix}_regenerate",
                        use_container_width=True,
                    )

                if regenerate_clicked:
                    previous_card = dict(st.session_state.card_regeneration_baselines[idx])
                    submitted_card = card_from_widgets(idx, widget_keys)
                    if not api_key:
                        st.error("Please provide a Gemini API Key.")
                    else:
                        with st.spinner(f"Regenerating Card {idx + 1}..."):
                            try:
                                updated_card = regenerate_single_card(
                                    api_key,
                                    model_choice,
                                    selected_lesson,
                                    lesson_data,
                                    previous_card=previous_card,
                                    current_card=submitted_card,
                                    no_assimil_mode=no_assimil_mode,
                                    target_language=target_language_name,
                                )
                                st.session_state.cards_data[idx] = updated_card
                                st.session_state.card_regeneration_baselines[idx] = dict(
                                    updated_card
                                )
                                st.session_state.card_form_versions[idx] = form_version + 1
                                st.toast(f"Card {idx + 1} updated!", icon="🎉")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to regenerate card: {str(e)}")

    # --- STEP 3: APPROVE & DOWNLOAD ---
    st.divider()
    st.subheader("3. Export Deck")

    save_all_card_widgets(all_widget_keys)
    speechify_api_key = st.secrets.get("SPEECHIFY_API_KEY", "")

    if not speechify_api_key:
        st.error(
            "Add SPEECHIFY_API_KEY to .streamlit/secrets.toml before exporting "
            "a deck with French audio."
        )
    else:
        try:
            speechify_voices = cached_french_voices(speechify_api_key)
        except Exception as error:
            speechify_voices = []
            st.error(f"Could not load Speechify voices: {error}")

        if not speechify_voices:
            st.warning(
                "No French Speechify voices compatible with Simba 3 are available "
                "for this API key."
            )
        else:
            voice_ids = [voice["id"] for voice in speechify_voices]
            configured_voice_id = st.secrets.get("SPEECHIFY_VOICE_ID", "")
            saved_voice_is_valid = saved_speechify_voice_id in voice_ids
            if "speechify_voice_id" not in st.session_state and saved_voice_is_valid:
                st.session_state.speechify_voice_id = saved_speechify_voice_id
            elif st.session_state.get("speechify_voice_id") not in voice_ids:
                # A saved voice may no longer be available for the API key.
                st.session_state.pop("speechify_voice_id", None)
            default_voice_index = (
                voice_ids.index(configured_voice_id)
                if configured_voice_id in voice_ids
                else 0
            )
            voice_by_id = {voice["id"]: voice for voice in speechify_voices}
            preview_phrase = (
                st.session_state.cards_data[0].get("fr_phrase", "").strip()
                if st.session_state.cards_data
                else ""
            )

            # Keep voice choice/preview before the export action, at half width.
            voice_col, _ = st.columns(2)
            with voice_col:
                selected_voice_id = st.selectbox(
                    "Select French voice",
                    options=voice_ids,
                    index=default_voice_index,
                    format_func=lambda voice_id: (
                        f"{voice_by_id[voice_id]['display_name']} "
                        f"({voice_by_id[voice_id]['locale']}, "
                        f"{voice_by_id[voice_id]['gender']})"
                    ),
                    help=(
                        "Only voices compatible with French on Simba 3 are shown. "
                        "Audio is generated at 0.7× speed."
                    ),
                    key="speechify_voice_id",
                    on_change=mark_voice_preferences_changed,
                )
                if st.button("▶ Preview selected voice", use_container_width=True):
                    if not preview_phrase:
                        st.warning("The first flashcard needs a French phrase to preview.")
                    else:
                        try:
                            st.session_state.speechify_preview_audio = synthesize_french_audio(
                                speechify_api_key,
                                selected_voice_id,
                                preview_phrase,
                            )
                        except Exception as error:
                            st.error(f"Could not generate voice preview: {error}")

                if preview_audio := st.session_state.get("speechify_preview_audio"):
                    st.audio(preview_audio, format="audio/mpeg")

            cards_for_export = [dict(card) for card in st.session_state.cards_data]
            lesson_for_export = st.session_state.selected_lesson
            tag_for_export = st.session_state.shared_tag
            voice_for_export = selected_voice_id
            lesson_num = re.sub(r'\D', '', lesson_for_export) or "01"
            export_file_name = (
                f"French_{target_language['deck_code']}_Practice.apkg"
                if no_assimil_mode
                else (
                    f"Assimil_FR2{target_language['deck_code']}_"
                    f"Lesson_{lesson_num.zfill(2)}.apkg"
                )
            )

            export_signature = hashlib.sha256(
                json.dumps(
                    {
                        "cards": cards_for_export,
                        "lesson": lesson_for_export,
                        "tag": tag_for_export,
                        "voice": voice_for_export,
                        "language": target_language_code,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()

            if st.session_state.get("prepared_apkg_signature") != export_signature:
                st.session_state.pop("prepared_apkg", None)
                st.session_state.pop("prepared_apkg_signature", None)

            if st.button(
                "📦 Approve All & Download .apkg",
                type="primary",
                use_container_width=True,
            ):
                try:
                    with st.spinner(
                        "Generating French pronunciations and packaging deck..."
                    ):
                        st.session_state.prepared_apkg = build_anki_apkg(
                            cards_for_export,
                            lesson_for_export,
                            speechify_api_key,
                            voice_for_export,
                            shared_tag=tag_for_export,
                            target_language_code=target_language_code,
                        ).getvalue()
                    st.session_state.prepared_apkg_signature = export_signature
                    # Download once on the following render, then clear this
                    # request so ordinary reruns never download it again.
                    st.session_state.apkg_auto_download_signature = export_signature
                    # The generated package remains available for the automatic
                    # download, while all disposable browser draft data is reset.
                    # Preferences live separately from disposable draft data.
                    st.session_state.cleanup_after_successful_export = True
                    clear_browser_draft = True
                    clear_target_words = True
                except Exception as error:
                    st.session_state.pop("prepared_apkg", None)
                    st.session_state.pop("prepared_apkg_signature", None)
                    st.session_state.pop("apkg_auto_download_signature", None)
                    st.error(f"Could not prepare the Anki package: {error}")

            if prepared_apkg := st.session_state.get("prepared_apkg"):
                if (
                    st.session_state.get("apkg_auto_download_signature")
                    == export_signature
                ):
                    apkg_base64 = base64.b64encode(prepared_apkg).decode("ascii")
                    components.html(
                        f'''<script>
                        const encodedPackage = {json.dumps(apkg_base64)};
                        const binary = atob(encodedPackage);
                        const bytes = Uint8Array.from(
                          binary, (character) => character.charCodeAt(0)
                        );
                        const packageUrl = URL.createObjectURL(new Blob(
                          [bytes], {{ type: "application/octet-stream" }}
                        ));
                        // The component itself is an iframe. Add the link to the
                        // app page so its sandbox cannot prevent the download.
                        const appDocument = window.parent.document;
                        const link = appDocument.createElement("a");
                        link.href = packageUrl;
                        link.download = {json.dumps(export_file_name)};
                        appDocument.body.appendChild(link);
                        link.click();
                        link.remove();
                        // Let the browser start reading the blob before freeing it.
                        setTimeout(() => URL.revokeObjectURL(packageUrl), 1000);
                        </script>''',
                        height=0,
                    )
                    st.session_state.pop("apkg_auto_download_signature", None)

# Write only preferences, card data, and ordinary strings to localStorage. API
# keys, audio, and the prepared package deliberately remain server-side.
if st.session_state.pop("preferences_dirty", False):
    preferences_json = build_preferences_json(
        target_language_code,
        st.session_state.no_assimil_mode,
        st.session_state.get("speechify_voice_id", saved_speechify_voice_id),
    )
    _draft_storage(
        action="write",
        draft=preferences_json,
        storage_key=PREFERENCES_STORAGE_KEY,
        key="assimil_preferences_writer",
        default=None,
    )

browser_draft = build_draft_json(
    st.session_state.cards_data,
    st.session_state.card_regeneration_baselines,
    target_language_code,
    st.session_state.get("selected_lesson", "French Practice"),
    st.session_state.shared_tag,
    st.session_state.no_assimil_mode,
)
draft_write_action = "clear" if clear_browser_draft else "noop"
if browser_draft and not clear_browser_draft:
    draft_write_action = "write"
_draft_storage(
    action=draft_write_action,
    draft=browser_draft,
    storage_key="assimil-flashcard-draft-v1",
    key="assimil_draft_writer",
    default=None,
)

target_words_write_action = "noop"
if clear_target_words:
    target_words_write_action = "clear"
elif st.session_state.get("target_words_storage_applied"):
    target_words_write_action = "write"
_draft_storage(
    action=target_words_write_action,
    draft=st.session_state.target_words,
    storage_key=TARGET_WORDS_STORAGE_KEY,
    key="assimil_target_words_writer",
    default=None,
)
