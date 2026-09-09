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
import time
from pathlib import Path

import streamlit.components.v1 as components

from draft_state import build_draft_json, restore_draft_json
from flashcard_regeneration import build_regeneration_prompt
from highlight_markup import (
    HIGHLIGHT_PATTERN,
    ensure_target_marker,
    phrase_highlight_error,
    repair_target_marker,
    strip_highlight_markers,
)
from notes_tidy import build_tidy_notes_prompt
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
TIDY_NOTES_UNDO_DURATION_SECONDS = 5

_draft_storage = components.declare_component(
    "assimil_draft_storage",
    path=str(Path(__file__).parent / "components" / "draft_storage"),
)
_paste_textarea = components.declare_component(
    "assimil_paste_textarea",
    path=str(Path(__file__).parent / "components" / "paste_textarea"),
)

FRONT_FR2EN = r"""
{{#fr_phrase}}
<div class="phrase marked-phrase">{{fr_phrase}}</div>
{{fr_audio}}
{{/fr_phrase}}

{{^fr_phrase}}
<div>{{fr_word}}</div>
{{fr_audio}}
{{/fr_phrase}}

<br><br>
{{type:nc:en_word}}

<script>
document.querySelectorAll(".marked-phrase").forEach((phrase) => {
  const source = phrase.textContent;
  const marker = /&([^&]*?)&/g;
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  let match;
  while ((match = marker.exec(source)) !== null) {
    fragment.append(document.createTextNode(source.slice(cursor, match.index)));
    const highlight = document.createElement("span");
    highlight.className = "highlight";
    highlight.textContent = match[1];
    fragment.append(highlight);
    cursor = marker.lastIndex;
  }
  if (cursor) {
    fragment.append(document.createTextNode(source.slice(cursor)));
    phrase.replaceChildren(fragment);
  }
});
</script>
"""

BACK_FR2EN = r"""
{{#fr_phrase}}
<div class="phrase marked-phrase">{{fr_phrase}}</div>
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

{{#en_phrase}}
<div class="translation marked-phrase">{{en_phrase}}</div>
{{/en_phrase}}
{{^en_phrase}}
<div class="translation">{{en_word}}</div>
{{/en_phrase}}

{{#extra_notes}}
<div class="notes">Note: {{extra_notes}}</div>
{{/extra_notes}}

<script>
document.querySelectorAll(".marked-phrase").forEach((phrase) => {
  const source = phrase.textContent;
  const marker = /&([^&]*?)&/g;
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  let match;
  while ((match = marker.exec(source)) !== null) {
    fragment.append(document.createTextNode(source.slice(cursor, match.index)));
    const highlight = document.createElement("span");
    highlight.className = "highlight";
    highlight.textContent = match[1];
    fragment.append(highlight);
    cursor = marker.lastIndex;
  }
  if (cursor) {
    fragment.append(document.createTextNode(source.slice(cursor)));
    phrase.replaceChildren(fragment);
  }
});
</script>
"""

FRONT_EN2FR = r"""
{{#en_phrase}}
<div class="phrase marked-phrase">{{en_phrase}}</div>
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
document.querySelectorAll(".marked-phrase").forEach((phrase) => {
  const source = phrase.textContent;
  const marker = /&([^&]*?)&/g;
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  let match;
  while ((match = marker.exec(source)) !== null) {
    fragment.append(document.createTextNode(source.slice(cursor, match.index)));
    const highlight = document.createElement("span");
    highlight.className = "highlight";
    highlight.textContent = match[1];
    fragment.append(highlight);
    cursor = marker.lastIndex;
  }
  if (cursor) {
    fragment.append(document.createTextNode(source.slice(cursor)));
    phrase.replaceChildren(fragment);
  }
});
</script>
"""

BACK_EN2FR = r"""
{{#en_phrase}}
<div class="phrase marked-phrase">{{en_phrase}}</div>
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

{{#fr_phrase}}
<div class="translation marked-phrase">{{fr_phrase}}</div>
{{/fr_phrase}}
{{^fr_phrase}}
<div class="translation">{{fr_word}}</div>
{{/fr_phrase}}

{{#fr_phrase}}
{{fr_audio}}
{{/fr_phrase}}
{{^fr_phrase}}
{{fr_audio}}
{{/fr_phrase}}

<script>
document.querySelectorAll(".marked-phrase").forEach((phrase) => {
  const source = phrase.textContent;
  const marker = /&([^&]*?)&/g;
  const fragment = document.createDocumentFragment();
  let cursor = 0;
  let match;
  while ((match = marker.exec(source)) !== null) {
    fragment.append(document.createTextNode(source.slice(cursor, match.index)));
    const highlight = document.createElement("span");
    highlight.className = "highlight";
    highlight.textContent = match[1];
    fragment.append(highlight);
    cursor = marker.lastIndex;
  }
  if (cursor) {
    fragment.append(document.createTextNode(source.slice(cursor)));
    phrase.replaceChildren(fragment);
  }
});
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
    fr_phrase: str = Field(description="Natural French sentence with fr_word wrapped in &ampersands&")
    # These legacy JSON keys keep existing saved cards compatible. Their values
    # contain whichever target language the user selected.
    en_word: str = Field(description="Direct target-language translation of fr_word")
    en_phrase: str = Field(description="Target-language translation with en_word wrapped in &ampersands&")
    extra_notes: str = Field(description="User notes combined with brief grammar tips if useful")


class TidiedTargetNotes(BaseModel):
    lines: list[str] = Field(
        description="Target French words or phrases, one per string, with optional notes"
    )


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


def flashcard_summary(phrase, target):
    """Create a compact, safely escaped expander label for a flashcard.

    Streamlit expanders support its colour-background Markdown directive, so the
    target can remain visible even while the card editor is collapsed.
    """
    def escape_label_markdown(value):
        # Escape the Markdown constructs that could otherwise change the label.
        return re.sub(r"([\\`*_{}\[\]()<>#+\-.!|])", r"\\\1", value)

    if not phrase:
        return escape_label_markdown(phrase or "New Card")

    parts = []
    original_cursor = 0
    for match in HIGHLIGHT_PATTERN.finditer(phrase):
        match_start, match_end = match.span()
        parts.append(escape_label_markdown(phrase[original_cursor:match_start]))
        highlighted_text = escape_label_markdown(match.group(1))
        parts.append(f":orange-background[{highlighted_text}]")
        original_cursor = match_end

    if not parts:
        return escape_label_markdown(phrase)
    parts.append(escape_label_markdown(phrase[original_cursor:]))
    return "".join(parts)


def ensure_card_markers(card):
    """Upgrade generated or legacy card phrases to explicit highlight markup."""
    card["fr_phrase"] = ensure_target_marker(
        card.get("fr_phrase", ""), card.get("fr_word", "")
    )
    card["en_phrase"] = ensure_target_marker(
        card.get("en_phrase", ""), card.get("en_word", "")
    )
    return card


def card_highlight_errors(card, target_language="Target-language"):
    """Return phrase containment/markup errors that must be fixed for export."""
    errors = []
    french_error = phrase_highlight_error(
        card.get("fr_phrase", ""),
        card.get("fr_word", ""),
        phrase_label="French sentence",
        target_label="French word",
    )
    if french_error:
        errors.append(french_error)
    target_error = phrase_highlight_error(
        card.get("en_phrase", ""),
        card.get("en_word", ""),
        phrase_label=f"{target_language} sentence",
        target_label=f"{target_language} word",
    )
    if target_error:
        errors.append(target_error)
    return errors


def repair_card_highlights_once(client, model_name, cards, target_language):
    """Ask Gemini once to repair invalid phrases, then validate the result again.

    Words, notes, and metadata remain authoritative. Only the two phrase fields
    are accepted from Gemini's repair response.
    """
    # Incorrect marker placement is deterministic when the target is already in
    # the phrase. Repair that locally before paying for a semantic rewrite.
    for card in cards:
        card["fr_phrase"] = repair_target_marker(
            card.get("fr_phrase", ""), card.get("fr_word", "")
        )
        card["en_phrase"] = repair_target_marker(
            card.get("en_phrase", ""), card.get("en_word", "")
        )

    invalid_cards = []
    for card_index, card in enumerate(cards):
        errors = card_highlight_errors(card, target_language)
        if errors:
            invalid_cards.append(
                {
                    "card_index": card_index,
                    "errors": errors,
                    "card": card,
                }
            )

    if not invalid_cards:
        return cards, []

    prompt = f"""
    You are repairing phrase validation errors in French study flashcards.

    Invalid cards, including their zero-based card indexes and validation errors:
    {json.dumps(invalid_cards, ensure_ascii=False, indent=2)}

    Every supplied card is invalid. Return exactly one repaired card for each
    input card, in the same order. You MUST rewrite each phrase named in its
    validation errors; never return an invalid phrase unchanged.
    Keep `fr_word`, `en_word`, and `extra_notes` exactly unchanged. Rewrite only
    `fr_phrase` and/or `en_phrase` when necessary.

    Each French phrase must naturally contain its exact `fr_word`, and each
    {target_language} phrase must naturally contain its exact `en_word`.
    Comparisons ignore letter case and repeated or surrounding whitespace.
    Wrap exactly the intended occurrence in ampersands, such as
    `&Le& chat est sur la table`. Use one matching marker pair per phrase.
    Return valid JSON matching the requested schema.
    """

    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=list[FlashcardItem],
            temperature=0.2,
        ),
    )
    repaired_cards = json.loads(response.text)
    if len(repaired_cards) != len(invalid_cards):
        raise ValueError("Gemini returned the wrong number of repaired cards.")

    for invalid_card, repaired_card in zip(invalid_cards, repaired_cards):
        card = cards[invalid_card["card_index"]]
        card["fr_phrase"] = repaired_card.get("fr_phrase", card.get("fr_phrase", ""))
        card["en_phrase"] = repaired_card.get("en_phrase", card.get("en_phrase", ""))
        ensure_card_markers(card)

    remaining_errors = [
        f"Card {card_number}: {error}"
        for card_number, card in enumerate(cards, start=1)
        for error in card_highlight_errors(card, target_language)
    ]
    return cards, remaining_errors


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


def tidy_target_notes(api_key, model_name, raw_text):
    """Rewrite rough notes into the compact input format used by card generation."""
    client = genai.Client(api_key=api_key)
    prompt = build_tidy_notes_prompt(raw_text)
    response = client.models.generate_content(
        model=model_name,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=TidiedTargetNotes,
            temperature=0.2,
        ),
    )
    payload = json.loads(response.text)
    lines = payload.get("lines") if isinstance(payload, dict) else None
    if not isinstance(lines, list) or not all(isinstance(line, str) for line in lines):
        raise ValueError("Gemini returned an invalid notes list.")

    cleaned_lines = [" ".join(line.split()).strip() for line in lines]
    result = "\n".join(line for line in cleaned_lines if line)
    if not result:
        raise ValueError("Gemini did not find any target words or phrases.")
    if len(result) > MAX_TARGET_WORDS_LENGTH:
        raise ValueError("The tidied notes are too long.")
    return result


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
       - The cleaned `fr_word` must appear inside `fr_phrase`; comparison ignores
         case and whitespace.
       - Wrap exactly the intended occurrence in ampersands. Example:
         for `fr_word` = `le`, write `&Le& chat est sur la table`.
    3. Write the {target_language} translation in the legacy JSON fields
       `en_word` and `en_phrase`.
       - The cleaned `en_word` must appear inside `en_phrase`; comparison ignores
         case and whitespace. Wrap its intended occurrence in ampersands too.
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
        ensure_card_markers(item)
        if idx < len(parsed_items):
            item["raw_word"] = parsed_items[idx]["raw_word"]
            item["user_notes"] = parsed_items[idx]["user_notes"]

    try:
        parsed_cards, _ = repair_card_highlights_once(
            client, model_name, parsed_cards, target_language
        )
    except Exception:
        # Keep the successfully generated cards available for review. The export
        # section offers the same repair again and reports API errors explicitly.
        pass
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
    ensure_card_markers(new_card)
    try:
        repaired_cards, _ = repair_card_highlights_once(
            client, model_name, [new_card], target_language
        )
        return repaired_cards[0]
    except Exception:
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

    validation_errors = []
    for card_number, item in enumerate(cards_data, start=1):
        for error in card_highlight_errors(
            item, TARGET_LANGUAGES[target_language_code]["name"]
        ):
            validation_errors.append(f"Card {card_number}: {error}")
    if validation_errors:
        raise ValueError(" ".join(validation_errors))
    
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
            french_text = strip_highlight_markers(
                french_phrase or french_word
            ).strip()
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
                    html.escape(french_word),
                    html.escape(french_phrase),
                    html.escape(target_word),
                    html.escape(target_phrase),
                    html.escape(item.get("extra_notes", "")),
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
st.set_page_config(page_title="Anki Generator", page_icon="🇫🇷", layout="wide")

# Streamlit reserves generous dashboard-style gutters by default. This app's
# first step is intentionally denser so the complete input flow remains visible
# without scrolling on a typical laptop or iPhone 15 viewport.
st.markdown(
    """
    <style>
    .stMain .block-container,
    [data-testid="stAppViewBlockContainer"],
    [data-testid="stMainBlockContainer"] {
        max-width: 74rem;
        padding: 0.75rem 2rem 2rem;
    }
    .st-key-app_header [data-testid="stHorizontalBlock"],
    .st-key-app-header [data-testid="stHorizontalBlock"] {
        align-items: flex-end;
        gap: 1rem;
    }
    .st-key-app_header h1,
    .st-key-app-header h1 {
        font-size: clamp(1.85rem, 3vw, 2.45rem);
        line-height: 1.1;
        margin: 0;
        padding: 0 0 0.3rem;
    }
    .st-key-app_header [data-testid="stSelectbox"],
    .st-key-app-header [data-testid="stSelectbox"] {
        margin-bottom: 0;
    }
    .st-key-notes_input_layout,
    .st-key-notes-input-layout {
        margin-top: -0.2rem;
    }
    /* Zero-height browser-storage components between the header and Step 1
       still receive Streamlit's root flex spacing. Compensate at the heading
       only when the optional API-key field is not occupying that space. */
    .st-key-input_step_heading_compact,
    .st-key-input-step-heading-compact {
        margin-top: -4.5rem;
    }
    .st-key-notes_input_layout [data-testid="stHorizontalBlock"],
    .st-key-notes-input-layout [data-testid="stHorizontalBlock"] {
        gap: 1rem;
    }
    .st-key-notes_input_layout [data-testid="stVerticalBlock"],
    .st-key-notes-input-layout [data-testid="stVerticalBlock"] {
        gap: 0.55rem;
    }
    .st-key-generate_initial_flashcards,
    .st-key-generate-initial-flashcards {
        margin-top: 0.25rem;
    }
    .st-key-generate_initial_flashcards button,
    .st-key-generate-initial-flashcards button {
        border: 0 !important;
        background: linear-gradient(
            110deg,
            #4285f4 0%,
            #8b5cf6 34%,
            #d946ef 62%,
            #f97316 100%
        ) !important;
        color: #fff !important;
        box-shadow: 0 0.2rem 0.65rem rgba(139, 92, 246, 0.24);
    }
    .st-key-generate_initial_flashcards button:hover,
    .st-key-generate-initial-flashcards button:hover {
        color: #fff !important;
        filter: brightness(1.06);
    }
    @media (max-width: 640px) {
        .stMain .block-container,
        [data-testid="stAppViewBlockContainer"],
        [data-testid="stMainBlockContainer"] {
            padding: 0.45rem 0.85rem 1.25rem;
        }
        .st-key-app_header [data-testid="stHorizontalBlock"],
        .st-key-app-header [data-testid="stHorizontalBlock"] {
            align-items: stretch;
            flex-direction: column !important;
            flex-wrap: nowrap !important;
            gap: 0.45rem;
        }
        .st-key-app_header [data-testid="stColumn"],
        .st-key-app-header [data-testid="stColumn"] {
            width: 100% !important;
            min-width: 100% !important;
            flex: 1 1 auto !important;
        }
        .st-key-app_header [data-testid="stColumn"]:first-child,
        .st-key-app-header [data-testid="stColumn"]:first-child {
            margin-bottom: 0.35rem;
        }
        .st-key-app_header h1,
        .st-key-app-header h1 {
            font-size: 1.75rem;
            padding-bottom: 0.05rem;
        }
        .st-key-notes_input_layout [data-testid="stHorizontalBlock"],
        .st-key-notes-input-layout [data-testid="stHorizontalBlock"] {
            gap: 0.4rem;
        }
        .st-key-notes_input_layout [data-testid="stVerticalBlock"],
        .st-key-notes-input-layout [data-testid="stVerticalBlock"] {
            gap: 0.35rem;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

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

with st.container(key="app_header"):
    header_col1, header_col_language, header_col_model = st.columns([3, 1.15, 1.15])

    with header_col1:
        st.title("🇫🇷 Anki Generator", anchor=False)

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
tidy_notes_undo_expires_at = st.session_state.get("tidy_notes_undo_expires_at")
if (
    isinstance(tidy_notes_undo_expires_at, (int, float))
    and time.time() >= tidy_notes_undo_expires_at
):
    st.session_state.pop("tidy_notes_original", None)
    st.session_state.pop("tidy_notes_undo_expires_at", None)
    tidy_notes_undo_expires_at = None
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
input_step_heading_key = (
    "input_step_heading_compact" if api_key else "input_step_heading"
)
with st.container(key=input_step_heading_key):
    st.subheader("1. Write your notes", anchor=False)
with st.container(key="notes_input_layout"):
    c1, _, c2 = st.columns([1, 0.16, 2.4])

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
    else:
        lesson_data = lessons[selected_lesson]

with c2:
    st.caption(
        "One word or phrase per line · optional notes in parentheses, "
        "e.g. `comment allez vous (formal way)`"
    )
    paste_result = _paste_textarea(
        textarea_label="Target Words",
        undo_available_until=tidy_notes_undo_expires_at,
        key="target_words_paste_button",
        default=None,
    )
    if isinstance(paste_result, dict):
        paste_request_id = paste_result.get("request_id")
        if (
            paste_request_id
            and paste_request_id
            != st.session_state.get("last_target_words_paste_request")
        ):
            st.session_state.last_target_words_paste_request = paste_request_id
            if paste_result.get("action") == "tidy":
                notes_to_tidy = paste_result.get("value", "")
                if not api_key:
                    st.toast("Add your Gemini API key before tidying notes.", icon="⚠️")
                elif not isinstance(notes_to_tidy, str) or not notes_to_tidy.strip():
                    st.toast("Paste or enter some notes first.", icon="⚠️")
                else:
                    with st.spinner("Tidying notes with Gemini..."):
                        try:
                            st.session_state.target_words = tidy_target_notes(
                                api_key,
                                model_choice,
                                notes_to_tidy,
                            )
                            st.session_state.target_words_storage_applied = True
                            st.session_state.tidy_notes_original = notes_to_tidy
                            st.session_state.tidy_notes_undo_expires_at = (
                                time.time() + TIDY_NOTES_UNDO_DURATION_SECONDS
                            )
                            # Render again so the injected undo control receives
                            # the newly created expiry time immediately.
                            st.rerun()
                        except Exception as error:
                            st.error(f"Could not tidy the notes: {error}")
            elif paste_result.get("action") == "undo_tidy":
                original_notes = st.session_state.get("tidy_notes_original")
                undo_expires_at = st.session_state.get("tidy_notes_undo_expires_at")
                if (
                    isinstance(original_notes, str)
                    and isinstance(undo_expires_at, (int, float))
                    and time.time() < undo_expires_at
                ):
                    st.session_state.target_words = original_notes
                    st.session_state.target_words_storage_applied = True
                    st.toast("Restored your original notes.", icon="↩️")
                else:
                    st.toast("The tidy-notes undo window has expired.", icon="⏱️")
                st.session_state.pop("tidy_notes_original", None)
                st.session_state.pop("tidy_notes_undo_expires_at", None)
                st.rerun()
            elif paste_result.get("status") == "success":
                pasted_value = paste_result.get("value", "")
                if (
                    isinstance(pasted_value, str)
                    and len(pasted_value) <= MAX_TARGET_WORDS_LENGTH
                ):
                    st.session_state.target_words = pasted_value
                    st.session_state.target_words_storage_applied = True
                else:
                    st.toast("That clipboard text is too long to paste.", icon="⚠️")
            else:
                st.toast(
                    "Clipboard access was blocked. Tap and hold the text box to paste.",
                    icon="⚠️",
                )
    user_input = st.text_area(
        "Target Words",
        key="target_words",
        height=270,
        placeholder="bonjour\ncomment ça va\ns'il vous plaît (please)\nmerci beaucoup",
        on_change=mark_target_words_changed,
    )

if st.button(
    "✨ Generate Initial Flashcards",
    type="primary",
    key="generate_initial_flashcards",
):
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
    st.subheader("2. Review & Edit Cards", anchor=False)
    st.caption(
        "Cards start collapsed. Select Edit card to edit or regenerate it. "
        "Edits save automatically."
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
        ensure_card_markers(card)
        if idx < len(st.session_state.card_regeneration_baselines):
            ensure_card_markers(st.session_state.card_regeneration_baselines[idx])
        with st.expander(
            ":gray[Edit card ·] "
            + flashcard_summary(
                card.get("fr_phrase", ""), card.get("fr_word", "")
            ),
            expanded=False,
        ):
            with st.container(key=f"card-editor-{idx}"):
                st.caption(
                    "`&word&` marks the text to highlight. Keep these markers when "
                    "editing, or place them back around the intended word."
                )
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
                        help="Wrap the intended French word between ampersands: &word&.",
                        on_change=save_card_field,
                        args=(idx, "fr_phrase", widget_keys["fr_phrase"]),
                    )
                    if french_error := phrase_highlight_error(
                        card.get("fr_phrase", ""),
                        card.get("fr_word", ""),
                        phrase_label="French sentence",
                        target_label="French word",
                    ):
                        st.error(french_error)

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
                        help=(
                            f"Wrap the intended {target_language_name} word in "
                            "ampersands: &word&."
                        ),
                        on_change=save_card_field,
                        args=(idx, "en_phrase", widget_keys["en_phrase"]),
                    )
                    if target_error := phrase_highlight_error(
                        card.get("en_phrase", ""),
                        card.get("en_word", ""),
                        phrase_label=f"{target_language_name} sentence",
                        target_label=f"{target_language_name} word",
                    ):
                        st.error(target_error)

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
    st.subheader("3. Export Deck", anchor=False)

    save_all_card_widgets(all_widget_keys)
    cards_for_export = [dict(card) for card in st.session_state.cards_data]
    export_validation_errors = [
        f"Card {card_number}: {error}"
        for card_number, card in enumerate(cards_for_export, start=1)
        for error in card_highlight_errors(card, target_language_name)
    ]
    if repair_result := st.session_state.pop("highlight_repair_result", None):
        if repair_result["success"]:
            st.success(repair_result["message"])
        else:
            st.warning(repair_result["message"])
    if export_validation_errors:
        st.error(
            "Export is locked until the phrase errors are fixed: "
            + " ".join(export_validation_errors)
        )
        if st.button(
            "✨ Fix phrase errors",
            disabled=not bool(api_key),
            help=(
                "Wrong markers are moved first. Remaining phrase errors get one "
                "repair pass, and every card is validated again."
            ),
        ):
            try:
                with st.spinner("Repairing and rechecking the phrases..."):
                    repair_client = genai.Client(api_key=api_key)
                    repaired_cards, remaining_errors = repair_card_highlights_once(
                        repair_client,
                        model_choice,
                        cards_for_export,
                        target_language_name,
                    )
                st.session_state.cards_data = repaired_cards
                st.session_state.card_regeneration_baselines = [
                    dict(card) for card in repaired_cards
                ]
                st.session_state.card_form_epoch += 1
                st.session_state.card_form_versions = {}
                st.session_state.pop("prepared_apkg", None)
                st.session_state.pop("prepared_apkg_signature", None)
                st.session_state.highlight_repair_result = {
                    "success": not remaining_errors,
                    "message": (
                        "✨ Phrase errors fixed. Export is unlocked."
                        if not remaining_errors
                        else "Some phrase errors remain after one repair attempt: "
                        + " ".join(remaining_errors)
                    ),
                }
                st.rerun()
            except Exception as error:
                st.error(f"Could not repair the phrase errors: {error}")

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
                strip_highlight_markers(
                    st.session_state.cards_data[0].get("fr_phrase", "")
                ).strip()
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
                disabled=bool(export_validation_errors),
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
