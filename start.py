"""Start the loopback GEO workspace and print actionable health diagnostics.

Usage:  python start.py            start both services
        python start.py --status   report current state only
        python start.py --stop      stop only processes recorded by this launcher
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from ports import port_busy
from process_identity import process_start_time
from runtime_config import API_PORT, API_URL, FRONTEND_PORT, FRONTEND_URL, LOCAL_HOST

ROOT = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get('GEO_REDESIGN_DATA', str(ROOT / 'data'))).expanduser().resolve()
PID_FILE = LOG_DIR / 'service-pids.json'
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(url, service, timeout=3):
    """Return a health result and reject an unrelated process on our port."""
    try:
        with NO_PROXY.open(url, timeout=timeout) as response:
            marker = response.headers.get('X-GEO-Service')
            if marker != service:
                return {'listening': True, 'responding': False,
                        'error': f'端口已有非 GEO 服务（X-GEO-Service={marker or "缺失"}）'}
            return {'listening': True, 'responding': response.status < 500}
    except Exception as exc:
        busy = port_busy(host=LOCAL_HOST, port=int(url.rsplit(':', 1)[1].split('/', 1)[0]))
        return {'listening': busy, 'responding': False,
                'error': '服务未响应，请查看 data/*.log' if busy else '未监听'}


def status():
    front = probe(f'{FRONTEND_URL}/workspace.html', 'frontend')
    api = probe(f'{API_URL}/api/projects', 'api')
    front['port'], api['port'] = FRONTEND_PORT, API_PORT
    return {'frontend': front, 'api': api}


def spawn(args, label):
    log = open(LOG_DIR / f'{label}.log', 'ab')
    proc = subprocess.Popen(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL,
                            creationflags=0x00000008 if sys.platform == 'win32' else 0,
                            env=os.environ.copy())
    log.close()
    return proc


def save_pids(processes):
    """Persist enough identity data to make --stop safe after PID reuse."""
    records = {}
    for label, proc in processes:
        argv = list(proc.args) if isinstance(proc.args, (list, tuple)) else []
        created = None
        # A just-created Windows process may not yet be queryable.  Retry briefly;
        # an unknown marker is intentionally left non-killable by stop.py.
        for _ in range(20):
            created = process_start_time(proc.pid)
            if created is not None:
                break
            time.sleep(0.05)
        records[label] = {
            'pid': proc.pid,
            'argv': argv,
            'created': created,
        }
    PID_FILE.write_text(json.dumps(records, indent=2), encoding='utf-8')


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if '--stop' in sys.argv:
        from stop import stop_recorded
        stopped = stop_recorded(PID_FILE)
        print(json.dumps({'stopped': stopped}, ensure_ascii=False, indent=2))
        return 0
    state = status()
    if '--status' in sys.argv:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0 if all(v['responding'] for v in state.values()) else 1

    conflicts = [f"{name} {item['port']}: {item['error']}" for name, item in state.items()
                 if item.get('listening') and not item.get('responding')]
    if conflicts:
        print('启动中止：' + '；'.join(conflicts), file=sys.stderr)
        return 2

    processes = []
    if not state['frontend']['responding']:
        processes.append(('frontend', spawn([sys.executable, 'frontend_server.py'], 'frontend')))
    if not state['api']['responding']:
        processes.append(('workflow-api', spawn([
            sys.executable, '-m', 'uvicorn', 'workflow_api:app',
            '--host', LOCAL_HOST, '--port', str(API_PORT)], 'workflow-api')))
    if processes:
        save_pids(processes)

    for _ in range(40):
        current = status()
        if all(v['responding'] for v in current.values()):
            break
        time.sleep(0.5)
    state = status()
    print(json.dumps(state, ensure_ascii=False, indent=2))
    ok = all(v['responding'] for v in state.values())
    print(f'打开 {FRONTEND_URL}/' if ok else '启动未完成，请查看 data/*.log')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
