import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import browser_paths
import runtime_config
from check_install import REQUIRED_RUNTIME_ASSETS, REQUIRED_SOURCE_FILES

ROOT = Path(__file__).resolve().parent


class DeploymentReadinessTests(unittest.TestCase):
    def test_manifest_declares_runtime_imports(self):
        manifest = (ROOT / 'requirements.txt').read_text(encoding='utf-8')
        for package in ('fastapi', 'uvicorn', 'playwright', 'python-multipart',
                        'pypdf', 'python-docx', 'openpyxl', 'lxml', 'numpy',
                        'opencv-python', 'Pillow', 'PyMuPDF', 'rapidocr-onnxruntime'):
            self.assertIn(package, manifest)

    def test_required_deliverable_files_are_present(self):
        required = (*REQUIRED_SOURCE_FILES,
                    'skill/geo-diagnosis-single/scripts/geo_driver.py',
                    'skill/geo-diagnosis-single/scripts/validate-run.ps1')
        for relative in required:
            self.assertTrue((ROOT / relative).is_file(), relative)
        self.assertIn('geo-config.js', REQUIRED_RUNTIME_ASSETS)

    def test_workspace_runtime_assets_are_manifested(self):
        for relative in ('workspace.css', 'workspace.js',
                         'fonts/geist-latin-wght-normal.woff2',
                         'fonts/geist-latin-ext-wght-normal.woff2',
                         'fonts/noto-sans-sc-subset.woff2',
                         'fonts/OFL-Geist.txt', 'fonts/OFL-NotoSansSC.txt'):
            self.assertIn(relative, REQUIRED_SOURCE_FILES)
        self.assertIn('geo-config.js', REQUIRED_RUNTIME_ASSETS)

    def test_chrome_override_is_portable(self):
        with patch.dict(os.environ, {'GEO_CHROME_PATH': str(ROOT / 'does-not-exist.exe')}):
            self.assertIsNone(browser_paths.chrome_executable())

    def test_bind_host_rejects_non_loopback(self):
        result = subprocess.run(
            [sys.executable, '-c', 'import runtime_config'],
            cwd=ROOT, env={**os.environ, 'GEO_BIND_HOST': '0.0.0.0'},
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('loopback', result.stderr)

    def test_default_ports_remain_loopback_contract(self):
        self.assertEqual(runtime_config.LOCAL_HOST, '127.0.0.1')
        self.assertEqual(runtime_config.FRONTEND_PORT, 4173)
        self.assertEqual(runtime_config.API_PORT, 8798)

    def test_ipv6_loopback_is_explicitly_rejected(self):
        result = subprocess.run(
            [sys.executable, '-c', 'import runtime_config'],
            cwd=ROOT, env={**os.environ, 'GEO_BIND_HOST': '::1'},
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('IPv6', result.stderr)

    def test_frontend_and_api_ports_must_differ(self):
        result = subprocess.run(
            [sys.executable, '-c', 'import runtime_config'],
            cwd=ROOT, env={**os.environ, 'GEO_FRONTEND_PORT': '9000', 'GEO_API_PORT': '9000'},
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('must be different', result.stderr)

    def test_typography_checker_uses_runtime_urls(self):
        source = (ROOT / 'check_typography.py').read_text(encoding='utf-8')
        self.assertIn('from runtime_config import API_URL, FRONTEND_URL', source)
        self.assertNotIn("'http://127.0.0.1:8798/api/projects'", source)
        self.assertNotIn("'http://127.0.0.1:4173/workspace.html", source)


if __name__ == '__main__':
    unittest.main()
