"""Benchmark scenario loading -- Phase 7.

A scenario is a directory holding a broken repository, a test that fails on the bug, and
a reference fix used only to prove the scenario is sound. Loading is kept separate from
the runner so a different scenario source (real-repo SWE-bench-style cases, say) can be
dropped in without touching the harness.
"""

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, List, Optional

DATASET_ROOT = Path(__file__).parent / "dataset"
SCENARIO_FILE = "scenario.json"


class ScenarioError(RuntimeError):
    """Raised when a scenario directory is malformed."""


@dataclass
class Scenario:
    """One benchmark case."""

    id: str
    category: str
    issue: str
    test_path: str
    buggy_file: str
    directory: Path

    @property
    def repo_dir(self) -> Path:
        return self.directory / "repo"

    @property
    def solution_dir(self) -> Path:
        return self.directory / "solution"

    def materialise(self, destination: Optional[str] = None) -> Path:
        """Copy the broken repository somewhere disposable and return that path.

        Runs always work on a copy: the agent edits files, and a scenario that mutated
        in place would only be usable once.
        """
        target = Path(destination or tempfile.mkdtemp(prefix=f"volatix-{self.id}-"))
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.repo_dir, target)
        return target

    def apply_reference_fix(self, repo: Path) -> None:
        """Overlay the reference solution. Used to validate the scenario, never by the agent."""
        for source in sorted(self.solution_dir.rglob("*")):
            if source.is_file():
                destination = repo / source.relative_to(self.solution_dir)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)


def load_scenario(directory: Path) -> Scenario:
    """Read one scenario directory."""
    manifest = directory / SCENARIO_FILE
    if not manifest.is_file():
        raise ScenarioError(f"{directory} has no {SCENARIO_FILE}")

    data: Dict = json.loads(manifest.read_text(encoding="utf-8"))
    missing = {"id", "category", "issue", "test_path", "buggy_file"} - set(data)
    if missing:
        raise ScenarioError(f"{manifest} is missing keys: {sorted(missing)}")

    scenario = Scenario(directory=directory, **data)
    if not scenario.repo_dir.is_dir():
        raise ScenarioError(f"{directory} has no repo/ directory")
    return scenario


def iter_scenarios(root: Optional[Path] = None) -> Iterator[Scenario]:
    """Yield every scenario under ``root``, ordered by id."""
    base = root or DATASET_ROOT
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        if (directory / SCENARIO_FILE).is_file():
            yield load_scenario(directory)


def load_all(root: Optional[Path] = None, categories: Optional[List[str]] = None) -> List[Scenario]:
    """Load every scenario, optionally filtered to a set of categories."""
    scenarios = list(iter_scenarios(root))
    if categories:
        wanted = set(categories)
        scenarios = [s for s in scenarios if s.category in wanted]
    return scenarios
