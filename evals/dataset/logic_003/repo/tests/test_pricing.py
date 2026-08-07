from pricing import apply_discount


def test_discount():
    assert apply_discount(100, 20) == 80


def test_zero_discount():
    assert apply_discount(50, 0) == 50
