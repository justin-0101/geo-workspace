"""Frozen runs with on-disk evidence contracts understood by the real browser engine."""
import hashlib
import json
import shutil
import uuid
from pathlib import Path
import workspace_store as store
from diagnosis_config import freeze_config

ACTIVE = ('frozen','preflight','ready','running','paused','interrupted','blocked','login')


def json_file(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    temporary.replace(path)


def create_frozen_run(slug, revision, confirmed):
    if confirmed is not True:
        raise ValueError('请确认问题和平台后再冻结诊断')
    created_path = None
    try:
        with store.connection() as c:
            c.execute('BEGIN IMMEDIATE')
            project = c.execute('SELECT * FROM projects WHERE slug=?', (slug,)).fetchone()
            if not project: raise KeyError('项目不存在')
            row = c.execute('SELECT * FROM project_profiles WHERE project_slug=?', (slug,)).fetchone()
            if not row: raise ValueError('请先保存主体资料和诊断配置')
            if revision != row['revision']: raise ValueError('配置已更新，请刷新后重新确认')
            active = c.execute("SELECT id FROM execution_runs WHERE project_slug=? AND status IN ('frozen','preflight','ready','running','paused','interrupted','blocked','login')", (slug,)).fetchone()
            if active: raise ValueError('当前项目已有未结束的诊断，请先处理当前批次')
            config = freeze_config(json.loads(row['profile_json']), json.loads(row['questions_json']), json.loads(row['platforms_json']))
            run_id = uuid.uuid4().hex
            # Paths use server-generated IDs only; no client paths or company names.
            directory = store.DATA / 'runs' / run_id
            directory.mkdir(parents=True, exist_ok=False)
            created_path = directory
            for folder in ['raw','evidence','logs','report']: (directory / folder).mkdir()
            json_file(directory/'frozen-config.json', config)
            digest = hashlib.sha256((directory/'frozen-config.json').read_bytes()).hexdigest()
            timestamp = store.now()
            tasks = []
            for q in config['scope']['questions']:
                for platform in config['platforms']:
                    tasks.append(dict(task_id=f"{q['id']}_{platform['id']}_01", question_id=q['id'],
                                      question_type=q['type'], business_value=q['business_value'],
                                      platform_id=platform['id'], platform_label=platform['label'], repetition=1,
                                      status='pending', started_at=None, completed_at=None,
                                      observation_ref=None, failure_reason=None))
            (directory/'tasks.jsonl').write_text(''.join(json.dumps(t, ensure_ascii=False)+'\n' for t in tasks), encoding='utf8')
            for file in ['observations.jsonl','citations.jsonl']: (directory/file).write_text('', encoding='utf8')
            json_file(directory/'state.json', dict(run_id=run_id, updated_at=timestamp,total=len(tasks),pending=len(tasks),running=0,success=0,failed=0,manual_required=0,next_task_id=tasks[0]['task_id']))
            json_file(directory/'manifest.json', dict(schema_version='1.0',run_id=run_id,created_at=timestamp,
                       config_sha256=digest,expected_questions=8,expected_platforms=len(config['platforms']),
                       expected_tasks=len(tasks),repetitions=1,execution_order='question_major',status='initialized'))
            c.execute('INSERT INTO execution_runs VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (run_id,slug,'frozen',json.dumps(config,ensure_ascii=False),digest,revision,str(directory),None,timestamp,timestamp))
            store.record_event(c,slug,'diagnosis_frozen','已确认诊断问题，等待环境检查')
        return run_id
    except BaseException:
        if created_path is not None:
            # Only remove files created by this failed transaction, never prior runs.
            shutil.rmtree(created_path)
        raise


def get_run(slug, run_id):
    with store.connection() as c:
        row = c.execute('SELECT * FROM execution_runs WHERE id=? AND project_slug=?',(run_id,slug)).fetchone()
        if not row: raise KeyError('诊断批次不存在')
        return dict(row)


def verify_frozen_run(slug, run_id):
    row = get_run(slug,run_id)
    path = Path(row['run_path']).resolve()
    if path.parent != (store.DATA/'runs').resolve() or path.name != run_id:
        raise ValueError('诊断目录不在当前工作空间')
    if hashlib.sha256((path/'frozen-config.json').read_bytes()).hexdigest() != row['config_hash']:
        raise ValueError('冻结配置已被修改，禁止执行')
    return path
