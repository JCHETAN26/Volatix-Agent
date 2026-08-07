# Benchmark dataset

Fifteen synthetic scenarios. Each directory holds:

| Path | Purpose |
|------|---------|
| `repo/` | The broken project, copied to a temp dir per run |
| `repo/tests/` | A test that **fails** on the bug |
| `solution/` | Reference fix, used only to verify the scenario is sound |
| `scenario.json` | id, category, issue text, test path |

`solution/` is never shown to the agent. It exists so `tests/unit/test_dataset.py` can
assert the fails-before / passes-after property that makes a scenario meaningful.

## Categories

The first four are the bug kinds named in the build plan:

| Category | Count | What it covers |
|----------|------:|----------------|
| `syntax` | 2 | Code that does not parse |
| `logic` | 3 | Wrong operator, off-by-one, inverted comparison |
| `type` | 2 | Type mismatch, `None` dereference |
| `edge` | 3 | Empty input, division by zero, boundary handling |

The last two were added **after the first benchmark sweep scored 100% under both
conditions and therefore proved nothing**. They exist specifically to separate the
single-pass baseline from the agent loop:

| Category | Count | Why it discriminates |
|----------|------:|----------------------|
| `multifile` | 2 | Two independent failures in two files. Condition A returns a single `{path, content}` object, so it cannot pass these **by construction** |
| `vague` | 3 | The report names a symptom, not a fix, and the repo carries four distractor modules — locating the defect is real work rather than a given |

## Honest limitations

- These are synthetic. They demonstrate that the architecture works; they do not
  establish a competitive benchmark number against real-world bugs.
- Fifteen scenarios is well short of the 50–100 the build plan calls for.
- `evals/loader.py` is deliberately source-agnostic, so real-repo (SWE-bench-style)
  scenarios can be added later without touching the harness.
