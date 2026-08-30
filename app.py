import streamlit as st
import genanki
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import json
import re
import io

# -----------------------------------------------------------------------------
# 1. ANKI MODEL DEFINITION (Using Raw Strings r"""...""")
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
    fr_phrase: str = Field(description="Natural French sentence featuring target word")
    en_word: str = Field(description="Direct English translation of fr_word")
    en_phrase: str = Field(description="English translation of fr_phrase")
    extra_notes: str = Field(description="User notes combined with brief grammar tips if useful")

@st.cache_data
def load_lessons():
    with open("lessons.json", "r", encoding="utf-8") as f:
        return json.load(f)

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

def generate_flashcards_with_gemini(api_key, lesson_name, lesson_data, parsed_items):
    client = genai.Client(api_key=api_key)
    
    prompt = f"""
    You are an expert French tutor building Anki flashcards based on the 'Assimil French with Ease' methodology.
    
    Reference sentences from {lesson_name}:
    {json.dumps(lesson_data, ensure_ascii=False, indent=2)}
    
    Target words/phrases provided by user:
    {json.dumps(parsed_items, ensure_ascii=False, indent=2)}
    
    For each item in the user input:
    - Return fr_word, fr_phrase, en_word, en_phrase, and extra_notes.
    """

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=list[FlashcardItem]
        )
    )
    
    return json.loads(response.text)

# -----------------------------------------------------------------------------
# 3. STREAMLIT APP UI
# -----------------------------------------------------------------------------
st.set_page_config(page_title="Assimil Anki Generator", page_icon="🇫🇷", layout="centered")

st.title("🇫🇷 Assimil French Anki Generator")

api_key = st.secrets.get("GEMINI_API_KEY", "")
if not api_key:
    api_key = st.text_input("Enter Gemini API Key", type="password")

lessons = load_lessons()
selected_lesson = st.selectbox("Select Assimil Lesson", list(lessons.keys()))

st.markdown("""
**Enter target words/phrases (one per line):**  
Optional notes can be added in parentheses `()`.  
*Example:*  
`salle de bains (bathroom)`  
`s'il vous plaît`  
`près de (near to)`
""")

user_input = st.text_area("Target Words / Expressions", height=150)

if st.button("Generate Anki Deck", type="primary"):
    if not api_key:
        st.error("Please provide a Gemini API Key.")
    elif not user_input.strip():
        st.warning("Please input at least one word.")
    else:
        parsed_items = parse_user_input(user_input)
        
        with st.spinner("Generating flashcards with Gemini..."):
            try:
                cards_data = generate_flashcards_with_gemini(
                    api_key, 
                    selected_lesson, 
                    lessons[selected_lesson], 
                    parsed_items
                )
                
                lesson_num = re.sub(r'\D', '', selected_lesson) or "01"
                deck_id = 2059400000 + int(lesson_num)
                tag_name = f"assimil_lesson_{lesson_num.zfill(2)}"
                
                deck = genanki.Deck(deck_id, f"Assimil French::Lesson_{lesson_num.zfill(2)}")
                
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
                
                st.success(f"Generated {len(cards_data)} flashcards successfully!")
                
                st.download_button(
                    label="📥 Download .apkg Package",
                    data=buffer,
                    file_name=f"Assimil_Lesson_{lesson_num.zfill(2)}.apkg",
                    mime="application/octet-stream"
                )
                
            except Exception as e:
                st.error(f"Error generating deck: {str(e)}")