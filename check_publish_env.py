"""实测浏览器自动化发布的可用性：逐个打开平台编辑器，检测登录态与编辑器是否可识别。

只读检查：不填写、不点击发布，只截图与判定，便于如实报告每个平台当前的状态。
判定复用 `platform_login.detect`（与登录窗口、重新检测同一套规则），并把结果写回登录态文件。
"""
import json
import os
import time
from pathlib import Path

import browser_publisher as bp
import platform_login as pl
import workspace_store as store

OUT = store.ROOT / 'test-results' / 'publish-env'
GOTO_TIMEOUT_MS = int(os.environ.get('GEO_CHECK_GOTO_TIMEOUT', '60000'))
ONLY = [p for p in os.environ.get('GEO_CHECK_ONLY', '').split(',') if p]


def _shot(page, path):
    try:
        page.screenshot(path=str(path), timeout=20000)
        return str(path)
    except Exception:
        return ''


def main():
    from playwright.sync_api import sync_playwright
    OUT.mkdir(parents=True, exist_ok=True)
    guard = bp.ProcessGuard(store.DATA / 'browser-execution.lock')
    guard.acquire()
    results = []
    targets = [(pid, spec) for pid, spec in bp.TARGETS.items() if not ONLY or pid in ONLY]
    try:
        with sync_playwright() as pw:
            context = pl._launch(pw, headless=True)
            try:
                for platform_id, spec in targets:
                    item = {'platform': platform_id, 'label': spec['label']}
                    started = time.time()
                    page = context.new_page()
                    try:
                        page.goto(spec['editor_url'], wait_until='domcontentloaded', timeout=GOTO_TIMEOUT_MS)
                        page.wait_for_timeout(4000)
                        # 先留证据，再判断内容
                        item['screenshot'] = _shot(page, OUT / f'{platform_id}.png')
                        item['url'] = page.url
                        state, note = pl.detect(page, spec)
                        item['state'] = state
                        item['verdict'] = pl.STATE_LABELS.get(state, state)
                        item['note'] = note
                        item['snippet'] = pl._text(page, 160).replace('\n', ' ')
                        pl.record(platform_id, state, note, page.url)
                    except Exception as exc:
                        item['state'] = 'unknown'
                        item['verdict'] = '打开失败'
                        item['error'] = str(exc)[:160]
                        item['screenshot'] = _shot(page, OUT / f'{platform_id}.png')
                        pl.record(platform_id, 'unknown', f'打开失败：{str(exc)[:100]}')
                    finally:
                        item['seconds'] = round(time.time() - started, 1)
                        try:
                            page.close()
                        except Exception:
                            pass
                    results.append(item)
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    finally:
        guard.release()
    (OUT / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf8')
    for item in results:
        print(f"{item['platform']:<12} {item.get('state', 'unknown'):<13} {item.get('seconds', 0):>6}s "
              f"shot={'Y' if item.get('screenshot') else 'N'} {item.get('note', item.get('error', ''))[:52]}")


if __name__ == '__main__':
    main()
