from eligibility import is_adult


def test_boundary():
    assert is_adult(18) is True


def test_below():
    assert is_adult(17) is False
