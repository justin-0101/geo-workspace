"""Local UI server with an explicit public-file allowlist. Never serves data/backups."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
from urllib.parse import urlsplit, parse_qs, urlencode, unquote
import sys

from ports import port_busy
from runtime_config import API_URL, FRONTEND_PORT, LOCAL_HOST

ROOT=Path(__file__).resolve().parent
ROUTES={'index.html':'workbench','dashboard.html':'workbench','projects.html':'projects',
        'diagnosis.html':'projects','new-project.html':'projects','tasks.html':'actions',
        'content-production.html':'content','publication.html':'publications','reports.html':'reports',
        'platforms.html':'platforms','settings.html':'settings','compare.html':'projects'}
#: 这些入口映射到默认视图，重定向时刻意不带 fragment。
#: 带上会覆盖访问者原本的 #视图（重定向目标的 fragment 优先于原请求），
#: 不带时浏览器会沿用原 fragment；完全没带时前端自己落到工作台。
ENTRY_ROUTES={'index.html','dashboard.html'}
PUBLIC={'workspace.html','workspace.css','workspace.js','geo-config.js'}
#: 自托管字体目录。OFL-1.1 许可的公开字体文件，可随仓库分发；
#: 按前缀放行，免得每加一个字重就改一次白名单。
PUBLIC_PREFIX=('fonts/',)
FONTS_ROOT = (ROOT / 'fonts').resolve()


def _within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _public_name(raw_path):
    """Decode once, then resolve against the intended public root.

    ``SimpleHTTPRequestHandler`` performs its own path normalization, so the
    allowlist must validate the resolved path before delegating to it.  Backslash
    is rejected too because it is a path separator on Windows even when sent
    in a URL as a literal character.
    """
    decoded = unquote(urlsplit(raw_path).path)
    if not decoded or '\x00' in decoded or '\\' in decoded:
        return None
    return decoded.lstrip('/')


def _allowed_file(name):
    if not name:
        return None
    candidate = (ROOT / name).resolve()
    if name in PUBLIC:
        return candidate if _within(candidate, ROOT) and candidate.is_file() else None
    if name.startswith(PUBLIC_PREFIX):
        return candidate if _within(candidate, FONTS_ROOT) and candidate.is_file() else None
    return None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def do_GET(self):
        parsed=urlsplit(self.path);name=_public_name(self.path) or ''
        if name in ROUTES:
            query=parse_qs(parsed.query);args={}
            if name!='dashboard.html' and query.get('project'):args['project']=query['project'][0]
            if name=='diagnosis.html' and args:args['tab']='runs'
            frag='' if name in ENTRY_ROUTES else '#'+ROUTES[name]
            target='/workspace.html'+frag+('?' + urlencode(args) if args else '')
            self.send_response(302);self.send_header('Location',target);self.send_header('Cache-Control','no-store');self.end_headers();return
        if name == 'geo-config.js':
            payload = (f'window.GEO_API_URL = {json.dumps(API_URL)};\n').encode('utf-8')
            self.send_response(200); self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(payload))); self.end_headers(); self.wfile.write(payload); return
        if _allowed_file(name) is None:self.send_error(404);return
        super().do_GET()
    def do_HEAD(self):
        name=_public_name(self.path) or ''
        if name == 'geo-config.js':
            payload = (f'window.GEO_API_URL = {json.dumps(API_URL)};\n').encode('utf-8')
            self.send_response(200); self.send_header('Content-Type', 'application/javascript; charset=utf-8')
            self.send_header('Content-Length', str(len(payload))); self.end_headers(); return
        if _allowed_file(name) is None:self.send_error(404);return
        super().do_HEAD()
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-GEO-Service', 'frontend')
        super().end_headers()

if __name__ == '__main__':
    # Refuse to start alongside another server: a plain `python -m http.server`
    # binds 0.0.0.0 and would expose data/, backups/ and scripts to the network.
    if port_busy(host=LOCAL_HOST, port=FRONTEND_PORT):
        print(f'前端端口 {FRONTEND_PORT} 已被其他程序占用。请先停止它（尤其是 python -m http.server），'
              '避免它把 data/、backups/、脚本暴露到局域网。', file=sys.stderr)
        raise SystemExit(2)
    print(f'GEO 前端已启动（仅 {LOCAL_HOST}:{FRONTEND_PORT}，仅提供公开 UI 文件）。', flush=True)
    ThreadingHTTPServer((LOCAL_HOST, FRONTEND_PORT), Handler).serve_forever()
