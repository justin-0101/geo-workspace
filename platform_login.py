"""平台登录窗口与登录态识别。

目标：点一次「打开登录窗口」→ 自动打开各平台的编辑器页 → 用户自己在窗口里登录 →
系统轮询页面，自动识别登录结果并写进工作空间 → 前端自动刷新。

边界：
- 只读页面判断状态，不代填账号密码、不读取或导出 cookie；
- 登录态只存在于隔离浏览器 profile 目录（本机敏感数据），不进数据库；
- 判定不出「明确已登录」时不会谎报：编辑器定位不到就写“已离开登录页”，页面没加载就写“未检测”。

判定顺序（从严到宽）：
1. 标题框 + 正文框都能定位            → logged_in（编辑器可识别，可自动发布）
2. 在登录 URL / 有密码框 / 有登录页文案 → needs_login
3. 页面已加载且离开登录页，但编辑器定位不到 → session_only（登录可能成功，选择器待确认）
4. 页面没打开 / 内容过少                 → unknown
"""
import json
import os
import threading
import time
from urllib.parse import urlparse

import browser_publisher as bp
import workspace_store as store
from process_guard import ProcessGuard

STATE_LABELS = {
    'logged_in': '已登录',
    'session_only': '已离开登录页',
    'needs_login': '需登录',
    'unknown': '未检测',
}
STATE_TONES = {'logged_in': 'good', 'session_only': 'warn', 'needs_login': 'warn', 'unknown': ''}

LOGIN_URL_HINTS = ('login', 'signin', 'sign-in', 'passport', 'auth/page')
# 只有登录页才会出现的控件文案：用于区分“登录页”与“登录后的页面”。
STRONG_MARKERS = ('获取验证码', '获取短信验证码', '扫码登录', '密码登录', '验证码登录', '立即登录',
                  '登录/注册', '请先登录', '去登录', '账号登录', '手机号登录', '登录后')
# 弱文案：登录后的页脚、菜单里也可能出现，单独不足以判定未登录。
WEAK_MARKERS = ('登录', '注册')

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36')
POLL_SECONDS = 3
SESSION_TIMEOUT = 1800          # 30 分钟等不到登录就收摊
MIN_TEXT = 200                  # 正文少于这么多字符视为“页面还没加载完”
PROBE_SETTLE_MS = 3000          # 无头复核时等页面渲染

_LOCK = threading.Lock()
_RUNTIME = {'thread': None, 'ids': [], 'started_at': ''}


# --- 状态存储 ---------------------------------------------------------------
def state_path():
    """延迟取路径：测试会替换 store.DATA，不能在导入时固化。"""
    return store.DATA / 'platform-login-state.json'


def read():
    try:
        data = json.loads(state_path().read_text(encoding='utf8'))
        if isinstance(data, dict):
            data.setdefault('platforms', {})
            data.setdefault('session', {})
            return data
    except Exception:
        pass
    return {'platforms': {}, 'session': {}}


def _write(data):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(tmp, path)


def record(platform_id, state, note='', url=''):
    with _LOCK:
        data = read()
        data['platforms'][platform_id] = {
            'state': state, 'note': note, 'url': url, 'checked_at': store.now()}
        _write(data)
    return state


def _finish(result, message):
    with _LOCK:
        data = read()
        data['session'] = {'result': result, 'message': message, 'ended_at': store.now()}
        _write(data)


def _ids(platform_ids):
    if not platform_ids:
        return list(bp.TARGETS)
    return [pid for pid in platform_ids if pid in bp.TARGETS]


def session_info():
    thread = _RUNTIME.get('thread')
    active = bool(thread and thread.is_alive())
    stored = read().get('session', {})
    return {
        'active': active,
        'active_platforms': list(_RUNTIME['ids']) if active else [],
        'started_at': _RUNTIME['started_at'] if active else '',
        'result': stored.get('result', ''),
        'message': stored.get('message', ''),
        'ended_at': stored.get('ended_at', ''),
    }


def snapshot(platform_ids=None):
    """给界面用的完整快照：状态 + 中文标签 + 会话信息。"""
    data = read()
    states = {}
    for pid in _ids(platform_ids):
        item = data['platforms'].get(pid, {})
        state = item.get('state') or 'unknown'
        states[pid] = {
            'state': state,
            'label': STATE_LABELS.get(state, state),
            'tone': STATE_TONES.get(state, ''),
            'note': item.get('note', ''),
            'url': item.get('url', ''),
            'checked_at': item.get('checked_at', ''),
        }
    summary = {'total': len(states), 'logged_in': 0, 'needs_login': 0, 'unknown': 0, 'session_only': 0}
    for item in states.values():
        summary[item['state']] = summary.get(item['state'], 0) + 1
    return {'session': session_info(), 'states': states, 'summary': summary,
            'labels': STATE_LABELS, 'tones': STATE_TONES}


# --- 页面判定 ---------------------------------------------------------------
def _text(page, limit=4000):
    try:
        return page.inner_text('body', timeout=5000)[:limit]
    except Exception:
        return ''


def _url(page):
    try:
        return page.url or ''
    except Exception:
        return ''


def _password_input(page):
    try:
        return bool(page.query_selector('input[type=password]'))
    except Exception:
        return False


def _editor_ready(page, spec):
    return bool(bp._first(page, spec['title']) and bp._first(page, spec['body']))


def _on_login_url(url):
    return any(hint in urlparse(url).path.lower() for hint in LOGIN_URL_HINTS)


def detect(page, spec):
    """返回 (state, note)。只看页面证据，绝不因为“点了按钮”判成功。"""
    url = _url(page)
    if _editor_ready(page, spec):
        return 'logged_in', '编辑器可识别（标题框与正文框都在）'
    if not url or url == 'about:blank' or url.startswith('chrome-error'):
        return 'unknown', '页面还没打开'
    text = _text(page)
    if len(text) < MIN_TEXT:
        return 'unknown', '页面内容过少，可能还没加载完'
    if _on_login_url(url) or _password_input(page) or any(m in text for m in STRONG_MARKERS):
        return 'needs_login', '仍在登录页（验证码/密码/扫码入口还在）'
    if any(m in text for m in WEAK_MARKERS):
        return 'needs_login', '页面上还有登录入口文案'
    return 'session_only', '已离开登录页，但编辑器未识别（发布需人工确认）'


# --- 浏览器 -----------------------------------------------------------------
def _launch(pw, headless):
    args = ['--profile-directory=Default', '--no-first-run', '--no-default-browser-check', '--lang=zh-CN']
    if not headless:
        args.append('--start-maximized')
    return pw.chromium.launch_persistent_context(
        user_data_dir=str(store.DATA / 'browser-profile'), executable_path=bp.CHROME,
        headless=headless, viewport=None if not headless else {'width': 1440, 'height': 1000},
        user_agent=UA, locale='zh-CN', timezone_id='Asia/Shanghai', args=args)


def _open_editor(page, spec):
    """优先进编辑器页：登录后平台会把用户带回编辑器，正好也是要识别的页面。"""
    for url in (spec['editor_url'], spec['login_url']):
        try:
            page.goto(url, wait_until='domcontentloaded', timeout=60000)
            return True
        except Exception:
            continue
    return False


def start(platform_ids=None):
    """打开登录窗口并开始轮询识别。已有窗口在开时直接返回当前会话。"""
    ids = _ids(platform_ids)
    if not ids:
        raise ValueError('没有可登录的浏览器平台')
    with _LOCK:
        thread = _RUNTIME.get('thread')
        if thread and thread.is_alive():
            return {'started': False, **session_info(),
                    'message': '登录窗口已经打开，请直接在那个窗口里完成登录'}
        _RUNTIME.update(thread=None, ids=ids, started_at=store.now())
        worker = threading.Thread(target=_run, args=(ids, SESSION_TIMEOUT),
                                  daemon=True, name='platform-login')
        _RUNTIME['thread'] = worker
        worker.start()
    labels = '、'.join(bp.TARGETS[pid]['label'] for pid in ids)
    return {'started': True, **session_info(),
            'message': f'已打开登录窗口（{labels}）。登录成功后系统会自动识别；'
                       f'全部识别到已登录后窗口会自动关闭。'}


def wait(timeout=None):
    thread = _RUNTIME.get('thread')
    if thread:
        thread.join(timeout)


def _run(ids, timeout):
    result, message = 'closed', '登录窗口已关闭'
    for pid in ids:
        record(pid, 'unknown', '登录窗口已打开，正在识别')
    guard = ProcessGuard(store.DATA / 'browser-execution.lock')
    try:
        guard.acquire()
    except ValueError:
        for pid in ids:
            record(pid, 'unknown', '浏览器正被诊断或发布占用，登录窗口没能打开')
        _finish('busy', '诊断或发布正在使用浏览器，登录窗口没有打开；请稍后重试')
        _RUNTIME.update(thread=None, ids=[], started_at='')
        return
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            context = _launch(pw, headless=False)
            try:
                pages = {}
                for pid in ids:
                    page = context.new_page()
                    pages[pid] = page
                    _open_editor(page, bp.TARGETS[pid])
                deadline = time.time() + timeout
                seen = {}
                while True:
                    try:
                        alive = bool(context.pages)
                    except Exception:
                        alive = False          # 浏览器被强杀/崩溃
                    if not alive:
                        result, message = 'closed', '登录窗口已关闭，已按关闭前的最后一次识别记录状态'
                        break
                    pending = []
                    for pid, page in pages.items():
                        try:
                            if page.is_closed():
                                continue
                            state, note = detect(page, bp.TARGETS[pid])
                            url = _url(page)
                        except Exception:
                            continue
                        if seen.get(pid) != state:
                            seen[pid] = state
                            record(pid, state, note, url)
                        if state not in ('logged_in', 'session_only'):
                            pending.append(pid)
                    if not pending:
                        result = 'completed'
                        message = f'已识别 {len(ids)} 个平台登录完成，登录窗口已自动关闭'
                        break
                    if time.time() > deadline:
                        result, message = 'timeout', '等待登录超时，登录窗口已关闭；可以重新打开'
                        break
                    time.sleep(POLL_SECONDS)
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as exc:
        result, message = 'failed', f'登录窗口异常：{str(exc)[:180]}'
    finally:
        guard.release()
        _RUNTIME.update(thread=None, ids=[], started_at='')
    _finish(result, message)
    # 用户可能登录完立刻关窗口；窗口关掉后再做一次无头复核，避免漏识别。
    if result in ('closed', 'timeout'):
        probe(ids)


def probe(platform_ids=None, timeout_ms=60000):
    """打开登录窗口之外的「重新检测」：无头打开编辑器页判定登录态。"""
    ids = _ids(platform_ids)
    if not ids:
        return {'ok': False, 'message': '没有可检测的浏览器平台'}
    guard = ProcessGuard(store.DATA / 'browser-execution.lock')
    try:
        guard.acquire()
    except ValueError:
        raise ValueError('浏览器正被占用（登录窗口或诊断执行中），请先关闭窗口再检测')
    results = {}
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            context = _launch(pw, headless=True)
            try:
                for pid in ids:
                    spec = bp.TARGETS[pid]
                    page = context.new_page()
                    try:
                        page.goto(spec['editor_url'], wait_until='domcontentloaded', timeout=timeout_ms)
                        page.wait_for_timeout(PROBE_SETTLE_MS)
                        state, note = detect(page, spec)
                    except Exception as exc:
                        state, note = 'unknown', f'打不开：{str(exc)[:120]}'
                    finally:
                        try:
                            page.close()
                        except Exception:
                            pass
                    record(pid, state, note)
                    results[pid] = state
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except Exception as exc:
        raise ValueError(f'检测失败：{str(exc)[:180]}')
    finally:
        guard.release()
    return {'ok': True, 'results': results}
