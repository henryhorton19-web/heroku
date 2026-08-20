"""Shipping labels: detect the carrier, crop to the label, merge into one batch.

Carriers give you an A4 page with the label somewhere on it and the rest taken up by
advertising, a returns slip, or whitespace. A thermal printer wants 6x4 inches of
label and nothing else; printing the A4 scaled down gives a barcode too small to
scan, which fails at the counter after the parcel is packed.

**The asymmetry: a mis-cropped label is a parcel that does not ship.** It fails after
the sale is made and the item is boxed. So detection refuses rather than guesses — an
unrecognised carrier returns `None` and the page passes through *uncropped*. That
prints badly and obviously, which is the failure you want. A wrong crop prints
beautifully and fails at the counter.

**Crop boxes are per carrier and are measurements, not settings.** Each is where that
carrier puts the label on their A4 output. They are as provisional as anything else
here — a carrier changing their template silently breaks the crop — but unlike a
threshold they are checkable in five seconds by looking at the output, which is why
they are not in the placeholder register: the feedback loop is immediate and visual.

`pdfplumber` reads the text layer to identify the carrier; `pypdf` does the geometry.
Both are installed, not reimplemented — this module is glue and a table of boxes.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

import pdfplumber
import pypdf

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

__all__ = [
    "LABEL_HEIGHT_PT",
    "LABEL_WIDTH_PT",
    "Carrier",
    "CropBox",
    "MergeResult",
    "PreparedLabel",
    "crop_to_label",
    "detect_carrier",
    "merge_labels",
    "prepare_label",
]

POINTS_PER_INCH = 72.0
LABEL_WIDTH_PT = 6 * POINTS_PER_INCH
LABEL_HEIGHT_PT = 4 * POINTS_PER_INCH
"""6x4 inches, the standard thermal label. PDF units are points, 72 to the inch."""


class Carrier(StrEnum):
    ROYAL_MAIL = "royal_mail"
    EVRI = "evri"
    YODEL = "yodel"
    DPD = "dpd"


CARRIER_MARKERS: dict[Carrier, tuple[str, ...]] = {
    Carrier.ROYAL_MAIL: ("royal mail", "royalmail"),
    Carrier.EVRI: ("evri", "hermes"),
    Carrier.YODEL: ("yodel",),
    Carrier.DPD: ("dpd",),
}
"""Lowercased substrings that identify a carrier's page. Evri was Hermes until 2022
and old label templates still say so; matching both costs nothing and a rebrand is
exactly the kind of thing that silently breaks detection."""


class CropBox(NamedTuple):
    """Where a carrier puts the label on their page, in PDF points from bottom-left.

    PDF's origin is bottom-left, not top-left. Getting that backwards crops the
    advertising and throws away the barcode, and the result looks like a real label.
    """

    left: float
    bottom: float


CARRIER_CROPS: dict[Carrier, CropBox] = {
    Carrier.ROYAL_MAIL: CropBox(left=28.0, bottom=430.0),
    Carrier.EVRI: CropBox(left=20.0, bottom=400.0),
    Carrier.YODEL: CropBox(left=24.0, bottom=420.0),
    Carrier.DPD: CropBox(left=30.0, bottom=410.0),
}


class PreparedLabel(NamedTuple):
    source: Path
    carrier: Carrier | None
    page: pypdf.PageObject | None
    cropped: bool


class MergeResult(NamedTuple):
    written: int
    cropped: int
    unidentified: int
    """Pages that went in uncropped. Counted rather than silently passed through:
    an uncropped page reaching the printer unnoticed is how a batch quietly wastes
    a sheet of thermal stock per parcel."""

    failed: int


def detect_carrier(path: Path) -> Carrier | None:
    """Identify the carrier from the page's text layer. `None` when unsure.

    Returns `None` rather than raising on an unreadable or image-only PDF: a
    rasterised label has no text to match, and the caller still wants the page in the
    batch.
    """
    try:
        with pdfplumber.open(path) as pdf:
            if not pdf.pages:
                return None
            text = (pdf.pages[0].extract_text() or "").lower()
    except (OSError, ValueError):
        return None
    for carrier, markers in CARRIER_MARKERS.items():
        if any(marker in text for marker in markers):
            return carrier
    return None


def crop_to_label(path: Path, carrier: Carrier) -> pypdf.PageObject | None:
    """Crop the first page to the 6x4 label region for `carrier`.

    The box is clamped inside the source page. A crop box extending past the page
    renders blank on some printers and raises on others, and both outcomes are a
    parcel that does not ship.
    """
    try:
        reader = pypdf.PdfReader(str(path))
    except (OSError, ValueError, pypdf.errors.PyPdfError):
        return None
    if not reader.pages:
        return None

    page = reader.pages[0]
    box = CARRIER_CROPS[carrier]
    page_width = float(page.mediabox.width)
    page_height = float(page.mediabox.height)

    left = max(0.0, min(box.left, max(page_width - LABEL_WIDTH_PT, 0.0)))
    bottom = max(0.0, min(box.bottom, max(page_height - LABEL_HEIGHT_PT, 0.0)))
    page.mediabox.left = left
    page.mediabox.bottom = bottom
    page.mediabox.right = left + LABEL_WIDTH_PT
    page.mediabox.top = bottom + LABEL_HEIGHT_PT
    return page


def prepare_label(path: Path) -> PreparedLabel:
    """Detect and crop in one step, passing an unknown carrier through uncropped."""
    carrier = detect_carrier(path)
    if carrier is None:
        try:
            reader = pypdf.PdfReader(str(path))
            page = reader.pages[0] if reader.pages else None
        except (OSError, ValueError, pypdf.errors.PyPdfError):
            page = None
        return PreparedLabel(source=path, carrier=None, page=page, cropped=False)
    page = crop_to_label(path, carrier)
    return PreparedLabel(source=path, carrier=carrier, page=page, cropped=page is not None)


def merge_labels(paths: Sequence[Path], destination: Path) -> MergeResult:
    """Crop each label and write them into one printable PDF.

    A source that cannot be read is counted and skipped rather than aborting the
    batch. Losing one label out of thirty is annoying; losing the batch because the
    ninth file was corrupt means re-printing all thirty.
    """
    writer = pypdf.PdfWriter()
    cropped = 0
    unidentified = 0
    failed = 0

    for path in paths:
        if not path.is_file():
            failed += 1
            continue
        prepared = prepare_label(path)
        if prepared.page is None:
            failed += 1
            continue
        writer.add_page(prepared.page)
        if prepared.cropped:
            cropped += 1
        else:
            unidentified += 1

    written = len(writer.pages)
    if written:
        with destination.open("wb") as handle:
            writer.write(handle)
    return MergeResult(written=written, cropped=cropped, unidentified=unidentified, failed=failed)
