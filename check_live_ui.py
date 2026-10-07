"""Live-instance UI check on whatever real data exists. Read-only: no writes.

Deliberately data-agnostic: it reads the project list from the API instead of
hardcoding any subject name, so this file never carries business information.
"""
import json
import os
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from browser_paths import chrome_executable

# 默认打真实运行中的实例；跑临时实例验收时用环境变量覆盖（端口与截图目录）。
API = os.environ.get('GEO_LIVE_API') or 'http://127.0.0.1:8798'
PAGE_URL = os.environ.get('GEO_LIVE_PAGE') or 'http://127.0.0.1:4173/'
OUT = Path(os.environ.get('GEO_LIVE_OUT') or (Path(__file__).resolve().parent / 'test-results'))
OUT.mkdir(parents=True, exist_ok=True)
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path):
    with NO_PROXY.open(API + path, timeout=20) as response:
        return json.loads(response.read().decode())


def main():
    checks, errors = [], []
    projects = api('/api/projects')['projects']
    with sync_playwright() as pw:
        executable = chrome_executable()
        if executable is None:
            raise RuntimeError('Chrome/Chromium not found; set GEO_CHROME_PATH')
        browser = pw.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.set_default_timeout(20000)
            page.on('pageerror', lambda e: errors.append(str(e)))
            # 前端把 API 写死在 8798；指向临时实例时把请求改写过去（并补 CORS 头）。
            if API != 'http://127.0.0.1:8798':
                def forward(route):
                    url = route.request.url.replace('http://127.0.0.1:8798', API)
                    response = route.fetch(url=url)
                    route.fulfill(response=response,
                                  headers={**response.headers, 'access-control-allow-origin': '*'})
                page.route('http://127.0.0.1:8798/**', forward)
            page.goto(PAGE_URL, wait_until='domcontentloaded')

            def expect_page_lead(title, subtitle):
                expect(page.locator('.page-lead')).to_be_visible()
                expect(page.locator('.page-lead h1')).to_have_text(title)
                expect(page.locator('.page-lead p').first).to_have_text(subtitle)
                breadcrumb = page.locator('#breadcrumb')
                expect(breadcrumb.get_by_text('工作空间', exact=True)).to_be_visible()
                expect(breadcrumb).to_have_css('font-size', '13px')
                typography = breadcrumb.locator('a, [aria-current]').evaluate_all(
                    "els => els.map(e => { const s=getComputedStyle(e); return [s.fontFamily,s.fontSize,s.lineHeight,s.padding] })")
                assert len({tuple(x) for x in typography}) == 1, typography

            expect(page.get_by_role('link', name='工作台', exact=True)).to_be_visible()
            expect_page_lead('工作台', '集中查看待处理事项和最近活动。')
            checks.append('workbench loads with unified page lead')

            page.get_by_role('link', name='诊断项目', exact=True).click()
            expect_page_lead('诊断项目', '创建并管理 GEO 诊断项目。')
            if not projects:
                expect(page.get_by_text('还没有项目')).to_be_visible()
                checks.append('empty project list state')
            else:
                name = projects[0]['name']
                expect(page.get_by_role('link', name=name, exact=True)).to_be_visible()
                page.get_by_label('搜索项目').fill('不存在的项目名')
                expect(page.get_by_role('link', name=name, exact=True)).to_be_hidden()
                page.get_by_label('搜索项目').fill(name[:2])
                expect(page.get_by_role('link', name=name, exact=True)).to_be_visible()
                checks.append('project list and search')

                page.get_by_role('link', name=name, exact=True).click()
                expect_page_lead(name, '查看主体资料、诊断配置、执行过程与结果。')
                expect(page.get_by_role('link', name='概览', exact=True)).to_be_visible()
                overview = api('/api/projects/' + projects[0]['slug'] + '/overview')
                if overview.get('generated'):
                    expect(page.locator('.summary')).to_be_visible()
                    expect(page.locator('.hero-main strong')).to_contain_text('%')
                    expect(page.locator('.donut')).to_be_visible()
                    expect(page.locator('.qbars i, .chart svg polyline')).not_to_have_count(0)
                    expect(page.get_by_role('link', name='查看报告', exact=True)).to_be_visible()
                    page.screenshot(path=str(OUT / 'live-overview.png'), full_page=True)
                    checks.append('overview renders real GEO status')
                    page.get_by_role('link', name='查看报告', exact=True).click()
                    expect(page.locator('#report-text')).to_be_visible()
                    expect(page.locator('#report-text table, #report-text h3')).not_to_have_count(0)
                    checks.append('report renders inside the frame')
                    page.get_by_role('link', name='诊断项目', exact=True).click()
                    page.get_by_role('link', name=name, exact=True).click()
                else:
                    expect(page.get_by_text('还没有诊断数据')).to_be_visible()
                    checks.append('overview empty state before any run')

                page.get_by_role('link', name='主体资料', exact=True).click()
                expect(page.get_by_label('主体名称')).not_to_have_value('')
                checks.append('saved profile loads')

                page.get_by_role('link', name='诊断执行', exact=True).click()
                expect(page.locator('#project-view')).to_be_visible()
                checks.append('run view reachable')
                # 终态批次要能从执行页一步进到报告中心，并直接打开本次批次的诊断正文
                runs = api('/api/projects/' + projects[0]['slug']).get('runs') or []
                if runs and runs[0]['status'] in ('completed', 'degraded'):
                    report_link = page.get_by_role('link', name='查看诊断报告', exact=True)
                    expect(report_link).to_be_visible()
                    page.screenshot(path=str(OUT / 'live-run-report-entry.png'), full_page=True)
                    report_link.click()
                    expect(page.locator('#report-text')).to_be_visible()
                    expect(page.locator('#report-text table, #report-text h3')).not_to_have_count(0)
                    checks.append('completed run links to its diagnosis report')

            page.get_by_role('link', name='优化清单', exact=True).click()
            expect_page_lead('优化清单', '把诊断结论转成可执行的改善任务。')
            expect(page.locator('#scope')).to_be_visible()

            page.get_by_role('link', name='内容生产', exact=True).click()
            expect_page_lead('内容生产', '确认写作任务书与素材，生成初稿。')
            expect(page.get_by_role('button', name='＋ 新建内容任务书', exact=True)).to_be_visible()
            page.screenshot(path=str(OUT / 'live-content.png'), full_page=True)
            checks.append('actions and content use the unified page lead')

            # 生成任务页：只应有「写作任务书 + 素材」，不再有稿件与手填事实依据。
            first_row = page.locator('#module a.row').first
            if first_row.count():
                first_row.click()
                expect(page.locator('#asset-form')).to_be_visible()
                for heading in ('写作任务书', '素材'):
                    expect(page.get_by_role('heading', name=heading, exact=True)).to_be_visible()
                expect(page.locator('#asset-form [name="channel"]')).to_be_visible()
                expect(page.locator('#asset-form [name="audience"]')).to_be_visible()
                expect(page.locator('#asset-form [name="objective"]')).to_be_visible()
                expect(page.locator('#asset-form [name="brief"]')).to_be_visible()
                # 稿件与手填事实依据已按需求移除
                expect(page.locator('#asset-form [name="body"]')).to_have_count(0)
                expect(page.locator('#asset-form [name="facts"]')).to_have_count(0)
                expect(page.locator('#asset-form #generate')).to_be_visible()
                expect(page.get_by_role('button', name='保存任务书', exact=True)).to_be_visible()
                # 素材：上传附件 / 添加网址 / 粘贴文本三入口
                expect(page.locator('#asset-form #src-file')).to_have_count(1)
                expect(page.locator('#asset-form #src-url')).to_be_visible()
                expect(page.get_by_role('button', name='添加网址', exact=True)).to_be_visible()
                expect(page.get_by_role('button', name='粘贴文本', exact=True)).to_be_visible()
                expect(page.locator('#src-list')).to_be_visible()
                page.screenshot(path=str(OUT / 'live-content-editor.png'), full_page=True)
                checks.append('content editor exposes brief plus materials, no draft block')
                page.get_by_role('link', name='返回生成任务').click()
                expect(page.get_by_role('button', name='＋ 新建内容任务书', exact=True)).to_be_visible()

            # 内容库：一级菜单 + 列表（序号/标题/操作），数量跟 API 对齐
            library = api('/api/projects/' + projects[0]['slug'] + '/library')['items']
            page.get_by_role('link', name='内容库', exact=True).click()
            expect_page_lead('内容库', '查看、编辑与审核所有生成的初稿。')
            if library:
                expect(page.locator('tr[data-lib]')).to_have_count(len(library))
                first = page.locator('tr[data-lib]').first
                expect(first.locator('td').nth(0)).to_have_text(str(library[0]['index']))
                expect(first.get_by_role('link', name='查看', exact=True)).to_be_visible()
                expect(first.get_by_role('link', name='编辑', exact=True)).to_be_visible()
                expect(first.get_by_role('button', name='删除', exact=True)).to_be_visible()
                page.screenshot(path=str(OUT / 'live-library.png'), full_page=True)
                checks.append('library lists every item with index and actions')
            else:
                expect(page.get_by_text('内容库是空的')).to_be_visible()
                checks.append('empty library states itself')

            page.get_by_role('link', name='批量发布', exact=True).click()
            expect_page_lead('批量发布', '把已审核的内容一次投到多个平台。')
            # 逐项断言，不写「总行数」：多一张表就失效，已经因此白摔两次。
            # 数量一律跟 API 对齐，不写死数字，数据一变就红才是门禁该有的样子。
            caps = api('/api/platforms')['publication']
            login = api('/api/platforms/login-state')
            assets = api('/api/projects/' + projects[0]['slug'] + '/editorial/content')['items']
            approved = [a for a in assets if a['status'] == 'approved']

            expect(page.get_by_role('heading', name='批量发布', exact=True)).to_be_visible()
            expect(page.locator('.gate')).to_be_visible()
            expect(page.locator('.gate-facts li')).to_have_count(4)                 # 准入条四个数字
            expect(page.locator('#tabs a')).to_have_count(2)                        # 准备发布 / 发布记录
            expect(page.locator('#tabs a[aria-current]')).to_have_count(1)          # 只有一个当前页签
            checks.append('batch publish gate plus two tabs')

            expect(page.locator('[name="pick-platform"]')).to_have_count(len(caps))  # 平台芯片
            expect(page.locator('[name="pick-asset"]')).to_have_count(len(approved)) # 内容只列已审核的
            expect(page.locator('#submitbar')).to_be_visible()
            expect(page.get_by_role('button', name='发布', exact=True)).to_be_disabled()  # 没选时不可点
            checks.append('publish picks reflect api counts')

            page.locator('#module details summary').click()
            expect(page.locator('#module details table')).to_be_visible()
            expect(page.locator('#login-rows tr')).to_have_count(len(caps))          # 平台明细：一行一个平台
            wechat_row = page.locator('#login-rows tr').filter(has_text='微信公众号')
            expect(wechat_row).to_have_count(1)
            expect(wechat_row).to_contain_text('待配凭据')
            checks.append('platform detail table covers every interface')

            expect(page.get_by_role('button', name='打开登录窗口', exact=True)).to_be_visible()
            expect(page.get_by_role('button', name='重新检测', exact=True)).to_be_visible()

            # 选中之后：提交条要给条数，按钮要能点，文案要说出数量
            if approved:
                page.locator('[name="pick-asset"]').first.check()
            page.locator('[name="pick-platform"]').first.check()
            page.wait_for_timeout(300)
            if approved:
                expect(page.locator('.submit-facts')).to_contain_text('条发布')
                expect(page.locator('#submitbar button')).to_be_enabled()
                expect(page.locator('#submitbar button')).to_contain_text('发布 ')
                page.locator('#submitbar button').click()
                expect(page.locator('#modal-title')).to_contain_text('发布')
                expect(page.locator('#modal-body tbody tr')).not_to_have_count(0)
                page.locator('#cancel-modal').click()
            page.screenshot(path=str(OUT / 'live-publication.png'), full_page=True)
            checks.append('selection drives the submit bar and the confirm dialog')

            page.locator('#tabs a').filter(has_text='发布记录').click()
            expect(page.locator('#tabs a[aria-current]')).to_contain_text('发布记录')
            checks.append('history tab reachable')

            page.get_by_role('link', name='报告中心', exact=True).click()
            expect_page_lead('报告中心', '查看诊断报告、数据质量和原始数据。')
            expect(page.locator('#scope')).to_be_visible()

            page.get_by_role('link', name='模型与平台', exact=True).click()
            expect_page_lead('模型与平台', '查看诊断模型与发布平台的接入方式。')
            expect(page.get_by_text('DeepSeek').first).to_be_visible()

            page.get_by_role('link', name='设置', exact=True).click()
            expect_page_lead('设置', '设置工作空间名称和默认操作人。')
            expect(page.locator('#settings-form')).to_be_visible()
            checks.append('reports, platforms and settings use the unified page lead')
            assert not errors, errors
        finally:
            browser.close()
    result = {'checks': checks, 'page_errors': errors, 'writes_performed': 0}
    OUT.mkdir(exist_ok=True)
    (OUT / 'live-ui-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
