import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import process_identity
import stop

class ServiceProcessIdentityTests(unittest.TestCase):
    def test_malformed_record_is_not_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'service-pids.json'
            path.write_text('[]', encoding='utf-8')
            with patch('stop.os.kill') as kill:
                self.assertEqual(stop.stop_recorded(path), [])
                kill.assert_not_called()

    def test_pid_only_legacy_record_is_not_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'service-pids.json'
            path.write_text(json.dumps({'frontend': 12345}), encoding='utf-8')
            with patch('stop.os.kill') as kill:
                self.assertEqual(stop.stop_recorded(path), [])
                kill.assert_not_called()

    def test_malformed_argv_shapes_are_not_killed_even_if_matches_is_patched(self):
        for argv in ('python frontend_server.py', [], ['python.exe']):
            with self.subTest(argv=argv), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'service-pids.json'
                path.write_text(json.dumps({'frontend': {
                    'pid': 12345, 'argv': argv, 'created': 99,
                }}), encoding='utf-8')
                with patch('stop.matches', return_value=True) as matches, patch('stop.os.kill') as kill:
                    self.assertEqual(stop.stop_recorded(path), [])
                    matches.assert_not_called()
                    kill.assert_not_called()

    def test_current_pid_and_forged_marker_require_launcher_command(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'service-pids.json'
            path.write_text(json.dumps({'frontend': {
                'pid': os.getpid(),
                'argv': ['python.exe', 'frontend_server.py'],
                'created': 123,
            }}), encoding='utf-8')
            with patch('process_identity.process_start_time', return_value=123), \
                    patch('process_identity.process_command_line',
                          return_value=['python.exe', '-c', 'unrelated']), \
                    patch('stop.os.kill') as kill:
                self.assertEqual(stop.stop_recorded(path), [])
                kill.assert_not_called()

    def test_string_creation_marker_is_normalised_not_treated_as_stale(self):
        """审查 note：记录里若把创建标记写成字符串，直接比会误判成陈旧记录而删掉。

        后果很重：删掉记录 = 还活着的服务再也无法通过启动器停止。
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'service-pids.json'
            path.write_text(json.dumps({'frontend': {
                'pid': 12345,
                'argv': ['python.exe', 'frontend_server.py'],
                'created': '99',
            }}), encoding='utf-8')
            with patch('stop.matches', return_value=False), \
                    patch('stop.process_start_time', return_value=99), \
                    patch('stop.os.kill') as kill:
                self.assertEqual(stop.stop_recorded(path), [])
                kill.assert_not_called()
            self.assertTrue(path.exists(), '字符串标记把活着的进程误判成陈旧记录，记录被删了')

    def test_command_match_requires_complete_launcher_argv(self):
        self.assertFalse(process_identity._command_matches(
            ['python.exe', 'frontend_server.py'], 'python.exe frontend_server.py'))
        self.assertFalse(process_identity._command_matches(
            ['python.exe', 'frontend_server.py'], []))
        self.assertFalse(process_identity._command_matches(
            ['python.exe', 'frontend_server.py'], ['python.exe']))
        self.assertTrue(process_identity._command_matches(
            ['python.exe', 'frontend_server.py'], ['python.exe', 'frontend_server.py']))

    def test_frontend_launcher_requires_exact_script_argument(self):
        recorded = ['python.exe', 'frontend_server.py']
        self.assertTrue(process_identity._command_matches(recorded, recorded))
        self.assertFalse(process_identity._command_matches(
            ['python.exe', r'C:\other\frontend_server.py'], recorded))

    def test_matching_record_is_terminated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'service-pids.json'
            path.write_text(json.dumps({'frontend': {
                'pid': 12345,
                'argv': ['python.exe', 'frontend_server.py'],
                'created': 99,
            }}), encoding='utf-8')
            with patch('stop.matches', return_value=True), patch('stop.os.kill') as kill:
                self.assertEqual(stop.stop_recorded(path), ['frontend:12345'])
                kill.assert_called_once()
            self.assertFalse(path.exists())

    def test_mismatched_identity_is_left_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'service-pids.json'
            path.write_text(json.dumps({'frontend': {
                'pid': 12345,
                'argv': ['python.exe', 'frontend_server.py'],
                'created': 99,
            }}), encoding='utf-8')
            with patch('stop.matches', return_value=False), patch('stop.os.kill') as kill:
                self.assertEqual(stop.stop_recorded(path), [])
                kill.assert_not_called()

    def test_live_but_unverifiable_process_keeps_the_record(self):
        """真实事故：PowerShell 查询超时→身份核验失败，但 stop.py 仍然删掉了 PID 记录，
        于是两个还在跑的服务再也不能通过本启动器停掉。

        进程还活着（创建标记与记录一致）时必须保留记录，以便重试。
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'service-pids.json'
            path.write_text(json.dumps({'frontend': {
                'pid': 12345,
                'argv': ['python.exe', 'frontend_server.py'],
                'created': 99,
            }}), encoding='utf-8')
            notes = []
            with patch('stop.matches', return_value=False), \
                    patch('stop.process_start_time', return_value=99), \
                    patch('stop.os.kill') as kill:
                self.assertEqual(stop.stop_recorded(path, notes), [])
                kill.assert_not_called()
            self.assertTrue(path.exists(), '记录被删掉了，服务将无法再被停止')
            self.assertEqual(notes, ['frontend:12345 仍在运行但身份无法核验，未终止；记录已保留以便重试'])

    def test_record_of_a_vanished_process_is_dropped(self):
        """记录已陈旧（进程不在了）时才可以丢弃，否则会留下无法清理的残记录。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'service-pids.json'
            path.write_text(json.dumps({'frontend': {
                'pid': 12345,
                'argv': ['python.exe', 'frontend_server.py'],
                'created': 99,
            }}), encoding='utf-8')
            with patch('stop.matches', return_value=False), \
                    patch('stop.process_start_time', return_value=None), \
                    patch('stop.os.kill') as kill:
                self.assertEqual(stop.stop_recorded(path), [])
                kill.assert_not_called()
            self.assertFalse(path.exists())

    def test_command_line_query_allows_a_cold_powershell_start(self):
        """真实事故：CIM 查询实测 3.9 秒，而超时预算只有 3 秒，导致每次核验都超时。"""
        with patch('process_identity.subprocess.run') as run:
            run.return_value = unittest.mock.Mock(returncode=0, stdout='', stderr='')
            process_identity.process_command_line(12345)
        self.assertGreaterEqual(run.call_args.kwargs.get('timeout', 0), 10)


if __name__ == '__main__':
    unittest.main()
