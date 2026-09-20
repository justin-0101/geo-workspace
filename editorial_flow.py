"""Evidence-linked improvement, editorial review and manual publishing."""
import json
import uuid
from urllib.parse import urlparse
import workspace_store as store
import publish_adapters as adapters
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


def build_outline(title, brief):
    """Mechanical scaffold only: headings from the user's own points, no invented facts."""
    import re as _re
    points = [p.strip() for p in _re.split(r'[\n;；]+', brief or '') if p.strip()]
    if not points:
        points = ['这篇内容要回答的问题']
    lines = [f'# {title}', '']
    for point in points:
        lines += [f'## {point}', '', '（待补充：写清可核查的事实、数据或案例，不要只写形容词。）', '']
    lines += ['## 事实与来源', '', '（待补充：逐条列出可公开核验的事实与出处。）']
    return '\n'.join(lines)


def create_manual_asset(slug, title, brief='', channel=''):
    """Create a content draft that does not depend on any diagnosis batch."""
    title = (title or '').strip()
    if not title:
        raise ValueError('请填写内容标题')
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        if not c.execute('SELECT 1 FROM projects WHERE slug=?', (slug,)).fetchone():
            raise KeyError('项目不存在')
        identity = uuid.uuid4().hex
        timestamp = store.now()
        note = f'直接创建内容（{channel.strip()}）' if channel.strip() else '直接创建内容'
        c.execute('''INSERT INTO editorial_assets
                     (id,project_slug,action_id,source,brief,title,body,created_at,updated_at)
                     VALUES(?,?,NULL,'manual',?,?,?,?,?)''',
                  (identity, slug, (brief or '').strip(), title, build_outline(title, brief), timestamp, timestamp))
        store.record_event(c, slug, 'content_created', note)
    return {'id': identity, 'source': 'manual'}


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
        if '（待补充' in old['body']: raise ValueError('正文还有未填写的“待补充”段落，请先补齐')
        if not old['body'].strip() or not old['facts'].strip(): raise ValueError('请先填写正文及可核查的事实依据')
        c.execute("UPDATE editorial_assets SET status='approved',reviewed_revision=revision,reviewer=?,updated_at=? WHERE id=?",(reviewer.strip(),store.now(),identity))
        store.record_event(c,slug,'content_approved','当前内容版本审核通过')
    return {'message':'审核通过'}


def publish_now(slug,asset_ids,platforms,confirmed,operator=''):
    """选定内容与平台后直接发布：公众号走官方 API 写草稿，其余走浏览器自动化。"""
    if confirmed is not True: raise ValueError('请确认发布')
    allowed={p['id'] for p in PUBLISH_PLATFORMS}
    if not asset_ids: raise ValueError('请选择要发布的内容')
    if not platforms: raise ValueError('请选择发布平台')
    unknown=[p for p in platforms if p not in allowed]
    if unknown: raise ValueError('不支持的平台：'+'、'.join(unknown))
    with store.connection() as c:
        assets=[require(c,'editorial_assets',slug,i) for i in set(asset_ids)]
    for a in assets:
        if a['status']!='approved' or a['reviewed_revision']!=a['revision']:
            raise ValueError('只能发布当前版本已审核的内容')
    evidence_dir=store.DATA/'publish'/store.now().replace(':','-').replace('+','_')
    results=[]
    for a in assets:
        for platform in platforms:
            plan=adapters.describe(a,platform)
            if plan['missing']:
                result=adapters.PublishResult('manual_required',plan['action'])
            else:
                result=adapters.get_adapter(platform).publish(a,None,evidence_dir)
            results.append({'asset_id':a['id'],'asset_title':a['title'],'platform':platform,
                            'platform_label':plan['label'],'mode':plan['mode'],
                            'mode_label':plan['mode_label'],'status':result.status,
                            'message':result.message,'url':result.published_url,
                            'evidence':result.evidence})
            stamp=store.now()
            with store.connection() as c:
                c.execute('''INSERT INTO publishing_jobs
                    (id,project_slug,asset_id,platform,asset_revision,title_snapshot,body_snapshot,
                     status,receipt_url,operator,mode,adapter_note,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(asset_id,platform,asset_revision) DO UPDATE SET
                      status=excluded.status,receipt_url=excluded.receipt_url,operator=excluded.operator,
                      mode=excluded.mode,adapter_note=excluded.adapter_note,updated_at=excluded.updated_at''',
                    (uuid.uuid4().hex,slug,a['id'],platform,a['revision'],a['title'],a['body'],
                     result.status,result.published_url,operator.strip(),plan['mode'],
                     result.message,stamp,stamp))
                store.record_event(c,slug,'publish_'+result.status,
                                   f"{plan['label']}：{result.message[:120]}")
    return {'results':results}


def receipt(slug,identity,url,operator,confirmed):
    parsed=urlparse(url)
    if parsed.scheme not in {'https','http'} or not parsed.hostname: raise ValueError('请填写完整的发布链接')
    if confirmed is not True or not operator.strip(): raise ValueError('请确认已实际发布并填写操作人')
    with store.connection() as c:
        require(c,'publishing_jobs',slug,identity)
        c.execute("UPDATE publishing_jobs SET receipt_url=?,operator=?,status='published_manual',updated_at=? WHERE id=?",(url,operator.strip(),store.now(),identity))
        store.record_event(c,slug,'receipt_recorded','已登记人工发布回执，链接未自动核验')
    return {'message':'已登记人工发布回执'}
