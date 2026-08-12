import pytest

from pricing import final_price
from shipping import shipping_fee


def test_checkout_total() -> None:
    assert final_price(100, 20) + shipping_fee("domestic", 80) == 85


def test_standard_domestic_shipping_is_free_at_threshold() -> None:
    assert shipping_fee("domestic", 100) == 0


def test_expedited_shipping_is_not_mistaken_for_free_shipping() -> None:
    assert shipping_fee("domestic", 120, expedited=True) == 15


@pytest.mark.parametrize(
    ("subtotal", "discount"),
    [(-1, 0), (100, -1), (100, 101)],
)
def test_invalid_pricing_inputs_fail_closed(subtotal: int, discount: int) -> None:
    with pytest.raises(ValueError):
        final_price(subtotal, discount)


def test_unknown_shipping_region_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported region"):
        shipping_fee("moon", 80)
