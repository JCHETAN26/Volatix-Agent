"""Rich console entry point -- Phase 6.

Renders live agent transitions (Planning -> Executing -> Validating -> Retrying)
alongside streaming execution logs.
"""

import argparse

from agent.state import DEFAULT_MAX_RETRIES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="volatix",
        description="Turn a bug report into a verified, test-passing patch.",
    )
    parser.add_argument("--issue", required=True, help="Bug report, ticket text, or error log")
    parser.add_argument("--repo-path", required=True, help="Path to the repository under repair")
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help="Self-correction cycles before giving up (default: %(default)s)",
    )
    return parser


def main() -> None:
    build_parser().parse_args()
    raise NotImplementedError("CLI run loop lands in Phase 6.")


if __name__ == "__main__":
    main()
