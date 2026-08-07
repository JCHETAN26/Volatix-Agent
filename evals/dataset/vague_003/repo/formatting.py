def as_currency(amount):
    return f"${amount:,.2f}"


def as_percent(fraction):
    return f"{fraction * 100:.1f}%"
