import json
import unittest
from unittest.mock import patch
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

    def test_content_can_be_created_without_diagnosis(self):
        import publish_adapters as adapters
        slug=self.create('无诊断项目'); base='/api/projects/'+slug
        r=self.client.post(base+'/content',json={'title':'设备资产管理系统选型指南','brief':'常见问题\n选型指标','channel':'公众号长文'})
        self.assertEqual(r.status_code,201); aid=r.json()['id']
        items=self.client.get(base+'/editorial/content').json()['items']
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['source'],'manual')
        self.assertIsNone(items[0]['action_id'])
        self.assertIn('## 常见问题',items[0]['body'])
        self.assertIn('（待补充',items[0]['body'])
        # 占位符未补齐不能过审
        r=self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':1,'confirm':True})
        self.assertEqual(r.status_code,409); self.assertIn('待补充',r.json()['detail'])
        # 补齐正文与事实后可过审、可发布
        self.assertEqual(self.client.put(base+'/content/'+aid,json={'title':'设备资产管理系统选型指南','body':'正文（已核对）','facts':'官网 https://www.example.com','revision':1}).status_code,200)
        self.assertEqual(self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':2,'confirm':True}).status_code,200)
        with patch.object(adapters.PublishAdapter,'submit_browser',
                          lambda self,a,evidence_dir=None: adapters.PublishResult('submitted','已提交（测试）')):
            out=self.client.post(base+'/publish',json={'asset_ids':[aid],'platforms':['zhihu'],'confirm':True})
        self.assertEqual(out.status_code,200)
        self.assertEqual(out.json()['results'][0]['status'],'submitted')

    def test_migration_keeps_existing_assets(self):
        slug,base,aid=self.seed()
        store.migrate()
        items=self.client.get(base+'/editorial/content').json()['items']
        self.assertEqual([x['id'] for x in items],[aid])
        self.assertEqual(items[0]['source'],'diagnosis')

    def _approved(self, base, aid):
        self.client.put(base+'/content/'+aid,json={'title':'测试内容','body':'正文（已核对）','facts':'来源 https://example.com','revision':1})
        self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':2,'confirm':True})
        return aid

    def test_platform_adapter_registry_covers_all_platforms(self):
        import publish_adapters as adapters
        caps=adapters.capabilities()
        self.assertEqual(len(caps),10)
        modes={c['id']:c['mode'] for c in caps}
        self.assertEqual(modes['wechat_mp'],'api')
        self.assertEqual(modes['baike'],'manual')
        self.assertEqual(modes['official_site'],'manual')
        for pid in ['zhihu','baijia','toutiao','csdn','xiaohongshu','sohu','dayu']:
            self.assertEqual(modes[pid],'browser')

    def test_publish_requires_confirmation(self):
        slug,base,aid=self.seed(); self._approved(base,aid)
        r=self.client.post(base+'/publish',json={'asset_ids':[aid],'platforms':['zhihu'],'confirm':False})
        self.assertEqual(r.status_code,409)
        self.assertIn('请确认发布',r.json()['detail'])

    def test_publish_blocks_unapproved_and_missing_elements(self):
        import publish_adapters as adapters
        slug,base,aid=self.seed()
        self.assertEqual(self.client.post(base+'/publish',json={'asset_ids':[aid],'platforms':['zhihu'],'confirm':True}).status_code,409)
        self._approved(base,aid)
        with patch.object(adapters.PublishAdapter,'submit_browser',
                          side_effect=AssertionError('缺要素时不应调用浏览器')):
            r=self.client.post(base+'/publish',json={'asset_ids':[aid],'platforms':['xiaohongshu'],'confirm':True})
        self.assertEqual(r.status_code,200)
        out=r.json()['results'][0]
        self.assertEqual(out['status'],'manual_required')
        self.assertIn('封面图',out['message'])

    def test_publish_runs_adapters_and_records_audit(self):
        import publish_adapters as adapters
        slug,base,aid=self.seed(); self._approved(base,aid)
        calls=[]
        def fake_browser(self,asset,evidence_dir=None):
            calls.append(self.spec.id)
            return adapters.PublishResult('submitted','浏览器自动化已提交（测试）',evidence='fake.png')
        with patch.object(adapters.PublishAdapter,'submit_browser',fake_browser):
            r=self.client.post(base+'/publish',json={'asset_ids':[aid],'platforms':['wechat_mp','zhihu','baike'],'confirm':True})
        self.assertEqual(r.status_code,200)
        got={x['platform']:x for x in r.json()['results']}
        self.assertEqual(got['zhihu']['status'],'submitted')
        self.assertEqual(calls,['zhihu'])
        # 公众号未配置凭据 → 明确转人工，不伪造成已写入草稿箱
        self.assertEqual(got['wechat_mp']['status'],'manual_required')
        self.assertIn('未配置',got['wechat_mp']['message'])
        self.assertEqual(got['baike']['status'],'manual_required')
        jobs={j['platform']:j for j in self.client.get(base+'/editorial/publications').json()['items']}
        self.assertEqual(jobs['zhihu']['status'],'submitted')
        self.assertEqual(jobs['zhihu']['mode'],'browser')
        self.assertEqual(jobs['wechat_mp']['status'],'manual_required')

    def test_platform_credentials_stored_masked(self):
        r=self.client.put('/api/platforms/wechat_mp/credentials',json={'appid':'wx1234567890','secret':'super-secret-value'})
        self.assertEqual(r.status_code,200)
        self.assertNotIn('super-secret-value',json.dumps(r.json(),ensure_ascii=False))
        caps={p['id']:p for p in self.client.get('/api/platforms').json()['publication']}
        creds={c['name']:c for c in caps['wechat_mp']['credentials']}
        self.assertTrue(creds['appid']['configured'] and creds['secret']['configured'])
        self.assertTrue(caps['wechat_mp']['can_attempt'])
        self.assertNotIn('super-secret-value',json.dumps(caps,ensure_ascii=False))
        # 空值表示保持原值不变
        self.client.put('/api/platforms/wechat_mp/credentials',json={'appid':'','secret':''})
        caps2={p['id']:p for p in self.client.get('/api/platforms').json()['publication']}
        creds={c['name']:c for c in caps2['wechat_mp']['credentials']}
        self.assertTrue(all(c['configured'] for c in creds.values()))

    def test_wechat_draft_uses_official_api(self):
        import wechat_mp
        from unittest.mock import MagicMock
        client=MagicMock()
        client.get.return_value=MagicMock(json=lambda:{'access_token':'tok','expires_in':7200})
        client.post.return_value=MagicMock(json=lambda:{'media_id':'MEDIA123'})
        api=wechat_mp.WechatMpClient('wxappid','secret',client=client)
        out=api.add_draft(title='标题',content='## 小节\n正文')
        self.assertEqual(out['media_id'],'MEDIA123')
        args,kwargs=client.post.call_args
        self.assertIn('/cgi-bin/draft/add',args[0])
        self.assertIn('access_token',kwargs['params'])
        payload=json.loads(kwargs['content'].decode('utf8'))
        self.assertEqual(payload['articles'][0]['title'],'标题')
        self.assertIn('<h2>小节</h2>',payload['articles'][0]['content'])

    def test_wechat_errors_are_actionable_and_secret_free(self):
        import wechat_mp
        from unittest.mock import MagicMock
        client=MagicMock()
        client.get.return_value=MagicMock(json=lambda:{'access_token':'tok','expires_in':7200})
        client.post.return_value=MagicMock(json=lambda:{'errcode':40164,'errmsg':'invalid ip'})
        api=wechat_mp.WechatMpClient('wxappid','topsecret',client=client)
        with self.assertRaises(wechat_mp.WechatError) as ctx:
            api.add_draft(title='标题',content='正文')
        message=str(ctx.exception)
        self.assertIn('IP 白名单',message)
        self.assertNotIn('topsecret',message)

    def test_manual_receipt_still_available(self):
        slug,base,aid=self.seed(); self._approved(base,aid)
        r=self.client.post(base+'/publish',json={'asset_ids':[aid],'platforms':['baike'],'confirm':True})
        self.assertEqual(r.status_code,200)
        job=self.client.get(base+'/editorial/publications').json()['items'][0]
        self.assertEqual(job['status'],'manual_required')
        self.assertEqual(self.client.post(base+'/publishing/'+job['id']+'/receipt',json={'url':'javascript:bad','operator':'测试','confirm':True}).status_code,409)
        self.assertEqual(self.client.post(base+'/publishing/'+job['id']+'/receipt',json={'url':'https://example.com/x','operator':'测试','confirm':True}).status_code,200)
        self.assertEqual(self.client.get(base+'/editorial/publications').json()['items'][0]['status'],'published_manual')

    def test_review_gate_blocks_empty_and_placeholder(self):
        slug,base,aid=self.seed()
        self.assertEqual(self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':1,'confirm':True}).status_code,409)
        self.client.put(base+'/content/'+aid,json={'title':'t','body':'正文（待补充：事实）','facts':'来源','revision':1})
        r=self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':2,'confirm':True})
        self.assertEqual(r.status_code,409)
        self.assertIn('待补充',r.json()['detail'])


if __name__=='__main__':unittest.main()
