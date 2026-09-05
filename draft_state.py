"""Serialization and validation for the browser-local flashcard draft."""

import json


DRAFT_VERSION = 1
MAX_DRAFT_BYTES = 2_000_000
CARD_STRING_FIELDS = (
    "fr_word",
    "fr_phrase",
    "en_word",
    "en_phrase",
    "extra_notes",
    "raw_word",
    "user_notes",
)


def _clean_card(value):
    if not isinstance(value, dict):
        return None
    return {
        field: field_value
        for field in CARD_STRING_FIELDS
        if isinstance((field_value := value.get(field)), str)
    }


def _clean_cards(value):
    if not isinstance(value, list):
        return None
    cards = [_clean_card(card) for card in value]
    if not cards or any(card is None for card in cards):
        return None
    return cards


def build_draft_json(
    cards_data,
    card_regeneration_baselines,
    target_language_code,
    selected_lesson,
    shared_tag,
    no_assimil_mode,
):
    """Return a compact JSON snapshot suitable for browser localStorage."""
    cards = _clean_cards(cards_data)
    if cards is None:
        return None

    baselines = _clean_cards(card_regeneration_baselines)
    if baselines is None or len(baselines) != len(cards):
        baselines = [dict(card) for card in cards]

    return json.dumps(
        {
            "version": DRAFT_VERSION,
            "cards": cards,
            "baselines": baselines,
            "target_language": target_language_code,
            "selected_lesson": selected_lesson,
            "shared_tag": shared_tag,
            "no_assimil_mode": bool(no_assimil_mode),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def restore_draft_json(raw_value, target_language_code, valid_lessons):
    """Validate an untrusted localStorage value and return restorable state."""
    if not isinstance(raw_value, str) or not raw_value:
        return None
    if len(raw_value.encode("utf-8")) > MAX_DRAFT_BYTES:
        return None

    try:
        draft = json.loads(raw_value)
    except (TypeError, ValueError):
        return None

    if not isinstance(draft, dict) or draft.get("version") != DRAFT_VERSION:
        return None
    if draft.get("target_language") != target_language_code:
        return None

    cards = _clean_cards(draft.get("cards"))
    baselines = _clean_cards(draft.get("baselines"))
    if cards is None:
        return None
    if baselines is None or len(baselines) != len(cards):
        baselines = [dict(card) for card in cards]

    no_assimil_mode = draft.get("no_assimil_mode") is True
    selected_lesson = draft.get("selected_lesson")
    if no_assimil_mode:
        selected_lesson = "French Practice"
    elif selected_lesson not in valid_lessons:
        return None

    shared_tag = draft.get("shared_tag")
    if not isinstance(shared_tag, str):
        shared_tag = (
            "" if no_assimil_mode else "assimil_lesson_01"
        )

    return {
        "cards_data": cards,
        "card_regeneration_baselines": baselines,
        "cards_target_language": target_language_code,
        "selected_lesson": selected_lesson,
        "shared_tag": shared_tag,
        "no_assimil_mode": no_assimil_mode,
    }
