"""Shared pytest configuration for the Fix My Vibe test suite.

Ensures the repository root is importable as `src.*` whether tests are run via
`pytest`, `python -m pytest`, or from an editable install.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURES = REPO_ROOT / "tests" / "fixtures"

# Fixture projects contain their own (intentionally broken / dependency-heavy)
# test files — they are test *data*, not part of our suite. Don't collect them.
collect_ignore = ["fixtures"]


@pytest.fixture
def fixtures_dir() -> Path:
    """Absolute path to tests/fixtures."""
    return FIXTURES
