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


if __name__ == '__main__':
    unittest.main()
