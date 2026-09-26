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
const labels = {workbench:'工作台',projects:'诊断项目',actions:'改善任务',content:'内容生产',library:'内容库',publications:'批量发布',reports:'报告中心',platforms:'模型与平台',settings:'设置'};
const pageIntro = {
 '工作台':'集中查看待处理事项和最近活动。',
 '诊断项目':'创建并管理 GEO 诊断项目。',
 '改善任务':'把诊断结论转成可执行的改善任务。',
 '内容生产':'确认写作任务书与素材，生成初稿。',
 '内容库':'查看、编辑与审核所有生成的初稿。',
 '批量发布':'把已审核的内容一次投到多个平台。',
 '报告中心':'查看诊断报告、数据质量和原始数据。',
 '模型与平台':'查看诊断模型与发布平台的接入方式。',
 '设置':'设置工作空间名称和默认操作人。'
};
const states = {archived:'已终止',degraded:'报告不完整',login:'等待登录',frozen:'待检查',preflight:'检查中',ready:'可执行',running:'执行中',paused:'等待人工处理',blocked:'需处理',interrupted:'已中断',completed:'已完成',pending:'待执行',success:'成功',failed:'失败',manual_required:'需人工处理',todo:'待办',doing:'进行中'};
let epoch = 0, timer = null;
// 批量发布的勾选与页签指示条几何：跨重渲染、跨轮询都要保住
let pubPicked = [], pubPlats = [], pubTabGeo = null;
// 发布记录是否只看待处理（筛选按钮的真状态，跨重渲染保留）
let recOnlyPending = false;
const route = () => { const [rawView='', query=''] = location.hash.slice(1).split('?'); return {view:rawView||'workbench', params:new URLSearchParams(query)}; };
const link = (view, project, tab) => '#'+view+(project?'?project='+encodeURIComponent(project)+(tab?'&tab='+tab:''):'');
async function api(path, method='GET', body) { const abort=new AbortController(),deadline=setTimeout(()=>abort.abort(),20000);try{const r=await fetch(API+path,{method,signal:abort.signal,headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})}); const d=await r.json().catch(()=>({})); if(!r.ok) throw Error(typeof d.detail==='string'?d.detail:'提交信息有误，请检查填写内容'); return d;}catch(e){if(e.name==='AbortError')throw Error('请求超时，请刷新核对是否已保存，避免重复提交');if(e instanceof TypeError)throw Error('服务连接失败，请确认本地服务已启动后重新加载');throw e;}finally{clearTimeout(deadline);} }
// 上传不能自己设 Content-Type，否则 multipart 的 boundary 会丢。
async function apiUpload(path, formData) { const abort=new AbortController(),deadline=setTimeout(()=>abort.abort(),180000);try{const r=await fetch(API+path,{method:'POST',body:formData,signal:abort.signal}); const d=await r.json().catch(()=>({})); if(!r.ok) throw Error(typeof d.detail==='string'?d.detail:'上传失败，请检查文件后重试'); return d;}catch(e){if(e.name==='AbortError')throw Error('上传超时，请改用更小的文件或稍后重试');if(e instanceof TypeError)throw Error('服务连接失败，请确认本地服务已启动后重新加载');throw e;}finally{clearTimeout(deadline);} }
function notice(text) { $('#notice').textContent=text; $('#notice').hidden=!text; }
function head(title, action='', subtitle=pageIntro[title]||'') {
 return `<div class="page-lead"><div><h1>${esc(title)}</h1>${subtitle?`<p>${esc(subtitle)}</p>`:''}</div>${action?`<div class="page-actions">${action}</div>`:''}</div>`;
}
function show(html, token) { if(token===epoch) $('#view').innerHTML=html; }
const TONES={good:['completed','success','approved','已审核'],bad:['failed','interrupted','blocked'],warn:['pending','todo','frozen','manual_required','login','degraded']};
const tone=s=>Object.keys(TONES).find(k=>TONES[k].includes(s))||'';
function badge(state) { return `<span class="badge ${tone(state)}">${esc(states[state]||state)}</span>`; }
const TAB_LABEL={overview:'概览',profile:'主体资料',config:'诊断配置',runs:'执行与结果'};
// 面包屑：项目上下文的页面可以一步回到项目列表或项目概览；最后一段不带链接，带 aria-current
function crumbHTML(view,project,tab){
 // 每一页都从同一个根节点开始，避免顶层页只有粗体当前项、项目页却从灰色链接开始。
 const seg=[['工作空间',link('workbench')]];
 if(project){
  const onOverview=view==='projects'&&(!tab||tab==='overview');
  // 标签不用「诊断项目」：那和侧边导航同名，会让 get_by_role('link', name='诊断项目') 撞到两个元素。
  // 这一段去的是项目列表，用「项目列表」既准确又不重名。
  seg.push(['项目列表',link('projects')]);
  seg.push([project.name,onOverview?null:link('projects',project.slug)]);
  seg.push([view==='projects'?(TAB_LABEL[tab||'overview']||'概览'):(labels[view]||view),null]);
 }else{
  seg.push([labels[view]||'工作台',null]);
 }
 return seg.map(([text,href],i)=>{
  if(i===seg.length-1)return `<span aria-current="page">${esc(text)}</span>`;
  return (href?`<a href="${href}">${esc(text)}</a>`:`<span>${esc(text)}</span>`)+'<span class="crumb-sep">/</span>';
 }).join('');
}
function modal(title, body, submit, button='确定', cancel='取消') {
 const d=$('#modal'); $('#modal-title').textContent=title; $('#modal-body').innerHTML=body; $('#modal-error').textContent=''; $('#confirm-modal').textContent=button; $('#cancel-modal').textContent=cancel;
 $('#modal-form').onsubmit=async e=>{e.preventDefault(); const b=$('#confirm-modal');b.disabled=true;try{const result=await submit(new FormData(e.target));d.close();await render();if(result&&result.message)notice(result.message);}catch(err){$('#modal-error').textContent=err.message;}finally{b.disabled=false;}}; d.showModal();
}
$('#close-modal').onclick=$('#cancel-modal').onclick=()=>$('#modal').close();

// 内容生成模型配置（DeepSeek 等 OpenAI 兼容端点）。密钥只存在本机数据库，不回显明文。
async function openContentLlm(){
 let st={};
 try{st=await api('/api/content-llm');}catch(e){notice(e.message);return;}
 const presets=st.presets||[];
 const keyHint=(st.api_key&&st.api_key.configured)?`已配置 ${st.api_key.masked}，留空表示不改`:'粘贴你的 API Key';
 modal('配置内容生成模型',
  `<p class="muted">${esc(st.note||'')}</p>
   <label>常用端点<select id="llm-preset">${presets.map(p=>`<option value="${esc(p.base_url)}|${esc(p.model)}">${esc(p.label)}</option>`).join('')}</select></label>
   <label>接口地址<input name="base_url" required maxlength="500" value="${esc(st.base_url||'')}" placeholder="https://api.deepseek.com/v1"></label>
   <label>模型名<input name="model" required maxlength="200" value="${esc(st.model||'')}" placeholder="deepseek-chat"></label>
   <label>API Key<input name="api_key" type="password" maxlength="500" placeholder="${esc(keyHint)}"></label>
   <div class="toolbar" style="margin:0"><button type="button" id="llm-test">测试连接</button><button type="button" class="btn-quiet" id="llm-clear">清除配置</button><span class="muted" id="llm-test-msg"></span></div>`,
  async data=>{const out=await api('/api/content-llm','PUT',Object.fromEntries(data));return {message:out.configured?`已保存内容模型：${out.model}`:'已保存，但还缺接口地址或模型名'};},
  '保存');
 $('#llm-preset').onchange=e=>{const [b,m]=String(e.target.value||'').split('|');if(b){$('#modal [name="base_url"]').value=b;$('#modal [name="model"]').value=m;}};
 $('#llm-test').onclick=async()=>{const el=$('#llm-test-msg');el.textContent='正在测试（只发一句问候，不发素材）…';try{const f=new FormData($('#modal-form'));const out=await api('/api/content-llm/check','POST',{base_url:f.get('base_url'),model:f.get('model'),api_key:f.get('api_key')});el.textContent='✓ '+out.message;}catch(err){el.textContent='✕ '+err.message;}};
 $('#llm-clear').onclick=async()=>{try{await api('/api/content-llm','DELETE');$('#modal').close();await render();notice('已清除内容模型配置，回到本地整理模式');}catch(err){notice(err.message);}};
}
function createProject() { modal('创建项目','<label>项目名称<input name="name" required maxlength="200" autofocus></label><label>主体类型<select name="target_type"><option value="enterprise">企业</option><option value="product">产品</option><option value="person">个人</option><option value="case">案例</option></select></label>',async data=>{const r=await api('/api/projects','POST',Object.fromEntries(data));location.hash=link('projects',r.slug,'profile');},'创建'); }
async function render() {
 clearTimeout(timer);const token=++epoch; const {view,params}=route();notice('');
 const context=params.get('project')||sessionStorage.getItem('geo-project');
 if(params.get('project'))sessionStorage.setItem('geo-project',params.get('project'));
 document.querySelectorAll('nav a').forEach(a=>{const target=a.hash.slice(1).split('?')[0];a.classList.toggle('active',target===view);a.href=link(target,['actions','content','library','publications','reports'].includes(target)?context:null);});
 $('#breadcrumb').innerHTML=crumbHTML(view,null,null);
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
  show(head(d.project.name,'<a class="button" href="#projects">返回项目列表</a>','查看主体资料、诊断配置、执行过程与结果。')+tabs+'<div id="project-view"></div>',token);if(token!==epoch)return;
  $('#breadcrumb').innerHTML=crumbHTML('projects',d.project,tab);const v=$('#project-view'),p=d.profile;
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
    ?`<div class="legend"><span><i></i>官方来源引用</span><span><i class="alt"></i>自然提及</span></div><div class="chart"><svg viewBox="0 0 100 100" preserveAspectRatio="none"><polyline points="${line(hist,'official_yes',hmax)}" vector-effect="non-scaling-stroke"/><polyline class="alt" points="${line(hist,'mentions',hmax)}" vector-effect="non-scaling-stroke"/></svg></div><div class="xlabels">${hist.map(x=>`<span>${esc(localDay(x.generated_at||x.created_at))}</span>`).join('')}</div>`
    :`<div class="legend"><span><i></i>有效回答</span><span><i class="alt"></i>官方来源引用</span></div><div class="qchart">${qs.map(q=>`<div class="qgroup"><div class="qbars"><i style="height:${Math.round((q.answered||0)/qmax*100)}%"></i><i class="alt" style="height:${Math.round((q.official_yes||0)/qmax*100)}%"></i></div><span>${esc(q.id)}</span></div>`).join('')}</div>`;
   const conclusion=`非品牌题 ${k.non_brand_success} 条有效回答中，自然提及 ${k.mentions} 次、明确推荐 ${k.recommendations} 次；官方来源引用命中 ${citedYes} 条（覆盖 ${citedYes}/${k.planned} 题）。`;
   v.innerHTML=`
   <div class="page-head"><div><p class="eyebrow">GEO 现状</p><h2 style="margin:0">${esc(o.project.name)}</h2><p class="muted" style="margin:4px 0 0">最近批次 ${esc(localTime(k.generated_at||k.created_at).slice(0,10))} · ${esc(states[k.status]||k.status)}</p></div><div class="toolbar" style="margin:0"><a class="button primary" href="${link('reports',slug)}&run=${encodeURIComponent(k.run_id)}">查看报告</a><a class="button" href="${link('projects',slug,'runs')}">执行与结果</a></div></div>
   <section class="summary"><div><span class="chip ${c.percent>=100?'good':'warn'}">证据完整度 ${c.percent}%</span><p class="summary-line">${esc(conclusion)}</p></div><div class="summary-meta"><span>边界</span><p class="muted">时点快照，不复测、不推断趋势、不做平台排名。</p></div></section>
   <div class="ov-grid ov-2">
    <section class="card hero"><p class="eyebrow">核心指标</p><div class="hero-main"><span class="muted">非品牌题自然提及率</span><strong>${mentionRate}%</strong><span class="muted">${k.mentions} 次提及 / ${k.non_brand_success} 条有效回答</span></div><div class="hero-grid"><div><span>明确推荐</span><strong>${k.recommendations}</strong></div><div><span>官方来源覆盖</span><strong>${citedYes}/${k.planned}</strong>${bar(citedYes,k.planned)}</div><div><span>引用记录</span><strong>${k.citations}</strong></div><div><span>任务完成</span><strong>${k.success}/${k.planned}</strong>${bar(k.success,k.planned)}</div></div></section>
    <section class="card"><div class="card-head"><div><h2>证据完整度</h2><p>有完整证据的终态任务占比</p></div></div><div class="score-body"><div class="donut" style="background:conic-gradient(var(--blue) 0 ${c.percent}%,var(--line) ${c.percent}% 100%)"><div class="score-value"><strong>${c.percent}%</strong><small>${c.complete}/${c.planned}</small></div></div><div class="score-copy"><strong>${c.percent>=100?'本批次证据齐全':(c.percent>=80?'基本齐全，有少量缺口':'缺口较多，报告不完整')}</strong><ul class="fact-list">${(o.incomplete||[]).map(x=>`<li>${esc(x)}</li>`).join('')||'<li>全部任务均有回答与截图证据</li>'}</ul></div></div></section>
   </div>
   <div class="ov-grid ov-2">
    <section class="card"><div class="card-head"><div><h2>${isTrend?'批次趋势':'问题维度分布'}</h2><p>${isTrend?'按批次对比官方来源引用与自然提及':'每个问题的有效回答与官方来源引用；第二次诊断后这里显示变化趋势'}</p></div></div><div class="chart-wrap">${chartBody}</div></section>
    <section class="card"><div class="card-head"><div><h2>平台对比</h2><p>成功/失败与提及、引用</p></div></div><div class="table-scroll"><table class="num-table"><thead><tr><th>平台</th><th>成功</th><th>失败</th><th>提及</th><th>引用</th></tr></thead><tbody>${(o.platforms||[]).map(x=>`<tr><td>${esc(x.label)}</td><td>${x.success}</td><td>${x.failed}</td><td>${x.non_brand_mention}</td><td>${x.official_citation_yes}</td></tr>`).join('')||'<tr><td colspan="5">暂无</td></tr>'}</tbody></table></div></section>
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
   v.innerHTML=`<section class="panel"><div class="toolbar"><select id="batch" aria-label="诊断批次">${d.runs.map(x=>`<option value="${esc(x.id)}" ${x.id===selected?'selected':''}>${esc(localTime(x.created_at))} · ${esc(states[x.status]||x.status)}</option>`).join('')}</select>${badge(r.status)}${operations.map(([k,l])=>`<button data-op="${k}" class="${k==='execute'?'primary':''}">${l}</button>`).join('')}</div>${r.paused?`<div class="panel" style="margin:0 0 18px;background:var(--orange2);border-color:var(--notice-line)"><strong>平台要求人工验证，执行已暂停</strong><p class="muted" style="margin:8px 0">待处理：${esc((r.waiting_tasks||[]).join('、'))}。请在已打开的浏览器窗口完成登录或验证码，然后点击“已处理验证”继续。系统不会重新提交本题问题。</p></div>`:''}${r.blocker?`<p class="muted">${esc(r.blocker)}</p>`:''}${(r.preflight||[]).length?`<p class="muted">平台检查：${r.preflight.map(c=>`${esc(c.platform)} ${c.check==='OK'?(c.login_state_guess==='logged_in'?'已登录':'未登录'):'打不开'}`).join(' · ')}</p>`:''}<p>成功 ${r.state.success||0} · 失败 ${r.state.failed||0} · 待执行 ${r.state.pending||0}</p><div class="table-scroll"><table><thead><tr><th>任务</th><th>平台</th><th>状态</th><th>结果</th></tr></thead><tbody>${r.tasks.map(t=>`<tr><td><strong>${esc(t.question_id)}</strong><div class="muted" style="max-width:480px;margin-top:5px">${esc(t.prompt)}</div></td><td>${esc(t.platform_label)}</td><td>${badge(t.status)}</td><td>${r.observations.some(o=>o.task_id===t.task_id)?`<button data-result="${esc(t.task_id)}" aria-expanded="false">查看回答与证据</button>`:esc(t.failure_reason||'')}</td></tr>`).join('')}</tbody></table></div></section>`;
   if(['completed','degraded'].includes(r.status)){v.insertAdjacentHTML('afterbegin','<div class="toolbar"><button id="derive" class="primary">生成改善任务</button></div>');$('#derive').onclick=async()=>{try{const out=await api(base+'/runs/'+selected+'/improvements/derive','POST',{});notice(out.created?`已生成 ${out.created} 条改善任务`:'没有新的可生成项（仅取有证据的成功观测）');location.hash=link('actions',slug);}catch(err){notice(err.message);}};}
   $('#batch').onchange=e=>{location.hash=link('projects',slug,'runs')+'&run='+encodeURIComponent(e.target.value);};
   document.querySelectorAll('[data-op]').forEach(b=>b.onclick=()=>modal(b.textContent,b.dataset.op==='execute'?'<p>将向所选平台提交冻结问题。请确认主体资料可用于本次诊断。</p>':'<p>确认执行此操作？</p>',()=>api(base+'/runs/'+selected+'/'+b.dataset.op,'POST',{confirm:true}),b.textContent));
   // 行内展开：详情插在被点那一行下面。同一时刻只开一行，再点一次收起
   document.querySelectorAll('[data-result]').forEach(b=>b.onclick=()=>{
    const o=r.observations.find(x=>x.task_id===b.dataset.result),row=b.closest('tr');
    const wasOpen=row.nextElementSibling&&row.nextElementSibling.classList.contains('evidence-row');
    document.querySelectorAll('.evidence-row').forEach(x=>x.remove());
    document.querySelectorAll('[data-result]').forEach(x=>x.setAttribute('aria-expanded','false'));
    if(wasOpen)return;
    b.setAttribute('aria-expanded','true');
    const files=(o.evidence_files||[]).map(f=>{const url=API+base+'/runs/'+selected+'/files/'+f.split('/').map(encodeURIComponent).join('/');return /\.(png|jpg|jpeg|webp)$/i.test(f)?`<figure><img style="max-width:100%" src="${esc(url)}" alt="${esc(f)}"><figcaption>${esc(f)}</figcaption></figure>`:`<a class="button" href="${esc(url)}" download>下载 ${esc(f)}</a>`;}).join('');
    const meta=`${esc(o.task_id)} · ${esc(o.platform_label)} · ${esc(states[o.status]||o.status)} · ${esc(localTime(o.attempted_at))}`;
    const tr=document.createElement('tr');tr.className='evidence-row';
    const answer=o.response_text||o.failure_reason||'无回答';
    const answerLabel=o.response_text?'回答正文':(o.failure_reason?'失败原因':'无内容');
    const held=files?`<p class="eyebrow" style="margin:16px 0 8px">证据文件</p>${files}`:'';
    tr.innerHTML=`<td colspan="4"><div class="stage"><div class="stage-body"><p class="eyebrow">${answerLabel}</p><pre>${esc(answer)}</pre>${held}</div><p class="stage-status ${tone(o.status)}">${meta}</p></div></td>`;
    row.insertAdjacentElement('afterend',tr);tr.scrollIntoView({block:'center'});
   });
   if(['running','preflight','login'].includes(r.status)){const poll=()=>{if(token!==epoch)return;if($('#modal').open){timer=setTimeout(poll,2000);return;}render();};timer=setTimeout(poll,5000);}
  }
 } else if(view==='settings') {
  const d=(await api('/api/settings')).settings;show(head('设置')+`<form id="settings-form" class="panel"><label>工作空间名称<input name="workspace_label" required value="${esc(d.workspace_label)}"></label><label>默认操作人<input name="default_operator" value="${esc(d.default_operator)}"></label><button class="primary">保存</button></form>`,token);
  if(token!==epoch)return;$('#settings-form').onsubmit=async e=>{e.preventDefault();try{await api('/api/settings','PUT',Object.fromEntries(new FormData(e.target)));notice('已保存');}catch(err){notice(err.message);}};
 } else if(['actions','content','library','publications','reports'].includes(view)) {
  const projects=(await api('/api/projects')).projects;
  const chosen=projects.find(p=>p.slug===context);
  const scopeSel=`<select id="scope" aria-label="当前项目"><option value="">选择项目</option>${projects.map(p=>`<option value="${p.slug}" ${p.slug===chosen?.slug?'selected':''}>${esc(p.name)}</option>`).join('')}</select>`;
  const createBtn=!projects.length?'<button id="create">创建项目</button>':'';
  // 四个项目上下文页面共用同一种一级页头：标题与说明在左，项目选择在右。
  show(head(labels[view],`<label class="field-inline"><span>项目</span>${scopeSel}</label>${createBtn}`)+`<div id="module"></div>`,token);
  if(token!==epoch)return;$('#scope').onchange=e=>{sessionStorage.setItem('geo-project',e.target.value);location.hash=link(view,e.target.value);};if($('#create'))$('#create').onclick=createProject;
  if(!chosen){$('#module').innerHTML='<p class="empty">请选择需要处理的项目</p>';return;}
  $('#breadcrumb').innerHTML=crumbHTML(view,chosen,null);const base='/api/projects/'+chosen.slug;
  if(view==='reports') {
   const items=(await api(base+'/reports')).reports;if(token!==epoch)return;
   // 报告名用文档自己的首个标题；文件名与元信息放极小字行，避免两层标题重复
   const isRaw=n=>/\.json$/i.test(n);
   const purpose={'diagnosis.md':'诊断正文：本次 GEO 现状与结论','optimization-plan.md':'优化执行方案：逐条整改项与依据','QUALITY_REPORT.md':'数据质量报告：本次采集的可信度','TEXT_CHECK.md':'文案体检：模板泄漏与主体一致性检查','manual-review.md':'人工复核清单：需要你确认的待定项','metrics.json':'指标原始数据：本批次全部计数'};
   const rtab=params.get('rview')==='raw'?'raw':'report';
   const group=items.filter(x=>rtab==='raw'?isRaw(x.name):!isRaw(x.name));
   const nMd=items.filter(x=>!isRaw(x.name)).length,nRaw=items.filter(x=>isRaw(x.name)).length;
   const tabs=`<div class="tabs"><a class="${rtab==='report'?'active':''}" href="${link('reports',chosen.slug)}">诊断报告<span class="tabs-count">${nMd}</span></a><a class="${rtab==='raw'?'active':''}" href="${link('reports',chosen.slug)}&rview=raw">原始数据<span class="tabs-count">${nRaw}</span></a></div>`;
   $('#module').innerHTML=tabs+`<section class="panel">${group.map((r,i)=>`<div class="row"><span>${esc(r.name)}<small>${esc(purpose[r.name]||'')}</small></span><button data-report="${i}">查看</button></div>`).join('')||`<p class="empty">${rtab==='raw'?'本批次没有原始数据文件':'本批次没有报告文件'}</p>`}</section><section id="report-text" hidden class="panel"></section>`;
   document.querySelectorAll('[data-report]').forEach(b=>b.onclick=async()=>{try{
    const r=group[Number(b.dataset.report)],d=await api(base+'/runs/'+r.run_id+'/text/report/'+encodeURIComponent(r.name));
    const raw=isRaw(r.name);
    const title=raw?r.name:(d.text.match(/^\s*#\s+(.+)$/m)||[,r.name])[1].trim();
    const body=raw?`<pre class="raw">${esc(d.text)}</pre>`:md(d.text.replace(/^\s*#\s+.*(\r?\n)?/,''));
    const meta=`${esc(states[r.status]||r.status)} · ${esc(chosen.name)} · 批次 ${esc(r.run_id.slice(0,8))}`;
    const bytes=new TextEncoder().encode(d.text).length,lines=d.text.split('\n').length;
    const stamp=(d.text.match(/(?:生成时间|generated_at)["\s：:]*([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}(?:Z|[+-][0-9]{2}:?[0-9]{2})?)/)||[])[1];
    const dl=API+base+'/runs/'+r.run_id+'/files/report/'+encodeURIComponent(r.name);
    const rail=[
     ['操作',`<div class="rail-actions"><button id="copy-body" class="primary" type="button">复制正文</button><a class="button" href="${esc(dl)}" download>下载文件</a></div>`],
     ['文件',`<div class="rail-rows"><div class="rail-row"><span>名称</span><strong>${esc(r.name)}</strong></div><div class="rail-row"><span>大小</span><strong id="rail-size">${bytes} 字节</strong></div><div class="rail-row"><span>行数</span><strong>${lines}</strong></div></div>`],
     ['批次',`<div class="rail-rows"><div class="rail-row"><span>编号</span><strong>${esc(r.run_id.slice(0,8))}</strong></div>${stamp?`<div class="rail-row"><span>生成</span><strong>${esc(localTime(stamp))}</strong></div>`:''}<div class="rail-row"><span>状态</span>${badge(r.status)}</div></div>`],
     ['相关',`<div class="rail-actions"><a class="button" href="${link('projects',chosen.slug,'runs')}&run=${encodeURIComponent(r.run_id)}">执行与结果</a><a class="button" href="${link('reports',chosen.slug)}&rview=raw">原始数据</a></div>`]
    ].map(([label,html])=>`<section class="rail-block"><p class="eyebrow">${label}</p>${html}</section>`).join('');
    const box=$('#report-text');box.hidden=false;box.className='report-wrap';
    box.innerHTML=`<div class="report-layout"><div><div class="page-head"><div><h2 style="margin:0">${esc(title)}</h2><p class="eyebrow" style="margin:5px 0 0">${meta} · ${esc(r.name)}</p><p class="muted" style="margin:7px 0 0">${esc(purpose[r.name]||'')}</p></div></div><div class="stage"><div class="${raw?'stage-body':'report'}">${body}</div><p class="stage-status ${tone(r.status)}">渲染于 ${esc(localTime(new Date().toISOString()))} · ${meta}</p></div></div><div class="report-rail">${rail}</div></div>`;
    $('#copy-body').onclick=async()=>{try{await navigator.clipboard.writeText(d.text);notice(`已复制 ${r.name} 全文（${d.text.length} 字）`);}catch(e){notice('复制失败，请手动选中正文');}};
    // 先显示按正文算出的字节，再用真实下载下来的字节数盖掉（该端点不支持 HEAD，HEAD 会拿到 405 的响应体长度）
    fetch(dl).then(x=>x.arrayBuffer()).then(b=>{if($('#rail-size'))$('#rail-size').textContent=b.byteLength+' 字节';}).catch(()=>{});
   }catch(err){notice(err.message);}});
   const wantRun=params.get('run');
   if(wantRun&&rtab==='report'){const i=group.findIndex(r=>r.run_id===wantRun&&r.name==='diagnosis.md');if(i>=0)document.querySelector(`[data-report="${i}"]`)?.click();}
  } else {
   // 各视图需要的清单不一样：改善任务、内容/内容库详情、发布与报告各自取自己的。
   let items=[];
   if(view==='actions') items=(await api(base+'/editorial/actions')).items;
   else if(view==='content'||params.get('asset')) items=(await api(base+'/editorial/content')).items;
   if(token!==epoch)return;
   if(view==='actions') {
    $('#module').innerHTML=`<section class="panel">${items.map(x=>`<div class="row"><div><strong>${esc(x.title)}</strong> ${badge(x.status)}<p class="muted">${esc(x.description)}</p><a href="${link('projects',chosen.slug,'runs')+'&run='+x.run_id}">查看诊断依据</a></div><button data-create-asset="${x.id}">编写内容</button></div>`).join('')||'<p class="empty">暂无改善任务。完成诊断后可从结果生成。</p>'}</section>`;
    document.querySelectorAll('[data-create-asset]').forEach(b=>b.onclick=async()=>{try{const x=await api(base+'/actions/'+b.dataset.createAsset+'/content','POST',{});location.hash=link('content',chosen.slug)+'&asset='+x.id;}catch(err){notice(err.message);}});
   } else if(view==='content') {
    // 内容生产：只负责「写作任务书 + 素材 → 生成初稿」。稿件在「内容库」查看与编辑。
    const asset=items.find(x=>x.id===params.get('asset'));
    if(asset){
     const ctx=await api(base+'/content/'+asset.id+'/context');if(token!==epoch)return;
     const q=ctx.quality||{errors:[],warnings:[],passed:false};
     const bundle=ctx.source_bundle||{};
     const meta=ctx.generation_meta||{};
     const generated=!/（待补充/.test(asset.body||'')&&!!(asset.body||'').trim();
     const sourceSummary=asset.source==='diagnosis'
      ?`诊断问题 ${esc(bundle.question_id||'—')} · ${(bundle.observations||[]).length} 条平台观测 · ${(bundle.evidence_files||[]).length} 个证据文件`
      :'直接创建 · 使用项目资料与素材';
     $('#module').innerHTML=`<form id="asset-form" class="panel">
      <div class="toolbar"><a class="button" href="${link('content',chosen.slug)}">返回生成任务</a>${badge(generated?'草稿':'待生成')}<span class="muted">${asset.source==='manual'?'直接创建':'来自诊断'}</span></div>
      <h2>写作任务书</h2><div class="form-grid">
       <label>目标渠道<input name="channel" value="${esc(asset.channel||'')}" placeholder="公众号长文 / 官网 / 知乎 / 小红书"></label>
       <label>目标读者<input name="audience" value="${esc(asset.audience||'')}" placeholder="谁会阅读并据此做什么决定"></label>
       <label class="wide">写作目标<textarea name="objective" rows="3" placeholder="这篇内容要回答什么问题、推动什么行动">${esc(asset.objective||'')}</textarea></label>
       <label>语气<input name="tone" value="${esc(asset.tone||'专业、克制、具体')}"></label>
       <label>目标字数<input name="target_length" type="number" min="200" max="10000" value="${esc(asset.target_length||1200)}"></label>
       <label class="wide">关键词<input name="keywords" value="${esc(asset.keywords||'')}" placeholder="多个关键词用逗号分隔"></label>
       <label class="wide">写作要点<textarea name="brief" rows="3">${esc(asset.brief||'')}</textarea></label>
      </div>
      <details><summary><strong>证据包</strong> <span class="muted">${sourceSummary}</span></summary>
       <p>${esc(bundle.notice||'')}</p>${bundle.question?`<p><strong>对应问题：</strong>${esc(bundle.question)}</p>`:''}
       ${bundle.suggested_title?`<p><strong>报告建议标题：</strong>${esc(bundle.suggested_title)}</p>`:''}
       ${(bundle.evidence_files||[]).length?`<p class="muted">证据索引：${bundle.evidence_files.map(esc).join('、')}</p>`:'<p class="muted">当前没有诊断证据文件；使用项目资料和上传的素材。</p>'}
      </details>
      <h2 style="margin-top:24px">素材</h2>
      <section class="source-panel">
       <div class="toolbar">
        <label class="button src-upload">上传附件<input type="file" id="src-file" multiple hidden accept=".txt,.md,.markdown,.csv,.json,.html,.htm,.pdf,.docx,.xlsx,.xlsm,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff"></label>
        <input id="src-url" placeholder="粘贴网址，例如 https://example.com/about" style="flex:1;min-width:220px">
        <button type="button" id="src-add-url">添加网址</button>
        <button type="button" id="src-paste">粘贴文本</button>
        <span class="muted" id="src-hint"></span>
       </div>
       <ul class="source-list" id="src-list"></ul>
       <p class="muted">生成时会把这里的内容读进来，提取可引用的企业介绍与事实，并自动写进稿件的「来源」。素材不等于已确认事实，审核前仍需核对口径与时效。图片与无文字层的扫描件会自动 OCR（本机识别，约 8 秒/页）。</p>
      </section>
      <div class="toolbar"><button class="primary" id="save-brief">保存任务书</button><button type="button" id="generate" class="primary">${ctx.generator.configured?(generated?'重新生成初稿':'生成初稿'):(generated?'重新整理素材':'整理素材')}</button><button type="button" id="cfg-llm">配置内容模型</button><span class="muted">${esc(ctx.generator.message)}${meta.generated_at?' · 上次生成 '+esc(localTime(meta.generated_at)):''}</span></div>
      ${generated
        ?`<p class="muted">本稿已生成：约 ${q.plain_length||0} 字，自动质量检查${q.passed?'通过':'未通过'}。正文、来源与审核都在<a href="${link('library',chosen.slug)+'&asset='+asset.id}">内容库</a>处理。</p>`
        :'<p class="muted">点「生成初稿」后，稿件会进入「内容库」，在那里查看、编辑和审核。</p>'}
     </form>`;
     const form=$('#asset-form');
     const payload=()=>{const o=Object.fromEntries(new FormData(form));o.target_length=Number(o.target_length||1200);return o;};
     form.onsubmit=async e=>{e.preventDefault();try{await api(base+'/content/'+asset.id,'PUT',{...payload(),title:asset.title,body:asset.body,revision:asset.revision});await render();notice('已保存任务书');}catch(err){notice(err.message);}};
     const runGenerate=async confirmOverwrite=>{
      try{
       const p=payload();
       const out=await api(base+'/content/'+asset.id+'/generate','POST',{revision:asset.revision,confirm_overwrite:confirmOverwrite,brief:p.brief,channel:p.channel,audience:p.audience,objective:p.objective,tone:p.tone,keywords:p.keywords,target_length:p.target_length});
       await render();
       const quality=out.quality||{};
       const notes=out.notes||[];
       const isDraft=ctx.generator.configured;
       modal(isDraft?'初稿已生成':'素材已整理',
        `<p>《${esc(out.title||asset.title)}》${isDraft?'已完成':'已按素材整理'}，正文约 ${quality.plain_length||0} 字。</p>
         <p class="muted">${quality.passed?'自动质量检查通过：可以到内容库核对后审核。':'自动质量检查未通过，到内容库看需要补什么。'}</p>
         ${(out.warnings||[]).map(w=>`<p class="muted">△ ${esc(w)}</p>`).join('')}
         ${notes.length?`<ul class="gen-notes">${notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`:''}`,
        async()=>{location.hash=link('library',chosen.slug)+'&asset='+asset.id;},'去内容库查看','留在本页');
      }catch(err){notice(err.message);}
     };
     $('#generate').onclick=()=>{if(generated)modal('确认重新生成','<p>重新生成会覆盖当前标题、摘要和正文。任务书与素材会保留。</p><label class="check"><input type="checkbox" required>我已确认覆盖当前稿件</label>',()=>runGenerate(true),'确认覆盖并生成');else runGenerate(false);};
     // OCR 是后台跑的，没结束就隔几秒刷新一次状态
     const SRC_KIND={file:'附件',url:'网址',note:'文本'};
     const SRC_STATUS={ok:['已提取','good'],empty:['没提取到文字','warn'],failed:['提取失败','bad'],unsupported:['需人工说明','warn'],pending:['待提取','warn'],ocr_pending:['OCR 识别中','warn']};
     const paintSources=()=>{
      const box=$('#src-list');if(!box)return;
      const rows=ctx.sources||[];
      box.innerHTML=rows.length?rows.map(s=>{
       const st=SRC_STATUS[s.status]||['未知','warn'];
       const title=s.label||s.file_name||s.url||'素材';
       const meta=s.kind==='url'?s.url:s.kind==='file'?`${s.file_name} · ${Math.max(1,Math.round((s.size_bytes||0)/1024))} KB`:'粘贴文本';
       return `<li data-source="${s.id}" data-status="${esc(s.status)}"><div><span class="src-tag">${SRC_KIND[s.kind]||esc(s.kind)}</span> <strong>${esc(title)}</strong> <span class="badge ${st[1]}">${st[0]}</span><small>${esc(meta)}${s.chars?` · ${s.chars} 字`:''}${s.note?' · '+esc(s.note):''}</small></div><div class="src-ops">${s.kind!=='note'?`<button type="button" class="btn-quiet" data-src-refresh="${s.id}">重新提取</button>`:''}${s.has_file?`<a class="btn-quiet" href="${API}/api/projects/${chosen.slug}/content/${asset.id}/sources/${s.id}/download">下载</a>`:''}<button type="button" class="btn-quiet" data-src-del="${s.id}">删除</button></div></li>`;
      }).join(''):'<li class="src-empty">还没有素材。上传产品资料、添加官网网址，或粘贴一段介绍。</li>';
      box.querySelectorAll('[data-src-del]').forEach(b=>b.onclick=async()=>{try{await api(base+'/content/'+asset.id+'/sources/'+b.dataset.srcDel,'DELETE');await render();notice('已删除素材');}catch(err){notice(err.message);}});
      box.querySelectorAll('[data-src-refresh]').forEach(b=>b.onclick=async()=>{const hint=$('#src-hint');if(hint)hint.textContent='正在重新读取…';try{const out=await api(base+'/content/'+asset.id+'/sources/'+b.dataset.srcRefresh+'/refresh','POST',{});await render();notice(out.status==='ok'?`已重新提取 ${out.chars} 字`:`重新读取完成：${out.note||out.status}`);}catch(err){if(hint)hint.textContent='';notice(err.message);}});
      const waiting=box.querySelectorAll('[data-status="ocr_pending"]').length;
      if(waiting&&token===epoch){
       const hint=$('#src-hint');if(hint)hint.textContent=`${waiting} 个素材正在 OCR 识别，完成后自动刷新…`;
       window.__geoOcrPolls=window.__geoOcrPolls||0;
       if(window.__geoOcrPolls<24){window.__geoOcrPolls+=1;const stamp=epoch;setTimeout(()=>{if(epoch===stamp)render();},4000);}
      }else if(token===epoch){window.__geoOcrPolls=0;}
     };
     paintSources();
     $('#src-file').onchange=async e=>{const files=[...e.target.files];if(!files.length)return;const fd=new FormData();files.forEach(f=>fd.append('files',f));const hint=$('#src-hint');if(hint)hint.textContent=`正在读取 ${files.length} 个文件…`;try{const out=await apiUpload(base+'/content/'+asset.id+'/sources/file',fd);const bad=out.results.filter(r=>r.error);const queued=out.results.filter(r=>r.status==='ocr_pending').length;await render();notice(bad.length?`${out.results.length-bad.length} 个素材已导入，${bad.length} 个失败：${bad[0].error}`:(queued?`已导入 ${out.results.length} 个素材，其中 ${queued} 个正在 OCR 识别`:`已导入 ${out.results.length} 个素材`));}catch(err){if(hint)hint.textContent='';notice(err.message);}};
     const addUrl=async()=>{const input=$('#src-url');const url=(input.value||'').trim();if(!url){notice('请先填写网址');return;}const hint=$('#src-hint');if(hint)hint.textContent='正在抓取页面…';try{const out=await api(base+'/content/'+asset.id+'/sources/url','POST',{url});await render();notice(out.status==='ok'?`已抓取并提取 ${out.chars} 字`:(out.status==='ocr_pending'?'已保存，正在 OCR 识别':`抓取完成但没提取到文字：${out.note||''}`));}catch(err){if(hint)hint.textContent='';notice(err.message);}};
     $('#src-add-url').onclick=addUrl;
     $('#src-url').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();addUrl();}};
     $('#src-paste').onclick=()=>modal('粘贴文本素材','<label>名称<input name="label" required maxlength="200" placeholder="例如：销售口述的产品要点"></label><label>内容<textarea name="text" rows="8" required placeholder="把已确认的介绍、参数或案例粘进来"></textarea></label>',async d=>{await api(base+'/content/'+asset.id+'/sources/note','POST',{label:d.get('label'),text:d.get('text')});},'保存素材');
     $('#cfg-llm').onclick=()=>openContentLlm();
    }else{
     const todo=items.filter(x=>/（待补充/.test(x.body||''));
     $('#module').innerHTML=`<div class="toolbar"><button id="direct" class="primary">＋ 新建内容任务书</button><a class="button" href="${link('library',chosen.slug)}">去内容库看已生成的初稿</a><span class="muted">确认任务书与素材后生成初稿。</span></div>
      <section class="panel">${todo.map(x=>`<a class="row" href="${link('content',chosen.slug)+'&asset='+x.id}"><span>${esc(x.title)}<small>${x.channel?'渠道：'+esc(x.channel)+' · ':''}还没有生成初稿</small></span>${badge('待生成')}</a>`).join('')||'<p class="empty">没有待生成的任务书。可以从「改善任务」点「编写内容」，或直接新建。</p>'}</section>`;
     $('#direct').onclick=()=>modal('新建内容任务书','<label>暂定标题<input name="title" required maxlength="200" autofocus placeholder="例如：设备资产管理系统选型指南"></label><label>写作要点<textarea name="brief" placeholder="要回答的问题\n需要覆盖的判断标准\n已有的可核验材料"></textarea></label><label>目标渠道<input name="channel" placeholder="例如：公众号长文 / 官网 / 知乎回答"></label><p class="muted">创建后上传素材并确认写作任务书，再生成初稿。</p>',async data=>{const r=await api(base+'/content','POST',Object.fromEntries(data));location.hash=link('content',chosen.slug)+'&asset='+r.id;},'创建任务书');
    }
   } else if(view==='library') {
    // 内容库：所有内容的清单 + 查看 / 编辑 / 删除。
    const lib=(await api(base+'/library')).items;if(token!==epoch)return;
    const libAsset=params.get('asset')?items.find(x=>x.id===params.get('asset')):null;
    const mode=params.get('mode')==='edit'?'edit':'view';
    const statusText=(status,generatedAt)=>status==='approved'?'已审核':(generatedAt?'草稿':'待生成');
    const statusTone=(status,generatedAt)=>status==='approved'?'good':(generatedAt?'':'warn');
    const removeAsset=(x)=>{
     const extra=x.job_count?`<p class="muted">这条内容已有 ${x.job_count} 条发布记录，删除会一并移除。</p>`:'';
     modal('删除内容',`<p>确定删除《${esc(x.title)}》？它的素材与发布记录会一起删除，不可恢复。</p>${extra}<label class="check"><input type="checkbox" required>我确认删除这条内容</label>`,
      async()=>{const out=await api(base+'/content/'+x.id,'DELETE');if(libAsset)location.hash=link('library',chosen.slug);return out;},'删除');
    };
    if(!libAsset){
     $('#module').innerHTML=`<div class="toolbar"><button id="newbrief" class="primary">＋ 新建内容任务书</button><span class="muted">共 ${lib.length} 条内容；序号按列表顺序，最新在前。</span></div>
      <section class="panel"><div class="table-scroll"><table class="lib-table"><thead><tr><th>序号</th><th>标题</th><th>来源</th><th>渠道</th><th>状态</th><th>字数</th><th>生成时间</th><th>操作</th></tr></thead><tbody>
      ${lib.map(x=>`<tr data-lib="${x.id}"><td>${x.index}</td><td><strong>${esc(x.title)}</strong>${x.material_count?`<small>素材 ${x.material_count} 个</small>`:''}</td><td>${x.source==='manual'?'直接创建':'来自诊断'}</td><td>${esc(x.channel||'—')}</td><td><span class="badge ${statusTone(x.status,x.generated_at)}">${statusText(x.status,x.generated_at)}</span></td><td>${x.chars||0}</td><td>${x.generated_at?esc(localTime(x.generated_at).slice(0,16).replace('T',' ')):'未生成'}</td><td class="lib-ops"><a href="${link('library',chosen.slug)+'&asset='+x.id}">查看</a><a href="${link('library',chosen.slug)+'&asset='+x.id+'&mode=edit'}">编辑</a><button type="button" class="btn-quiet" data-lib-del="${x.id}">删除</button></td></tr>`).join('')||'<tr><td colspan="8" class="src-empty">内容库是空的。到「内容生产」确认任务书与素材后生成初稿。</td></tr>'}
      </tbody></table></div></section>`;
     $('#newbrief').onclick=()=>modal('新建内容任务书','<label>暂定标题<input name="title" required maxlength="200" autofocus></label><label>写作要点<textarea name="brief" placeholder="要回答的问题"></textarea></label><label>目标渠道<input name="channel" placeholder="例如：公众号长文"></label>',async data=>{const r=await api(base+'/content','POST',Object.fromEntries(data));location.hash=link('content',chosen.slug)+'&asset='+r.id;},'创建任务书');
     $('#module').querySelectorAll('[data-lib-del]').forEach(b=>b.onclick=()=>removeAsset(lib.find(x=>x.id===b.dataset.libDel)||{id:b.dataset.libDel,title:''}));
    }else{
     const ctx=await api(base+'/content/'+libAsset.id+'/context');if(token!==epoch)return;
     const q=ctx.quality||{errors:[],warnings:[],passed:false};
     const libRow=lib.find(x=>x.id===libAsset.id)||{};
     const meta=ctx.generation_meta||{};
     const qualityHtml=q.passed
      ?'<p style="color:var(--green)">✓ 当前版本通过自动质量检查</p>'
      :`<div>${(q.errors||[]).map(x=>`<p style="color:var(--red)">● ${esc(x)}</p>`).join('')}</div>`;
     const warnings=(q.warnings||[]).map(x=>`<p class="muted">△ ${esc(x)}</p>`).join('');
     const head=`<div class="toolbar"><a class="button" href="${link('library',chosen.slug)}">返回内容库</a>${badge(statusText(libAsset.status,meta.generated_at))}<span class="muted">第 ${libRow.index||'—'} 条 · ${libAsset.source==='manual'?'直接创建':'来自诊断'} · ${esc(libAsset.channel||'未设渠道')}</span></div>`;
     if(mode==='view'){
      $('#module').innerHTML=`${head}
       <section class="panel"><h2>${esc(libAsset.title)}</h2>
        <p class="muted">约 ${q.plain_length||0} 字${meta.generated_at?` · 生成于 ${esc(localTime(meta.generated_at))}`:''}${meta.engine?` · ${esc(meta.engine)}`:''}${meta.model?` / ${esc(meta.model)}`:''} · 素材 ${(ctx.sources||[]).length} 个 · 版本 ${libAsset.revision}</p>
        ${libAsset.summary?`<p>${esc(libAsset.summary)}</p>`:''}
        <div class="draft-body">${esc(libAsset.body||'（还没有正文）')}</div>
        <h3 style="margin-top:24px">来源</h3><div class="draft-sources">${esc(libAsset.facts||'（没有来源记录）')}</div>
        ${(meta.notes||[]).length?`<h3 style="margin-top:24px">生成说明</h3><ul class="gen-notes">${meta.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`:''}
        ${(meta.warnings||[]).length?`<ul class="gen-notes warn">${meta.warnings.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`:''}
        <h3 style="margin-top:24px">质量检查</h3>${qualityHtml}${warnings}
        <p class="muted">有效正文约 ${q.plain_length||0} 字；最低要求 ${q.minimum_length||0} 字。审核与发布前都会重新检查。</p>
        <div class="toolbar" style="margin-top:20px"><a class="button primary" href="${link('library',chosen.slug)+'&asset='+libAsset.id+'&mode=edit'}">编辑</a><a class="button" href="${link('publications',chosen.slug)}">发布安排</a><button type="button" class="btn-quiet" id="lib-del">删除</button></div>
       </section>`;
      $('#lib-del').onclick=()=>removeAsset(libRow.id?libRow:{id:libAsset.id,title:libAsset.title});
     }else{
      $('#module').innerHTML=`${head}
       <form id="lib-form" class="panel">
        <h2>编辑初稿</h2>
        <label>标题<input name="title" required maxlength="200" value="${esc(libAsset.title)}"></label>
        <label>摘要<textarea name="summary" rows="3">${esc(libAsset.summary||'')}</textarea></label>
        <label>正文<textarea name="body" style="min-height:420px">${esc(libAsset.body||'')}</textarea></label>
        <h3 style="margin-top:24px">来源（由素材与项目资料自动带出，只读）</h3><div class="draft-sources">${esc(libAsset.facts||'（没有来源记录）')}</div>
        <h3 style="margin-top:24px">生成说明（不属于正文）</h3><ul class="gen-notes">${(meta.notes||[]).length?meta.notes.map(n=>`<li>${esc(n)}</li>`).join(''):'<li class="muted">这份内容还没有生成过，或是早期版本生成的（没有记录说明）。</li>'}</ul>
        ${(meta.warnings||[]).length?`<ul class="gen-notes warn">${meta.warnings.map(n=>`<li>${esc(n)}</li>`).join('')}</ul>`:''}
        <h3 style="margin-top:24px">质量检查</h3>${qualityHtml}${warnings}
        <p class="muted">有效正文约 ${q.plain_length||0} 字；最低要求 ${q.minimum_length||0} 字。保存后原审核会失效。</p>
        <div class="toolbar" style="margin-top:20px"><button class="primary">保存草稿</button><button type="button" id="lib-review" ${q.passed?'':'disabled'}>审核当前版本</button><a class="button" href="${link('publications',chosen.slug)}">发布安排</a><button type="button" id="lib-regen">重新生成初稿</button><a class="button" href="${link('content',chosen.slug)+'&asset='+libAsset.id}">回任务书与素材</a></div>
       </form>`;
      const form=$('#lib-form');
      const values=()=>{const o=Object.fromEntries(new FormData(form));return {title:o.title,summary:o.summary,body:o.body,revision:libAsset.revision};};
      form.onsubmit=async e=>{e.preventDefault();try{const out=await api(base+'/content/'+libAsset.id,'PUT',values());await render();notice(`已保存（版本 ${out.revision}），原审核已失效`);}catch(err){notice(err.message);}};
      $('#lib-review').onclick=()=>{const v=values();const dirty=['title','summary','body'].some(k=>String(v[k]||'')!==String(libAsset[k]||''));if(dirty){notice('请先保存修改并重新通过质量检查');return;}modal('审核内容','<label>审核人<input name="reviewer" required></label><label class="check"><input type="checkbox" required>已逐条核对正文、事实依据及公开范围</label>',async data=>{await api(base+'/content/'+libAsset.id+'/review','POST',{reviewer:data.get('reviewer'),revision:libAsset.revision,confirm:true});},'审核通过');};
      $('#lib-regen').onclick=()=>modal('确认重新生成','<p>重新生成会覆盖当前标题、摘要和正文。任务书与素材会保留。</p><label class="check"><input type="checkbox" required>我已确认覆盖当前稿件</label>',async()=>{const out=await api(base+'/content/'+libAsset.id+'/generate','POST',{revision:libAsset.revision,confirm_overwrite:true});return {message:`已重新生成（约 ${(out.quality||{}).plain_length||0} 字，引擎 ${out.engine}）`};},'确认覆盖并生成');
     }
    }

   } else {
    // ===== 批量发布 =====
    // 版式来自原型 v3：准入条 → 双页签 → 内容/平台双栏 → 贴底提交条 → 记录页签。
    // 判定规则与 publish_adapters.Adapter.publish 一致：先查内容要素，再看平台能不能自动发。
    const caps=(await api('/api/platforms')).publication;if(token!==epoch)return;
    const loginState=await api('/api/platforms/login-state');if(token!==epoch)return;
    const assets=(await api(base+'/editorial/content')).items;if(token!==epoch)return;
    const jobs=(await api(base+'/editorial/publications')).items;if(token!==epoch)return;
    const pubTab=params.get('tab')==='history'?'history':'prep';
    const pubBase=link('publications',chosen.slug);

    // 登录态与平台能力是「会变」的，放进 LIVE 里，轮询只重画不看重建 DOM
    const LIVE={caps,loginState};
    const elemLabel={title:'标题',body:'正文',facts:'事实依据',cover:'封面图'};
    const status4={draft_created:['已写入草稿箱','good'],submitted:['已提交','good'],
      published_manual:['人工已回填','good'],manual_required:['转人工','warn'],failed:['失败','bad']};
    // 勾选状态存在模块级，跨页签与轮询都不丢
    const picked=new Set(pubPicked),plats=new Set(pubPlats);
    const keep=()=>{pubPicked=[...picked];pubPlats=[...plats];};

    const approved=assets.filter(a=>a.status==='approved');
    const drafts=assets.filter(a=>a.status!=='approved');
    const platById=id=>LIVE.caps.find(p=>p.id===id);
    const lState=id=>(LIVE.loginState.states||{})[id]||{};
    const isLogged=id=>lState(id).state==='logged_in';
    const credsReady=p=>(p.credentials||[]).every(c=>c.configured);
    // 一个平台自己能不能自动发。api 看凭据，browser 看登录态，manual 无接口。
    const platState=p=>p.mode==='manual'?{text:'无自动接口',tone:'grey'}
      :p.mode==='api'?(credsReady(p)?{text:'可直发',tone:'good'}:{text:'待配凭据',tone:''})
      :(isLogged(p.id)?{text:'可直发',tone:'good'}:{text:'需登录',tone:''});
    const missingOf=(a,p)=>(p.requires||[]).filter(k=>k==='cover'?true:!String(a[k]??'').trim());
    // 单对「内容 × 平台」的结果。短说与长说共用词根。
    const pairAction=(a,p)=>{
      if(p.mode==='manual')return{kind:'manual',reason:'无自动接口，发布后回填链接',short:'无自动接口'};
      const miss=missingOf(a,p);
      if(miss.length)return{kind:'manual',reason:'缺'+miss.map(k=>elemLabel[k]||k).join('、')+'，无法自动发布',short:'缺'+miss.map(k=>elemLabel[k]||k).join('、')};
      if(p.mode==='api'&&!credsReady(p))return{kind:'manual',reason:'待配凭据：需要 AppID/AppSecret',short:'待配凭据'};
      if(p.mode==='browser'&&!isLogged(p.id))return{kind:'manual',reason:'该平台未登录',short:'未登录'};
      return{kind:'auto',reason:p.mode==='api'?'写入公众号草稿箱':'浏览器自动化创建',short:''};
    };
    const pairsNow=()=>{
      const out=[];
      approved.filter(a=>picked.has(a.id)).forEach(a=>
        LIVE.caps.filter(p=>plats.has(p.id)).forEach(p=>out.push({a,p,act:pairAction(a,p)})));
      return out;
    };
    const incompleteTitle=t=>/^\s*(待填写|补充问题)/.test(t)||(t.length>34&&!/[。？?！!）)]$/.test(t));
    const sourceLabel=a=>a.source==='manual'?'直接创建':'来自诊断';

    // ---- ① 准入条 ----
    const gateCounts=()=>{
      const ps=LIVE.caps;
      return {
        ready:ps.filter(p=>platState(p).tone==='good').length,
        login:ps.filter(p=>p.mode==='browser'&&!isLogged(p.id)).length,
        creds:ps.filter(p=>p.mode==='api'&&!credsReady(p)).length,
        manual:ps.filter(p=>p.mode==='manual').length,
      };
    };
    const paintGate=()=>{
      const c=gateCounts(),s=LIVE.loginState.session||{};
      const note=s.active?'登录窗口已打开，正在自动识别登录态（每 3 秒刷新，不用手动点）…'
        :c.ready?'有 '+c.ready+' 个平台可以直接发布。':'现在没有平台能直发。先登录，或先配公众号凭据。';
      const el=$('#gate');if(!el)return;
      el.innerHTML=`<section class="gate" aria-labelledby="gate-h">
        <h2 class="sr-only" id="gate-h">发布环境准入</h2>
        <div class="gate-top">
          <ul class="gate-facts">
            <li><span>可直发</span><strong class="${c.ready?'good':'warn'}">${c.ready}/${LIVE.caps.length}</strong></li>
            <li><span>需登录</span><strong>${c.login}</strong></li>
            <li><span>待配凭据</span><strong>${c.creds}</strong></li>
            <li><span>无自动接口</span><strong>${c.manual}</strong></li>
          </ul>
          <div class="toolbar" style="margin:0">
            <button id="probe-login">重新检测</button>
            <button id="login" class="primary">打开登录窗口</button>
          </div>
        </div>
        <p class="gate-note">${esc(note)}</p>
        <details class="gate-details">
          <summary>平台明细（登录态、凭据、必需内容要素）</summary>
          <div class="table-scroll"><table>
            <thead><tr><th>平台</th><th>执行方式</th><th>当前状态</th><th>必需内容要素</th><th>操作</th></tr></thead>
            <tbody id="login-rows"></tbody>
          </table></div>
        </details>
      </section>`;
      paintLoginTable();
      $('#login').onclick=()=>openLogin(LIVE.caps.filter(p=>p.mode==='browser').map(p=>p.id));
      $('#probe-login').onclick=async()=>{try{
        const d=await api('/api/platforms/login-state/probe','POST',{platforms:LIVE.caps.filter(p=>p.mode==='browser').map(p=>p.id)});
        if(token!==epoch)return;LIVE.loginState=d;paintGate();paintChips();paintBar();
        const s=d.summary||{};notice(`重新检测完成：已登录 ${s.logged_in||0}，需登录 ${s.needs_login||0}，未检测 ${s.unknown||0}`);
      }catch(err){notice(err.message);}};
    };
    const paintLoginTable=()=>{
      const tb=$('#login-rows');if(!tb)return;
      tb.innerHTML=LIVE.caps.map(p=>{
        const st=platState(p),s=lState(p.id);
        const detail=p.mode==='api'?(credsReady(p)?'凭据已配置；发布时校验权限与 IP 白名单':'未配置 '+p.credentials.map(c=>c.name).join('、'))
          :p.mode==='browser'?esc(s.note||'还没有检测过登录态'):'该平台没有自动发布接口';
        const op=p.mode==='api'?`<button class="btn-quiet" data-cred="${p.id}">${credsReady(p)?'修改凭据':'填写凭据'}</button> <button class="btn-quiet" data-checkcred="${p.id}">校验</button>`
          :p.mode==='browser'?`<button class="btn-quiet" data-login-one="${p.id}">只开这一个</button>`:'—';
        return `<tr><td>${esc(p.label)}</td><td>${esc(p.mode_label)}</td>
          <td><span class="chip ${st.tone}">${esc(st.text)}</span></td>
          <td class="muted">${(p.requires||[]).map(r=>esc(elemLabel[r]||r)).join('、')}</td>
          <td class="muted">${detail}</td><td>${op}</td></tr>`;
      }).join('');
      document.querySelectorAll('[data-login-one]').forEach(b=>b.onclick=()=>openLogin([b.dataset.loginOne]));
      document.querySelectorAll('[data-cred]').forEach(b=>b.onclick=()=>modal('配置公众号凭据',
        '<p class="muted">只保存在本机数据库，接口不会回显明文。需要该公众号已认证并开通草稿接口权限，且本机公网 IP 在白名单内。</p><label>AppID<input name="appid" required placeholder="wx 开头的 AppID"></label><label>AppSecret<input name="secret" type="password" required></label>',
        async f=>{const r=await api('/api/platforms/'+b.dataset.cred+'/credentials','PUT',Object.fromEntries(f));
          const d=await api('/api/platforms');LIVE.caps=d.publication;paintGate();paintChips();paintBar();notice(r.message);},'保存'));
      document.querySelectorAll('[data-checkcred]').forEach(b=>b.onclick=async()=>{try{
        const r=await api('/api/platforms/'+b.dataset.checkcred+'/credentials/check','POST',{});notice(r.message||'凭据可用');
      }catch(err){notice(err.message);}});
    };

    // ---- ② 页签 ----
    const paintIndicator=(animate)=>{
      const box=$('#tabs'),bar=box&&box.querySelector('.tabs-indicator'),cur=box&&box.querySelector('a[aria-current]');
      if(!box||!bar||!cur)return;
      const place=()=>{bar.style.width=cur.offsetWidth+'px';bar.style.transform='translateX('+cur.offsetLeft+'px)';};
      if(!animate){bar.style.transition='none';place();requestAnimationFrame(()=>{bar.style.transition='';});}
      else place();
      box.classList.add('has-indicator');bar.classList.add('is-ready');
      pubTabGeo={w:bar.style.width,t:bar.style.transform};
    };
    const renderTabs=()=>{
      const box=$('#tabs');if(!box)return;
      // 一个页签只带一个计数角标。之前把「待处理 N」也塞进页签里，它看起来像个能点的按钮，
      // 实际是链接里的 <span>，点了只会重跳当前页签。待处理现在移进发布记录的工具条做筛选。
      box.innerHTML=`
        <a href="${pubBase}" ${pubTab==='prep'?'aria-current="page"':''}>准备发布 <span class="tabs-count">${approved.length}</span></a>
        <a href="${pubBase}&tab=history" ${pubTab==='history'?'aria-current="page"':''}>发布记录 <span class="tabs-count">${jobs.length}</span></a>
        <span class="tabs-indicator" aria-hidden="true"></span>`;
      const bar=box.querySelector('.tabs-indicator');
      if(pubTabGeo){ // 先把条摆回上一次的位置，下一帧再动到新位置，才是「滑过去」而不是「从左边缘滑入」
        bar.style.transition='none';bar.style.width=pubTabGeo.w;bar.style.transform=pubTabGeo.t;
        bar.classList.add('is-ready');box.classList.add('has-indicator');
        requestAnimationFrame(()=>{bar.style.transition='';paintIndicator(true);});
      }else paintIndicator(false);
    };

    // ---- ③ 准备发布 ----
    const paintChips=()=>{
      const el=$('#col-platforms');if(!el)return;
      LIVE.caps.forEach(p=>{
        const chip=el.querySelector('[data-platform-chip="'+p.id+'"]');if(!chip)return;
        const s=platState(p),dot=chip.querySelector('.st'),sr=chip.querySelector('.sr-only');
        if(dot)dot.className='st '+s.tone;
        if(sr)sr.textContent=s.text;
        chip.classList.toggle('picked',plats.has(p.id));
        const cb=chip.querySelector('input');if(cb)cb.checked=plats.has(p.id);
      });
    };
    const paintBar=()=>{
      const el=$('#submitbar');if(!el)return;
      const ps=pairsNow(),manual=ps.filter(x=>x.act.kind==='manual');
      const tally=new Map();manual.forEach(x=>tally.set(x.act.short,(tally.get(x.act.short)||0)+1));
      const breakdown=[...tally].map(([k,n])=>`${n} 条${k}`).join('、');
      const aN=approved.filter(a=>picked.has(a.id)).length,pN=plats.size;
      const head=ps.length
        ? `已选 <strong>${aN}</strong> 篇 × <strong>${pN}</strong> 个平台 = <strong>${ps.length}</strong> 条发布<span class="dot"> · </span>${manual.length?`其中 <strong>${manual.length}</strong> 条会转人工`:'<strong class="good">全部可以直发</strong>'}${breakdown?`<span class="dot"> · </span><span style="font-size:var(--fs-note)">${esc(breakdown)}</span>`:''}`
        :aN&&!pN?`已选 <strong>${aN}</strong> 篇内容，还没选平台。`
        :pN&&!aN?`已选 <strong>${pN}</strong> 个平台，还没选内容。`
        :'还没有选内容或平台。';
      el.innerHTML=`<p class="submit-facts" style="margin:0">${head}</p>
        <button class="primary" id="publish" ${ps.length?'':'disabled'}>${ps.length?`发布 ${ps.length} 条`:'发布'}</button>`;
      $('#live').textContent=ps.length
        ?`已选 ${aN} 篇内容、${pN} 个平台，共 ${ps.length} 条发布，其中 ${manual.length} 条转人工。${breakdown}。`
        :'还没有选内容或平台。';
      $('#publish').onclick=()=>{
        const snap=pairsNow();if(!snap.length)return;
        const platN=new Set(snap.map(x=>x.p.id)).size;
        const manualN=snap.filter(x=>x.act.kind==='manual').length,autoN=snap.length-manualN;
        const buckets=new Map();
        snap.forEach(x=>{const k=x.act.kind+'|'+x.act.reason;
          const g=buckets.get(k)||{kind:x.act.kind,reason:x.act.reason,plats:new Set(),n:0};
          g.plats.add(x.p.label);g.n+=1;buckets.set(k,g);});
        const rows=[...buckets.values()].map(g=>`<tr><td>${[...g.plats].map(esc).join('、')}</td><td>${g.n} 篇</td>
          <td><span class="badge ${g.kind==='auto'?'good':'warn'}">${g.kind==='auto'?'直接发布':'转人工'}</span></td>
          <td class="muted">${esc(g.reason)}</td></tr>`).join('');
        modal(`发布 ${snap.length} 条到 ${platN} 个平台`,
          `<p class="muted" style="margin:0">确认后立即执行，不会再问第二次。</p>
           <p style="margin:16px 0 12px;font-size:14px"><strong>${autoN}</strong> 条直接执行，<strong>${manualN}</strong> 条转人工。</p>
           <div class="table-scroll"><table><thead><tr><th>平台</th><th>篇数</th><th>结果</th><th>原因</th></tr></thead><tbody>${rows}</tbody></table></div>
           ${manualN?'<p class="muted" style="margin-top:18px">转人工的条目不会自动发出去，需要你到对应平台手动完成，再回来回填链接。</p>':''}
           <label class="check" style="margin-top:18px"><input type="checkbox" required>内容已审核，同意按上述方式发布</label>`,
          async()=>{
            const now=pairsNow();
            const sig=l=>l.map(x=>`${x.a.id}|${x.p.id}|${x.act.kind}|${x.act.reason}`).join(';');
            if(sig(now)!==sig(snap)){ // 弹窗开着的时候登录态可能刚刷新，方案已过期就不发
              return {message:now.length!==snap.length?`选中范围变了（开弹窗时 ${snap.length} 条，现在 ${now.length} 条）。本次没有发布，请再看一遍。`
                :'平台状态在确认期间刷新了，结果和弹窗里看到的不一样。本次没有发布，请再看一遍。'};
            }
            const r=await api(base+'/publish','POST',{asset_ids:[...picked],platforms:[...plats],confirm:true,operator:''});
            const ok=r.results.filter(x=>['draft_created','submitted'].includes(x.status)).length;
            const man=r.results.filter(x=>x.status==='manual_required').length;
            const bad=r.results.filter(x=>x.status==='failed').length;
            return {message:`已执行 ${r.results.length} 条：成功 ${ok} 条，转人工 ${man} 条，失败 ${bad} 条。到「发布记录」看每一条的结果。`};
          },'发布 '+snap.length+' 条');
      };
    };
    const renderPrep=()=>{
      $('#pane').innerHTML=`
      <div class="prep-grid">
        <section class="pick-col" id="col-assets" aria-labelledby="pick-assets-h">
          <div class="pick-head">
            <h2 id="pick-assets-h">内容 <span class="tabs-count" id="assets-count">${picked.size} 篇已选</span></h2>
            <div class="pick-head-right">
              <label class="check"><input type="checkbox" id="all-assets">全选</label>
              <button type="button" class="linkish" id="clear-assets">清空</button>
            </div>
          </div>
          <div class="pick-list">${approved.map(a=>`
            <div class="pick-row" data-asset-row="${a.id}">
              <input type="checkbox" name="pick-asset" id="asset-${a.id}" value="${a.id}" ${picked.has(a.id)?'checked':''}>
              <div>
                <label class="pick-title" for="asset-${a.id}" title="${esc(a.title)}">${esc(a.title)}</label>
                <p class="pick-meta"><span>${esc(sourceLabel(a))}</span><span class="dot">·</span><span>v${a.revision}</span><span class="dot">·</span><span>${esc(localTime(a.updated_at).slice(0,10))}</span>
                  ${incompleteTitle(a.title)?'<span class="chip bad">标题没写完</span>':''}</p>
              </div>
              <button type="button" class="btn-quiet" data-preview="${a.id}">预览</button>
            </div>`).join('')}</div>
          ${drafts.length?`<p class="pick-list-tail">另有 ${drafts.length} 篇未审核，审核后才能发。</p>`:''}
        </section>
        <section class="pick-col" id="col-platforms" aria-labelledby="pick-platforms-h">
          <div class="pick-head">
            <h2 id="pick-platforms-h">平台 <span class="tabs-count" id="platforms-count">${plats.size} 个已选</span></h2>
            <div class="pick-head-right">
              <label class="check"><input type="checkbox" id="all-platforms">全选</label>
              <button type="button" class="linkish" id="clear-platforms">清空</button>
            </div>
          </div>
          <div class="chips">${LIVE.caps.map(p=>{const s=platState(p);return `
            <label class="pick-chip" data-platform-chip="${p.id}">
              <input type="checkbox" name="pick-platform" value="${p.id}" ${plats.has(p.id)?'checked':''}>
              ${esc(p.label)}<span class="st ${s.tone}" aria-hidden="true"></span><span class="sr-only">${esc(s.text)}</span>
            </label>`;}).join('')}</div>
          <p class="chip-legend"><span><i></i>可直发</span><span><i class="warn"></i>需登录或配凭据</span><span><i class="grey"></i>无自动接口</span></p>
          <p class="chip-note">红点不表示平台坏了，是「现在还不能自动发」。<strong>小红书需要封面图，当前还没有封面图字段，所以它一定会转人工。</strong></p>
        </section>
      </div>
      <div class="submit-bar" id="submitbar"></div>`;
      if(!approved.length) $('#col-assets .pick-list').innerHTML='<p class="pick-list-tail">还没有已审核内容。到「内容生产」写完并审核后再来。</p>';
      const sync=()=>{
        approved.forEach(a=>{const b=$('#asset-'+a.id);if(b)b.checked=picked.has(a.id);});
        LIVE.caps.forEach(p=>{const b=$('#col-platforms input[value="'+p.id+'"]');if(b)b.checked=plats.has(p.id);});
      };
      $('#all-assets').onchange=e=>{approved.forEach(a=>e.target.checked?picked.add(a.id):picked.delete(a.id));keep();sync();paintAll();};
      $('#clear-assets').onclick=()=>{picked.clear();keep();sync();paintAll();};
      $('#all-platforms').onchange=e=>{LIVE.caps.forEach(p=>e.target.checked?plats.add(p.id):plats.delete(p.id));keep();sync();paintAll();};
      $('#clear-platforms').onclick=()=>{plats.clear();keep();sync();paintAll();};
      $('#col-assets').addEventListener('change',e=>{if(e.target.name!=='pick-asset')return;
        e.target.checked?picked.add(e.target.value):picked.delete(e.target.value);keep();paintAll();});
      $('#col-platforms').addEventListener('change',e=>{if(e.target.name!=='pick-platform')return;
        e.target.checked?plats.add(e.target.value):plats.delete(e.target.value);keep();paintAll();});
      document.querySelectorAll('[data-preview]').forEach(b=>b.onclick=()=>{
        const a=assets.find(x=>x.id===b.dataset.preview);
        modal('内容预览',
          `<p class="muted" style="margin:0 0 14px">${esc(sourceLabel(a))} · v${a.revision} · 最后修改 ${esc(localTime(a.updated_at))} · ${a.facts?'已填事实依据':'未填事实依据'} · ${a.status==='approved'?'已审核':'未审核'}</p>
           ${incompleteTitle(a.title)?'<p class="gate-note" style="margin:0 0 14px;color:var(--notice-ink)">标题看起来还没写完。发出去标题就是这个。</p>':''}
           <div class="stage"><div class="stage-body">${md(a.body||'（正文为空）')}</div><p class="stage-status">${esc(a.title.slice(0,60))}</p></div>`,
          ()=>{},'关闭');
      });
      paintAll();
    };
    // 勾选态与计数一起刷新。不重建 DOM，键盘焦点不会丢。
    function paintAll(){
      document.querySelectorAll('[data-asset-row]').forEach(r=>r.classList.toggle('picked',picked.has(r.dataset.assetRow)));
      const ac=$('#assets-count'),pc=$('#platforms-count');
      if(ac)ac.textContent=picked.size+' 篇已选';
      if(pc)pc.textContent=plats.size+' 个已选';
      const tri=(box,on,total)=>{if(!box)return;box.checked=total>0&&on===total;box.indeterminate=on>0&&on<total;};
      tri($('#all-assets'),approved.filter(a=>picked.has(a.id)).length,approved.length);
      tri($('#all-platforms'),LIVE.caps.filter(p=>plats.has(p.id)).length,LIVE.caps.length);
      paintBar();
    }

    // ---- ④ 发布记录 ----
    const renderHistory=()=>{
      const open=j=>j.status==='manual_required'||j.status==='failed';
      const nOpen=jobs.filter(open).length;
      // 「待处理」是个真按钮：切到只看待处理，不再是个看得见点不动的角标。
      const shown=recOnlyPending?jobs.filter(open):jobs;
      const groups=[];
      shown.forEach(j=>{const g=groups.find(x=>x.title===j.title_snapshot);
        g?g.items.push(j):groups.push({title:j.title_snapshot,items:[j]});});
      const filterBtn=nOpen?`<button type="button" id="rec-filter" aria-pressed="${recOnlyPending}">${recOnlyPending?`显示全部（${jobs.length}）`:`只看待处理（${nOpen}）`}</button>`:'';
      $('#pane').innerHTML=`<section class="pick-col" style="margin-bottom:var(--sp-6)">
        <div class="rec-head">
          <div><h2>发布记录</h2><p>每条内容 × 平台的结果与原因，按内容分组。共 ${jobs.length} 条，待处理 ${nOpen} 条。${recOnlyPending?'（当前只显示待处理）':''}</p></div>
          <div class="toolbar" style="margin:0">${filterBtn}</div>
        </div>
        ${groups.map(g=>`<div class="rec-group">
          <div class="rec-group-title"><strong>${esc(g.title)}</strong><span class="muted" style="font-size:var(--fs-meta)">${g.items.length} 个平台</span></div>
          ${g.items.map(j=>{const s=status4[j.status]||[j.status,''];const p=platById(j.platform);
            return `<div class="rec-item">
              <div class="rec-item-main"><span class="badge ${s[1]}">${esc(s[0])}</span> <strong style="font-size:var(--fs-note)">${esc(p?p.label:j.platform)}</strong>
                ${j.adapter_note?`<small>${esc(j.adapter_note)}</small>`:''}
                ${j.receipt_url?`<small><a href="${esc(j.receipt_url)}">${esc(j.receipt_url)}</a></small>`:''}
                <small>v${j.asset_revision} · ${esc(localTime(j.updated_at))}${j.operator?' · '+esc(j.operator):''}</small></div>
              <div>${open(j)?`<button type="button" class="btn-quiet" data-receipt="${j.id}">回填链接</button>`:''}</div>
            </div>`;}).join('')}
        </div>`).join('')||(jobs.length?'<p class="empty-block">没有待处理的发布记录。</p>':'<p class="empty-block">还没有发布记录。到「准备发布」勾选内容和平台。</p>')}</section>`;
      const toggle=$('#rec-filter');
      if(toggle)toggle.onclick=()=>{recOnlyPending=!recOnlyPending;renderHistory();};
      document.querySelectorAll('[data-receipt]').forEach(b=>b.onclick=()=>modal('回填发布链接',
        '<p class="muted">先去该平台完成发布，再把页面链接回填到这里。</p><label>发布链接<input name="url" type="url" required placeholder="https://"></label><label>操作人<input name="operator" required></label><label class="check"><input type="checkbox" required>确认已实际发布</label>',
        f=>api(base+'/publishing/'+b.dataset.receipt+'/receipt','POST',{...Object.fromEntries(f),confirm:true}),'保存回执'));
    };

    // ---- 登录窗口与轮询 ----
    let loginTimer=null;
    const openLogin=async ids=>{
      if(loginTimer){clearTimeout(loginTimer);loginTimer=null;}
      try{
        const d=await api('/api/platforms/login','POST',{platforms:ids});
        if(token!==epoch)return;LIVE.loginState=d;paintGate();paintChips();paintBar();
        notice(d.message||'已打开登录窗口');
      }catch(err){notice(err.message);return;}
      loginTimer=setTimeout(watchLogin,2000);
    };
    const watchLogin=async()=>{
      if(token!==epoch)return;
      try{
        const d=await api('/api/platforms/login-state');if(token!==epoch)return;
        const before=LIVE.loginState.states||{};
        const flipped=Object.keys(d.states||{}).filter(k=>d.states[k].state==='logged_in'&&before[k]&&before[k].state!=='logged_in');
        LIVE.loginState=d;paintGate();paintChips();paintBar();
        const s=d.session||{};
        if(flipped.length)notice('已识别登录成功：'+flipped.map(k=>(platById(k)||{}).label||k).join('、'));
        else if(!s.active&&s.result==='completed'&&s.message)notice(s.message);
        loginTimer=s.active?setTimeout(watchLogin,3000):null;
      }catch(err){loginTimer=setTimeout(watchLogin,5000);}
    };

    // ---- 装配 ----
    $('#module').innerHTML='<div id="gate"></div><div id="tabs" class="tabs"></div><div id="pane"></div>';
    paintGate();
    renderTabs();
    if(pubTab==='history')renderHistory();else renderPrep();
    if((LIVE.loginState.session||{}).active)loginTimer=setTimeout(watchLogin,2000);
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
