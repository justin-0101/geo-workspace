"""回环端口存活探测：**唯一一份实现**，由 `start.py` 与 `frontend_server.py` 共用。

为什么单独放一个模块：这段判断原先在两个文件里各写了一份，改一处漏一处
（本项目已因此吃过两次亏）。要改就改这里。

修掉的错误：`socket.settimeout()` 之后的 socket 是非阻塞的，`connect_ex()`
在"连接还在进行中"时会返回 WSAEWOULDBLOCK/EINPROGRESS（Windows 10035/10036），
而不是 0。旧写法 `connect_ex(...) == 0` 把"还在连"当成"端口没在监听"，
于是打印出 `listening:false` + `responding:true` 这种自相矛盾的状态。
"""
import errno
import select
import socket

# 连接尚未完成时的返回值：Windows 10035/10036/10037，POSIX 对应
# EWOULDBLOCK/EINPROGRESS/EALREADY。这些不等于"连不上"，必须再等一次。
_IN_PROGRESS = (
    errno.EWOULDBLOCK,
    errno.EINPROGRESS,
    getattr(errno, 'EALREADY', errno.EINPROGRESS),
)


def port_busy(host='127.0.0.1', port=4173, timeout=1.0):
    """True 当且仅当这一轮探测确认有进程正在这个 host/port 上接受连接。

    判定路径：
      connect_ex() == 0                    -> 有（连接立即完成）
      返回值在"进行中"集合内               -> 等 socket 可写，再看 SO_ERROR
      其余（拒绝/超时/不可达/无法建socket）-> 没有
    """
    probe = socket.socket()
    try:
        probe.settimeout(timeout)
        code = probe.connect_ex((host, port))
        if code == 0:
            return True
        if code not in _IN_PROGRESS:
            return False
        try:
            _, writable, _ = select.select([], [probe], [], timeout)
        except (OSError, ValueError):
            return False
        if not writable:
            return False
        return probe.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0
    except OSError:
        return False
    finally:
        probe.close()
