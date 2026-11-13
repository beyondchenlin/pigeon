import importlib.util
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ops'))
from bk_lifecycle_monitor import Identity, Proc, release


@unittest.skipUnless(os.name == 'posix', 'Run process identity checks in the existing Linux runtime')
class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='bk-lifecycle-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'uptime').write_text('1000.0 0')
        self.proc = Proc(self.root)
        self.parent = self.make_process(100, 1, 100, ['pipe_read','do_epoll_wait'])
        self.child = self.make_process(101, 100, 200, ['futex_wait_queue'])
        (self.root / '100/task/100/children').write_text('101')
        (self.root / '100/task/100/syscall').write_text('0 0x86 0x1 0x2')
        self.pipe(100, 134, 42, os.O_RDONLY | os.O_CLOEXEC)
        self.pipe(101, 135, 42, os.O_WRONLY | os.O_CLOEXEC)

    def make_process(self, pid, ppid, tick, waits, exe='/runtime/python3'):
        p = self.root / str(pid)
        for name in ('fd','fdinfo','task'):
            (p / name).mkdir(parents=True)
        (p / 'exe').symlink_to(exe)
        fields=['S',str(ppid)]+['0']*17+[str(tick)]
        (p / 'stat').write_text('%d (lamda worker) '%pid+' '.join(fields))
        (p / 'cmdline').write_bytes(b'lamda\0--launch\0')
        for i,w in enumerate(waits):
            t=p/'task'/str(pid+i);t.mkdir()
            (t/'wchan').write_text(w)
            (t/'children').write_text('')
            (t/'syscall').write_text('running')
        return self.proc.identity(pid)

    def pipe(self,pid,fd,inode,flags):
        (self.root/str(pid)/'fd'/str(fd)).symlink_to('pipe:[%d]'%inode)
        (self.root/str(pid)/'fdinfo'/str(fd)).write_text('flags:\t%o\n'%flags)

    def test_matches_historical_pre_exec_pipe_and_stable_identity(self):
        self.assertEqual(self.proc.candidates(self.parent),[self.child])

    def test_verified_old_and_new_kernel_wait_names_preserve_stall_detection(self):
        for parent_wait in ('pipe_read', 'anon_pipe_read'):
            for child_wait in ('futex_wait_queue', 'futex_wait_queue_me', 'futex_do_wait', '__futex_wait'):
                with self.subTest(parent_wait=parent_wait, child_wait=child_wait):
                    (self.root/'100/task/100/wchan').write_text(parent_wait)
                    (self.root/'101/task/101/wchan').write_text(child_wait)
                    self.assertEqual(self.proc.candidates(self.parent), [self.child])

    def test_similar_but_unverified_wait_names_are_not_accepted(self):
        (self.root/'100/task/100/wchan').write_text('anon_pipe_read')
        for child_wait in ('futex_do_wait_extra', '__futex_do_wait', '__futex_wait_extra', '___futex_wait', 'do_futex', 'futex_wait', '0'):
            with self.subTest(child_wait=child_wait):
                (self.root/'101/task/101/wchan').write_text(child_wait)
                self.assertEqual(self.proc.candidates(self.parent), [])
        (self.root/'101/task/101/wchan').write_text('futex_do_wait')
        for parent_wait in ('anon_pipe_read_extra', '__anon_pipe_read', 'pipe_read_v2', 'do_pipe_read', '0'):
            with self.subTest(parent_wait=parent_wait):
                (self.root/'100/task/100/wchan').write_text(parent_wait)
                self.assertEqual(self.proc.candidates(self.parent), [])

    def test_new_wait_names_do_not_relax_syscall_or_exec_error_pipe_requirements(self):
        (self.root/'100/task/100/wchan').write_text('anon_pipe_read')
        (self.root/'101/task/101/wchan').write_text('futex_do_wait')
        syscall = self.root/'100/task/100/syscall'
        syscall.write_text('1 0x86 0x1 0x2')
        self.assertEqual(self.proc.candidates(self.parent), [])
        syscall.write_text('0 0x86 0x1 0x2')
        (self.root/'101/fdinfo/135').write_text('flags:\t1\n')
        self.assertEqual(self.proc.candidates(self.parent), [])
        (self.root/'101/fdinfo/135').write_text('flags:\t%o\n'%(os.O_WRONLY|os.O_CLOEXEC))
        self.pipe(100, 135, 42, os.O_WRONLY|os.O_CLOEXEC)
        self.assertEqual(self.proc.candidates(self.parent), [])

    def test_android_without_children_file_uses_verified_uid_and_ppid(self):
        (self.root/'100/task/100/children').unlink()
        self.assertEqual(self.proc.candidates(self.parent),[self.child])

    def test_idle_worker_without_blocked_parent_read_is_not_a_target(self):
        (self.root/'100/task/100/wchan').write_text('do_epoll_wait')
        self.assertEqual(self.proc.candidates(self.parent),[])

    def test_pipe_must_be_close_on_exec_and_parent_writer_closed(self):
        (self.root/'101/fdinfo/135').write_text('flags:\t1\n')
        self.assertEqual(self.proc.candidates(self.parent),[])
        (self.root/'101/fdinfo/135').write_text('flags:\t%o\n'%(os.O_WRONLY|os.O_CLOEXEC))
        self.pipe(100,135,42,os.O_WRONLY|os.O_CLOEXEC)
        self.assertEqual(self.proc.candidates(self.parent),[])

    def test_executed_child_and_other_parent_are_not_targets(self):
        (self.root/'101/cmdline').write_bytes(b'screen\0')
        self.assertEqual(self.proc.candidates(self.parent),[])
        (self.root/'101/cmdline').write_bytes(b'lamda\0--launch\0')
        self.assertIsNone(self.proc.candidate(Identity(99,'100',os.getuid(),'/runtime/python3',1),101))

    def test_fresh_or_running_child_is_not_a_target(self):
        (self.root/'uptime').write_text('2.0 0')
        self.assertEqual(self.proc.candidates(self.parent),[])
        (self.root/'uptime').write_text('1000.0 0')
        (self.root/'101/task/101/wchan').write_text('0')
        self.assertEqual(self.proc.candidates(self.parent),[])

    def test_identity_change_prevents_signal(self):
        with patch('bk_lifecycle_monitor.os.pidfd_open',return_value=900), \
             patch('bk_lifecycle_monitor.os.close'), \
             patch('bk_lifecycle_monitor.signal.pidfd_send_signal') as send:
            stale=Identity(101,'199',os.getuid(),'/runtime/python3',100)
            self.assertEqual(release(self.proc,self.parent,stale),'changed')
            send.assert_not_called()

    def test_missing_proc_permission_does_not_relax_matching(self):
        with patch.object(self.proc,'blocked_reads',side_effect=PermissionError):
            self.assertEqual(self.proc.candidates(self.parent),[])


@unittest.skipUnless(os.name == 'posix' and hasattr(os,'pidfd_open'), 'Linux pidfd integration')
class RealStallTest(unittest.TestCase):
    def test_real_pre_exec_stall_is_released_without_stopping_parent(self):
        # A task-owned fixture creates the same pipe/futex failure shape. No
        # live phone service is stopped or intentionally deadlocked for testing.
        with tempfile.TemporaryDirectory(prefix='bk-preexec-fixture-') as folder:
            child_file=Path(folder)/'child'
            code='''import os,sys,threading,time,subprocess
lock=threading.Lock(); lock.acquire()
def blocked():
    with open(sys.argv[1],'w') as f:f.write(str(os.getpid()))
    lock.acquire()
def spawn():
    p=subprocess.Popen([sys.executable,'-c','pass'],preexec_fn=blocked)
    p.wait()
    print('released',flush=True)
threading.Thread(target=spawn,daemon=True).start()
print('ready',flush=True)
time.sleep(45)
'''
            parent=subprocess.Popen([sys.executable,'-u','-c',code,str(child_file)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            proc=Proc(); child=None
            try:
                self.assertEqual(parent.stdout.readline().strip(),'ready')
                end=time.monotonic()+4
                while not child_file.exists() and time.monotonic()<end:time.sleep(.05)
                parent_id=proc.identity(parent.pid)
                child=proc.identity(int(child_file.read_text()))
                self.assertEqual(proc.candidate(parent_id,child.pid,min_age=0),child)
                # Production enforces twenty seconds. Exercise the actual
                # signalling path with the same classifier and a shorter age.
                original=proc.candidate
                with patch.object(proc,'candidate',side_effect=lambda p,c:original(p,c,min_age=0)):
                    outcome=release(proc,parent_id,child,grace=.2)
                self.assertIn(outcome,('released_term','released_kill'))
                self.assertIsNone(parent.poll())
                self.assertEqual(parent.stdout.readline().strip(),'released')
            finally:
                if child and proc.same(child):
                    fd=os.pidfd_open(child.pid)
                    try:signal.pidfd_send_signal(fd,signal.SIGKILL)
                    finally:os.close(fd)
                parent.terminate()
                parent.communicate(timeout=5)


if __name__=='__main__':unittest.main()
