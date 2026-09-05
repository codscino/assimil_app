import streamlit as st
import genanki
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import json
import re
import io
import hashlib
import tempfile
from pathlib import Path

import requests

from flashcard_regeneration import build_regeneration_prompt

# -----------------------------------------------------------------------------
# 1. ANKI MODEL DEFINITION (Raw Strings)
# -----------------------------------------------------------------------------
# Changed because the model now has an embedded ElevenLabs audio field.
MODEL_ID = 1607392320
FR2EN_DECK_ID = 2059500001
EN2FR_DECK_ID = 2059500002

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
{{type:en_word}}

<script>
const phrase = document.querySelector(".phrase");
const word = document.querySelector(".target-word")?.textContent.trim();

if (phrase && word) {
  const escaped = word.replace(/[-\/\\^$*+?.()|[\]{}]/g, "\\$&");
  phrase.innerHTML = phrase.innerHTML.replace(
    new RegExp(escaped, "gi"),
    "<span class='highlight'>$&</span>"
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
{{type:en_word}}

<div class="translation">
  {{#en_phrase}}{{en_phrase}}{{/en_phrase}}
  {{^en_phrase}}{{en_word}}{{/en_phrase}}
</div>

{{#en_phrase}}
{{tts en_US:en_phrase}}
{{/en_phrase}}
{{^en_phrase}}
{{tts en_US:en_word}}
{{/en_phrase}}

{{#extra_notes}}
<div class="notes">Note: {{extra_notes}}</div>
{{/extra_notes}}

<script>
const phrase = document.querySelector(".phrase");
const word = document.querySelector(".target-word")?.textContent.trim();

if (phrase && word) {
  const escaped = word.replace(/[-\/\\^$*+?.()|[\]{}]/g, "\\$&");
  phrase.innerHTML = phrase.innerHTML.replace(
    new RegExp(escaped, "gi"),
    "<span class='highlight'>$&</span>"
  );
}
</script>
"""

FRONT_EN2FR = r"""
{{#en_phrase}}
<div class="phrase">{{en_phrase}}</div>
<span class="target-word">{{text:en_word}}</span>
{{tts en_US:en_phrase}}
{{/en_phrase}}

{{^en_phrase}}
<div>{{en_word}}</div>
{{tts en_US:en_word}}
{{/en_phrase}}

{{#extra_notes}}
<div class="notes">Note: {{extra_notes}}</div>
{{/extra_notes}}

<br><br>
{{type:fr_word}}

<script>
const phrase = document.querySelector(".phrase");
const word = document.querySelector(".target-word")?.textContent.trim();

if (phrase && word) {
  const escaped = word.replace(/[-\/\\^$*+?.()|[\]{}]/g, "\\$&");
  phrase.innerHTML = phrase.innerHTML.replace(
    new RegExp(escaped, "gi"),
    "<span class='highlight'>$&</span>"
  );
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
{{type:fr_word}}

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

if (phrase && word) {
  const escaped = word.replace(/[-\/\\^$*+?.()|[\]{}]/g, "\\$&");
  phrase.innerHTML = phrase.innerHTML.replace(
    new RegExp(escaped, "gi"),
    "<span class='highlight'>$&</span>"
  );
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
.target-word {
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

anki_model = genanki.Model(
    MODEL_ID,
    'Assimil French Model EN',
    fields=[
        {'name': 'fr_word'},
        {'name': 'fr_phrase'},
        {'name': 'en_word'},
        {'name': 'en_phrase'},
        {'name': 'extra_notes'},
        {'name': 'fr_audio'},
    ],
    templates=[
        {
            'name': 'FR -> EN',
            'qfmt': FRONT_FR2EN,
            'afmt': BACK_FR2EN,
            'did': FR2EN_DECK_ID,
        },
        {
            'name': 'EN -> FR',
            'qfmt': FRONT_EN2FR,
            'afmt': BACK_EN2FR,
            'did': EN2FR_DECK_ID,
        },
    ],
    css=CARD_STYLE
)

# -----------------------------------------------------------------------------
# 2. HELPER FUNCTIONS & SCHEMAS
# -----------------------------------------------------------------------------
class FlashcardItem(BaseModel):
    fr_word: str = Field(description="Cleaned target French word or expression")
    fr_phrase: str = Field(description="Natural French sentence featuring the target word matching Assimil style")
    en_word: str = Field(description="Direct English translation of fr_word")
    en_phrase: str = Field(description="English translation of fr_phrase")
    extra_notes: str = Field(description="User notes combined with brief grammar tips if useful")

def load_lessons():
    """Read the current lesson file on every rerun.

    The JSON is small, and caching this no-argument function can otherwise keep
    serving an earlier version after lessons.json is updated on a deployment.
    """
    with open("lessons.json", "r", encoding="utf-8") as f:
        return json.load(f)

def get_lesson_tag(lesson_name):
    lesson_num = re.sub(r'\D', '', lesson_name) or "01"
    lesson_num_padded = lesson_num.zfill(2)
    return f"assimil_lesson_{lesson_num_padded}"


def get_lesson_number(lesson_name):
    """Return the numeric part of a lesson key, e.g. ``Lesson 12`` -> 12."""
    match = re.search(r'\d+', lesson_name)
    return int(match.group()) if match else None

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

def generate_flashcards_with_gemini(api_key, model_name, lesson_name, lesson_data, parsed_items):
    client = genai.Client(api_key=api_key)
    lesson_tag_main = get_lesson_tag(lesson_name)

    prompt = f"""
    You are an expert French tutor creating Anki flashcards for the Assimil method.

    Lesson context:
    - Lesson name: {lesson_name}
    - Shared tag: `{lesson_tag_main}`

    Reference sentences from this lesson:
    {json.dumps(lesson_data, ensure_ascii=False, indent=2)}

    User target words/phrases:
    {json.dumps(parsed_items, ensure_ascii=False, indent=2)}

    Instructions for each card:
    1. Preserve the French target as written for `fr_word`; only correct clear spelling mistakes.
    2. Write a natural French sentence for `fr_phrase` that matches the conversational Assimil style.
       - The cleaned `fr_word` must appear inside `fr_phrase` (case-insensitive).
    3. Write the English translation for `en_word` and `en_phrase`.
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
):
    client = genai.Client(api_key=api_key)
    lesson_tag_main = get_lesson_tag(lesson_name)

    prompt = build_regeneration_prompt(
        lesson_name,
        lesson_tag_main,
        lesson_data,
        previous_card,
        current_card,
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
def list_elevenlabs_voices(elevenlabs_api_key):
    """Return the voices available to the account, cached for one hour."""
    response = requests.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": elevenlabs_api_key},
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("voices", [])


def synthesize_french_audio(elevenlabs_api_key, voice_id, text):
    """Generate an Anki-friendly MP3 using a multilingual French request."""
    response = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers={
            "xi-api-key": elevenlabs_api_key,
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "language_code": "fr",
            # ElevenLabs supports 0.7–1.2; 0.7 is its slowest supported pace.
            "voice_settings": {"speed": 0.7},
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.content


def build_anki_apkg(
    cards_data,
    lesson_name,
    elevenlabs_api_key,
    elevenlabs_voice_id,
    shared_tag=None,
):
    lesson_num = re.sub(r'\D', '', lesson_name) or "01"
    lesson_num_padded = lesson_num.zfill(2)

    tag_name = (shared_tag or get_lesson_tag(lesson_name)).strip()
    if not tag_name:
        tag_name = f"assimil_lesson_{lesson_num_padded}"
    
    # FR -> EN is the source deck; DirectionalDeck moves each EN -> FR card
    # (template ordinal 1) to the fixed reverse deck.
    deck = DirectionalDeck(FR2EN_DECK_ID, "FR2EN", EN2FR_DECK_ID)
    reverse_deck = genanki.Deck(EN2FR_DECK_ID, "EN2FR")
    
    with tempfile.TemporaryDirectory() as temp_dir:
        media_files = []
        generated_audio = {}

        for item in cards_data:
            french_text = (item.get("fr_phrase") or item.get("fr_word") or "").strip()
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
                            elevenlabs_api_key, elevenlabs_voice_id, french_text
                        )
                    )
                    generated_audio[filename] = audio_path
                    media_files.append(str(audio_path))
                audio_field = f"[sound:{filename}]"

            note = genanki.Note(
                model=anki_model,
                fields=[
                    item.get("fr_word", ""),
                    item.get("fr_phrase", ""),
                    item.get("en_word", ""),
                    item.get("en_phrase", ""),
                    item.get("extra_notes", ""),
                    audio_field,
                ],
                tags=[tag_name],
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

header_col1, header_col2 = st.columns([3, 1])

with header_col1:
    st.title("🇫🇷 Assimil French Anki Generator")

with header_col2:
    model_choice = st.selectbox(
        "Model",
        ["gemini-3.5-flash-lite", "gemini-3.5-flash"],
        index=0
    )

api_key = st.secrets.get("GEMINI_API_KEY", "")
if not api_key:
    api_key = st.text_input("Enter Gemini API Key", type="password")

lessons = load_lessons()
lesson_numbers = {
    number: lesson_name
    for lesson_name in lessons
    if (number := get_lesson_number(lesson_name)) is not None
}
if not lesson_numbers:
    st.error("No numbered lessons were found in lessons.json.")
    st.stop()
lesson_numbers = dict(sorted(lesson_numbers.items()))

if "cards_data" not in st.session_state:
    st.session_state.cards_data = None
if "selected_lesson" not in st.session_state or st.session_state.selected_lesson not in lessons:
    st.session_state.selected_lesson = next(iter(lesson_numbers.values()))
if "lesson_number_picker" not in st.session_state:
    st.session_state.lesson_number_picker = get_lesson_number(st.session_state.selected_lesson)
if "shared_tag" not in st.session_state:
    st.session_state.shared_tag = get_lesson_tag(st.session_state.selected_lesson)
if "card_form_epoch" not in st.session_state:
    st.session_state.card_form_epoch = 0
if "card_form_versions" not in st.session_state:
    st.session_state.card_form_versions = {}
if "card_regeneration_baselines" not in st.session_state:
    st.session_state.card_regeneration_baselines = []


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
    st.session_state.pop("elevenlabs_preview_audio", None)

# --- STEP 1: INPUT FORM ---
st.subheader("1. Input Words & Select Lesson")
c1, c2 = st.columns([1, 2])

with c1:
    selected_lesson_number = st.select_slider(
        "Select Assimil Lesson",
        options=list(lesson_numbers),
        key="lesson_number_picker",
        help="Drag the selector or use the arrow keys to move quickly between lessons.",
    )
    selected_lesson = lesson_numbers[selected_lesson_number]
    if selected_lesson != st.session_state.selected_lesson:
        st.session_state.selected_lesson = selected_lesson
        new_lesson_tag = get_lesson_tag(selected_lesson)
        st.session_state.shared_tag = new_lesson_tag
        # The text input has its own keyed widget state. Keep it in sync here;
        # otherwise its value from the previous lesson overwrites shared_tag
        # when the editor is rendered later in this run.
        st.session_state.shared_tag_editor = new_lesson_tag

with c2:
    st.markdown("""
    **Enter target words/phrases (one per line):**<br>
    Add extra notes in parentheses `()`.<br>
    *Example:* `comment allez vous (formal way to ask how someone is)`
    """, unsafe_allow_html=True)
    user_input = st.text_area(
        "Target Words",
        height=120,
        placeholder="bonjour\ncomment ça va\ns'il vous plaît (please)\nmerci beaucoup"
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
                    lessons[selected_lesson], 
                    parsed_items
                )
                st.session_state.cards_data = cards
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
        "Edits save automatically. Open a card to edit it, then regenerate it if needed."
    )

    st.session_state.shared_tag = st.text_input(
        "Shared tag for all cards",
        value=st.session_state.shared_tag,
        help="This tag will be applied to every flashcard in the deck.",
        key="shared_tag_editor"
    )
    
    col_actions1, col_actions2 = st.columns([1, 1])
    with col_actions2:
        if st.button("🗑️ Reset All Cards", use_container_width=True):
            st.session_state.cards_data = None
            st.session_state.card_regeneration_baselines = []
            st.session_state.card_form_epoch += 1
            st.session_state.card_form_versions = {}
            st.rerun()

    cards_list = st.session_state.cards_data
    all_widget_keys = {}

    for idx, card in enumerate(cards_list):
        with st.expander(
            f"📌 Card {idx + 1}: **{card.get('fr_word', 'New Card')}** ➔ "
            f"{card.get('en_word', '')}",
            expanded=True,
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
                col_fr, col_en, col_opt = st.columns([2, 2, 1])

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

                with col_en:
                    st.text_input(
                        "English Word",
                        value=card.get("en_word", ""),
                        key=widget_keys["en_word"],
                        on_change=save_card_field,
                        args=(idx, "en_word", widget_keys["en_word"]),
                    )
                    st.text_area(
                        "English Sentence",
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
                                    st.session_state.selected_lesson,
                                    lessons[st.session_state.selected_lesson],
                                    previous_card=previous_card,
                                    current_card=submitted_card,
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
    elevenlabs_api_key = st.secrets.get("ELEVENLABS_API_KEY", "")

    if not elevenlabs_api_key:
        st.error(
            "Add ELEVENLABS_API_KEY to .streamlit/secrets.toml before exporting "
            "a deck with French audio."
        )
    else:
        try:
            elevenlabs_voices = list_elevenlabs_voices(elevenlabs_api_key)
        except requests.RequestException as error:
            elevenlabs_voices = []
            st.error(f"Could not load ElevenLabs voices: {error}")

        if not elevenlabs_voices:
            st.warning("No ElevenLabs voices are available for this API key.")
        else:
            voice_ids = [voice["voice_id"] for voice in elevenlabs_voices]
            configured_voice_id = st.secrets.get("ELEVENLABS_VOICE_ID", "")
            default_voice_index = (
                voice_ids.index(configured_voice_id)
                if configured_voice_id in voice_ids
                else 0
            )
            voice_by_id = {voice["voice_id"]: voice for voice in elevenlabs_voices}

            # Keep voice choice/preview before the export action, at half width.
            voice_col, _ = st.columns(2)
            with voice_col:
                selected_voice_id = st.selectbox(
                    "French ElevenLabs voice",
                    options=voice_ids,
                    index=default_voice_index,
                    format_func=lambda voice_id: (
                        f"{voice_by_id[voice_id].get('name', 'Unnamed voice')} "
                        f"({voice_by_id[voice_id].get('category', 'voice')})"
                    ),
                    help=(
                        "Choose a voice trained for French or with a French accent "
                        "for the most natural pronunciation."
                    ),
                    key="elevenlabs_voice_id",
                    on_change=clear_voice_preview,
                )
                if st.button("▶ Preview selected voice", use_container_width=True):
                    try:
                        st.session_state.elevenlabs_preview_audio = synthesize_french_audio(
                            elevenlabs_api_key,
                            selected_voice_id,
                            "Bonjour ! Voici un exemple de prononciation française.",
                        )
                    except requests.RequestException as error:
                        st.error(f"Could not generate voice preview: {error}")

                if preview_audio := st.session_state.get("elevenlabs_preview_audio"):
                    st.audio(preview_audio, format="audio/mpeg")

            if st.button(
                "📦 Approve All & Generate .apkg Package",
                type="primary",
                use_container_width=True,
            ):
                try:
                    with st.spinner("Generating ElevenLabs French audio and packaging deck..."):
                        st.session_state.apkg_buffer = build_anki_apkg(
                            st.session_state.cards_data,
                            st.session_state.selected_lesson,
                            elevenlabs_api_key,
                            selected_voice_id,
                            shared_tag=st.session_state.shared_tag,
                        ).getvalue()
                    st.success("Package is ready to download.")
                except requests.RequestException as error:
                    st.error(f"Could not generate ElevenLabs audio: {error}")

            if apkg_bytes := st.session_state.get("apkg_buffer"):
                lesson_num = re.sub(r'\D', '', st.session_state.selected_lesson) or "01"
                st.download_button(
                    label="📥 Download .apkg Package",
                    data=apkg_bytes,
                    file_name=f"Assimil_Lesson_{lesson_num.zfill(2)}.apkg",
                    mime="application/octet-stream",
                    use_container_width=True,
                )
