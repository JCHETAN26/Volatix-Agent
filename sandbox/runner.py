"""Ephemeral Docker sandbox runner -- Phase 2.

Mounts a workspace into a throwaway container, runs a command under a hard timeout,
captures stdout/stderr and the exit code, then destroys the container.

Everything executed here is untrusted: it is code an LLM wrote in response to a bug
report. The container is therefore locked down by default -- no network, dropped
capabilities, no privilege escalation, and capped memory/PIDs -- and is always removed,
including when the command times out or the caller raises.
"""

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import docker
import requests
from docker.errors import DockerException, ImageNotFound, NotFound

SANDBOX_IMAGE = "volatix-sandbox:latest"
WORKSPACE_MOUNT = "/workspace"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MEM_LIMIT = "512m"
DEFAULT_PIDS_LIMIT = 256

# Conventional shell exit code for "killed by timeout", matching coreutils `timeout`.
EXIT_CODE_TIMEOUT = 124


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot be prepared or the container fails to start."""


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

    @property
    def combined_output(self) -> str:
        """stdout and stderr joined, for feeding to the stack-trace parser."""
        return "\n".join(part for part in (self.stdout, self.stderr) if part)


class SandboxRunner:
    """Runs one-shot commands inside ephemeral, isolated Docker containers."""

    def __init__(
        self,
        image: str = SANDBOX_IMAGE,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        mem_limit: str = DEFAULT_MEM_LIMIT,
        pids_limit: int = DEFAULT_PIDS_LIMIT,
        network_disabled: bool = True,
        client: Optional[docker.DockerClient] = None,
    ):
        self.image = image
        self.timeout = timeout
        self.mem_limit = mem_limit
        self.pids_limit = pids_limit
        self.network_disabled = network_disabled
        self._client = client

    @property
    def client(self) -> docker.DockerClient:
        """Connect lazily so constructing a runner never requires a live daemon."""
        if self._client is None:
            try:
                self._client = docker.from_env()
            except DockerException as exc:
                raise SandboxError("Cannot reach the Docker daemon. Is Docker running?") from exc
        return self._client

    def _container_user(self) -> Optional[str]:
        """Run as the host UID so files written into the mount stay accessible.

        The image declares a non-root USER, but that UID rarely matches the host user
        who owns the workspace, which makes pytest's cache and __pycache__ writes fail.
        Matching the host UID keeps the container non-root *and* able to write.
        """
        if hasattr(os, "getuid"):
            return f"{os.getuid()}:{os.getgid()}"
        return None

    def ensure_image(self) -> None:
        """Fail fast with an actionable message if the sandbox image is missing."""
        try:
            self.client.images.get(self.image)
        except ImageNotFound as exc:
            raise SandboxError(
                f"Sandbox image '{self.image}' not found. Build it with:\n"
                f"  docker build -t {self.image} -f sandbox/Dockerfile.sandbox ."
            ) from exc

    def run(self, workspace_path: str, command: Sequence[str]) -> ExecutionResult:
        """Execute ``command`` against ``workspace_path`` in a throwaway container.

        Args:
            workspace_path: Host directory mounted read-write at ``/workspace``.
            command: Argv list, e.g. ``["pytest", "-q"]``.

        Returns:
            An ExecutionResult; a timeout yields ``timed_out=True`` and exit code 124
            rather than raising.

        Raises:
            SandboxError: The workspace is missing, the image is absent, or the
                container could not be created.
        """
        workspace = Path(workspace_path).expanduser().resolve()
        if not workspace.is_dir():
            raise SandboxError(f"Workspace path is not a directory: {workspace}")

        self.ensure_image()

        container = None
        started = time.perf_counter()
        try:
            try:
                container = self.client.containers.create(
                    image=self.image,
                    command=list(command),
                    working_dir=WORKSPACE_MOUNT,
                    volumes={str(workspace): {"bind": WORKSPACE_MOUNT, "mode": "rw"}},
                    user=self._container_user(),
                    network_disabled=self.network_disabled,
                    mem_limit=self.mem_limit,
                    pids_limit=self.pids_limit,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    detach=True,
                )
            except DockerException as exc:
                raise SandboxError(f"Failed to create sandbox container: {exc}") from exc

            container.start()
            timed_out = False
            try:
                exit_code = container.wait(timeout=self.timeout)["StatusCode"]
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError):
                # docker-py surfaces a wait timeout as a transport error, not its own type.
                timed_out = True
                exit_code = EXIT_CODE_TIMEOUT
                self._kill_quietly(container)

            duration_ms = (time.perf_counter() - started) * 1000
            return ExecutionResult(
                exit_code=exit_code,
                stdout=self._read_logs(container, stdout=True),
                stderr=self._read_logs(container, stdout=False),
                duration_ms=duration_ms,
                timed_out=timed_out,
            )
        finally:
            if container is not None:
                self._remove_quietly(container)

    def run_tests(self, workspace_path: str, test_path: str = "tests/") -> ExecutionResult:
        """Convenience wrapper used by the Validator node."""
        return self.run(workspace_path, ["pytest", test_path, "-q", "--no-header"])

    @staticmethod
    def _read_logs(container, *, stdout: bool) -> str:
        """Read one stream. Requires tty=False, or Docker would interleave them."""
        try:
            raw = container.logs(stdout=stdout, stderr=not stdout)
        except DockerException:
            return ""
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _kill_quietly(container) -> None:
        try:
            container.kill()
        except (DockerException, NotFound):
            pass  # Already dead; nothing to clean up.

    @staticmethod
    def _remove_quietly(container) -> None:
        """Ephemeral means ephemeral: never leak a container, even on failure."""
        try:
            container.remove(force=True)
        except (DockerException, NotFound):
            pass
