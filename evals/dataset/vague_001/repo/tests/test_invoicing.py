from invoicing import invoice_total


def test_total():
    assert invoice_total([{"price": 1.0}, {"price": 2.0}]) == 3.0


def test_single_item():
    assert invoice_total([{"price": 5.0}]) == 5.0
