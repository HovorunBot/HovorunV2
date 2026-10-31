"""Tests for version resolution, client release notes (CHANGES.md), and technical changelog fallback."""

from pathlib import Path

from _pytest.monkeypatch import MonkeyPatch

from hovorunv2.application.operations import changelog
from hovorunv2.application.operations.changelog import (
    extract_fallback_notes,
    extract_release_notes,
    format_markdown_for_telegram,
    get_changelog_updates,
    get_current_version,
    parse_version,
)

SAMPLE_CLIENT_NOTES = """## 0.2.1

✨ **New in Hovorun v0.2.1: Instant Release Links & Feedback!**

* 📬 **/feedback Command:** Send suggestions directly to the developers.
* 🔗 **Direct Release Notes:** Update alerts now link to GitHub releases.

## 0.2.0

✨ **New in Hovorun v0.2.0: Reddit & YouTube!**

* 🤖 **Reddit & YouTube Support:** Automatically extracts posts and videos.

## 0.1.0

🚀 **Welcome to Hovorun v0.1.0!**

* 📹 **Rich Media:** Automatically extracts media links.
"""

SAMPLE_TECHNICAL_CHANGELOG = """## v0.2.1 (2026-10-22)

### Feat

- **notification**: add direct GitHub release tag link
- **ci**: add Git pre-commit hooks and ruff formatting
- **test**: add tests for extractor
- **queue**: implement SAQ task queue and /feedback command

### Fix

- resolve connection timeout

## v0.2.0 (2026-10-20)

### Feat

- **providers**: add Reddit support
"""


def test_parse_version() -> None:
    """Test semantic version parsing."""
    assert parse_version("0.1.0") == (0, 1, 0)
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("v2.0.0-rc1") == (2, 0, 0)
    assert parse_version("unknown") == (0, 0, 0)


def test_get_current_version_precedence(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Test version resolution precedence: env > package > pyproject."""
    monkeypatch.setenv("APP_VERSION", "2.0.0")
    assert get_current_version() == "2.0.0"

    monkeypatch.delenv("APP_VERSION", raising=False)
    pyproject_file = tmp_path / "pyproject.toml"
    pyproject_file.write_text('[project]\nversion = "3.1.4"\n', encoding="utf-8")
    monkeypatch.setattr(
        changelog,
        "pkg_version",
        lambda _pkg: (_ for _ in ()).throw(changelog.PackageNotFoundError),
    )
    monkeypatch.setattr(Path, "resolve", lambda _self: tmp_path / "src" / "hovorunv2" / "application" / "changelog.py")

    assert get_current_version() == "3.1.4"


def test_format_markdown_for_telegram() -> None:
    """Test converting Markdown bold, code, and list items into Telegram HTML."""
    raw = "✨ **New Feature**\n* 🔔 **Alerts:** Get notified.\n- 📬 `/feedback` command: Run `help` for details."
    formatted = format_markdown_for_telegram(raw)
    assert "<b>New Feature</b>" in formatted
    assert "• 🔔 <b>Alerts:</b> Get notified." in formatted
    assert "• 📬 <code>/feedback</code> command: Run <code>help</code> for details." in formatted


def test_extract_release_notes() -> None:
    """Test extracting release notes strictly for target version range."""
    notes = extract_release_notes(SAMPLE_CLIENT_NOTES, "0.2.0", "0.2.1")
    assert notes is not None
    assert "New in Hovorun v0.2.1" in notes
    assert "/feedback Command" in notes
    assert "Reddit & YouTube" not in notes
    assert "Welcome to Hovorun v0.1.0" not in notes

    # Multi-version range
    multi = extract_release_notes(SAMPLE_CLIENT_NOTES, "0.1.0", "0.2.1")
    assert multi is not None
    assert "New in Hovorun v0.2.1" in multi
    assert "New in Hovorun v0.2.0" in multi
    assert "Welcome to Hovorun v0.1.0" not in multi

    # Equal or downgrade
    assert extract_release_notes(SAMPLE_CLIENT_NOTES, "0.2.1", "0.2.1") is None
    assert extract_release_notes(SAMPLE_CLIENT_NOTES, "0.2.1", "0.2.0") is None


def test_extract_fallback_notes_filters_technical_scopes() -> None:
    """Test that technical fallback filters internal developer scopes (ci, test, chore)."""
    fallback = extract_fallback_notes(SAMPLE_TECHNICAL_CHANGELOG, "0.2.0", "0.2.1")
    assert fallback is not None
    # User-facing features kept
    assert "<b>Notification:</b> Add direct GitHub release tag link" in fallback
    assert "<b>Queue:</b> Implement SAQ task queue and /feedback command" in fallback
    assert "Resolve connection timeout" in fallback
    # Internal developer scopes filtered out
    assert "ci" not in fallback.lower()
    assert "ruff formatting" not in fallback
    assert "add tests for extractor" not in fallback


def test_get_changelog_updates_prefers_client_notes(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Test that client-facing CHANGES.md takes precedence over CHANGELOG.md."""
    client_file = tmp_path / "CHANGES.md"
    client_file.write_text(SAMPLE_CLIENT_NOTES, encoding="utf-8")

    tech_file = tmp_path / "CHANGELOG.md"
    tech_file.write_text(SAMPLE_TECHNICAL_CHANGELOG, encoding="utf-8")

    monkeypatch.setattr(changelog, "get_client_changelog_path", lambda: client_file)
    monkeypatch.setattr(changelog, "get_technical_changelog_path", lambda: tech_file)

    updates = get_changelog_updates("0.2.0", "0.2.1")
    assert updates is not None
    # Must use client notes
    assert "New in Hovorun v0.2.1: Instant Release Links & Feedback!" in updates
    assert "<b>/feedback Command:</b>" in updates


def test_get_changelog_updates_fallback_when_client_notes_missing(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    """Test fallback to technical changelog when CHANGES.md has no entry for target version."""
    client_file = tmp_path / "CHANGES.md"
    # Client notes only has 0.1.0 and 0.2.0, missing 0.2.1
    client_file.write_text("## 0.2.0\n\nExisting notes\n", encoding="utf-8")

    tech_file = tmp_path / "CHANGELOG.md"
    tech_file.write_text(SAMPLE_TECHNICAL_CHANGELOG, encoding="utf-8")

    monkeypatch.setattr(changelog, "get_client_changelog_path", lambda: client_file)
    monkeypatch.setattr(changelog, "get_technical_changelog_path", lambda: tech_file)

    updates = get_changelog_updates("0.2.0", "0.2.1")
    assert updates is not None
    # Fell back to technical changelog user-facing items
    assert "<b>Notification:</b> Add direct GitHub release tag link" in updates
    assert "<b>Queue:</b> Implement SAQ task queue and /feedback command" in updates
