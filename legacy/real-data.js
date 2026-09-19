(() => {
  const API_BASE = window.GEO_API_BASE || 'http://127.0.0.1:8798';
  const page = document.body.dataset.page || '';
  let project = null;
  let publicationState = null;

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = value => String(value ?? '').replace(/[&<>\"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[c]));

  function status(text, kind = 'info') {
    let bar = $('#real-data-status');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'real-data-status';
      bar.style.cssText = 'margin:0 0 18px;padding:10px 13px;border-radius:8px;font-size:12px;border:1px solid #d8e5e8;background:#eef8fa;color:#176b88;';
      $('.content')?.prepend(bar);
    }
    bar.textContent = text;
    bar.style.background = kind === 'error' ? '#fff1f1' : '#eef8fa';
    bar.style.color = kind === 'error' ? '#b84b4b' : '#176b88';
    bar.style.borderColor = kind === 'error' ? '#f0caca' : '#cfe8ec';
  }

  async function api(path, options = {}) {
    const response = await fetch(API_BASE + path, { headers: {'Content-Type': 'application/json'}, ...options });
    let data = null;
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) throw new Error(data?.detail || data?.message || `接口请求失败（${response.status}）`);
    return data;
  }

  async function bootstrap() {
    try {
      const projects = await api('/api/projects');
      const slug = new URLSearchParams(location.search).get('project');
      if (page === 'dashboard') { status(`新版独立数据已连接 · 数据库：redesign.db · 后端：${API_BASE}`); await renderDashboard(projects.projects); return; }
      project = projects.projects.find(x => x.slug === slug) || projects.projects[0];
      if (!project) { status(`新版工作空间暂无项目。请先创建项目；原版本数据不会被读取。`, 'info'); if ($('.content')) $('.content').innerHTML = '<section class="card" style="padding:24px"><h2>还没有新版项目</h2><p>这里不会显示原版本或演示数据。</p><a class="btn" href="new-project.html">创建第一个项目</a></section>'; return; }
      document.body.dataset.project = project.slug;
      $('.crumb strong') && ($('.crumb strong').textContent = project.name);
      status(`新版独立数据已连接 · 项目：${project.name} · 后端：${API_BASE}`);
      if (page === 'content') await renderContent(project);
      if (page === 'publish') await renderPublication(project);
    } catch (error) {
      status(`真实数据连接失败：${error.message}。请确认后端已按 README 启动。`, 'error');
    }
  }

  async function renderDashboard(projects) {
    const stateCard = $('#workspace-state');
    const next = $('#next-step');
    const tbody = $('.workspace-table .table tbody');
    if (!projects.length) {
      if (stateCard) stateCard.innerHTML = '<h2>全新工作空间</h2><p>当前没有项目，也没有读取原版本数据。</p><div class="state-line"><div><strong>项目数量</strong><span>独立数据库</span></div><span>0 个</span></div><div class="state-line"><div><strong>当前状态</strong><span>等待第一个项目</span></div><span class="status warn">未开始</span></div>';
      if (next) next.innerHTML = '<h2>下一步</h2><div class="next-action"><span class="status warn">从零开始</span><strong>创建第一个项目</strong><p>项目创建后，数据只会写入新版独立数据库。</p><a class="btn" href="new-project.html">创建项目 →</a></div>';
      if (tbody) tbody.innerHTML = '<tr><td colspan="5">新版工作空间暂无项目。创建项目后会显示在这里。</td></tr>';
      return;
    }
    if (stateCard) stateCard.innerHTML = `<h2>新版工作空间</h2><p>项目之间相互独立，数据不来自原版本。</p><div class="state-line"><div><strong>项目数量</strong><span>新版独立数据库</span></div><span>${projects.length} 个</span></div><div class="state-line"><div><strong>当前入口</strong><span>从项目列表选择项目</span></div><a class="btn secondary small" href="projects.html">查看项目</a></div>`;
    if (next) next.innerHTML = '<h2>下一步</h2><div class="next-action"><span class="status good">工作空间已就绪</span><strong>选择项目或创建新项目</strong><p>首页不绑定任何单一项目；项目详情从项目页面进入。</p><a class="btn" href="projects.html">进入项目列表 →</a></div>';
    if (tbody) tbody.innerHTML = projects.map(p => `<tr><td><span class="project-name">${esc(p.name)}<small>${esc(p.target_type_label || '')}</small></span></td><td>尚未诊断</td><td><span class="status warn">未开始</span></td><td>开始项目配置</td><td><a class="btn secondary small" href="diagnosis.html?project=${encodeURIComponent(p.slug)}">进入</a></td></tr>`).join('');
  }

  async function renderContent(p) {
    const data = await api(`/api/projects/${encodeURIComponent(p.slug)}/content`);
    const assets = data.assets || [];
    const counts = {draft:0, generating:0, review:0, published:0};
    assets.forEach(a => { if (a.review_status === 'draft') counts.draft++; else if (a.review_status === 'needs_revision') counts.review++; else if (['approved','exported'].includes(a.review_status)) counts.published++; });
    $('.pipeline')?.remove();
    const kpis = $$('.kpi strong');
    if (kpis[0]) kpis[0].textContent = counts.draft;
    if (kpis[1]) kpis[1].textContent = counts.generating;
    if (kpis[2]) kpis[2].textContent = counts.review;
    if (kpis[3]) kpis[3].textContent = counts.published;
    $$('.kpi .trend').forEach(el => { el.textContent = '来自真实内容资产'; el.className = 'trend'; });
    const tbody = $('.module-page .full-card .table tbody');
    if (!tbody) return;
    tbody.innerHTML = assets.length ? assets.map(a => `<tr><td><span class="project-name">${esc(a.title)}<small>${esc(a.channel || '渠道待定')}</small></span></td><td>${esc(a.action_title || a.source_type || '手动')}</td><td>${esc((a.fact_ids || []).length)} 条</td><td><span class="status ${['approved','exported'].includes(a.review_status) ? 'good' : 'warn'}">${esc(a.review_status)}</span></td><td><span class="risk">${a.publish_blockers?.length ? esc(a.publish_blockers[0]) : '查看详情'}</span></td></tr>`).join('') : '<tr><td colspan="5">当前项目暂无内容资产，请先完成诊断任务或创建手动内容。</td></tr>';
  }

  function renderPlatformChoices(platforms) {
    const grid = $('.choice-grid');
    if (!grid) return;
    grid.innerHTML = platforms.filter(p => p.enabled).map(p => `<label class="platform-choice"><input type="checkbox" data-platform-id="${esc(p.id)}" onchange="updatePublishCount()">${esc(p.label)}</label>`).join('');
  }

  function renderHistory(publications) {
    const tbody = $('.history .table tbody');
    if (!tbody) return;
    tbody.innerHTML = publications.length ? publications.map(x => `<tr><td>${esc(x.created_at || '—')}</td><td><span class="project-name">${esc(x.asset_title || '内容资产')}<small>正文 v${esc(x.content_revision ?? '—')}</small></span></td><td>${esc(x.platform_label || x.channel || '—')}</td><td>${esc(x.operator || '未指定')}</td><td><span class="status ${x.status === 'online' ? 'good' : 'warn'}">${esc(x.status)}</span></td><td>${x.published_url ? `<a class="risk" href="${esc(x.published_url)}" target="_blank" rel="noopener">查看链接</a>` : esc(x.last_error || '待人工回填')}</td></tr>`).join('') : '<tr><td colspan="6">当前项目暂无发布历史。</td></tr>';
  }

  async function renderPublication(p) {
    publicationState = await api(`/api/projects/${encodeURIComponent(p.slug)}/publications`);
    const contents = publicationState.content || [];
    const select = $('#publish-content');
    if (select) {
      select.innerHTML = contents.length ? contents.map(a => `<option value="${a.id}">${esc(a.title)} · 正文 v${esc(a.revision)}</option>`).join('') : '<option value="">暂无可发布内容</option>';
      select.disabled = !contents.length;
    }
    renderPlatformChoices(publicationState.platforms || []);
    renderHistory(publicationState.publications || []);
    const pubs = publicationState.publications || [];
    const queue = $('.full-card .table tbody');
    if (queue) queue.innerHTML = pubs.length ? pubs.map(x => `<tr><td><span class="project-name">${esc(x.asset_title || '内容资产')}<small>正文 v${esc(x.content_revision ?? '—')}</small></span></td><td>${esc(x.platform_label || x.channel || '—')}</td><td>${esc(x.operator || '未指定')}</td><td>${esc(x.updated_at || x.created_at || '—')}</td><td><span class="status ${x.status === 'online' ? 'good' : 'warn'}">${esc(x.status)}</span></td><td>${esc(x.last_error || (x.published_url ? '已回填' : '待人工回填'))}</td></tr>`).join('') : '<tr><td colspan="6">当前项目暂无发布任务。</td></tr>';
    const kpis = $$('.kpi strong');
    const pending = pubs.filter(x => ['todo','submitted','review','manual_required','paused'].includes(x.status)).length;
    const online = pubs.filter(x => ['online','submitted'].includes(x.status)).length;
    const attention = pubs.filter(x => ['failed','rejected','manual_required','paused'].includes(x.status)).length;
    if (kpis[0]) kpis[0].textContent = pending;
    if (kpis[1]) kpis[1].textContent = pubs.length;
    if (kpis[2]) kpis[2].textContent = attention;
    if (kpis[3]) kpis[3].textContent = online;
    $$('.kpi .trend').forEach(el => { el.textContent = '来自真实发布任务'; el.className = 'trend'; });
    const count = $('#publish-count'); if (count) count.textContent = '0 个';
    if (!contents.length) status('真实数据已连接，但当前没有通过审核且可发布的内容。', 'error');
  }

  window.updatePublishCount = function () {
    const checked = $$('.platform-choice input:checked');
    const count = $('#publish-count'); if (count) count.textContent = `${checked.length} 个`;
    const target = $('#confirm-platforms'); if (target) target.textContent = checked.length ? checked.map(x => x.parentElement.textContent.trim()).join('、') : '尚未选择';
  };

  window.confirmPublish = async function () {
    const contentId = $('#publish-content')?.value;
    const platformIds = $$('.platform-choice input:checked').map(x => x.dataset.platformId).filter(Boolean);
    if (!contentId) return status('请先选择一条可发布内容。', 'error');
    if (!platformIds.length) return status('请至少选择一个发布平台。', 'error');
    if (!confirm(`确认创建 ${platformIds.length} 个平台的发布任务？创建任务不会自动对外发布。`)) return;
    try {
      const result = await api(`/api/projects/${encodeURIComponent(project.slug)}/publications/batch`, {method:'POST', body:JSON.stringify({confirm:true, content_asset_id:Number(contentId), platform_ids:platformIds, operator:'当前操作人'})});
      status(`${result.message} 发布任务状态为“待处理”，需要人工逐个平台发布并回填回执。`);
      await renderPublication(project);
    } catch (error) { status(`创建发布任务失败：${error.message}`, 'error'); }
  };

  bootstrap();
})();
