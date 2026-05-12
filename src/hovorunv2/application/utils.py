"""Utility functions for the application."""

import html
import os
import platform
import re
import shutil
from pathlib import Path
from typing import Final

BILLION: Final = 1_000_000_000
MILLION: Final = 1_000_000
THOUSAND: Final = 1_000


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


def format_number(num: int) -> str:
    """Format large numbers into readable text (e.g. 1.2K, 3.4M)."""
    billion_threshold = 1_000_000_000
    million_threshold = 1_000_000
    thousand_threshold = 1_000

    if num >= billion_threshold:
        return f"{num / billion_threshold:.1f}B".replace(".0B", "B")
    if num >= million_threshold:
        return f"{num / million_threshold:.1f}M".replace(".0M", "M")
    if num >= thousand_threshold:
        return f"{num / thousand_threshold:.1f}K".replace(".0K", "K")
    return str(num)


def extract_og_metadata(html_content: str) -> dict[str, str]:
    """Robustly extract OG and Twitter metadata from HTML."""
    metadata = {}
    # Match meta tags and extract key-value pairs
    for meta in re.finditer(r"<meta\s+([^>]+)>", html_content, re.IGNORECASE):
        body = meta.group(1)
        # Find property="og:..." or name="twitter:..." etc.
        key_m = re.search(r'(?:property|name)=["\'](?:og:|twitter:)?([^"\']+)["\']', body, re.IGNORECASE)
        val_m = re.search(r'content=["\']([^"\']+)["\']', body, re.IGNORECASE)

        if key_m and val_m:
            key = key_m.group(1).lower()
            val = html.unescape(val_m.group(1))
            if key not in metadata:
                metadata[key] = val
    return metadata
