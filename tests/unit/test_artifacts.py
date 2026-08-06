"""Diff and root-cause report generation."""

from agent.artifacts import (
    DIFF_FILENAME,
    REPORT_FILENAME,
    RunArtifacts,
    build_artifacts,
    build_report,
    build_unified_diff,
    missing_baselines,
)
from agent.state import build_initial_state

BEFORE = "def add(a, b):\n    return a - b\n"
AFTER = "def add(a, b):\n    return a + b\n"


def finished_state(**overrides):
    state = build_initial_state("add() returns the wrong value", "/repo", max_retries=3)
    state.update(
        {
            "plan": ["Fix the operator in calc.add"],
            "analysis": "The body computes a - b instead of a + b.",
            "target_files": ["calc.py"],
            "modified_files": {"calc.py": AFTER},
            "test_output": "1 passed in 0.01s",
            "test_passed": True,
            "retry_count": 1,
            "token_usage": {
                "input_tokens": 6234,
                "output_tokens": 529,
                "total_tokens": 6763,
                "calls": 4,
            },
        }
    )
    state.update(overrides)
    return state


# --- unified diff ----------------------------------------------------------------


def test_diff_has_git_style_headers():
    diff = build_unified_diff({"calc.py": BEFORE}, {"calc.py": AFTER})
    assert "--- a/calc.py" in diff
    assert "+++ b/calc.py" in diff


def test_diff_contains_the_change():
    diff = build_unified_diff({"calc.py": BEFORE}, {"calc.py": AFTER})
    assert "-    return a - b" in diff
    assert "+    return a + b" in diff


def test_created_file_diffs_against_dev_null():
    """git apply needs /dev/null on the from-side or the patch will not apply."""
    diff = build_unified_diff({"new.py": None}, {"new.py": "x = 1\n"})
    assert "--- /dev/null" in diff
    assert "+++ b/new.py" in diff
    assert "+x = 1" in diff


def test_unchanged_file_produces_no_hunk():
    assert build_unified_diff({"calc.py": AFTER}, {"calc.py": AFTER}) == ""


def test_multiple_files_are_ordered_deterministically():
    diff = build_unified_diff({"b.py": "1\n", "a.py": "1\n"}, {"b.py": "2\n", "a.py": "2\n"})
    assert diff.index("a/a.py") < diff.index("a/b.py")


def test_missing_trailing_newline_is_annotated():
    diff = build_unified_diff({"f.py": "x = 1\n"}, {"f.py": "x = 2"})
    assert "No newline at end of file" in diff


def test_empty_inputs_produce_an_empty_diff():
    assert build_unified_diff({}, {}) == ""


def test_absent_baseline_is_not_treated_as_a_new_file():
    """An unrecorded baseline must not yield a patch claiming an existing file is new."""
    diff = build_unified_diff({}, {"calc.py": AFTER})
    assert diff == ""
    assert "/dev/null" not in diff


def test_absent_baseline_differs_from_an_explicit_none():
    """None means 'created by the run'; a missing key means 'unknown'."""
    created = build_unified_diff({"new.py": None}, {"new.py": "x = 1\n"})
    unknown = build_unified_diff({}, {"new.py": "x = 1\n"})
    assert "/dev/null" in created
    assert unknown == ""


def test_missing_baselines_are_reported():
    assert missing_baselines({"a.py": "x"}, {"a.py": "y", "b.py": "z"}) == ["b.py"]
    assert missing_baselines({"a.py": None}, {"a.py": "y"}) == []


# --- report ----------------------------------------------------------------------


def test_report_marks_a_successful_run():
    report = build_report(finished_state())
    assert "Resolved" in report
    assert "✅" in report


def test_report_marks_a_failed_run():
    report = build_report(finished_state(test_passed=False))
    assert "Unresolved" in report
    assert "retry budget was exhausted" in report


def test_report_includes_analysis_plan_and_files():
    report = build_report(finished_state())
    assert "The body computes a - b instead of a + b." in report
    assert "1. Fix the operator in calc.add" in report
    assert "`calc.py`" in report


def test_report_records_retries_and_tokens():
    report = build_report(finished_state())
    assert "1 of 3" in report
    assert "6,763 tokens" in report
    assert "4 model calls" in report


def test_report_flags_a_planner_executor_target_mismatch():
    """A plan that aimed elsewhere is a review signal worth surfacing."""
    report = build_report(finished_state(modified_files={"other.py": "x = 1\n"}))
    assert "Planner predicted" in report


def test_report_omits_the_mismatch_note_when_targets_agree():
    assert "Planner predicted" not in build_report(finished_state())


def test_report_trims_the_test_output():
    noisy = (
        "=== FAILURES ===\n___ t ___\n"
        "/x/site-packages/_pytest/x.py:1: in f\n"
        "E  assert 1 == 2\n\na.py:3: AssertionError\n1 failed\n"
    )
    report = build_report(finished_state(test_passed=False, test_output=noisy))
    assert "assert 1 == 2" in report
    assert "site-packages" not in report


def test_report_embeds_the_patch_when_given_one():
    report = build_report(finished_state(), diff="--- a/calc.py\n+++ b/calc.py\n")
    assert "```diff" in report


def test_report_handles_a_run_with_nothing_recorded():
    bare = build_initial_state("something broke", "/repo")
    report = build_report(bare)
    assert "No files were modified." in report
    assert "did not record an analysis" in report


# --- writing ---------------------------------------------------------------------


def test_artifacts_write_both_files(tmp_path):
    artifacts = build_artifacts(finished_state(), {"calc.py": BEFORE})
    written = artifacts.write(str(tmp_path / "out"))

    assert written["diff"].name == DIFF_FILENAME
    assert written["report"].name == REPORT_FILENAME
    assert "return a + b" in written["diff"].read_text()
    assert "Root Cause Analysis" in written["report"].read_text()


def test_write_creates_the_output_directory(tmp_path):
    target = tmp_path / "deep" / "nested"
    RunArtifacts(diff="", report="# hi\n").write(str(target))
    assert (target / REPORT_FILENAME).exists()


def test_build_artifacts_threads_the_diff_into_the_report():
    artifacts = build_artifacts(finished_state(), {"calc.py": BEFORE})
    assert "return a + b" in artifacts.diff
    assert "```diff" in artifacts.report


def test_report_warns_when_the_patch_is_incomplete():
    """Silently shipping a partial patch is the failure this guards against."""
    artifacts = build_artifacts(finished_state(), {})
    assert "Incomplete patch" in artifacts.report
    assert "`calc.py`" in artifacts.report


def test_no_incomplete_warning_when_every_baseline_is_present():
    assert "Incomplete patch" not in build_artifacts(finished_state(), {"calc.py": BEFORE}).report
