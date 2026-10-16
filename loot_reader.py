"""Conservative proposals from user-selected item text and images.

This module never captures the screen or updates a ledger. Every result must be
reviewed by the user; a proposal is not evidence of an acquired item by itself.
"""
from dataclasses import dataclass, field
from collections.abc import Callable, Iterable, Mapping
import re
import unicodedata

import cv2
import numpy as np


Catalog = Iterable[str] | Mapping[str, Iterable[str]]
MAX_TEXT_LENGTH = 100_000
# Match the ledger's supported integer range without importing ledger state.
MAX_QUANTITY = 10 ** 18
MAX_IMAGE_PIXELS = 4_000_000
MAX_IMAGE_SIDE = 4096
OCR_TIMEOUT_SECONDS = 3.0
_QUANTITY = r"(?:0|[1-9][0-9]*|[1-9][0-9]{0,2}(?:,[0-9]{3})+)"
_SUFFIX = re.compile(rf"(?P<name>.+) [x×] (?P<quantity>{_QUANTITY})")
_PREFIX = re.compile(rf"(?P<quantity>{_QUANTITY}) (?P<name>.+)")
_UNREADABLE_SUFFIX = re.compile(r"(?P<name>.+) [x×](?: .*)?")
_UNREADABLE_PREFIX = re.compile(r"\S+ (?P<name>.+)")


@dataclass(frozen=True)
class ItemProposal:
    name: str
    quantity: int
    mode: str
    source_lines: tuple[str, ...]


@dataclass(frozen=True)
class ReadResult:
    items: tuple[ItemProposal, ...]
    warnings: tuple[str, ...]
    raw_text: str
    mode: str
    review_required: bool = field(default=True, init=False)
    source_png: bytes | None = None
    ocr_texts: tuple[str, ...] = ()
    conflicted_names: tuple[str, ...] = ()


def _normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _catalog_aliases(items: Catalog) -> dict[str, set[str]]:
    """Aliases are supplied explicitly; there is no stemming or fuzzy matching."""
    entries = items.items() if isinstance(items, Mapping) else ((name, ()) for name in items)
    aliases = {}
    for name, alternatives in entries:
        if not isinstance(name, str) or not _normalize(name):
            raise ValueError("Catalog names must be non-empty strings")
        if isinstance(alternatives, str):
            alternatives = (alternatives,)
        for alias in (name, *alternatives):
            if not isinstance(alias, str) or not _normalize(alias):
                raise ValueError("Catalog aliases must be non-empty strings")
            aliases.setdefault(_normalize(alias), set()).add(name)
    return aliases


def _check_mode(mode: str) -> None:
    if mode not in ("pickup", "inventory"):
        raise ValueError("Mode must be 'pickup' or 'inventory'")


def parse_item_text(text: str, items: Catalog, *, mode: str = "pickup") -> ReadResult:
    """Parse full lines into proposals, preserving unknown text for manual review.

    Accepts ``name x quantity``, ``quantity name`` and, in pickup mode only,
    ``Obtained quantity name``. Canonical names may map to explicit aliases.
    Duplicate quantities are never summed; conflicting quantities stay unknown.
    Inventory quantities are observations, not collected-item event counts.
    """
    _check_mode(mode)
    if not isinstance(text, str):
        raise TypeError("Item text must be a string")
    if len(text) > MAX_TEXT_LENGTH:
        return ReadResult((), ("Text is too large; select a smaller region.",), text, mode)
    aliases = _catalog_aliases(items)
    warnings = []
    observed = {}
    conflicted = set()
    for number, source_line in enumerate(text.splitlines(), 1):
        line = _normalize(source_line)
        if not line:
            continue
        if line.startswith("obtained "):
            if mode != "pickup":
                warnings.append(f"Line {number}: pickup wording cannot describe an inventory snapshot.")
                continue
            line = line[len("obtained "):]
            match = _PREFIX.fullmatch(line)
        else:
            match = _SUFFIX.fullmatch(line) or _PREFIX.fullmatch(line)
        if not match:
            warnings.append(f"Line {number}: missing or invalid explicit item quantity; review the source.")
            unreadable = _UNREADABLE_SUFFIX.fullmatch(line) or _UNREADABLE_PREFIX.fullmatch(line)
            if unreadable:
                conflicted.update(aliases.get(unreadable["name"], ()))
            conflicted.update(aliases.get(line, ()))
            continue
        names = aliases.get(match["name"], set())
        if len(names) != 1:
            warnings.append(f"Line {number}: unknown or ambiguous item name '{match['name']}'.")
            conflicted.update(names)
            continue
        name = next(iter(names))
        digits = match["quantity"].replace(",", "")
        if len(digits) > 19 or int(digits) > MAX_QUANTITY:
            warnings.append(f"Line {number}: quantity for {name} is too large.")
            conflicted.add(name)
            continue
        quantity = int(digits)
        if quantity == 0 and mode == "pickup":
            warnings.append(f"Line {number}: a pickup must have a positive quantity.")
            conflicted.add(name)
            continue
        quantities = observed.setdefault(name, {})
        if quantities and quantity not in quantities:
            conflicted.add(name)
        if quantity in quantities:
            warnings.append(f"Line {number}: duplicate quantity for {name}; counted once for review.")
        sources = quantities.setdefault(quantity, [])
        if source_line not in sources:
            sources.append(source_line)
    proposals = []
    for name, quantities in observed.items():
        if name in conflicted:
            continue
        quantity, sources = next(iter(quantities.items()))
        proposals.append(ItemProposal(name, quantity, mode, tuple(sources)))
    for name in sorted(conflicted):
        warnings.append(f"Conflicting or unreadable readings or aliases for {name}; quantity is unknown.")
    if not text.strip():
        warnings.append("No readable item text; quantities are unknown, not zero.")
    return ReadResult(tuple(proposals), tuple(warnings), text, mode,
                      conflicted_names=tuple(sorted(conflicted)))


def read_item_image(bgr: np.ndarray, items: Catalog, *, mode: str = "pickup",
                    ocr: Callable[..., str] | None = None) -> ReadResult:
    """Read a supplied BGR image without capture, native APIs or ledger writes.

    The caller configures the local Tesseract executable. The optional OCR seam
    has pytesseract's ``image_to_string(image, config=..., timeout=...)`` shape.
    Two of three preprocessing variants must agree on each exact name/quantity,
    and any conflicting quantity blocks that item, even against a majority.
    This agreement is a review aid, not a live-game accuracy guarantee.

    Original PNG bytes and all OCR texts are returned for local evidence. This
    function neither saves nor transmits them. The caller controls retention.
    """
    _check_mode(mode)
    if (not isinstance(bgr, np.ndarray) or bgr.dtype != np.uint8 or bgr.ndim != 3
            or bgr.shape[2] != 3 or not bgr.size):
        return ReadResult((), ("Invalid or empty image; quantities are unknown.",), "", mode)
    height, width = bgr.shape[:2]
    if height * width > MAX_IMAGE_PIXELS or max(height, width) > MAX_IMAGE_SIDE:
        return ReadResult((), ("Image is too large; select a smaller region.",), "", mode)
    encoded, png = cv2.imencode(".png", bgr)
    if not encoded:
        return ReadResult((), ("Could not preserve the image; read it manually.",), "", mode)
    source_png = png.tobytes()
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if int(gray.max()) == int(gray.min()):
        return ReadResult((), ("Blank image; quantities are unknown, not zero.",), "", mode,
                          source_png=source_png)
    scaled = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, threshold = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    variants = (scaled, threshold, cv2.bitwise_not(threshold))
    if isinstance(items, Mapping):
        catalog = {name: (aliases,) if isinstance(aliases, str) else tuple(aliases)
                   for name, aliases in items.items()}
    else:
        catalog = tuple(items)
    if ocr is None:
        import pytesseract
        ocr = pytesseract.image_to_string
    texts = []
    warnings = []
    observed = {}
    conflicted = set()
    for index, variant in enumerate(variants, 1):
        try:
            text = ocr(variant, config="--psm 6", timeout=OCR_TIMEOUT_SECONDS)
        except (RuntimeError, OSError) as error:
            warnings.append(f"OCR variant {index} failed ({type(error).__name__}); no reading.")
            texts.append("")
            continue
        if not isinstance(text, str):
            warnings.append(f"OCR variant {index} returned no text; no reading.")
            texts.append("")
            continue
        texts.append(text)
        parsed = parse_item_text(text, catalog, mode=mode)
        warnings.extend(f"OCR variant {index}: {warning}" for warning in parsed.warnings)
        conflicted.update(parsed.conflicted_names)
        for proposal in parsed.items:
            observed.setdefault(proposal.name, {}).setdefault(proposal.quantity, []).append(proposal)
    proposals = []
    for name, quantities in observed.items():
        if len(quantities) > 1 or name in conflicted:
            conflicted.add(name)
            warnings.append(f"Conflicting OCR quantities for {name}; quantity is unknown.")
            continue
        quantity, readings = next(iter(quantities.items()))
        if len(readings) < 2:
            warnings.append(f"No OCR consensus for {name}; quantity is unknown.")
            continue
        lines = tuple(dict.fromkeys(line for reading in readings for line in reading.source_lines))
        proposals.append(ItemProposal(name, quantity, mode, lines))
    return ReadResult(tuple(proposals), tuple(warnings), "\n\n".join(texts), mode,
                      source_png=source_png, ocr_texts=tuple(texts),
                      conflicted_names=tuple(sorted(conflicted)))
