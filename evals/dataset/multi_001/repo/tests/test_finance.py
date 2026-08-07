from rates import usd_to_eur
from fees import with_fee


def test_rate():
    assert usd_to_eur() == 0.9


def test_fee():
    assert with_fee(10.0) == 12.0
