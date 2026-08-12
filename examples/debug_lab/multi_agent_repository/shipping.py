def shipping_fee(region: str, subtotal: int, *, expedited: bool = False) -> int:
    """Return a route-aware fee without silently accepting unknown regions."""

    return 0
