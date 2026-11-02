"""Your own entries: add a folder from this computer or a GitHub repo to a personal catalog.

Own entries live in the user catalog (default ~/.skillpantry/catalog), apart from the shared
catalog, so updating skillpantry never touches them. The form in the full-screen UI and
`skillpantry add <path|url> --mine` both go through `inspect_source` → `entry_data` → `save`.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from skillpantry.audit import AuditReport, audit_paths
from skillpantry.catalog import (CATEGORIES_FILE, ID_RE, RISKS, Catalog, CatalogError, Entry,
                                content_hash, parse_entry, today, validate_entry)
from skillpantry.fetch import fetch, remote_head
from skillpantry.i18n import T
from skillpantry.maintain import detect_license, detect_mcp, detect_skills, parse_repo

RULE_FILES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md", "RULES.md", "rules.md", ".cursorrules")
COMMAND_GLOBS = (".claude/commands/*.md", "commands/*.md")
ROOT_SKILL_EXCLUDE = ["README.md", "CONTRIBUTING.md", "CHANGELOG.md", "tests", ".github"]
OWN = "own"


def own_category() -> dict:
    return {"id": OWN, "title": T("Mine", "Свои"),
            "description": T("Your entries: your own skills, rules and MCP.",
                             "Ваши записи: собственные скиллы, правила и MCP.")}
SECRET_KEY = re.compile(r"key|token|secret|password|auth", re.I)

TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o",
                     "p", "r", "s", "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e",
                     "yu", "ya"]))


def slug(text: str, fallback: str = "my-entry") -> str:
    """'Мои правила для Go' → 'moi-pravila-dlya-go' (id and category keys are Latin)."""
    s = "".join(TRANSLIT.get(ch, ch) for ch in text.lower())
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:48].strip("-") or fallback


def unique_id(cat: Catalog, base: str, own: str | None = None) -> str:
    eid, n = base, 2
    while eid in cat.entries and eid != own:
        eid = f"{base}-{n}"
        n += 1
    return eid


# ---- looking at a source -------------------------------------------------------------------

@dataclass
class Component:
    """One thing the entry can install; the form shows these as checkboxes."""
    key: str
    kind: str          # skills | rules | mcp | commands
    value: str         # path/glob, or the MCP server name
    label: str
    on: bool = False
    spec: dict | None = None  # MCP server definition


@dataclass
class Source:
    kind: str                     # local | github
    location: str                 # absolute folder or owner/repo
    ref: str | None
    snapshot: Path
    license: str
    components: list[Component] = field(default_factory=list)
    name: str = ""
    summary: str = ""
    description: str = ""
    plugin: bool = False

    def chosen(self, keys: list[str] | None = None) -> list[Component]:
        keys = set(keys) if keys is not None else {c.key for c in self.components if c.on}
        return [c for c in self.components if c.key in keys]

    def audit(self, keys: list[str] | None = None) -> AuditReport:
        paths: list[Path] = []
        for c in self.chosen(keys):
            if c.kind in ("skills", "commands"):
                paths += (list(self.snapshot.glob(c.value)) if any(ch in c.value for ch in "*?[")
                          else [self.snapshot / c.value])
            elif c.kind == "rules":
                paths.append(self.snapshot / c.value)
        if self.kind == "github" and not paths:
            paths = [self.snapshot]
        return audit_paths([p for p in paths if p.exists()], self.snapshot)


def frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*(\n|$)", text, re.S)
    out: dict[str, str] = {}
    if not m:
        return out
    key = None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            out[key] = val.strip("'\"") if val not in ("|", ">", "|-", ">-") else ""
        elif key and line.startswith((" ", "\t")):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def readme_intro(folder: Path) -> str:
    for name in ("README.md", "readme.md", "README.ru.md", "README"):
        f = folder / name
        if f.is_file():
            para: list[str] = []
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if not s or s.startswith(("#", "!", "<", "[!", "|", "```", "---")):
                    if para:
                        break
                    continue
                para.append(s)
            return " ".join(para)[:600]
    return ""


def first_sentence(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    m = re.match(r"^(.+?[.!?])(\s|$)", text)
    s = m.group(1) if m and len(m.group(1)) <= limit else text
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _skill_names(snap: Path, value: str) -> list[str]:
    if value == ".":
        return [snap.name]
    if any(ch in value for ch in "*?["):
        return sorted(p.name for p in snap.glob(value) if (p / "SKILL.md").is_file())
    return [Path(value).name]


def resolve_location(text: str) -> tuple[str, str, str | None]:
    """('local', folder, picked file) or ('github', owner/repo, None).

    Accepts paths with ~, quotes (terminals add them when a folder is dragged in) and file
    paths: a SKILL.md means its folder is the skill, another file is used as the rules file.
    """
    raw = text.strip().strip("'\"").strip()
    if raw.startswith("file://"):
        from urllib.parse import unquote, urlparse
        raw = unquote(urlparse(raw).path)
        if os.name == "nt" and re.match(r"^/[A-Za-z]:", raw):
            raw = raw[1:]
    p = Path(os.path.expandvars(raw)).expanduser()
    if raw and (p.exists() or raw.startswith(("/", "~", ".", "\\")) or re.match(r"^[A-Za-z]:[\\/]", raw)):
        if not p.exists():
            raise CatalogError(T(f"No such folder or file: {p}", f"Нет такой папки или файла: {p}"))
        p = p.resolve()
        if p.is_file():
            return "local", str(p.parent), p.name
        return "local", str(p), None
    return "github", parse_repo(raw), None


def inspect_source(text: str, ref: str | None = None, entry_id: str = "new") -> Source:
    """Look at a folder or repo and work out what can be installed from it."""
    kind, location, picked = resolve_location(text)
    if kind == "local":
        snap = Path(location)
        src = Source("local", location, None, snap, "own")
    else:
        sha = ref or remote_head(location)
        tmp = parse_entry({"id": entry_id, "source": {"repo": location, "ref": sha}}, Path(f"{entry_id}.toml"))
        snap = fetch(tmp, full=True)
        src = Source("github", location, sha, snap, detect_license(snap),
                     plugin=(snap / ".claude-plugin").is_dir())

    comps: list[Component] = []
    if picked and picked != "SKILL.md":
        comps.append(Component(f"rules:{picked}", "rules", picked, T(f"Rules from the file {picked}", f"Правила из файла {picked}"), True))
    else:
        if picked == "SKILL.md":
            chosen, others = ["."], []
        else:
            # a local folder may be big (a whole project): look only a few levels down
            chosen, others = detect_skills(snap, max_depth=4 if src.kind == "local" else None)
        for v in chosen:
            names = _skill_names(snap, v)
            label = (T("Skill — the whole folder", "Скилл — вся папка") if v == "." else
                     T(f"Skills {v} ({len(names)}: {', '.join(names[:4])}{'…' if len(names) > 4 else ''})", f"Скиллы {v} ({len(names)}: {', '.join(names[:4])}{'…' if len(names) > 4 else ''})")
                     if len(names) != 1 else T(f"Skill {v}", f"Скилл {v}"))
            comps.append(Component(f"skills:{v}", "skills", v, label, True))
        for v in others[:30]:
            comps.append(Component(f"skills:{v}", "skills", v, T(f"Skill {v}", f"Скилл {v}"), False))
        has_skills = bool(chosen)
        for name in RULE_FILES:
            if (snap / name).is_file():
                comps.append(Component(f"rules:{name}", "rules", name, T(f"Rules from {name}", f"Правила из {name}"),
                                       not has_skills and not any(c.kind == "rules" for c in comps)))
    for g in COMMAND_GLOBS:
        found = sorted(snap.glob(g))
        if found:
            comps.append(Component(f"commands:{g}", "commands", g,
                                   T(f"Slash commands {g} ({len(found)})", f"Slash-команды {g} ({len(found)})"), g.startswith(".claude")))
    for name, spec in detect_mcp(snap).items():
        if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
            continue
        plugin_bound = any("CLAUDE_PLUGIN_ROOT" in str(a) for a in spec.get("args", []))
        clean: dict = {}
        if spec.get("url"):
            clean["url"] = str(spec["url"])
        else:
            clean["command"] = str(spec["command"])
            clean["args"] = [str(a) for a in spec.get("args", [])]
        env = {k: ("" if SECRET_KEY.search(k) else str(v)) for k, v in (spec.get("env") or {}).items()}
        if env:
            clean["env"] = env
        headers = {k: ("" if SECRET_KEY.search(k) else str(v)) for k, v in (spec.get("headers") or {}).items()}
        if headers:
            clean["headers"] = headers
        what = clean.get("url") or " ".join([clean["command"], *clean["args"]])
        comps.append(Component(f"mcp:{name}", "mcp", name, f"MCP {name}: {what}", not plugin_bound, clean))
    src.components = comps

    # name and description suggestions
    skills = [c for c in comps if c.kind == "skills" and c.on]
    meta: dict[str, str] = {}
    if len(skills) == 1 and not any(ch in skills[0].value for ch in "*?["):
        f = snap / skills[0].value / "SKILL.md"
        if f.is_file():
            meta = frontmatter(f.read_text(encoding="utf-8", errors="replace"))
    base = Path(location).name if kind == "local" else location.split("/")[1]
    if picked and picked != "SKILL.md":
        base = Path(picked).stem if Path(picked).stem.upper() not in ("CLAUDE", "AGENTS", "GEMINI", "RULES") else base
    src.name = meta.get("name") or base
    desc = meta.get("description") or readme_intro(snap)
    src.description = desc
    src.summary = first_sentence(desc) if desc else ""
    return src


# ---- writing manifests ---------------------------------------------------------------------

def _str(s: str) -> str:
    s = "".join(ch for ch in s if ch in "\t\n" or (ord(ch) >= 0x20 and ord(ch) != 0x7F))
    return json.dumps(s, ensure_ascii=False)


def _ml(s: str) -> str:
    s = "".join(ch for ch in s if ch in "\t\n" or (ord(ch) >= 0x20 and ord(ch) != 0x7F))
    s = s.strip("\n").replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    if s.endswith('"'):
        s = s[:-1] + '\\"'
    return '"""\n' + s.strip("\n") + '\\\n"""'  # the trailing backslash keeps the last newline out


def _key(k: str) -> str:
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else _str(k)


def _val(v, multiline: bool = False) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return _ml(v) if multiline and "\n" in v else _str(v)
    if isinstance(v, list):
        return "[" + ", ".join(_val(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_val(x)}" for k, x in v.items()) + " }" if v else "{}"
    raise TypeError(T(f"cannot write this to TOML: {type(v)}", f"не умею записать в TOML: {type(v)}"))


def dump_toml(data: dict, comment: str = "") -> str:
    """Small TOML writer for manifests: scalars, then tables, sub-tables and arrays of tables."""
    out: list[str] = [f"# {line}" for line in comment.splitlines()] if comment else []
    tables, arrays = [], []
    for k, v in data.items():
        if isinstance(v, dict) and v:
            tables.append((k, v))
        elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            arrays.append((k, v))
        elif v is not None:
            out.append(f"{_key(k)} = {_val(v, multiline=(k == 'description'))}")
    for k, v in tables:
        nested = all(isinstance(x, dict) for x in v.values())
        if nested and k in ("mcp",):
            for name, spec in v.items():
                out += ["", f"[{_key(k)}.{_key(name)}]"]
                out += [f"{_key(a)} = {_val(b)}" for a, b in spec.items() if b is not None]
        else:
            out += ["", f"[{_key(k)}]"]
            out += [f"{_key(a)} = {_val(b, multiline=(a == 'description'))}" for a, b in v.items()
                    if b is not None]
    for k, items in arrays:
        for it in items:
            out += ["", f"[[{_key(k)}]]"]
            out += [f"{_key(a)} = {_val(b)}" for a, b in it.items() if b is not None]
    return "\n".join(out) + "\n"


@dataclass
class Form:
    """What the user filled in."""
    name: str
    id: str
    category: str
    summary: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    risk: str = "low"
    work_safe: bool = False
    reviewed: bool = True
    components: list[str] | None = None   # component keys; None — the source's defaults
    new_category: dict | None = None      # {"id", "title", "description"} for a new one


def check_form(cat: Catalog, src: Source, f: Form, editing: Entry | None = None) -> dict[str, str]:
    """Problems by field name; empty when the form can be saved."""
    errs: dict[str, str] = {}
    if not f.name.strip():
        errs["name"] = T("A name is required", "Нужно название")
    if not ID_RE.match(f.id or ""):
        errs["id"] = T("Lowercase Latin letters, digits and hyphens only, starting with a letter or digit", "Только строчные латинские буквы, цифры и дефис, с буквы или цифры")
    elif f.id in cat.entries and (editing is None or f.id != editing.id):
        errs["id"] = T(f"id «{f.id}» is taken ({cat.entries[f.id].name})", f"id «{f.id}» уже занят ({cat.entries[f.id].name})")
    if not f.summary.strip():
        errs["summary"] = T("One line: what it is and why", "Одна строка: что это и зачем")
    if f.new_category:
        nc = f.new_category
        if not nc.get("title", "").strip():
            errs["category"] = T("The new category needs a name", "Нужно название новой категории")
        elif not ID_RE.match(nc.get("id", "")):
            errs["category"] = T("Category key: Latin letters, digits, hyphens", "Ключ категории: латиница, цифры, дефис")
        elif nc["id"] in cat.categories:
            errs["category"] = T(f"Category «{nc['id']}» already exists — pick it from the list", f"Категория «{nc['id']}» уже есть — выберите её из списка")
    elif f.category not in cat.categories and f.category != OWN:
        errs["category"] = T("Choose a category", "Выберите категорию")
    chosen = src.chosen(f.components)
    if not chosen:
        errs["components"] = T("Tick at least one: skills, rules, MCP or commands", "Отметьте хотя бы одно: скиллы, правила, MCP или команды")
    elif sum(1 for c in chosen if c.kind == "rules") > 1:
        errs["components"] = T("Rules — only one file per entry", "Правила — только один файл на запись")
    if f.risk not in RISKS:
        errs["risk"] = T("low, medium or high", "low, medium или high")
    return errs


def entry_data(cat: Catalog, src: Source, f: Form, base: dict | None = None) -> dict:
    """Manifest as a dict. `base` (the old manifest when editing) keeps fields the form does not show."""
    data: dict = dict(base or {})
    for k in ("skills", "rules", "mcp", "commands"):
        data.pop(k, None)
    number = data.get("number") or cat.next_number(user=True)
    status = "verified" if (src.kind == "local" or f.reviewed) else "draft"
    head = {
        "id": f.id, "number": number, "name": f.name.strip(),
        "category": f.new_category["id"] if f.new_category else f.category,
        "summary": " ".join(f.summary.split()), "description": f.description.strip(),
        "status": status, "risk": f.risk, "work_safe": bool(f.work_safe),
        "confirm": f.risk == "high",
        "reviewed": today() if status == "verified" else "",
        "tags": [t for t in dict.fromkeys(t.strip().lower() for t in f.tags) if t],
    }
    out: dict = dict(head)
    source = {"path": src.location} if src.kind == "local" else {"repo": src.location, "ref": src.ref}
    source["license"] = (base or {}).get("source", {}).get("license") or src.license
    out["source"] = source
    chosen = src.chosen(f.components)
    skills = []
    for c in chosen:
        if c.kind == "skills":
            spec: dict = {"path": c.value}
            if c.value == ".":
                skill_md = src.snapshot / "SKILL.md"
                fm = frontmatter(skill_md.read_text(encoding="utf-8", errors="replace")) if skill_md.is_file() else {}
                spec["name"] = fm.get("name") if ID_RE.match(fm.get("name", "")) else f.id
                spec["exclude"] = ROOT_SKILL_EXCLUDE
            skills.append(spec)
    if skills:
        out["skills"] = skills
    rules = [c for c in chosen if c.kind == "rules"]
    if rules:
        out["rules"] = {"path": rules[0].value}
    cmds = [c.value for c in chosen if c.kind == "commands"]
    if cmds:
        out["commands"] = cmds
    mcp = {c.value: c.spec for c in chosen if c.kind == "mcp" and c.spec}
    if mcp:
        out["mcp"] = mcp
    for k, v in data.items():  # what the form does not edit: risk_notes, manual, targets…
        if k not in out:
            out[k] = v
    return out


def user_dir(cat: Catalog) -> Path:
    if cat.user_dir is None:
        raise CatalogError(T("Your own catalog is off (started with a separate catalog without your entries)", "Свой каталог отключён (запущено с отдельным каталогом без пользовательских записей)"))
    return cat.user_dir


def add_category(cat: Catalog, key: str, title: str, description: str = "") -> None:
    d = user_dir(cat)
    d.mkdir(parents=True, exist_ok=True)
    f = d / CATEGORIES_FILE
    data: dict = {}
    if f.is_file():
        with f.open("rb") as fh:
            data = tomllib.load(fh)
    data[key] = {"order": 0, "title": title.strip(), "description": description.strip()}
    text = "\n\n".join(dump_toml({k: v}).strip() for k, v in data.items())
    header = T("# Your categories — skillpantry shows them together with the shared ones.\n\n", "# Ваши категории — skillpantry показывает их вместе с общими.\n\n")
    f.write_bytes((header + text + "\n").encode("utf-8"))


def save(cat: Catalog, src: Source, f: Form, editing: Entry | None = None) -> Path:
    """Write the manifest into the user catalog and reload the catalog. Returns the file."""
    errs = check_form(cat, src, f, editing)
    if errs:
        raise CatalogError("; ".join(errs.values()))
    d = user_dir(cat)
    d.mkdir(parents=True, exist_ok=True)
    base = None
    if editing is not None:
        if editing.origin != "user":
            raise CatalogError(T("Only your own entries can be edited with the form", "Изменять через форму можно только свои записи"))
        with editing.file.open("rb") as fh:
            base = tomllib.load(fh)
    data = entry_data(cat, src, f, base)
    path = d / f"{f.id}.toml"
    check = parse_entry(tomllib.loads(dump_toml(data)), path)
    problems = validate_entry(check)
    if problems:
        raise CatalogError("; ".join(problems))
    if f.new_category:
        add_category(cat, f.new_category["id"], f.new_category["title"], f.new_category.get("description", ""))
    elif f.category == OWN and f.category not in cat.categories:
        oc = own_category()
        add_category(cat, OWN, oc["title"], oc["description"])
    text = dump_toml(data, comment=T("Your own skillpantry entry. Edit: skillpantry → Catalog → entry → Edit.", "Своя запись skillpantry. Изменить: skillpantry → Каталог → запись → Изменить."))
    path.write_bytes(text.encode("utf-8"))
    if editing is not None and editing.file != path:
        editing.file.unlink(missing_ok=True)
    cat.load()
    return path


def delete(cat: Catalog, entry: Entry) -> None:
    if entry.origin != "user":
        raise CatalogError(T("Only your own entries can be removed from the catalog", "Удалить из каталога можно только свою запись"))
    entry.file.unlink(missing_ok=True)
    cat.load()


def form_from_entry(e: Entry) -> Form:
    return Form(name=e.name, id=e.id, category=e.category, summary=e.summary_text,
                description=e.description_text, tags=list(e.tags), risk=e.risk, work_safe=e.work_safe,
                reviewed=e.status == "verified", components=None)


def mark_entry_components(src: Source, e: Entry) -> list[str]:
    """Tick what the entry installs now; add components the detection did not find."""
    want: list[tuple[str, str]] = []
    for sk in e.skills:
        if sk.path:
            want.append(("skills", sk.path))
    if e.rules and e.rules.get("path"):
        want.append(("rules", e.rules["path"]))
    want += [("commands", c) for c in e.commands]
    want += [("mcp", m.name) for m in e.mcp]
    keys = []
    for kind, value in want:
        key = f"{kind}:{value}"
        if not any(c.key == key for c in src.components):
            spec = None
            if kind == "mcp":
                m = next(m for m in e.mcp if m.name == value)
                spec = {k: v for k, v in (("command", m.command), ("args", m.args or None),
                                          ("env", m.env or None), ("url", m.url),
                                          ("headers", m.headers or None)) if v}
            src.components.append(Component(key, kind, value, f"{kind} {value}", True, spec))
        keys.append(key)
    for c in src.components:
        c.on = c.key in keys
    return keys


def version_of(e: Entry) -> str | None:
    """Current version of a local entry (content hash of the paths it installs)."""
    return content_hash(e.local, e.local_paths()) if e.local else e.ref
