from decimal import Decimal

from metal_calc.pricing import _round_price


def test_half_up_and_bankers_are_distinct_at_exact_half_ruble() -> None:
    assert _round_price(Decimal("2.50"), "half_up") == Decimal("3")
    assert _round_price(Decimal("2.50"), "bankers") == Decimal("2")


def test_half_up_rounds_below_half_down() -> None:
    assert _round_price(Decimal("2.49"), "half_up") == Decimal("2")


def test_half_up_rounds_negative_half_away_from_zero() -> None:
    assert _round_price(Decimal("-2.50"), "half_up") == Decimal("-3")
