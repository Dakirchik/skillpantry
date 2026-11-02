"""Interface language: English and Russian.

Priority: `--lang` / `set_lang()` > SKILLPANTRY_LANG > `language` in ~/.skillpantry/config.toml >
the OS UI language. Strings are written in place as `T("English", "Русский")`, so both texts stay
next to each other; nothing is cached, so switching the language at runtime takes effect on the
next screen.
"""

from __future__ import annotations

import locale
import os
import sys

SUPPORTED = ("en", "ru")
NAMES = {"en": "English", "ru": "Русский"}
_lang: str | None = None


def _system_lang() -> str:
    if sys.platform == "win32":
        try:
            import ctypes
            langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "ru" if (langid & 0x3FF) == 0x19 else "en"  # 0x19 = LANG_RUSSIAN
        except Exception:
            pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var, "")
        if v and v not in ("C", "POSIX") and not v.startswith("C."):
            return "ru" if v.lower().startswith("ru") else "en"
    try:
        loc = locale.getlocale()[0] or ""
    except Exception:
        loc = ""
    return "ru" if loc.lower().startswith("ru") else "en"


def norm(value: str | None) -> str | None:
    if not value:
        return None
    v = str(value).strip().lower()[:2]
    return v if v in SUPPORTED else None


def configured() -> str | None:
    from skillpantry.config import settings
    return norm(settings().get("language"))


def set_lang(value: str | None = None) -> str:
    """Pick the language; None re-reads the environment, config and OS."""
    global _lang
    _lang = norm(value) or norm(os.environ.get("SKILLPANTRY_LANG")) or configured() or _system_lang()
    return _lang


def lang() -> str:
    return _lang or set_lang()


def T(en: str, ru: str) -> str:
    return ru if lang() == "ru" else en


def pick(en, ru):
    """Like T for any value (lists, tuples)."""
    return ru if lang() == "ru" else en


class Labels(dict):
    """A dict of {key: (en, ru)} that returns the text in the current language."""

    def __getitem__(self, key):
        return T(*super().__getitem__(key))

    def get(self, key, default=None):
        return self[key] if key in self else default

    def values(self):
        return [self[k] for k in self]

    def items(self):
        return [(k, self[k]) for k in self]
