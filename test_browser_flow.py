"""Isolated browser acceptance. Does not send questions to external platforms."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'test-results'
OUT.mkdir(exist_ok=True)


def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]


def main():
    checks=[]; errors=[]
    with tempfile.TemporaryDirectory(prefix='geo-browser-test-') as tmp:
        apiport=port();webport=port();env=os.environ.copy();env['GEO_REDESIGN_DATA']=tmp
        with open(Path(tmp)/'api.log','w') as log:
            api=subprocess.Popen([sys.executable,'-m','uvicorn','workflow_api:app','--host','127.0.0.1','--port',str(apiport)],cwd=ROOT,env=env,stdout=log,stderr=log)
            web=subprocess.Popen([sys.executable,'-m','http.server',str(webport),'--bind','127.0.0.1'],cwd=ROOT,stdout=log,stderr=log)
            try:
                for _ in range(50):
                    try:
                        urllib.request.urlopen(f'http://127.0.0.1:{apiport}/api/projects',timeout=1);break
                    except Exception:time.sleep(.1)
                with sync_playwright() as pw:
                    browser=pw.chromium.launch(executable_path=r'C:\Program Files\Google\Chrome\Application\chrome.exe',headless=True)
                    context=browser.new_context(viewport={'width':1440,'height':1000})
                    # Actual API calls, rerouted only to the isolated server, never production.
                    def forward(route):
                        url=route.request.url.replace('http://127.0.0.1:8798',f'http://127.0.0.1:{apiport}')
                        response=route.fetch(url=url)
                        route.fulfill(response=response,headers={**response.headers,'access-control-allow-origin':'*'})
                    context.route('http://127.0.0.1:8798/**',forward)
                    page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                    page.set_default_timeout(12000)
                    page.goto(f'http://127.0.0.1:{webport}/workspace.html#workbench')
                    expect(page.get_by_text('暂无待处理事项')).to_be_visible();checks.append('empty workbench')
                    page.route('http://127.0.0.1:8798/api/settings',lambda route:route.abort())
                    page.get_by_role('link',name='设置',exact=True).click()
                    expect(page.get_by_role('button',name='重新加载',exact=True)).to_be_visible()
                    page.unroute('http://127.0.0.1:8798/api/settings')
                    page.get_by_role('button',name='重新加载',exact=True).click()
                    expect(page.get_by_label('工作空间名称')).to_be_visible();checks.append('network error and retry recovery')
                    page.get_by_role('link',name='设置',exact=True).click()
                    page.get_by_label('工作空间名称').fill('浏览器验收空间')
                    page.get_by_role('button',name='保存',exact=True).click()
                    page.reload();expect(page.get_by_label('工作空间名称')).to_have_value('浏览器验收空间');checks.append('settings persist without projects')
                    page.get_by_role('link',name='诊断项目',exact=True).click()
                    page.get_by_role('button',name='创建项目',exact=True).click()
                    page.get_by_label('项目名称').fill('浏览器隔离验收')
                    page.get_by_role('button',name='创建',exact=True).click()
                    expect(page.get_by_role('heading',name='浏览器隔离验收')).to_be_visible()
                    page.get_by_label('主营业务',exact=True).fill('设备维护')
                    page.get_by_label('服务区域',exact=True).fill('广州')
                    page.get_by_label('目标客户',exact=True).fill('工厂')
                    page.get_by_label('暂无官方页面').check()
                    page.get_by_role('button',name='保存并配置诊断').click()
                    page.get_by_label('DeepSeek',exact=True).check()
                    page.get_by_role('button',name='根据主体资料生成').click()
                    expect(page.locator('[data-question]')).to_have_count(8)
                    page.get_by_role('button',name='确认并冻结',exact=True).click()
                    page.get_by_role('button',name='确认冻结',exact=True).click()
                    expect(page.get_by_role('button',name='检查环境',exact=True)).to_be_visible()
                    expect(page.locator('#project-view tbody tr')).to_have_count(8)
                    page.reload();expect(page.locator('#project-view tbody tr')).to_have_count(8);checks.append('create profile questions freeze and refresh')
                    page.screenshot(path=str(OUT/'diagnosis-frozen.png'),full_page=True)
                    # Explicit synthetic evidence fixture tests only downstream UI, not live diagnosis.
                    with sqlite3.connect(Path(tmp)/'redesign.db') as db:
                        rid,run_path=db.execute('SELECT id,run_path FROM execution_runs').fetchone()
                        db.execute("UPDATE execution_runs SET status='completed' WHERE id=?",(rid,))
                    db.close()
                    run_path=Path(run_path)
                    (run_path/'evidence/test.txt').write_text('SYNTHETIC BROWSER TEST',encoding='utf8')
                    obs=dict(task_id='Q01_deepseek_01',status='success',response_text='SYNTHETIC BROWSER TEST',input_prompt='测试问题',evidence_files=['evidence/test.txt'],classification={'brand_mention':'no'})
                    (run_path/'observations.jsonl').write_text(json.dumps(obs)+'\n',encoding='utf8')
                    page.reload();page.get_by_role('button',name='生成改善任务',exact=True).click()
                    page.get_by_role('button',name='编写内容',exact=True).click()
                    page.get_by_label('正文',exact=True).fill('浏览器验收正文，不作为真实诊断结论。')
                    page.get_by_label('事实依据与来源',exact=True).fill('仅使用隔离测试夹具')
                    page.get_by_role('button',name='保存草稿',exact=True).click()
                    expect(page.get_by_text('已保存，需重新审核',exact=True)).to_be_visible()
                    page.get_by_role('button',name='审核当前版本',exact=True).click()
                    page.get_by_label('审核人',exact=True).fill('测试审核人')
                    page.get_by_label('已核对正文与事实依据').check()
                    page.get_by_role('button',name='审核通过',exact=True).click()
                    expect(page.get_by_text('已审核',exact=True)).to_be_visible()
                    page.get_by_role('link',name='发布安排',exact=True).click()
                    page.get_by_role('button',name='安排发布',exact=True).click()
                    page.get_by_label('负责人',exact=True).fill('测试负责人')
                    page.get_by_label('知乎',exact=True).check()
                    page.get_by_label('确认由负责人逐平台发布并回填链接').check()
                    page.get_by_role('button',name='创建任务',exact=True).click()
                    expect(page.get_by_text('知乎 · 待人工发布',exact=True)).to_be_visible()
                    page.get_by_role('button',name='回填发布链接',exact=True).click()
                    page.get_by_label('发布链接',exact=True).fill('https://example.com/test-fixture')
                    page.get_by_label('操作人',exact=True).fill('测试操作人')
                    page.get_by_label('确认已实际发布',exact=True).check()
                    page.get_by_role('button',name='保存回执',exact=True).click()
                    page.reload();expect(page.get_by_text('知乎 · 人工已回填',exact=True)).to_be_visible()
                    checks.append('synthetic evidence to improvement content review manual receipt persists')
                    page.get_by_role('link',name='内容生产',exact=True).click()
                    expect(page.locator('#scope option:checked')).to_have_text('浏览器隔离验收');checks.append('project context retained')
                    # Cross-project isolation: a second project must not expose the first project's records.
                    page.get_by_role('link',name='诊断项目',exact=True).click()
                    page.get_by_role('button',name='创建项目',exact=True).click()
                    page.get_by_label('项目名称').fill('隔离对照项目')
                    page.get_by_role('button',name='创建',exact=True).click()
                    expect(page.get_by_role('heading',name='隔离对照项目')).to_be_visible()
                    page.get_by_role('link',name='内容生产',exact=True).click()
                    expect(page.locator('#scope option:checked')).to_have_text('隔离对照项目')
                    expect(page.get_by_text('暂无内容，可从改善任务开始编写')).to_be_visible()
                    expect(page.get_by_text('浏览器验收正文，不作为真实诊断结论。')).to_have_count(0)
                    page.get_by_role('link',name='批量发布',exact=True).click()
                    expect(page.get_by_text('暂无发布任务')).to_be_visible();checks.append('cross-project data isolation in UI')
                    page.get_by_role('link',name='诊断项目',exact=True).click()
                    expect(page.get_by_role('link',name='隔离对照项目',exact=True)).to_be_visible()
                    target_row=page.get_by_role('row').filter(has_text='浏览器隔离验收')
                    target_row.get_by_role('button',name='删除',exact=True).click()
                    page.get_by_role('button',name='取消',exact=True).click()
                    expect(page.get_by_role('link',name='浏览器隔离验收',exact=True)).to_be_visible()
                    page.get_by_role('row').filter(has_text='浏览器隔离验收').get_by_role('button',name='删除',exact=True).click()
                    page.locator('#confirm-modal').click()
                    expect(page.get_by_role('link',name='浏览器隔离验收',exact=True)).to_have_count(0)
                    expect(page.get_by_role('link',name='隔离对照项目',exact=True)).to_be_visible();checks.append('delete only removes the confirmed project')
                    page.get_by_role('row').filter(has_text='隔离对照项目').get_by_role('button',name='删除',exact=True).click()
                    page.locator('#confirm-modal').click()
                    expect(page.get_by_text('还没有项目',exact=True)).to_be_visible();checks.append('delete cancel and confirm')
                    page.get_by_role('link',name='工作台',exact=True).click()
                    expect(page.get_by_text('暂无待处理事项')).to_be_visible()
                    page.screenshot(path=str(OUT/'workbench-empty.png'),full_page=True)
                    assert not errors,errors
                    browser.close()
            finally:
                for process in [api,web]:
                    if os.name=='nt':
                        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=30)
                    else: process.terminate()
                    process.wait(timeout=15)
    result={'checks':checks,'page_errors':errors,'external_questions_submitted':0,'production_data_touched':False}
    (OUT/'browser-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
