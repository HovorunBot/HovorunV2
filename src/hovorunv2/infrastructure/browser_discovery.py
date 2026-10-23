"""Browser executable discovery across platforms."""

import os
import platform
import shutil
from pathlib import Path


def find_browser_executable() -> str | None:
    """Find a suitable Chromium-based browser executable on the system."""
    # Check manual override
    if (env_path := os.environ.get("BROWSER_PATH")) and Path(env_path).exists():
        return env_path

    # 1. Check PATH candidates (Native/Binary)
    # Order: Chrome -> Chromium -> Brave -> Edge -> Vivaldi -> Opera
    candidates = [
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
    ]
    for candidate in candidates:
        if path := shutil.which(candidate):
            return path

    # 2. Check platform-specific locations
    match platform.system():
        case "Darwin":
            locations = [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
                "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi",
                "/Applications/Chromium.app/Contents/MacOS/Chromium",
                "/Applications/Opera.app/Contents/MacOS/Opera",
            ]
        case "Linux":
            locations = _get_linux_browser_paths()
        case "Windows":
            locations = _get_windows_browser_paths()
        case _:
            locations = []

    for path in locations:
        if Path(path).exists():
            return path

    return None


def _get_linux_browser_paths() -> list[str]:
    """Generate exhaustive list of Linux browser locations (Native, Snap, Flatpak)."""
    return [
        # Snap
        "/snap/bin/chromium",
        "/snap/bin/google-chrome",
        "/snap/bin/brave",
        # Flatpak
        "/var/lib/flatpak/exports/bin/org.chromium.Chromium",
        "/var/lib/flatpak/exports/bin/com.google.Chrome",
        "/var/lib/flatpak/exports/bin/com.brave.Browser",
        "/var/lib/flatpak/exports/bin/com.vivaid.Vivaldi",
        "/var/lib/flatpak/exports/bin/com.microsoft.Edge",
        # Common local/bin paths
        "/usr/local/bin/google-chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
    ]


def _get_windows_browser_paths() -> list[str]:
    """Generate exhaustive list of Windows browser locations."""
    program_files = os.environ.get("PROGRAMFILES", "C:\\Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")
    local_app_data = os.environ.get("LOCALAPPDATA", str(Path("~\\AppData\\Local").expanduser()))

    return [
        rf"{program_files}\Google\Chrome\Application\chrome.exe",
        rf"{program_files_x86}\Google\Chrome\Application\chrome.exe",
        rf"{local_app_data}\Google\Chrome\Application\chrome.exe",
        rf"{program_files}\BraveSoftware\Brave-Browser\Application\brave.exe",
        rf"{program_files}\Microsoft\Edge\Application\msedge.exe",
        rf"{local_app_data}\Vivaldi\Application\vivaldi.exe",
        rf"{local_app_data}\Programs\Opera\opera.exe",
        rf"{program_files}\Opera\opera.exe",
    ]
