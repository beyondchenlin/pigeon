"""Bionic posix_spawn adapter for native media subprocesses (Android API 28+).

Use libc's C-only child setup instead of Python/gRPC fork callbacks in the
multithreaded phone service. ABI: AOSP bionic libc/include/spawn.h defines both
action and attribute handles as pointers. This module never changes global
subprocess behavior or uses a shell. Only the BK media adapter calls it.
"""
import asyncio
import ctypes as C
import os
import signal
from pathlib import Path


def spawn_pipes(argv):
    if not argv or not os.path.isabs(argv[0]) or any('\0' in v for v in argv):
        raise ValueError('Expected an absolute executable and literal argv')
    libc = C.CDLL('libc.so', use_errno=True)
    handle = C.c_void_p
    hp = C.POINTER(handle)
    chars = C.POINTER(C.c_char_p)
    signatures = {
        'posix_spawn_file_actions_init': [hp],
        'posix_spawn_file_actions_destroy': [hp],
        'posix_spawn_file_actions_adddup2': [hp, C.c_int, C.c_int],
        'posix_spawn_file_actions_addclose': [hp, C.c_int],
        'posix_spawnattr_init': [hp],
        'posix_spawnattr_destroy': [hp],
        'posix_spawnattr_setflags': [hp, C.c_short],
        'posix_spawn': [C.POINTER(C.c_int), C.c_char_p, hp, hp, chars, chars],
    }
    for name, args in signatures.items():
        f = getattr(libc, name); f.argtypes = args; f.restype = C.c_int
    def check(error):
        if error:
            raise OSError(error, os.strerror(error))
    actions = handle()
    check(libc.posix_spawn_file_actions_init(C.byref(actions)))
    attr = handle()
    opened = []
    try:
        check(libc.posix_spawnattr_init(C.byref(attr)))
        # Bionic otherwise falls back to fork when file actions are present.
        # Force its native vfork path; no Python or pthread at-fork callbacks.
        check(libc.posix_spawnattr_setflags(C.byref(attr), 64))
        stdin = os.open('/dev/null', os.O_RDONLY | os.O_CLOEXEC); opened.append(stdin)
        out_r, out_w = os.pipe2(os.O_CLOEXEC); opened.extend((out_r, out_w))
        err_r, err_w = os.pipe2(os.O_CLOEXEC); opened.extend((err_r, err_w))
        for source, dest in ((stdin, 0), (out_w, 1), (err_w, 2)):
            check(libc.posix_spawn_file_actions_adddup2(C.byref(actions), source, dest))
        # Enumerated descriptors are closed in the child, never in the parent.
        # Descriptors concurrently opened by Python are CLOEXEC by default.
        for item in Path('/proc/self/fd').iterdir():
            fd = int(item.name)
            if fd > 2:
                check(libc.posix_spawn_file_actions_addclose(C.byref(actions), fd))
        args = (C.c_char_p * (len(argv) + 1))(*(os.fsencode(x) for x in argv), None)
        env = [os.fsencode(k + '=' + v) for k, v in os.environ.items()]
        envp = (C.c_char_p * (len(env) + 1))(*env, None)
        pid = C.c_int()
        check(libc.posix_spawn(C.byref(pid), os.fsencode(argv[0]), C.byref(actions), C.byref(attr), args, envp))
        for fd in (stdin, out_w, err_w):
            os.close(fd); opened.remove(fd)
        opened.clear()
        return pid.value, out_r, err_r
    finally:
        libc.posix_spawn_file_actions_destroy(C.byref(actions))
        if attr.value: libc.posix_spawnattr_destroy(C.byref(attr))
        for fd in opened:
            os.close(fd)


class MediaProcess:
    def __init__(self, pid):
        self.pid, self.returncode = pid, None
        self._transports = []
        self._waiter = None

    def _signal(self, sig):
        if self.returncode is None:
            try: os.kill(self.pid, sig)
            except ProcessLookupError: pass

    def terminate(self): self._signal(signal.SIGTERM)
    def kill(self): self._signal(signal.SIGKILL)

    async def wait(self):
        if self._waiter is None:
            async def reap():
                while True:
                    pid, status = os.waitpid(self.pid, os.WNOHANG)
                    if pid:
                        self.returncode = os.waitstatus_to_exitcode(status)
                        return self.returncode
                    await asyncio.sleep(.025)
            self._waiter = asyncio.create_task(reap())
        return await asyncio.shield(self._waiter)

    def close_pipes(self):
        for transport in self._transports: transport.close()
        self._transports.clear()


async def create_media_process(argv):
    pid, out_fd, err_fd = spawn_pipes(argv)
    process = MediaProcess(pid)
    pending = [out_fd, err_fd]
    try:
        loop = asyncio.get_running_loop()
        for name, fd in [('stdout', out_fd), ('stderr', err_fd)]:
            reader = asyncio.StreamReader(limit=65536)
            pipe = os.fdopen(fd, 'rb', buffering=0); pending.remove(fd)
            try:
                transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), pipe)
            except BaseException:
                pipe.close(); raise
            process._transports.append(transport)
            setattr(process, name, reader)
        process._monitor = asyncio.create_task(process.wait())
        process._monitor.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return process
    except BaseException:
        process.kill(); await process.wait(); process.close_pipes()
        for fd in pending: os.close(fd)
        raise
