"""Where skillpantry keeps its data and which catalog/library it uses.

Priority for every setting: environment variable > ~/.skillpantry/config.toml > default.
The pantryctl scripts write config.toml during install.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path


def home_dir() -> Path:
    env = os.environ.get("SKILLPANTRY_HOME")
    if env:
        return Path(env).expanduser()
    new = Path.home() / ".skillpantry"
    if not new.exists():
        _migrate_legacy_home(Path.home() / ".skillshelf", new)
    return new


def _migrate_legacy_home(old: Path, new: Path) -> None:
    """Before 0.5.0 the project was called skillshelf: move its data (journal, own entries, config)."""
    if not old.is_dir():
        return
    try:
        old.rename(new)
    except OSError:
        return  # another process, a different drive: keep working with an empty home
    journal = new / "installs.json"
    if journal.is_file():
        text = journal.read_text(encoding="utf-8")
        for a, b in ((str(old), str(new)), (str(old).replace("\\", "\\\\"), str(new).replace("\\", "\\\\"))):
            text = text.replace(a, b)
        journal.write_text(text, encoding="utf-8")


def config_file() -> Path:
    return home_dir() / "config.toml"


@lru_cache(maxsize=8)
def _load(path: str, mtime: int, size: int) -> dict:
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def settings() -> dict:
    f = config_file()
    if not f.is_file():
        return {}
    st = f.stat()
    return _load(str(f), st.st_mtime_ns, st.st_size)


def setting_path(env_name: str, key: str, default: Path) -> Path:
    env = os.environ.get(env_name)
    if env:
        return Path(env).expanduser()
    value = settings().get(key)
    if isinstance(value, str) and value.strip():
        return Path(os.path.expandvars(value)).expanduser()
    return default


def set_setting(key: str, value: str) -> Path:
    """Set `key = "value"` in config.toml, keeping comments and other lines."""
    import json
    import re
    f = config_file()
    line = f"{key} = {json.dumps(value, ensure_ascii=False)}"
    text = f.read_bytes().decode("utf-8-sig") if f.is_file() else ""
    nl = "\r\n" if "\r\n" in text else "\n"
    pat = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$", re.M)
    if pat.search(text):
        text = pat.sub(lambda _m: line, text, count=1)
    else:
        # top-level keys must come before the first [table]
        m = re.search(r"^\[", text, re.M)
        text = (text[:m.start()] + line + nl + text[m.start():]) if m else (
            text + ("" if not text or text.endswith(("\n", "\r")) else nl) + line + nl)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(text.encode("utf-8"))
    _load.cache_clear()
    return f
