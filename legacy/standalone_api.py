# -*- coding: utf-8 -*-
"""新版 GEO 平台的独立本地 API。与原版 geo-pi-runbook 数据库、页面和路由完全隔离。"""
from pathlib import Path
from datetime import datetime
import json, sqlite3, re, uuid
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
DB = DATA / "redesign.db"
PLATFORMS = ["微信公众号", "知乎", "百家号", "今日头条", "CSDN", "小红书", "官网", "搜狐号", "大鱼号", "百度百科"]
TYPE_LABELS = {"enterprise":"企业", "product":"产品", "person":"个人", "case":"案例"}
app = FastAPI(title="GEO Platform Redesign API")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:4173", "http://localhost:4173"], allow_methods=["*"], allow_headers=["*"])

def now(): return datetime.now().isoformat(timespec="seconds")
def conn():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init():
    c=conn(); c.executescript('''
    CREATE TABLE IF NOT EXISTS projects (slug TEXT PRIMARY KEY, name TEXT NOT NULL, target_type TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, project_slug TEXT NOT NULL, title TEXT NOT NULL, description TEXT, priority TEXT, gap_type TEXT, status TEXT, source_task_ids TEXT, FOREIGN KEY(project_slug) REFERENCES projects(slug) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS content_assets (id TEXT PRIMARY KEY, project_slug TEXT NOT NULL, title TEXT NOT NULL, status TEXT, FOREIGN KEY(project_slug) REFERENCES projects(slug) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS publications (id TEXT PRIMARY KEY, project_slug TEXT NOT NULL, asset_id TEXT, platform TEXT, status TEXT, manual_gate INTEGER DEFAULT 1, created_at TEXT, FOREIGN KEY(project_slug) REFERENCES projects(slug) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS diagnosis_batches (id TEXT PRIMARY KEY, project_slug TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT, FOREIGN KEY(project_slug) REFERENCES projects(slug) ON DELETE CASCADE);
    CREATE TABLE IF NOT EXISTS diagnosis_tasks (id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, platform TEXT NOT NULL, question TEXT NOT NULL, status TEXT NOT NULL, result TEXT, updated_at TEXT NOT NULL, FOREIGN KEY(batch_id) REFERENCES diagnosis_batches(id) ON DELETE CASCADE);
    ''')
    c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('workspace_label','我的 GEO 工作台'),('default_operator','')")
    c.commit(); c.close()
init()

def project_row(slug):
    c=conn(); r=c.execute('SELECT * FROM projects WHERE slug=?',(slug,)).fetchone();
    if not r: c.close(); raise HTTPException(404,'项目不存在。')
    p=dict(r); p['target_type_label']=TYPE_LABELS.get(p['target_type'],p['target_type']); p['counts']={
      'actions': c.execute('SELECT COUNT(*) FROM actions WHERE project_slug=?',(slug,)).fetchone()[0],
      'assets': c.execute('SELECT COUNT(*) FROM content_assets WHERE project_slug=?',(slug,)).fetchone()[0],
      'publications': c.execute('SELECT COUNT(*) FROM publications WHERE project_slug=?',(slug,)).fetchone()[0]}
    c.close(); p['latest_run']=None; return p

def project_list():
    c=conn(); rows=c.execute('SELECT * FROM projects ORDER BY created_at DESC').fetchall(); out=[]
    for r in rows:
      p=dict(r); p['target_type_label']=TYPE_LABELS.get(p['target_type'],p['target_type']); p['counts']={
       'actions':c.execute('SELECT COUNT(*) FROM actions WHERE project_slug=?',(p['slug'],)).fetchone()[0],
       'assets':c.execute('SELECT COUNT(*) FROM content_assets WHERE project_slug=?',(p['slug'],)).fetchone()[0],
       'publications':c.execute('SELECT COUNT(*) FROM publications WHERE project_slug=?',(p['slug'],)).fetchone()[0]}; p['latest_run']=None; out.append(p)
    c.close(); return out

@app.get('/api/projects')
def projects(q: str=''):
    rows=project_list(); q=q.strip().lower(); return {'projects':[p for p in rows if not q or q in p['name'].lower() or q in p['slug'].lower()]}

@app.post('/api/projects')
def create_project(payload: dict=Body(...)):
    name=str(payload.get('name') or '').strip(); typ=str(payload.get('target_type') or 'enterprise')
    if not name: raise HTTPException(400,'请填写项目名称。')
    if typ not in TYPE_LABELS: typ='enterprise'
    slug='redesign-'+datetime.now().strftime('%Y%m%d%H%M%S')+'-'+uuid.uuid4().hex[:6]
    c=conn(); c.execute('INSERT INTO projects VALUES(?,?,?,?)',(slug,name,typ,now())); c.commit(); c.close(); return {'slug':slug,'message':'项目已创建。'}

@app.delete('/api/projects/{slug}')
def delete_project(slug: str):
    project_row(slug); c=conn(); c.execute('PRAGMA foreign_keys=ON'); c.execute('DELETE FROM projects WHERE slug=?',(slug,)); c.commit(); c.close(); return {'message':'项目已删除。','slug':slug}

@app.get('/api/projects/{slug}/overview')
def overview(slug: str):
    p=project_row(slug); return {'project':p,'workflow':{'latest_status':'未开始','next_label':'开始项目配置','next_note':'请先补充主体资料和公开页面，再创建诊断批次。','next_url':'projects.html'}}

FROZEN_QUESTIONS = [
    '当用户询问该主体时，AI 是否能准确识别它？',
    '该主体的核心产品、服务或观点是什么？',
    '该主体与同类对象相比有什么差异？',
    '用户会在什么场景下被推荐该主体？',
    '该主体有哪些可信的公开证据？',
    'AI 是否能找到该主体的官方来源？',
    '该主体当前最明显的认知缺口是什么？',
    '如果用户准备购买或合作，AI 会如何评价？',
]

@app.get('/api/projects/{slug}/diagnosis')
def diagnosis(slug: str):
    project_row(slug); c=conn(); batches=[dict(r) for r in c.execute('SELECT * FROM diagnosis_batches WHERE project_slug=? ORDER BY created_at DESC',(slug,)).fetchall()]
    for b in batches:
        b['tasks']=[dict(r) for r in c.execute('SELECT * FROM diagnosis_tasks WHERE batch_id=? ORDER BY platform,id',(b['id'],)).fetchall()]
    c.close(); latest=batches[0] if batches else None
    if latest:
        total=len(latest['tasks']); success=sum(x['status']=='completed' for x in latest['tasks']); failed=sum(x['status']=='failed' for x in latest['tasks'])
        run={'run_id':latest['id'],'status':latest['status'],'task_count':total,'state':{'total':total,'success':success,'failed':failed,'pending':total-success-failed},'tasks':latest['tasks']}
    else: run=None
    return {'run':run,'batches':batches,'questions':FROZEN_QUESTIONS}

@app.post('/api/projects/{slug}/diagnosis/start')
def start_diagnosis(slug: str, payload: dict=Body(default={} )):
    project_row(slug); c=conn(); active=c.execute("SELECT id FROM diagnosis_batches WHERE project_slug=? AND status IN ('prepared','running') ORDER BY created_at DESC LIMIT 1",(slug,)).fetchone()
    if active: c.close(); return {'batch_id':active['id'],'message':'已有一轮诊断在处理中。','created':False}
    batch='batch-'+datetime.now().strftime('%Y%m%d%H%M%S')+'-'+uuid.uuid4().hex[:6]; created=now(); c.execute('INSERT INTO diagnosis_batches VALUES(?,?,?,?,?,?)',(batch,slug,'prepared',created,None,None))
    platforms=payload.get('platforms') or PLATFORMS[:4]
    for platform in platforms:
        for i,q in enumerate(FROZEN_QUESTIONS,1):
            c.execute('INSERT INTO diagnosis_tasks VALUES(?,?,?,?,?,?,?)',(f'{batch}:{i}:{uuid.uuid4().hex[:6]}',batch,str(platform),q,'pending',None,created))
    c.commit(); c.close(); return {'batch_id':batch,'message':'诊断批次已创建，等待逐平台执行。','created':True,'task_count':len(platforms)*len(FROZEN_QUESTIONS)}

@app.post('/api/projects/{slug}/diagnosis/tasks/{task_id}')
def update_diagnosis_task(slug: str, task_id: str, payload: dict=Body(...)):
    project_row(slug); status=str(payload.get('status') or 'pending')
    if status not in {'pending','completed','failed','skipped'}: raise HTTPException(400,'无效的诊断任务状态。')
    c=conn(); row=c.execute('SELECT t.id FROM diagnosis_tasks t JOIN diagnosis_batches b ON b.id=t.batch_id WHERE t.id=? AND b.project_slug=?',(task_id,slug)).fetchone()
    if not row: c.close(); raise HTTPException(404,'诊断任务不存在。')
    c.execute('UPDATE diagnosis_tasks SET status=?,result=?,updated_at=? WHERE id=?',(status,str(payload.get('result') or ''),now(),task_id)); c.commit(); c.close(); return {'message':'诊断任务已保存。','status':status}
@app.get('/api/projects/{slug}/compare')
def compare(slug: str): project_row(slug); return {'run_id':None,'platforms':[]}
@app.get('/api/projects/{slug}/evidence')
def evidence(slug: str): project_row(slug); return {'evidence':[]}

@app.get('/api/projects/{slug}/actions')
def actions(slug: str):
    project_row(slug); c=conn(); rows=c.execute('SELECT * FROM actions WHERE project_slug=? ORDER BY id',(slug,)).fetchall(); c.close(); return {'run_id':None,'actions':[dict(r,source_task_ids=json.loads(r['source_task_ids'] or '[]')) for r in rows]}

@app.post('/api/projects/{slug}/actions/{action_id}/status')
def action_status(slug: str, action_id: str, payload: dict=Body(...)):
    project_row(slug); status=str(payload.get('status') or '')
    if status not in {'todo','doing','done','skipped'}: raise HTTPException(400,'无效的任务状态。')
    c=conn(); cur=c.execute('UPDATE actions SET status=? WHERE id=? AND project_slug=?',(status,action_id,slug)); c.commit(); c.close()
    if not cur.rowcount: raise HTTPException(404,'改善任务不存在。')
    return {'message':'改善任务状态已更新。','status':status}

@app.get('/api/projects/{slug}/content')
def content(slug: str):
    project_row(slug); c=conn(); rows=c.execute('SELECT * FROM content_assets WHERE project_slug=? ORDER BY id',(slug,)).fetchall(); c.close(); return {'assets':[dict(r) for r in rows]}
@app.get('/api/projects/{slug}/publications')
def publications(slug: str):
    project_row(slug); c=conn(); rows=c.execute('SELECT * FROM publications WHERE project_slug=? ORDER BY created_at DESC',(slug,)).fetchall(); c.close(); return {'publications':[dict(r) for r in rows]}
@app.post('/api/projects/{slug}/publications/batch')
def publication_batch(slug: str, payload: dict=Body(...)):
    project_row(slug)
    if payload.get('confirm') is not True: raise HTTPException(400,'请先确认内容、平台和人工发布安排。')
    return {'created':[],'manual_gate':True,'message':'当前版本只创建人工发布安排，不会自动上线。'}
@app.get('/api/projects/{slug}/reports')
def reports(slug: str): project_row(slug); return {'reports':[]}

@app.get('/api/platforms')
def platforms(): return {'platforms':[{'id':re.sub(r'[^a-z0-9]+','-',x.lower()).strip('-'),'label':x,'mode':'人工发布','manual_gate':True} for x in PLATFORMS]}
@app.get('/api/settings')
def get_settings():
    c=conn(); rows=c.execute('SELECT key,value FROM settings').fetchall(); c.close(); return {'settings':dict(rows)}
@app.post('/api/settings')
def save_settings(payload: dict=Body(...)):
    c=conn();
    for k in ('workspace_label','default_operator'): c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,str(payload.get(k) or '')))
    c.commit(); c.close(); return {'settings':payload,'message':'工作空间设置已保存。'}
