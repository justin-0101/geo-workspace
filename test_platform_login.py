"""登录态识别与会话的单测：不打开浏览器，不接触真实数据。

真实浏览器行为（窗口真的打开、用户登录后真的被识别）只能在实机验证，
这里的假页面对象只覆盖判定分支与状态读写，避免把“测试通过”当成“登录可用”。
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path

import browser_publisher as bp
import platform_login as pl
import workspace_store as store
from process_guard import ProcessGuard

FILLER = '这是一段足够长的页面正文，用来表示页面已经加载完成而不是空白页。' * 12   # > MIN_TEXT
SPEC = bp.TARGETS['zhihu']


class FakePage:
    """只实现 detect 用到的那几件事：url / inner_text / query_selector。"""

    def __init__(self, url='', text='', editor=False, password=False):
        self.url = url
        self.text = text
        self.editor = editor
        self.password = password

    def inner_text(self, selector, timeout=None):
        return self.text

    def query_selector(self, selector):
        if self.password and selector == 'input[type=password]':
            return object()
        if self.editor:
            return object()
        return None


class AliveThread:
    def is_alive(self):
        return True


class DetectTests(unittest.TestCase):
    def test_editor_ready_is_logged_in(self):
        state, note = pl.detect(FakePage('https://zhuanlan.zhihu.com/write', FILLER, editor=True), SPEC)
        self.assertEqual(state, 'logged_in')
        self.assertIn('编辑器', note)

    def test_login_url_is_needs_login(self):
        state, _ = pl.detect(FakePage('https://www.zhihu.com/signin?next=%2Fwrite', FILLER), SPEC)
        self.assertEqual(state, 'needs_login')

    def test_password_box_is_needs_login(self):
        state, _ = pl.detect(FakePage('https://mp.dayu.com/', FILLER, password=True), SPEC)
        self.assertEqual(state, 'needs_login')

    def test_strong_marker_is_needs_login(self):
        state, _ = pl.detect(FakePage('https://mp.dayu.com/', FILLER + '扫码登录', password=False), SPEC)
        self.assertEqual(state, 'needs_login')

    def test_weak_marker_alone_is_still_needs_login(self):
        """只有“登录”两个字时宁可报未登录，也不能谎报已登录。"""
        state, _ = pl.detect(FakePage('https://mp.sohu.com/', FILLER + '登录', password=False), SPEC)
        self.assertEqual(state, 'needs_login')

    def test_left_login_page_without_editor_is_session_only(self):
        state, note = pl.detect(FakePage('https://zhuanlan.zhihu.com/creator', FILLER), SPEC)
        self.assertEqual(state, 'session_only')
        self.assertIn('编辑器未识别', note)

    def test_blank_or_thin_page_is_unknown(self):
        self.assertEqual(pl.detect(FakePage('about:blank', ''), SPEC)[0], 'unknown')
        self.assertEqual(pl.detect(FakePage('https://zhuanlan.zhihu.com/write', '短'), SPEC)[0], 'unknown')
        self.assertEqual(pl.detect(FakePage('', FILLER), SPEC)[0], 'unknown')


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = store.DATA, store.DB
        store.DATA = Path(self.tmp.name)
        store.DB = store.DATA / 'test.db'

    def tearDown(self):
        store.DATA, store.DB = self.old
        self.tmp.cleanup()

    def test_state_path_follows_data_dir(self):
        """路径必须延迟计算，否则测试会写进真实数据目录。"""
        self.assertEqual(pl.state_path(), store.DATA / 'platform-login-state.json')

    def test_record_and_snapshot(self):
        pl.record('zhihu', 'logged_in', '编辑器可识别', 'https://zhuanlan.zhihu.com/write')
        pl.record('csdn', 'needs_login', '仍在登录页')
        snap = pl.snapshot(['zhihu', 'csdn', 'sohu'])
        self.assertEqual(snap['states']['zhihu']['label'], '已登录')
        self.assertEqual(snap['states']['zhihu']['tone'], 'good')
        self.assertEqual(snap['states']['csdn']['label'], '需登录')
        self.assertEqual(snap['states']['sohu']['state'], 'unknown')      # 没记录的算未检测
        self.assertEqual(snap['summary'], {'total': 3, 'logged_in': 1, 'needs_login': 1,
                                           'unknown': 1, 'session_only': 0})
        self.assertFalse(snap['session']['active'])
        self.assertTrue(json.loads(pl.state_path().read_text(encoding='utf8'))['platforms']['zhihu'])

    def test_corrupt_state_file_does_not_crash(self):
        pl.state_path().write_text('{不是 json', encoding='utf8')
        self.assertEqual(pl.read()['platforms'], {})
        self.assertEqual(pl.snapshot(['zhihu'])['states']['zhihu']['state'], 'unknown')

    def test_start_refuses_second_window(self):
        old = dict(pl._RUNTIME)
        pl._RUNTIME.update(thread=AliveThread(), ids=['zhihu'], started_at='2026-09-20T08:00:00')
        try:
            out = pl.start(['zhihu'])
            self.assertFalse(out['started'])
            self.assertIn('已经打开', out['message'])
        finally:
            pl._RUNTIME.update(old)

    def test_probe_refuses_when_browser_busy(self):
        """窗口或诊断占着 profile 时不抢锁，直接报错让用户先关窗口。"""
        guard = ProcessGuard(store.DATA / 'browser-execution.lock').acquire()
        try:
            with self.assertRaises(ValueError):
                pl.probe(['zhihu'])
        finally:
            guard.release()

    def test_finish_keeps_last_session_summary(self):
        pl._finish('completed', '已识别 7 个平台登录完成')
        session = pl.snapshot(['zhihu'])['session']
        self.assertEqual(session['result'], 'completed')
        self.assertIn('7 个平台', session['message'])


if __name__ == '__main__':
    unittest.main()
