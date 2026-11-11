"""Parse target words, supplied marked sentences, and parenthetical notes."""

import re

from highlight_markup import HIGHLIGHT_PATTERN


def parse_input_line(line):
    match = re.match(r'^(.*?)\s*\((.*)\)\s*$', line)
    if match:
        return match.group(1).strip(), match.group(2).strip()

    word, separator, notes = line.partition("(")
    return word.strip(), notes.rstrip(") ").strip() if separator else ""


def input_item(text, notes):
    item = {"raw_word": text, "user_notes": notes}
    matches = list(HIGHLIGHT_PATTERN.finditer(text))
    if text.count("&") == 2 and len(matches) == 1 and matches[0].group(1).strip():
        item["raw_word"] = matches[0].group(1).strip()
        item["fr_phrase"] = text
    return item


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
                items.append(input_item(word, notes))
            pending_line = ""

        pending_line = f"{pending_line} {line}".strip()
        if pending_line.count("(") > pending_line.count(")"):
            continue

        word, notes = parse_input_line(pending_line)
        if word:
            items.append(input_item(word, notes))
        pending_line = ""

    if pending_line:
        word, notes = parse_input_line(pending_line)
        if word:
            items.append(input_item(word, notes))

    return items
