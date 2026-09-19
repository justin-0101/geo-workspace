import json
import unittest
from test_workflow_api import APITests
from diagnosis_config import suggest_questions
from diagnosis_runs import verify_frozen_run
import workspace_store as store


class EditorialTests(APITests):
    def seed(self):
        slug=self.create(); base='/api/projects/'+slug
        p=dict(canonical_name='测试项目',business='设备维护',region='广州',audience='工厂',aliases=[],official_pages=[],no_official_web_presence=True)
        self.client.put(base+'/profile',json=dict(profile=p,questions=suggest_questions(p),platforms=['deepseek'],revision=0))
        rid=self.client.post(base+'/runs',json={'confirm':True,'revision':1}).json()['id']
        path=verify_frozen_run(slug,rid)
        # Explicit fixture, never presented as actual platform evidence.
        (path/'observations.jsonl').write_text(json.dumps(dict(task_id='Q01_deepseek_01',question_id='Q01',question_type='non_brand',status='success',response_text='TEST FIXTURE',input_prompt='测试问题',evidence_files=['evidence/test.txt'],classification={'brand_mention':'no'}))+'\n',encoding='utf8')
        (path/'evidence/test.txt').write_text('TEST FIXTURE',encoding='utf8')
        with store.connection() as c:c.execute("UPDATE execution_runs SET status='completed' WHERE id=?",(rid,))
        r=self.client.post(base+'/runs/'+rid+'/improvements/derive');self.assertEqual(r.status_code,200)
        action=self.client.get(base+'/editorial/actions').json()['items'][0]['id']
        aid=self.client.post(base+'/actions/'+action+'/content').json()['id']
        return slug,base,aid

    def test_degraded_run_can_derive_but_interrupted_cannot(self):
        slug,base,aid=self.seed()
        rid=self.client.get(base).json()['runs'][0]['id']
        with store.connection() as c:
            c.execute("UPDATE execution_runs SET status='degraded' WHERE id=?",(rid,))
        self.assertEqual(self.client.post(base+'/runs/'+rid+'/improvements/derive').status_code,200)
        with store.connection() as c:
            c.execute("UPDATE execution_runs SET status='interrupted' WHERE id=?",(rid,))
        self.assertEqual(self.client.post(base+'/runs/'+rid+'/improvements/derive').status_code,409)

    def test_overview_surfaces_real_metrics(self):
        slug,base,aid=self.seed()
        rid=self.client.get(base).json()['runs'][0]['id']
        path=verify_frozen_run(slug,rid)
        (path/'report').mkdir(exist_ok=True)
        (path/'report/metrics.json').write_text(json.dumps({
            'generated_at':'2026-01-01T00:00:00Z','planned_tasks':8,'terminal_tasks':8,
            'success':7,'failed':1,'non_brand_success':4,'non_brand_brand_mentions':0,
            'non_brand_explicit_recommendations':0,
            'official_source_citation_counts':{'yes':2,'no':3,'not_shown':2,'unknown':1},
            'accuracy_counts':{'unverified':7},
            'platforms':[{'id':'deepseek','label':'DeepSeek','planned':8,'success':7,'failed':1,
                          'non_brand_success':4,'non_brand_mention':0,'non_brand_explicit':0,
                          'official_citation_yes':2}],
            'citation_records':8,'citation_domain_counts':{'sample.example.org':5,'example.com':3},
            'limits':['一次性测试时点快照，不代表趋势或因果。']},ensure_ascii=False),encoding='utf8')
        d=self.client.get(base+'/overview').json()
        self.assertTrue(d['generated'])
        self.assertEqual(d['latest']['planned'],8)
        self.assertEqual(d['latest']['official_yes'],2)
        self.assertEqual(d['completeness'],{'complete':8,'planned':8,'percent':100})
        self.assertEqual(d['domains'][0],['sample.example.org',5])
        self.assertEqual(len(d['history']),1)
        self.assertEqual([q['id'] for q in d['questions']],['Q01'])
        self.assertEqual(d['platforms'][0]['label'],'DeepSeek')
        # A project without a finished batch must not pretend to have data.
        empty=self.create('空项目')
        e=self.client.get('/api/projects/'+empty+'/overview').json()
        self.assertFalse(e['generated'])
        self.assertIsNone(e['latest'])

    def test_review_and_publication_gate(self):
        slug,base,aid=self.seed()
        publish=dict(asset_ids=[aid],platforms=['zhihu'],operator='测试操作人',confirm=True)
        self.assertEqual(self.client.post(base+'/publishing',json=publish).status_code,409)
        save=dict(title='测试标题',body='测试正文',facts='测试事实依据',revision=1)
        self.assertEqual(self.client.put(base+'/content/'+aid,json=save).status_code,200)
        review=dict(reviewer='测试审核人',revision=2,confirm=True)
        self.assertEqual(self.client.post(base+'/content/'+aid+'/review',json=review).status_code,200)
        r=self.client.post(base+'/publishing',json=publish);self.assertEqual(r.status_code,200)
        identity=r.json()['created'][0]
        self.assertEqual(self.client.post(base+'/publishing',json=publish).json()['created'],[])
        self.assertEqual(self.client.get(base+'/editorial/publications').json()['items'][0]['status'],'manual_required')
        save.update(body='已修改正文',revision=2)
        self.client.put(base+'/content/'+aid,json=save)
        self.assertEqual(self.client.post(base+'/publishing',json=publish).status_code,409)
        jobs=self.client.get(base+'/editorial/publications').json()['items']
        self.assertEqual(jobs[0]['body_snapshot'],'测试正文')
        other=self.create('另一项目')
        self.assertEqual(self.client.post('/api/projects/'+other+'/publishing/'+identity+'/receipt',json=dict(url='https://example.com/test',operator='测试',confirm=True)).status_code,404)
        self.assertEqual(self.client.post(base+'/publishing/'+identity+'/receipt',json=dict(url='javascript:bad',operator='测试',confirm=True)).status_code,409)
        r=self.client.post(base+'/publishing/'+identity+'/receipt',json=dict(url='https://example.com/test',operator='测试',confirm=True))
        self.assertEqual(r.status_code,200)

if __name__=='__main__':unittest.main()
