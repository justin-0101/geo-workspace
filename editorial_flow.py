"""Evidence-linked improvement, content generation, editorial review and publishing."""
import json
import re
import uuid
from urllib.parse import urlparse

import content_generation as generation
import source_materials as materials
import workspace_store as store
import publish_adapters as adapters
from diagnosis_runs import verify_frozen_run
from browser_engine import rows
from diagnosis_config import PUBLISH_PLATFORMS


def require(c, table, slug, identity):
    row=c.execute(f'SELECT * FROM {table} WHERE id=? AND project_slug=?',(identity,slug)).fetchone()
    if not row: raise KeyError('记录不存在')
    return dict(row)


def require_asset(slug, identity):
    """Public guard used by the API routes that only need to verify ownership."""
    with store.connection() as c:
        return require(c, 'editorial_assets', slug, identity)


def delete_asset(slug, identity):
    """Delete a draft. Material files are removed from disk before the rows cascade away."""
    with store.connection() as c:
        asset=require(c,'editorial_assets',slug,identity)
        jobs=c.execute('SELECT count(*) FROM publishing_jobs WHERE asset_id=?',(identity,)).fetchone()[0]
        sources=c.execute('SELECT count(*) FROM asset_sources WHERE asset_id=?',(identity,)).fetchone()[0]
    removed=materials.delete_files_for([identity])
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        require(c,'editorial_assets',slug,identity)
        c.execute('DELETE FROM editorial_assets WHERE id=?',(identity,))
        if asset.get('action_id'):
            # 删掉稿件后，对应的优化清单应该回到待处理，而不是停在 doing/done。
            c.execute("UPDATE improvement_items SET status='todo' WHERE id=?",(asset['action_id'],))
        store.record_event(c,slug,'content_deleted',
                           f"删除内容《{(asset.get('title') or '')[:40]}》（素材 {sources} 个，发布记录 {jobs} 条）")
    return {'message':'已删除内容','removed_files':removed,'deleted_jobs':jobs,'deleted_sources':sources}


def library(slug):
    """Draft library: metadata only, no body, so the list stays small."""
    store.get_profile(slug)
    with store.connection() as c:
        rows=[dict(r) for r in c.execute(
            'SELECT * FROM editorial_assets WHERE project_slug=? ORDER BY created_at DESC',(slug,))]
        material_counts={r['asset_id']:r['n'] for r in c.execute(
            'SELECT asset_id,count(*) AS n FROM asset_sources WHERE project_slug=? GROUP BY asset_id',(slug,))}
        job_counts={r['asset_id']:r['n'] for r in c.execute(
            'SELECT asset_id,count(*) AS n FROM publishing_jobs WHERE project_slug=? GROUP BY asset_id',(slug,))}
    items=[]
    for index,row in enumerate(rows,1):
        try:
            meta=json.loads(row.get('generation_meta_json') or '{}')
        except (TypeError, ValueError):
            meta={}
        try:
            quality=json.loads(row.get('quality_report_json') or '{}')
        except (TypeError, ValueError):
            quality={}
        items.append({
            'index':index, 'id':row['id'], 'title':row['title'], 'source':row['source'],
            'channel':row['channel'], 'status':row['status'], 'revision':row['revision'],
            'reviewed_revision':row['reviewed_revision'], 'reviewer':row['reviewer'],
            'chars':generation.plain_length(row['body']), 'summary':row['summary'],
            'generated_at':meta.get('generated_at') or '', 'engine':meta.get('engine') or '',
            'quality_passed':bool(quality.get('passed')), 'quality_errors':quality.get('errors') or [],
            'material_count':material_counts.get(row['id'],0),
            'job_count':job_counts.get(row['id'],0),
            'action_id':row['action_id'],
            'created_at':row['created_at'], 'updated_at':row['updated_at'],
        })
    return {'items':items}


def _question_id(observation):
    value=str(observation.get('question_id') or '').strip()
    if value: return value
    match=re.match(r'(Q\d+)',str(observation.get('task_id') or ''),re.I)
    return match.group(1).upper() if match else str(observation.get('task_id') or '').strip()


def _report_title(path, question_id):
    report=path/'report'/'optimization-plan.md'
    if not report.is_file(): return ''
    pattern=re.compile(r'^\|\s*'+re.escape(question_id)+r'\s*\|\s*([^|]+?)\s*\|',re.I)
    for line in report.read_text(encoding='utf8',errors='replace').splitlines():
        match=pattern.match(line)
        if match: return match.group(1).strip()
    return ''


def _fallback_title(prompt):
    value=str(prompt or '').strip().rstrip('？?。')
    if not value: return '待确认的内容选题'
    if len(value)>72: value=value[:70].rstrip('，,；;：: ')+'…'
    return value


def derive(slug, run_id):
    """Create one improvement topic per diagnosis question, not one per platform."""
    path=verify_frozen_run(slug,run_id)
    observations=rows(path/'observations.jsonl')
    with store.connection() as c:
        run=require(c,'execution_runs',slug,run_id)
        if run['status'] not in {'completed','degraded'}:
            raise ValueError('请先完成诊断并生成报告')
        grouped={}
        for observation in observations:
            if observation.get('status')!='success' or not observation.get('response_text') or not observation.get('evidence_files'): continue
            category=observation.get('classification') or {}
            # Failed observations and brand questions are never interpreted as content gaps.
            if category.get('brand_mention') not in {'no',False}: continue
            qid=_question_id(observation)
            grouped.setdefault(qid,[]).append(observation)
        count=0
        for qid,items in grouped.items():
            # Old versions used platform task IDs. Treat those rows as existing topics and keep user data.
            old=c.execute('''SELECT 1 FROM improvement_items
                WHERE run_id=? AND (task_id=? OR task_id LIKE ?) LIMIT 1''',(run_id,qid,qid+'_%')).fetchone()
            if old: continue
            prompt=str(items[0].get('input_prompt') or '').strip()
            title=_report_title(path,qid) or _fallback_title(prompt)
            labels=set()
            for item in items:
                label=str(item.get('platform') or '').strip()
                if not label:
                    parts=str(item.get('task_id') or '').split('_')
                    label=parts[1] if len(parts)>1 else ''
                if label: labels.add(label)
            description=f'诊断问题 {qid} 在 {len(items)} 个有效观测中未自然提及主体。请基于企业可核验资料补充内容。'
            if labels: description+=f'涉及平台：{"、".join(sorted(labels))}。'
            result=c.execute('INSERT OR IGNORE INTO improvement_items(id,project_slug,run_id,task_id,title,description,created_at) VALUES(?,?,?,?,?,?,?)',
                       (uuid.uuid4().hex,slug,run_id,qid,title,description,store.now()))
            count+=result.rowcount
        store.record_event(c,slug,'improvements_derived',f'生成 {count} 条优化项（按问题聚合）')
    return {'created':count,'topics':len(grouped)}


def build_outline(title, brief):
    """Mechanical scaffold used before the first evidence-aware generation."""
    points=[p.strip() for p in re.split(r'[\n;；]+',brief or '') if p.strip()]
    if not points: points=['这篇内容要回答的问题']
    lines=[f'# {title}','']
    for point in points:
        lines += [f'## {point}','','（待补充：写清可核查的事实、数据或案例，不要只写形容词。）','']
    lines += ['## 事实与来源','','（待补充：逐条列出可公开核验的事实与出处。）']
    return '\n'.join(lines)


def build_source_bundle(slug, asset=None, action=None):
    """Assemble the traceable inputs used for draft generation.

    Includes uploaded files, fetched URLs and pasted text as raw material. Material text is
    drafting input only; it is not a confirmed business fact and the editor must still
    confirm the facts field before a version can be reviewed.
    """
    profile=store.get_profile(slug)['profile']
    if action is None and asset and asset.get('action_id'):
        with store.connection() as c:
            action=require(c,'improvement_items',slug,asset['action_id'])
    bundle={
        'project_slug':slug,
        'profile':profile,
        'source':(asset or {}).get('source','manual'),
        'brief':(asset or {}).get('brief',''),
        'verified_facts':(asset or {}).get('facts',''),
        'question_id':'', 'question':'', 'suggested_title':'',
        'observations':[], 'evidence_files':[],
        'materials':[], 'materials_text':'',
        'notice':'诊断平台回答只用于理解内容缺口，不作为企业事实。企业事实需由人工核验来源。',
        'material_notice':'素材文字只作为写作输入；资质、案例、价格、效果仍需人工确认后才能发布。',
        'assembled_at':store.now(),
    }
    if asset and asset.get('id'):
        items, combined = materials.digest(slug, asset['id'])
        bundle['materials']=items
        bundle['materials_text']=combined
    if not action: return bundle
    path=verify_frozen_run(slug,action['run_id'])
    observations=rows(path/'observations.jsonl')
    task_id=str(action.get('task_id') or '')
    qid=task_id if re.fullmatch(r'Q\d+',task_id,re.I) else (re.match(r'(Q\d+)',task_id,re.I).group(1) if re.match(r'(Q\d+)',task_id,re.I) else task_id)
    selected=[]
    for observation in observations:
        if (_question_id(observation)==qid or str(observation.get('task_id'))==task_id):
            selected.append(observation)
    question=next((str(x.get('input_prompt') or '').strip() for x in selected if x.get('input_prompt')),'')
    evidence=[]
    summaries=[]
    for observation in selected:
        refs=[str(x) for x in (observation.get('evidence_files') or [])]
        evidence.extend(refs)
        summaries.append({
            'task_id':observation.get('task_id'),
            'platform':observation.get('platform') or (str(observation.get('task_id') or '').split('_')[1] if '_' in str(observation.get('task_id') or '') else ''),
            'status':observation.get('status'),
            'classification':observation.get('classification') or {},
            'response_excerpt':str(observation.get('response_text') or '')[:1200],
            'evidence_files':refs,
            'fact_status':'unverified-model-response',
        })
    bundle.update({
        'action_id':action['id'], 'run_id':action['run_id'],
        'question_id':qid, 'question':question,
        'suggested_title':_report_title(path,qid),
        'observations':summaries,
        'evidence_files':sorted(set(evidence)),
    })
    return bundle


def create_manual_asset(slug, title, brief='', channel=''):
    """Create a content brief that does not depend on a diagnosis batch."""
    title=(title or '').strip()
    if not title: raise ValueError('请填写内容标题')
    profile=store.get_profile(slug)['profile']
    identity=uuid.uuid4().hex; timestamp=store.now()
    initial={'source':'manual','brief':(brief or '').strip(),'facts':''}
    bundle=build_source_bundle(slug,initial)
    note=f'直接创建内容（{channel.strip()}）' if channel.strip() else '直接创建内容'
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        c.execute('''INSERT INTO editorial_assets
          (id,project_slug,action_id,source,brief,channel,audience,objective,title,body,source_bundle_json,created_at,updated_at)
          VALUES(?,?,NULL,'manual',?,?,?,?,?,?,?, ?,?)''',
          (identity,slug,(brief or '').strip(),(channel or '').strip(),str(profile.get('audience') or '').strip(),
           (brief or '').strip(),title,build_outline(title,brief),json.dumps(bundle,ensure_ascii=False),timestamp,timestamp))
        store.record_event(c,slug,'content_created',note)
    return {'id':identity,'source':'manual'}


def create_asset(slug, action_id):
    with store.connection() as c:
        action=require(c,'improvement_items',slug,action_id)
        old=c.execute('SELECT id FROM editorial_assets WHERE action_id=?',(action_id,)).fetchone()
        if old: return {'id':old['id']}
    profile=store.get_profile(slug)['profile']
    bundle=build_source_bundle(slug,{'source':'diagnosis'},action)
    title=bundle.get('suggested_title') or _fallback_title(bundle.get('question') or action['title'])
    identity=uuid.uuid4().hex; timestamp=store.now()
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('SELECT id FROM editorial_assets WHERE action_id=?',(action_id,)).fetchone()
        if old: return {'id':old['id']}
        c.execute('''INSERT INTO editorial_assets
          (id,project_slug,action_id,source,audience,objective,title,body,source_bundle_json,created_at,updated_at)
          VALUES(?,?,?,'diagnosis',?,?,?,?,?,?,?)''',
          (identity,slug,action_id,str(profile.get('audience') or '').strip(),store.reader_objective(bundle.get('question') or ''),title,
           build_outline(title,bundle.get('question') or ''),json.dumps(bundle,ensure_ascii=False),timestamp,timestamp))
        c.execute("UPDATE improvement_items SET status='doing' WHERE id=?",(action_id,))
        store.record_event(c,slug,'content_created','由优化清单创建内容任务书')
    return {'id':identity}


def _asset_values(old, values):
    merged=dict(old)
    for key in ('title','summary','body','facts','brief','channel','audience','objective','tone','keywords','cover'):
        if key in values and values[key] is not None: merged[key]=str(values[key]).strip()
    if 'target_length' in values and values['target_length'] is not None:
        merged['target_length']=max(200,min(int(values['target_length']),10000))
    return merged


def save_asset(slug,identity,title,body,facts,revision,**values):
    if not str(title or '').strip(): raise ValueError('请填写标题')
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        old=require(c,'editorial_assets',slug,identity)
        if revision!=old['revision']: raise ValueError('内容已更新，请刷新后再保存')
        candidate=_asset_values(old,dict(values,title=title,body=body,facts=facts))
        quality=generation.quality_check(candidate)
        c.execute('''UPDATE editorial_assets SET
          title=?,summary=?,body=?,facts=?,brief=?,channel=?,audience=?,objective=?,tone=?,keywords=?,target_length=?,cover=?,
          quality_report_json=?,revision=revision+1,status='draft',reviewed_revision=NULL,reviewer=NULL,updated_at=? WHERE id=?''',
          (candidate['title'],candidate['summary'],candidate['body'],candidate['facts'],candidate['brief'],candidate['channel'],
           candidate['audience'],candidate['objective'],candidate['tone'],candidate['keywords'],candidate['target_length'],candidate['cover'],
           json.dumps(quality,ensure_ascii=False),store.now(),identity))
        if old.get('action_id'):
            c.execute("UPDATE improvement_items SET status='doing' WHERE id=?",(old['action_id'],))
        store.record_event(c,slug,'content_saved','保存草稿，原审核已失效')
    return {'revision':revision+1,'quality':quality}


def content_context(slug,identity):
    with store.connection() as c:
        asset=require(c,'editorial_assets',slug,identity)
    bundle=build_source_bundle(slug,asset)
    return {'source_bundle':bundle,'generator':generation.capability(),
            'quality':generation.quality_check(asset),
            'sources':materials.list_sources(slug,identity),
            'generation_meta':json.loads(asset.get('generation_meta_json') or '{}')}


def generate_asset(slug,identity,revision,confirm_overwrite=False,**values):
    with store.connection() as c:
        old=require(c,'editorial_assets',slug,identity)
    if revision!=old['revision']: raise ValueError('内容已更新，请刷新后再生成')
    candidate=_asset_values(old,values)
    body=str(old.get('body') or '').strip()
    if body and not generation.PLACEHOLDER_RE.search(body) and not confirm_overwrite:
        raise ValueError('当前正文已有内容，重新生成会覆盖正文，请先确认覆盖')
    bundle=build_source_bundle(slug,candidate)
    # 素材里能提出可核验来源时，先把来源写成事实依据的草稿，再由人工审核确认。
    if not str(candidate.get('facts') or '').strip():
        lines=materials.source_lines(materials.list_sources(slug,identity))
        if lines: candidate['facts']='\n'.join(lines)
    result=generation.generate(candidate,bundle)
    candidate.update(title=result['title'],summary=result.get('summary',''),body=result['body'],
                     facts=result.get('facts') or candidate.get('facts',''))
    quality=generation.quality_check(candidate)
    meta={key:result.get(key) for key in ('engine','model','generated_at','warnings')}
    meta['material_ids']=[item['id'] for item in bundle['materials']]
    meta['material_chars']=len(bundle['materials_text'])
    meta['notes']=list(result.get('notes') or [])
    waiting=materials.pending_count(slug,identity)
    if waiting:
        meta['warnings']=list(meta['warnings'])+[f'还有 {waiting} 个素材在 OCR 识别中，本次生成没有用到它们；识别完成后可重新生成。']
    timestamp=store.now()
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        current=require(c,'editorial_assets',slug,identity)
        if revision!=current['revision']: raise ValueError('内容已更新，请刷新后再生成')
        c.execute('''UPDATE editorial_assets SET
          title=?,summary=?,body=?,facts=?,brief=?,channel=?,audience=?,objective=?,tone=?,keywords=?,target_length=?,cover=?,
          source_bundle_json=?,generation_meta_json=?,quality_report_json=?,revision=revision+1,status='draft',
          reviewed_revision=NULL,reviewer=NULL,updated_at=? WHERE id=?''',
          (candidate['title'],candidate['summary'],candidate['body'],candidate['facts'],candidate['brief'],candidate['channel'],
           candidate['audience'],candidate['objective'],candidate['tone'],candidate['keywords'],candidate['target_length'],candidate['cover'],
           json.dumps(bundle,ensure_ascii=False),json.dumps(meta,ensure_ascii=False),json.dumps(quality,ensure_ascii=False),timestamp,identity))
        if current.get('action_id'):
            c.execute("UPDATE improvement_items SET status='doing' WHERE id=?",(current['action_id'],))
        store.record_event(c,slug,'content_generated',f"使用 {meta['engine']} 生成内容草稿")
    return {'id':identity,'revision':revision+1,'engine':meta['engine'],'model':meta['model'],
            'warnings':meta['warnings'],'notes':meta['notes'],'quality':quality,'title':candidate['title']}


def review_asset(slug,identity,revision,reviewer,confirmed):
    if confirmed is not True or not reviewer.strip(): raise ValueError('请确认事实核验并填写审核人')
    with store.connection() as c:
        c.execute('BEGIN IMMEDIATE')
        old=require(c,'editorial_assets',slug,identity)
        if revision!=old['revision']: raise ValueError('内容已更新，请重新审核当前版本')
        quality=generation.quality_check(old)
        if quality['errors']: raise ValueError('内容质量检查未通过：'+'；'.join(quality['errors']))
        c.execute("UPDATE editorial_assets SET status='approved',reviewed_revision=revision,reviewer=?,quality_report_json=?,updated_at=? WHERE id=?",
                  (reviewer.strip(),json.dumps(quality,ensure_ascii=False),store.now(),identity))
        if old.get('action_id'):
            c.execute("UPDATE improvement_items SET status='done' WHERE id=?",(old['action_id'],))
        store.record_event(c,slug,'content_approved','当前内容版本审核通过')
    return {'message':'审核通过','quality':quality}


def publish_now(slug,asset_ids,platforms,confirmed,operator=''):
    """Publish selected approved assets after repeating the content quality gate."""
    if confirmed is not True: raise ValueError('请确认发布')
    allowed={p['id'] for p in PUBLISH_PLATFORMS}
    if not asset_ids: raise ValueError('请选择要发布的内容')
    if not platforms: raise ValueError('请选择发布平台')
    unknown=[p for p in platforms if p not in allowed]
    if unknown: raise ValueError('不支持的平台：'+'、'.join(unknown))
    with store.connection() as c:
        assets=[require(c,'editorial_assets',slug,i) for i in set(asset_ids)]
    for asset in assets:
        if asset['status']!='approved' or asset['reviewed_revision']!=asset['revision']:
            raise ValueError('只能发布当前版本已审核的内容')
        quality=generation.quality_check(asset)
        if quality['errors']:
            raise ValueError(f"内容《{asset['title']}》未通过发布前质量检查："+'；'.join(quality['errors']))
    evidence_dir=store.DATA/'publish'/store.now().replace(':','-').replace('+','_')
    results=[]
    for asset in assets:
        for platform in platforms:
            plan=adapters.describe(asset,platform)
            if plan['missing']:
                result=adapters.PublishResult('manual_required',plan['action'])
            else:
                result=adapters.get_adapter(platform).publish(asset,None,evidence_dir)
            results.append({'asset_id':asset['id'],'asset_title':asset['title'],'platform':platform,
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
                    (uuid.uuid4().hex,slug,asset['id'],platform,asset['revision'],asset['title'],asset['body'],
                     result.status,result.published_url,operator.strip(),plan['mode'],result.message,stamp,stamp))
                store.record_event(c,slug,'publish_'+result.status,f"{plan['label']}：{result.message[:120]}")
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
