# -*- coding: utf-8 -*-
"""
GEO one-shot browser driver（通用稳定单批版）
- 启动本机 Chrome/Playwright 持久化上下文，复用指定 profile 登录态
- 只读预检：打开平台、探测登录态、截图、读可见文本
- 禁止：读取/导出 Cookie、令牌；提交正式问题只由 geo_run.py 在门禁通过后调用
用法:
  python geo_driver.py preflight <run_dir> <platform_id|all>
环境变量:
  GEO_CHROME_PATH        Chrome 可执行文件路径
  GEO_BROWSER_USER_DATA  浏览器 user-data-dir，默认 E:\\geo-profile\\UserData
  GEO_CDP_PORT           CDP 端口，默认 9333
"""
import sys, os, json, time, subprocess, re, glob
from urllib.request import urlopen

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CDP_PORT = int(os.environ.get("GEO_CDP_PORT", "9333"))
CHROME = os.environ.get("GEO_CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
# 副本 profile（Chrome 禁止在默认数据目录上开调试，用 E 盘副本）；稳定单批版默认共用一个 profile，因此禁止并发。
USER_DATA = os.environ.get("GEO_BROWSER_USER_DATA", r"E:\geo-profile\UserData")
PROFILE_DIR = os.environ.get("GEO_BROWSER_PROFILE_DIR", "Default")

PLATFORMS = {
    "deepseek": {"url": "https://chat.deepseek.com/", "login_hints": ["登录", "log in", "手机号"]},
    "doubao":   {"url": "https://www.doubao.com/chat/", "login_hints": ["登录", "立即登录"]},
    "qianwen":  {"url": "https://www.qianwen.com/", "login_hints": ["登录", "登录/注册", "立即登录"]},
    "metaso":   {"url": "https://metaso.cn/", "login_hints": ["登录"]},
    "yuanbao":  {"url": "https://yuanbao.tencent.com/chat/", "login_hints": ["登录", "微信登录"]},
}


def _no_proxy_opener():
    import urllib.request
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def cdp_alive():
    try:
        with _no_proxy_opener().open(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def launch_chrome():
    if cdp_alive():
        return "already-running"
    subprocess.Popen([
        CHROME,
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={USER_DATA}",
        f"--profile-directory={PROFILE_DIR}",
        "--no-first-run", "--no-default-browser-check",
        "about:blank",
    ])
    for _ in range(30):
        time.sleep(1)
        if cdp_alive():
            return "launched"
    raise RuntimeError("Chrome CDP 启动超时")


def open_browser():
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    ctx = pw.chromium.launch_persistent_context(
        user_data_dir=USER_DATA,
        executable_path=CHROME,
        headless=False,
        channel=None,
        viewport=None,
        args=[f"--profile-directory={PROFILE_DIR}", "--start-maximized",
              "--no-first-run", "--no-default-browser-check"],
    )
    return pw, ctx


def cdp_alive():
    try:
        with _no_proxy_opener().open(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def visible_text(page, selector):
    try:
        el = page.query_selector(selector)
        return el.inner_text().strip() if el else ""
    except Exception:
        return ""


def clean_text(s):
    return (s or "").replace("\ufeff", " ").replace("\u200b", " ")


def platform_state(page, pid):
    """登录态启发式探测（读 DOM 可见文本，不读 cookie）。"""
    cfg = PLATFORMS[pid]
    page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    body = clean_text(page.inner_text("body"))[:8000]
    login_btn = re.search(r"(登录|立即登录|登 录|sign\s*in|log\s*in)", body, re.I)
    # 常见已登录标志（头像菜单/开始对话区/用户昵称特征，启发式）
    avatar = page.query_selector('[class*="avatar" i], [class*="user-info" i], [data-testid*="avatar" i]')
    comfy = bool(re.search(r"(新对话|开始聊天|输入消息|帮我写作|你想聊什么|有什么可以帮你)", body))
    state = "logged_in" if (avatar and not login_btn) or (comfy and not login_btn) else \
            ("maybe_logged_in" if (avatar or comfy) else "logged_out")
    return {"platform": pid, "url": page.url, "login_state_guess": state,
            "login_button_text_found": bool(login_btn), "body_snippet": body[:300]}


def configured_platform_ids(run_dir):
    cfg_path = os.path.join(run_dir, "frozen-config.json")
    try:
        with open(cfg_path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        ids = [str(p.get("id", "")).strip() for p in cfg.get("platforms", [])]
        return [pid for pid in ids if pid]
    except Exception:
        return ["deepseek", "doubao", "qianwen", "metaso"]


def preflight(run_dir, only=None):
    pw, ctx = open_browser()
    page = ctx.new_page()
    os.makedirs(os.path.join(run_dir, "evidence"), exist_ok=True)
    out = []
    ids = [only] if only and only != "all" else configured_platform_ids(run_dir)
    for pid in ids:
        try:
            st = platform_state(page, pid)
            shot = os.path.join(run_dir, "evidence", f"preflight_{pid}.png")
            page.screenshot(path=shot, full_page=False)
            st["screenshot"] = shot
            st["check"] = "OK"
        except Exception as e:
            st = {"platform": pid, "check": "ERROR", "error": str(e)[:500]}
        out.append(st)
        print(json.dumps(st, ensure_ascii=False))
    page.close()
    ctx.close()
    pw.stop()
    with open(os.path.join(run_dir, "preflight.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "preflight"
    if cmd == "preflight":
        run_dir = sys.argv[2]
        only = sys.argv[3] if len(sys.argv) > 3 else "all"
        preflight(run_dir, only)
