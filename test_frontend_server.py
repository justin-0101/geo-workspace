import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from http.server import ThreadingHTTPServer

from frontend_server import Handler


class FrontendAllowlistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def assert_not_found(self, method, path):
        request = urllib.request.Request(self.base + path, method=method)
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(context.exception.code, 404)

    def test_service_root_serves_workspace(self):
        response = urllib.request.urlopen(self.base + '/', timeout=3)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.url.endswith('/'))
        self.assertIn(b'<title>', response.read())

    def test_service_root_supports_head(self):
        request = urllib.request.Request(self.base + '/', method='HEAD')
        response = urllib.request.urlopen(request, timeout=3)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.headers.get('Content-Length'))

    def test_encoded_parent_path_cannot_escape_fonts_on_get(self):
        self.assert_not_found('GET', '/fonts/%2e%2e/README.md')

    def test_encoded_parent_path_cannot_escape_fonts_on_head(self):
        self.assert_not_found('HEAD', '/fonts/%2e%2e/workspace.js')

    def test_encoded_backslash_is_not_a_path_separator_bypass(self):
        self.assert_not_found('GET', '/fonts/%2e%2e%5cREADME.md')

    def test_question_generation_uses_extended_timeout_and_blocks_duplicates(self):
        source = (Path(__file__).with_name('workspace.js')).read_text(encoding='utf-8')
        self.assertIn('const QUESTION_SUGGESTION_TIMEOUT = 150000;', source)
        self.assertIn("api(base+'/question-suggestions','POST',{},QUESTION_SUGGESTION_TIMEOUT)", source)
        self.assertIn("button.disabled=true", source)

    def test_run_actions_and_navigation_use_approved_labels(self):
        root = Path(__file__).parent
        html = (root / 'workspace.html').read_text(encoding='utf-8')
        source = (root / 'workspace.js').read_text(encoding='utf-8')
        self.assertIn('<a href="#actions">优化清单</a>', html)
        self.assertIn("actions:'优化清单'", source)
        self.assertIn("['archive','终止诊断','danger']", source)
        self.assertNotIn('关闭诊断浏览器', source)
        self.assertNotIn("'close-browser'", source)


if __name__ == '__main__':
    unittest.main()
