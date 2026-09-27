"""Deterministic clean-machine preflight for the local GEO workspace.

Use a throwaway data directory for this check. It never opens SQLite or browser
profiles and only creates/removes a small write-test file in the selected directory.
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

from browser_paths import chrome_executable
from frontend_server import PUBLIC
from ports import port_busy
from runtime_config import API_PORT, FRONTEND_PORT, LOCAL_HOST

ROOT = Path(__file__).resolve().parent
REQUIRED_IMPORTS = {
    'fastapi': 'fastapi', 'uvicorn': 'uvicorn', 'pydantic': 'pydantic',
    'httpx': 'httpx', 'playwright': 'playwright', 'pypdf': 'pypdf',
    'python-docx': 'docx', 'openpyxl': 'openpyxl', 'lxml': 'lxml',
    'numpy': 'numpy', 'opencv-python': 'cv2', 'Pillow': 'PIL',
    'PyMuPDF': 'fitz', 'rapidocr-onnxruntime': 'rapidocr_onnxruntime',
    'python-multipart': 'multipart',
}
ENGINE_FILES = ('geo_driver.py', 'geo_run.py', 'render-report.py',
                'validate-run.ps1', 'update-task-state.ps1')
# Keep this release-path manifest explicit: these files are required by the
# shipped launcher/API and must not be omitted by a clean Git export.
REQUIRED_SOURCE_FILES = (
    'cover_gen.py', 'browser_paths.py', 'process_identity.py', 'runtime_config.py',
    'requirements.txt', 'install.ps1', 'check_install.py', 'start.py', 'stop.py',
    'frontend_server.py',
    # Static assets referenced by workspace.html/workspace.css.
    'workspace.html', 'workspace.css', 'workspace.js',
    'fonts/geist-latin-wght-normal.woff2',
    'fonts/geist-latin-ext-wght-normal.woff2',
    'fonts/noto-sans-sc-subset.woff2',
    'fonts/OFL-Geist.txt', 'fonts/OFL-NotoSansSC.txt',
)
# Generated per request by frontend_server.py; it is a runtime asset rather
# than a file copied from the source tree.
REQUIRED_RUNTIME_ASSETS = ('geo-config.js',)


def check(data_dir, require_chrome=True):
    checks = []
    checks.append(('python-3.11+', sys.version_info >= (3, 11), sys.version.split()[0]))
    missing = [name for name, module in REQUIRED_IMPORTS.items()
               if importlib.util.find_spec(module) is None]
    checks.append(('python-dependencies', not missing, ', '.join(missing) or 'all imports available'))
    scripts = Path(os.environ.get('GEO_REDESIGN_ENGINE', str(ROOT / 'skill' / 'geo-diagnosis-single' / 'scripts')))
    missing_files = [name for name in ENGINE_FILES if not (scripts / name).is_file()]
    checks.append(('diagnosis-engine-files', not missing_files, ', '.join(missing_files) or str(scripts)))
    missing_source = [name for name in REQUIRED_SOURCE_FILES if not (ROOT / name).is_file()]
    checks.append(('deliverable-source-files', not missing_source, ', '.join(missing_source) or 'application and notices present'))
    missing_runtime = [name for name in REQUIRED_RUNTIME_ASSETS if name not in PUBLIC]
    checks.append(('runtime-assets', not missing_runtime,
                   ', '.join(missing_runtime) or 'generated assets are served by frontend_server.py'))
    chrome = chrome_executable()
    checks.append(('chrome', chrome is not None or not require_chrome,
                   str(chrome) if chrome else 'not found; set GEO_CHROME_PATH'))
    data_dir = Path(data_dir).expanduser().resolve()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / '.geo-install-write-test'
        probe.write_text('ok', encoding='ascii')
        probe.unlink()
        writable = True
        detail = str(data_dir)
    except OSError as exc:
        writable = False
        detail = str(exc)
    checks.append(('data-directory-writable', writable, detail))
    for label, port in (('frontend-port', FRONTEND_PORT), ('api-port', API_PORT)):
        busy = port_busy(host=LOCAL_HOST, port=port)
        checks.append((label, not busy, f'{LOCAL_HOST}:{port} is free' if not busy else f'{LOCAL_HOST}:{port} is occupied'))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=os.environ.get('GEO_REDESIGN_DATA', str(ROOT / 'data')))
    parser.add_argument('--skip-chrome', action='store_true', help='only for UI/API-only validation')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    results = check(args.data_dir, require_chrome=not args.skip_chrome)
    payload = {'ok': all(ok for _, ok, _ in results),
               'checks': [{'name': name, 'ok': ok, 'detail': detail} for name, ok, detail in results]}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in payload['checks']:
            print(f"{'PASS' if item['ok'] else 'FAIL'} {item['name']}: {item['detail']}")
    return 0 if payload['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
