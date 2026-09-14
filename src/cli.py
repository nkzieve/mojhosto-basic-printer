"""
Direct-print CLI. Run this on whatever machine is physically connected
(USB or paired Bluetooth) to the printer.

Examples:

  # USB, e.g. on the Pi or a laptop
  python -m mtg_printer.cli "Lightning Bolt" --connection usb \\
      --vendor-id 0x0483 --product-id 0x5743

  # Bluetooth, paired and bound to a serial device first
  python -m mtg_printer.cli "Lightning Bolt" --connection bluetooth \\
      --serial-port /dev/rfcomm0
"""
from __future__ import annotations

import argparse
import sys
import time

from imaging import PRINTER_WIDTH_PRESETS, prepare_for_printer
from cardtext import build_card_text
from printer import PrinterConfigError, get_printer, print_image, warm_up_head, print_text_lines
from scryfall import CardNotFoundError, fetch_card_art, fetch_card_data, fetch_oldest_printing, get_art_image

from escpos.exceptions import DeviceNotFoundError, USBNotFoundError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print MtG card art to a thermal printer.")
    parser.add_argument("card_name", help="Card name (fuzzy match supported)")
    parser.add_argument("--set", dest="set_code", default=None, help="Optional set code")
    parser.add_argument(
        "--width",
        default="58mm",
        choices=list(PRINTER_WIDTH_PRESETS.keys()),
        help="Printer paper width preset (default: 58mm)",
    )
    parser.add_argument(
        "--connection",
        required=True,
        choices=["usb", "bluetooth", "network"],
        help="How this machine talks to the printer",
    )
    parser.add_argument("--vendor-id", type=lambda x: int(x, 0), default=None)
    parser.add_argument("--product-id", type=lambda x: int(x, 0), default=None)
    parser.add_argument("--serial-port", default=None, help="e.g. /dev/rfcomm0 or COM5")
    parser.add_argument("--baudrate", type=int, default=9600)
    parser.add_argument("--host", default=None, help="Printer IP address (network connection)")
    parser.add_argument("--gamma", type=float, default=0.85, help="Gamma correction before dithering. <1 lightens shadows (default 0.85), >1 darkens.")
    parser.add_argument("--contrast-cutoff", type=float, default=0, help="Autocontrast clip percentage (default 0.5). Lower = gentler.")
    parser.add_argument("--heating-time", type=int, default=200)
    parser.add_argument("--warm-up-rows", type=int, default=30, help="Rows of solid black to print before the real image, to heat up the print head (default 30, 0 to disable)")
    parser.add_argument("--no-darkness-boost", action="store_true")
    parser.add_argument("--no-sharpen", action="store_true", help="Skip sharpening before dithering")
    parser.add_argument("--no-contrast", action="store_true", help="Skip autocontrast entirely")
    parser.add_argument("--no-cut", action="store_true", help="Skip the auto-cut command")
    parser.add_argument("--no-text", action="store_true", help="Skip the card text; image only")
    parser.add_argument("--text-width", type=int, default=None, help="Chars per line (default: 32 for 58mm, 48 for 80mm)")
    parser.add_argument("--oldest-art", action="store_true", default=True, help="Use the card's original printing/art instead of the default")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.oldest_art:
            art_card = fetch_oldest_printing(args.card_name, set_code=args.set_code)
            art = get_art_image(art_card, image_variant="art_crop")
        else:
            art = fetch_card_art(args.card_name, set_code=args.set_code)
    except CardNotFoundError as e:
        print(f"Card lookup failed: {e}", file=sys.stderr)
        return 1

    image = prepare_for_printer(art, printer_width_px=PRINTER_WIDTH_PRESETS[args.width], enhance_contrast=not args.no_contrast, contrast_cutoff=args.contrast_cutoff, gamma=args.gamma, sharpen=not args.no_sharpen)

    try:
        printer = get_printer(
            args.connection,
            vendor_id=args.vendor_id,
            product_id=args.product_id,
            serial_port=args.serial_port,
            baudrate=args.baudrate,
            host=args.host,
        )
    except PrinterConfigError as e:
        print(f"Printer config error: {e}", file=sys.stderr)
        return 1

    if args.warm_up_rows > 0:
        warm_up_head(printer, PRINTER_WIDTH_PRESETS[args.width], rows=args.warm_up_rows)

    print_image(printer, image, cut=False)

    # The printer appears to power-cycle itself after a big print job
    # (likely a brownout from the print head's current draw), which drops
    # and re-enumerates the USB device. Reconnect rather than reuse the
    # old (now stale) handle, retrying since re-enumeration timing varies.
    printer.close()

    reconnected = None
    for attempt in range(15):
        time.sleep(1)
        try:
            candidate = get_printer(
                args.connection,
                vendor_id=args.vendor_id,
                product_id=args.product_id,
                serial_port=args.serial_port,
                baudrate=args.baudrate,
                host=args.host,
            )
            tester = candidate.device # force probing USB
            reconnected = candidate
            break
        except (PrinterConfigError, DeviceNotFoundError, USBNotFoundError):
            continue

    if reconnected is None:
        print("Could not reconnect to printer after image print.", file=sys.stderr)
        return 1

    printer = reconnected

    printer._raw(b"\x1b\x40")  # ESC @ -- initialize the freshly-rebooted printer before sending anything else

    if not args.no_text:
        card = fetch_card_data(args.card_name, set_code=args.set_code)
        text_width = args.text_width or (32 if args.width == "58mm" else 48)
        printer.text("\n")
        print_text_lines(printer, build_card_text(card, width_chars=text_width))

    if not args.no_cut:
        text_width = args.text_width or (32 if args.width == "58mm" else 48)
        printer.text("-" * text_width + "\n")
        printer.cut(feed=False)

    print(f"Printed: {args.card_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
