"""
Formats Scryfall card data into plain text for the thermal printer:
name, mana cost, type line, rules text, and power/toughness or loyalty.

Thermal printers can't render mana symbol glyphs, so costs are shown in
bracket notation instead, e.g. {2}{R}{R} -> [2] [R] [R].
"""
from __future__ import annotations

import textwrap
from typing import List, Optional

_UNICODE_REPLACEMENTS = {
    "\u2014": "-",   # em dash (type line separator, e.g. "Creature — Bird")
    "\u2013": "-",   # en dash
    "\u2018": "'",   # left single quote
    "\u2019": "'",   # right single quote / apostrophe
    "\u201c": '"',   # left double quote
    "\u201d": '"',   # right double quote
    "\u2022": "*",   # bullet
    "\u2212": "-",   # minus sign
}

def _sanitize(text: str) -> str:
    """Replace Unicode punctuation these printers can't render with ASCII equivalents."""
    for unicode_char, ascii_char in _UNICODE_REPLACEMENTS.items():
        text = text.replace(unicode_char, ascii_char)
    return text

def _face_field(card: dict, field: str) -> Optional[str]:
    """Fall back to the first face's field for double-faced/split cards."""
    value = card.get(field)
    if value is None and "card_faces" in card:
        value = card["card_faces"][0].get(field)
    return value


def build_card_text(card: dict, width_chars: int = 32) -> List[str]:
    """
    Return printable lines: name + mana cost, type line, a divider,
    wrapped rules text, then power/toughness or loyalty if applicable.
    """
    name = _sanitize(card.get("name", ""))
    mana_cost = _face_field(card, "mana_cost") or ""
    type_line = _sanitize(card.get("type_line") or _face_field(card, "type_line") or "")
    oracle_text = _sanitize(_face_field(card, "oracle_text") or "")
    power = card.get("power") or _face_field(card, "power")
    toughness = card.get("toughness") or _face_field(card, "toughness")
    loyalty = card.get("loyalty")
    defense = _face_field(card, "defense")

    lines: List[str] = []

    header = f"{name} {mana_cost}" if mana_cost else name
    lines.extend(textwrap.wrap(header, width=width_chars) or [header])

    if type_line:
        lines.extend(textwrap.wrap(type_line, width=width_chars))

    lines.append("-" * width_chars)

    for paragraph in oracle_text.split("\n"):
        wrapped = textwrap.wrap(paragraph, width=width_chars)
        lines.extend(wrapped if wrapped else [""])

    if power is not None and toughness is not None:
        lines.append(f"{power}/{toughness}")
    elif loyalty is not None:
        lines.append(f"Loyalty: {loyalty}")
    elif defense is not None:
        lines.append(f"Defense: {defense}")

    return lines