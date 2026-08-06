from labels import build_label


def test_label():
    assert build_label("widget", 3) == "widget x3"
