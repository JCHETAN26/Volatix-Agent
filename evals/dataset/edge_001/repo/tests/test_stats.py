from stats import average


def test_empty():
    assert average([]) == 0.0


def test_values():
    assert average([2, 4]) == 3.0
