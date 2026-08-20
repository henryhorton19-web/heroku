"""Shipping labels: detect the carrier, crop the label, merge to a 6x4 batch.

Carriers hand you an A4 page with a label somewhere on it and the rest whitespace,
advertising, or a returns slip. A thermal printer wants 6x4 inches of label and
nothing else. Printing the A4 straight gives you a postage-stamp-sized barcode that
scanners reject.

The asymmetry that shapes every choice here: **a mis-cropped label is a parcel that
does not ship.** It fails at the counter, after you have packed it, and the sale is
already made. So detection refuses rather than guesses — an unrecognised carrier
returns `None` and the page passes through uncropped, which prints badly and is
obvious, rather than being cropped to the wrong box and printing plausibly.

Fixtures are generated rather than vendored: real carrier labels carry live tracking
barcodes and customer addresses, and committing one to a public repo would leak both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pypdf
import pytest
from pypdf.generic import DecodedStreamObject, NameObject

from arb.selling.labels import (
    LABEL_HEIGHT_PT,
    LABEL_WIDTH_PT,
    Carrier,
    crop_to_label,
    detect_carrier,
    merge_labels,
    prepare_label,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

A4 = (595.0, 842.0)


def _page_pdf(path: Path, text: str, size: tuple[float, float] = A4) -> Path:
    """A one-page PDF carrying `text`. Built with pypdf so no fixture is vendored."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=size[0], height=size[1])
    # pypdf cannot draw text, so the marker goes in the page's content stream via a
    # minimal Tj operator. pdfplumber reads it back as extractable text.
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode())
    page = writer.pages[0]
    page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


@pytest.fixture
def royal_mail(tmp_path: Path) -> Path:
    return _page_pdf(tmp_path / "rm.pdf", "Royal Mail Tracked 48 postage paid")


@pytest.fixture
def evri(tmp_path: Path) -> Path:
    return _page_pdf(tmp_path / "evri.pdf", "Evri ParcelShop drop off")


@pytest.fixture
def unknown(tmp_path: Path) -> Path:
    return _page_pdf(tmp_path / "unknown.pdf", "Some other courier we do not know")


# ---------------------------------------------------------------- detection


def test_royal_mail_is_detected(royal_mail: Path) -> None:
    assert detect_carrier(royal_mail) is Carrier.ROYAL_MAIL


def test_evri_is_detected(evri: Path) -> None:
    assert detect_carrier(evri) is Carrier.EVRI


def test_an_unknown_carrier_refuses_rather_than_guessing(unknown: Path) -> None:
    """Guessing produces a plausible-looking crop of the wrong region. That prints,
    ships, and fails at the counter after the parcel is packed."""
    assert detect_carrier(unknown) is None


def test_detection_is_case_insensitive(tmp_path: Path) -> None:
    assert detect_carrier(_page_pdf(tmp_path / "c.pdf", "ROYAL MAIL")) is Carrier.ROYAL_MAIL


def test_a_pdf_with_no_text_layer_is_not_a_crash(tmp_path: Path) -> None:
    """A rasterised label has no extractable text. Refusing beats raising: the caller
    still wants the page in the batch, uncropped."""
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=A4[0], height=A4[1])
    path = tmp_path / "scan.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    assert detect_carrier(path) is None


# ---------------------------------------------------------------- cropping


def test_a_cropped_page_is_six_by_four(royal_mail: Path) -> None:
    page = crop_to_label(royal_mail, Carrier.ROYAL_MAIL)
    assert page is not None
    box = page.mediabox
    assert round(float(box.width)) == round(LABEL_WIDTH_PT)
    assert round(float(box.height)) == round(LABEL_HEIGHT_PT)


def test_cropping_stays_inside_the_source_page(royal_mail: Path) -> None:
    """A crop box outside the page renders blank on some printers and raises on
    others. Either way the parcel does not ship."""
    page = crop_to_label(royal_mail, Carrier.ROYAL_MAIL)
    assert page is not None
    assert float(page.mediabox.left) >= 0
    assert float(page.mediabox.bottom) >= 0


def test_each_carrier_crops_a_different_region(royal_mail: Path) -> None:
    rm = crop_to_label(royal_mail, Carrier.ROYAL_MAIL)
    ev = crop_to_label(royal_mail, Carrier.EVRI)
    assert rm is not None
    assert ev is not None
    assert (float(rm.mediabox.left), float(rm.mediabox.bottom)) != (
        float(ev.mediabox.left),
        float(ev.mediabox.bottom),
    )


# ---------------------------------------------------------------- prepare


def test_prepare_detects_and_crops_in_one_step(royal_mail: Path) -> None:
    prepared = prepare_label(royal_mail)
    assert prepared.carrier is Carrier.ROYAL_MAIL
    assert prepared.cropped


def test_an_unknown_carrier_passes_through_uncropped(unknown: Path) -> None:
    """The page still belongs in the batch. It prints badly and visibly, which is the
    failure you want -- unlike a wrong crop, which prints well and fails later."""
    prepared = prepare_label(unknown)
    assert prepared.carrier is None
    assert not prepared.cropped
    assert prepared.page is not None


# ---------------------------------------------------------------- merging


def test_merging_produces_one_page_per_label(royal_mail: Path, evri: Path, tmp_path: Path) -> None:
    out = tmp_path / "batch.pdf"
    result = merge_labels([royal_mail, evri], out)
    assert result.written == 2
    assert len(pypdf.PdfReader(str(out)).pages) == 2


def test_merging_reports_what_it_could_not_identify(
    royal_mail: Path, unknown: Path, tmp_path: Path
) -> None:
    """A silent pass-through is how an uncropped page reaches the printer unnoticed."""
    result = merge_labels([royal_mail, unknown], tmp_path / "batch.pdf")
    assert result.cropped == 1
    assert result.unidentified == 1


def test_merging_nothing_writes_nothing(tmp_path: Path) -> None:
    out = tmp_path / "batch.pdf"
    empty: Sequence[Path] = []
    assert merge_labels(empty, out).written == 0
    assert not out.exists()


def test_a_missing_source_is_reported_not_raised(royal_mail: Path, tmp_path: Path) -> None:
    result = merge_labels([royal_mail, tmp_path / "gone.pdf"], tmp_path / "batch.pdf")
    assert result.written == 1
    assert result.failed == 1
