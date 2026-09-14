"""
Prepares a full-color image for monochrome thermal printing.

Thermal printers can only turn each dot on or off, so we resize to the
printer's dot width and dither down to 1-bit. Dithering (vs. a flat
threshold) is what keeps gradients in card art from turning into muddy
blobs.
"""
from __future__ import annotations

from PIL import Image, ImageFilter, ImageOps

# Common thermal printer print-head widths, in dots, at 203 DPI.
PRINTER_WIDTH_PRESETS = {
    "58mm": 384,
    "80mm": 576,
}


def _apply_gamma(image: Image.Image, gamma: float) -> Image.Image:
    """
    Lighten (gamma < 1) or darken (gamma > 1) midtones/shadows.

    Dark MtG art tends to lose all shadow detail once reduced to 1-bit,
    since anything below the dither's midpoint just becomes solid black.
    Lifting gamma slightly before dithering gives the shadows somewhere
    to go other than pure black.
    """
    if gamma == 1.0:
        return image
    lut = [round(255 * ((i / 255) ** gamma)) for i in range(256)]
    return image.point(lut)


def prepare_for_printer(
    image: Image.Image,
    printer_width_px: int = 384,
    enhance_contrast: bool = True,
    contrast_cutoff: float = 0.5,
    gamma: float = 0.85,
    sharpen: bool = True,
) -> Image.Image:
    """
    Resize to the printer's dot width (preserving aspect ratio) and
    convert to 1-bit using Floyd-Steinberg dithering.

    The defaults are tuned to avoid the "solid black blobs next to
    washed-out patches" look that comes from clipping contrast too hard
    before dithering: a gentle contrast stretch (low cutoff), a mild
    gamma lift to keep shadow detail out of pure black, and a light
    sharpen so dithering has more edge information to work with.
    """
    image = image.convert("RGB")

    if enhance_contrast:
        # A low cutoff avoids clipping shadows/highlights to flat
        # black/white before dithering gets a chance to work with them.
        image = ImageOps.autocontrast(image, cutoff=contrast_cutoff)

    width, height = image.size
    if width != printer_width_px:
        new_height = round(height * (printer_width_px / width))
        image = image.resize((printer_width_px, new_height), Image.LANCZOS)

    image = image.convert("L")
    image = _apply_gamma(image, gamma)

    if sharpen:
        # Helps fine detail (linework, small text in the art) survive
        # being reduced to 1-bit; radius/percent kept mild since thermal
        # printers already have limited resolution.
        image = image.filter(ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))

    image = image.convert("1")  # PIL uses Floyd-Steinberg dithering by default

    return image