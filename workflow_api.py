"""Replacement API under development. Start separately until UI acceptance passes."""
from contextlib import asynccontextmanager
import json
import uuid
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import workspace_store as store
import browser_engine as engine
import editorial_flow as editorial
import publish_adapters as adapters
import platform_login
from diagnosis_config import DIAGNOSIS_PLATFORMS, PUBLISH_PLATFORMS, suggest_questions
from diagnosis_runs import create_frozen_run, verify_frozen_run
from process_guard import ProcessGuard


@asynccontextmanager
async def lifespan(app):
    guard=ProcessGuard(store.DATA/'service.lock').acquire()
    try:
        store.migrate()
        # A restarted server cannot claim an old subprocess is still controlled.
        with store.connection() as c:
            c.execute("UPDATE execution_runs SET status='interrupted',blocker='服务已重启，请核对进度后继续' WHERE status IN ('running','preflight','login')")
        yield
    finally:
        engine.shutdown()
        guard.release()

app = FastAPI(title='GEO 工作空间', lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:4173','http://localhost:4173'],allow_methods=['GET','POST','PUT','DELETE'],allow_headers=['Content-Type'])

@app.exception_handler(ValueError)
async def bad_request(request,exc): return JSONResponse(status_code=409,content={'detail':str(exc)})
@app.exception_handler(KeyError)
async def missing(request,exc): return JSONResponse(status_code=404,content={'detail':str(exc.args[0])})

class ProjectInput(BaseModel):
    name: str = Field(min_length=1,max_length=200)
    target_type: str = 'enterprise'

class ProfileInput(BaseModel):
    profile: dict
    questions: list = []
    platforms: list[str] = []
    revision: int = Field(ge=0)

class Confirmation(BaseModel):
    confirm: bool = False
    revision: int = 0

@app.get('/api/projects')
def projects(q: str = ''):
    with store.connection() as c:
        data=[dict(r) for r in c.execute('SELECT * FROM projects ORDER BY created_at DESC')]
    return {'projects':[r for r in data if q.casefold() in r['name'].casefold()]}

@app.post('/api/projects',status_code=201)
def create_project(payload: ProjectInput):
    name=payload.name.strip()
    if not name: raise ValueError('请填写项目名称')
    if payload.target_type not in {'enterprise','product','person','case'}: raise ValueError('主体类型无效')
    slug=uuid.uuid4().hex
    with store.connection() as c:
        c.execute('INSERT INTO projects VALUES(?,?,?,?)',(slug,name,payload.target_type,store.now()))
        store.record_event(c,slug,'project_created','创建项目')
    return {'slug':slug}

@app.get('/api/projects/{slug}')
def project(slug: str):
    with store.connection() as c:
        row=c.execute('SELECT * FROM projects WHERE slug=?',(slug,)).fetchone()
        if not row: raise KeyError('项目不存在')
        runs=[dict(r) for r in c.execute('SELECT id,status,blocker,created_at FROM execution_runs WHERE project_slug=? ORDER BY created_at DESC',(slug,))]
    return {'project':dict(row),**store.get_profile(slug),'runs':runs}

@app.get('/api/projects/{slug}/overview')
def project_overview(slug: str):
    """Real GEO status for the project overview page, including batch history for trends."""
    stored = store.get_profile(slug)
    with store.connection() as c:
        row = c.execute('SELECT * FROM projects WHERE slug=?', (slug,)).fetchone()
        if not row: raise KeyError('项目不存在')
        project = dict(row)
        runs = [dict(r) for r in c.execute(
            'SELECT id,status,blocker,created_at FROM execution_runs WHERE project_slug=? ORDER BY created_at ASC',
            (slug,))]
    history, latest = [], None
    for r in runs:
        try:
            path = verify_frozen_run(slug, r['id'])
        except (ValueError, KeyError, OSError):
            continue
        metrics_file = path / 'report' / 'metrics.json'
        if not metrics_file.is_file():
            continue
        try:
            m = json.loads(metrics_file.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            continue
        entry = {
            'run_id': r['id'], 'status': r['status'], 'created_at': r['created_at'],
            'generated_at': m.get('generated_at'),
            'planned': m.get('planned_tasks') or 0, 'success': m.get('success') or 0,
            'failed': m.get('failed') or 0,
            'non_brand_success': m.get('non_brand_success') or 0,
            'mentions': m.get('non_brand_brand_mentions') or 0,
            'recommendations': m.get('non_brand_explicit_recommendations') or 0,
            'official_yes': (m.get('official_source_citation_counts') or {}).get('yes', 0),
            'citations': m.get('citation_records') or 0,
        }
        history.append(entry)
        latest = {'entry': entry, 'metrics': m, 'path': path}
    if latest is None:
        return {'project': project, **stored, 'latest': None, 'platforms': [], 'domains': [],
                'incomplete': [], 'completeness': None, 'history': [], 'generated': False}
    problems = engine.incomplete_tasks(latest['path'])
    by_question = {}
    for o in engine.rows(latest['path'] / 'observations.jsonl'):
        qid = o.get('question_id') or str(o.get('task_id') or '').split('_')[0] or None
        if not qid:
            continue
        item = by_question.setdefault(qid, dict(id=qid, type=o.get('question_type'),
                                                success=0, failed=0, answered=0,
                                                official_yes=0, mentions=0))
        cls = o.get('classification') or {}
        if o.get('status') == 'success':
            item['success'] += 1
            item['answered'] += 1
        else:
            item['failed'] += 1
        if cls.get('source_citation') == 'yes':
            item['official_yes'] += 1
        if cls.get('brand_mention') in ('yes', True):
            item['mentions'] += 1
    questions = [by_question[k] for k in sorted(by_question)]
    planned = latest['entry']['planned'] or 0
    complete = max(0, latest['entry']['success'] + latest['entry']['failed'] - len(problems))
    metrics = latest['metrics']
    domains = sorted((metrics.get('citation_domain_counts') or {}).items(),
                     key=lambda kv: (-kv[1], kv[0]))[:8]
    return {'project': project, **stored, 'latest': latest['entry'],
            'platforms': metrics.get('platforms') or [],
            'citation_breakdown': metrics.get('official_source_citation_counts') or {},
            'accuracy_breakdown': metrics.get('accuracy_counts') or {},
            'limits': metrics.get('limits') or [],
            'domains': domains, 'incomplete': problems, 'questions': questions,
            'completeness': {'complete': complete, 'planned': planned,
                             'percent': round(complete * 100 / planned) if planned else 0},
            'history': history, 'generated': True}


@app.delete('/api/projects/{slug}')
def delete_project(slug: str,payload: Confirmation):
    if not payload.confirm: raise ValueError('请确认删除项目')
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        active=c.execute("SELECT id FROM execution_runs WHERE project_slug=? AND status IN ('running','preflight','login')",(slug,)).fetchone()
        if active: raise ValueError('请先停止正在执行的诊断')
        if c.execute('DELETE FROM projects WHERE slug=?',(slug,)).rowcount == 0: raise KeyError('项目不存在')
    # Evidence directories retained, inaccessible without their project mapping.
    return {'message':'项目已删除'}

@app.put('/api/projects/{slug}/profile')
def save_profile(slug: str,payload: ProfileInput):
    revision=store.save_profile(slug,payload.profile,payload.questions,payload.platforms,payload.revision)
    return {'revision':revision,'message':'已保存'}

@app.post('/api/projects/{slug}/question-suggestions')
def questions(slug: str):
    return {'questions':suggest_questions(store.get_profile(slug)['profile'])}

@app.post('/api/projects/{slug}/runs',status_code=201)
def freeze(slug: str,payload: Confirmation):
    return {'id':create_frozen_run(slug,payload.revision,payload.confirm)}

@app.get('/api/projects/{slug}/runs/{run_id}')
def run(slug: str,run_id: str): return engine.status(slug,run_id)

@app.post('/api/projects/{slug}/runs/{run_id}/{action}')
def operate(slug: str,run_id: str,action: str,payload: Confirmation):
    if action in {'preflight','execute','login','report'}: return engine.launch(slug,run_id,action,payload.confirm)
    if action=='close-browser': return engine.close_browser(slug,run_id,payload.confirm)
    if action=='archive': return engine.archive(slug,run_id,payload.confirm)
    if action=='resume':
        if not payload.confirm: raise ValueError('请先确认已处理浏览器提示')
        return engine.resume(slug,run_id)
    if action=='stop':
        if not payload.confirm: raise ValueError('请确认停止诊断')
        engine.stop(slug,run_id); return {'message':'已停止'}
    raise HTTPException(404,'操作不存在')

@app.get('/api/projects/{slug}/runs/{run_id}/files/{file_path:path}')
def file(slug: str,run_id: str,file_path: str):
    directory=verify_frozen_run(slug,run_id)
    path=(directory/file_path).resolve()
    if directory.resolve() not in path.parents or not path.is_file(): raise KeyError('文件不存在')
    if path.relative_to(directory).parts[0] not in {'report','raw','evidence'}: raise KeyError('文件不存在')
    return FileResponse(path,filename=path.name)

class ManualContentInput(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(default='', max_length=4000)
    channel: str = Field(default='', max_length=60)


class AssetInput(BaseModel):
    title: str
    body: str
    facts: str
    revision: int

class ReviewInput(Confirmation):
    reviewer: str = ''

class PublishInput(Confirmation):
    asset_ids: list[str] = []
    platforms: list[str] = []
    operator: str = ''

class ReceiptInput(Confirmation):
    url: str
    operator: str = ''

@app.post('/api/projects/{slug}/runs/{run_id}/improvements/derive')
def derive_actions(slug: str,run_id: str): return editorial.derive(slug,run_id)

@app.get('/api/projects/{slug}/editorial/{kind}')
def editorial_list(slug: str,kind: str):
    store.get_profile(slug)
    table={'actions':'improvement_items','content':'editorial_assets','publications':'publishing_jobs'}.get(kind)
    if not table: raise KeyError('功能不存在')
    with store.connection() as c:
        return {'items':[dict(r) for r in c.execute(f'SELECT * FROM {table} WHERE project_slug=? ORDER BY created_at DESC',(slug,))]}

@app.post('/api/projects/{slug}/actions/{identity}/content')
def asset_create(slug: str,identity: str): return editorial.create_asset(slug,identity)

@app.post('/api/projects/{slug}/content', status_code=201)
def asset_create_manual(slug: str, payload: ManualContentInput):
    return editorial.create_manual_asset(slug, payload.title, payload.brief, payload.channel)


@app.put('/api/projects/{slug}/content/{identity}')
def asset_save(slug: str,identity: str,payload: AssetInput):
    return editorial.save_asset(slug,identity,payload.title,payload.body,payload.facts,payload.revision)

@app.post('/api/projects/{slug}/content/{identity}/review')
def asset_review(slug: str,identity: str,payload: ReviewInput):
    return editorial.review_asset(slug,identity,payload.revision,payload.reviewer,payload.confirm)

@app.get('/api/projects/{slug}/publishing/capabilities')
def publishing_capabilities(slug: str, asset_id: str = ''):
    """每个平台预留接口的当前状态；给出 asset_id 时一并计算缺失的内容要素。"""
    store.get_profile(slug)
    asset = None
    if asset_id:
        with store.connection() as c:
            row = c.execute('SELECT * FROM editorial_assets WHERE id=? AND project_slug=?',
                            (asset_id, slug)).fetchone()
            if not row: raise KeyError('内容不存在')
            asset = dict(row)
    items = [adapters.describe(asset, adapter.spec.id) if asset else adapter.capability()
             for adapter in adapters.REGISTRY.values()]
    return {'asset_id': asset_id or None, 'platforms': items}


@app.post('/api/projects/{slug}/publish')
def publish_assets(slug: str, payload: PublishInput):
    return editorial.publish_now(slug, payload.asset_ids, payload.platforms, payload.confirm, payload.operator)


class CredentialsInput(BaseModel):
    appid: str = Field(default='', max_length=200)
    secret: str = Field(default='', max_length=400)


@app.put('/api/platforms/{platform_id}/credentials')
def save_platform_credentials(platform_id: str, payload: CredentialsInput):
    adapter = adapters.get_adapter(platform_id)
    import platform_credentials
    platform_credentials.set_values(platform_id, payload.model_dump())
    return {'message': '凭据已保存到本机数据库；接口不会回显明文',
            'credentials': adapter.capability()['credentials']}


@app.post('/api/platforms/{platform_id}/credentials/check')
def check_platform_credentials(platform_id: str):
    adapter = adapters.get_adapter(platform_id)
    if adapter.spec.mode != 'api':
        raise ValueError('该平台没有可测试的 API 凭据')
    health = adapter.health()
    if not health.get('ready'):
        raise ValueError(health.get('reason', '凭据未配置'))
    import platform_credentials, wechat_mp
    client = wechat_mp.WechatMpClient(platform_credentials.get(platform_id, 'appid'),
                                      platform_credentials.get(platform_id, 'secret'))
    try:
        return client.check()
    except Exception as exc:
        raise ValueError(f'凭据校验失败：{str(exc)[:200]}')


class LoginInput(BaseModel):
    platforms: list[str] = []


@app.post('/api/platforms/login')
def open_platform_login(payload: LoginInput):
    # 打开登录窗口并在后台轮询识别登录态；已有窗口在开时直接返回当前会话。
    # 返回完整的登录态快照，前端可以直接重绘而不用再发一次 GET。
    started = platform_login.start(payload.platforms or None)
    return {**platform_login.snapshot(),
            'started': started.get('started'), 'message': started.get('message')}


@app.get('/api/platforms/login-state')
def platform_login_state():
    return platform_login.snapshot()


@app.post('/api/platforms/login-state/probe')
def probe_platform_login(payload: LoginInput):
    # 无头重新检测（窗口没开时用）：持锁占用时抛 409，不硬抢 profile。
    result = platform_login.probe(payload.platforms or None)
    return {**platform_login.snapshot(), 'probe': result}

@app.post('/api/projects/{slug}/publishing/{identity}/receipt')
def publication_receipt(slug: str,identity: str,payload: ReceiptInput):
    return editorial.receipt(slug,identity,payload.url,payload.operator,payload.confirm)

@app.get('/api/settings')
def read_settings():
    with store.connection() as c: saved=dict(c.execute('SELECT key,value FROM workspace_preferences').fetchall())
    return {'settings':{'workspace_label':'GEO 工作空间','default_operator':'',**saved}}

class SettingsInput(BaseModel):
    workspace_label: str = Field(min_length=1,max_length=100)
    default_operator: str = Field(default='',max_length=100)

@app.put('/api/settings')
def write_settings(payload: SettingsInput):
    with store.connection() as c:
        for key,value in payload.model_dump().items():
            c.execute('INSERT INTO workspace_preferences VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
    return {'message':'已保存'}

@app.get('/api/projects/{slug}/reports')
def report_list(slug: str):
    store.get_profile(slug)
    with store.connection() as c: runs=[dict(r) for r in c.execute('SELECT id,status FROM execution_runs WHERE project_slug=?',(slug,))]
    out=[]
    for r in runs:
        directory=verify_frozen_run(slug,r['id'])
        for path in (directory/'report').glob('*'):
            if path.is_file() and path.suffix in {'.md','.json','.txt','.csv'}:
                out.append({'run_id':r['id'],'name':path.name,'status':r['status']})
    return {'reports':out}

@app.get('/api/projects/{slug}/runs/{run_id}/text/{file_path:path}')
def text_file(slug: str,run_id: str,file_path: str):
    directory=verify_frozen_run(slug,run_id)
    path=(directory/file_path).resolve()
    if directory.resolve() not in path.parents or not path.is_file(): raise KeyError('文件不存在')
    if path.relative_to(directory).parts[0] not in {'report','raw'} or path.suffix not in {'.md','.json','.txt','.csv'}: raise KeyError('文件不存在')
    if path.stat().st_size>2_000_000: raise ValueError('文件过大，请下载查看')
    return {'text':path.read_text(encoding='utf-8-sig')}

@app.get('/api/platforms')
def platforms(): return {'diagnosis':DIAGNOSIS_PLATFORMS,'publication':adapters.capabilities()}

@app.get('/api/workbench')
def workbench():
    with store.connection() as c:
        events=[dict(r) for r in c.execute('SELECT e.*,p.name AS project_name FROM workflow_events e JOIN projects p ON p.slug=e.project_slug ORDER BY e.id DESC LIMIT 20')]
        pending=[dict(r) for r in c.execute("SELECT r.id,r.project_slug,p.name AS project_name,r.status,r.blocker FROM execution_runs r JOIN projects p ON p.slug=r.project_slug WHERE r.status NOT IN ('completed','archived') ORDER BY r.updated_at DESC")]
        for item in pending: item['view']='projects';item['tab']='runs'
        drafts=[dict(r) for r in c.execute("SELECT a.id,a.project_slug,p.name AS project_name,a.title AS blocker,a.status FROM editorial_assets a JOIN projects p ON p.slug=a.project_slug WHERE a.status='draft' ORDER BY a.updated_at DESC LIMIT 30")]
        for item in drafts: item['view']='content';item['asset']=item['id']
        jobs=[dict(r) for r in c.execute("SELECT j.id,j.project_slug,p.name AS project_name,j.title_snapshot AS blocker,j.status FROM publishing_jobs j JOIN projects p ON p.slug=j.project_slug WHERE j.status='manual_required' ORDER BY j.created_at DESC LIMIT 30")]
        for item in jobs: item['view']='publications'
        pending.extend(drafts);pending.extend(jobs)
    return {'pending':pending,'events':events}
