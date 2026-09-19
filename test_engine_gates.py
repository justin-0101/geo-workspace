import json
from unittest.mock import patch
from test_workspace_core import WorkspaceTests
from diagnosis_runs import create_frozen_run,verify_frozen_run
from diagnosis_config import suggest_questions
import browser_engine as engine
import workspace_store as store
from process_guard import ProcessGuard


class EngineTests(WorkspaceTests):
    def frozen(self):
        store.save_profile('test-a',self.profile,suggest_questions(self.profile),['deepseek'],0)
        return create_frozen_run('test-a',1,True)

    def test_process_lock(self):
        guard=ProcessGuard(store.DATA/'service.lock').acquire()
        try:
            with self.assertRaises(ValueError):ProcessGuard(store.DATA/'service.lock').acquire()
        finally:guard.release()
        again=ProcessGuard(store.DATA/'service.lock').acquire();again.release()

    def test_launch_checks_no_network(self):
        rid=self.frozen()
        with self.assertRaises(ValueError):engine.launch('test-a',rid,'execute',True)
        with patch.object(engine,'dependencies',return_value=['test missing dependency']):
            with self.assertRaises(ValueError):engine.launch('test-a',rid,'preflight')
        self.assertIsNone(engine._ACTIVE)
        with self.assertRaises(ValueError):engine.archive('test-a',rid,False)
        engine.archive('test-a',rid,True)
        self.assertEqual(engine.get_run('test-a',rid)['status'],'archived')
        # Archive retains evidence and permits a new batch without resubmitting old tasks.
        self.assertTrue(verify_frozen_run('test-a',rid).exists())
        self.assertNotEqual(create_frozen_run('test-a',1,True),rid)

    def test_ambiguous_task_cannot_resubmit(self):
        rid=self.frozen();path=verify_frozen_run('test-a',rid)
        tasks=engine.rows(path/'tasks.jsonl');tasks[0]['status']='running'
        (path/'tasks.jsonl').write_text(''.join(json.dumps(t)+'\n' for t in tasks),encoding='utf8')
        engine.transition('test-a',rid,'ready')
        with self.assertRaises(ValueError):engine.launch('test-a',rid,'execute',True)

    def test_resume_needs_live_job_and_writes_flag(self):
        rid=self.frozen()
        with self.assertRaises(ValueError):engine.resume('test-a',rid)
        job=dict(slug='test-a',run_id=rid,path=verify_frozen_run('test-a',rid),mode='execute',stopped=False,process=None)
        engine._ACTIVE=job
        try:
            engine.resume('test-a',rid)
            self.assertTrue((job['path']/'resume.flag').exists())
            job['mode']='preflight'
            with self.assertRaises(ValueError):engine.resume('test-a',rid)
        finally:
            engine._ACTIVE=None

    def test_run_status_exposes_pause_guidance(self):
        from fastapi.testclient import TestClient
        from workflow_api import app
        rid=self.frozen();path=verify_frozen_run('test-a',rid)
        tasks=engine.rows(path/'tasks.jsonl');tasks[0]['status']='running'
        (path/'tasks.jsonl').write_text(''.join(json.dumps(t)+'\n' for t in tasks),encoding='utf8')
        (path/'PAUSED.md').write_text('请在浏览器窗口完成登录或验证码\n',encoding='utf8')
        with TestClient(app) as client:
            data=client.get('/api/projects/test-a/runs/'+rid).json()
        self.assertTrue(data['paused'])
        self.assertEqual(data['waiting_tasks'],['Q01_deepseek_01'])
        self.assertIn('登录',data['pause_note'])
        self.assertNotIn('config_json',data)
        self.assertNotIn('run_path',data)
        self.assertTrue(all('prompt' in t for t in data['tasks']))

    def test_stale_browser_is_cleaned_then_launch_proceeds(self):
        rid=self.frozen();engine.transition('test-a',rid,'frozen')
        sequence=iter([[11],[11],[11],[],[]])
        with patch.object(engine,'profile_processes',side_effect=lambda *a,**k: next(sequence,[])), \
             patch.object(engine,'close_profile_processes',return_value=1), \
             patch.object(engine,'dependencies',return_value=[]), \
             patch.object(engine,'_worker',lambda job: None), \
             patch.object(engine,'time'):
            result=engine.launch('test-a',rid,'preflight')
        self.assertIn('已自动清理',result['message'])
        engine._ACTIVE=None

    def test_persistent_browser_lock_reports_manual_step(self):
        rid=self.frozen();engine.transition('test-a',rid,'frozen')
        with patch.object(engine,'profile_processes',return_value=[7]), \
             patch.object(engine,'close_profile_processes',return_value=0), \
             patch.object(engine,'time'):
            with self.assertRaises(ValueError) as ctx:
                engine.launch('test-a',rid,'preflight')
        self.assertIn('手动结束',str(ctx.exception))
        self.assertIsNone(engine._ACTIVE)

    def test_failure_reason_translation(self):
        rid=self.frozen();path=verify_frozen_run('test-a',rid)
        (path/'logs').mkdir(exist_ok=True)
        log=path/'logs/engine.log'
        log.write_text('Failed to create a ProcessSingleton for your profile directory.',encoding='utf8')
        self.assertIn('关闭浏览器窗口',engine.failure_reason(path,'fallback'))
        log.write_text('net::ERR_CONNECTION_REFUSED',encoding='utf8')
        self.assertIn('网络',engine.failure_reason(path,'fallback'))
        log.write_text('everything fine',encoding='utf8')
        self.assertEqual(engine.failure_reason(path,'fallback'),'fallback')

    def test_incomplete_tasks_are_listed(self):
        rid=self.frozen();path=verify_frozen_run('test-a',rid)
        tasks=[json.loads(l) for l in (path/'tasks.jsonl').read_text(encoding='utf-8-sig').splitlines() if l.strip()]
        (path/'evidence').mkdir(exist_ok=True)
        (path/'evidence/e.png').write_bytes(b'x')
        obs=[dict(task_id=tasks[0]['task_id'],status='success',evidence_files=['evidence/e.png']),
             dict(task_id=tasks[1]['task_id'],status='success',evidence_files=[])]
        (path/'observations.jsonl').write_text(''.join(json.dumps(o)+'\n' for o in obs),encoding='utf8')
        rewritten=[json.dumps({**t,'status':'success' if i<2 else 'pending'}) for i,t in enumerate(tasks)]
        (path/'tasks.jsonl').write_text(chr(10).join(rewritten),encoding='utf8')
        problems=engine.incomplete_tasks(path)
        self.assertEqual(len(problems),1)
        self.assertIn(tasks[1]['task_id'],problems[0])

    def test_preflight_reason_names_the_real_cause(self):
        rid=self.frozen();path=verify_frozen_run('test-a',rid)
        pf=path/'preflight.json'
        pf.write_text(json.dumps([{'platform':'deepseek','check':'OK','login_state_guess':'logged_out'}]),encoding='utf8')
        self.assertIn('待登录',engine.preflight_reason(path))
        pf.write_text(json.dumps([{'platform':'deepseek','check':'ERROR','error':'Page.goto: Timeout 60000ms exceeded.'}]),encoding='utf8')
        timeout_reason=engine.preflight_reason(path)
        self.assertIn('超时',timeout_reason)
        self.assertNotIn('待登录',timeout_reason)
        pf.write_text(json.dumps([{'platform':'doubao','check':'OK','login_state_guess':'logged_out'},
                                  {'platform':'metaso','check':'ERROR','error':'Page.screenshot: Timeout 30000ms exceeded.'}]),encoding='utf8')
        mixed=engine.preflight_reason(path)
        self.assertIn('超时',mixed)
        self.assertIn('待登录',mixed)
        pf.unlink()
        self.assertIn('重试',engine.preflight_reason(path))

    def test_preflight_logged_out_is_blocked(self):
        rid=self.frozen();path=verify_frozen_run('test-a',rid)
        job=dict(slug='test-a',run_id=rid,path=path,mode='preflight',stopped=False)
        def fake_run(*args):
            (path/'preflight.json').write_text(json.dumps([{'platform':'deepseek','check':'OK','login_state_guess':'logged_out'}]),encoding='utf8')
            return 0
        with patch.object(engine,'_run',side_effect=fake_run):engine._worker(job)
        run=engine.get_run('test-a',rid)
        self.assertEqual(run['status'],'blocked')
        self.assertIn('待登录',run['blocker'])
        with self.assertRaises(ValueError):create_frozen_run('test-a',1,True)
