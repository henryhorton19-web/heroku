"""Fee table tests. Money maths, so property-tested."""

from __future__ import annotations

from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from arb.comps.fees import FeeTable, load_fee_table
from arb.protocols import FeeModel

FEE_DIR = Path(__file__).resolve().parent.parent / "src" / "arb" / "data" / "fees"


def _table() -> FeeTable:
    return load_fee_table(FEE_DIR / "ebay_uk.yaml")


def test_shipped_tables_load() -> None:
    for path in sorted(FEE_DIR.glob("*.yaml")):
        assert load_fee_table(path).components


def test_fee_table_satisfies_the_protocol() -> None:
    model: FeeModel = _table()
    assert isinstance(model, FeeModel)


def test_version_is_venue_and_content_hash() -> None:
    table = _table()
    assert table.version.startswith("ebay_uk@")
    assert len(table.version.split("@")[1]) == 12


def test_editing_the_file_changes_the_version(tmp_path: Path) -> None:
    """The audit trail depends on this. A changed assumption must be a new version."""
    original = (FEE_DIR / "ebay_uk.yaml").read_text(encoding="utf-8")
    a = tmp_path / "a.yaml"
    a.write_text(original, encoding="utf-8")
    b = tmp_path / "b.yaml"
    b.write_text(original + "\n# a trailing comment is still a change\n", encoding="utf-8")
    assert load_fee_table(a).version != load_fee_table(b).version


def test_shipped_tables_are_marked_provisional() -> None:
    """Until settlement data is measured, every number is an assumption.
    If this test ever fails, someone has claimed verification -- check they did it."""
    for path in sorted(FEE_DIR.glob("*.yaml")):
        table = load_fee_table(path)
        assert table.provisional
        assert table.verified_at is None


def test_per_order_components_do_not_multiply_with_qty() -> None:
    """The bundle seam: buying five of a thing pays one order fee, not five."""
    table = _table()
    one = table.fees_pence(10_00, qty=1)
    five = table.fees_pence(10_00, qty=5)
    assert five < one * 5


def test_per_item_components_do_multiply() -> None:
    table = _table()
    item_only = [c for c in table.components if c.scope.value == "item"]
    assert item_only
    charged = sum(c.charge_pence(10_00, 3) for c in item_only)
    assert charged == sum(c.charge_pence(10_00, 1) for c in item_only) * 3


@given(st.integers(min_value=0, max_value=5_000_00), st.integers(min_value=1, max_value=50))
def test_fees_are_non_negative_integers(price: int, qty: int) -> None:
    fee = _table().fees_pence(price, qty=qty)
    assert isinstance(fee, int)
    assert fee >= 0


@given(
    st.integers(min_value=0, max_value=1_000_00),
    st.integers(min_value=0, max_value=1_000_00),
)
def test_fees_are_non_decreasing_in_price(a: int, b: int) -> None:
    """Non-decreasing, not strictly increasing. Per-penny rounding means a 0p and a
    1p item carry the same fee, which is correct -- the earlier biconditional form of
    this test was wrong about the maths, not about the code."""
    table = _table()
    lo, hi = sorted((a, b))
    assert table.fees_pence(lo) <= table.fees_pence(hi)


@pytest.mark.parametrize(("price", "qty"), [(-1, 1), (100, 0), (100, -3)])
def test_invalid_inputs_raise(price: int, qty: int) -> None:
    with pytest.raises(ValueError, match=r"cannot be negative|at least 1"):
        _table().fees_pence(price, qty=qty)


def test_percentage_rate_above_one_is_rejected() -> None:
    """12.5% is 0.125. Writing 12.5 would silently charge 1250% and still 'work'."""
    with pytest.raises(ValidationError, match=r"outside 0\.\.1"):
        FeeTable.model_validate(
            {
                "venue": "x",
                "content_hash": "deadbeefcafe",
                "components": [{"name": "f", "kind": "percentage", "rate": "12.5"}],
            }
        )


def test_percentage_without_rate_is_rejected() -> None:
    with pytest.raises(ValidationError, match="needs a rate"):
        FeeTable.model_validate(
            {
                "venue": "x",
                "content_hash": "deadbeefcafe",
                "components": [{"name": "f", "kind": "percentage"}],
            }
        )


def test_fixed_without_amount_is_rejected() -> None:
    with pytest.raises(ValidationError, match="needs amount_pence"):
        FeeTable.model_validate(
            {
                "venue": "x",
                "content_hash": "deadbeefcafe",
                "components": [{"name": "f", "kind": "fixed"}],
            }
        )


def test_non_mapping_yaml_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- not\n- a mapping\n", encoding="utf-8")
    with pytest.raises(TypeError, match="must be a YAML mapping"):
        load_fee_table(bad)
