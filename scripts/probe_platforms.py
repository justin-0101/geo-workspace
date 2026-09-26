# -*- coding: utf-8 -*-
"""探测各平台输入控件与发送按钮结构，输出 JSON，供 geo_run.py 使用。"""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from geo_driver import open_browser, PLATFORMS

def probe(page, pid):
    url = PLATFORMS[pid]["url"]
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)
    out = {"platform": pid, "url": page.url, "textareas": [], "contenteditables": [],
           "buttons": [], "inputs": []}
    for i, el in enumerate(page.query_selector_all("textarea")):
        out["textareas"].append({"ph": (el.get_attribute("placeholder") or "")[:60],
                                 "cls": (el.get_attribute("class") or "")[:60]})
        if i >= 4: break
    for i, el in enumerate(page.query_selector_all('[contenteditable="true"]')):
        out["contenteditables"].append({"ph": (el.get_attribute("placeholder") or "")[:60],
                                        "aria": (el.get_attribute("aria-label") or "")[:60],
                                        "cls": (el.get_attribute("class") or "")[:60]})
        if i >= 4: break
    for i, el in enumerate(page.query_selector_all("button")):
        t = (el.inner_text() or "").strip().replace("\n", " ")
        if t:
            out["buttons"].append({"text": t[:30], "aria": (el.get_attribute("aria-label") or "")[:40]})
        if i >= 30: break
    return out

res = []
pw, ctx = open_browser()
page = ctx.new_page()
ids = sys.argv[1:] or ["deepseek", "doubao", "qianwen", "metaso"]
for pid in ids:
    try:
        res.append(probe(page, pid))
    except Exception as e:
        res.append({"platform": pid, "error": str(e)[:200]})
page.close(); ctx.close(); pw.stop()
print(json.dumps(res, ensure_ascii=False, indent=1))
