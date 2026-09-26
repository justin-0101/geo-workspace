"""Start the local GEO workspace (whitelist frontend + API) and verify both are up.

Usage:  python start.py            start both services
        python start.py --status   report current state only
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from ports import port_busy

ROOT = Path(__file__).resolve().parent
FRONT_PORT, API_PORT = 4173, 8798
LOG_DIR = ROOT / 'data'
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def alive(url, timeout=3):
    try:
        with NO_PROXY.open(url, timeout=timeout) as response:
            return response.status < 500
    except Exception:
        return False


def status():
    return {
        'frontend': {'port': FRONT_PORT, 'listening': port_busy(port=FRONT_PORT),
                     'responding': alive(f'http://127.0.0.1:{FRONT_PORT}/workspace.html')},
        'api': {'port': API_PORT, 'listening': port_busy(port=API_PORT),
                'responding': alive(f'http://127.0.0.1:{API_PORT}/api/projects')},
    }


def spawn(args):
    log = open(LOG_DIR / ('frontend.log' if 'frontend' in ' '.join(args) else 'workflow-api.log'), 'ab')
    return subprocess.Popen(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, creationflags=0x00000008)


def main():
    LOG_DIR.mkdir(exist_ok=True)
    state = status()
    if '--status' in sys.argv:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return 0 if all(v['responding'] for v in state.values()) else 1

    if not state['frontend']['listening']:
        spawn([sys.executable, 'frontend_server.py'])
    if not state['api']['listening']:
        spawn([sys.executable, '-m', 'uvicorn', 'workflow_api:app',
               '--host', '127.0.0.1', '--port', str(API_PORT)])

    for _ in range(40):
        if all(v['responding'] for v in status().values()):
            break
        time.sleep(0.5)
    state = status()  # 出结果后再测一次：不打印循环中间态的旧快照
    print(json.dumps(state, ensure_ascii=False, indent=2))
    ok = all(v['responding'] for v in state.values())
    print('打开 http://127.0.0.1:4173/ ' if ok else '启动未完成，请查看 data/*.log')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
