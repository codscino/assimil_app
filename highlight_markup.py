"""Explicit ``&...&`` highlight markup used by the editor and Anki cards."""

import re


HIGHLIGHT_PATTERN = re.compile(r"&([^&]*?)&")


def strip_highlight_markers(text):
    """Remove valid highlight delimiters while preserving their text."""
    return HIGHLIGHT_PATTERN.sub(lambda match: match.group(1), text or "")


def highlighted_texts(text):
    """Return the explicitly highlighted portions of a phrase."""
    return [match.group(1) for match in HIGHLIGHT_PATTERN.finditer(text or "")]


def normalize_for_containment(text):
    """Normalize case and whitespace for the phrase-containment rule."""
    return " ".join((text or "").casefold().split())


def _target_index(normalized_phrase, normalized_target):
    """Find a target occurrence without matching inside a larger word."""
    search_from = 0
    while normalized_target:
        match_index = normalized_phrase.find(normalized_target, search_from)
        if match_index == -1:
            return -1
        match_end = match_index + len(normalized_target)
        starts_inside_word = (
            normalized_target[0].isalnum()
            and match_index > 0
            and normalized_phrase[match_index - 1].isalnum()
        )
        ends_inside_word = (
            normalized_target[-1].isalnum()
            and match_end < len(normalized_phrase)
            and normalized_phrase[match_end].isalnum()
        )
        if not starts_inside_word and not ends_inside_word:
            return match_index
        search_from = match_index + 1
    return -1


def phrase_contains_target(phrase, target):
    """Check that target occurs in phrase, ignoring case and whitespace."""
    normalized_target = normalize_for_containment(target)
    normalized_phrase = normalize_for_containment(strip_highlight_markers(phrase))
    return _target_index(normalized_phrase, normalized_target) != -1


def marker_matches_target(phrase, target):
    """Check that one well-formed marker pair contains the target text."""
    phrase = phrase or ""
    normalized_target = normalize_for_containment(target)
    return phrase.count("&") == 2 and bool(normalized_target) and any(
        normalize_for_containment(marked_text) == normalized_target
        for marked_text in highlighted_texts(phrase)
    )


def _normalized_with_positions(text):
    """Case/space-fold text and retain offsets for inserting a marker."""
    normalized = []
    positions = []
    for index, character in enumerate(text or ""):
        if character.isspace():
            if normalized and normalized[-1] != " ":
                normalized.append(" ")
                positions.append((index, index + 1))
            continue
        for folded_character in character.casefold():
            normalized.append(folded_character)
            positions.append((index, index + 1))
    if normalized and normalized[-1] == " ":
        normalized.pop()
        positions.pop()
    return "".join(normalized), positions


def ensure_target_marker(phrase, target):
    """Add one marker to legacy/unmarked text when the target can be found.

    Existing markup is never guessed over or silently rewritten; callers can
    validate it and ask the user to correct a mismatched explicit selection.
    """
    phrase = phrase or ""

    # A user may delete only one side of an existing marker. Remove incomplete
    # or surplus delimiters before adding a fresh pair; otherwise text such as
    # ``Buongiorno&`` would become ``&Buongiorno&&``.
    if phrase.count("&") not in (0, 2):
        phrase = phrase.replace("&", "")

    if highlighted_texts(phrase) or not target:
        return phrase

    normalized_phrase, positions = _normalized_with_positions(phrase)
    normalized_target = normalize_for_containment(target)
    match_index = _target_index(normalized_phrase, normalized_target)
    if match_index == -1 or not normalized_target:
        return phrase

    start = positions[match_index][0]
    end = positions[match_index + len(normalized_target) - 1][1]
    return f"{phrase[:start]}&{phrase[start:end]}&{phrase[end:]}"


def repair_target_marker(phrase, target):
    """Move an incorrect marker when the target already exists in the phrase."""
    if phrase_contains_target(phrase, target) and not marker_matches_target(phrase, target):
        return ensure_target_marker(strip_highlight_markers(phrase), target)
    return phrase or ""


def phrase_highlight_error(phrase, target, phrase_label="phrase", target_label="word"):
    """Return a user-facing validation error, or an empty string."""
    if not phrase_contains_target(phrase, target):
        return (
            f"The {phrase_label} must contain the {target_label} "
            "(case and whitespace are ignored)."
        )
    if not marker_matches_target(phrase, target):
        return (
            f"Wrap the {target_label} in the {phrase_label} between ampersands "
            "(&word&)."
        )
    return ""
