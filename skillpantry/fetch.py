"""Fetch a manifest's source into a local cache at exactly the pinned commit."""

from __future__ import annotations

from skillpantry.i18n import T

import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from skillpantry.catalog import Entry, git_base


class FetchError(Exception):
    pass


from skillpantry.config import home_dir  # noqa: E402  (re-exported for other modules)


def cache_dir() -> Path:
    return home_dir() / "cache"


def _git(*args: str, cwd: Path | None = None, timeout: int = 300) -> str:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError as ex:
        raise FetchError(T("git is not on PATH. Install Git: https://git-scm.com", "git не найден в PATH. Установите Git: https://git-scm.com")) from ex
    except subprocess.TimeoutExpired as ex:
        raise FetchError(T(f"git {' '.join(args[:2])}: timed out", f"git {' '.join(args[:2])}: превышено время ожидания")) from ex
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip().splitlines()
        raise FetchError(T(f"git {' '.join(args[:2])}: {msg[-1] if msg else 'error'}", f"git {' '.join(args[:2])}: {msg[-1] if msg else 'ошибка'}"))
    return r.stdout


def remote_head(repo: str) -> str:
    out = _git("ls-remote", f"{git_base()}/{repo}", "HEAD", timeout=60)
    sha = out.split()[0] if out.split() else ""
    if len(sha) != 40:
        raise FetchError(T(f"Could not get HEAD of {repo}", f"Не удалось получить HEAD для {repo}"))
    return sha


def _rmtree(path: Path) -> None:
    def fix(func, p, _exc):  # read-only files in .git on Windows
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=fix)
    else:
        shutil.rmtree(path, onerror=fix)


def snapshot_path(entry: Entry, ref: str | None = None) -> Path:
    ref = ref or entry.ref or ""
    pats = entry.sparse_patterns()
    key = hashlib.sha1(repr(pats).encode()).hexdigest()[:8]
    return cache_dir() / f"{entry.id}-{ref[:12]}-{key}"


def fetch(entry: Entry, ref: str | None = None, refresh: bool = False,
          full: bool = False) -> Path:
    """Return a directory with the repo checked out at `ref` (default: pinned ref).

    Entries with a local folder (`source.path`) return that folder.

    Only the paths the manifest needs are checked out (sparse), unless `full`.
    The snapshot is verified to be exactly the requested commit.
    """
    if entry.local is not None:
        folder = Path(entry.local)
        if not folder.is_dir():
            raise FetchError(T(f"Folder of '{entry.id}' not found: {entry.local}", f"Папка '{entry.id}' не найдена: {entry.local}"))
        return folder  # your own folder is used as is; its version is a content hash
    if not entry.repo:
        raise FetchError(T(f"'{entry.id}' has no source.repo — nothing to install", f"У '{entry.id}' нет source.repo — ставить нечего"))
    ref = ref or entry.ref
    if not ref:
        raise FetchError(T(f"'{entry.id}' has no pinned commit (source.ref)", f"У '{entry.id}' не закреплён коммит (source.ref)"))
    dest = snapshot_path(entry, ref) if not full else cache_dir() / f"{entry.id}-{ref[:12]}-full"
    marker = dest / ".skillpantry-ok"
    if marker.exists() and not refresh:
        return dest
    _rmtree(dest)
    dest.mkdir(parents=True)
    try:
        _git("init", "-q", str(dest))
        _git("remote", "add", "origin", entry.git_url, cwd=dest)
        pats = None if full else entry.sparse_patterns()
        if pats:
            _git("sparse-checkout", "set", "--no-cone", *pats, cwd=dest)
        _git("fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", ref,
             cwd=dest, timeout=900)
        _git("-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD",
             cwd=dest, timeout=900)
        head = _git("rev-parse", "HEAD", cwd=dest).strip()
        if head != ref:
            raise FetchError(T(f"Got commit {head[:12]}, expected {ref[:12]}", f"Получен коммит {head[:12]}, ожидался {ref[:12]}"))
    except Exception:
        _rmtree(dest)
        raise
    marker.write_text(ref, encoding="utf-8")
    return dest


def license_text(snapshot: Path) -> str | None:
    for name in sorted(os.listdir(snapshot)):
        if name.upper().startswith(("LICENSE", "LICENCE", "COPYING")):
            p = snapshot / name
            if p.is_file():
                return p.read_text(encoding="utf-8", errors="replace")
    return None


def clear_cache(entry_id: str | None = None) -> int:
    root = cache_dir()
    if not root.exists():
        return 0
    n = 0
    for d in root.iterdir():
        if entry_id is None or d.name.startswith(entry_id + "-"):
            _rmtree(d)
            n += 1
    return n
