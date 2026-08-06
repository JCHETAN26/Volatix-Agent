"""Stack-trace trimming.

The fixtures below are verbatim pytest output captured from real runs, not invented
samples -- a parser tested only against hand-written input tends to break on the first
real failure it sees.
"""

import textwrap

from agent.nodes.stack_parser import (
    MAX_CHARS,
    parse_pytest_output,
    trim_stack_trace,
)

ASSERTION_AND_EXCEPTION = textwrap.dedent("""\
    FF.                                                                      [100%]
    =================================== FAILURES ===================================
    ___________________________________ test_add ___________________________________

        def test_add():
    >       assert add(2, 3) == 5
    E       assert -1 == 5
    E        +  where -1 = add(2, 3)

    tests/test_calc.py:5: AssertionError
    __________________________________ test_boom ___________________________________

        def test_boom():
    >       assert boom() == 1
                   ^^^^^^

    tests/test_calc.py:9:
    _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

        def boom():
    >       return 1 / 0
                   ^^^^^
    E       ZeroDivisionError: division by zero

    calc.py:6: ZeroDivisionError
    =========================== short test summary info ============================
    FAILED tests/test_calc.py::test_add - assert -1 == 5
    FAILED tests/test_calc.py::test_boom - ZeroDivisionError: division by zero
    2 failed, 1 passed in 0.02s
    """)

COLLECTION_ERROR = textwrap.dedent("""\
    ==================================== ERRORS ====================================
    _____________________ ERROR collecting tests/test_calc.py ______________________
    /repo/venv/lib/python3.14/site-packages/_pytest/python.py:508: in importtestmodule
        mod = import_path(
    /repo/venv/lib/python3.14/site-packages/_pytest/pathlib.py:596: in import_path
        importlib.import_module(module_name)
    /opt/python@3.14/lib/python3.14/importlib/__init__.py:88: in import_module
        return _bootstrap._gcd_import(name[level:], package, level)
    <frozen importlib._bootstrap>:1406: in _gcd_import
        ???
    /repo/venv/lib/python3.14/site-packages/_pytest/assertion/rewrite.py:188: in exec_module
        exec(co, module.__dict__)
    tests/test_calc.py:1: in <module>
        from calc import add, boom
    E     File "/repo/calc.py", line 1
    E       def add(a, b)
    E                    ^
    E   SyntaxError: expected ':'
    =========================== short test summary info ============================
    ERROR tests/test_calc.py
    !!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
    1 error in 0.11s
    """)

ALL_PASSING = (
    "...                                                          [100%]\n3 passed in 0.00s\n"
)


# --- assertion and exception failures ---------------------------------------------


def test_both_failures_are_found():
    result = parse_pytest_output(ASSERTION_AND_EXCEPTION)
    assert len(result.failures) == 2


def test_assertion_detail_is_kept():
    rendered = trim_stack_trace(ASSERTION_AND_EXCEPTION)
    assert "assert -1 == 5" in rendered
    assert "where -1 = add(2, 3)" in rendered


def test_exception_type_is_kept():
    assert "ZeroDivisionError: division by zero" in trim_stack_trace(ASSERTION_AND_EXCEPTION)


def test_location_points_at_the_innermost_user_frame():
    """test_boom fails at tests/test_calc.py:9 but the bug is at calc.py:6."""
    result = parse_pytest_output(ASSERTION_AND_EXCEPTION)
    boom = next(f for f in result.failures if "boom" in f.test_id)
    assert boom.location == "calc.py:6"


def test_full_node_ids_are_recovered_from_the_summary():
    result = parse_pytest_output(ASSERTION_AND_EXCEPTION)
    assert {f.test_id for f in result.failures} == {
        "tests/test_calc.py::test_add",
        "tests/test_calc.py::test_boom",
    }


def test_counts_line_is_preserved():
    assert "2 failed, 1 passed" in trim_stack_trace(ASSERTION_AND_EXCEPTION)


def test_progress_noise_is_dropped():
    rendered = trim_stack_trace(ASSERTION_AND_EXCEPTION)
    assert "[100%]" not in rendered
    assert "FF." not in rendered


def test_trimming_actually_shrinks_the_output():
    trimmed = trim_stack_trace(ASSERTION_AND_EXCEPTION)
    assert len(trimmed) < len(ASSERTION_AND_EXCEPTION) / 2


# --- collection errors ------------------------------------------------------------


def test_syntax_error_detail_survives():
    rendered = trim_stack_trace(COLLECTION_ERROR)
    assert "SyntaxError: expected ':'" in rendered
    assert "def add(a, b)" in rendered


def test_framework_frames_are_stripped():
    """The whole point: 20 lines of import machinery must not reach the model."""
    rendered = trim_stack_trace(COLLECTION_ERROR)
    for noise in ("site-packages", "_pytest", "<frozen", "importlib", "_bootstrap"):
        assert noise not in rendered, f"{noise!r} leaked into the trimmed output"


def test_collection_error_is_reported_as_a_failure():
    result = parse_pytest_output(COLLECTION_ERROR)
    assert len(result.failures) == 1
    assert "test_calc.py" in result.failures[0].test_id


def test_collection_error_shrinks_dramatically():
    assert len(trim_stack_trace(COLLECTION_ERROR)) < len(COLLECTION_ERROR) / 3


# --- non-failure and unrecognised input -------------------------------------------


def test_passing_output_reports_no_failures():
    result = parse_pytest_output(ALL_PASSING)
    assert result.parsed is False
    assert "3 passed" in result.counts


def test_unrecognised_output_falls_back_to_the_raw_tail():
    """Returning nothing here would silently break the retry loop."""
    raw = "Sandbox could not run the tests: Docker daemon unreachable"
    assert "Docker daemon unreachable" in trim_stack_trace(raw)


def test_segfault_style_output_is_passed_through():
    raw = "Fatal Python error: Segmentation fault\nCurrent thread 0x00007f...\n"
    assert "Segmentation fault" in trim_stack_trace(raw)


def test_empty_output_is_reported_explicitly():
    assert "no output" in trim_stack_trace("")
    assert "no output" in trim_stack_trace("   \n  ")


def test_output_is_capped():
    result = trim_stack_trace("noise line\n" * 5000)
    assert len(result) <= MAX_CHARS + 40


def test_fallback_keeps_the_end_not_the_beginning():
    """The cause sits near the bottom of a long log."""
    raw = "filler\n" * 2000 + "RootCauseMarker: the real problem\n"
    assert "RootCauseMarker" in trim_stack_trace(raw)


def test_many_failures_are_capped_with_a_count():
    blocks = "".join(
        f"____ test_{i} ____\n\nE       assert {i} == 0\n\ntests/t.py:{i}: AssertionError\n"
        for i in range(10)
    )
    rendered = trim_stack_trace(f"=== FAILURES ===\n{blocks}10 failed in 1s\n")
    assert "more failure(s) not shown" in rendered
