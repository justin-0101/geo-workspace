import tempfile
from pathlib import Path
import unittest
from fastapi.testclient import TestClient
import workspace_store as store
from workflow_api import app
from diagnosis_config import suggest_questions

class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=store.DATA,store.DB
        store.DATA=Path(self.tmp.name);store.DB=store.DATA/'test.db'
        self.client=TestClient(app);self.client.__enter__()
    def tearDown(self):
        self.client.__exit__(None,None,None)
        store.DATA,store.DB=self.old;self.tmp.cleanup()
    def create(self,name='测试项目'):
        r=self.client.post('/api/projects',json={'name':name})
        self.assertEqual(r.status_code,201);return r.json()['slug']
    def test_project_lifecycle(self):
        a=self.create();b=self.create('另一项目')
        self.assertEqual(len(self.client.get('/api/projects').json()['projects']),2)
        r=self.client.request('DELETE','/api/projects/'+a,json={'confirm':False})
        self.assertEqual(r.status_code,409)
        r=self.client.request('DELETE','/api/projects/'+a,json={'confirm':True})
        self.assertEqual(r.status_code,200)
        self.assertEqual(self.client.get('/api/projects/'+b).status_code,200)
        self.assertEqual(self.client.get('/api/projects/'+a).status_code,404)
    def test_profile_freeze_and_cross_project(self):
        a=self.create();b=self.create('另一项目')
        base='/api/projects/'+a
        profile=dict(canonical_name='测试项目',business='设备维护',region='广州',audience='工厂',aliases=[],official_pages=[],no_official_web_presence=True)
        data=dict(profile=profile,questions=suggest_questions(profile),platforms=['deepseek'],revision=0)
        self.assertEqual(self.client.put(base+'/profile',json=data).status_code,200)
        self.assertEqual(self.client.put(base+'/profile',json=data).status_code,409)
        r=self.client.post(base+'/runs',json={'revision':1,'confirm':True})
        self.assertEqual(r.status_code,201);rid=r.json()['id']
        self.assertEqual(self.client.post(base+'/runs',json={'revision':1,'confirm':True}).status_code,409)
        run=self.client.get(base+'/runs/'+rid)
        self.assertEqual(run.status_code,200);self.assertEqual(len(run.json()['tasks']),8)
        self.assertEqual(self.client.get('/api/projects/'+b+'/runs/'+rid).status_code,404)
        self.assertEqual(self.client.post(base+'/runs/'+rid+'/execute',json={'confirm':True}).status_code,409)
        self.assertEqual(len(self.client.get('/api/workbench').json()['pending']),1)
    def test_empty_workbench_platforms(self):
        self.assertEqual(self.client.get('/api/workbench').json(),{'pending':[],'events':[]})
        data=self.client.get('/api/platforms').json()
        self.assertEqual(len(data['diagnosis']),4);self.assertEqual(len(data['publication']),10)

    def test_login_state_snapshot_covers_browser_platforms(self):
        from unittest.mock import patch
        r=self.client.get('/api/platforms/login-state')
        self.assertEqual(r.status_code,200);d=r.json()
        browser={s['id'] for s in self.client.get('/api/platforms').json()['publication'] if s['mode']=='browser'}
        self.assertEqual(set(d['states']),browser)          # 只列可登录的浏览器平台
        self.assertTrue(all(v['state']=='unknown' for v in d['states'].values()))
        self.assertEqual(d['states']['zhihu']['label'],'未检测')
        self.assertFalse(d['session']['active'])
        self.assertEqual(d['summary']['total'],len(browser))
        # 打开登录窗口：把真正的浏览器启动换掉，只验证接口契约。
        with patch('platform_login.start',return_value={'started':True,'message':'已打开登录窗口（知乎）'}) as start:
            r=self.client.post('/api/platforms/login',json={'platforms':['zhihu']})
        self.assertEqual(r.status_code,200);body=r.json()
        self.assertTrue(body['started']);self.assertIn('知乎',body['message'])
        self.assertIn('states',body)                        # 返回完整快照，前端可直接重绘
        start.assert_called_once_with(['zhihu'])

    def test_login_probe_refuses_while_browser_busy(self):
        from process_guard import ProcessGuard
        guard=ProcessGuard(store.DATA/'browser-execution.lock').acquire()
        try:
            r=self.client.post('/api/platforms/login-state/probe',json={'platforms':['zhihu']})
            self.assertEqual(r.status_code,409)
            self.assertIn('占用',r.json()['detail'])
        finally:
            guard.release()

if __name__=='__main__':unittest.main()
