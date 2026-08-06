"""Dataset soundness.

A benchmark scenario is only meaningful if its test genuinely fails on the bug and
genuinely passes once fixed. A scenario that passes while still broken silently inflates
Pass@1; one that fails even when fixed makes the agent look worse than it is. Both are
checked here by actually running pytest, not by inspection.

These run the dataset's own fixture code, never model output.
"""

import subprocess
import sys

import pytest

from evals.loader import ScenarioError, iter_scenarios, load_all, load_scenario

SCENARIOS = load_all()
EXPECTED_CATEGORIES = {"syntax", "logic", "type", "edge"}


def run_pytest(repo) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header"],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=120,
    )


# --- dataset shape ----------------------------------------------------------------


def test_dataset_is_not_empty():
    assert len(SCENARIOS) == 10


def test_scenario_ids_are_unique():
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))


def test_every_bug_category_is_represented():
    """The build plan calls for syntax, logic, type, and edge-case coverage."""
    assert {s.category for s in SCENARIOS} == EXPECTED_CATEGORIES


def test_every_category_has_at_least_two_scenarios():
    counts = {c: sum(1 for s in SCENARIOS if s.category == c) for c in EXPECTED_CATEGORIES}
    assert all(n >= 2 for n in counts.values()), counts


def test_issue_text_is_substantial():
    """A one-word issue would not exercise the planner."""
    for scenario in SCENARIOS:
        assert len(scenario.issue) > 40, scenario.id


# --- the property that makes a scenario meaningful ---------------------------------


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario_fails_before_the_fix(scenario, tmp_path):
    repo = scenario.materialise(str(tmp_path / scenario.id))
    result = run_pytest(repo)
    assert result.returncode != 0, f"{scenario.id} passes while still broken:\n{result.stdout}"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.id)
def test_scenario_passes_after_the_reference_fix(scenario, tmp_path):
    repo = scenario.materialise(str(tmp_path / scenario.id))
    scenario.apply_reference_fix(repo)
    result = run_pytest(repo)
    assert result.returncode == 0, f"{scenario.id} still fails when fixed:\n{result.stdout}"


# --- loader -----------------------------------------------------------------------


def test_materialise_produces_an_independent_copy(tmp_path):
    scenario = SCENARIOS[0]
    repo = scenario.materialise(str(tmp_path / "copy"))
    (repo / scenario.buggy_file).write_text("# clobbered\n")

    assert "clobbered" not in (scenario.repo_dir / scenario.buggy_file).read_text()


def test_materialise_overwrites_a_stale_destination(tmp_path):
    scenario = SCENARIOS[0]
    target = tmp_path / "reused"
    scenario.materialise(str(target))
    (target / "leftover.txt").write_text("stale")

    scenario.materialise(str(target))
    assert not (target / "leftover.txt").exists()


def test_reference_fix_is_never_inside_the_agent_visible_repo():
    """If solution/ were under repo/, the agent could simply read the answer."""
    for scenario in SCENARIOS:
        assert scenario.solution_dir.is_dir()
        assert not (scenario.repo_dir / "solution").exists()


def test_filtering_by_category():
    syntax = load_all(categories=["syntax"])
    assert syntax and all(s.category == "syntax" for s in syntax)


def test_missing_manifest_is_rejected(tmp_path):
    with pytest.raises(ScenarioError, match="no scenario.json"):
        load_scenario(tmp_path)


def test_incomplete_manifest_is_rejected(tmp_path):
    (tmp_path / "scenario.json").write_text('{"id": "x"}')
    with pytest.raises(ScenarioError, match="missing keys"):
        load_scenario(tmp_path)


def test_manifest_without_a_repo_is_rejected(tmp_path):
    (tmp_path / "scenario.json").write_text(
        '{"id": "x", "category": "logic", "issue": "i", '
        '"test_path": "tests/", "buggy_file": "m.py"}'
    )
    with pytest.raises(ScenarioError, match="no repo/"):
        load_scenario(tmp_path)


def test_iter_scenarios_skips_directories_without_a_manifest(tmp_path):
    (tmp_path / "not-a-scenario").mkdir()
    assert list(iter_scenarios(tmp_path)) == []
