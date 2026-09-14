"""
Shared print pipeline: prepares and prints a card's art, handles the
printer's power-cycle-and-reconnect quirk after a big image job, then
prints the card's text and cuts.

This is lifted directly from cli.py's main() so momir.py doesn't
duplicate (and risk breaking) that hard-won reconnect logic.
"""
from __future__ import annotations

import time
from typing import Optional

from escpos.exceptions import DeviceNotFoundError, USBNotFoundError

from cardtext import build_card_text
from imaging import PRINTER_WIDTH_PRESETS, prepare_for_printer
from printer import PrinterConfigError, get_printer, print_image, print_text_lines, warm_up_head
from scryfall import get_art_image


class PrintJobError(Exception):
    pass


def reconnect_printer(conn_args: dict, retries: int = 15, delay: float = 1.0):
    """
    Reconnect to the printer, retrying since USB re-enumeration timing
    varies after the printer power-cycles itself following a big image job.
    """
    for _ in range(retries):
        time.sleep(delay)
        try:
            candidate = get_printer(**conn_args)
            _ = candidate.device  # force it to actually probe the USB bus now
            return candidate
        except (PrinterConfigError, DeviceNotFoundError, USBNotFoundError):
            continue
    raise PrintJobError("Could not reconnect to printer after image print.")


def print_card(
    conn_args: dict,
    printer,
    art_card: dict,
    text_card: Optional[dict] = None,
    *,
    width: str = "58mm",
    gamma: float = 0.85,
    contrast_cutoff: float = 0.0,
    enhance_contrast: bool = True,
    sharpen: bool = True,
    warm_up_rows: int = 30,
    no_text: bool = False,
    text_width: Optional[int] = None,
    no_cut: bool = False,
):
    """
    Print one card's art (+ text unless no_text) and return a fresh,
    reconnected printer object -- the caller should use THIS returned
    object for the next print, not the one passed in.

    art_card and text_card can be the same dict, or different printings
    (e.g. one printing's art alongside another printing's up-to-date text).
    """
    printer_width_px = PRINTER_WIDTH_PRESETS[width]

    try:
        printer.device.clear_halt(printer.out_ep)
    except Exception:
        pass

    art = get_art_image(art_card, image_variant="art_crop")
    image = prepare_for_printer(
        art,
        printer_width_px=printer_width_px,
        enhance_contrast=enhance_contrast,
        contrast_cutoff=contrast_cutoff,
        gamma=gamma,
        sharpen=sharpen,
    )

    if warm_up_rows > 0:
        warm_up_head(printer, printer_width_px, rows=warm_up_rows)

    print_image(printer, image, cut=False)

    # The printer power-cycles itself after a big print job (brownout from
    # the print head's current draw), dropping and re-enumerating the USB
    # device. Reconnect rather than reuse the now-stale handle.
    printer.close()
    printer = reconnect_printer(conn_args)
    printer._raw(b"\x1b\x40")  # ESC @ -- re-initialize the freshly-rebooted printer

    chars = text_width or (32 if width == "58mm" else 48)

    if not no_text:
        card_for_text = text_card if text_card is not None else art_card
        printer.text("\n")
        print_text_lines(printer, build_card_text(card_for_text, width_chars=chars))

    if not no_cut:
        printer.text("-" * chars + "\n\n")
        printer.cut(feed=False)

    return printer


def print_text_only(
    printer,
    card: dict,
    *,
    width: str = "58mm",
    text_width: Optional[int] = None,
    no_cut: bool = False,
):
    """
    Print just a card's text -- no art, no image job, no reconnect dance.

    For batch modes (Jhoira/Tibalt) where you're browsing several
    candidates: the printer's power-cycle-and-reconnect issue is
    specifically triggered by the big image transfer's current draw, so
    skipping the image here also means skipping that ~15s reconnect
    wait per card.
    """
    chars = text_width or (32 if width == "58mm" else 48)
    printer.text("\n")
    print_text_lines(printer, build_card_text(card, width_chars=chars))
    if not no_cut:
        printer.text("-" * chars + "\n")
        printer.cut(feed=False)
    return printer
