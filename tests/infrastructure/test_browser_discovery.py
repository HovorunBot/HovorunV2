"""Verify browser discovery using real executables, directories and symlinks."""

from pathlib import Path

import pytest

from hovorunv2.infrastructure.browser_discovery import (
    _appimage_candidates,
    _browser_candidates,
    _select_browser,
    find_browser_executable,
)

pytestmark = [pytest.mark.browser]


def executable(path: Path) -> Path:
    """Create a disposable executable without launching a browser."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o700)
    return path


def test_installation_priority_overrides_candidate_order(tmp_path: Path) -> None:
    """Removing each preferred installation exposes exactly the next tier."""
    package = executable(tmp_path / "usr/bin/chromium")
    flatpak = executable(tmp_path / "flatpak/exports/bin/com.google.Chrome")
    snap = executable(tmp_path / "snap/bin/google-chrome")
    appimage = executable(tmp_path / "Applications/Brave.AppImage")
    candidates = [appimage, snap, flatpak, package]
    for expected in (package, flatpak, snap, appimage):
        assert _select_browser(candidates) == str(expected)
        expected.unlink()
    assert _select_browser(candidates) is None


@pytest.mark.parametrize(
    ("target_path", "preferred_path"),
    [
        ("Applications/Chromium.AppImage", "flatpak/exports/bin/com.google.Chrome"),
        ("snap/bin/chromium", "flatpak/exports/bin/com.google.Chrome"),
        ("flatpak/exports/bin/org.chromium.Chromium", "usr/bin/google-chrome"),
    ],
)
def test_path_symlink_keeps_installation_priority(tmp_path: Path, target_path: str, preferred_path: str) -> None:
    """A sandboxed or portable browser aliased on PATH retains its installation tier."""
    target = executable(tmp_path / target_path)
    alias = tmp_path / "chromium"
    alias.symlink_to(target)
    preferred = executable(tmp_path / preferred_path)
    assert _select_browser([alias, preferred]) == str(preferred)
    preferred.unlink()
    assert _select_browser([alias, preferred]) == str(alias)


def test_discovery_rejects_nonexecutable_files_and_directories(tmp_path: Path) -> None:
    """Existing paths alone do not identify launchable browsers."""
    download = tmp_path / "Chrome.AppImage"
    download.touch()
    directory = tmp_path / "chromium"
    directory.mkdir()
    assert _select_browser([directory, download]) is None


def test_override_wins_and_invalid_override_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit executable wins; a directory override uses normal discovery."""
    override = executable(tmp_path / "custom-browser")
    package = executable(tmp_path / "bin/google-chrome")
    monkeypatch.setenv("PATH", str(package.parent))
    monkeypatch.setenv("BROWSER_PATH", str(override))
    assert find_browser_executable() == str(override)
    monkeypatch.setenv("BROWSER_PATH", str(tmp_path))
    assert find_browser_executable() == str(package)


def test_home_candidates_include_bundles_flatpak_and_portable_browsers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Shared discovery finds user application bundles, exports and AppImages."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    bundle = executable(tmp_path / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
    flatpak = executable(tmp_path / "data/flatpak/exports/bin/com.vivaldi.Vivaldi")
    appimage = executable(tmp_path / "AppImages/Chromium-123-x86_64.AppImage")
    all_candidates = list(_browser_candidates())
    assert Path("/opt/homebrew/bin/chromium") in all_candidates
    assert Path("/usr/local/bin/chromium") in all_candidates
    assert Path("/home/linuxbrew/.linuxbrew/bin/chromium") in all_candidates
    assert Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome") in all_candidates
    candidates = [path for path in all_candidates if path.is_relative_to(tmp_path)]
    assert _select_browser(candidates) == str(bundle)
    bundle.unlink()
    assert _select_browser(candidates) == str(flatpak)
    flatpak.unlink()
    assert _select_browser(candidates) == str(appimage)


def test_appimage_search_is_bounded_to_browser_files(tmp_path: Path) -> None:
    """Ignore unrelated and nested AppImages, accepting browser filename case variants."""
    browser = executable(tmp_path / "Brave-Browser-x86_64.appimage")
    executable(tmp_path / "Editor.AppImage")
    executable(tmp_path / "nested/Chromium.AppImage")
    assert list(_appimage_candidates([tmp_path, tmp_path / "missing"])) == [browser]
