"""Verify the manual login window path used by the 打开登录窗口 button.

Runs browser_login.py in the isolated profile against a synthetic config, confirms the
window opens and signals readiness, then closes it. No platform credentials are read and
no questions are submitted.
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import browser_engine as engine
import workspace_store as store


def main():
    with tempfile.TemporaryDirectory(prefix='geo-login-window-') as tmp:
        run = Path(tmp)
        (run / 'frozen-config.json').write_text(
            json.dumps({'platforms': [{'id': 'deepseek', 'entry_url': 'https://chat.deepseek.com/'}]}),
            encoding='utf8')
        proc = subprocess.Popen(
            [sys.executable, str(store.ROOT / 'engine_worker.py'), str(store.ROOT / 'browser_login.py'), str(run)],
            env=engine.environment(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf8', errors='replace')
        opened = False
        try:
            for _ in range(60):
                if (run / 'login-open.flag').exists():
                    opened = True
                    break
                if proc.poll() is not None:
                    break
                time.sleep(1)
            print(json.dumps({'login_window_opened': opened, 'returncode': proc.poll()},
                             ensure_ascii=False))
        finally:
            if proc.poll() is None:
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               capture_output=True, timeout=30)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass


if __name__ == '__main__':
    main()
