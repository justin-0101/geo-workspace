"""前台常驻：同时跑前端(4173)与 API(8798)，任一退出即整体收尾。

给外部托管方（例如 <project-dashboard> 的「启动」按钮）用：
那里的运行记录按「一个长期存活的 PID + taskkill /T」管理，
而 start.py 是「拉起就退出」的启动器，会被判成「进程启动后立刻退出了」，
记下的 PID 也早已不在，停止按钮就杀不到真正的两个服务。

自己在本机用，仍然可以直接 `python start.py`（拉起后即返回，日志落 data/*.log）。

用法:  python serve.py        （Ctrl+C 或结束本进程 = 整体停止）

注意 `CREATE_NO_WINDOW`：本进程通常是被外部托管方用 DETACHED_PROCESS 拉起的（自己没有控制台），
这时子进程若不加标志，Windows 会给每个控制台子系统进程**新分配一个控制台窗口**——
表现为用户桌面上凭空弹出终端窗口，而关掉那个窗口就等于给服务发关闭信号。
"""
import subprocess
import sys
import time
from pathlib import Path

from ports import port_busy
from runtime_config import API_PORT, FRONTEND_PORT, LOCAL_HOST

ROOT = Path(__file__).resolve().parent

#: 不给子进程分配控制台窗口（否则会弹终端，且关窗=杀服务）
NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0

CHILDREN = (
    (f'前端 :{FRONTEND_PORT}', [sys.executable, 'frontend_server.py']),
    (f'API :{API_PORT}', [sys.executable, '-m', 'uvicorn', 'workflow_api:app',
                          '--host', LOCAL_HOST, '--port', str(API_PORT)]),
)


def main() -> int:
    occupied = []
    for label, port in (('前端', FRONTEND_PORT), ('API', API_PORT)):
        if port_busy(host=LOCAL_HOST, port=port):
            occupied.append(f'{label}端口 {port}')
    if occupied:
        print('启动中止：' + '、'.join(occupied) + ' 已被占用，请先运行 python start.py --status 检查归属。', flush=True)
        return 2

    procs = []
    try:
        for label, argv in CHILDREN:
            print(f'启动 {label}: {" ".join(argv[1:])}', flush=True)
            flags = NO_WINDOW | (0x00000200 if sys.platform == 'win32' else 0)
            procs.append((label, subprocess.Popen(argv, cwd=ROOT, creationflags=flags)))
    except OSError as exc:
        print(f'启动失败：{exc}', flush=True)
        for _, proc in procs:
            proc.terminate()
        return 1

    code = 0
    try:
        while True:
            for label, proc in procs:
                if proc.poll() is not None:
                    code = proc.returncode or 1
                    print(f'{label} 已退出（code={proc.returncode}），整体停止', flush=True)
                    return code
            time.sleep(1.0)
    except KeyboardInterrupt:
        print('收到中断，停止子进程', flush=True)
    finally:
        for _, proc in procs:
            if proc.poll() is None:
                proc.terminate()
        deadline = time.time() + 8
        for label, proc in procs:
            try:
                proc.wait(timeout=max(0.1, deadline - time.time()))
            except subprocess.TimeoutExpired:
                print(f'{label} 未在期限内退出，强制结束', flush=True)
                proc.kill()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
