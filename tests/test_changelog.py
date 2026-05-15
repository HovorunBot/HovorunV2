"""Tests for changelog utilities."""

import textwrap
from typing import Any

from _pytest.monkeypatch import MonkeyPatch

from hovorunv2.application import changelog
from hovorunv2.application.changelog import get_changelog_updates, parse_version


def test_parse_version() -> None:
    """Test version parsing."""
    assert parse_version("0.1.0") == (0, 1, 0)
    assert parse_version("1.2.3-rc1") == (1, 2, 3)


class MockPath:
    """Mock for pathlib.Path to simulate filesystem state in tests."""

    def __init__(self, *args: Any, content: str = "") -> None:  # noqa: ARG002
        """Initialize with content."""
        self._content = content

    def __truediv__(self, other: Any) -> MockPath:
        """Return self for any path joining."""
        return self

    @property
    def parent(self) -> MockPath:
        """Return self as parent."""
        return self

    def exists(self) -> bool:
        """Return True for these tests."""
        return True

    def read_text(self, encoding: str | None = None) -> str:  # noqa: ARG002
        """Return the pre-defined content."""
        return self._content


def test_get_changelog_updates(monkeypatch: MonkeyPatch) -> None:
    """Test changelog extraction."""
    changes_content = textwrap.dedent("""\
        ## 0.2.0
        - Feature B
        - Fix C

        ## 0.1.0
        - Initial release
        """)

    monkeypatch.setattr(changelog, "Path", lambda *args: MockPath(*args, content=changes_content))

    updates = get_changelog_updates("0.1.0", "0.2.0")
    assert updates == "- Feature B\n- Fix C"

    updates = get_changelog_updates("0.0.0", "0.2.0")
    assert updates is not None
    assert "Initial release" in updates
    assert "Feature B" in updates

    updates = get_changelog_updates("0.2.0", "0.2.0")
    assert updates is None


def test_get_changelog_updates_no_current(monkeypatch: MonkeyPatch) -> None:
    """Test changelog extraction when current version is missing in file."""
    changes_content = "## 0.1.0\n- Initial"
    monkeypatch.setattr(changelog, "Path", lambda *args: MockPath(*args, content=changes_content))

    # 0.2.0 is current but not in CHANGES.md -> should be silent
    assert get_changelog_updates("0.1.0", "0.2.0") is None


def test_get_changelog_updates_empty_current(monkeypatch: MonkeyPatch) -> None:
    """Test changelog extraction when current version exists but has no entries."""
    changes_content = "## 0.2.0\n\n## 0.1.0\n- Initial"
    monkeypatch.setattr(changelog, "Path", lambda *args: MockPath(*args, content=changes_content))

    # 0.2.0 exists but is empty -> should be silent
    assert get_changelog_updates("0.1.0", "0.2.0") is None
