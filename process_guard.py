"""OS-held file lock; crash releases it without stale lock deletion."""
import os


class ProcessGuard:
    def __init__(self,path): self.path=path;self.handle=None
    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        handle=self.path.open('a+b')
        if self.path.stat().st_size==0: handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError,IOError):
            handle.close()
            raise ValueError('此工作空间已由另一个服务进程使用')
        self.handle=handle
        return self
    def release(self):
        if not self.handle:return
        handle=self.handle;self.handle=None
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        finally:handle.close()
