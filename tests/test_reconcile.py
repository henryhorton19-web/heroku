"""Settlement parsing and fee reconciliation — the path that closes P1.

Every margin in this tool is computed from invented fee rates. These tests pin the
behaviour that makes replacing them safe: **refuse below a floor, and never silently
drop a fee we are being charged.** A reconciliation that quietly ignores an
unmodelled fee type overstates margin by exactly that amount, permanently, and
nothing downstream can detect it.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from arb.books.reconcile import (
    MIN_SETTLEMENTS,
    corrected_yaml,
    reconcile,
)
from arb.comps.fees import FeeKind, load_fee_table
from arb.money import CurrencyMismatchError
from arb.selling.finances import Settlement, SettlementFee, parse_transactions, sales_only
from tests.conftest import FIXTURES

PAYLOAD = json.loads((FIXTURES / "finances" / "transactions_gb.json").read_text(encoding="utf-8"))
FEES = load_fee_table(Path(__file__).resolve().parent.parent / "src/arb/data/fees/ebay_uk.yaml")


def _settlement(basis: int, fvf: int, rof: int, fixed: int = 30) -> Settlement:
    return Settlement(
        order_id="o",
        line_item_id="l",
        transaction_type="SALE",
        fee_basis_pence=basis,
        fees=(
            SettlementFee("FINAL_VALUE_FEE", fvf),
            SettlementFee("REGULATORY_OPERATING_FEE", rof),
            SettlementFee("FINAL_VALUE_FEE_FIXED_PER_ORDER", fixed),
        ),
    )


def _many(n: int) -> list[Settlement]:
    """n clean sales at a true 12.00% FVF and 0.40% ROF."""
    return [_settlement(basis=4400, fvf=528, rof=18) for _ in range(n)]


# ---------------------------------------------------------------- parsing


def test_parse_reads_the_verified_field_names() -> None:
    settlements = parse_transactions(PAYLOAD)
    assert len(settlements) == 4
    first = settlements[0]
    assert first.order_id == "12-11111-11111"
    assert first.fee_basis_pence == 4400
    assert first.total_fees_pence == 575


def test_fee_types_are_kept_verbatim() -> None:
    """Mapping happens in reconcile. An unfamiliar type must survive parsing or it
    can never be reported."""
    types = {f.fee_type for s in parse_transactions(PAYLOAD) for f in s.fees}
    assert "AD_FEE" in types


def test_refunds_are_parsed_but_distinguishable() -> None:
    settlements = parse_transactions(PAYLOAD)
    assert len(sales_only(settlements)) == 3
    assert any(not s.is_sale for s in settlements)


def test_a_non_gbp_settlement_is_refused_not_converted() -> None:
    """A EUR fee folded into a GBP fit is a wrong answer wearing the right units."""
    payload = {
        "transactions": [
            {
                "orderId": "o",
                "transactionType": "SALE",
                "orderLineItems": [
                    {
                        "lineItemId": "l",
                        "feeBasisAmount": {"value": "40.00", "currency": "EUR"},
                        "marketplaceFees": [],
                    }
                ],
            }
        ]
    }
    with pytest.raises(CurrencyMismatchError):
        parse_transactions(payload)


def test_a_malformed_payload_is_empty_not_an_exception() -> None:
    assert parse_transactions({}) == ()
    assert parse_transactions([]) == ()
    assert parse_transactions({"transactions": [{"orderId": "o"}]}) == ()


# ---------------------------------------------------------------- refusing


def test_reconcile_refuses_below_the_settlement_floor() -> None:
    """Rewriting a fee table from three sales replaces a guess with a guess that now
    carries the authority of having been measured."""
    assert reconcile(sales_only(parse_transactions(PAYLOAD)), FEES) is None


def test_the_floor_is_the_documented_one() -> None:
    assert reconcile(_many(MIN_SETTLEMENTS - 1), FEES) is None
    assert reconcile(_many(MIN_SETTLEMENTS), FEES) is not None


def test_refunds_do_not_count_toward_the_floor() -> None:
    """A refunded order has fees credited back, so it is not a reading of the fee
    schedule. Counting it would let refunds unlock a correction."""
    refund = Settlement(
        order_id="r",
        line_item_id="l",
        transaction_type="REFUND",
        fee_basis_pence=2200,
        fees=(SettlementFee("FINAL_VALUE_FEE_FIXED_PER_ORDER", 30),),
    )
    assert reconcile([*_many(MIN_SETTLEMENTS - 1), refund], FEES) is None


def test_refunds_are_counted_and_reported() -> None:
    refund = Settlement(
        order_id="r", line_item_id="l", transaction_type="REFUND", fee_basis_pence=2200, fees=()
    )
    result = reconcile([*_many(MIN_SETTLEMENTS), refund], FEES)
    assert result is not None
    assert result.refunds_excluded == 1
    assert result.settlements_used == MIN_SETTLEMENTS


# ---------------------------------------------------------------- fitting


def test_the_measured_rate_replaces_the_assumed_one() -> None:
    result = reconcile(_many(MIN_SETTLEMENTS), FEES)
    assert result is not None
    fvf = next(f for f in result.fits if f.name == "final_value_fee")
    assert fvf.assumed == Decimal("0.1250")
    assert fvf.measured == Decimal("0.1200")
    assert fvf.materially_different


def test_a_fixed_component_is_fitted_in_pence_not_as_a_rate() -> None:
    result = reconcile(_many(MIN_SETTLEMENTS), FEES)
    assert result is not None
    fixed = next(f for f in result.fits if f.name == "fixed_order_fee")
    assert fixed.kind is FeeKind.FIXED
    assert fixed.measured == Decimal(30)
    assert not fixed.materially_different


def test_the_median_resists_one_odd_order() -> None:
    """A single discounted or promoted order should not move the rate. With a small
    sample the mean is exactly what it would move."""
    outlier = _settlement(basis=4400, fvf=2000, rof=18)
    result = reconcile([*_many(MIN_SETTLEMENTS), outlier], FEES)
    assert result is not None
    fvf = next(f for f in result.fits if f.name == "final_value_fee")
    assert fvf.measured == Decimal("0.1200")


def test_drift_is_signed_so_direction_is_visible() -> None:
    result = reconcile(_many(MIN_SETTLEMENTS), FEES)
    assert result is not None
    fvf = next(f for f in result.fits if f.name == "final_value_fee")
    assert fvf.drift < 0


# ---------------------------------------------------------------- unmodelled fees


def test_an_unmodelled_fee_type_is_reported_not_dropped() -> None:
    """The single most important test here. A fee we are charged but do not model
    overstates every margin by that amount, forever, undetectably."""
    promoted = _many(MIN_SETTLEMENTS)
    promoted[0] = promoted[0]._replace(fees=(*promoted[0].fees, SettlementFee("AD_FEE", 106)))
    result = reconcile(promoted, FEES)
    assert result is not None
    assert ("AD_FEE", 1) in result.unmodelled
    assert result.needs_rewrite


def test_unmodelled_fees_are_counted_in_the_realised_total() -> None:
    """Excluding them from the total would make the drift look smaller than it is."""
    plain = reconcile(_many(MIN_SETTLEMENTS), FEES)
    promoted = _many(MIN_SETTLEMENTS)
    promoted[0] = promoted[0]._replace(fees=(*promoted[0].fees, SettlementFee("AD_FEE", 106)))
    with_ad = reconcile(promoted, FEES)
    assert plain is not None
    assert with_ad is not None
    assert with_ad.realised_total_pence == plain.realised_total_pence + 106


def test_predicted_versus_realised_is_reported_in_pence() -> None:
    result = reconcile(_many(MIN_SETTLEMENTS), FEES)
    assert result is not None
    assert result.predicted_total_pence > 0
    assert result.realised_total_pence > 0
    assert result.total_drift_pence == (result.realised_total_pence - result.predicted_total_pence)


# ---------------------------------------------------------------- rewriting


def test_the_corrected_table_lifts_provisional() -> None:
    """This flag is exactly what `arb provenance` reads to close P1."""
    result = reconcile(_many(MIN_SETTLEMENTS), FEES)
    assert result is not None
    rendered = corrected_yaml(FEES, result, verified_at="2026-08-20")
    assert "provisional: false" in rendered
    assert FEES.provisional is True


def test_the_corrected_table_round_trips_and_changes_version(tmp_path: Path) -> None:
    """A rewrite must produce a new content hash, or two different fee assumptions
    share an audit trail."""
    result = reconcile(_many(MIN_SETTLEMENTS), FEES)
    assert result is not None
    path = tmp_path / "ebay_uk.yaml"
    path.write_text(corrected_yaml(FEES, result, verified_at="2026-08-20"), encoding="utf-8")
    reloaded = load_fee_table(path)
    assert reloaded.provisional is False
    assert reloaded.version != FEES.version


def test_the_corrected_table_actually_charges_the_measured_rate(tmp_path: Path) -> None:
    result = reconcile(_many(MIN_SETTLEMENTS), FEES)
    assert result is not None
    path = tmp_path / "ebay_uk.yaml"
    path.write_text(corrected_yaml(FEES, result, verified_at="2026-08-20"), encoding="utf-8")
    reloaded = load_fee_table(path)
    assert reloaded.fees_pence(4400) < FEES.fees_pence(4400)


def test_unmodelled_fees_are_carried_into_the_written_table_as_a_warning(
    tmp_path: Path,
) -> None:
    """A fee we cannot cost should not vanish just because the file was rewritten."""
    promoted = _many(MIN_SETTLEMENTS)
    promoted[0] = promoted[0]._replace(fees=(*promoted[0].fees, SettlementFee("AD_FEE", 106)))
    result = reconcile(promoted, FEES)
    assert result is not None
    rendered = corrected_yaml(FEES, result, verified_at="2026-08-20")
    assert "UNMODELLED: AD_FEE" in rendered
    path = tmp_path / "ebay_uk.yaml"
    path.write_text(rendered, encoding="utf-8")
    assert load_fee_table(path).provisional is False
