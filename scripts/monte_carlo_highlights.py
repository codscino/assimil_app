#!/usr/bin/env python3
"""Generate and stress-test flashcard highlight validation without audio calls."""

import argparse
import copy
import json
import random
import re
import time
import tomllib
from collections import Counter
from pathlib import Path

from google import genai
from google.genai import types

from highlight_markup import (
    ensure_target_marker,
    phrase_highlight_error,
    phrase_contains_target,
    repair_target_marker,
    strip_highlight_markers,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = Path("/tmp/assimil_highlight_monte_carlo.json")
FIELDS = ("fr_word", "fr_phrase", "en_word", "en_phrase", "extra_notes")


def gemini_json(api_key, model, prompt, attempts=4, temperature=0.8):
    """Call Gemini through the same SDK authentication path as the app."""
    client = genai.Client(api_key=api_key)

    for attempt in range(attempts):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=temperature,
                ),
            )
            return json.loads(response.text)
        except Exception as error:
            if attempt == attempts - 1:
                raise RuntimeError(f"Gemini request failed: {error}") from error
        time.sleep(2**attempt)
    raise RuntimeError("Gemini request failed after retries")


def ensure_card_markers(card):
    card["fr_phrase"] = ensure_target_marker(card["fr_phrase"], card["fr_word"])
    card["en_phrase"] = ensure_target_marker(card["en_phrase"], card["en_word"])
    return card


def card_errors(card):
    errors = []
    french_error = phrase_highlight_error(
        card.get("fr_phrase", ""),
        card.get("fr_word", ""),
        "French sentence",
        "French word",
    )
    target_error = phrase_highlight_error(
        card.get("en_phrase", ""),
        card.get("en_word", ""),
        "English sentence",
        "English word",
    )
    if french_error:
        errors.append(french_error)
    if target_error:
        errors.append(target_error)
    return errors


def validate_cards(cards):
    return {index: card_errors(card) for index, card in enumerate(cards) if card_errors(card)}


def generate_cards(api_key, model, count):
    cards = []
    edge_cases = (
        "Include these early targets where natural: le, la, un, à, de, sur la table, "
        "l'été, aujourd'hui, est-ce que, rendez-vous."
    )
    for batch_start in range(0, count, 20):
        batch_size = min(20, count - batch_start)
        prompt = f"""
        Create exactly {batch_size} varied French-to-English study flashcards as a JSON array.
        {edge_cases if batch_start == 0 else "Choose diverse everyday French targets."}

        Each object must have exactly these string fields:
        fr_word, fr_phrase, en_word, en_phrase, extra_notes.

        The French phrase must naturally contain the exact fr_word, ignoring case and
        repeated/surrounding whitespace. Wrap only the intended occurrence in ampersands,
        for example `&Le& chat est sur la table`. Do the same for en_word in en_phrase.
        Use exactly one marker pair per phrase and do not use ampersands elsewhere.
        Vary target length, capitalization, apostrophes, accents, punctuation, and sentence
        position. Keep extra_notes empty. Return JSON only.
        """
        batch = gemini_json(api_key, model, prompt)
        if not isinstance(batch, list) or len(batch) != batch_size:
            raise RuntimeError(
                f"Generation batch returned {len(batch) if isinstance(batch, list) else 'non-list'} "
                f"cards instead of {batch_size}"
            )
        for card in batch:
            if not isinstance(card, dict) or not all(isinstance(card.get(f), str) for f in FIELDS):
                raise RuntimeError("Gemini returned a card with missing or non-string fields")
            cards.append({field: card[field] for field in FIELDS})
    return cards


def wrong_marker(phrase, target):
    plain = strip_highlight_markers(phrase)
    target_folded = " ".join(target.casefold().split())
    for match in re.finditer(r"[^\W\d_]+(?:['’][^\W\d_]+)?", plain, re.UNICODE):
        candidate = match.group(0)
        if " ".join(candidate.casefold().split()) != target_folded:
            return f"{plain[:match.start()]}&{candidate}&{plain[match.end():]}"
    return f"{plain} &incorrect&"


def inject_edits(cards, seed):
    rng = random.Random(seed)
    edited = copy.deepcopy(cards)
    indexes = list(range(len(edited)))
    rng.shuffle(indexes)
    edits_per_category = max(1, len(edited) // 10)
    categories = (
        ["remove_fr_marker"] * edits_per_category
        + ["wrong_fr_marker"] * edits_per_category
        + ["remove_fr_target"] * edits_per_category
        + ["change_fr_word"] * edits_per_category
        + ["remove_en_marker"] * edits_per_category
        + ["case_space_only"] * edits_per_category
    )
    applied = {}
    for index, category in zip(indexes, categories):
        card = edited[index]
        applied[index] = category
        if category == "remove_fr_marker":
            card["fr_phrase"] = strip_highlight_markers(card["fr_phrase"])
        elif category == "wrong_fr_marker":
            card["fr_phrase"] = wrong_marker(card["fr_phrase"], card["fr_word"])
        elif category == "remove_fr_target":
            card["fr_phrase"] = re.sub(
                r"&[^&]*?&", "quelque chose", card["fr_phrase"], count=1
            )
        elif category == "change_fr_word":
            card["fr_word"] = f"{card['fr_word'].strip()} introuvable"
        elif category == "remove_en_marker":
            card["en_phrase"] = strip_highlight_markers(card["en_phrase"])
        elif category == "case_space_only":
            card["fr_word"] = f"  {card['fr_word'].swapcase()}  "
    return edited, applied


def repair_invalid_cards(api_key, model, cards, invalid):
    repaired = copy.deepcopy(cards)
    for card in repaired:
        card["fr_phrase"] = repair_target_marker(card["fr_phrase"], card["fr_word"])
        card["en_phrase"] = repair_target_marker(card["en_phrase"], card["en_word"])
    invalid = validate_cards(repaired)
    invalid_items = [
        {"card_index": index, "errors": errors, "card": repaired[index]}
        for index, errors in invalid.items()
    ]
    for batch_start in range(0, len(invalid_items), 15):
        batch = invalid_items[batch_start : batch_start + 15]
        prompt = f"""
        Every supplied card is invalid. Repair these flashcards and return a JSON array
        with exactly one full card for each input item, in the same order. You MUST rewrite
        each phrase named in its errors; never return an invalid phrase unchanged:
        {json.dumps(batch, ensure_ascii=False, indent=2)}

        Keep fr_word, en_word, and extra_notes exactly unchanged. Rewrite only fr_phrase
        and/or en_phrase. Each phrase must naturally contain its corresponding exact word,
        ignoring case and repeated/surrounding whitespace. Wrap exactly that occurrence in
        ampersands, e.g. `&Le& chat est sur la table`. Use exactly one marker pair and no
        other ampersands. Return JSON only.
        """
        proposals = gemini_json(api_key, model, prompt, temperature=0.2)
        if not isinstance(proposals, list) or len(proposals) != len(batch):
            raise RuntimeError("Repair returned the wrong number of cards")
        for item, proposal in zip(batch, proposals):
            if not isinstance(proposal, dict):
                raise RuntimeError("Repair returned a non-object card")
            card = repaired[item["card_index"]]
            for phrase_field in ("fr_phrase", "en_phrase"):
                if isinstance(proposal.get(phrase_field), str):
                    card[phrase_field] = proposal[phrase_field]
            ensure_card_markers(card)
    return repaired


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260909)
    parser.add_argument("--model", default="gemini-3.5-flash-lite")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--auth-check",
        action="store_true",
        help="Make one minimal Gemini request, print success, and exit.",
    )
    args = parser.parse_args()

    secrets = tomllib.loads((ROOT / ".streamlit" / "secrets.toml").read_text())
    api_key = secrets.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    if args.auth_check:
        result = gemini_json(
            api_key,
            args.model,
            'Return exactly this JSON object: {"authenticated": true}',
            attempts=1,
            temperature=0,
        )
        if result != {"authenticated": True}:
            raise RuntimeError("Gemini authenticated but returned an unexpected response")
        print(json.dumps({"authenticated": True, "model": args.model}))
        return

    cards = generate_cards(api_key, args.model, args.count)
    for card in cards:
        ensure_card_markers(card)
    initial_errors = validate_cards(cards)
    if initial_errors:
        cards = repair_invalid_cards(api_key, args.model, cards, initial_errors)
    generation_errors_after_repair = validate_cards(cards)

    edited_cards, applied_edits = inject_edits(cards, args.seed)
    for card in edited_cards:
        ensure_card_markers(card)
    errors_after_local_pass = validate_cards(edited_cards)

    marker_repaired_cards = copy.deepcopy(edited_cards)
    for card in marker_repaired_cards:
        card["fr_phrase"] = repair_target_marker(card["fr_phrase"], card["fr_word"])
        card["en_phrase"] = repair_target_marker(card["en_phrase"], card["en_word"])
    errors_before_gemini = validate_cards(marker_repaired_cards)
    repaired_cards = repair_invalid_cards(
        api_key, args.model, marker_repaired_cards, errors_before_gemini
    ) if errors_before_gemini else marker_repaired_cards
    final_errors = validate_cards(repaired_cards)

    category_counts = Counter(applied_edits.values())
    locally_resolved = Counter()
    marker_relocated = Counter()
    gemini_required = Counter()
    final_failures = Counter()
    for index, category in applied_edits.items():
        if index in errors_after_local_pass:
            if index in errors_before_gemini:
                gemini_required[category] += 1
            else:
                marker_relocated[category] += 1
        else:
            locally_resolved[category] += 1
        if index in final_errors:
            final_failures[category] += 1

    report = {
        "model": args.model,
        "seed": args.seed,
        "audio_calls": 0,
        "generated_cards": len(cards),
        "generation_errors_before_repair": len(initial_errors),
        "generation_errors_after_repair": len(generation_errors_after_repair),
        "simulated_edits": dict(category_counts),
        "locally_resolved_by_category": dict(locally_resolved),
        "wrong_markers_relocated_locally": dict(marker_relocated),
        "sent_to_gemini_by_category": dict(gemini_required),
        "errors_after_initial_local_pass": len(errors_after_local_pass),
        "errors_before_gemini_repair": len(errors_before_gemini),
        "errors_after_gemini_repair": len(final_errors),
        "final_failures_by_category": dict(final_failures),
        "final_failure_examples": [
            {
                "card_index": index,
                "edit": applied_edits.get(index, "generation"),
                "errors": errors,
                "card": repaired_cards[index],
            }
            for index, errors in list(final_errors.items())[:10]
        ],
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
