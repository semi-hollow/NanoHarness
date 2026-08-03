"""不同币种的金额精度规则。"""

from decimal import Decimal, ROUND_DOWN


CURRENCY_SCALE = {
    "CNY": Decimal("0.01"),
    "USD": Decimal("0.01"),
    "JPY": Decimal("1"),
}


def normalize_amount(amount: Decimal, currency: str) -> Decimal:
    """在进入账本前把金额归一到币种精度。"""

    normalized_currency = currency.strip().upper()
    try:
        scale = CURRENCY_SCALE[normalized_currency]
    except KeyError as exc:
        raise ValueError(f"unsupported currency: {currency}") from exc
    return amount.quantize(scale, rounding=ROUND_DOWN)
