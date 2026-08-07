from ratio import rate


def test_zero_total():
    assert rate(0, 10) == 0.0


def test_normal():
    assert rate(50, 25) == 50.0
