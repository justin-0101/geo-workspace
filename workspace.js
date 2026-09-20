(() => {
'use strict';
const API = 'http://127.0.0.1:8798';
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// 后端时间戳都是 UTC（带 +00:00），直接截字符串会比本地时间早 8 小时。
const localTime = v => { const d = new Date(v || ''); return isNaN(d) ? '' : d.toLocaleString('zh-CN', {year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}); };
const localDay = v => { const d = new Date(v || ''); return isNaN(d) ? '' : `${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`; };
function md(text) {
 const lines=String(text||'').replace(/\r\n?/g,'\n').split('\n');
 const inline=s=>esc(s).replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>').replace(/`(.+?)`/g,'<code>$1</code>');
 const out=[];let i=0;
 while(i<lines.length){
  const line=lines[i];
  if(/^\s*\|/.test(line)&&/^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i+1]||'')){
   const cells=r=>r.trim().replace(/^\|/,'').replace(/\|$/,'').split('|').map(c=>c.trim());
   const head=cells(line);i+=2;const rows=[];
   while(i<lines.length&&/^\s*\|/.test(lines[i])){rows.push(cells(lines[i]));i++;}
   out.push('<div class="table-scroll"><table><thead><tr>'+head.map(h=>`<th>${inline(h)}</th>`).join('')+'</tr></thead><tbody>'+rows.map(r=>'<tr>'+r.map(c=>`<td>${inline(c)}</td>`).join('')+'</tr>').join('')+'</tbody></table></div>');
   continue;
  }
  const h=line.match(/^(#{1,4})\s+(.*)$/);
  if(h){const lv=Math.min(4,h[1].length+1);out.push(`<h${lv}>${inline(h[2])}</h${lv}>`);i++;continue;}
  if(/^\s*[-*]\s+/.test(line)){
   const items=[];
   while(i<lines.length&&/^\s*[-*]\s+/.test(lines[i])){items.push(lines[i].replace(/^\s*[-*]\s+/,''));i++;}
   out.push('<ul>'+items.map(x=>`<li>${inline(x)}</li>`).join('')+'</ul>');continue;
  }
  if(!line.trim()){i++;continue;}
  out.push(`<p>${inline(line)}</p>`);i++;
 }
 return out.join('');
}
const labels = {workbench:'工作台',projects:'诊断项目',actions:'改善任务',content:'内容生产',publications:'批量发布',reports:'报告中心',platforms:'模型与平台',settings:'设置'};
const states = {archived:'已终止',degraded:'报告不完整',login:'等待登录',frozen:'待检查',preflight:'检查中',ready:'可执行',running:'执行中',paused:'等待人工处理',blocked:'需处理',interrupted:'已中断',completed:'已完成',pending:'待执行',success:'成功',failed:'失败',manual_required:'需人工处理'};
let epoch = 0, timer = null;
const route = () => { const [view='workbench', query=''] = location.hash.slice(1).split('?'); return {view, params:new URLSearchParams(query)}; };
const link = (view, project, tab) => '#'+view+(project?'?project='+encodeURIComponent(project)+(tab?'&tab='+tab:''):'');
async function api(path, method='GET', body) { const abort=new AbortController(),deadline=setTimeout(()=>abort.abort(),20000);try{const r=await fetch(API+path,{method,signal:abort.signal,headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})}); const d=await r.json().catch(()=>({})); if(!r.ok) throw Error(typeof d.detail==='string'?d.detail:'提交信息有误，请检查填写内容'); return d;}catch(e){if(e.name==='AbortError')throw Error('请求超时，请刷新核对是否已保存，避免重复提交');if(e instanceof TypeError)throw Error('服务连接失败，请确认本地服务已启动后重新加载');throw e;}finally{clearTimeout(deadline);} }
function notice(text) { $('#notice').textContent=text; $('#notice').hidden=!text; }
function head(title, action='') { return `<div class="page-head"><h1>${esc(title)}</h1>${action}</div>`; }
function show(html, token) { if(token===epoch) $('#view').innerHTML=html; }
function badge(state) { const tone=['completed','success','approved','已审核'].includes(state)?'good':['failed','interrupted','blocked'].includes(state)?'bad':['pending','frozen','manual_required','login','degraded'].includes(state)?'warn':'';return `<span class="badge ${tone}">${esc(states[state]||state)}</span>`; }
function modal(title, body, submit, button='确定') {
 const d=$('#modal'); $('#modal-title').textContent=title; $('#modal-body').innerHTML=body; $('#modal-error').textContent=''; $('#confirm-modal').textContent=button;
 $('#modal-form').onsubmit=async e=>{e.preventDefault(); const b=$('#confirm-modal');b.disabled=true;try{const result=await submit(new FormData(e.target));d.close();await render();if(result&&result.message)notice(result.message);}catch(err){$('#modal-error').textContent=err.message;}finally{b.disabled=false;}}; d.showModal();
}
$('#close-modal').onclick=$('#cancel-modal').onclick=()=>$('#modal').close();
function createProject() { modal('创建项目','<label>项目名称<input name="name" required maxlength="200" autofocus></label><label>主体类型<select name="target_type"><option value="enterprise">企业</option><option value="product">产品</option><option value="person">个人</option><option value="case">案例</option></select></label>',async data=>{const r=await api('/api/projects','POST',Object.fromEntries(data));location.hash=link('projects',r.slug,'profile');},'创建'); }
async function render() {
 clearTimeout(timer);const token=++epoch; const {view,params}=route();notice('');
 const context=params.get('project')||sessionStorage.getItem('geo-project');
 if(params.get('project'))sessionStorage.setItem('geo-project',params.get('project'));
 document.querySelectorAll('nav a').forEach(a=>{const target=a.hash.slice(1).split('?')[0];a.classList.toggle('active',target===view);a.href=link(target,['actions','content','publications','reports'].includes(target)?context:null);});
 $('#breadcrumb').textContent=labels[view]||'工作台';$('#project-context').textContent='';
 $('#view').innerHTML='<p class="muted">加载中…</p>';
 try {
 if(view==='workbench') {
  const d=await api('/api/workbench');show(head('工作台','<button id="create" class="primary">创建项目</button>')+`<div class="split"><section class="panel"><h2>待处理</h2>${d.pending.map(x=>`<a class="row" href="${link(x.view||'projects',x.project_slug,x.tab)}${x.asset?'&asset='+encodeURIComponent(x.asset):''}"><span>${esc(x.project_name)}<small>${esc(x.blocker||states[x.status]||x.status)}</small></span><span>继续处理</span></a>`).join('')||'<p class="empty">暂无待处理事项</p>'}</section><section class="panel"><h2>最近活动</h2>${d.events.map(x=>`<div class="row"><span>${esc(x.project_name)}<small>${esc(x.title)}</small></span><small>${esc(localTime(x.created_at))}</small></div>`).join('')||'<p class="empty">暂无活动</p>'}</section></div>`,token);if(token===epoch)$('#create').onclick=createProject;
 } else if(view==='projects' && !params.get('project')) {
  const d=await api('/api/projects');show(head('诊断项目','<button id="create" class="primary">创建项目</button>')+`<section class="panel"><div class="toolbar"><input id="search" aria-label="搜索项目" placeholder="搜索项目"></div><div class="table-scroll"><table><thead><tr><th>项目</th><th>创建时间</th><th>操作</th></tr></thead><tbody>${d.projects.map(p=>`<tr data-name="${esc(p.name)}"><td><a href="${link('projects',p.slug)}">${esc(p.name)}</a></td><td>${esc(localTime(p.created_at))}</td><td><button data-delete="${esc(p.slug)}" data-title="${esc(p.name)}" class="danger">删除</button></td></tr>`).join('')||'<tr><td colspan="3" class="empty">还没有项目</td></tr>'}</tbody></table></div></section>`,token);
  if(token!==epoch)return;$('#create').onclick=createProject;$('#search').oninput=e=>document.querySelectorAll('tr[data-name]').forEach(r=>r.hidden=!r.dataset.name.toLowerCase().includes(e.target.value.toLowerCase()));
  document.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>modal('删除项目',`<p>确认删除「${esc(b.dataset.title)}」？此操作不可撤销。</p>`,()=>api('/api/projects/'+b.dataset.delete,'DELETE',{confirm:true}),'删除'));
 } else if(view==='projects') {
  const slug=params.get('project'), base='/api/projects/'+encodeURIComponent(slug), d=await api(base), tab=params.get('tab')||'overview';
  const tabs=`<div class="tabs">${[['overview','概览'],['profile','主体资料'],['config','诊断配置'],['runs','执行与结果']].map(([k,v])=>`<a class="${tab===k?'active':''}" href="${link('projects',slug,k)}">${v}</a>`).join('')}</div>`;
  show(head(d.project.name,'<a class="button" href="#projects">返回项目列表</a>')+tabs+'<div id="project-view"></div>',token);if(token!==epoch)return;
  $('#project-context').textContent=d.project.name;const v=$('#project-view'),p=d.profile;
  if(tab==='overview'){
   const o=await api(base+'/overview');if(token!==epoch)return;
   if(!o.generated){
    v.innerHTML='<section class="panel"><h2>还没有诊断数据</h2><p class="muted">完成一次诊断后，这里会显示该项目的 GEO 现状、证据完整度与引用来源。</p><div class="toolbar"><a class="button primary" href="'+link('projects',slug,'config')+'">配置诊断</a>'+(d.runs.length?'<a class="button" href="'+link('projects',slug,'runs')+'">查看批次</a>':'')+'</div></section>';
    return;
   }
   const k=o.latest,c=o.completeness||{percent:0,complete:0,planned:0},cb=o.citation_breakdown||{};
   const citedYes=cb.yes||0;
   const mentionRate=k.non_brand_success?Math.round(k.mentions*100/k.non_brand_success):0;
   const bar=(val,max)=>`<div class="bar-track"><div class="bar-fill" style="width:${max?Math.round(val/max*100):0}%"></div></div>`;
   const domMax=Math.max(1,...(o.domains||[]).map(x=>x[1]));
   const qs=o.questions||[],qmax=Math.max(1,...qs.map(q=>Math.max(q.answered||0,q.official_yes||0)));
   const hist=(o.history||[]).slice(-6),isTrend=hist.length>=2;
   const hmax=Math.max(1,...hist.map(x=>Math.max(x.official_yes||0,x.mentions||0)));
   const line=(arr,key,max)=>arr.map((x,i)=>`${(arr.length===1?50:i*100/(arr.length-1)).toFixed(2)},${(100-((x[key]||0)/max)*100).toFixed(2)}`).join(' ');
   const chartBody=isTrend
    ?`<div class="legend"><span><i></i>官方来源引用</span><span><i class="alt"></i>自然提及</span></div><div class="chart"><svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="${line(hist,'official_yes',hmax)}" fill="none" stroke="#176b88" stroke-width="1.6" vector-effect="non-scaling-stroke"/><polyline points="${line(hist,'mentions',hmax)}" fill="none" stroke="#d97835" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg></div><div class="xlabels">${hist.map(x=>`<span>${esc(localDay(x.generated_at||x.created_at))}</span>`).join('')}</div>`
    :`<div class="legend"><span><i></i>有效回答</span><span><i class="alt"></i>官方来源引用</span></div><div class="qchart">${qs.map(q=>`<div class="qgroup"><div class="qbars"><i style="height:${Math.round((q.answered||0)/qmax*100)}%"></i><i class="alt" style="height:${Math.round((q.official_yes||0)/qmax*100)}%"></i></div><span>${esc(q.id)}</span></div>`).join('')}</div>`;
   const conclusion=`非品牌题 ${k.non_brand_success} 条有效回答中，自然提及 ${k.mentions} 次、明确推荐 ${k.recommendations} 次；官方来源引用命中 ${citedYes} 条（覆盖 ${citedYes}/${k.planned} 题）。`;
   v.innerHTML=`
   <div class="page-head"><div><p class="eyebrow">GEO 现状</p><h2 style="margin:0">${esc(o.project.name)}</h2><p class="muted" style="margin:4px 0 0">最近批次 ${esc(localTime(k.generated_at||k.created_at).slice(0,10))} · ${esc(states[k.status]||k.status)}</p></div><div class="toolbar" style="margin:0"><a class="button primary" href="${link('reports',slug)}&run=${encodeURIComponent(k.run_id)}">查看报告</a><a class="button" href="${link('projects',slug,'runs')}">执行与结果</a></div></div>
   <section class="summary"><div><span class="chip ${c.percent>=100?'good':'warn'}">证据完整度 ${c.percent}%</span><p class="summary-line">${esc(conclusion)}</p></div><div class="summary-meta"><span>边界</span><p class="muted">时点快照，不复测、不推断趋势、不做平台排名。</p></div></section>
   <div class="ov-grid ov-2">
    <section class="card hero"><p class="eyebrow">核心指标</p><div class="hero-main"><span class="muted">非品牌题自然提及率</span><strong>${mentionRate}%</strong><span class="muted">${k.mentions} 次提及 / ${k.non_brand_success} 条有效回答</span></div><div class="hero-grid"><div><span>明确推荐</span><strong>${k.recommendations}</strong></div><div><span>官方来源覆盖</span><strong>${citedYes}/${k.planned}</strong>${bar(citedYes,k.planned)}</div><div><span>引用记录</span><strong>${k.citations}</strong></div><div><span>任务完成</span><strong>${k.success}/${k.planned}</strong>${bar(k.success,k.planned)}</div></div></section>
    <section class="card"><div class="card-head"><div><h2>证据完整度</h2><p>有完整证据的终态任务占比</p></div></div><div class="score-body"><div class="donut" style="background:conic-gradient(#176b88 0 ${c.percent}%,#dfecef ${c.percent}% 100%)"><div class="score-value"><strong>${c.percent}%</strong><small>${c.complete}/${c.planned}</small></div></div><div class="score-copy"><strong>${c.percent>=100?'本批次证据齐全':(c.percent>=80?'基本齐全，有少量缺口':'缺口较多，报告不完整')}</strong><ul class="fact-list">${(o.incomplete||[]).map(x=>`<li>${esc(x)}</li>`).join('')||'<li>全部任务均有回答与截图证据</li>'}</ul></div></div></section>
   </div>
   <div class="ov-grid ov-2">
    <section class="card"><div class="card-head"><div><h2>${isTrend?'批次趋势':'问题维度分布'}</h2><p>${isTrend?'按批次对比官方来源引用与自然提及':'每个问题的有效回答与官方来源引用；第二次诊断后这里显示变化趋势'}</p></div></div><div class="chart-wrap">${chartBody}</div></section>
    <section class="card"><div class="card-head"><div><h2>平台对比</h2><p>成功/失败与提及、引用</p></div></div><div class="table-scroll"><table><thead><tr><th>平台</th><th>成功</th><th>失败</th><th>提及</th><th>引用</th></tr></thead><tbody>${(o.platforms||[]).map(x=>`<tr><td>${esc(x.label)}</td><td>${x.success}</td><td>${x.failed}</td><td>${x.non_brand_mention}</td><td>${x.official_citation_yes}</td></tr>`).join('')||'<tr><td colspan="5">暂无</td></tr>'}</tbody></table></div></section>
   </div>
   <div class="ov-grid ov-2">
    <section class="card"><div class="card-head"><div><h2>引用来源分布</h2><p>本次诊断引用到的域名，最多 8 个</p></div></div><div class="bars">${(o.domains||[]).map(x=>`<div class="bar-row"><div><div class="bar-name">${esc(x[0])}</div>${bar(x[1],domMax)}</div><div class="bar-count">${x[1]}</div></div>`).join('')||'<p class="empty">本批次没有抽取到来源链接</p>'}</div></section>
    <section class="card"><div class="card-head"><div><h2>报告边界</h2><p>来自诊断脚本的声明</p></div></div><ul class="fact-list">${(o.limits||[]).map(x=>`<li>${esc(x)}</li>`).join('')||'<li>无</li>'}</ul></section>
   </div>`;
   return;
  }
  if(tab==='profile') {
   const fields=[['canonical_name','主体名称',p.canonical_name||d.project.name],['business','主营业务',p.business],['region','服务区域',p.region],['audience','目标客户',p.audience]];
   v.innerHTML=`<form id="profile-form" class="panel"><div class="form-grid">${fields.map(([k,l,value])=>`<label>${l}<input name="${k}" value="${esc(value)}" required></label>`).join('')}<label>别名（每行一个）<textarea name="aliases">${esc((p.aliases||[]).join('\n'))}</textarea></label><label>官方链接（每行一个）<textarea name="official_pages">${esc((p.official_pages||[]).join('\n'))}</textarea></label></div><label class="check"><input name="no_official_web_presence" type="checkbox" ${p.no_official_web_presence?'checked':''}>暂无官方页面</label><button class="primary" type="submit">保存并配置诊断</button></form>`;
   $('#profile-form').onsubmit=async e=>{e.preventDefault();const f=new FormData(e.target),profile=Object.fromEntries(f);profile.aliases=String(profile.aliases||'').split('\n').map(s=>s.trim()).filter(Boolean);profile.official_pages=String(profile.official_pages||'').split('\n').map(s=>s.trim()).filter(Boolean);profile.no_official_web_presence=f.has('no_official_web_presence');try{await api(base+'/profile','PUT',{profile,questions:d.questions,platforms:d.platforms,revision:d.revision});location.hash=link('projects',slug,'config');}catch(err){notice(err.message);}};
  } else if(tab==='config') {
   const options=await api('/api/platforms');if(token!==epoch)return;
   v.innerHTML=`<form id="config-form" class="panel"><h2>诊断平台</h2><div class="toolbar">${options.diagnosis.map(x=>`<label class="check"><input type="checkbox" name="platform" value="${x.id}" ${d.platforms.includes(x.id)?'checked':''}>${esc(x.label)}</label>`).join('')}</div><div class="page-head"><h2>诊断问题</h2><button type="button" id="suggest">根据主体资料生成</button></div><div id="questions"></div><div class="toolbar"><button type="submit">保存配置</button><button type="button" id="freeze" class="primary">确认并冻结</button></div></form>`;
   let qs=d.questions,revision=d.revision;
   const paint=()=>{$('#questions').innerHTML=qs.length?qs.map(q=>`<label class="question"><span>${esc(q.id)}</span><textarea data-question="${esc(q.id)}" required>${esc(q.prompt)}</textarea></label>`).join(''):'<p class="empty">先生成问题，再逐题检查和修改</p>';};paint();
   $('#suggest').onclick=async()=>{try{qs=(await api(base+'/question-suggestions','POST',{})).questions;paint();}catch(err){notice(err.message);}};
   const save=async()=>{qs=qs.map(q=>({...q,prompt:document.querySelector(`[data-question="${q.id}"]`).value}));const platforms=[...document.querySelectorAll('[name="platform"]:checked')].map(e=>e.value);const saved=await api(base+'/profile','PUT',{profile:d.profile,questions:qs,platforms,revision});revision=saved.revision;};
   $('#config-form').onsubmit=async e=>{e.preventDefault();try{await save();notice('已保存');}catch(err){notice(err.message);}};
   $('#freeze').onclick=()=>modal('确认诊断配置','<p>确认当前问题与平台。冻结后本批次不再修改，每题每平台只提交一次。</p>',async()=>{await save();await api(base+'/runs','POST',{confirm:true,revision});location.hash=link('projects',slug,'runs');},'确认冻结');
  } else {
   if(!d.runs.length){v.innerHTML='<section class="panel"><p class="empty">尚未创建诊断批次</p><a class="button primary" href="'+link('projects',slug,'config')+'">配置诊断</a></section>';return;}
   const selected=params.get('run')||d.runs[0].id,r=await api(base+'/runs/'+encodeURIComponent(selected));if(token!==epoch)return;
   const operations=r.status==='ready'?[['execute','开始诊断'],['close-browser','关闭诊断浏览器']]:r.status==='login'?[['resume','已完成登录'],['close-browser','关闭诊断浏览器'],['stop','停止']]:r.status==='running'?[['resume','已处理验证'],['stop','停止']]:r.status==='preflight'?[['stop','停止']]:['frozen','blocked','interrupted'].includes(r.status)?[['preflight','检查环境'],['login','打开登录窗口'],['close-browser','关闭诊断浏览器']]:[];
   if(['frozen','blocked','interrupted','ready'].includes(r.status))operations.push(['archive','终止本批次']);
   if(r.status==='interrupted' && r.tasks.length && r.tasks.every(t=>['success','failed'].includes(t.status)))operations.push(['report','重新生成报告']);
   v.innerHTML=`<section class="panel"><div class="toolbar"><select id="batch" aria-label="诊断批次">${d.runs.map(x=>`<option value="${esc(x.id)}" ${x.id===selected?'selected':''}>${esc(localTime(x.created_at))} · ${esc(states[x.status]||x.status)}</option>`).join('')}</select>${badge(r.status)}${operations.map(([k,l])=>`<button data-op="${k}" class="${k==='execute'?'primary':''}">${l}</button>`).join('')}</div>${r.paused?`<div class="panel" style="margin:0 0 18px;background:var(--orange2);border-color:#f0d5c3"><strong>平台要求人工验证，执行已暂停</strong><p class="muted" style="margin:8px 0">待处理：${esc((r.waiting_tasks||[]).join('、'))}。请在已打开的浏览器窗口完成登录或验证码，然后点击“已处理验证”继续。系统不会重新提交本题问题。</p></div>`:''}${r.blocker?`<p class="muted">${esc(r.blocker)}</p>`:''}${(r.preflight||[]).length?`<p class="muted">平台检查：${r.preflight.map(c=>`${esc(c.platform)} ${c.check==='OK'?(c.login_state_guess==='logged_in'?'已登录':'未登录'):'打不开'}`).join(' · ')}</p>`:''}<p>成功 ${r.state.success||0} · 失败 ${r.state.failed||0} · 待执行 ${r.state.pending||0}</p><div class="table-scroll"><table><thead><tr><th>任务</th><th>平台</th><th>状态</th><th>结果</th></tr></thead><tbody>${r.tasks.map(t=>`<tr><td><strong>${esc(t.question_id)}</strong><div class="muted" style="max-width:480px;margin-top:5px">${esc(t.prompt)}</div></td><td>${esc(t.platform_label)}</td><td>${badge(t.status)}</td><td>${r.observations.some(o=>o.task_id===t.task_id)?`<button data-result="${esc(t.task_id)}">查看回答与证据</button>`:esc(t.failure_reason||'')}</td></tr>`).join('')}</tbody></table></div></section>`;
   if(['completed','degraded'].includes(r.status)){v.insertAdjacentHTML('afterbegin','<div class="toolbar"><button id="derive" class="primary">生成改善任务</button></div>');$('#derive').onclick=async()=>{try{const out=await api(base+'/runs/'+selected+'/improvements/derive','POST',{});notice(out.created?`已生成 ${out.created} 条改善任务`:'没有新的可生成项（仅取有证据的成功观测）');location.hash=link('actions',slug);}catch(err){notice(err.message);}};}
   $('#batch').onchange=e=>{location.hash=link('projects',slug,'runs')+'&run='+encodeURIComponent(e.target.value);};
   document.querySelectorAll('[data-op]').forEach(b=>b.onclick=()=>modal(b.textContent,b.dataset.op==='execute'?'<p>将向所选平台提交冻结问题。请确认主体资料可用于本次诊断。</p>':'<p>确认执行此操作？</p>',()=>api(base+'/runs/'+selected+'/'+b.dataset.op,'POST',{confirm:true}),b.textContent));
   document.querySelectorAll('[data-result]').forEach(b=>b.onclick=()=>{const o=r.observations.find(x=>x.task_id===b.dataset.result);const panel=document.createElement('section');panel.className='panel';panel.id='observation-detail';$('#observation-detail')?.remove();panel.innerHTML=`<div class="page-head"><h2>${esc(o.task_id)} 回答与证据</h2><button id="close-evidence">收起</button></div><pre>${esc(o.response_text||o.failure_reason||'无回答')}</pre>${(o.evidence_files||[]).map(f=>{const url=API+base+'/runs/'+selected+'/files/'+f.split('/').map(encodeURIComponent).join('/');return /\.(png|jpg|jpeg|webp)$/i.test(f)?`<figure><img style="max-width:100%" src="${esc(url)}" alt="${esc(f)}"><figcaption>${esc(f)}</figcaption></figure>`:`<a class="button" href="${esc(url)}" download>下载 ${esc(f)}</a>`;}).join('')}`;v.append(panel);$('#close-evidence').onclick=()=>panel.remove();panel.scrollIntoView({block:'start'});});
   if(['running','preflight','login'].includes(r.status)){const poll=()=>{if(token!==epoch)return;if($('#modal').open){timer=setTimeout(poll,2000);return;}render();};timer=setTimeout(poll,5000);}
  }
 } else if(view==='settings') {
  const d=(await api('/api/settings')).settings;show(head('设置')+`<form id="settings-form" class="panel"><label>工作空间名称<input name="workspace_label" required value="${esc(d.workspace_label)}"></label><label>默认操作人<input name="default_operator" value="${esc(d.default_operator)}"></label><button class="primary">保存</button></form>`,token);
  if(token!==epoch)return;$('#settings-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/settings','PUT',Object.fromEntries(new FormData(e.target)));notice('已保存');}catch(err){notice(err.message);}};
 } else if(['actions','content','publications','reports'].includes(view)) {
  const projects=(await api('/api/projects')).projects;
  const chosen=projects.find(p=>p.slug===context);
  show(head(labels[view])+`<div class="toolbar"><select id="scope" aria-label="当前项目"><option value="">选择项目</option>${projects.map(p=>`<option value="${p.slug}" ${p.slug===chosen?.slug?'selected':''}>${esc(p.name)}</option>`).join('')}</select>${!projects.length?'<button id="create">创建项目</button>':''}</div><div id="module"></div>`,token);
  if(token!==epoch)return;$('#scope').onchange=e=>{sessionStorage.setItem('geo-project',e.target.value);location.hash=link(view,e.target.value);};if($('#create'))$('#create').onclick=createProject;
  if(!chosen){$('#module').innerHTML='<p class="empty">请选择需要处理的项目</p>';return;}
  $('#project-context').textContent=chosen.name;const base='/api/projects/'+chosen.slug;
  if(view==='reports') {
   const items=(await api(base+'/reports')).reports;if(token!==epoch)return;
   $('#module').innerHTML=`<section class="panel">${items.map((r,i)=>`<div class="row"><span>${esc(r.name)} <small>${esc(r.run_id)}</small></span><button data-report="${i}">查看</button></div>`).join('')||'<p class="empty">尚无报告</p>'}</section><section id="report-text" hidden class="panel"></section>`;
   document.querySelectorAll('[data-report]').forEach(b=>b.onclick=async()=>{try{const r=items[Number(b.dataset.report)],d=await api(base+'/runs/'+r.run_id+'/text/report/'+encodeURIComponent(r.name));$('#report-text').hidden=false;$('#report-text').className='panel report';$('#report-text').innerHTML=`<h2>${esc(r.name)}</h2>`+md(d.text);}catch(err){notice(err.message);}});
   const wantRun=params.get('run');
   if(wantRun){const i=items.findIndex(r=>r.run_id===wantRun&&r.name==='diagnosis.md');if(i>=0)document.querySelector(`[data-report="${i}"]`)?.click();}
  } else {
   const items=(await api(base+'/editorial/'+view)).items;if(token!==epoch)return;
   if(view==='actions') {
    $('#module').innerHTML=`<section class="panel">${items.map(x=>`<div class="row"><div><strong>${esc(x.title)}</strong><p class="muted">${esc(x.description)}</p><a href="${link('projects',chosen.slug,'runs')+'&run='+x.run_id}">查看诊断依据</a></div><button data-create-asset="${x.id}">编写内容</button></div>`).join('')||'<p class="empty">暂无改善任务。完成诊断后可从结果生成。</p>'}</section>`;
    document.querySelectorAll('[data-create-asset]').forEach(b=>b.onclick=async()=>{try{const x=await api(base+'/actions/'+b.dataset.createAsset+'/content','POST',{});location.hash=link('content',chosen.slug)+'&asset='+x.id;}catch(err){notice(err.message);}});
   } else if(view==='content') {
    const asset=items.find(x=>x.id===params.get('asset'));
    if(asset){
     $('#module').innerHTML=`<form id="asset-form" class="panel"><div class="toolbar"><a class="button" href="${link('content',chosen.slug)}">返回内容列表</a>${badge(asset.status==='approved'?'已审核':'草稿')}<span class="muted">${asset.source==='manual'?'直接创建':'来自诊断'}</span></div><label>标题<input name="title" required value="${esc(asset.title)}"></label><label>正文<textarea name="body" style="min-height:280px">${esc(asset.body)}</textarea></label><label>事实依据与来源<textarea name="facts">${esc(asset.facts)}</textarea></label><div class="toolbar"><button class="primary">保存草稿</button><button type="button" id="review">审核当前版本</button><a class="button" href="${link('publications',chosen.slug)}">发布安排</a></div></form>`;
     $('#asset-form').onsubmit=async e=>{e.preventDefault();try{await api(base+'/content/'+asset.id,'PUT',{...Object.fromEntries(new FormData(e.target)),revision:asset.revision});await render();notice('已保存，需重新审核');}catch(err){notice(err.message);}};
     $('#review').onclick=()=>{const f=new FormData($('#asset-form'));if(['title','body','facts'].some(k=>f.get(k)!==asset[k])){notice('请先保存修改再审核');return;}modal('审核内容','<label>审核人<input name="reviewer" required></label><label class="check"><input type="checkbox" required>已核对正文与事实依据</label>',async data=>{await api(base+'/content/'+asset.id+'/review','POST',{reviewer:data.get('reviewer'),revision:asset.revision,confirm:true});},'审核通过');};
    }else{
     $('#module').innerHTML=`<div class="toolbar"><button id="direct" class="primary">＋ 直接生成内容</button><span class="muted">不需要诊断结果，可直接创建内容草稿</span></div><section class="panel">${items.map(x=>`<a class="row" href="${link('content',chosen.slug)+'&asset='+x.id}"><span>${esc(x.title)}<small>${x.source==='manual'?'直接创建':'来自诊断'}</small></span>${badge(x.status==='approved'?'已审核':'草稿')}</a>`).join('')||'<p class="empty">暂无内容。可以直接创建，也可以先完成诊断再从改善任务生成。</p>'}</section>`;
     $('#direct').onclick=()=>modal('直接生成内容','<label>内容标题<input name="title" required maxlength="200" autofocus placeholder="例如：设备资产管理系统选型指南"></label><label>写作要点（每行一个，会生成小节标题）<textarea name="brief" placeholder="这个行业常见的问题\n选型要看的指标\n可核查的案例"></textarea></label><label>用途<input name="channel" placeholder="例如：公众号长文 / 官网页 / 知乎回答"></label><p class="muted">这里只生成结构大纲，正文与事实依据需要你填写；未补齐“待补充”段落不能通过审核。</p>',async data=>{const r=await api(base+'/content','POST',Object.fromEntries(data));location.hash=link('content',chosen.slug)+'&asset='+r.id;},'创建草稿');
    }
   } else {
    const caps=(await api('/api/platforms')).publication;if(token!==epoch)return;
    const loginState=await api('/api/platforms/login-state');if(token!==epoch)return;
    const assets=(await api(base+'/editorial/content')).items;if(token!==epoch)return;
    const approved=assets.filter(x=>x.status==='approved'),drafts=assets.filter(x=>x.status!=='approved');
    const names=Object.fromEntries(caps.map(p=>[p.id,p])),elem={title:'标题',body:'正文',facts:'事实依据',cover:'封面图'};
    const browserIds=caps.filter(p=>p.mode==='browser').map(p=>p.id);
    const st={draft_created:['已写入草稿箱','warn'],submitted:['已提交','good'],manual_required:['需人工发布','warn'],failed:['失败','bad'],published_manual:['人工已回填','good']};
    const canPublish=approved.length>0;
    const needsCover=id=>((names[id]||{}).requires||[]).includes('cover');
    const wechat=caps.find(p=>p.id==='wechat_mp')||{};
    const wechatReady=(wechat.credentials||[]).every(c=>c.configured);
    const browserCaps=caps.filter(p=>p.mode==='browser');
    const loginRows=d=>browserCaps.map(p=>{const s=(d.states||{})[p.id]||{};return `<tr><td>${esc(p.label)}</td><td><span class="badge ${esc(s.tone||'')}">${esc(s.label||'未检测')}</span></td><td class="muted">${esc(localTime(s.checked_at)||'—')}</td><td class="muted">${esc(s.note||'')}</td><td><button data-login-one="${esc(p.id)}">只开这一个</button></td></tr>`;}).join('');
    const loginSummary=d=>{const s=d.session||{};if(s.active)return '登录窗口已打开，正在自动识别登录态（每 3 秒刷新，不用手动点）…';return s.message||'还没有检测过登录态：点「打开登录窗口」登录，或点「重新检测」。';};
    const known={};Object.entries(loginState.states||{}).forEach(([k,v])=>{known[k]=v.state;});
    $('#module').innerHTML=`
     <section class="panel"><div class="card-head"><div><h2>确认后直接发布</h2><p>勾选内容与平台 → 「确认发布」→ 逐平台执行：公众号走官方 API 写入草稿箱，其余平台走浏览器自动化</p></div><div class="toolbar" style="margin:0"><button id="publish" class="primary" ${canPublish?'':'disabled'}>确认发布</button></div></div>
      <div class="steps">
       <div class="step ${canPublish?'done':'current'}"><strong><em>1</em>选择已审核内容</strong><span>${approved.length?`已审核 ${approved.length} 条${drafts.length?`，另有 ${drafts.length} 条未审核`:''}`:(drafts.length?`${drafts.length} 条内容还没通过审核`:'还没有内容')}</span></div>
       <div class="step ${canPublish?'current':''}"><strong><em>2</em>勾选发布平台</strong><span>${canPublish?'公众号可用官方接口，其余走浏览器自动化':'需要先有已审核内容'}</span></div>
       <div class="step ${items.length?'current':''}"><strong><em>3</em>查看结果</strong><span>${items.length?'下方发布记录显示每个平台的结果与原因':'发布后在这里查看每个平台结果'}</span></div>
      </div></section>
     ${canPublish?'':`<section class="panel"><h2>先准备一条已审核内容</h2><p class="muted">只能发布当前版本审核通过的内容。${drafts.length?`当前有 ${drafts.length} 条草稿待审核。`:'当前还没有内容。'}</p><div class="toolbar"><a class="button primary" href="${link('content',chosen.slug)}">去内容生产</a><a class="button" href="${link('actions',chosen.slug)}">从改善任务开始</a></div></section>`}
     <section class="panel" id="login-panel"><div class="card-head"><div><h2>平台登录状态</h2><p>点「打开登录窗口」→ 在弹出的窗口里自己登录 → 登录成功会自动识别；全部识别到已登录后窗口自动关闭</p></div><div class="toolbar" style="margin:0"><button id="probe-login">重新检测</button><button id="login" class="primary">打开登录窗口</button></div></div><p class="muted" id="login-msg">${esc(loginSummary(loginState))}</p><div class="table-scroll"><table><thead><tr><th>平台</th><th>登录状态</th><th>最近识别</th><th>说明</th><th>操作</th></tr></thead><tbody id="login-rows">${loginRows(loginState)}</tbody></table></div><p class="muted" style="margin:12px 0 0">登录态存在本机隔离浏览器 profile 里，不写数据库、不导出 cookie；系统只读页面判断状态，不代填账号密码。</p></section>
     <section class="panel"><div class="card-head"><div><h2>选择内容</h2><p>只列出当前版本审核通过的内容</p></div></div><div style="padding-top:4px">${approved.map(a=>`<label class="check" style="padding:10px 0;border-bottom:1px solid #edf0f1"><input type="checkbox" name="pick-asset" value="${a.id}">${esc(a.title)}</label>`).join('')||'<p class="empty">没有已审核内容</p>'}</div></section>
     <section class="panel"><div class="card-head"><div><h2>选择平台</h2><p>每个平台的执行方式与前置条件</p></div></div><div class="table-scroll" style="padding-top:6px"><table><thead><tr><th>选择</th><th>平台</th><th>执行方式</th><th>前置条件</th></tr></thead><tbody>${caps.map(p=>`<tr><td><input type="checkbox" name="pick-platform" value="${p.id}"></td><td>${esc(p.label)}</td><td>${esc(p.mode_label)}</td><td class="muted">${esc(p.mode==='api'?(p.credentials.every(c=>c.configured)?'凭据已配置':'需先配置 AppID/AppSecret'):(p.mode==='browser'?'需在隔离浏览器登录该平台':'无自动接口，发布后回填链接'))}${needsCover(p.id)?' · 需要封面图':''}</td></tr>`).join('')}</tbody></table></div></section>
     <section class="panel"><div class="card-head"><div><h2>发布记录</h2><p>每个内容 × 平台的结果、原因与证据</p></div></div><div style="padding-top:6px">${items.map(x=>`<div class="row"><div><strong>${esc(x.title_snapshot)}</strong><small>${esc((names[x.platform]||{}).label||x.platform)} · ${esc((names[x.platform]||{}).mode_label||x.mode||'')} · <span class="badge ${(st[x.status]||[])[1]||''}">${esc((st[x.status]||[x.status])[0])}</span></small>${x.adapter_note?`<small class="muted">${esc(x.adapter_note)}</small>`:''}${x.receipt_url?`<small>${esc(x.receipt_url)}</small>`:''}</div><div class="toolbar" style="margin:0">${['manual_required','failed'].includes(x.status)?`<button data-receipt="${x.id}">回填发布链接</button>`:''}</div></div>`).join('')||'<p class="empty">还没有发布记录。勾选内容和平台后点「确认发布」。</p>'}</div></section>
     <details class="panel"><summary style="cursor:pointer"><strong>平台接入与凭据（${caps.length} 个）</strong> <span class="muted">公众号可填 AppID/AppSecret；其余平台用浏览器登录</span></summary><div class="table-scroll" style="padding-top:12px"><table><thead><tr><th>平台</th><th>执行方式</th><th>接口状态</th><th>内容要素</th><th>操作</th></tr></thead><tbody>${caps.map(p=>`<tr><td>${esc(p.label)}</td><td>${esc(p.mode_label)}</td><td>${p.mode==='manual'?'<span class="badge warn">人工</span>':(p.can_attempt?'<span class="badge good">可执行</span>':'<span class="badge warn">待配置</span>')}</td><td>${p.requires.map(r=>esc(elem[r]||r)).join('、')}</td><td>${p.id==='wechat_mp'?`<button data-cred="${p.id}">${wechatReady?'修改凭据':'填写凭据'}</button> <button data-checkcred="${p.id}">校验</button>`:(p.mode==='browser'?'<a class="button" href="#" data-login="1">登录窗口</a>':'—')}</td></tr>`).join('')}</tbody></table></div><p class="muted" style="margin:12px 0 0">${esc(wechat.entry||'')}${wechat.note?' · '+esc(wechat.note):''}</p></details>`;
    $('#publish').onclick=async()=>{if(!canPublish){notice('还没有已审核内容，请先在内容生产里审核');return;}
      const picked=[...document.querySelectorAll('[name="pick-asset"]:checked')].map(e=>e.value);
      const plats=[...document.querySelectorAll('[name="pick-platform"]:checked')].map(e=>e.value);
      if(!picked.length){notice('请先勾选要发布的内容');return;}
      if(!plats.length){notice('请先勾选发布平台');return;}
      const titleOf=id=>{const a=assets.find(x=>x.id===id);return a?a.title:id;};
      const lines=[];let warns=[];
      picked.forEach(id=>plats.forEach(pid=>{
        const p=names[pid]||{label:pid,mode:'manual'};
        let action=p.mode==='api'?'调用官方接口写入公众号草稿箱':(p.mode==='browser'?'浏览器自动化创建':'人工发布（回填链接）');
        if(needsCover(pid)){action='缺 封面图，将转人工';warns.push(`${p.label} 需要封面图`);}
        if(p.mode==='api'&&!(p.credentials||[]).every(c=>c.configured)){action='未配置凭据，将转人工';warns.push(`${p.label} 未配置 AppID/AppSecret`);}
        lines.push(`${esc(titleOf(id))} → ${esc(p.label)}：${action}`);
      }));
      modal('确认发布','<p class="muted">确认后立即执行，不再有“创建任务”这一步。</p><ul class="fact-list" style="margin:10px 0">'+lines.map(l=>`<li>${l}</li>`).join('')+'</ul>'+(warns.length?`<p class="muted">注意：${esc([...new Set(warns)].join('；'))}</p>`:'')+'<label class="check"><input type="checkbox" required>确认内容已审核，同意按上述方式发布</label>',async()=>{const r=await api(base+'/publish','POST',{asset_ids:picked,platforms:plats,confirm:true,operator:''});const ok=r.results.filter(x=>['draft_created','submitted'].includes(x.status)).length;notice(`已执行 ${r.results.length} 条：成功 ${ok} 条，需人工 ${r.results.filter(x=>x.status==='manual_required').length} 条，失败 ${r.results.filter(x=>x.status==='failed').length} 条`);},'确认发布');
    };
    let loginTimer=null;
    const paintLogin=d=>{
      const rows=$('#login-rows');if(rows)rows.innerHTML=loginRows(d);
      const msg=$('#login-msg');if(msg)msg.textContent=loginSummary(d);
      document.querySelectorAll('[data-login-one]').forEach(b=>b.onclick=()=>openLogin([b.dataset.loginOne]));
    };
    const watchLogin=async()=>{
      if(token!==epoch)return;
      try{
        const d=await api('/api/platforms/login-state');if(token!==epoch)return;
        const flipped=Object.keys(d.states||{}).filter(k=>d.states[k].state==='logged_in'&&known[k]!==undefined&&known[k]!=='logged_in');
        Object.keys(d.states||{}).forEach(k=>{known[k]=d.states[k].state;});
        paintLogin(d);
        const s=d.session||{};
        if(flipped.length)notice(`已识别登录成功：${flipped.map(k=>(names[k]||{}).label||k).join('、')}`);
        else if(!s.active&&s.result==='completed'&&s.message)notice(s.message);
        loginTimer=s.active?setTimeout(watchLogin,3000):null;
      }catch(e){loginTimer=setTimeout(watchLogin,5000);}
    };
    const openLogin=async ids=>{
      if(loginTimer){clearTimeout(loginTimer);loginTimer=null;}
      try{const d=await api('/api/platforms/login','POST',{platforms:ids});if(token!==epoch)return;paintLogin(d);notice(d.message||'已打开登录窗口');}catch(e){notice(e.message);return;}
      loginTimer=setTimeout(watchLogin,2000);
    };
    paintLogin(loginState);
    $('#login').onclick=()=>openLogin(browserIds);
    $('#probe-login').onclick=async()=>{try{const d=await api('/api/platforms/login-state/probe','POST',{platforms:browserIds});if(token!==epoch)return;paintLogin(d);const s=d.summary||{};notice(`重新检测完成：已登录 ${s.logged_in||0}，需登录 ${s.needs_login||0}，未检测 ${s.unknown||0}`);}catch(e){notice(e.message);}};
    document.querySelectorAll('[data-login]').forEach(el=>el.onclick=e=>{e.preventDefault();openLogin(browserIds);});
    if((loginState.session||{}).active)loginTimer=setTimeout(watchLogin,2000);
    document.querySelectorAll('[data-cred]').forEach(b=>b.onclick=()=>modal('配置公众号凭据','<p class="muted">只保存在本机数据库，接口不会回显明文。需要该公众号已认证并开通草稿接口权限，且本机公网 IP 在白名单内。</p><label>AppID<input name="appid" required placeholder="wx开头的 AppID"></label><label>AppSecret<input name="secret" type="password" required></label>',async f=>{const r=await api('/api/platforms/'+b.dataset.cred+'/credentials','PUT',Object.fromEntries(f));notice(r.message);},'保存'));
    document.querySelectorAll('[data-checkcred]').forEach(b=>b.onclick=async()=>{try{const r=await api('/api/platforms/'+b.dataset.checkcred+'/credentials/check','POST',{});notice(r.message||'凭据可用');}catch(e){notice(e.message);}});
    document.querySelectorAll('[data-receipt]').forEach(b=>b.onclick=()=>modal('回填发布链接','<p class="muted">先去该平台完成发布，再把页面链接回填到这里。</p><label>发布链接<input name="url" type="url" required></label><label>操作人<input name="operator" required></label><label class="check"><input type="checkbox" required>确认已实际发布</label>',f=>api(base+'/publishing/'+b.dataset.receipt+'/receipt','POST',{...Object.fromEntries(f),confirm:true}),'保存回执'));
   }
  }
 } else if(view==='platforms') {
  const d=await api('/api/platforms');if(token!==epoch)return;
  const login=await api('/api/platforms/login-state');if(token!==epoch)return;
  show(head('模型与平台')+`<section class="panel"><h2>诊断平台</h2>${d.diagnosis.map(p=>`<div class="row"><span>${esc(p.label)}</span><span class="muted">浏览器登录后执行</span></div>`).join('')}</section><section class="panel"><h2>发布平台</h2>${d.publication.map(p=>{const s=(login.states||{})[p.id];return `<div class="row"><span>${esc(p.label)}</span><span class="muted">${esc(p.mode_label)}${p.mode==='api'?' · 需配置 AppID/AppSecret':''}${s?' · 登录态 '+esc(s.label):''}</span></div>`;}).join('')}</section>`,token);
 } else {show(head(labels[view]||'页面')+'<section class="panel"><p class="muted">此功能正在重构，暂不可操作。</p></section>',token);}
 } catch(err) {if(token===epoch){notice(err.message);$('#view').innerHTML='<button id="retry">重新加载</button>';$('#retry').onclick=render;}}
}
window.addEventListener('hashchange',render);render();
})();
