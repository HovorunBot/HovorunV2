"""Hygiene regressions for test suite decoupling and import rules."""

from pathlib import Path

import pytest

from tests.conftest import _check_no_cross_test_imports


def test_no_cross_test_imports_on_current_tree() -> None:
    """The current repository must have zero imports from tests.test_*."""
    _check_no_cross_test_imports()


def test_cross_test_import_guard_rejects_deliberate_violation(tmp_path: Path) -> None:
    """The guard must detect and reject any import matching tests.test_*."""
    fake_tests = tmp_path / "tests"
    fake_tests.mkdir()
    violating_file = fake_tests / "test_dummy.py"
    violating_file.write_text("from tests.test_membership_adapter import API\n", encoding="utf-8")

    with pytest.raises(pytest.UsageError, match="Forbidden cross-test imports detected"):
        _check_no_cross_test_imports(fake_tests)
