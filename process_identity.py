"""Safe identity checks for processes launched by the local service runner.

A PID file is not ownership proof: Windows can reuse a PID after a service exits.
The runner records both the process command line and its creation-time value.  If
this information cannot be read (for example, due to Windows permissions), the
safe result is ``False`` and the process is left alone.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
from pathlib import Path


def _argv_parts(value):
    """Return clean argv parts, or ``None`` for an untrusted record/value."""
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if any(not isinstance(part, str) or not part.strip() for part in value):
        return None
    return tuple(part.strip() for part in value)


def _basename(value):
    return value.strip('\\\"').replace('\\', '/').rsplit('/', 1)[-1].lower()


def _launcher_argv(value):
    """Normalize the exact launcher command shapes written by start.py.

    The frontend script argument is intentionally compared verbatim.  start.py
    writes ``frontend_server.py`` relative to ROOT, so accepting its basename
    from an arbitrary path would allow an unrelated checkout to look owned.
    """
    argv = _argv_parts(value)
    executable = _basename(argv[0]) if argv is not None else ''
    if executable and not re.fullmatch(r'python(?:[0-9]+(?:\.[0-9]+)*)?(?:\.exe)?', executable):
        return None
    if argv is None:
        return None
    if len(argv) == 2 and argv[1] == 'frontend_server.py':
        return ('frontend', argv[1])
    if (len(argv) == 8 and argv[1].lower() == '-m'
            and argv[2].lower() == 'uvicorn'
            and argv[3].lower() == 'workflow_api:app'
            and argv[4].lower() == '--host'
            and argv[5].lower() in {'127.0.0.1', 'localhost'}
            and argv[6].lower() == '--port'):
        try:
            port = int(argv[7])
        except ValueError:
            return None
        if 1 <= port <= 65535 and str(port) == argv[7]:
            return ('workflow-api', tuple(part.lower() for part in argv[1:]))
    return None


def valid_launcher_argv(value):
    """Whether a PID record contains a complete, recognized launcher argv."""
    return _launcher_argv(value) is not None


def process_start_time(pid: int):
    """Return an opaque process creation marker, or ``None`` when unavailable."""
    pid = int(pid)
    if os.name == "nt":
        # PROCESS_QUERY_LIMITED_INFORMATION is available on supported Windows
        # versions and does not require opening a process with termination rights.
        import ctypes
        from ctypes import wintypes

        try:
            kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.GetProcessTimes.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
                ctypes.POINTER(wintypes.FILETIME)]
            kernel32.GetProcessTimes.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            handle = kernel32.OpenProcess(0x1000, False, pid)
        except (OSError, AttributeError, TypeError):
            return None
        if not handle:
            return None
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            ok = kernel32.GetProcessTimes(
                handle, ctypes.byref(created), ctypes.byref(exited),
                ctypes.byref(kernel), ctypes.byref(user))
            if not ok:
                return None
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
        except (OSError, TypeError):
            return None
        finally:
            try:
                kernel32.CloseHandle(handle)
            except OSError:
                pass

    stat = Path(f"/proc/{pid}/stat")
    try:
        # The comm field may contain spaces and ')' characters; split only
        # after its final closing parenthesis.  Field 22 is index 19 here.
        fields = stat.read_text(encoding="ascii").rsplit(")", 1)[1].split()
        return int(fields[19])
    except (OSError, ValueError, IndexError):
        return None


def process_command_line(pid: int):
    """Return argv-like command-line parts, or ``None`` if unreadable."""
    pid = int(pid)
    if os.name != "nt":
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            return [part.decode(sys.getfilesystemencoding(), "replace")
                    for part in raw.split(b"\0") if part]
        except OSError:
            return None

    # WMI/CIM is the standard supported Windows interface for command lines.
    # A missing/blocked PowerShell is intentionally a safe no-op at stop time.
    # The budget must clear a cold PowerShell start: measured 3.9 s on this machine,
    # and the previous 3 s budget timed out on every call, which silently turned every
    # stop into "unverified, left alone".
    script = (
        "$p=Get-CimInstance Win32_Process -Filter 'ProcessId = %d';"
        "if ($p) {$p.CommandLine}" % pid
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    line = result.stdout.strip()
    if result.returncode != 0 or not line:
        return None
    try:
        return shlex.split(line, posix=False)
    except ValueError:
        return [line]


def _command_matches(actual, expected):
    """Match only two complete, recognized launcher command lines."""
    actual_launcher = _launcher_argv(actual)
    expected_launcher = _launcher_argv(expected)
    if actual_launcher is None or expected_launcher is None:
        return False
    # The executable may be an absolute interpreter path, but the launcher kind
    # and every service argument must be identical.  In particular, a one-item
    # or arbitrary argv record can never become an identity match.
    return actual_launcher == expected_launcher


def matches(pid: int, expected_argv, started_at) -> bool:
    """Check creation marker and command line before allowing termination."""
    try:
        if process_start_time(pid) != int(started_at):
            return False
    except (TypeError, ValueError):
        return False
    return _command_matches(process_command_line(pid), expected_argv)
