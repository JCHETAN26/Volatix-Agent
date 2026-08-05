"""Ephemeral Docker sandbox runner -- Phase 2.

Mounts a workspace into a throwaway container, runs a command under a hard timeout,
captures stdout/stderr and the exit code, then destroys the container.
"""

from dataclasses import dataclass

SANDBOX_IMAGE = "volatix-sandbox:latest"
DEFAULT_TIMEOUT_SECONDS = 30


@dataclass
class ExecutionResult:
    """Outcome of a single sandboxed command."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class SandboxRunner:
    """Thin wrapper over docker-py for one-shot, isolated command execution."""

    def __init__(self, image: str = SANDBOX_IMAGE, timeout: int = DEFAULT_TIMEOUT_SECONDS):
        self.image = image
        self.timeout = timeout

    def run(self, workspace_path: str, command: list[str]) -> ExecutionResult:
        raise NotImplementedError("Sandbox runner lands in Phase 2.")
