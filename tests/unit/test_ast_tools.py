"""AST outline extraction -- signatures kept, bodies discarded."""

import textwrap

from mcp_server.ast_tools import extract_outline

SAMPLE = textwrap.dedent('''
    """Module docstring.

    Second paragraph that should not appear.
    """
    import os
    from typing import Optional

    MAX_RETRIES = 3
    TIMEOUT: int = 30

    def parse(payload: dict, strict: bool = False) -> Optional[str]:
        """Parse a payload."""
        secret = payload["x"]
        return secret

    async def fetch(url: str) -> bytes:
        return b""

    @dataclass
    class Payment:
        """A payment."""

        def __init__(self, amount: int):
            self.amount = amount

        @property
        def doubled(self) -> int:
            return self.amount * 2
    ''')


def outline():
    return extract_outline(SAMPLE, path="pkg/pay.py")


def test_parses_cleanly():
    assert outline().parsed is True
    assert outline().syntax_error is None


def test_bodies_are_stripped():
    """The whole point: implementation must not reach the context window."""
    rendered = outline().render()
    assert "secret = payload" not in rendered
    assert "self.amount * 2" not in rendered


def test_signatures_are_preserved():
    rendered = outline().render()
    assert "def parse(payload: dict, strict: bool=False) -> Optional[str]:" in rendered
    assert "async def fetch(url: str) -> bytes:" in rendered


def test_class_and_methods_are_captured():
    result = outline()
    names = [s.name for s in result.symbols]
    assert "Payment" in names
    assert [m.name for m in result.children["Payment"]] == ["__init__", "doubled"]


def test_decorators_are_recorded():
    result = outline()
    payment = next(s for s in result.symbols if s.name == "Payment")
    doubled = next(m for m in result.children["Payment"] if m.name == "doubled")
    assert payment.decorators == ["dataclass"]
    assert doubled.decorators == ["property"]


def test_imports_and_constants():
    result = outline()
    assert result.imports == ["import os", "from typing import Optional"]
    assert result.constants == ["MAX_RETRIES", "TIMEOUT"]


def test_only_first_docstring_line_is_kept():
    result = outline()
    assert result.docstring == "Module docstring."
    assert "Second paragraph" not in result.render()


def test_line_ranges_point_at_the_source():
    result = outline()
    parse = next(s for s in result.symbols if s.name == "parse")
    assert parse.lineno < parse.end_lineno
    assert f"L{parse.lineno}-{parse.end_lineno}" in result.render()


def test_outline_is_much_smaller_than_the_source():
    assert len(outline().render()) < len(SAMPLE)


# --- syntax errors are data, not failures ----------------------------------------


def test_syntax_error_is_returned_not_raised():
    result = extract_outline("def broken(\n", path="bad.py")

    assert result.parsed is False
    assert result.syntax_error is not None
    assert result.syntax_error.lineno is not None


def test_syntax_error_renders_with_location():
    rendered = extract_outline("def f():\nreturn 1\n", path="bad.py").render()

    assert "SyntaxError" in rendered
    assert "bad.py" in rendered


def test_syntax_error_render_includes_a_caret():
    rendered = extract_outline("x = (1 + \n", path="bad.py").render()
    assert "^" in rendered


# --- edge cases ------------------------------------------------------------------


def test_empty_module():
    result = extract_outline("", path="empty.py")
    assert result.parsed is True
    assert result.symbols == []
    assert result.render() == "# empty.py"


def test_class_with_bases_and_keywords():
    result = extract_outline("class A(B, metaclass=M):\n    pass\n")
    assert result.symbols[0].signature == "class A(B, metaclass=M):"


def test_nested_functions_are_not_promoted():
    """A closure is an implementation detail, not part of the module's shape."""
    result = extract_outline("def outer():\n    def inner():\n        pass\n")
    assert [s.name for s in result.symbols] == ["outer"]
