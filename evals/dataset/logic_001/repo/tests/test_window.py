from window import last_n


def test_last_n():
    assert last_n([1, 2, 3, 4, 5], 3) == [3, 4, 5]


def test_last_n_all():
    assert last_n([1, 2], 2) == [1, 2]
