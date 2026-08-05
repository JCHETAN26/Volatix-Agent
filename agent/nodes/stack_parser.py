"""Stack-trace trimming -- Phase 5.

Strips noisy framework frames from pytest output and isolates the failing
assertion plus the originating error message, keeping retry prompts small.
"""


def trim_stack_trace(test_output: str) -> str:
    raise NotImplementedError("Stack-trace parser lands in Phase 5.")
