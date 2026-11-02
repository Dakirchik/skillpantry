"""Small terminal helpers: colours that degrade gracefully, prompts, tables."""

from __future__ import annotations

from skillpantry.i18n import T

import os
import shutil
import sys

_COLOR = None


def color_enabled() -> bool:
    global _COLOR
    if _COLOR is None:
        _COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        if _COLOR and os.name == "nt":
            os.system("")  # enables ANSI escape handling in Windows consoles
    return _COLOR


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if color_enabled() else s


def bold(s): return _c("1", s)
def dim(s): return _c("2", s)
def red(s): return _c("31", s)
def green(s): return _c("32", s)
def yellow(s): return _c("33", s)
def blue(s): return _c("34", s)
def cyan(s): return _c("36", s)


RISK_COLOR = {"low": green, "medium": yellow, "high": red}
STATUS_MARK = {"verified": "✓", "draft": "✎", "reference": "ⓘ", "excluded": "✗"}
SUPPORT_MARK = {"auto": "●", "manual": "◐", "none": "·"}


def width() -> int:
    return shutil.get_terminal_size((100, 30)).columns


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: max(1, n - 1)] + "…"


def setup_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        ans = ""
    return ans or (default or "")


def confirm(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"  # Enter = the capital option
    ans = ask(f"{prompt} [{d}]").lower()
    if not ans:
        return default
    return ans in ("y", "yes", "д", "да", "l")  # "l" is "y" on a Russian keyboard layout


def choose(prompt: str, options: list[tuple[str, str]], default: str | None = None) -> str:
    """options: [(value, label)]. Returns the chosen value."""
    for i, (_, label) in enumerate(options, 1):
        print(f"  {i}. {label}")
    while True:
        dflt = None
        if default is not None:
            for i, (v, _) in enumerate(options, 1):
                if v == default:
                    dflt = str(i)
        ans = ask(prompt, dflt)
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return options[int(ans) - 1][0]
        for v, _ in options:
            if ans == v:
                return v
        print(red(T("  Enter a number from the list", "  Введите номер из списка")))


def parse_selection(text: str, maximum: int) -> list[int]:
    """'1 3, 5-7' -> [1, 3, 5, 6, 7]."""
    out: list[int] = []
    for part in text.replace(",", " ").split():
        if "-" in part:
            a, _, b = part.partition("-")
            if a.isdigit() and b.isdigit():
                out += list(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.append(int(part))
    seen = []
    for n in out:
        if 1 <= n <= maximum and n not in seen:
            seen.append(n)
    return seen
