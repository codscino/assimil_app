import streamlit as st
import genanki
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
import json
import re
import io

# -----------------------------------------------------------------------------
# 1. ANKI MODEL DEFINITION
# -----------------------------------------------------------------------------
MODEL_ID = 1607392319

FRONT_FR2EN = """
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
            var escapedWord = word.replace(/[-\/\\\\^$*+?.()|[\\]{}]/g, '\\\\$&');
            var regex = new RegExp('(' + escapedWord + ')', "gi");
            phraseDiv.innerHTML = phraseDiv.innerHTML.replace(regex, "<span class='highlight'>$1</span>");
        }
    }
})();
</script>
"""

BACK_FR2EN = """
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
            var escapedWord = word.replace(/[-\/\\\\^$*+?.()|[\\]{}]/g, '\\\\$&');
            var regex = new RegExp('(' + escapedWord + ')', "gi");
            phraseDiv.innerHTML = phraseDiv.innerHTML.replace(regex, "<span class='highlight'>$1</span>");
        }
    }
})();
</script>
"""

FRONT_EN2FR = """
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

BACK_EN2FR = """
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

CARD_STYLE = """
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