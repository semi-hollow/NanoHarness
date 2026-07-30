from pricing import final_price
from shipping import shipping_fee


def test_checkout_total() -> None:
    assert final_price(100, 20) + shipping_fee() == 85
