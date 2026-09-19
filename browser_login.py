"""Open configured platforms for manual login using only the new profile.
No messages are submitted. Called as an owned subprocess by browser_engine.
"""
import json
import os
from pathlib import Path
import sys
import time


def main():
    run=Path(sys.argv[1])
    scripts=Path(os.environ['GEO_REDESIGN_ENGINE'])
    sys.path.insert(0,str(scripts))
    from geo_driver import open_browser
    config=json.loads((run/'frozen-config.json').read_text(encoding='utf-8-sig'))
    pw,context=open_browser()
    try:
        for platform in config['platforms']:
            page=context.new_page()
            page.goto(platform['entry_url'],wait_until='domcontentloaded',timeout=60000)
        (run/'login-open.flag').write_text('ready',encoding='utf8')
        deadline=time.monotonic()+1800
        while time.monotonic()<deadline:
            if (run/'login-done.flag').exists(): return
            pages=[p for p in context.pages if not p.is_closed()]
            if not pages: return
            try:
                pages[0].wait_for_timeout(500)
            except Exception:
                return
        raise RuntimeError('登录等待超时，请重新打开登录窗口')
    finally:
        context.close();pw.stop()

if __name__=='__main__':main()
