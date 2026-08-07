from normalize import normalize
from dedupe import unique


def test_normalize():
    assert normalize("  Ada ") == "ada"


def test_unique_preserves_order():
    assert unique(["b", "a", "b", "c"]) == ["b", "a", "c"]
