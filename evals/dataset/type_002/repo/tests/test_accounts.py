from accounts import display_name


def test_missing_nickname():
    assert display_name(None, "anon") == "anon"


def test_present():
    assert display_name("  ada ", "anon") == "ada"
