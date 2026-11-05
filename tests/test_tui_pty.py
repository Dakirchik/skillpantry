"""The real program in a pseudo-terminal: raw keys (escape sequences) and screen output."""

import os
import re
import select
import signal
import sys
import time

import pytest

pty = pytest.importorskip("pty")  # macOS and Linux; Windows uses msvcrt and is covered by unit tests

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def run_keys(keys, env, cols=100, rows=30, cwd=None, timeout=15, command=None):
    import fcntl
    import struct
    import termios
    pid, fd = pty.fork()
    if pid == 0:
        try:
            if cwd:
                os.chdir(cwd)
            os.execvpe(sys.executable, command or [sys.executable, "-m", "skillpantry"], env)
        except BaseException:
            import traceback
            traceback.print_exc()
            os._exit(127)
    out = b""
    status = None
    eof = False
    deadline = time.monotonic() + timeout

    def pump(t):
        nonlocal out, status, eof
        end = min(time.monotonic() + t, deadline)
        while True:
            now = time.monotonic()
            if now >= deadline and status is None:
                tail = ANSI.sub("", out.decode("utf-8", "replace"))[-8000:]
                raise TimeoutError(f"PTY child did not exit within {timeout}s. Last terminal output:\n{tail}")
            if now >= end:
                return
            wait = min(0.05, end - now) if status is None else 0
            if not eof and select.select([fd], [], [], wait)[0]:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    data = b""  # Linux reports EIO when the slave terminal closes.
                if not data:
                    eof = True
                else:
                    out += data
            elif status is not None:
                return
            elif eof:
                time.sleep(wait)
            if status is None:
                finished, code = os.waitpid(pid, os.WNOHANG)
                if finished:
                    status = code

    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        pump(2.0)
        for k in keys:
            if status is not None:
                break
            os.write(fd, k.encode("utf-8"))
            pump(0.4)
        while status is None:
            pump(0.1)  # Keep draining output while polling for exit; never block in waitpid.
        pump(0.1)
        return out.decode("utf-8", "replace"), os.waitstatus_to_exitcode(status)
    finally:
        try:
            if status is None:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)
        finally:
            os.close(fd)


def test_run_keys_times_out_and_cleans_up(monkeypatch):
    children = []
    fork = pty.fork

    def track_fork():
        pid, fd = fork()
        if pid:
            children.append((pid, fd))
        return pid, fd

    monkeypatch.setattr(pty, "fork", track_fork)
    command = [sys.executable, "-c", "import time; print('waiting for input', flush=True); time.sleep(60)"]
    with pytest.raises(TimeoutError, match="waiting for input"):
        run_keys([], dict(os.environ), command=command, timeout=2)
    pid, fd = children[0]
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)
    with pytest.raises(OSError):
        os.fstat(fd)


def test_run_keys_reports_early_exit():
    command = [sys.executable, "-c", "print('startup failed', flush=True); raise SystemExit(3)"]
    text, code = run_keys([], dict(os.environ), command=command)
    assert code == 3
    assert "startup failed" in text


def test_full_screen_ui_in_a_real_terminal(env, tmp_path):
    e = dict(os.environ)
    e["PYTHONPATH"] = os.pathsep.join([os.getcwd()] + sys.path)
    e["TERM"] = "xterm-256color"
    e.pop("SKILLPANTRY_PLAIN", None)
    # Find the catalog by label, so adding a home menu item cannot redirect this test.
    # Still exercise a raw arrow escape sequence inside the catalog, then quit.
    text, code = run_keys(["/", "Каталог", "\r", "\r", "\x1b[B", "q"], e, cwd=str(tmp_path))
    assert code == 0
    assert "\x1b[?1049h" in text and "\x1b[?1049l" in text  # alternate screen entered and left
    frames = [ANSI.sub("", f) for f in text.split("\x1b[H")]
    assert any("Главная" in f and "Установить или обновить" in f for f in frames)
    assert any("Каталог" in f and "Kit" in f for f in frames)
