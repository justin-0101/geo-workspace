import threading
import unittest
import urllib.error
import urllib.request

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

    def test_encoded_parent_path_cannot_escape_fonts_on_get(self):
        self.assert_not_found('GET', '/fonts/%2e%2e/README.md')

    def test_encoded_parent_path_cannot_escape_fonts_on_head(self):
        self.assert_not_found('HEAD', '/fonts/%2e%2e/workspace.js')

    def test_encoded_backslash_is_not_a_path_separator_bypass(self):
        self.assert_not_found('GET', '/fonts/%2e%2e%5cREADME.md')


if __name__ == '__main__':
    unittest.main()
