# -*- coding: utf-8 -*-
"""打开指定平台等待人工登录。用法: python open_login.py <platform_id> [more...]"""
import sys, time, json, os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from geo_driver import open_browser, PLATFORMS, clean_text

ids = sys.argv[1:] or ["deepseek", "doubao", "qianwen", "metaso"]
pw, ctx = open_browser()
for pid in ids:
    cfg = PLATFORMS.get(pid)
    if not cfg:
        continue
    page = ctx.new_page()
    page.goto(cfg["url"], wait_until="domcontentloaded", timeout=60000)
    page.bring_to_front()
    print(json.dumps({"opened": pid, "url": page.url}, ensure_ascii=False), flush=True)

print("READY: 请在打开的窗口中登录以下平台: " + ",".join(ids), flush=True)
# 保持浏览器存活直到哨兵文件出现；出现后优雅关闭以落盘 cookie
sentinel = os.environ.get("GEO_LOGIN_STOP", os.path.join(os.getcwd(), ".login_stop"))
check = os.environ.get("GEO_LOGIN_CHECK", os.path.join(os.getcwd(), ".login_check"))
state_out = os.environ.get("GEO_LOGIN_STATE", os.path.join(os.getcwd(), ".login_state.json"))
import re as _re
try:
    while True:
        if os.path.exists(check):
            os.remove(check)
            states = []
            for pg in ctx.pages:
                try:
                    body = clean_text(pg.inner_text("body"))[:12000]
                    # 改进："退出登录/切换账号"是已登录信号；单独的"登录"入口才是未登录
                    logout_btn = bool(_re.search(r"(退出登录|切换账号|安全退出)", body))
                    login_only = bool(_re.search(r"登录", body)) and not logout_btn
                    avatar = bool(pg.query_selector('[class*="avatar" i], [data-testid*="avatar" i], [class*="user-info" i]'))
                    idx = body.find("退出登录")
                    ctx_around = body[max(0, idx - 60):idx + 60] if idx >= 0 else ""
                    states.append({"url": pg.url, "logout_text": logout_btn,
                                   "login_btn_only": login_only, "avatar": avatar,
                                   "guess": "logged_in" if (logout_btn or avatar) else "logged_out",
                                   "ctx_around_logout": ctx_around,
                                   "snippet": body[:200]})
                except Exception as e:
                    states.append({"url": getattr(pg, 'url', ''), "error": str(e)[:200]})
            with open(state_out, "w", encoding="utf-8") as f:
                json.dump(states, f, ensure_ascii=False, indent=2)
            print("STATE_DUMPED", flush=True)
        if os.path.exists(sentinel):
            print("STOP sentinel found, closing gracefully...", flush=True)
            break
        time.sleep(2)
except KeyboardInterrupt:
    pass
finally:
    try:
        ctx.close()
    except Exception:
        pass
    pw.stop()
    print("CLOSED", flush=True)
