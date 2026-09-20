"""非 API 平台的浏览器自动化发布。

设计原则：
- 复用与诊断相同的隔离浏览器 profile（用户只需登录一次，凭据不进数据库）；
- 每一步要么拿到明确成功信号，要么返回失败/转人工并截图留证；绝不把“点了按钮”当成已发布；
- 与诊断执行互斥（同一把浏览器锁），避免两个自动化同时抢 profile。
"""
import os
from pathlib import Path

import workspace_store as store
from process_guard import ProcessGuard

CHROME = os.environ.get('GEO_CHROME_PATH', r'C:\Program Files\Google\Chrome\Application\chrome.exe')

# 各平台的编辑器入口与候选选择器；候选是“尽力而为”，定位不到就转人工并留截图。
TARGETS = {
    'zhihu': dict(
        label='知乎', login_url='https://www.zhihu.com/signin',
        editor_url='https://zhuanlan.zhihu.com/write',
        title=['textarea[placeholder*="标题"]', 'input[placeholder*="标题"]', '.WriteIndex-titleInput textarea'],
        body=['div[contenteditable="true"]', '.public-DraftEditor-content', '.ProseMirror'],
        save=['保存草稿', '发布'], success=['发布成功', '已保存']),
    'baijia': dict(
        label='百家号', login_url='https://baijiahao.baidu.com/',
        editor_url='https://baijiahao.baidu.com/builder/rc/edit?type=news',
        title=['textarea[placeholder*="标题"]', 'input[placeholder*="标题"]'],
        body=['div[contenteditable="true"]', '.ql-editor', '.ProseMirror'],
        save=['存草稿', '保存草稿', '发布'], success=['保存成功', '发布成功']),
    'toutiao': dict(
        label='今日头条', login_url='https://mp.toutiao.com/auth/page/login',
        editor_url='https://mp.toutiao.com/profile_v4/graphic/publish',
        title=['textarea[placeholder*="标题"]', 'input[placeholder*="标题"]'],
        body=['div[contenteditable="true"]', '.ProseMirror', '.ql-editor'],
        save=['存草稿', '保存草稿', '预览并发布'], success=['保存成功', '发布成功']),
    'csdn': dict(
        label='CSDN', login_url='https://passport.csdn.net/login',
        editor_url='https://editor.csdn.net/md/',
        title=['input[placeholder*="标题"]', '.article-bar__title input'],
        body=['textarea', '.editor__inner', '.cledit-section'],
        save=['保存草稿', '发布文章'], success=['保存成功', '发布成功']),
    'xiaohongshu': dict(
        label='小红书', login_url='https://creator.xiaohongshu.com/login',
        editor_url='https://creator.xiaohongshu.com/publish/publish?source=official&target=article',
        title=['input[placeholder*="标题"]'],
        body=['div[contenteditable="true"]', '.ql-editor', '.ProseMirror'],
        save=['发布', '存草稿'], success=['发布成功', '已保存']),
    'sohu': dict(
        label='搜狐号', login_url='https://mp.sohu.com/',
        editor_url='https://mp.sohu.com/mpfe/v4/contentManagement/news/addarticle',
        title=['input[placeholder*="标题"]', 'textarea[placeholder*="标题"]'],
        body=['div[contenteditable="true"]', '.ql-editor'],
        save=['存草稿', '发布'], success=['保存成功', '发布成功']),
    'dayu': dict(
        label='大鱼号', login_url='https://mp.dayu.com/',
        editor_url='https://mp.dayu.com/dashboard/index',
        title=['input[placeholder*="标题"]', 'textarea[placeholder*="标题"]'],
        body=['div[contenteditable="true"]'],
        save=['存草稿', '发布'], success=['保存成功', '发布成功']),
}


def _first(page, selectors):
    for selector in selectors:
        try:
            element = page.query_selector(selector)
            if element:
                return element
        except Exception:
            continue
    return None


def _fill(page, selectors, text):
    element = _first(page, selectors)
    if not element:
        return False
    try:
        element.click()
        element.fill('')
        element.type(text[:2000], delay=0)
        return True
    except Exception:
        try:
            element.evaluate('(el, value) => { el.value = value; el.dispatchEvent(new Event("input", {bubbles:true})); }', text)
            return True
        except Exception:
            return False


def _click_text(page, labels):
    for label in labels:
        try:
            locator = page.get_by_text(label, exact=False)
            if locator.count():
                locator.first.click(timeout=5000)
                return label
        except Exception:
            continue
    return ''


def publish(platform_id, title, body, evidence_dir, operator='', timeout_ms=90000):
    """尝试在该平台创建内容；返回明确状态，绝不谎报成功。"""
    spec = TARGETS.get(platform_id)
    if not spec:
        return {'status': 'manual_required', 'message': f'{platform_id} 没有配置浏览器自动化目标', 'evidence': ''}
    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    shot = evidence_dir / f'publish_{platform_id}.png'
    guard = ProcessGuard(store.DATA / 'browser-execution.lock')
    try:
        guard.acquire()
    except ValueError:
        return {'status': 'failed', 'message': '诊断或发布正在使用浏览器，请稍后重试', 'evidence': ''}

    try:
        from playwright.sync_api import sync_playwright
        profile = str(store.DATA / 'browser-profile')
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=profile, executable_path=CHROME, headless=False, viewport=None,
                args=['--profile-directory=Default', '--start-maximized',
                      '--no-first-run', '--no-default-browser-check'])
            try:
                page = context.new_page()
                page.goto(spec['editor_url'], wait_until='domcontentloaded', timeout=60000)
                page.wait_for_timeout(4000)
                try:
                    page.screenshot(path=str(shot))
                except Exception:
                    pass

                title_box = _first(page, spec['title'])
                body_box = _first(page, spec['body'])
                if not title_box or not body_box:
                    current = page.url
                    return {'status': 'manual_required', 'evidence': str(shot),
                            'message': f"{spec['label']} 编辑器未自动识别（当前页面 {current}）。"
                                       f"请确认已在隔离浏览器登录该平台；截图已保存，可手动发布后回填链接。"}
                if not _fill(page, spec['title'], title) or not _fill(page, spec['body'], body):
                    return {'status': 'failed', 'evidence': str(shot),
                            'message': f"{spec['label']} 定位到编辑区但写入失败，请查看截图"}
                clicked = _click_text(page, spec['save'])
                page.wait_for_timeout(3000)
                try:
                    page.screenshot(path=str(shot))
                except Exception:
                    pass
                page_text = page.inner_text('body')[:6000]
                hit = next((signal for signal in spec['success'] if signal in page_text), '')
                if hit and clicked:
                    return {'status': 'submitted', 'evidence': str(shot), 'url': page.url,
                            'message': f"{spec['label']}：已点击“{clicked}”并出现“{hit}”；请在平台后台确认，"
                                       f"必要时回填链接。"}
                return {'status': 'manual_required', 'evidence': str(shot), 'url': page.url,
                        'message': f"{spec['label']}：未确认成功信号（点击了“{clicked or '未找到按钮'}”）。"
                                   f"请查看截图并手动完成。"}
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as exc:
        return {'status': 'failed', 'evidence': str(shot) if shot.exists() else '',
                'message': f"{spec['label']} 浏览器自动化失败：{str(exc)[:180]}"}
    finally:
        try:
            guard.release()
        except Exception:
            pass
