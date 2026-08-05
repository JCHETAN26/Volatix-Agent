"""Benchmark harness -- Phase 7.

Runs every scenario in ``evals/dataset/`` under two conditions and exports metrics:

    Condition A (baseline)  -- single-pass LLM prompt, no agent loop, no AST trimming
    Condition B (optimized) -- full self-healing loop with AST trimming and sandboxing

Metrics: completion rate (Pass@1), convergence speed, token efficiency, sandbox latency.
"""

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Volatix-Agent benchmark suite.")
    parser.add_argument("--output-file", default="evals/results.json", help="Metrics destination")
    parser.add_argument(
        "--condition",
        choices=["baseline", "optimized", "both"],
        default="both",
        help="Which experimental condition to run (default: %(default)s)",
    )
    return parser


def main() -> None:
    build_parser().parse_args()
    raise NotImplementedError("Evaluation harness lands in Phase 7.")


if __name__ == "__main__":
    main()
