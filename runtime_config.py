"""Portable local-service configuration.

Only loopback listeners are supported. Values are intentionally environment-based so
an unpacked checkout does not contain machine-specific paths or ports.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOCAL_HOST = os.environ.get("GEO_BIND_HOST", "127.0.0.1").strip() or "127.0.0.1"
if LOCAL_HOST not in {"127.0.0.1", "localhost"}:
    raise ValueError("GEO_BIND_HOST must remain a loopback address (127.0.0.1 or localhost); IPv6 ::1 is not supported")


def _port(name, default):
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer port") from exc
    if not 1 <= value <= 65535:
        raise ValueError(f"{name} must be between 1 and 65535")
    return value


FRONTEND_PORT = _port("GEO_FRONTEND_PORT", 4173)
API_PORT = _port("GEO_API_PORT", 8798)
if FRONTEND_PORT == API_PORT:
    raise ValueError("GEO_FRONTEND_PORT and GEO_API_PORT must be different")
DATA_DIR = Path(os.environ.get("GEO_REDESIGN_DATA", str(ROOT / "data"))).expanduser().resolve()
FRONTEND_ORIGINS = [f"http://127.0.0.1:{FRONTEND_PORT}",
                    f"http://localhost:{FRONTEND_PORT}"]
API_URL = f"http://127.0.0.1:{API_PORT}"
FRONTEND_URL = f"http://127.0.0.1:{FRONTEND_PORT}"
