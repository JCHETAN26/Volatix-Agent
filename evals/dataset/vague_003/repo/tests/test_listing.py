from listing import sort_names


def test_case_insensitive_order():
    assert sort_names(["banana", "Apple", "cherry"]) == ["Apple", "banana", "cherry"]


def test_already_sorted():
    assert sort_names(["a", "B"]) == ["a", "B"]
