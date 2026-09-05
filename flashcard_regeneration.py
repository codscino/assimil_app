import json


CARD_CONTENT_FIELDS = ("fr_word", "fr_phrase", "en_word", "en_phrase")
FIELD_LABELS = {
    "fr_word": "French word",
    "fr_phrase": "French phrase",
    "en_word": "English word",
    "en_phrase": "English phrase",
}


def changed_content_fields(previous_card, current_card):
    """Return user-edited content fields in conflict-resolution order."""
    return [
        field
        for field in CARD_CONTENT_FIELDS
        if current_card.get(field, "").strip()
        != previous_card.get(field, "").strip()
    ]


def regeneration_source(previous_card, current_card):
    """Pick the highest-priority non-empty edit, or current value as fallback."""
    changed_fields = changed_content_fields(previous_card, current_card)
    for field in changed_fields:
        if current_card.get(field, "").strip():
            return field, changed_fields

    for field in CARD_CONTENT_FIELDS:
        if current_card.get(field, "").strip():
            return field, changed_fields

    return None, changed_fields


def build_regeneration_prompt(
    lesson_name, lesson_tag, lesson_data, previous_card, current_card, no_assimil_mode=False
):
    """Build a prompt based on the values submitted from a flashcard form."""
    source_field, changed_fields = regeneration_source(previous_card, current_card)
    source_description = (
        f"{FIELD_LABELS[source_field]} (`{source_field}`)"
        if source_field
        else "none (all content fields are empty)"
    )
    changed_description = ", ".join(f"`{field}`" for field in changed_fields) or "none"
    current_content = {
        field: current_card.get(field, "").strip()
        for field in (*CARD_CONTENT_FIELDS, "extra_notes")
    }

    context = (
        """Practice context: free French practice with no Assimil lesson reference.
    Invent a pleasant, useful, natural sentence suitable for a French learner."""
        if no_assimil_mode
        else f"""Lesson context:
    - Lesson: {lesson_name}
    - Tag: `{lesson_tag}`

    Reference sentences from this lesson:
    {json.dumps(lesson_data, ensure_ascii=False, indent=2)}"""
    )

    return f"""
    You are an expert French tutor refreshing one Assimil Anki flashcard.

    {context}

    Current flashcard values submitted by the user:
    {json.dumps(current_content, ensure_ascii=False, indent=2)}

    Fields edited since the previous generation: {changed_description}
    Primary source of truth: {source_description}

    CRITICAL INSTRUCTIONS:
    1. Refresh the entire card from the current values above. Do not use a previous
       value when it conflicts with a current value.
    2. Preserve current text when it is natural and consistent; rewrite fields that
       must change to make the card coherent.
    3. Resolve conflicts in this strict order: `fr_word` > `fr_phrase` > `en_word`
       > `en_phrase`. The primary source of truth identifies the highest-priority
       non-empty field explicitly edited by the user.
    4. Return a concise natural `fr_word`, its exact `en_word` translation, a natural
       `fr_phrase` containing `fr_word` (case-insensitive), and an `en_phrase` that
       translates the French phrase and contains `en_word` (case-insensitive).
    5. Preserve `extra_notes` exactly, including when it is empty.
    6. Return valid JSON matching the requested schema.
    """
