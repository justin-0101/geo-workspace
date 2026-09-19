"""Local UI server with an explicit public-file allowlist. Never serves data/backups."""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, urlencode
import sys

ROOT=Path(__file__).resolve().parent
ROUTES={'index.html':'workbench','dashboard.html':'workbench','projects.html':'projects',
        'diagnosis.html':'projects','new-project.html':'projects','tasks.html':'actions',
        'content-production.html':'content','publication.html':'publications','reports.html':'reports',
        'platforms.html':'platforms','settings.html':'settings','compare.html':'projects'}
PUBLIC={'workspace.html','workspace.css','workspace.js'}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def do_GET(self):
        parsed=urlsplit(self.path);name=parsed.path.lstrip('/') or 'index.html'
        if name in ROUTES:
            query=parse_qs(parsed.query);args={}
            if name!='dashboard.html' and query.get('project'):args['project']=query['project'][0]
            if name=='diagnosis.html' and args:args['tab']='runs'
            target='/workspace.html#'+ROUTES[name]+('?' + urlencode(args) if args else '')
            self.send_response(302);self.send_header('Location',target);self.send_header('Cache-Control','no-store');self.end_headers();return
        if name not in PUBLIC:self.send_error(404);return
        super().do_GET()
    def do_HEAD(self):
        name=urlsplit(self.path).path.lstrip('/')
        if name not in PUBLIC:self.send_error(404);return
        super().do_HEAD()
    def end_headers(self):
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        super().end_headers()

def port_busy(host='127.0.0.1', port=4173, timeout=1.0):
    """True when something already answers on this host/port from another process."""
    import socket
    with socket.socket() as probe:
        probe.settimeout(timeout)
        return probe.connect_ex((host, port)) == 0


if __name__ == '__main__':
    # Refuse to start alongside another server: a plain `python -m http.server`
    # binds 0.0.0.0 and would expose data/, backups/ and scripts to the network.
    if port_busy():
        print('端口 4173 已被其他程序占用。请先停止它（尤其是 python -m http.server），'
              '避免它把 data/、backups/、脚本暴露到局域网。', file=sys.stderr)
        raise SystemExit(2)
    print('GEO 前端已启动（仅 127.0.0.1:4173，仅提供 workspace.html/css/js）。', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 4173), Handler).serve_forever()
