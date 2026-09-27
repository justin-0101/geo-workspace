"""Stop only GEO service processes recorded by start.py."""
import json
import os
import signal
import sys
import time
from pathlib import Path

from process_identity import matches, valid_launcher_argv


def stop_recorded(path):
    path = Path(path)
    try:
        records = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if not isinstance(records, dict):
        return []
    stopped = []
    for label, value in records.items():
        # Older PID-only records are deliberately not killable: a reused PID
        # could belong to an unrelated process.  start.py writes identity data.
        if not isinstance(value, dict):
            continue
        try:
            pid = int(value['pid'])
            argv = value['argv']
            created = value['created']
        except (KeyError, TypeError, ValueError):
            continue
        # Validate the persisted shape before consulting live process data.  A
        # legacy/string/arbitrary argv must never turn a matching marker into a
        # permission to terminate a process.
        if pid <= 0 or not valid_launcher_argv(argv):
            continue
        if not matches(pid, argv, created):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(f'{label}:{pid}')
        except (OSError, ValueError, TypeError):
            continue
    try:
        path.unlink()
    except OSError:
        pass
    # Windows child processes receive termination through the launcher-owned PID;
    # wait briefly so --status does not report a transient stale listener.
    time.sleep(0.2)
    return stopped


if __name__ == '__main__':
    data = Path(os.environ.get('GEO_REDESIGN_DATA', str(Path(__file__).resolve().parent / 'data')))
    print(json.dumps({'stopped': stop_recorded(data / 'service-pids.json')}, ensure_ascii=False, indent=2))
