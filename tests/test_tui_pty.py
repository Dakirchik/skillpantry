"""The real program in a pseudo-terminal: raw keys (escape sequences) and screen output."""

import os
import re
import select
import sys
import time

import pytest

pty = pytest.importorskip("pty")  # macOS and Linux; Windows uses msvcrt and is covered by unit tests

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def run_keys(keys, env, cols=100, rows=30, cwd=None):
    import fcntl
    import struct
    import termios
    pid, fd = pty.fork()
    if pid == 0:
        if cwd:
            os.chdir(cwd)
        os.execvpe(sys.executable, [sys.executable, "-m", "skillpantry"], env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    out = b""

    def pump(t):
        nonlocal out
        end = time.time() + t
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                out += data

    pump(2.0)
    for k in keys:
        os.write(fd, k.encode())
        pump(0.4)
    pump(1.0)
    _, status = os.waitpid(pid, 0)
    return out.decode("utf-8", "replace"), os.waitstatus_to_exitcode(status)


def test_full_screen_ui_in_a_real_terminal(env, tmp_path):
    e = dict(os.environ)
    e["PYTHONPATH"] = os.pathsep.join([os.getcwd()] + sys.path)
    e["TERM"] = "xterm-256color"
    e.pop("SKILLPANTRY_PLAIN", None)
    # down (escape sequence) to "Каталог", Enter, then q to quit from the catalog list
    text, code = run_keys(["\x1b[B", "\r", "q"], e, cwd=str(tmp_path))
    assert code == 0
    assert "\x1b[?1049h" in text and "\x1b[?1049l" in text  # alternate screen entered and left
    frames = [ANSI.sub("", f) for f in text.split("\x1b[H")]
    assert any("Главная" in f and "Установить или обновить" in f for f in frames)
    assert any("Каталог" in f and "Kit" in f for f in frames)
