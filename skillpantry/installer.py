"""Plan, apply and undo installs. Every change is recorded so it can be removed."""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from skillpantry import textio
from skillpantry.i18n import T
from skillpantry.catalog import Entry, SkillSpec
from skillpantry.fetch import home_dir
from skillpantry.targets import (Layout, claude_mcp_add_command, mcp_codex_toml,
                                mcp_json, required_env)

DEFAULT_EXCLUDE = {".git", ".github", ".skillpantry-ok", "node_modules", "__pycache__"}


class InstallError(Exception):
    pass


# ---- state (what skillpantry has installed where) ---------------------------

def state_file() -> Path:
    return home_dir() / "installs.json"


def load_state() -> dict:
    f = state_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as ex:
            raise InstallError(T(f"Damaged {f}: {ex}", f"Повреждён {f}: {ex}")) from ex
    return {"version": 1, "installs": {}}


def save_state(state: dict) -> None:
    f = state_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    textio.write(tmp, json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(f)


def install_key(layout: Layout, entry_id: str) -> str:
    return f"{layout.target}|{layout.scope}|{layout.root.resolve()}|{entry_id}"


def owned_paths(state: dict, exclude_key: str | None = None) -> dict[str, str]:
    """Paths installed by other skillpantry records: {path: entry id}."""
    out: dict[str, str] = {}
    for k, rec in state["installs"].items():
        if k == exclude_key:
            continue
        for a in rec.get("actions", []):
            if a["type"] in ("dir", "file"):
                out[a["path"]] = rec["id"]
    return out


# ---- actions -----------------------------------------------------------------

@dataclass
class Action:
    kind: str                     # dir | file | md-block | json-key | toml-block
    path: Path
    describe: str
    src: Path | None = None
    content: str | None = None
    marker: str | None = None
    keypath: list[str] = field(default_factory=list)
    value: object = None
    exclude: set[str] = field(default_factory=set)


def _backup(path: Path, stamp: str) -> None:
    if not path.exists():
        return
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(path.resolve()))
    dst = home_dir() / "backups" / stamp / safe
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(path, dst)


def _copytree(src: Path, dst: Path, exclude: set[str]) -> None:
    def ignore(d, names):
        rel_d = Path(d).relative_to(src).as_posix()
        out = []
        for n in names:
            rel = n if rel_d == "." else f"{rel_d}/{n}"
            if n in DEFAULT_EXCLUDE or any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(n, pat)
                                           for pat in exclude):
                out.append(n)
        return out
    shutil.copytree(src, dst, ignore=ignore)


PREFIX = "skillpantry"
LEGACY_PREFIXES = ("skillshelf",)  # the project's name before 0.5.0: its blocks are still recognized


def block_markers(marker: str, comment: str, prefix: str = PREFIX) -> tuple[str, str]:
    if comment == "md":
        return f"<!-- >>> {prefix}:{marker} -->", f"<!-- <<< {prefix}:{marker} -->"
    return f"# >>> {prefix}:{marker}", f"# <<< {prefix}:{marker}"


def _drop_legacy_blocks(text: str, marker: str, comment: str) -> str:
    for prefix in LEGACY_PREFIXES:
        begin, end = block_markers(marker, comment, prefix)
        if begin in text:
            text = _replace_block(text, begin, end, None)
    return text


def _replace_block(text: str, begin: str, end: str, body: str | None) -> str:
    pat = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.S)
    if body is None:
        new = pat.sub("", text)
        return new.rstrip("\n") + "\n" if new.strip() else ""
    block = f"{begin}\n{body.rstrip()}\n{end}\n"
    if pat.search(text):
        return pat.sub(lambda _m: block, text, count=1)
    sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
    return text + sep + block


def _read_json(path: Path) -> dict:
    text, _ = textio.read(path)
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as ex:
        raise InstallError(T(f"{path} — not valid JSON ({ex}); I only edit valid files", f"{path} — не валидный JSON ({ex}); правлю только корректные файлы")) from ex
    if not isinstance(data, dict):
        raise InstallError(T(f"{path}: a JSON object was expected", f"{path}: ожидался JSON-объект"))
    return data


def _write_json(path: Path, data: dict) -> None:
    _, eol = textio.read(path)
    textio.write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", eol)


def apply(action: Action, stamp: str) -> dict:
    p = action.path
    if action.kind == "dir":
        if p.exists():
            shutil.rmtree(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        _copytree(action.src, p, action.exclude)
        return {"type": "dir", "path": str(p)}
    if action.kind == "file":
        p.parent.mkdir(parents=True, exist_ok=True)
        if action.src:
            shutil.copy2(action.src, p)
        else:
            textio.write(p, action.content)
        return {"type": "file", "path": str(p)}
    if action.kind in ("md-block", "toml-block"):
        _backup(p, stamp)
        comment = "md" if action.kind == "md-block" else "hash"
        begin, end = block_markers(action.marker, comment)
        text, eol = textio.read(p)
        textio.write(p, _replace_block(_drop_legacy_blocks(text, action.marker, comment), begin, end,
                                       action.content), eol)
        return {"type": action.kind, "path": str(p), "marker": action.marker,
                "created": not text}
    if action.kind == "json-key":
        _backup(p, stamp)
        existed = p.exists()
        data = _read_json(p)
        node = data
        for k in action.keypath[:-1]:
            node = node.setdefault(k, {})
            if not isinstance(node, dict):
                raise InstallError(T(f"{p}: '{k}' is not an object", f"{p}: '{k}' не объект"))
        previous = node.get(action.keypath[-1])
        node[action.keypath[-1]] = action.value
        _write_json(p, data)
        return {"type": "json-key", "path": str(p), "keypath": action.keypath,
                "previous": previous, "created": not existed}
    raise InstallError(T(f"unknown action {action.kind}", f"неизвестное действие {action.kind}"))


def undo(rec: dict) -> None:
    p = Path(rec["path"])
    t = rec["type"]
    stop = rec.get("stop")
    if t == "dir":
        if p.exists():
            shutil.rmtree(p)
        _prune_empty(p.parent, stop)
    elif t == "file":
        if p.exists():
            p.unlink()
        _prune_empty(p.parent, stop)
    elif t in ("md-block", "toml-block"):
        if not p.exists():
            return
        comment = "md" if t == "md-block" else "hash"
        begin, end = block_markers(rec["marker"], comment)
        old, eol = textio.read(p)
        text = _drop_legacy_blocks(_replace_block(old, begin, end, None), rec["marker"], comment)
        if not text.strip():
            p.unlink()
            _prune_empty(p.parent, stop)
        else:
            textio.write(p, text, eol)
    elif t == "json-key":
        if not p.exists():
            return
        data = _read_json(p)
        node = data
        for k in rec["keypath"][:-1]:
            node = node.get(k, {})
        if rec.get("previous") is not None:
            node[rec["keypath"][-1]] = rec["previous"]
        else:
            node.pop(rec["keypath"][-1], None)
        parent_key = rec["keypath"][0]
        if parent_key in data and data[parent_key] == {}:
            data.pop(parent_key)
        if not data:
            p.unlink()
            _prune_empty(p.parent, stop)
        else:
            _write_json(p, data)


def _prune_empty(d: Path, stop: str | None = None) -> None:
    """Remove now-empty dirs left behind (skills/, .claude/ …), never the install root
    or the home directory."""
    root = Path(stop).resolve() if stop else None
    for _ in range(3):
        if (not d.exists() or any(d.iterdir())
                or (root is not None and d.resolve() == root) or d.resolve() == Path.home()):
            return
        d.rmdir()
        d = d.parent


# ---- planning ----------------------------------------------------------------

@dataclass
class Plan:
    entry: Entry
    layout: Layout
    actions: list[Action] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    manual: list[str] = field(default_factory=list)
    env: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    options: dict = field(default_factory=dict)   # remembered so an update repeats them


def expand_skills(entry: Entry, snap: Path, target: str, only: set[str] | None,
                  with_optional: bool, skip: set[str] | None = None,
                  include: set[int] | None = None) -> list[tuple[str, Path, SkillSpec]]:
    """Resolve skill specs (paths may be globs) into (name, src dir, spec).
    `include` holds indexes of optional specs chosen one by one."""
    out = []
    for idx, spec in enumerate(entry.skills):
        if spec.optional and not with_optional and not only and idx not in (include or ()):
            continue
        rel = spec.path_for(target)
        if not rel:
            continue
        if rel in (".", ""):
            matches = [snap]
        elif any(ch in rel for ch in "*?["):
            matches = sorted(p for p in snap.glob(rel) if p.is_dir() and (p / "SKILL.md").exists())
        else:
            matches = [snap / rel]
        if not matches:
            raise InstallError(T(f"{entry.id}: the pattern '{rel}' matched no skills " f"(outdated manifest? check skillpantry audit {entry.id})", f"{entry.id}: по шаблону '{rel}' не найдено ни одного скилла "
                               f"(манифест устарел? проверьте skillpantry audit {entry.id})"))
        for m in matches:
            if not (m / "SKILL.md").exists():
                raise InstallError(T(f"{entry.id}: no SKILL.md in {m.relative_to(snap) if m != snap else '.'}", f"{entry.id}: в {m.relative_to(snap) if m != snap else '.'} нет SKILL.md"))
            name = (spec.name if (spec.name and len(matches) == 1)
                    else skill_name(m / "SKILL.md") or (entry.id if m == snap else m.name))
            if (only and name not in only) or (skip and name in skip):
                continue
            out.append((name, m, spec))
    if only:
        missing = only - {n for n, _, _ in out}
        if missing:
            raise InstallError(T(f"{entry.id}: no skills {', '.join(sorted(missing))}", f"{entry.id}: нет скиллов {', '.join(sorted(missing))}"))
    return out


def skill_name(skill_md: Path) -> str | None:
    """`name:` from SKILL.md frontmatter — the Agent Skills spec wants the folder to match it."""
    try:
        text = skill_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    head = text.split("---", 2)[1] if text.count("---") >= 2 else ""
    m = re.search(r"^name:\s*[\"']?([a-z0-9][a-z0-9-]{0,63})[\"']?\s*$", head, re.M)
    return m.group(1) if m else None


def list_variants(entry: Entry, snap: Path) -> list[tuple[str, Path]]:
    if not entry.variants:
        return []
    files = sorted(p for p in snap.glob(entry.variants["glob"]) if p.is_file())
    return [(p.parent.name, p) for p in files]


def read_rules(entry: Entry, snap: Path) -> str:
    f = snap / entry.rules["path"]
    if not f.is_file():
        raise InstallError(T(f"{entry.id}: no rules file {entry.rules['path']}", f"{entry.id}: нет файла правил {entry.rules['path']}"))
    text = f.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    text = text.strip()
    first, _, rest = text.partition("\n")
    if re.fullmatch(r"#\s+(CLAUDE|AGENTS|GEMINI|CURSOR|RULES)\.md\s*", first, re.I):
        text = f"# {entry.name}\n{rest}"
    return text


def plan_install(entry: Entry, layout: Layout, snap: Path | None, *,
                 only_skills: set[str] | None = None, with_optional: bool = False,
                 variant: str | None = None, force: bool = False,
                 state: dict | None = None, batch: dict[str, str] | None = None,
                 skip_skills: set[str] | None = None,
                 include_optional: set[int] | None = None) -> Plan:
    """Build the list of changes. `batch` maps paths already claimed by other
    entries in the same install run to their ids, so two entries never write
    the same file."""
    state = state or load_state()
    plan = Plan(entry, layout)
    plan.options = {k: v for k, v in {
        "skills": sorted(only_skills) if only_skills else None,
        "skip_skills": sorted(skip_skills) if skip_skills else None,
        "optional": with_optional or None,
        "include_optional": sorted(include_optional) if include_optional else None,
        "variant": variant,
    }.items() if v}
    t = layout.target
    key = install_key(layout, entry.id)
    previous = state["installs"].get(key, {})
    ours = {a["path"] for a in previous.get("actions", []) if a["type"] in ("dir", "file")}
    others = owned_paths(state, exclude_key=key)
    others.update({p: i for p, i in (batch or {}).items() if i != entry.id})
    head = f"{entry.repo or entry.id}@{entry.ref[:12]}" if entry.ref else entry.id

    def claim(path: Path, what: str) -> bool:
        sp = str(path)
        if sp in others:
            plan.conflicts.append(T(f"{what}: {path.name} is already installed by '{others[sp]}' — " f"choose one or add --skip-skill {path.name}", f"{what}: {path.name} уже ставит запись '{others[sp]}' — "
                                  f"выберите одну или добавьте --skip-skill {path.name}"))
            return False
        if path.exists() and sp not in ours and not force:
            plan.conflicts.append(T(f"{what}: {path} already exists (not from skillpantry). " f"--force replaces it and keeps a copy in ~/.skillpantry/backups", f"{what}: {path} уже существует (не от skillpantry). "
                                  f"Флаг --force заменит его, копия будет в ~/.skillpantry/backups"))
            return False
        return True

    plan.manual = entry.manual_for(t)
    support = entry.support(t)
    if support == "none":
        reason = (T("a workspace is installed only into a folder (target folder)", "рабочее пространство ставится только в папку (цель folder)") if entry.workspace
                  else T("no way to install for this target", "для этой цели нет способа установки"))
        plan.skipped.append(reason)
        return plan
    if support == "manual" or snap is None:
        return plan

    # workspace: the whole repo snapshot becomes a folder to work in
    if entry.workspace:
        dst = layout.root / entry.id
        if claim(dst, T("workspace folder", "рабочая папка")):
            plan.actions.append(Action("dir", dst, T(f"workspace folder ← {head}", f"рабочая папка ← {head}"), src=snap,
                                       exclude=set(entry.workspace.get("exclude", []))))
    # skills
    if entry.skills:
        sdir = layout.skills_dir(entry.id)
        if sdir is None:
            plan.skipped.append(T("skills: this target has no skills folder", "скиллы: у этой цели нет каталога скиллов"))
        else:
            for name, src, spec in expand_skills(entry, snap, t, only_skills, with_optional, skip_skills,
                                                 include_optional):
                dst = sdir / name
                if claim(dst, T("skill", "скилл")):
                    plan.actions.append(Action("dir", dst, T(f"skill {name}", f"скилл {name}"), src=src,
                                               exclude=set(spec.exclude)))
    # rules
    if entry.rules:
        rf = layout.rules_file(entry.id)
        if rf is None:
            plan.skipped.append(T("rules: Cursor user rules live in the app settings " "— install into a project or copy them by hand", "правила: у Cursor пользовательские правила задаются в настройках "
                                "приложения — поставьте в проект или скопируйте вручную"))
        else:
            path, mode = rf
            body = read_rules(entry, snap)
            src_note = T(f"Source: {entry.source_url} @ {(entry.ref or '?')[:12]} · installed by skillpantry", f"Источник: {entry.source_url} @ {(entry.ref or '?')[:12]} · ставит skillpantry")
            if mode == "block":
                content = f"<!-- {src_note} -->\n{body}"
                plan.actions.append(Action("md-block", path, T("rules (block)", "правила (блок)"),
                                           content=content, marker=entry.id))
            else:
                if t == "cursor":
                    desc = entry.summary.replace("\n", " ")
                    content = (f"---\ndescription: {desc}\nalwaysApply: true\n---\n"
                               f"<!-- {src_note} -->\n{body}\n")
                else:
                    content = f"<!-- {src_note} -->\n{body}\n"
                if claim(path, T("rules", "правила")):
                    plan.actions.append(Action("file", path, T("rules", "правила"), content=content))
    # MCP
    for server in entry.mcp:
        plan.env += required_env(server)
        mf = layout.mcp_file(entry.id)
        if mf is None:
            plan.manual.append(claude_mcp_add_command(server))
            plan.notes.append(T(f"MCP {server.name}: a global MCP in Claude Code is added " f"with the command above (skillpantry does not edit ~/.claude.json)", f"MCP {server.name}: глобальный MCP в Claude Code добавляется "
                              f"командой выше (skillpantry не правит ~/.claude.json)"))
            continue
        path, fmt = mf
        if fmt == "codex":
            text, _ = textio.read(path)
            header = re.compile(rf"^\[mcp_servers\.\"?{re.escape(server.name)}\"?\]", re.M)
            begin, end = block_markers(f"{entry.id}:{server.name}", "hash")
            mine = _drop_legacy_blocks(_replace_block(text, begin, end, None), f"{entry.id}:{server.name}", "hash")
            if header.search(mine):
                if not force:
                    plan.conflicts.append(T(f"MCP {server.name}: already defined by hand in {path}", f"MCP {server.name}: уже описан в {path} вручную"))
                    continue
            plan.actions.append(Action("toml-block", path, f"MCP {server.name}",
                                       content=mcp_codex_toml(server),
                                       marker=f"{entry.id}:{server.name}"))
        else:
            value = mcp_json(server, fmt)
            existing = _read_json(path).get("mcpServers", {}).get(server.name) if path.exists() else None
            mine = any(a["type"] == "json-key" and a["path"] == str(path)
                       and a["keypath"][-1] == server.name for a in previous.get("actions", []))
            if existing is not None and existing != value and not mine and not force:
                plan.conflicts.append(T(f"MCP {server.name}: {path} already has another server with this name", f"MCP {server.name}: в {path} уже есть другой сервер с этим именем"))
                continue
            plan.actions.append(Action("json-key", path, f"MCP {server.name}",
                                       keypath=["mcpServers", server.name], value=value))
    # slash commands
    if entry.commands:
        cdir = layout.commands_dir(entry.id)
        if cdir is None:
            plan.skipped.append(T("only Claude Code has slash commands", "slash-команды есть только у Claude Code"))
        else:
            for c in entry.commands:
                for src in sorted(snap.glob(c)):
                    dst = cdir / src.name
                    if claim(dst, T("command", "команда")):
                        plan.actions.append(Action("file", dst, T(f"command /{src.stem}", f"команда /{src.stem}"), src=src))
    # variants (choose one file, e.g. a DESIGN.md)
    if entry.variants:
        root = layout.variant_root(entry.id)
        options = dict(list_variants(entry, snap))
        if root is None:
            plan.skipped.append(T("a variant goes only into a project or a folder", "вариант ставится только в проект или папку"))
        elif not variant:
            plan.skipped.append(T(f"no variant chosen (--variant), {len(options)} available: " f"{', '.join(list(options)[:8])}…", f"не выбран вариант (--variant), доступно {len(options)}: "
                                f"{', '.join(list(options)[:8])}…"))
        elif variant not in options:
            raise InstallError(T(f"{entry.id}: no variant '{variant}'", f"{entry.id}: нет варианта '{variant}'"))
        else:
            dst = root / entry.variants["dest"]
            if claim(dst, T("variant file", "файл варианта")):
                plan.actions.append(Action("file", dst, f"{entry.variants['dest']} ← {variant}",
                                           src=options[variant]))
    # a readme next to everything installed into a plain folder
    if t == "folder" and plan.actions and not entry.workspace:
        dst = layout.root / entry.id / "SKILLPANTRY.md"
        plan.actions.append(Action("file", dst, T("SKILLPANTRY.md (source, license, steps)", "SKILLPANTRY.md (источник, лицензия, шаги)"),
                                   content=folder_readme(entry, plan)))
    return plan


def folder_readme(entry: Entry, plan: Plan) -> str:
    lines = [f"# {entry.name}", "", entry.summary, "",
             T(f"- Source: {entry.source_url}", f"- Источник: {entry.source_url}"),
             (T(f"- Content version: `{entry.ref}`", f"- Версия содержимого: `{entry.ref}`") if entry.local else T(f"- Commit: `{entry.ref}`", f"- Коммит: `{entry.ref}`")),
             T(f"- License: {entry.license or 'own'}", f"- Лицензия: {entry.license or 'своё'}"), T(f"- Risk: {entry.risk}", f"- Риск: {entry.risk}"),
             T(f"- Installed by skillpantry: {datetime.now():%Y-%m-%d %H:%M}", f"- Поставлено skillpantry: {datetime.now():%Y-%m-%d %H:%M}"), ""]
    if entry.description:
        lines += [entry.description, ""]
    if entry.risk_notes:
        lines += [T("## Risks", "## Риски"), *[f"- {n}" for n in entry.risk_notes], ""]
    if plan.manual:
        lines += [T("## Manual steps", "## Ручные шаги"), *[f"- `{m}`" for m in plan.manual], ""]
    if plan.env:
        lines += [T("## Environment variables", "## Переменные окружения"), *[f"- `{v}`" for v in plan.env], ""]
    lines += [T("## How to connect", "## Как подключить"),
              T("- Claude Code: folders from `skills/` → `.claude/skills/`, RULES.md → CLAUDE.md, mcp.json → `.mcp.json`", "- Claude Code: папки из `skills/` → `.claude/skills/`, RULES.md → CLAUDE.md, mcp.json → `.mcp.json`"),
              "- Codex: `skills/` → `.agents/skills/`, RULES.md → AGENTS.md",
              "- Gemini CLI: `skills/` → `.gemini/skills/`, RULES.md → GEMINI.md",
              "- Cursor: `skills/` → `.cursor/skills/`, RULES.md → `.cursor/rules/*.mdc`", ""]
    return "\n".join(lines)


def execute(plan: Plan, force: bool = False) -> dict:
    if plan.conflicts and not force:
        raise InstallError(T("Conflicts:\n  ", "Конфликты:\n  ") + "\n  ".join(plan.conflicts))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    state = load_state()
    key = install_key(plan.layout, plan.entry.id)
    old = state["installs"].get(key, {})
    old_paths = {(a["type"], a["path"], a.get("marker"), tuple(a.get("keypath", [])))
                 for a in old.get("actions", [])}
    done: list[dict] = []
    try:
        for a in plan.actions:
            if a.kind in ("dir", "file") and a.path.exists() and force:
                if str(a.path) not in {x["path"] for x in old.get("actions", [])}:
                    _backup_tree(a.path, stamp)
            rec = apply(a, stamp)
            rec["stop"] = str(plan.layout.root.resolve())
            done.append(rec)
    except Exception:
        for rec in reversed(done):
            try:
                undo(rec)
            except Exception:
                pass
        raise
    # drop leftovers from a previous install that this plan no longer produces
    new_paths = {(a["type"], a["path"], a.get("marker"), tuple(a.get("keypath", []))) for a in done}
    for rec in old.get("actions", []):
        k = (rec["type"], rec["path"], rec.get("marker"), tuple(rec.get("keypath", [])))
        if k not in new_paths:
            undo(rec)
    for rec in done:  # keep the original "previous" value across reinstalls
        k = (rec["type"], rec["path"], rec.get("marker"), tuple(rec.get("keypath", [])))
        if rec["type"] == "json-key" and k in old_paths:
            for o in old["actions"]:
                if o["type"] == "json-key" and o["path"] == rec["path"] and o["keypath"] == rec["keypath"]:
                    rec["previous"] = o.get("previous")
                    rec["created"] = o.get("created", rec.get("created"))
        if rec["type"] in ("md-block", "toml-block") and k in old_paths:
            for o in old["actions"]:
                if o["type"] == rec["type"] and o["path"] == rec["path"] and o.get("marker") == rec.get("marker"):
                    rec["created"] = o.get("created", rec.get("created"))
    record = {
        "id": plan.entry.id, "target": plan.layout.target, "scope": plan.layout.scope,
        "root": str(plan.layout.root.resolve()), "repo": plan.entry.repo, "local": plan.entry.local, "ref": plan.entry.ref,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
        "actions": done, "manual": plan.manual, "env": plan.env, "options": plan.options,
    }
    state["installs"][key] = record
    save_state(state)
    return record


def _backup_tree(path: Path, stamp: str) -> None:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(path.resolve()))
    dst = home_dir() / "backups" / stamp / safe
    dst.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        shutil.copytree(path, dst, dirs_exist_ok=True)
        shutil.rmtree(path)
    else:
        shutil.copy2(path, dst)


def uninstall(layout: Layout, entry_id: str) -> dict:
    state = load_state()
    key = install_key(layout, entry_id)
    rec = state["installs"].get(key)
    if not rec:
        raise InstallError(T(f"'{entry_id}' was not installed by skillpantry for {layout.label}", f"'{entry_id}' не установлен skillpantry для {layout.label}"))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for a in reversed(rec["actions"]):
        if a["type"] in ("md-block", "toml-block", "json-key"):
            _backup(Path(a["path"]), stamp)
        undo(a)
    del state["installs"][key]
    save_state(state)
    return rec


def installed(root: Path | None = None) -> list[dict]:
    state = load_state()
    recs = list(state["installs"].values())
    if root is not None:
        r = str(root.resolve())
        recs = [x for x in recs if x["root"] == r or x["scope"] == "global"]
    return sorted(recs, key=lambda x: (x["root"], x["target"], x["id"]))
