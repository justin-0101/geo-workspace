"""Resolve an installed Chrome/Chromium executable without hard-coded user paths."""
import os
import shutil
from pathlib import Path


def chrome_executable():
    """Return the configured or commonly installed browser executable, if present."""
    configured = os.environ.get("GEO_CHROME_PATH", "").strip()
    # An explicit override is authoritative: a typo must not silently select a
    # different browser/profile than the operator intended.
    if configured:
        path = Path(configured)
        try:
            return path.resolve() if path.is_file() else None
        except OSError:
            return None
    candidates = []
    if os.name == "nt":
        program_files = [os.environ.get(name) for name in ("PROGRAMFILES", "ProgramW6432", "PROGRAMFILES(X86)")]
        local_app_data = os.environ.get("LOCALAPPDATA")
        for base in filter(None, program_files):
            candidates.extend([
                Path(base) / "Google/Chrome/Application/chrome.exe",
                Path(base) / "Chromium/Application/chrome.exe",
            ])
        if local_app_data:
            candidates.append(Path(local_app_data) / "Google/Chrome/Application/chrome.exe")
    for name in ("chrome", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for path in candidates:
        try:
            if path.is_file():
                return path.resolve()
        except OSError:
            continue
    return None
