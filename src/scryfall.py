"""
Fetches Magic: The Gathering card art from the Scryfall API.

Scryfall's fuzzy-name search means you don't need exact spelling/capitalization.
Docs: https://scryfall.com/docs/api/cards/named
"""
from __future__ import annotations

import io
import time
from typing import Optional

import requests
from PIL import Image

SCRYFALL_NAMED_URL = "https://api.scryfall.com/cards/named"
SCRYFALL_SEARCH_URL = "https://api.scryfall.com/cards/search"

REQUEST_HEADERS = {
    "User-Agent": "MtgThermalPrinter/1.0",
    "Accept": "application/json",
}

# Scryfall asks that clients not hammer the API. This is a floor, not a target.
MIN_REQUEST_INTERVAL_SECONDS = 0.1
_last_request_time = 0.0

SCRYFALL_RANDOM_URL = "https://api.scryfall.com/cards/random"

def fetch_random_card(query: str) -> dict:
    """Return one random card matching a Scryfall search query."""
    _throttle()
    try:
        resp = requests.get(
            SCRYFALL_RANDOM_URL, params={"q": query}, headers=REQUEST_HEADERS, timeout=10
        )
    except requests.exceptions.RequestException as e:
        raise CardNotFoundError(f"Network error while searching: {e}")
    if resp.status_code == 404:
        raise CardNotFoundError(f"No cards found matching: {query}")
    resp.raise_for_status()
    return resp.json()

def fetch_oldest_printing(card_name: str, set_code: Optional[str] = None) -> dict:
    """
    Return the chronologically oldest printing of a card.

    Looks up the card by name to get its oracle_id (a stable identifier
    for the card itself, independent of any specific printing), then
    searches by that id directly -- avoids the ambiguity of text-based
    search operators like is:firstprint matching unrelated cards.
    """
    card = fetch_card_data(card_name, set_code=set_code)
    oracle_id = card["oracle_id"]

    _throttle()
    resp = requests.get(
        SCRYFALL_SEARCH_URL,
        params={
            "q": f"oracleid:{oracle_id}",
            "unique": "prints",
            "order": "released",
            "dir": "asc",
        },
        headers=REQUEST_HEADERS,
        timeout=10,
    )
    resp.raise_for_status()
    results = resp.json().get("data", [])

    if not results:
        return card  # shouldn't happen, but fall back to the original lookup

    return results[0]  # first result = oldest, since we sorted ascending by release date


def _throttle() -> None:
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL_SECONDS:
        time.sleep(MIN_REQUEST_INTERVAL_SECONDS - elapsed)
    _last_request_time = time.monotonic()


class CardNotFoundError(Exception):
    pass


def fetch_card_data(card_name: str, set_code: Optional[str] = None) -> dict:
    """Look up a card by (fuzzy) name and return its raw Scryfall JSON."""
    params = {"fuzzy": card_name}
    if set_code:
        params["set"] = set_code

    _throttle()
    resp = requests.get(SCRYFALL_NAMED_URL, params=params, headers=REQUEST_HEADERS, timeout=10)

    if resp.status_code == 404:
        raise CardNotFoundError(f"No card found matching '{card_name}'")
    resp.raise_for_status()
    return resp.json()


def get_art_image(card: dict, image_variant: str = "art_crop") -> Image.Image:
    """Download just the illustration for a card dict you already have (e.g. from a search result)."""
    image_uris = card.get("image_uris")
    if image_uris is None and "card_faces" in card:
        image_uris = card["card_faces"][0].get("image_uris")

    card_name = card.get("name", "this card")
    if not image_uris or image_variant not in image_uris:
        raise CardNotFoundError(f"No '{image_variant}' image available for '{card_name}'")

    _throttle()
    img_resp = requests.get(image_uris[image_variant], headers=REQUEST_HEADERS, timeout=10)
    img_resp.raise_for_status()

    return Image.open(io.BytesIO(img_resp.content)).convert("RGB")

def fetch_card_art(
    card_name: str,
    set_code: Optional[str] = None,
    image_variant: str = "art_crop",
) -> Image.Image:
    """
    Fetch just the illustration for a card (not the full card frame).

    image_variant options (per Scryfall): 'art_crop', 'small', 'normal',
    'large', 'png', 'border_crop'. 'art_crop' is the illustration only,
    which is what you want for a thermal-printer keepsake.
    """
    card = fetch_card_data(card_name, set_code=set_code)

    image_uris = card.get("image_uris")
    if image_uris is None and "card_faces" in card:
        # Double-faced cards store images per-face instead of top-level.
        image_uris = card["card_faces"][0].get("image_uris")

    if not image_uris or image_variant not in image_uris:
        raise CardNotFoundError(
            f"No '{image_variant}' image available for '{card_name}'"
        )

    _throttle()
    img_resp = requests.get(image_uris[image_variant], headers=REQUEST_HEADERS, timeout=10)
    img_resp.raise_for_status()

    return Image.open(io.BytesIO(img_resp.content)).convert("RGB")
