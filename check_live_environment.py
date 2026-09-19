"""Read-only platform readiness check for the isolated browser profile.
Opens each configured diagnosis platform and reports login heuristics.
No questions are submitted and no user data is touched.
"""
import json
import subprocess
import sys
from pathlib import Path
import workspace_store as store
import browser_engine as engine
from diagnosis_config import DIAGNOSIS_PLATFORMS


def main():
    only = sys.argv[1:] or [p['id'] for p in DIAGNOSIS_PLATFORMS]
    directory = store.ROOT / 'test-results/live-preflight'
    (directory / 'evidence').mkdir(parents=True, exist_ok=True)
    (directory / 'frozen-config.json').write_text(
        json.dumps({'platforms': [p for p in DIAGNOSIS_PLATFORMS if p['id'] in only]}), encoding='utf8')
    command = [sys.executable, str(store.ROOT / 'engine_worker.py'),
               str(engine.SCRIPTS / 'geo_driver.py'), 'preflight', str(directory), 'all']
    result = subprocess.run(command, env=engine.environment(), capture_output=True,
                            text=True, encoding='utf8', errors='replace', timeout=900)
    (directory / 'driver.log').write_text(result.stdout + '\n' + result.stderr, encoding='utf8')
    checks = engine.read_json(directory / 'preflight.json', [])
    summary = {'exit_code': result.returncode, 'questions_submitted': 0,
               'ready': [], 'need_login': [], 'failed': [],
               'checks': [{k: c.get(k) for k in ['platform', 'check', 'login_state_guess', 'error']} for c in checks]}
    for c in checks:
        if c.get('check') != 'OK':
            summary['failed'].append(c.get('platform'))
        elif c.get('login_state_guess') == 'logged_in':
            summary['ready'].append(c.get('platform'))
        else:
            summary['need_login'].append(c.get('platform'))
    (directory / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
