import streamlit as st
import genanki
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import json
import re
import io

from flashcard_regeneration import build_regeneration_prompt

# -----------------------------------------------------------------------------
# 1. ANKI MODEL DEFINITION (Raw Strings)
# -----------------------------------------------------------------------------
MODEL_ID = 1607392319

FRONT_FR2EN = r"""
<div id="fr-target-data" style="display:none;">{{fr_word}}</div>

{{#fr_phrase}}
<div id="fr-phrase">{{fr_phrase}}</div>
{{/fr_phrase}}
{{^fr_phrase}}
<div id="fr-word">{{fr_word}}</div>
{{/fr_phrase}}

<br><br>
{{type:en_word}}

<script>
(function() {
    var phraseDiv = document.getElementById("fr-phrase");
    var targetData = document.getElementById("fr-target-data");
    if (phraseDiv && targetData) {
        var word = targetData.textContent.trim();
        if (word) { 
            var escapedWord = word.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
            var regex = new RegExp('(' + escapedWord + ')', "gi");
            phraseDiv.innerHTML = phraseDiv.innerHTML.replace(regex, "<span class='highlight'>$1</span>");
        }
    }
})();
</script>
"""

BACK_FR2EN = r"""
<div id="fr-target-back-data" style="display:none;">{{fr_word}}</div>

{{#fr_phrase}}
<div id="fr-phrase-back">{{fr_phrase}}</div>
{{/fr_phrase}}
{{^fr_phrase}}
<div id="fr-word-back">{{fr_word}}</div>
{{/fr_phrase}}

<hr id="answer">
{{type:en_word}}

<div style="font-size: 1.1em; color: #555; margin-top: 8px;">
{{en_phrase}}
</div>

{{#extra_notes}}
<div class="notes">
note: {{extra_notes}}
</div>
{{/extra_notes}}

{{#fr_phrase}}
  {{tts fr_FR:fr_phrase}}
{{/fr_phrase}}
{{^fr_phrase}}
  {{tts fr_FR:fr_word}}
{{/fr_phrase}}

<script>
(function() {
    var phraseDiv = document.getElementById("fr-phrase-back");
    var targetData = document.getElementById("fr-target-back-data");
    if (phraseDiv && targetData) {
        var word = targetData.textContent.trim();
        if (word) {
            var escapedWord = word.replace(/[-\/\\^$*+?.()|[\]{}]/g, '\\$&');
            var regex = new RegExp('(' + escapedWord + ')', "gi");
            phraseDiv.innerHTML = phraseDiv.innerHTML.replace(regex, "<span class='highlight'>$1</span>");
        }
    }
})();
</script>
"""

FRONT_EN2FR = r"""
{{#en_phrase}}
<div id="en-phrase">{{en_phrase}}</div>
{{/en_phrase}}
{{^en_phrase}}
<div id="en-word">{{en_word}}</div>
{{/en_phrase}}

{{#extra_notes}}
<div class="notes">
note: {{extra_notes}}
</div>
{{/extra_notes}}

<br><br>
{{type:fr_word}}
"""

BACK_EN2FR = r"""
{{#en_phrase}}
<div id="en-phrase-back">{{en_phrase}}</div>
{{/en_phrase}}
{{^en_phrase}}
<div id="en-word-back">{{en_word}}</div>
{{/en_phrase}}

{{#extra_notes}}
<div class="notes">
note: {{extra_notes}}
</div>
{{/extra_notes}}

<hr id="answer">
{{type:fr_word}}

<div style="font-size: 1.1em; color: #555; margin-top: 8px;">
{{fr_phrase}}
</div>

{{#fr_phrase}}
  {{tts fr_FR:fr_phrase}}
{{/fr_phrase}}
{{^fr_phrase}}
  {{tts fr_FR:fr_word}}
{{/fr_phrase}}
"""

CARD_STYLE = r"""
.card {
  font-family: Arial, sans-serif;
  font-size: 20px;
  text-align: center;
  color: #222;
  background-color: #ffffff;
}
.highlight {
  background-color: #ffe066;
  color: #000;
  font-weight: bold;
  padding: 0 4px;
  border-radius: 3px;
}
.notes {
  font-size: 0.85em;
  color: #666;
  margin-top: 12px;
  font-style: italic;
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
    ],
    templates=[
        {'name': 'FR -> EN', 'qfmt': FRONT_FR2EN, 'afmt': BACK_FR2EN},
        {'name': 'EN -> FR', 'qfmt': FRONT_EN2FR, 'afmt': BACK_EN2FR},
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

@st.cache_data
def load_lessons():
    with open("lessons.json", "r", encoding="utf-8") as f:
        return json.load(f)

def get_lesson_tag(lesson_name):
    lesson_num = re.sub(r'\D', '', lesson_name) or "01"
    lesson_num_padded = lesson_num.zfill(2)
    return f"assimil_lesson_{lesson_num_padded}"

def parse_user_input(raw_text):
    items = []
    for line in raw_text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        match = re.search(r'^(.*?)(?:\((.*?)\))?$', line)
        if match:
            word = match.group(1).strip()
            notes = match.group(2).strip() if match.group(2) else ""
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
    1. Clean the French target into a short, natural form for `fr_word`.
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

def build_anki_apkg(cards_data, lesson_name, shared_tag=None):
    lesson_num = re.sub(r'\D', '', lesson_name) or "01"
    lesson_num_padded = lesson_num.zfill(2)
    deck_id = 2059400000 + int(lesson_num)

    tag_name = (shared_tag or get_lesson_tag(lesson_name)).strip()
    if not tag_name:
        tag_name = f"assimil_lesson_{lesson_num_padded}"
    
    deck = genanki.Deck(deck_id, f"Assimil French::Lesson_{lesson_num_padded}")
    
    for item in cards_data:
        note = genanki.Note(
            model=anki_model,
            fields=[
                item.get("fr_word", ""),
                item.get("fr_phrase", ""),
                item.get("en_word", ""),
                item.get("en_phrase", ""),
                item.get("extra_notes", "")
            ],
            tags=[tag_name]
        )
        deck.add_note(note)
    
    buffer = io.BytesIO()
    genanki.Package(deck).write_to_file(buffer)
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

if "cards_data" not in st.session_state:
    st.session_state.cards_data = None
if "selected_lesson" not in st.session_state:
    st.session_state.selected_lesson = list(lessons.keys())[0]
if "shared_tag" not in st.session_state:
    st.session_state.shared_tag = get_lesson_tag(st.session_state.selected_lesson)
if "card_form_epoch" not in st.session_state:
    st.session_state.card_form_epoch = 0
if "card_form_versions" not in st.session_state:
    st.session_state.card_form_versions = {}

# --- STEP 1: INPUT FORM ---
st.subheader("1. Input Words & Select Lesson")
c1, c2 = st.columns([1, 2])

with c1:
    selected_lesson = st.selectbox(
        "Select Assimil Lesson", 
        list(lessons.keys()),
        index=list(lessons.keys()).index(st.session_state.selected_lesson)
    )
    if selected_lesson != st.session_state.selected_lesson:
        st.session_state.selected_lesson = selected_lesson
        st.session_state.shared_tag = get_lesson_tag(selected_lesson)

with c2:
    st.markdown("""
    **Enter target words/phrases (one per line):**  
    Add extra notes in parentheses `()`.  
    *Example:* `comment allez vous (formal way to ask how someone is)`
    """)
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
                st.session_state.card_form_epoch += 1
                st.session_state.card_form_versions = {}
                st.success(f"Generated {len(cards)} cards! Review them below.")
                st.rerun()
            except Exception as e:
                st.error(f"Error: {str(e)}")

# --- STEP 2: REVIEW & EDIT SECTION ---
if st.session_state.cards_data:
    st.divider()
    st.subheader("2. Review, Edit & Regenerate Cards")
    st.info(
        "Edit a card, then click Save edits or Regenerate. Regenerate submits the "
        "visible values directly, so you do not need to press Enter first."
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
            st.session_state.card_form_epoch += 1
            st.session_state.card_form_versions = {}
            st.rerun()

    cards_list = st.session_state.cards_data

    for idx, card in enumerate(cards_list):
        with st.expander(f"📌 Card {idx + 1}: **{card.get('fr_word', 'New Card')}** ➔ {card.get('en_word', '')}", expanded=True):
            form_version = st.session_state.card_form_versions.get(idx, 0)
            widget_prefix = f"card_{st.session_state.card_form_epoch}_{idx}_{form_version}"
            widget_keys = {
                "fr_word": f"{widget_prefix}_fr_word",
                "fr_phrase": f"{widget_prefix}_fr_phrase",
                "en_word": f"{widget_prefix}_en_word",
                "en_phrase": f"{widget_prefix}_en_phrase",
                "extra_notes": f"{widget_prefix}_notes",
            }
            with st.form(key=f"{widget_prefix}_form", border=False):
                col_fr, col_en, col_opt = st.columns([2, 2, 1])

                with col_fr:
                    fr_word_val = st.text_input(
                        "French Word",
                        value=card.get("fr_word", ""),
                        key=widget_keys["fr_word"],
                    )
                    fr_phrase_val = st.text_area(
                        "French Sentence",
                        value=card.get("fr_phrase", ""),
                        key=widget_keys["fr_phrase"],
                        height=80,
                    )

                with col_en:
                    en_word_val = st.text_input(
                        "English Word",
                        value=card.get("en_word", ""),
                        key=widget_keys["en_word"],
                    )
                    en_phrase_val = st.text_area(
                        "English Sentence",
                        value=card.get("en_phrase", ""),
                        key=widget_keys["en_phrase"],
                        height=80,
                    )

                with col_opt:
                    notes_val = st.text_input(
                        "Notes",
                        value=card.get("extra_notes", ""),
                        key=widget_keys["extra_notes"],
                    )
                    save_clicked = st.form_submit_button("💾 Save edits", use_container_width=True)
                    regenerate_clicked = st.form_submit_button(
                        "🔄 Regenerate", use_container_width=True
                    )

                submitted_card = {
                    "fr_word": fr_word_val,
                    "fr_phrase": fr_phrase_val,
                    "en_word": en_word_val,
                    "en_phrase": en_phrase_val,
                    "extra_notes": notes_val,
                }

                if save_clicked:
                    st.session_state.cards_data[idx] = submitted_card
                    st.toast(f"Card {idx + 1} saved!", icon="💾")
                    st.rerun()

                if regenerate_clicked:
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
                                    previous_card=card,
                                    current_card=submitted_card,
                                )
                                st.session_state.cards_data[idx] = updated_card
                                st.session_state.card_form_versions[idx] = form_version + 1
                                st.toast(f"Card {idx + 1} updated!", icon="🎉")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to regenerate card: {str(e)}")

    # --- STEP 3: APPROVE & DOWNLOAD ---
    st.divider()
    st.subheader("3. Export Deck")
    
    apkg_buffer = build_anki_apkg(
        st.session_state.cards_data,
        st.session_state.selected_lesson,
        shared_tag=st.session_state.shared_tag,
    )
    lesson_num = re.sub(r'\D', '', st.session_state.selected_lesson) or "01"
    
    st.download_button(
        label="📥 Approve All & Download .apkg Package",
        data=apkg_buffer,
        file_name=f"Assimil_Lesson_{lesson_num.zfill(2)}.apkg",
        mime="application/octet-stream",
        type="primary",
        use_container_width=True
    )
