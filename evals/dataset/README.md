# Benchmark dataset

Ten synthetic scenarios across the four bug categories in the build plan.
Each directory holds:

| Path | Purpose |
|------|---------|
| `repo/` | The broken project, copied to a temp dir per run |
| `repo/tests/` | A test that **fails** on the bug |
| `solution/` | Reference fix, used only to verify the scenario is sound |
| `scenario.json` | id, category, issue text, test path |

`solution/` is never shown to the agent. It exists so
`pytest evals/ -m dataset` can assert the fails-before / passes-after
property that makes a scenario meaningful.
