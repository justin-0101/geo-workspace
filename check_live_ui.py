"""Live-instance UI check on whatever real data exists. Read-only: no writes.

Deliberately data-agnostic: it reads the project list from the API instead of
hardcoding any subject name, so this file never carries business information.
"""
import json
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

API = 'http://127.0.0.1:8798'
OUT = Path(__file__).resolve().parent / 'test-results'
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(path):
    with NO_PROXY.open(API + path, timeout=20) as response:
        return json.loads(response.read().decode())


def main():
    checks, errors = [], []
    projects = api('/api/projects')['projects']
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe', headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.set_default_timeout(20000)
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto('http://127.0.0.1:4173/', wait_until='domcontentloaded')
            expect(page.get_by_role('link', name='工作台', exact=True)).to_be_visible()
            checks.append('workbench loads from live entry')

            page.get_by_role('link', name='诊断项目', exact=True).click()
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

                page.get_by_role('link', name='执行与结果', exact=True).click()
                expect(page.locator('#project-view')).to_be_visible()
                checks.append('run view reachable')

            page.get_by_role('link', name='模型与平台', exact=True).click()
            expect(page.get_by_role('heading', name='模型与平台')).to_be_visible()
            expect(page.get_by_text('DeepSeek').first).to_be_visible()
            checks.append('platform list reachable')
            assert not errors, errors
        finally:
            browser.close()
    result = {'checks': checks, 'page_errors': errors, 'writes_performed': 0}
    OUT.mkdir(exist_ok=True)
    (OUT / 'live-ui-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
