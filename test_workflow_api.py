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

if __name__=='__main__':unittest.main()
