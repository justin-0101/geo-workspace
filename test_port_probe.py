"""端口存活探测（ports.port_busy）的回归测试。

核心回归：设了 timeout 的 socket 是非阻塞的，Windows 上 connect_ex 会返回
10035(WSAEWOULDBLOCK) 表示"连接还在进行中"。旧写法把它当成"端口没在监听"，
会打印出 `listening:false` + `responding:true` 这种自相矛盾的状态。

测试手法：`_lying()` 先真的 connect 一次（让 socket 进入真实的连接完成/失败状态），
再把返回值谎报成"进行中"——这正是 Windows 上观察到的现象，而 SO_ERROR/select
反映的是真实状态，所以断言不会被 mock 掩盖。
"""
import errno
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import ports

_REAL_CONNECT_EX = socket.socket.connect_ex


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'ok'
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _start_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def _lying(code):
    """先执行真实 connect，再把返回值谎报成 code。"""
    def fake(self, address):
        try:
            _REAL_CONNECT_EX(self, address)
        except OSError:
            pass  # 真实失败已记进 SO_ERROR，正是我们要保留的状态
        return code
    return fake


class PortProbeTest(unittest.TestCase):
    def test_true_when_something_is_listening(self):
        server, port = _start_server()
        try:
            self.assertTrue(ports.port_busy(port=port))
        finally:
            server.shutdown()
            server.server_close()

    def test_false_when_nothing_is_listening(self):
        self.assertFalse(ports.port_busy(port=_free_port(), timeout=0.5))

    def test_in_progress_code_is_not_reported_as_dead(self):
        """核心回归：谎报 10035 时，真实在监听的端口必须仍判为"有"。"""
        server, port = _start_server()
        try:
            with mock.patch.object(socket.socket, 'connect_ex', _lying(errno.EWOULDBLOCK)):
                self.assertTrue(ports.port_busy(port=port))
        finally:
            server.shutdown()
            server.server_close()

    def test_in_progress_code_with_nobody_listening_stays_false(self):
        """反过来不能变成假阳性：谎报 10035 但端口真关着，必须是 False。"""
        port = _free_port()
        with mock.patch.object(socket.socket, 'connect_ex', _lying(errno.EWOULDBLOCK)):
            self.assertFalse(ports.port_busy(port=port, timeout=0.5))

    def test_refused_is_decided_without_waiting(self):
        """明确的拒绝不该再进 select 等一轮。"""
        port = _free_port()
        with mock.patch.object(socket.socket, 'connect_ex',
                               _lying(errno.ECONNREFUSED)), \
                mock.patch.object(ports.select, 'select') as selector:
            self.assertFalse(ports.port_busy(port=port, timeout=5.0))
            selector.assert_not_called()

    def test_in_progress_codes_cover_windows_and_posix(self):
        """WSAEWOULDBLOCK=10035 / EINPROGRESS=10036 / EALREADY=10037 都要在内。"""
        for value in (10035, 10036, 10037, errno.EWOULDBLOCK, errno.EINPROGRESS):
            self.assertIn(value, ports._IN_PROGRESS)

    def test_timeout_is_respected(self):
        started = time.monotonic()
        ports.port_busy(port=_free_port(), timeout=0.2)
        self.assertLess(time.monotonic() - started, 2.0)


if __name__ == '__main__':
    unittest.main()
