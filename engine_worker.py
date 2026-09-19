"""Hold an OS lock for the entire engine subprocess, even if the API exits."""
from pathlib import Path
import os
import runpy
import sys
from process_guard import ProcessGuard


def main():
    script=Path(sys.argv[1]).resolve()
    root=Path(__file__).resolve().parent
    engine=Path(os.environ['GEO_REDESIGN_ENGINE']).resolve()
    allowed={engine/'geo_driver.py',engine/'geo_run.py',engine/'render-report.py',root/'browser_login.py'}
    if script not in allowed: raise ValueError('不允许的执行脚本')
    data=Path(os.environ['GEO_REDESIGN_DATA']).resolve()
    guard=ProcessGuard(data/'browser-execution.lock').acquire()
    try:
        sys.argv=sys.argv[1:]
        sys.path.insert(0,str(script.parent))
        runpy.run_path(str(script),run_name='__main__')
    finally:guard.release()

if __name__=='__main__':main()
