from chunker import chunk


def test_partial_tail():
    assert chunk([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_exact():
    assert chunk([1, 2, 3, 4], 2) == [[1, 2], [3, 4]]
