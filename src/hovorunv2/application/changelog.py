"""Utilities for reading project version and parsing the changelog."""

import re
from pathlib import Path


def get_current_version() -> str:
    """Read the current project version from pyproject.toml."""
    pyproject_path = Path(__file__).parent.parent.parent.parent / "pyproject.toml"
    if not pyproject_path.exists():
        return "0.0.0"

    content = pyproject_path.read_text(encoding="utf-8")
    for line in content.splitlines():
        if line.startswith("version = "):
            return line.split("=")[1].strip().strip('"').strip("'")
    return "0.0.0"


def get_changelog_updates(last_version: str, current_version: str) -> str | None:
    """Extract changelog entries strictly between last_version and current_version."""
    if last_version == current_version:
        return None

    changes_path = Path(__file__).parent.parent.parent.parent / "CHANGES.md"
    if not changes_path.exists():
        return None

    content = changes_path.read_text(encoding="utf-8")
    lines = content.splitlines()

    updates = []
    recording = False

    # Note: We assume version headers start with '## ' and are in descending order.
    current_version_found = False
    for line in lines:
        if line.startswith("## "):
            version = line.strip("# ").strip()
            if version == last_version:
                break
            if version == current_version:
                current_version_found = True
                recording = True
                continue
            if recording:
                # We hit another version header while recording, keep recording
                # if there are more versions between current and last?
                # Actually, usually current is the first one.
                continue

        if recording:
            updates.append(line)

    if not current_version_found or not updates:
        return None

    result = "\n".join(updates).strip()
    return result or None


def parse_version(version: str) -> tuple[int, ...]:
    """Convert version string to a comparable tuple of integers."""
    parts = version.split(".")
    return tuple(
        int(match.group())
        for p in parts
        if (match := re.search(r"\d+", p))
    )
