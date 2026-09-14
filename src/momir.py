"""
MoJhoJace Basic: a continuous, mode-switching random-card printer.

Modes:
  Momir   - pick a mana value, print (art + text) a random creature of
            that value.
  MoSto   - Momir, then also print (art + text) a random equipment/aura/
            either at or below that mana value.
  Jhoira  - pick instant or sorcery, print (text only) three DISTINCT
            random cards of that type.
  Jace    - pick card type(s) + a mana value, print (art + text) one
            random matching card.
  Tibalt  - pick any card type(s), print (text only) three DISTINCT
            random matching cards.

Once you pick a mode, it stays active and keeps re-prompting for another
round (another mana value, another Jhoira pick, etc.) rather than
dropping back to the mode menu after each print. Two commands work at
any prompt, in any mode:

  menu   (or back/m/b)   - stop this mode, return to the mode-select menu
  quit   (or exit)       - end the program immediately

Momir/MoSto/Jace print a single card at a time, so they get full art +
text. Jhoira/Tibalt print three candidates at once, so they're text-only
-- this also means they skip the printer's image-triggered power-cycle/
reconnect wait entirely, since that's specifically caused by the image
transfer's current draw.
"""
from __future__ import annotations

import argparse
import sys
import os

from imaging import PRINTER_WIDTH_PRESETS
from printer import PrinterConfigError, get_printer
from printjob import PrintJobError, print_card, print_text_only
from scryfall import CardNotFoundError, fetch_random_card

MOMIR_VALUES = list(range(0, 14)) + [15, 16]  # no creature exists at CMC 14
CARD_TYPES = ["Artifact", "Battle", "Creature", "Enchantment", "Instant", "Land", "Planeswalker", "Sorcery"]

# Applied to every query: real paper cards only, no Un-set/joke cards.
BASE_FILTER = "game:paper -is:funny"

# Only for pure instant/sorcery searches (Jhoira): a creature's attached
# Adventure or Prepared spell shares its type line, so a plain instant/
# sorcery search can scoop it up even though the "real" card is the
# creature. This should NOT apply to Momir/MoSto (which want exactly
# this kind of creature) or to Jace/Tibalt.
SPELL_ONLY_FILTER = "-layout:adventure -layout:prepare -keyword:aftermath"

MENU_WORDS = ("menu", "back", "m", "b")
QUIT_WORDS = ("quit", "exit", "q")

class QuitProgram(Exception):
    """Raised from a prompt when the user types quit/exit, to unwind out of a mode immediately."""

def _check_control_words(raw: str) -> bool:
    """Returns True if raw means 'go back to the menu'. Raises QuitProgram if raw means 'quit'."""
    low = raw.strip().lower()
    if low in QUIT_WORDS:
        os.system("cls")
        raise QuitProgram()
    elif low in MENU_WORDS:
        intro()
        return low

# ---------------------------------------------------------------- queries

def _is_all_noncreature(card: dict) -> bool:
    """
    True only if NO face of this card is a Creature.

    Handles transform/flip/mdfc cards where one face matches the
    requested type (e.g. Land, Enchantment) but another face is
    secretly a Creature -- Scryfall's -t:creature doesn't reliably
    exclude these (e.g. Aclazotz, Deepest Betrayal // Temple of the
    Dead; Rune-Tail, Kitsune Ascendant // Rune-Tail's Essence).
    """
    faces = card.get("card_faces") or [card]
    return not any("Creature" in (face.get("type_line") or "") for face in faces)

def momir_query(cmc: int) -> str:
    return f"t:creature cmc={cmc} {BASE_FILTER}"

def mosto_query(cmc_max: int, kinds: list) -> str:
    type_clause = " or ".join(f"t:{k}" for k in kinds)
    return f"({type_clause}) cmc<={cmc_max} {BASE_FILTER}"

def jhoira_query(spell_type: str) -> str:
    return f"t:{spell_type} {BASE_FILTER} {SPELL_ONLY_FILTER}"

def type_query(types: list, cmc_clause: str = "") -> str:
    """Shared type-search builder for Jace/Tibalt, with creature-aware exclusions."""
    type_clause = " or ".join(f"t:{t}" for t in types)
    query = f"({type_clause}) {cmc_clause} {BASE_FILTER}".strip()
    if "Creature" not in types:
        query += " -t:Creature"
        if any(t in ("Instant", "Sorcery") for t in types):
            query += f" {SPELL_ONLY_FILTER}"
    return query

def jace_query(cmc: int, types: list) -> str:
    return type_query(types, cmc_clause=f"cmc={cmc}")

def tibalt_query(types: list) -> str:
    return type_query(types)

def fetch_unique_random_cards(query: str, count: int, max_attempts_per_card: int = 10, require_noncreature: bool = False) -> list:
    """
    Fetch `count` random cards matching query, guaranteed distinct from
    each other within this batch (re-rolling on a duplicate draw).
    Separate calls to this function are independent -- duplicates
    across different batches/queries are fine and expected.

    If require_noncreature is True, also re-rolls on any card where a
    face is a Creature (handles transform/flip/mdfc cards where the
    query matched a noncreature face but another face is secretly a
    Creature -- see _is_all_noncreature).
    """
    chosen = []
    seen_ids = set()
    for _ in range(count):
        card = None
        for _ in range(max_attempts_per_card):
            candidate = fetch_random_card(query)
            if candidate["oracle_id"] in seen_ids:
                continue
            if require_noncreature and not _is_all_noncreature(candidate):
                continue
            card = candidate
            break
        if card is None:
            raise CardNotFoundError(
                f"Could not find {count} distinct matching cards for: {query}"
            )
        seen_ids.add(card["oracle_id"])
        chosen.append(card)
    return chosen

# ---------------------------------------------------------------- prompts
#
# Every prompt function accepts 'menu'/'back'/'m'/'b' (returns None) and
# 'quit'/'exit' (raises QuitProgram) in addition to a real answer.

def prompt_int_choice(prompt: str, valid_values) -> "int | None":
    valid_set = set(valid_values)
    while True:
        print("------------------------------------------------")
        raw = input(prompt).strip()
        if _check_control_words(raw):
            return None
        try:
            n = int(raw)
        except ValueError:
            print("  Enter a non-negative whole number.")
            continue
        if n not in valid_set:
            print(f"  Must be one of: {sorted(valid_set)}")
            continue
        return n

def prompt_choice(label: str, options: list) -> "str | None":
    print(label)
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        print("------------------------------------------------")
        raw = input("Selection ('m' to return, 'q' to exit): ").strip()
        if _check_control_words(raw):
            return None
        try:
            i = int(raw)
        except ValueError:
            print("  Enter a number.")
            continue
        if i < 1 or i > len(options):
            print(f"  Enter a number between 1 and {len(options)}.")
            continue
        return options[i - 1]

def prompt_multiselect(label: str, options: list) -> "list | None":
    print(label)
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        print("------------------------------------------------")
        raw = input("Selection, comma-separated ('m' to return, 'q' to exit): ").strip()
        if _check_control_words(raw):
            return None
        try:
            indices = [int(x) for x in raw.split(",")]
        except ValueError:
            print("  Enter numbers separated by commas, e.g. 1,3")
            continue
        if not indices or any(i < 1 or i > len(options) for i in indices):
            print(f"  Enter numbers between 1 and {len(options)}.")
            continue
        return [options[i - 1] for i in indices]

def prompt_yes_no(label: str, default: bool = True) -> "bool | None":
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{label} {suffix}: ").strip()
        if _check_control_words(raw):
            return None
        low = raw.lower()
        if low == "":
            return default
        if low in ("y", "yes"):
            return True
        if low in ("n", "no"):
            return False
        print("  Enter y or n.")

# ------------------------------------------------------------------ modes
#
# Each "_once" helper does exactly one round and returns None where a
# prompt returned None (user asked for the menu) so the caller can stop.
# The public run_* functions loop the "_once" helper until that happens.

def _momir_once(printer, conn_args, print_opts):
    """One creature spawn. Returns (printer, card_or_None)."""
    n = prompt_int_choice(f"Enter mana value (0-13, 15-16, 'm' to return, 'q' to exit): ", MOMIR_VALUES)
    if n is None:
        return printer, None
    try:
        card = fetch_random_card(momir_query(n))
    except CardNotFoundError as e:
        print(f"  {e}")
        return printer, "retry"
    print(f"  -> {card['name']}")
    printer = print_card(conn_args, printer, card, card, **print_opts)
    return printer, card

def run_momir(printer, conn_args, print_opts):
    while True:
        printer, card = _momir_once(printer, conn_args, print_opts)
        if card is None:
            return printer
        # "retry" (lookup failure) or a real card both loop back for another round

def run_mosto(printer, conn_args, print_opts):
    print("\n------------------------------------------------")
    equipment = True
    auras = prompt_yes_no("Include auras?", default=False)
    if auras is True:
        equipment = prompt_yes_no("Include equipment?", default=True)
    while True:
        if equipment is None:
            return printer
        if auras is None:
            return printer
        kinds = [k for k, enabled in (("equipment", equipment), ("aura", auras)) if enabled]
        if not kinds:
            print("  Need at least one of equipment/aura -- defaulting to equipment.")
            kinds = ["equipment"]

        printer, creature = _momir_once(printer, conn_args, print_opts)
        if creature is None:
            return printer
        if creature == "retry":
            continue
        
        cmc_max = int(creature["cmc"])
        try:
            gear = fetch_random_card(mosto_query(cmc_max, kinds))
        except CardNotFoundError as e:
            print(f"  No matching equipment/aura found: {e}")
            continue
        print(f"  -> {gear['name']}")
        printer = print_card(conn_args, printer, gear, gear, **print_opts)

def run_jhoira(printer, conn_args, print_opts):
    while True:
        spell_type = prompt_choice("------------------------------------------------\nSorcery or Instant?", ["Sorcery", "Instant"])
        if spell_type is None:
            return printer
        try:
            cards = fetch_unique_random_cards(jhoira_query(spell_type), 3)
        except CardNotFoundError as e:
            print(f"  {e}")
            continue
        for card in cards:
            printer = print_text_only(
                printer, card,
                width=print_opts["width"],
                text_width=print_opts["text_width"],
                no_cut=print_opts["no_cut"],
            )

def run_jace(printer, conn_args, print_opts):
    while True:
        types = prompt_multiselect("------------------------------------------------\nSelect card type(s):", CARD_TYPES)
        if types is None:
            return printer
        n = prompt_int_choice("Mana value (0-16, 'm' to return, 'q' to exit): ", range(0, 17))
        if n is None:
            return printer
        try:
            for _ in range(10):
                query = jace_query(n, types)
                print(f"  [debug] query: {query}")
                card = fetch_random_card(jace_query(n, types))
                if "Creature" in types or _is_all_noncreature(card):
                    break
            else:
                raise CardNotFoundError("Could not find a matching card after several tries.")
        except CardNotFoundError as e:
            print(f"  {e}")
            continue
        print(f"  -> {card['name']}")
        printer = print_card(conn_args, printer, card, card, **print_opts)

def run_tibalt(printer, conn_args, print_opts):
    while True:
        types = prompt_multiselect("------------------------------------------------\nSelect card type(s):", CARD_TYPES)
        if types is None:
            return printer
        try:
            cards = fetch_unique_random_cards(
                tibalt_query(types), 3, require_noncreature="Creature" not in types
            )
        except CardNotFoundError as e:
            print(f"  {e}")
            continue
        for card in cards:
            printer = print_text_only(
                printer, card,
                width=print_opts["width"],
                text_width=print_opts["text_width"],
                no_cut=print_opts["no_cut"],
            )

EXPLANATIONS = {
    "Momir": ("{X}, Discard a card: Create a token copy of a random creature card of mana value X.\n  Activate only as a sorcery and only once each turn."),
    "MoSto": ("{X}, Discard a card: Create a token copy of a random creature card of mana value X.\n  Activate only as a sorcery and only once each turn.\n\n  Whenever a creature you control enters, create a token that\'s a copy of a random\n  Equipment card with mana value less than that creature\'s mana value. Attach\n  that Equipment to that creature."),
    "Jhoira": ("{3}, Discard a card, Choose instant or sorcery: View three random distinct cards of\n  the type chosen. You may cast a copy of one of them without paying its mana cost.\n  You may choose sorcery only if activated as a sorcery."),
    "Jace": ("{X}, Discard a card, Choose any number of card type(s): Create a token copy of a\n  random card of one or more of the chosen type(s). Activate only as a sorcery\n  (unless the only chosen type is instant) and only once each turn."),
    "Tibalt": ("{3}, Discard a card, Choose any number of card type(s): View three random distinct\n  cards of one or more of the chosen type(s). You may cast a copy of one of them\n  without paying its mana cost. You may choose non-instant types only as a sorcery."),
}

def explain():
    while True:
        print("------------------------------------------------")
        mode_choice = prompt_choice("Select mode to explain ('m' to return, 'q' to exit):", list(EXPLANATIONS.keys()))
        if mode_choice is None:
            return
        teach = EXPLANATIONS.get(mode_choice)
        print("------------------------------------------------\n")
        print(f" {mode_choice}:")
        print(f"  {teach}\n")
        
MODES = {
    "1": ("Momir", "Momir:  {X} -> MV=X Creature                   (Sorcery-speed, 1x/turn)", run_momir),
    "2": ("MoSto", "MoSto:  {X} -> MV=X Creature + MV<=X Equipment (Sorcery-speed, 1x/turn)", run_mosto),
    "3": ("Jhoira", "Jhoira: {3} -> 1 of 3 Instants    or Sorceries (Sorcery-speed)", run_jhoira),
    "4": ("Jace", "Jace:   {X} -> MV=X Card of Type(s) chosen     (Sorcery-speed, 1x/turn)", run_jace),
    "5": ("Tibalt", "Tibalt: {3} -> 1 of 3 Cards of Type(s) chosen  (Sorcery-speed if not just Instant)", run_tibalt),
}

def intro():
    os.system("cls")

    print("------------------------------------------------")
    print("MoJhoSto Basic Expanded")
    print("By: Nicholas Zieve")
    print("Inspired By Hayden Moritz's Raspberry Pi Variant \n")

    print("Type 'x' from this screen for detailed explanations of each mode.")
    print("Type 'c' from this screen for optional life and hand total changes.\n")

    print("Type 'm' at any point to return to this screen.")
    print("Type 'q' at any point to exit the program.")    
    print("------------------------------------------------")

def lifechange():
    print("---------------------------------------------")
    print("|        Vanguard Name        | Life | Hand |")
    print("|-----------------------------|------|------|")
    print("| Momir Vig, Simic Visionary  |  +4  |  +0  |")
    print("| Stonehewer Giant            |  -5  |  +1  |")
    print("| Jhoira of the Ghitu         |  +0  |  +1  |")
    print("| Jace Beleren (Custom Mode)  |  +1  |  +0  |")
    print("| Tibalt (Custom Mode)        |  +0  |  -2  |")
    print("---------------------------------------------\n")

# ------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MoJhoJace Basic -- continuous random-card printer.")
    parser.add_argument(
        "--width", default="58mm", choices=list(PRINTER_WIDTH_PRESETS.keys()),
        help="Printer paper width preset (default: 58mm)",
    )
    parser.add_argument("--connection", default="usb", choices=["usb", "bluetooth", "network"], help="How this machine talks to the printer")
    parser.add_argument("--vendor-id", type=lambda x: int(x, 0), default=None)
    parser.add_argument("--product-id", type=lambda x: int(x, 0), default=None)
    parser.add_argument("--serial-port", default=None, help="e.g. /dev/rfcomm0 or COM5")
    parser.add_argument("--baudrate", type=int, default=9600)
    parser.add_argument("--host", default=None, help="Printer IP address (network connection)")
    parser.add_argument("--gamma", type=float, default=0.85)
    parser.add_argument("--contrast-cutoff", type=float, default=0.0)
    parser.add_argument("--no-contrast", action="store_true")
    parser.add_argument("--no-sharpen", action="store_true")
    parser.add_argument("--warm-up-rows", type=int, default=30)
    parser.add_argument("--no-text", action="store_true")
    parser.add_argument("--text-width", type=int, default=None)
    parser.add_argument("--no-cut", action="store_true")
    return parser

def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    conn_args = dict(
        connection=args.connection,
        vendor_id=args.vendor_id,
        product_id=args.product_id,
        serial_port=args.serial_port,
        baudrate=args.baudrate,
        host=args.host,
    )
    print_opts = dict(
        width=args.width,
        gamma=args.gamma,
        contrast_cutoff=args.contrast_cutoff,
        enhance_contrast=not args.no_contrast,
        sharpen=not args.no_sharpen,
        warm_up_rows=args.warm_up_rows,
        no_text=args.no_text,
        text_width=args.text_width,
        no_cut=args.no_cut,
    )

    try:
        printer = get_printer(**conn_args)
    except PrinterConfigError as e:
        print(f"Printer config error: {e}", file=sys.stderr)
        return 1

    intro()
    print(" ")
    
    while True:
        print("Modes:")
        for key, (name, description, _) in MODES.items():
            print(f"  {key}. {description}")
        print("\n------------------------------------------------")
        choice = input("Choose a mode ('q' to exit): ").strip()

        try:
            if choice.lower() in QUIT_WORDS:
                os.system("cls")
                break
            if choice.lower() in ("x", "h", "help", "info"):
                explain()
                continue
            if choice.lower() in ("c", "life", "hand"):
                lifechange()
                continue
            entry = MODES.get(choice)
            if entry is None:
                print("Unknown choice.\n")
                continue

            name, description, handler = entry
            clarify = EXPLANATIONS.get(name)
            os.system("cls")
            print(f"------------------------------------------------")
            print(f"Mode: {name}")
            print(f"  {clarify}\n")
            print("Type 'm' to return to the main menu.")
            print("Type 'q' to exit the program.")
            printer = handler(printer, conn_args, print_opts)
            print()
        except QuitProgram:
            break
        except PrintJobError as e:
            print(f"Printer error: {e}", file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            break

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
