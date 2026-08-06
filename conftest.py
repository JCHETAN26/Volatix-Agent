"""Root conftest -- puts the repository root on sys.path for test imports."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture
def anyio_backend():
    """MCP tool handlers are async; run them on asyncio only, not trio."""
    return "asyncio"
