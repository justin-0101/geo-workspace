"""Run with unittest; all writes use a temporary database, never user data."""
import tempfile
import unittest
from pathlib import Path
import workspace_store as store
from diagnosis_config import freeze_config, suggest_questions, DIAGNOSIS_PLATFORMS, PUBLISH_PLATFORMS


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = store.DATA, store.DB
        store.DATA = Path(self.tmp.name)
        store.DB = store.DATA / 'test.db'
        store.migrate()
        with store.connection() as c:
            c.execute('INSERT INTO projects VALUES(?,?,?,?)', ('test-a','测试主体','enterprise',store.now()))
            c.execute('INSERT INTO projects VALUES(?,?,?,?)', ('test-b','另一主体','enterprise',store.now()))
        self.profile = dict(canonical_name='测试主体', business='设备维护', region='广州', audience='工厂',
                            official_pages=[], aliases=[], no_official_web_presence=True)

    def tearDown(self):
        store.DATA, store.DB = self.old
        self.tmp.cleanup()

    def test_additive_migration(self):
        store.migrate()
        with store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 2)

    def test_isolation_and_optimistic_save(self):
        self.assertEqual(store.save_profile('test-a', self.profile, [], ['deepseek'], 0), 1)
        self.assertEqual(store.get_profile('test-b')['profile'], {})
        with self.assertRaises(ValueError): store.save_profile('test-a', {}, [], [], 0)
        self.assertEqual(store.get_profile('test-a')['profile'], self.profile)

    def test_configuration(self):
        questions = suggest_questions(self.profile)
        config = freeze_config(self.profile, questions, [p['id'] for p in DIAGNOSIS_PLATFORMS])
        self.assertEqual(config['scope']['expected_tasks'], 32)
        self.assertEqual(len({p['id'] for p in PUBLISH_PLATFORMS}), 12)
        with self.assertRaises(ValueError): freeze_config(self.profile, questions, ['wechat_mp'])
        questions[0]['prompt'] += '测试主体'
        with self.assertRaises(ValueError): freeze_config(self.profile, questions, ['deepseek'])

    def test_frozen_run_isolation_and_lock(self):
        from diagnosis_runs import create_frozen_run, verify_frozen_run, get_run
        questions = suggest_questions(self.profile)
        store.save_profile('test-a', self.profile, questions, ['deepseek'], 0)
        with self.assertRaises(ValueError): create_frozen_run('test-a', 1, False)
        run = create_frozen_run('test-a', 1, True)
        directory = verify_frozen_run('test-a', run)
        self.assertEqual(len((directory/'tasks.jsonl').read_text(encoding='utf8').splitlines()), 8)
        with self.assertRaises(ValueError): create_frozen_run('test-a', 1, True)
        with self.assertRaises(KeyError): get_run('test-b', run)
        (directory/'frozen-config.json').write_text('{}', encoding='utf8')
        with self.assertRaises(ValueError): verify_frozen_run('test-a', run)

    def test_official_source_confirmation(self):
        questions = suggest_questions(self.profile)
        self.profile['no_official_web_presence'] = False
        with self.assertRaises(ValueError): freeze_config(self.profile, questions, ['deepseek'])

    def test_delete_cascades_only_target(self):
        for slug in ['test-a','test-b']: store.save_profile(slug, self.profile, [], [], 0)
        with store.connection() as c: c.execute('DELETE FROM projects WHERE slug=?', ('test-a',))
        self.assertEqual(store.get_profile('test-b')['revision'], 1)
        with store.connection() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM project_profiles').fetchone()[0], 1)


if __name__ == '__main__': unittest.main()
