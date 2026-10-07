"""Stop only GEO service processes recorded by start.py."""
import json
import os
import signal
import sys
import time
from pathlib import Path

from process_identity import matches, process_start_time, valid_launcher_argv


def stop_recorded(path, notes=None):
    """Stop the processes recorded in ``path``; return ``label:pid`` strings.

    The record is dropped only when nothing was left pending.  A process that is still
    alive with the recorded creation marker but failed the command-line check keeps its
    record: that means the identity check itself failed (for example the PowerShell query
    timed out), not that the record is stale.  Dropping it there would silently delete the
    only handle on a running service and make it unstoppable through this launcher.
    """
    path = Path(path)
    try:
        records = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if not isinstance(records, dict):
        return []
    stopped = []
    kept = []
    for label, value in records.items():
        # Older PID-only records are deliberately not killable: a reused PID
        # could belong to an unrelated process.  start.py writes identity data.
        if not isinstance(value, dict):
            continue
        try:
            pid = int(value['pid'])
            argv = value['argv']
            # 与 matches() 一致：创建标记统一按 int 比较。记录里若写成字符串，
            # 直接比会把「还活着的进程」误判成陈旧记录而被删掉。
            created = int(value['created'])
        except (KeyError, TypeError, ValueError):
            continue
        # Validate the persisted shape before consulting live process data.  A
        # legacy/string/arbitrary argv must never turn a matching marker into a
        # permission to terminate a process.
        if pid <= 0 or not valid_launcher_argv(argv):
            continue
        if not matches(pid, argv, created):
            # Alive with the recorded marker -> the verification failed, not the record.
            if process_start_time(pid) == created:
                kept.append(f'{label}:{pid}')
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(f'{label}:{pid}')
        except (OSError, ValueError, TypeError):
            continue
    if kept:
        if notes is not None:
            notes.extend(f'{item} 仍在运行但身份无法核验，未终止；记录已保留以便重试' for item in kept)
    else:
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
    notes = []
    stopped = stop_recorded(data / 'service-pids.json', notes)
    print(json.dumps({'stopped': stopped, 'kept': notes}, ensure_ascii=False, indent=2))
