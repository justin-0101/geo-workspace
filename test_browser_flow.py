"""Isolated browser acceptance. Does not send questions to external platforms."""
import json
import os
import re
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
                    # 诊断来源的内容必须带上证据包：问题原文、观测摘要与证据文件索引
                    expect(page.get_by_role('heading',name='写作任务书',exact=True)).to_be_visible()
                    evidence=page.locator('#module details')
                    expect(evidence).to_contain_text('对应问题：测试问题')
                    expect(evidence).to_contain_text('证据索引：evidence/test.txt')
                    expect(evidence).to_contain_text('不作为企业事实')
                    page.get_by_label('目标读者',exact=True).fill('工厂设备负责人')
                    page.locator('#asset-form [name="objective"]').fill('浏览器隔离验收目标')
                    page.get_by_label('目标渠道',exact=True).fill('公众号长文')
                    # 内容生产页不再有「稿件」与手填事实依据；事实由素材自动带出
                    expect(page.locator('#asset-form [name="facts"]')).to_have_count(0)
                    expect(page.locator('#asset-form [name="body"]')).to_have_count(0)
                    page.get_by_role('button',name='保存任务书',exact=True).click()
                    expect(page.get_by_text('已保存任务书',exact=True)).to_be_visible()
                    # 素材：上传附件 + 粘贴文本（不跑外部网络）
                    page.locator('#src-file').set_input_files({
                        'name': '隔离测试产品说明.md',
                        'mimeType': 'text/markdown',
                        'buffer': '设备资产管理系统说明\n系统支持设备台账、点检、预测性维护与备件管理。\n已通过 ISO 9001 认证，标准交付周期为 8 周。'.encode('utf8')})
                    expect(page.locator('#src-list li[data-source]')).to_have_count(1)
                    expect(page.locator('#src-list')).to_contain_text('隔离测试产品说明.md')
                    expect(page.locator('#src-list')).to_contain_text('已提取')
                    page.get_by_role('button',name='粘贴文本',exact=True).click()
                    page.locator('#modal [name="label"]').fill('销售口述')
                    page.locator('#modal [name="text"]').fill('公司现有服务团队 60 人，覆盖全国 12 个省份。')
                    page.locator('#confirm-modal').click()
                    expect(page.locator('#src-list li[data-source]')).to_have_count(2)
                    page.screenshot(path=str(OUT/'content-materials.png'),full_page=True)
                    # 生成后弹框提示完成，并引导到内容库
                    page.locator('#asset-form #generate').click()
                    # 标题取决于引擎：未配模型=素材已整理，配了模型=初稿已生成
                    expect(page.locator('#modal-title')).to_have_text(re.compile('初稿已生成|素材已整理'))
                    expect(page.locator('#modal-body')).to_contain_text('自动质量检查通过')
                    expect(page.locator('#modal-body .gen-notes li')).not_to_have_count(0)   # 生成说明在弹框里
                    page.get_by_role('button',name='去内容库查看',exact=True).click()
                    expect(page.get_by_role('heading',name='内容库',exact=True)).to_be_visible()
                    expect(page.locator('.draft-body')).to_contain_text('ISO 9001')
                    expect(page.locator('.draft-sources')).to_contain_text('隔离测试产品说明.md')
                    # 正文是 Markdown，查看时必须渲染，不能把 ## / ** 原样显示给用户
                    expect(page.locator('.draft-body h2, .draft-body h3')).not_to_have_count(0)
                    expect(page.locator('.draft-body')).not_to_contain_text('## ')
                    # 未审核的稿子不能发布：入口要禁用并说明，不能给一个点了没用的链接
                    expect(page.locator('#module a', has_text='发布安排')).to_have_count(0)
                    expect(page.get_by_role('button', name='发布安排', exact=True)).to_be_disabled()
                    expect(page.locator('#module')).to_contain_text('需先通过审核才能发布')
                    expect(page.get_by_role('button', name='审核当前版本', exact=True)).to_be_enabled()
                    checks.append('draft is blocked from publishing until reviewed, and markdown renders')
                    # 内容库列表：序号 / 生成时间 / 操作三件套
                    page.get_by_role('link',name='返回内容库',exact=True).click()
                    row=page.locator('tr[data-lib]').first
                    expect(row.locator('td').nth(0)).to_have_text('1')
                    expect(row).to_contain_text('测试问题')
                    expect(row).to_contain_text('草稿')
                    expect(row.get_by_role('link',name='查看',exact=True)).to_be_visible()
                    checks.append('library lists drafts with index and actions')
                    # 编辑：改标题后保存，原审核失效
                    row.get_by_role('link',name='编辑',exact=True).click()
                    expect(page.get_by_role('heading',name='编辑初稿',exact=True)).to_be_visible()
                    page.locator('#lib-form [name="title"]').fill('隔离测试内容（已编辑）')
                    page.get_by_role('button',name='保存草稿',exact=True).click()
                    expect(page.get_by_text(re.compile('已保存（版本 \\d+）'))).to_be_visible()
                    page.get_by_role('button',name='审核当前版本',exact=True).click()
                    page.get_by_label('审核人',exact=True).fill('测试审核人')
                    page.get_by_label('已逐条核对正文、事实依据及公开范围').check()
                    page.get_by_role('button',name='审核通过',exact=True).click()
                    expect(page.get_by_text('已审核',exact=True).first).to_be_visible()
                    checks.append('library edit then review')
                    expect(page.locator('#module a', has_text='发布安排')).to_have_count(1)   # 审核通过后才变真链接
                    page.get_by_role('link',name='发布安排',exact=True).click()
                    expect(page.get_by_role('heading',name='批量发布',exact=True)).to_be_visible()
                    expect(page.locator('.gate')).to_be_visible()
                    expect(page.locator('[name="pick-asset"]:checked')).to_have_count(1)      # 带着 asset 过来自动勾上
                    expect(page.locator('.pick-banner')).to_have_count(0)                      # 已审核，不该再提示未审核
                    checks.append('approved draft arrives pre-selected on the publish page')
                    page.locator('input[name="pick-asset"]').first.check()
                    page.locator('input[name="pick-platform"][value="baike"]').check()
                    page.locator('#publish').click()
                    page.get_by_label('内容已审核，同意按上述方式发布').check()
                    page.locator('#confirm-modal').click()
                    expect(page.get_by_text('转人工',exact=True).first).to_be_visible()
                    # 再插一条已完结的发布记录，让「只看待处理」能真的减掉行数（
                    # 这是回归：以前「待处理 N」是页签链接里的角标，看着像按钮但点了没反应。
                    with sqlite3.connect(Path(tmp) / 'redesign.db') as db:
                        row = db.execute('SELECT id,project_slug,asset_id,asset_revision,title_snapshot,body_snapshot'
                                         ' FROM publishing_jobs LIMIT 1').fetchone()
                        db.execute('INSERT INTO publishing_jobs(id,project_slug,asset_id,platform,asset_revision,'
                                   'title_snapshot,body_snapshot,status,operator,mode,adapter_note,created_at,updated_at)'
                                   ' VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                   ('fixture-settled', row[1], row[2], 'official_site', row[3], row[4], row[5],
                                    'submitted', '隔离测试', 'browser', '夹具：已提交',
                                    '2026-09-20T08:00:00+00:00', '2026-09-20T08:00:00+00:00'))
                    db.close()
                    page.get_by_role('link',name='发布记录').click()
                    expect(page.locator('#tabs .tabs-count.warn')).to_have_count(0)   # 页签上不再有粘连的「待处理」角标
                    expect(page.locator('.rec-item')).to_have_count(2)
                    filter_btn=page.locator('#rec-filter')
                    expect(filter_btn).to_have_text('只看待处理（1）')
                    filter_btn.click()
                    expect(page.locator('.rec-item')).to_have_count(1)                # 真的筛掉了已完结那条
                    expect(page.locator('#rec-filter')).to_have_attribute('aria-pressed','true')
                    expect(page.locator('#pane')).to_contain_text('当前只显示待处理')
                    page.locator('#rec-filter').click()
                    expect(page.locator('.rec-item')).to_have_count(2)
                    checks.append('pending filter is a real control, not a glued badge')
                    expect(page.get_by_role('button',name='回填链接',exact=True).first).to_be_visible()
                    page.get_by_role('button',name='回填链接',exact=True).first.click()
                    page.get_by_label('发布链接',exact=True).fill('https://example.com/test-fixture')
                    page.get_by_label('操作人',exact=True).fill('测试操作人')
                    page.get_by_label('确认已实际发布',exact=True).check()
                    page.get_by_role('button',name='保存回执',exact=True).click()
                    page.reload();expect(page.get_by_text('人工已回填',exact=True).first).to_be_visible()
                    checks.append('direct publish (confirm) then manual receipt persists')
                    # 直接创建内容：不需要诊断结果
                    page.get_by_role('link',name='内容生产',exact=True).click()
                    page.get_by_role('button',name='＋ 新建内容任务书',exact=True).click()
                    page.locator('#modal [name="title"]').fill('直接创建的隔离测试内容')
                    page.locator('#modal [name="brief"]').fill('常见问题\n选型指标')
                    page.locator('#modal [name="channel"]').fill('公众号长文')
                    page.get_by_role('button',name='创建任务书',exact=True).click()
                    expect(page.get_by_text('直接创建',exact=True)).to_be_visible()
                    expect(page.locator('#asset-form [name="body"]')).to_have_count(0)   # 任务书页不再展示稿件
                    # 没有素材时生成：无法通过质量门禁，必须说清楚缺什么
                    page.locator('#src-paste').click()
                    page.locator('#modal [name="label"]').fill('隔离测试素材')
                    page.locator('#modal [name="text"]').fill('本系统支持设备台账、点检与备件管理。\n标准交付周期为 8 周，实施配置包含数据治理。')
                    page.locator('#confirm-modal').click()
                    expect(page.locator('#src-list li[data-source]')).to_have_count(1)
                    page.locator('#asset-form #generate').click()
                    expect(page.locator('#modal-title')).to_have_text(re.compile('初稿已生成|素材已整理'))
                    page.get_by_role('button',name='去内容库查看',exact=True).click()
                    expect(page.locator('.draft-body')).to_contain_text('8 周')
                    # 重新生成会覆盖正文，必须显式确认
                    page.get_by_role('link',name='编辑',exact=True).first.click()
                    page.get_by_role('button',name='重新生成初稿',exact=True).click()
                    expect(page.locator('#modal-title')).to_have_text('确认重新生成')
                    page.get_by_label('我已确认覆盖当前稿件').check()
                    page.get_by_role('button',name='确认覆盖并生成',exact=True).click()
                    expect(page.get_by_text(re.compile('已重新生成'))).to_be_visible()
                    checks.append('direct content creation generates a draft and confirms overwrite')
                    # 删除：确认框 + 列表同步
                    page.get_by_role('link',name='返回内容库',exact=True).click()
                    expect(page.locator('tr[data-lib]')).to_have_count(2)     # 诊断来源 1 条 + 直接创建 1 条
                    page.locator('tr[data-lib]').first.get_by_role('button',name='删除',exact=True).click()
                    expect(page.locator('#modal-title')).to_have_text('删除内容')
                    page.get_by_label('我确认删除这条内容').check()
                    page.locator('#confirm-modal').click()
                    expect(page.locator('tr[data-lib]')).to_have_count(1)
                    checks.append('library delete removes the row')
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
                    expect(page.get_by_text('没有待生成的任务书')).to_be_visible()
                    expect(page.get_by_role('button',name='＋ 新建内容任务书',exact=True)).to_be_visible()
                    # 内容库也要按项目隔离
                    page.get_by_role('link',name='内容库',exact=True).click()
                    expect(page.get_by_text('内容库是空的')).to_be_visible()
                    expect(page.get_by_text('浏览器隔离验收目标')).to_have_count(0)
                    page.get_by_role('link',name='批量发布',exact=True).click()
                    expect(page.locator('.gate')).to_be_visible()
                    expect(page.locator('#tabs a')).to_have_count(2)
                    expect(page.locator('#col-assets')).to_contain_text('还没有已审核内容')
                    expect(page.locator('#submitbar')).to_contain_text('还没有选内容或平台')
                    # 发布平台契约表：十个平台都要有接入位，缺一就是能力声明少了。
                    expect(page.locator('[name="pick-platform"]')).to_have_count(10)
                    # 平台明细：一行一个平台（与平台契约表一致，共 10 个）
                    expect(page.locator('#module details summary')).to_contain_text('平台明细')
                    expect(page.locator('#login-rows tr')).to_have_count(10)
                    expect(page.locator('#login-rows')).to_contain_text('还没有检测过登录态')
                    # 把识别结果写进状态文件再刷新：界面要读出已登录。
                    # （真实浏览器里的登录识别属于实机验证，这里只验界面与接口的契约。）
                    (Path(tmp)/'platform-login-state.json').write_text(json.dumps(
                        {'platforms':{'zhihu':{'state':'logged_in','note':'编辑器可识别',
                                               'checked_at':'2026-09-20T08:00:00'}},
                         'session':{'result':'completed','message':'已识别 1 个平台登录完成，登录窗口已自动关闭'}},
                        ensure_ascii=False),encoding='utf8')
                    page.reload();page.wait_for_timeout(1200)
                    zhihu_row=page.locator('#login-rows tr').filter(has_text='知乎')
                    expect(zhihu_row).to_have_count(1)
                    expect(zhihu_row).to_contain_text('编辑器可识别')   # 状态文件里的备注要读到
                    expect(zhihu_row).to_contain_text('可直发')         # logged_in → 芯片变成可直发
                    # 登录态不能把公众号的凭据状态一起改掉
                    wechat_row=page.locator('#login-rows tr').filter(has_text='微信公众号')
                    expect(wechat_row).to_contain_text('待配凭据')
                    checks.append('cross-project data isolation in UI')
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
