"""Discover Chromium browsers by installation type across Unix systems."""

import os
from collections.abc import Iterable, Iterator
from enum import IntEnum
from pathlib import Path

BROWSER_NAMES = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "brave",
    "microsoft-edge",
    "microsoft-edge-stable",
    "vivaldi",
    "opera",
    "opera-gx",
)
BROWSER_BUNDLES = (
    ("Google Chrome", "Google Chrome"),
    ("Chromium", "Chromium"),
    ("Brave Browser", "Brave Browser"),
    ("Microsoft Edge", "Microsoft Edge"),
    ("Vivaldi", "Vivaldi"),
    ("Opera", "Opera"),
    ("Opera GX", "Opera GX"),
)
FLATPAK_IDS = (
    "com.google.Chrome",
    "org.chromium.Chromium",
    "com.brave.Browser",
    "com.microsoft.Edge",
    "com.vivaldi.Vivaldi",
    "com.opera.Opera",
)
APPIMAGE_PREFIXES = ("googlechrome", "chrome", "chromium", "brave", "microsoftedge", "msedge", "vivaldi", "opera")


class InstallationKind(IntEnum):
    """Search priority independent of browser preference and PATH ordering."""

    PACKAGE = 0
    FLATPAK = 1
    SNAP = 2
    APPIMAGE = 3


def find_browser_executable() -> str | None:
    """Prefer an override, then packages, Flatpak, Snap and AppImage browsers."""
    # Check manual override.
    if (env_path := os.environ.get("BROWSER_PATH")) and _is_executable(Path(env_path)):
        return env_path

    return _select_browser(_browser_candidates())


def _is_executable(path: Path) -> bool:
    """Reject directories, missing files and non-executable downloads."""
    return path.is_file() and os.access(path, os.X_OK)


def _installation_kind(path: Path) -> InstallationKind:
    """Recognize installation paths and symlink targets before ranking candidates."""
    for candidate in (path.absolute(), path.resolve()):
        if candidate.suffix.lower() == ".appimage":
            return InstallationKind.APPIMAGE
        if "flatpak" in candidate.parts:
            return InstallationKind.FLATPAK
        if "snap" in candidate.parts or "snapd" in candidate.parts:
            return InstallationKind.SNAP
    return InstallationKind.PACKAGE


def _select_browser(candidates: Iterable[Path]) -> str | None:
    """Keep installation priority even when PATH lists sandboxed browsers first."""
    for path in sorted(dict.fromkeys(candidates), key=_installation_kind):
        if _is_executable(path):
            return str(path)
    return None


def _browser_candidates() -> Iterator[Path]:
    """Combine PATH, package locations and bounded portable-browser directories."""
    home = Path.home()
    path_directories = tuple(Path(directory) for directory in os.get_exec_path())
    package_directories = (
        *path_directories,
        Path("/usr/local/bin"),
        Path("/usr/bin"),
        Path("/opt/homebrew/bin"),
        Path("/home/linuxbrew/.linuxbrew/bin"),
        home / ".linuxbrew/bin",
    )
    # Order: Chrome -> Chromium -> Brave -> Edge -> Vivaldi -> Opera.
    for name in BROWSER_NAMES:
        for directory in package_directories:
            yield directory / name

    # Homebrew casks and manually/App Store installed application bundles share these locations.
    for directory in (Path("/Applications"), home / "Applications"):
        for bundle, executable in BROWSER_BUNDLES:
            yield directory / f"{bundle}.app/Contents/MacOS/{executable}"

    data_home = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    for directory in (data_home / "flatpak/exports/bin", Path("/var/lib/flatpak/exports/bin")):
        for app_id in FLATPAK_IDS:
            yield directory / app_id

    for directory in (Path("/snap/bin"), Path("/var/lib/snapd/snap/bin")):
        for name in BROWSER_NAMES:
            yield directory / name

    appimage_directories = (*path_directories, home / "Applications", home / "AppImages", Path("/opt/appimages"))
    yield from _appimage_candidates(appimage_directories)


def _appimage_candidates(directories: Iterable[Path]) -> Iterator[Path]:
    """Search browser AppImages without recursively scanning disks or executing files."""
    for directory in dict.fromkeys(directories):
        try:
            entries = sorted(directory.iterdir())
        except OSError:
            continue
        for path in entries:
            normalized = "".join(character for character in path.stem.lower() if character.isalnum())
            if path.suffix.lower() == ".appimage" and normalized.startswith(APPIMAGE_PREFIXES):
                yield path
