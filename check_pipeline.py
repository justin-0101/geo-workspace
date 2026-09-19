"""Verify the post-execution pipeline on a synthetic run.

Exercises the same vendor scripts the UI depends on after a batch runs:
task-state write-back, report rendering, and final validation.
No platform is contacted, no user data is read or written.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
import workspace_store as store
import diagnosis_runs
from diagnosis_config import suggest_questions

OUT = ROOT / 'test-results'
OUT.mkdir(exist_ok=True)


def observation(task_id, question_id, platform, status, prompt, evidence):
    record = {
        'task_id': task_id, 'question_id': question_id, 'question_type': 'non_brand',
        'platform_id': platform, 'platform_label': platform.title(), 'attempted_at': store.now(),
        'input_prompt': prompt, 'status': status,
        'environment': {'entry_url': 'https://example.invalid/', 'model_or_mode': 'synthetic',
                        'network_state': 'on', 'account_state': 'logged_in'},
        'response_text': '合成测试回答，仅用于验证报告渲染链路，不作为真实诊断结论。' * 3 if status == 'success' else '',
        'sources': [], 'evidence_files': [evidence] if evidence else [],
        'classification': {'brand_mention': 'no', 'recommend_strength': 'none',
                           'description_accuracy': 'unverified', 'source_citation': 'not_shown'},
        'notes': 'SYNTHETIC PIPELINE FIXTURE', 'failure_reason': None if status == 'success' else '合成失败样本',
    }
    return record


def main():
    results = {}
    with tempfile.TemporaryDirectory(prefix='geo-pipeline-') as tmp:
        store.DATA = Path(tmp)
        store.DB = store.DATA / 'pipeline.db'
        store.migrate()
        with store.connection() as c:
            c.execute('INSERT INTO projects VALUES(?,?,?,?)', ('p1', '合成测试主体', 'enterprise', store.now()))
        profile = dict(canonical_name='合成测试主体', business='设备维护', region='广州', audience='工厂',
                       aliases=[], official_pages=[], no_official_web_presence=True)
        store.save_profile('p1', profile, suggest_questions(profile), ['deepseek'], 0)
        run_id = diagnosis_runs.create_frozen_run('p1', 1, True)
        path = diagnosis_runs.verify_frozen_run('p1', run_id)
        tasks = [json.loads(l) for l in (path / 'tasks.jsonl').read_text(encoding='utf-8-sig').splitlines() if l.strip()]
        results['task_count'] = len(tasks)

        # State write-back through the vendor PowerShell helper.
        # The helper enforces pending -> running -> terminal, so drive it properly.
        first = tasks[0]
        (path / 'evidence' / f"{first['task_id']}_top.png").write_bytes(b'SYNTHETIC')
        from browser_engine import SCRIPTS

        def ps_update(task_id, status, reason='', ref=None):
            args = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                    '-File', str(SCRIPTS / 'update-task-state.ps1'),
                    '-RunDir', str(path), '-TaskId', task_id, '-Status', status]
            effective = task_id if (ref is None and status in ('success', 'failed')) else ref
            if effective:
                args += ['-ObservationRef', effective]
            if reason:
                args += ['-FailureReason', reason]
            return subprocess.run(args, capture_output=True, text=True, encoding='utf8',
                                  errors='replace', timeout=120)

        illegal = ps_update(first['task_id'], 'success')
        results['direct_pending_to_success_rejected'] = illegal.returncode != 0
        started = ps_update(first['task_id'], 'running')
        results['pending_to_running_accepted'] = started.returncode == 0
        missing_ref = ps_update(first['task_id'], 'success', ref='')
        results['success_without_observation_ref_rejected'] = missing_ref.returncode != 0
        settled = ps_update(first['task_id'], 'success')
        results['running_to_success_accepted'] = settled.returncode == 0
        driven = {first['task_id']}

        # Synthetic observations for every task so the report can render.
        lines = []
        for i, t in enumerate(tasks):
            ok = t['task_id'] in driven or i % 4 != 0
            status = 'success' if ok else 'failed'
            evidence = f"evidence/{t['task_id']}_top.png"
            (path / 'evidence' / f"{t['task_id']}_top.png").write_bytes(b'SYNTHETIC')
            if t['task_id'] not in driven:
                ran = ps_update(t['task_id'], 'running')
                cut = ps_update(t['task_id'], status, '' if ok else '合成失败样本')
                if ran.returncode or cut.returncode:
                    results.setdefault('state_writeback_errors', []).append(t['task_id'])
            lines.append(json.dumps(observation(t['task_id'], t['question_id'], t['platform_id'],
                                                status, t['task_id'], evidence), ensure_ascii=False))
        (path / 'observations.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf8')
        final = {json.loads(l)['task_id']: json.loads(l)['status']
                 for l in (path / 'tasks.jsonl').read_text(encoding='utf-8-sig').splitlines() if l.strip()}
        results['task0_status_after_writeback'] = final[first['task_id']]
        results['terminal_tasks'] = sum(1 for v in final.values() if v in ('success', 'failed'))
        results['state_writeback_exit'] = 0 if not results.get('state_writeback_errors') else 1

        env = {'PYTHONIOENCODING': 'utf-8'}
        import os
        full_env = {**os.environ, **env}
        render = subprocess.run([sys.executable, str(SCRIPTS / 'render-report.py'), str(path)],
                                capture_output=True, text=True, encoding='utf8', errors='replace',
                                timeout=300, env=full_env)
        results['render_exit'] = render.returncode
        if render.returncode != 0:
            results['render_error'] = (render.stderr or render.stdout)[-400:]
        results['report_files'] = sorted(p.name for p in (path / 'report').glob('*'))

        validate = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                   str(SCRIPTS / 'validate-run.ps1'), '-RunDir', str(path)],
                                  capture_output=True, text=True, encoding='utf8', errors='replace', timeout=300)
        results['validate_exit'] = validate.returncode
        results['validate_tail'] = (validate.stdout or validate.stderr)[-400:]

        # The UI report reader must be able to load a rendered report as text.
        markdown = sorted((path / 'report').glob('*.md'))
        if markdown:
            results['report_text_first_line'] = markdown[0].read_text(encoding='utf-8-sig').splitlines()[0][:80]

    (OUT / 'pipeline-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(results, ensure_ascii=False))


if __name__ == '__main__':
    main()
