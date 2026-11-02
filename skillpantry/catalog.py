"""Catalog: load, validate and edit the TOML manifests that describe each entry."""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from skillpantry.i18n import Labels, T, lang

TARGETS = ("claude-code", "codex", "gemini", "cursor", "folder")
TARGET_LABELS = Labels({
    "claude-code": ("Claude Code", "Claude Code"),
    "codex": ("Codex", "Codex"),
    "gemini": ("Gemini CLI", "Gemini CLI"),
    "cursor": ("Cursor", "Cursor"),
    "folder": ("Folder (for yourself)", "Папка (для себя)"),
})
STATUSES = ("verified", "draft", "reference", "excluded")
STATUS_LABELS = Labels({
    "verified": ("verified", "проверено"),
    "draft": ("draft", "черновик"),
    "reference": ("reference", "справка"),
    "excluded": ("excluded", "исключено"),
})
RISKS = ("low", "medium", "high")
RISK_LABELS = Labels({"low": ("low", "низкий"), "medium": ("medium", "средний"), "high": ("high", "высокий")})
SUPPORT = ("auto", "manual", "none")

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SAFE_REL_RE = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[^\\:]*$")


def git_base() -> str:
    """Where repos are fetched from. Tests point this at a local directory."""
    return os.environ.get("SKILLPANTRY_GIT_BASE", "https://github.com").rstrip("/")


def default_catalog_dir() -> Path:
    from skillpantry.config import setting_path
    return setting_path("SKILLPANTRY_CATALOG", "catalog", Path(__file__).resolve().parent / "catalog")


def default_user_catalog_dir() -> Path:
    """Your own entries live here, apart from the shared catalog, so updates never touch them."""
    from skillpantry.config import home_dir, setting_path
    return setting_path("SKILLPANTRY_USER_CATALOG", "user_catalog", home_dir() / "catalog")


HASH_SKIP = {".git", "node_modules", "__pycache__", ".venv", "venv", ".DS_Store", ".skillpantry-ok"}


def _hash_tree(h, root: Path, start: Path) -> None:
    if start.is_file():
        try:
            data = start.read_bytes()
        except OSError:
            return
        h.update(start.relative_to(root).as_posix().encode("utf-8") + b"\0")
        h.update(hashlib.sha1(data).digest())
        return
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if d not in HASH_SKIP)
        for name in sorted(filenames):
            if name not in HASH_SKIP:
                _hash_tree(h, root, Path(dirpath) / name)


def content_hash(folder: str | Path, patterns: list[str] | None = None) -> str | None:
    """Version of a local source: sha1 over names and contents (40 hex, like a commit).

    Only files under `patterns` (paths or globs relative to the folder) count, so a rules
    file sitting in a big folder does not make skillpantry read the whole folder.
    """
    root = Path(folder)
    if not root.is_dir():
        return None
    h = hashlib.sha1()
    if patterns is None or any(p in (".", "") for p in patterns):
        _hash_tree(h, root, root)
        return h.hexdigest()
    found: set[Path] = set()
    for pat in patterns:
        if any(ch in pat for ch in "*?["):
            found.update(root.glob(pat))
        elif (root / pat).exists():
            found.add(root / pat)
    for p in sorted(found):
        _hash_tree(h, root, p)
    return h.hexdigest()


@dataclass
class SkillSpec:
    path: str | None = None            # path or glob inside the repo
    paths: dict[str, str] = field(default_factory=dict)  # per-target override
    name: str | None = None            # install name (default: folder name)
    optional: bool = False             # installed only when asked for
    exclude: list[str] = field(default_factory=list)

    def path_for(self, target: str) -> str | None:
        return self.paths.get(target) or self.paths.get("default") or self.path


@dataclass
class McpServer:
    name: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Entry:
    id: str
    number: int
    name: str
    category: str
    summary_text: str                  # main text (Russian in the shared catalog)
    description_text: str
    status: str
    risk: str
    work_safe: bool
    confirm: bool
    reviewed: str
    tags: list[str]
    risk_notes_list: list[str]
    overlaps: list[str]
    repo: str | None
    ref: str | None
    license: str | None
    url: str | None
    skills: list[SkillSpec]
    rules: dict | None
    mcp: list[McpServer]
    commands: list[str]
    variants: dict | None
    workspace: dict | None
    manual_main: dict[str, list[str]]
    targets: dict[str, str]
    requires: list[str]
    file: Path
    local: str | None = None           # [source] path: a folder on this computer instead of a repo
    origin: str = "main"               # "main" — shared catalog, "user" — your own entries
    en: dict = field(default_factory=dict)  # English texts: summary_en, description_en, risk_notes_en

    @property
    def search_text(self) -> str:
        """Both languages, so a search finds an entry whatever the interface language is."""
        return " ".join([self.summary_text, self.description_text, self.en.get("summary", ""),
                         self.en.get("description", ""), " ".join(self.tags)])

    # ---- texts in the interface language (English falls back to the main text) ----
    @property
    def summary(self) -> str:
        return (lang() == "en" and self.en.get("summary")) or self.summary_text

    @property
    def description(self) -> str:
        return (lang() == "en" and self.en.get("description")) or self.description_text

    @property
    def manual(self) -> dict[str, list[str]]:
        return (lang() == "en" and self.en.get("manual")) or self.manual_main

    @property
    def risk_notes(self) -> list[str]:
        return (lang() == "en" and self.en.get("risk_notes")) or self.risk_notes_list


    # ---- derived -------------------------------------------------------
    @property
    def installable(self) -> bool:
        return self.status in ("verified", "draft") and (self.repo is not None or self.local is not None)

    @property
    def git_url(self) -> str | None:
        return f"{git_base()}/{self.repo}" if self.repo else None

    @property
    def is_local(self) -> bool:
        return self.local is not None

    @property
    def source_url(self) -> str | None:
        """Where the files come from: repo URL or local folder."""
        return self.git_url or self.local

    @property
    def source_label(self) -> str:
        """Short source line for screens: 'owner/repo @ abc123' or 'папка ~/x · версия abc1'."""
        ver = (self.ref or "?")[:12]
        if self.local:
            return T(f"folder {self.local} · version {ver[:8]}", f"папка {self.local} · версия {ver[:8]}") if self.ref else T(f"folder {self.local} (not found)", f"папка {self.local} (не найдена)")
        if self.repo:
            return f"{self.repo} @ {ver}"
        return self.url or ""

    def has_payload(self) -> bool:
        return bool(self.skills or self.rules or self.mcp or self.commands
                    or self.variants or self.workspace)

    def support(self, target: str) -> str:
        """How this entry reaches a target: auto, manual or none."""
        if target in self.targets:
            return self.targets[target]
        if not self.installable:
            return "none"
        if self.workspace:
            return "auto" if target == "folder" else "none"
        if self.has_payload():
            return "auto"
        if self.manual_for(target):
            return "manual"
        return "none"

    def manual_for(self, target: str) -> list[str]:
        steps = list(self.manual.get(target, []))
        steps += self.manual.get("any", [])
        return steps

    def local_paths(self) -> list[str] | None:
        """Paths of a local source that make up its version; None — the whole folder."""
        if self.workspace:
            return None
        out: list[str] = []
        for sk in self.skills:
            out += [p for p in {sk.path, *sk.paths.values()} if p]
        if self.rules and self.rules.get("path"):
            out.append(self.rules["path"])
        out += self.commands
        if self.variants and self.variants.get("glob"):
            out.append(self.variants["glob"])
        return out

    def sparse_patterns(self) -> list[str] | None:
        """Paths to check out from the repo; None means the whole repo."""
        if self.workspace:
            return None
        pats: set[str] = {"/LICENSE*", "/LICENCE*", "/COPYING*", "/README*"}
        for s in self.skills:
            for p in {s.path, *s.paths.values()} - {None}:
                if p in (".", ""):
                    return None
                pats.add("/" + p.strip("/") + "/")  # non-cone patterns accept globs
        if self.rules and self.rules.get("path"):
            pats.add("/" + self.rules["path"])
        for c in self.commands:
            pats.add("/" + c)
        if self.variants:
            root = _glob_root(self.variants["glob"])
            pats.add("/" + (root.strip("/") + "/" if root != "." else self.variants["glob"].strip("/")))
        return sorted(pats)


def _glob_root(path: str) -> str:
    """'skills/*' -> 'skills', 'a/b/*/c.md' -> 'a/b'."""
    parts = []
    for part in path.split("/"):
        if any(ch in part for ch in "*?["):
            break
        parts.append(part)
    return "/".join(parts) or "."


class CatalogError(Exception):
    pass


def _as_list(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [v]


def parse_entry(data: dict, file: Path) -> Entry:
    src = data.get("source", {}) or {}
    skills = []
    for s in data.get("skills", []) or []:
        skills.append(SkillSpec(
            path=s.get("path"),
            paths=dict(s.get("paths", {}) or {}),
            name=s.get("name"),
            optional=bool(s.get("optional", False)),
            exclude=_as_list(s.get("exclude")),
        ))
    mcp = []
    for name, spec in (data.get("mcp", {}) or {}).items():
        mcp.append(McpServer(
            name=name,
            command=spec.get("command"),
            args=[str(a) for a in spec.get("args", [])],
            env={k: str(v) for k, v in (spec.get("env", {}) or {}).items()},
            url=spec.get("url"),
            headers={k: str(v) for k, v in (spec.get("headers", {}) or {}).items()},
        ))
    manual = {k: _as_list(v) for k, v in (data.get("manual", {}) or {}).items()}
    e = Entry(
        id=data.get("id", ""),
        number=int(data.get("number", 0)),
        name=data.get("name", data.get("id", "")),
        category=data.get("category", "other"),
        summary_text=data.get("summary", ""),
        description_text=data.get("description", "").strip(),
        status=data.get("status", "draft"),
        risk=data.get("risk", "medium"),
        work_safe=bool(data.get("work_safe", False)),
        confirm=bool(data.get("confirm", False)),
        reviewed=str(data.get("reviewed", "")),
        tags=_as_list(data.get("tags")),
        risk_notes_list=_as_list(data.get("risk_notes")),
        overlaps=_as_list(data.get("overlaps")),
        repo=src.get("repo"),
        ref=src.get("ref"),
        license=src.get("license"),
        url=src.get("url"),
        skills=skills,
        rules=data.get("rules"),
        mcp=mcp,
        commands=_as_list(data.get("commands")),
        variants=data.get("variants"),
        workspace=data.get("workspace"),
        manual_main=manual,
        targets=dict(data.get("targets", {}) or {}),
        requires=_as_list(data.get("requires")),
        file=file,
        local=src.get("path"),
        en={k: v for k, v in (("summary", data.get("summary_en")),
                               ("description", (data.get("description_en") or "").strip()),
                               ("risk_notes", _as_list(data.get("risk_notes_en"))),
                               ("manual", {k: _as_list(v) for k, v in (data.get("manual_en") or {}).items()}))
            if v},
    )
    if e.local and not e.ref:
        e.ref = content_hash(e.local, e.local_paths())
    return e


def validate_entry(e: Entry) -> list[str]:
    """Return a list of problems; empty means the manifest is valid."""
    errs: list[str] = []
    stem = e.file.stem
    if not ID_RE.match(e.id):
        errs.append(T(f"id '{e.id}': lowercase Latin letters, digits and '-' only", f"id '{e.id}': только строчные латинские буквы, цифры и '-'"))
    if e.id != stem:
        errs.append(T(f"id '{e.id}' does not match the file name '{stem}.toml'", f"id '{e.id}' не совпадает с именем файла '{stem}.toml'"))
    if e.status not in STATUSES:
        errs.append(T(f"status '{e.status}' is not one of {STATUSES}", f"status '{e.status}' не из {STATUSES}"))
    if e.risk not in RISKS:
        errs.append(T(f"risk '{e.risk}' is not one of {RISKS}", f"risk '{e.risk}' не из {RISKS}"))
    if not e.summary:
        errs.append(T("empty summary", "пустой summary"))
    for fname in ("name", "category", "summary", "description"):
        if "TODO" in str(getattr(e, fname)):
            errs.append(T(f"{fname}: fill in the TODO", f"{fname}: заполните TODO"))
    if e.status in ("verified", "draft") and e.local is not None:
        if e.repo:
            errs.append(T("source: set only one of repo or path", "source: укажите что-то одно — repo или path"))
        if not os.path.isabs(e.local):
            errs.append(T(f"source.path '{e.local}' must be a full path to a folder", f"source.path '{e.local}' должен быть полным путём к папке"))
        elif not Path(e.local).is_dir():
            errs.append(T(f"source.path: folder not found — {e.local}", f"source.path: папка не найдена — {e.local}"))
        if not e.has_payload() and not e.manual:
            errs.append(T("nothing to install: no skills/rules/mcp/commands/variants/workspace and no manual", "нечего ставить: нет skills/rules/mcp/commands/variants/workspace и нет manual"))
    elif e.status in ("verified", "draft"):
        if not e.repo:
            errs.append(T("verified/draft needs source.repo (owner/repo) or source.path (a folder)", "для verified/draft нужен source.repo (owner/repo) или source.path (папка)"))
        elif not REPO_RE.match(e.repo):
            errs.append(T(f"source.repo '{e.repo}' must look like owner/repo", f"source.repo '{e.repo}' должен быть вида owner/repo"))
        if not e.ref or not SHA_RE.match(e.ref):
            errs.append(T("source.ref must be a full commit SHA (40 hex characters)", "source.ref должен быть полным SHA коммита (40 hex-символов)"))
        if not e.license:
            errs.append(T("set source.license (or 'none' if there is no license)", "укажите source.license (или 'none', если лицензии нет)"))
        if not e.has_payload() and not e.manual:
            errs.append(T("nothing to install: no skills/rules/mcp/commands/variants/workspace and no manual", "нечего ставить: нет skills/rules/mcp/commands/variants/workspace и нет manual"))
    if e.status == "verified" and not e.reviewed:
        errs.append(T("verified needs a reviewed date", "для verified нужна дата reviewed"))
    if e.risk == "high" and not e.confirm and e.status != "excluded":
        errs.append(T("risk = high requires confirm = true", "risk = high требует confirm = true"))
    for t, v in e.targets.items():
        if t not in TARGETS:
            errs.append(T(f"targets: unknown target '{t}'", f"targets: неизвестная цель '{t}'"))
        if v not in SUPPORT:
            errs.append(T(f"targets.{t} = '{v}', allowed {SUPPORT}", f"targets.{t} = '{v}', допустимо {SUPPORT}"))
    for t in e.manual:
        if t not in TARGETS and t != "any":
            errs.append(T(f"manual: unknown target '{t}'", f"manual: неизвестная цель '{t}'"))
    rel_paths = []
    for s in e.skills:
        if not s.path and not s.paths:
            errs.append(T("skills: path or paths is required", "skills: нужен path или paths"))
        rel_paths += [p for p in (s.path, *s.paths.values()) if p]
        for t in s.paths:
            if t not in TARGETS and t != "default":
                errs.append(T(f"skills.paths: unknown target '{t}'", f"skills.paths: неизвестная цель '{t}'"))
    if e.rules:
        if not e.rules.get("path"):
            errs.append(T("rules: path is required", "rules: нужен path"))
        else:
            rel_paths.append(e.rules["path"])
    rel_paths += e.commands
    if e.variants:
        if not e.variants.get("glob") or not e.variants.get("dest"):
            errs.append(T("variants: glob and dest are required", "variants: нужны glob и dest"))
        else:
            rel_paths += [e.variants["glob"], e.variants["dest"]]
    for p in rel_paths:
        if p != "." and not SAFE_REL_RE.match(p):
            errs.append(T(f"unsafe path '{p}' (absolute, with '..' or '\\')", f"небезопасный путь '{p}' (абсолютный, с '..' или '\\')"))
    for m in e.mcp:
        if bool(m.command) == bool(m.url):
            errs.append(T(f"mcp.{m.name}: exactly one of command or url is required", f"mcp.{m.name}: нужен ровно один из command или url"))
        for k, v in {**m.env, **m.headers}.items():
            if v and not re.fullmatch(r"\$\{[A-Z0-9_]+\}", v) and _looks_secret(k):
                errs.append(T(f"mcp.{m.name}: '{k}' looks like a secret — store only ${{VAR}}", f"mcp.{m.name}: '{k}' похоже на секрет — храните только ${{VAR}}"))
    return errs


def _looks_secret(key: str) -> bool:
    return bool(re.search(r"key|token|secret|password|auth", key, re.I))


@dataclass
class Category:
    id: str
    title_text: str
    description_text: str
    order: int
    origin: str = "main"
    title_en: str = ""
    description_en: str = ""

    @property
    def title(self) -> str:
        return (lang() == "en" and self.title_en) or self.title_text

    @property
    def description(self) -> str:
        return (lang() == "en" and self.description_en) or self.description_text


CATEGORIES_FILE = "_categories.toml"


USER_NUMBER_START = 101  # your own entries are numbered from here, away from the shared ones


class Catalog:
    """The shared catalog plus your own entries (`user_dir`, default ~/.skillpantry/catalog).

    `user_dir=False` loads only the shared catalog.
    """

    def __init__(self, directory: Path | None = None, user_dir: Path | bool | None = True):
        self.dir = Path(directory) if directory else default_catalog_dir()
        if user_dir is True:
            user_dir = default_user_catalog_dir()
        self.user_dir: Path | None = Path(user_dir) if user_dir else None
        if self.user_dir and self.user_dir.resolve() == self.dir.resolve():
            self.user_dir = None
        self.entries: dict[str, Entry] = {}
        self.categories: dict[str, Category] = {}
        self.errors: dict[str, list[str]] = {}
        self.load()

    def _load_categories(self, directory: Path, origin: str) -> None:
        f = directory / CATEGORIES_FILE
        if not f.is_file():
            return
        try:
            with f.open("rb") as fh:
                data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as ex:
            self.errors[f"{origin}:{CATEGORIES_FILE}"] = [T(f"{f}: cannot be read: {ex}", f"{f}: не читается: {ex}")]
            return
        for key, spec in data.items():
            if isinstance(spec, dict):
                self.categories[key] = Category(key, str(spec.get("title", key)),
                                                str(spec.get("description", "")).strip(),
                                                int(spec.get("order", 50)), origin,
                                                str(spec.get("title_en", "")),
                                                str(spec.get("description_en", "")).strip())

    def _load_dir(self, directory: Path, origin: str) -> None:
        for f in sorted(directory.glob("*.toml")):
            if f.name.startswith("_"):
                continue
            try:
                with f.open("rb") as fh:
                    data = tomllib.load(fh)
                e = parse_entry(data, f)
            except (tomllib.TOMLDecodeError, ValueError, TypeError) as ex:
                self.errors[f.stem] = [T(f"{f}: cannot be read: {ex}", f"{f}: не читается: {ex}")]
                continue
            e.origin = origin
            key = e.id or f.stem
            if key in self.entries:
                self.errors.setdefault(key, []).append(
                    T(f"id '{key}' already exists in the shared catalog — rename your entry ({f})", f"id '{key}' уже есть в общем каталоге — переименуйте свою запись ({f})"))
                continue
            problems = validate_entry(e)
            if problems:
                self.errors[key] = problems
            self.entries[key] = e

    def load(self) -> None:
        self.entries.clear()
        self.errors.clear()
        if not self.dir.is_dir():
            raise CatalogError(T(f"Catalog not found: {self.dir}", f"Каталог не найден: {self.dir}"))
        self.categories.clear()
        self._load_categories(self.dir, "main")
        if self.user_dir and self.user_dir.is_dir():
            self._load_categories(self.user_dir, "user")
        self._load_dir(self.dir, "main")
        if self.user_dir and self.user_dir.is_dir():
            self._load_dir(self.user_dir, "user")
        numbers: dict[int, str] = {}
        for e in self.entries.values():
            if e.number and e.number in numbers:
                self.errors.setdefault(e.id, []).append(
                    T(f"number {e.number} is already taken by '{numbers[e.number]}'", f"number {e.number} уже занят записью '{numbers[e.number]}'"))
            numbers[e.number] = e.id
        for e in self.entries.values():
            for o in e.overlaps:
                if o not in self.entries:
                    self.errors.setdefault(e.id, []).append(T(f"overlaps: no entry '{o}'", f"overlaps: нет записи '{o}'"))
            # a custom catalog without _categories.toml may use any category keys
            if any(c.origin == "main" for c in self.categories.values()) and e.category not in self.categories:
                self.errors.setdefault(e.id, []).append(
                    T(f"category '{e.category}' is not in {CATEGORIES_FILE} " f"(available: {', '.join(self.categories)})", f"category '{e.category}' нет в {CATEGORIES_FILE} "
                    f"(есть: {', '.join(self.categories)})"))

    def category(self, key: str) -> Category:
        """Category by id; unknown ids get a placeholder so custom catalogs still work."""
        return self.categories.get(key) or Category(key, key, "", 50)

    def category_order(self) -> list[Category]:
        used = {e.category for e in self.entries.values()}
        return sorted((self.category(c) for c in used), key=lambda c: (c.order, c.title))

    def sorted(self) -> list[Entry]:
        return sorted(self.entries.values(), key=lambda e: (e.number or 10_000, e.id))

    def get(self, key: str) -> Entry:
        if key in self.entries:
            return self.entries[key]
        if key.isdigit():
            for e in self.entries.values():
                if e.number == int(key):
                    return e
        matches = [e for e in self.entries.values() if key.lower() in e.id]
        if len(matches) == 1:
            return matches[0]
        hint = T(f" Similar: {', '.join(m.id for m in matches[:6])}", f" Похожие: {', '.join(m.id for m in matches[:6])}") if matches else ""
        raise CatalogError(T(f"No entry '{key}'.{hint}", f"Нет записи '{key}'.{hint}"))

    def search(self, text: str) -> list[Entry]:
        t = text.lower()
        out = []
        for e in self.sorted():
            hay = " ".join([e.id, e.name, e.search_text, e.category,
                            " ".join(e.tags)]).lower()
            if t in hay:
                out.append(e)
        return out

    def next_number(self, user: bool = False) -> int:
        nums = [e.number for e in self.entries.values() if (e.origin == "user") == user]
        if user:
            return max(nums + [USER_NUMBER_START - 1]) + 1
        return max(nums, default=0) + 1

    def user_entries(self) -> list[Entry]:
        return [e for e in self.sorted() if e.origin == "user"]

    def all_tags(self) -> list[str]:
        """Every tag in use, most common first."""
        from collections import Counter
        c = Counter(t for e in self.entries.values() for t in e.tags)
        return [t for t, _ in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]


# ---- in-place manifest edits (keep comments and layout) -----------------

def set_field(file: Path, key: str, value: str, section: str | None = None) -> None:
    """Replace `key = "..."` (top level or inside [section]) in a TOML file."""
    text = file.read_bytes().decode("utf-8")  # keep the file's own line endings
    lines = text.splitlines(keepends=True)
    current = None
    pat = re.compile(rf'^(\s*{re.escape(key)}\s*=\s*)"[^"]*"(.*)$')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and not stripped.startswith("[["):
            current = stripped.strip("[]").strip()
        elif stripped.startswith("[["):
            current = stripped.strip("[]").strip()
        if current == section:
            m = pat.match(line.rstrip("\r\n"))
            if m:
                nl = line[len(line.rstrip("\r\n")):]
                lines[i] = f'{m.group(1)}"{value}"{m.group(2)}{nl}'
                file.write_bytes("".join(lines).encode("utf-8"))
                return
    raise CatalogError(T(f"{file.name} has no field {section + '.' if section else ''}{key}", f"В {file.name} нет поля {section + '.' if section else ''}{key}"))


def today() -> str:
    return date.today().isoformat()
