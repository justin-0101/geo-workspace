import json
import unittest
from unittest.mock import patch
from test_workflow_api import APITests
from diagnosis_config import suggest_questions
from diagnosis_runs import verify_frozen_run
import workspace_store as store


FACTS = '来源：测试夹具 https://example.com/fixture（不代表真实企业事实）'
_SENTENCE = '质量门禁测试正文：本节用于验证审核与发布前的自动检查，不包含任何真实企业事实。'
LONG_BODY = ('# 测试内容\n\n## 判断标准\n\n' + _SENTENCE * 8 +
             '\n\n## 核验清单\n\n' + _SENTENCE * 6)


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

    def _asset(self, base, aid):
        return next(x for x in self.client.get(base+'/editorial/content').json()['items'] if x['id']==aid)

    def test_content_can_be_created_and_generated_without_diagnosis(self):
        import publish_adapters as adapters
        slug=self.create('无诊断项目'); base='/api/projects/'+slug
        r=self.client.post(base+'/content',json={'title':'设备资产管理系统选型指南','brief':'常见问题\n选型指标','channel':'公众号长文'})
        self.assertEqual(r.status_code,201); aid=r.json()['id']
        asset=self._asset(base,aid)
        self.assertEqual(asset['source'],'manual'); self.assertIsNone(asset['action_id'])
        self.assertIn('## 常见问题',asset['body']); self.assertIn('（待补充',asset['body'])
        # 证据包与生成能力可读，未填事实时质量检查不通过
        ctx=self.client.get(base+'/content/'+aid+'/context').json()
        self.assertIn('source_bundle',ctx); self.assertIn('generator',ctx)
        self.assertEqual(ctx['source_bundle']['notice'],'诊断平台回答只用于理解内容缺口，不作为企业事实。企业事实需由人工核验来源。')
        self.assertEqual(ctx['source_bundle']['question_id'],'')
        self.assertEqual(ctx['source_bundle']['observations'],[])
        self.assertFalse(ctx['quality']['passed'])
        # 占位符未补齐不能过审
        r=self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':1,'confirm':True})
        self.assertEqual(r.status_code,409); self.assertIn('待补充',r.json()['detail'])
        # 生成：未配置内容模型时用本地引擎（产出素材整理稿，不是成稿）
        out=self.client.post(base+'/content/'+aid+'/generate',json={
            'revision':1,'facts':FACTS,'channel':'小红书','audience':'制造企业负责人','objective':'帮助读者建立可核验的选型框架'})
        self.assertEqual(out.status_code,200)
        self.assertEqual(out.json()['engine'],'local-safe'); self.assertEqual(out.json()['revision'],2)
        # 生成说明必须与正文分开，并明说本地引擎只做整理、不写成稿
        self.assertTrue(any('只做素材整理' in n for n in out.json()['notes']), out.json()['notes'])
        asset=self._asset(base,aid)
        self.assertNotIn('（待补充',asset['body'])
        self.assertNotEqual(asset['generation_meta_json'],'{}')
        self.assertEqual(asset['facts'],FACTS)
        self.assertTrue(self.client.get(base+'/content/'+aid+'/context').json()['quality']['passed'])
        self.assertEqual(self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':2,'confirm':True}).status_code,200)
        with patch.object(adapters.PublishAdapter,'submit_browser',
                          lambda self,a,evidence_dir=None: adapters.PublishResult('submitted','已提交（测试）')):
            out=self.client.post(base+'/publish',json={'asset_ids':[aid],'platforms':['zhihu'],'confirm':True})
        self.assertEqual(out.status_code,200)
        self.assertEqual(out.json()['results'][0]['status'],'submitted')

    def test_generate_requires_overwrite_confirmation(self):
        slug,base,aid=self.seed()
        first=self.client.post(base+'/content/'+aid+'/generate',json={'revision':1,'facts':FACTS,'channel':'公众号长文'})
        self.assertEqual(first.status_code,200)
        # 第二次生成必须显式确认覆盖，避免静默丢掉人工修改
        blocked=self.client.post(base+'/content/'+aid+'/generate',json={'revision':2,'facts':FACTS,'channel':'公众号长文'})
        self.assertEqual(blocked.status_code,409); self.assertIn('覆盖',blocked.json()['detail'])
        allowed=self.client.post(base+'/content/'+aid+'/generate',json={'revision':2,'facts':FACTS,'channel':'公众号长文','confirm_overwrite':True})
        self.assertEqual(allowed.status_code,200); self.assertEqual(allowed.json()['revision'],3)

    def test_generate_rejects_stale_revision(self):
        slug,base,aid=self.seed()
        r=self.client.post(base+'/content/'+aid+'/generate',json={'revision':99,'facts':FACTS})
        self.assertEqual(r.status_code,409); self.assertIn('刷新',r.json()['detail'])

    def test_derive_groups_by_question(self):
        slug,base,aid=self.seed()
        rid=self.client.get(base).json()['runs'][0]['id']
        path=verify_frozen_run(slug,rid)
        with open(path/'observations.jsonl','a',encoding='utf8') as fh:
            fh.write(json.dumps(dict(task_id='Q01_metaso_01',question_id='Q01',status='success',
                                     response_text='TEST FIXTURE 2',input_prompt='测试问题',
                                     evidence_files=['evidence/test.txt'],classification={'brand_mention':'no'}))+chr(10))
        with store.connection() as c:
            c.execute('DELETE FROM editorial_assets WHERE project_slug=?',(slug,))
            c.execute('DELETE FROM improvement_items WHERE project_slug=?',(slug,))
        self.assertEqual(self.client.post(base+'/runs/'+rid+'/improvements/derive').status_code,200)
        items=self.client.get(base+'/editorial/actions').json()['items']
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['task_id'],'Q01')
        self.assertIn('2 个有效观测',items[0]['description'])
        self.assertIn('涉及平台',items[0]['description'])

    def test_derive_prefers_report_suggested_title(self):
        slug,base,aid=self.seed()
        rid=self.client.get(base).json()['runs'][0]['id']
        path=verify_frozen_run(slug,rid)
        (path/'report').mkdir(exist_ok=True)
        (path/'report'/'optimization-plan.md').write_text(
            '| 对应问题 | 建议内容标题 | 目标 |\n| --- | --- | --- |\n'
            '| Q01 | 怎么选：判断标准、适用场景与避坑清单 | 争取推荐 |\n',encoding='utf8')
        with store.connection() as c:
            c.execute('DELETE FROM editorial_assets WHERE project_slug=?',(slug,))
            c.execute('DELETE FROM improvement_items WHERE project_slug=?',(slug,))
        self.client.post(base+'/runs/'+rid+'/improvements/derive')
        action=self.client.get(base+'/editorial/actions').json()['items'][0]
        self.assertEqual(action['title'],'怎么选：判断标准、适用场景与避坑清单')
        new=self.client.post(base+'/actions/'+action['id']+'/content').json()['id']
        asset=self._asset(base,new)
        self.assertEqual(asset['title'],'怎么选：判断标准、适用场景与避坑清单')
        bundle=json.loads(asset['source_bundle_json'])
        self.assertEqual(bundle['question_id'],'Q01')
        self.assertEqual(bundle['suggested_title'],'怎么选：判断标准、适用场景与避坑清单')
        self.assertEqual(len(bundle['observations']),1)
        self.assertEqual(bundle['observations'][0]['fact_status'],'unverified-model-response')

    def test_diagnosis_asset_bundle_carries_question_and_evidence(self):
        slug,base,aid=self.seed()
        ctx=self.client.get(base+'/content/'+aid+'/context').json()
        bundle=ctx['source_bundle']
        self.assertEqual(bundle['question_id'],'Q01')
        self.assertEqual(bundle['question'],'测试问题')
        self.assertEqual(bundle['evidence_files'],['evidence/test.txt'])
        self.assertIn('不作为企业事实',bundle['notice'])
        # 诊断回答只能作为缺口线索，不能自动变成已核验事实
        self.assertEqual(bundle['verified_facts'],'')

    def test_quality_gate_blocks_instruction_title_short_body_and_fake_source(self):
        slug,base,aid=self.seed()
        self.client.put(base+'/content/'+aid,json={'title':'待填写：补充问题相关的事实与内容：测试问题','body':LONG_BODY,'facts':FACTS,'revision':1})
        r=self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':2,'confirm':True})
        self.assertEqual(r.status_code,409); self.assertIn('标题仍是占位符',r.json()['detail'])
        self.client.put(base+'/content/'+aid,json={'title':'正常标题','body':'刚刚发生的','facts':FACTS,'revision':2})
        r=self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':3,'confirm':True})
        self.assertEqual(r.status_code,409); self.assertIn('低于当前渠道最低要求',r.json()['detail'])
        self.client.put(base+'/content/'+aid,json={'title':'正常标题','body':LONG_BODY,'facts':'刚刚发生的','revision':3})
        r=self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':4,'confirm':True})
        self.assertEqual(r.status_code,409); self.assertIn('标明出处',r.json()['detail'])
        # 四项都补齐后才能过审
        self.client.put(base+'/content/'+aid,json={'title':'正常标题','body':LONG_BODY,'facts':FACTS,'revision':4})
        self.assertEqual(self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':5,'confirm':True}).status_code,200)

    def test_short_body_passes_after_channel_is_set(self):
        slug,base,aid=self.seed()
        brief_body='# 小红书笔记\n\n## 判断标准\n\n'+_SENTENCE*2
        self.client.put(base+'/content/'+aid,json={'title':'正常标题','body':brief_body,'facts':FACTS,'channel':'小红书','revision':1})
        r=self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':2,'confirm':True})
        self.assertEqual(r.status_code,409)
        longer='# 小红书笔记\n\n## 判断标准\n\n'+_SENTENCE*6
        self.client.put(base+'/content/'+aid,json={'title':'正常标题','body':longer,'facts':FACTS,'channel':'小红书','revision':2})
        self.assertEqual(self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':3,'confirm':True}).status_code,200)

    def test_publish_rechecks_quality_for_legacy_approved_assets(self):
        """旧数据曾用宽松门禁过审，发布前必须重新检查（实时库曾出现 4 字正文被视为可发布）。"""
        slug,base,aid=self.seed()
        with store.connection() as c:
            c.execute("UPDATE editorial_assets SET title=?,body=?,facts=?,status='approved',"
                      "reviewed_revision=revision,quality_report_json='{}' WHERE id=?",
                      ('待填写：补充问题相关的事实与内容：测试问题','刚刚发生的','刚刚发生的',aid))
        r=self.client.post(base+'/publish',json={'asset_ids':[aid],'platforms':['zhihu'],'confirm':True})
        self.assertEqual(r.status_code,409); self.assertIn('质量检查',r.json()['detail'])
        self.assertEqual(self.client.get(base+'/editorial/publications').json()['items'],[])

    def test_migration_is_idempotent_and_preserves_content_fields(self):
        slug,base,aid=self.seed()
        self.client.put(base+'/content/'+aid,json={'title':'正常标题','summary':'摘要','body':LONG_BODY,'facts':FACTS,
                                                  'channel':'公众号长文','audience':'制造企业负责人',
                                                  'keywords':'设备资产管理系统','target_length':1200,'revision':1})
        store.migrate(); store.migrate()
        asset=self._asset(base,aid)
        self.assertEqual(asset['channel'],'公众号长文')
        self.assertEqual(asset['summary'],'摘要')
        self.assertEqual(asset['audience'],'制造企业负责人')
        self.assertEqual(asset['target_length'],1200)
        self.assertEqual(asset['revision'],2)
        self.assertEqual(asset['status'],'draft')

    def test_review_marks_linked_action_done(self):
        slug,base,aid=self.seed()
        action_id=self._asset(base,aid)['action_id']
        self.client.put(base+'/content/'+aid,json={'title':'正常标题','body':LONG_BODY,'facts':FACTS,'revision':1})
        self.client.post(base+'/content/'+aid+'/review',json={'reviewer':'复核人','revision':2,'confirm':True})
        items={x['id']:x for x in self.client.get(base+'/editorial/actions').json()['items']}
        self.assertEqual(items[action_id]['status'],'done')

    def test_migration_keeps_existing_assets(self):
        slug,base,aid=self.seed()
        store.migrate()
        items=self.client.get(base+'/editorial/content').json()['items']
        self.assertEqual([x['id'] for x in items],[aid])
        self.assertEqual(items[0]['source'],'diagnosis')

    def _approved(self, base, aid):
        asset=self._asset(base,aid)
        saved=self.client.put(base+'/content/'+aid,json={'title':'测试内容','summary':'摘要',
            'body':LONG_BODY,'facts':FACTS,'channel':'公众号长文','audience':'制造企业负责人',
            'objective':'帮助选型','keywords':'设备资产管理系统','target_length':1200,
            'revision':asset['revision']})
        self.assertEqual(saved.status_code,200, saved.text)
        review=self.client.post(base+'/content/'+aid+'/review',
            json={'reviewer':'复核人','revision':saved.json()['revision'],'confirm':True})
        self.assertEqual(review.status_code,200, review.text)
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
        out=api.add_draft(title='标题',content='## 小节\n正文',thumb_media_id='THUMB1')
        self.assertEqual(out['media_id'],'MEDIA123')
        args,kwargs=client.post.call_args
        self.assertIn('/cgi-bin/draft/add',args[0])
        self.assertIn('access_token',kwargs['params'])
        payload=json.loads(kwargs['content'].decode('utf8'))
        self.assertEqual(payload['articles'][0]['title'],'标题')
        self.assertIn('<h2>小节</h2>',payload['articles'][0]['content'])
        self.assertEqual(payload['articles'][0]['thumb_media_id'],'THUMB1')
        self.assertEqual(payload['articles'][0]['article_type'],'news')

    def test_wechat_draft_requires_cover(self):
        """图文消息没封面时必须在本地拦下，不能换个看不懂的 40007 回来。"""
        import wechat_mp
        from unittest.mock import MagicMock
        client=MagicMock()
        client.get.return_value=MagicMock(json=lambda:{'access_token':'tok','expires_in':7200})
        api=wechat_mp.WechatMpClient('wxappid','secret',client=client)
        with self.assertRaises(wechat_mp.WechatError) as ctx:
            api.add_draft(title='标题',content='正文')
        self.assertIn('封面',str(ctx.exception))
        client.post.assert_not_called()

    def test_wechat_thumb_upload_uses_permanent_material(self):
        """封面必须进永久素材库：临时素材 3 天过期，拿它当封面就是 40007。"""
        import wechat_mp
        from unittest.mock import MagicMock
        client=MagicMock()
        client.get.return_value=MagicMock(json=lambda:{'access_token':'tok','expires_in':7200})
        client.post.return_value=MagicMock(json=lambda:{'media_id':'THUMB9'})
        api=wechat_mp.WechatMpClient('wxappid','secret',client=client)
        self.assertEqual(api.upload_thumb('cover.jpg',b'jpegbytes'),'THUMB9')
        args,kwargs=client.post.call_args
        self.assertIn('/cgi-bin/material/add_material',args[0])
        self.assertNotIn('/cgi-bin/media/upload',args[0])
        self.assertEqual(kwargs['params']['type'],wechat_mp.THUMB_MATERIAL_TYPE)
        self.assertIn('media',kwargs['files'])

    def test_wechat_errors_keep_wechat_wording(self):
        """本地提示是猜的，微信原文才是证据——两者都要留在报错里。"""
        import wechat_mp
        from unittest.mock import MagicMock
        client=MagicMock()
        client.get.return_value=MagicMock(json=lambda:{'access_token':'tok','expires_in':7200})
        client.post.return_value=MagicMock(json=lambda:{'errcode':40007,'errmsg':'invalid media_id'})
        api=wechat_mp.WechatMpClient('wxappid','secret',client=client)
        with self.assertRaises(wechat_mp.WechatError) as ctx:
            api.add_draft(title='标题',content='正文',thumb_media_id='X')
        message=str(ctx.exception)
        self.assertIn('errcode 40007',message)
        self.assertIn('invalid media_id',message)
        self.assertNotIn('serrcode',message)

    def test_wechat_errors_are_actionable_and_secret_free(self):
        import wechat_mp
        from unittest.mock import MagicMock
        client=MagicMock()
        client.get.return_value=MagicMock(json=lambda:{'access_token':'tok','expires_in':7200})
        client.post.return_value=MagicMock(json=lambda:{'errcode':40164,'errmsg':'invalid ip'})
        api=wechat_mp.WechatMpClient('wxappid','topsecret',client=client)
        with self.assertRaises(wechat_mp.WechatError) as ctx:
            # 必须带封面才能走到微信这一层：没封面会被本地提前拦下
            api.add_draft(title='标题',content='正文',thumb_media_id='THUMB1')
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
