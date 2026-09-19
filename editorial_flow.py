"""Evidence-linked improvement, editorial review and manual publishing."""
import json
import uuid
from urllib.parse import urlparse
import workspace_store as store
from diagnosis_runs import verify_frozen_run
from browser_engine import rows
from diagnosis_config import PUBLISH_PLATFORMS


def require(c, table, slug, identity):
    row=c.execute(f'SELECT * FROM {table} WHERE id=? AND project_slug=?',(identity,slug)).fetchone()
    if not row: raise KeyError('记录不存在')
    return dict(row)


def derive(slug, run_id):
    path=verify_frozen_run(slug,run_id)
    observations=rows(path/'observations.jsonl')
    with store.connection() as c:
        run=require(c,'execution_runs',slug,run_id)
        if run['status'] not in {'completed','degraded'}:
            raise ValueError('请先完成诊断并生成报告')
        count=0
        for o in observations:
            if o.get('status')!='success' or not o.get('response_text') or not o.get('evidence_files'): continue
            category=o.get('classification') or {}
            # A failed observation is never interpreted as a visibility gap.
            if category.get('brand_mention') not in {'no',False}: continue
            title='补充问题相关的事实与内容：'+str(o.get('input_prompt',''))[:90]
            description='本次回答未提及主体。需人工核对原因，不代表缺少内容就是唯一原因。'
            result=c.execute('INSERT OR IGNORE INTO improvement_items(id,project_slug,run_id,task_id,title,description,created_at) VALUES(?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex,slug,run_id,o['task_id'],title,description,store.now()))
            count+=result.rowcount
        store.record_event(c,slug,'improvements_derived',
                           f'生成 {count} 条改善任务（仅取有证据的成功观测）')
    return {'created':count}


def create_asset(slug,action_id):
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        action=require(c,'improvement_items',slug,action_id)
        old=c.execute('SELECT id FROM editorial_assets WHERE action_id=?',(action_id,)).fetchone()
        if old: return {'id':old['id']}
        identity=uuid.uuid4().hex; timestamp=store.now()
        c.execute('INSERT INTO editorial_assets(id,project_slug,action_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                  (identity,slug,action_id,action['title'],timestamp,timestamp))
        c.execute("UPDATE improvement_items SET status='doing' WHERE id=?",(action_id,))
        store.record_event(c,slug,'content_created','由改善任务创建内容草稿')
    return {'id':identity}


def save_asset(slug,identity,title,body,facts,revision):
    if not title.strip(): raise ValueError('请填写标题')
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        old=require(c,'editorial_assets',slug,identity)
        if revision!=old['revision']: raise ValueError('内容已更新，请刷新后再保存')
        c.execute("UPDATE editorial_assets SET title=?,body=?,facts=?,revision=revision+1,status='draft',reviewed_revision=NULL,reviewer=NULL,updated_at=? WHERE id=?",
                  (title.strip(),body,facts,store.now(),identity))
        store.record_event(c,slug,'content_saved','保存草稿，原审核已失效')
    return {'revision':revision+1}


def review_asset(slug,identity,revision,reviewer,confirmed):
    if confirmed is not True or not reviewer.strip(): raise ValueError('请确认事实核验并填写审核人')
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        old=require(c,'editorial_assets',slug,identity)
        if revision!=old['revision']: raise ValueError('内容已更新，请重新审核当前版本')
        if not old['body'].strip() or not old['facts'].strip(): raise ValueError('请先填写正文及可核查的事实依据')
        c.execute("UPDATE editorial_assets SET status='approved',reviewed_revision=revision,reviewer=?,updated_at=? WHERE id=?",(reviewer.strip(),store.now(),identity))
        store.record_event(c,slug,'content_approved','当前内容版本审核通过')
    return {'message':'审核通过'}


def publish_batch(slug,asset_ids,platforms,confirmed,operator):
    if confirmed is not True: raise ValueError('请确认人工发布安排')
    if not operator.strip(): raise ValueError('请填写发布负责人')
    allowed={p['id'] for p in PUBLISH_PLATFORMS}
    if not asset_ids or not platforms or any(p not in allowed for p in platforms): raise ValueError('请选择内容和有效发布平台')
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        assets=[require(c,'editorial_assets',slug,i) for i in set(asset_ids)]
        for a in assets:
            if a['status']!='approved' or a['reviewed_revision']!=a['revision']: raise ValueError('只能发布当前版本已审核的内容')
        created=[]
        for a in assets:
            for platform in set(platforms):
                existing=c.execute('SELECT id FROM publishing_jobs WHERE asset_id=? AND platform=? AND asset_revision=?',(a['id'],platform,a['revision'])).fetchone()
                if existing: continue
                identity=uuid.uuid4().hex;timestamp=store.now()
                c.execute('INSERT INTO publishing_jobs(id,project_slug,asset_id,platform,asset_revision,title_snapshot,body_snapshot,operator,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (identity,slug,a['id'],platform,a['revision'],a['title'],a['body'],operator.strip(),timestamp,timestamp))
                created.append(identity)
        store.record_event(c,slug,'publishing_arranged',f'创建 {len(created)} 个待人工发布任务')
    return {'created':created,'manual_gate':True}


def receipt(slug,identity,url,operator,confirmed):
    parsed=urlparse(url)
    if parsed.scheme not in {'https','http'} or not parsed.hostname: raise ValueError('请填写完整的发布链接')
    if confirmed is not True or not operator.strip(): raise ValueError('请确认已实际发布并填写操作人')
    with store.connection() as c:
        require(c,'publishing_jobs',slug,identity)
        c.execute("UPDATE publishing_jobs SET receipt_url=?,operator=?,status='published_manual',updated_at=? WHERE id=?",(url,operator.strip(),store.now(),identity))
        store.record_event(c,slug,'receipt_recorded','已登记人工发布回执，链接未自动核验')
    return {'message':'已登记人工发布回执'}
