"""
Wraps python-escpos so the rest of the codebase doesn't care whether the
printer is reached over USB, a paired Bluetooth serial port, or the network.

This is the ONLY module that needs to change if you switch how a given
machine talks to the printer.
"""
from __future__ import annotations

from typing import Optional

from escpos.printer import Usb, Serial, Network
from PIL import Image

try:
    import libusb_package
    _HAS_LIBUSB_PACKAGE = True
except ImportError:
    _HAS_LIBUSB_PACKAGE = False

class PrinterConfigError(Exception):
    pass


def get_printer(
    connection: str,
    *,
    # USB
    vendor_id: Optional[int] = None,
    product_id: Optional[int] = None,
    # Bluetooth (classic SPP), once paired and bound to a serial device.
    # On Linux/Pi this is typically /dev/rfcomm0.
    # On Windows this is typically a COM port, e.g. "COM5".
    # On Android/Termux via a rooted rfcomm bind, similar to Linux.
    serial_port: Optional[str] = None,
    baudrate: int = 9600,
    # Network (if your printer has Wi-Fi/ethernet, not relevant to BT/USB
    # models but included since python-escpos supports it for free)
    host: Optional[str] = None,
):
    """
    Factory that returns a ready-to-use escpos Printer object.

    connection: one of "usb", "bluetooth", "network"
    """
    if connection == "usb":
        if vendor_id is None or product_id is None:
            raise PrinterConfigError(
                "USB connection requires vendor_id and product_id. "
                "Find these with `lsusb` (Linux) and convert hex, e.g. "
                "'0x0483' and '0x5743'."
            )
        usb_args = {}
        if _HAS_LIBUSB_PACKAGE:
            usb_args["backend"] = libusb_package.get_libusb1_backend()
        return Usb(vendor_id, product_id, usb_args=usb_args, profile="POS-5890")

    if connection == "bluetooth":
        if not serial_port:
            raise PrinterConfigError(
                "Bluetooth connection requires serial_port, e.g. "
                "'/dev/rfcomm0' (Linux/Pi) or 'COM5' (Windows). "
                "Pair the printer first, then bind it to that port "
                "(e.g. `sudo rfcomm bind 0 <MAC_ADDRESS>` on Linux)."
            )
        return Serial(devfile=serial_port, baudrate=baudrate)

    if connection == "network":
        if not host:
            raise PrinterConfigError("Network connection requires host (IP address).")
        return Network(host)

    raise PrinterConfigError(
        f"Unknown connection type '{connection}'. Use 'usb', 'bluetooth', or 'network'."
    )

def warm_up_head(printer, printer_width_px: int, rows: int = 30) -> None:
    """
    Print a strip of solid black before the real image.
 
    Cheap clone print heads are cold at the start of a job and print
    faint for the first several rows until they reach temperature --
    unlike name-brand printers, these usually have no heat compensation
    to correct for it. Printing a junk black strip first (which gets
    fed/torn off or just ignored) means the real image starts once the
    head is already hot.
    """
    warmup_image = Image.new("1", (printer_width_px, rows), color=0)  # 0 = black in mode "1"
    printer.image(warmup_image)

def set_darkness(
    printer,
    heating_dots: int = 20,
    heating_time: int = 200,
    heating_interval: int = 2,
    print_density: int = 20,
    print_break_time: int = 2,
) -> None:
    printer._raw(bytes([0x1B, 0x37, heating_dots, heating_time, heating_interval]))
    n = ((print_break_time & 0x07) << 5) | (print_density & 0x1F)
    printer._raw(bytes([0x12, 0x23, n]))

def print_text_lines(printer, lines, bold_first_n: int = 1) -> None:
    """Print a list of plain-text lines, bolding the first N (name/cost header)."""
    for i, line in enumerate(lines):
        printer.set(bold=(i < bold_first_n))
        printer.text(line + "\n")
    printer.set(bold=False)

def print_image(printer, image: Image.Image, cut: bool = True) -> None:
    """Send a prepared (1-bit) PIL image to the printer."""
    printer.image(image)
    if cut:
        printer.cut()
