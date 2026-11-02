"""Catalog upkeep: draft new entries, check for updates, bump pins, approve reviews."""

from __future__ import annotations

from skillpantry.i18n import T

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from skillpantry.audit import audit_paths
from skillpantry.catalog import Catalog, Entry, parse_entry, set_field, today
from skillpantry.fetch import fetch, license_text, remote_head

HARNESS_DIRS = (".cursor", ".gemini", ".codex", ".opencode", ".qwen", ".kimi", ".grok", ".pi",
                ".devin", ".kiro", ".qoder", ".vibe", ".omp", ".dsh", ".rovodev", ".openclaw",
                ".antigravitycli", ".github", "node_modules", "legacy", "tests", "examples")
PREFERRED = ("skills", ".agents/skills", ".claude/skills")


def parse_repo(url_or_slug: str) -> str:
    s = url_or_slug.strip().removesuffix(".git").rstrip("/")
    m = re.search(r"github\.com[/:]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", s)
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", s):
        return s
    raise ValueError(T(f"Does not look like a GitHub repository: {url_or_slug}", f"Не похоже на GitHub-репозиторий: {url_or_slug}"))


def make_id(repo: str) -> str:
    name = repo.split("/")[1].lower()
    return re.sub(r"[^a-z0-9-]+", "-", name).strip("-") or "entry"


def detect_license(snap: Path) -> str:
    for f in (snap / ".claude-plugin" / "plugin.json", snap / "package.json"):
        if f.exists():
            try:
                lic = json.loads(f.read_text(encoding="utf-8")).get("license")
                if isinstance(lic, str) and lic:
                    return lic
            except json.JSONDecodeError:
                pass
    text = license_text(snap)
    if not text:
        return "none"
    head = text[:600]
    for needle, spdx in (("MIT License", "MIT"), ("Permission is hereby granted, free of charge", "MIT"),
                         ("Apache License", "Apache-2.0"), ("GNU AFFERO", "AGPL-3.0"),
                         ("GNU LESSER", "LGPL-3.0"), ("GNU GENERAL PUBLIC", "GPL-3.0"),
                         ("ISC License", "ISC"), ("Attribution 4.0", "CC-BY-4.0"),
                         ("BSD", "BSD"), ("Mozilla Public License", "MPL-2.0"),
                         ("The Unlicense", "Unlicense")):
        if needle.lower() in head.lower():
            return spdx
    return "unknown"


def _skill_files(root: Path, max_depth: int | None):
    if max_depth is None:
        yield from root.rglob("SKILL.md")
        return
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        dirnames[:] = [] if depth >= max_depth else [
            d for d in dirnames if d not in (".git", "node_modules", "__pycache__", ".venv", "venv")]
        if "SKILL.md" in filenames:
            yield Path(dirpath) / "SKILL.md"


def detect_skills(snap: Path, max_depth: int | None = None) -> tuple[list[str], list[str]]:
    """Return (chosen skill paths/globs, other skill locations for the reviewer)."""
    found = []
    for f in _skill_files(snap, max_depth):
        rel = f.parent.relative_to(snap).as_posix()
        if rel == ".":
            found.append(".")
            continue
        if any(part in HARNESS_DIRS for part in Path(rel).parts):
            continue
        found.append(rel)
    groups: dict[str, list[str]] = {}
    for rel in found:
        parent = rel.rsplit("/", 1)[0] if "/" in rel else ""
        groups.setdefault(parent, []).append(rel)
    if not groups:
        return [], []
    ranked = sorted(groups, key=lambda g: (
        0 if g in PREFERRED else 1 if g.endswith("/skills") else 2, -len(groups[g]), g))
    best = ranked[0]
    if best == "" and "." in groups[best]:
        chosen = ["."]
    elif len(groups[best]) > 1:
        chosen = [f"{best}/*" if best else "*"]
    else:
        chosen = groups[best]
    others = [p for g in ranked[1:] for p in groups[g]]
    return chosen, others


def detect_mcp(snap: Path) -> dict:
    for f in (snap / ".mcp.json", snap / ".claude-plugin" / "plugin.json"):
        if f.exists():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            servers = data.get("mcpServers")
            if isinstance(servers, dict):
                return servers
    return {}


def _q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def draft_manifest(cat: Catalog, url: str, entry_id: str | None = None) -> tuple[Path, dict]:
    repo = parse_repo(url)
    eid = entry_id or make_id(repo)
    if eid in cat.entries:
        raise ValueError(T(f"Entry '{eid}' already exists: {cat.entries[eid].file}", f"Запись '{eid}' уже есть: {cat.entries[eid].file}"))
    sha = remote_head(repo)
    tmp = parse_entry({"id": eid, "source": {"repo": repo, "ref": sha}}, cat.dir / f"{eid}.toml")
    snap = fetch(tmp, full=True)
    skills, other_skills = detect_skills(snap)
    mcp = detect_mcp(snap)
    lic = detect_license(snap)
    rules = next((n for n in ("CLAUDE.md", "AGENTS.md") if (snap / n).is_file()), None)
    plugin = (snap / ".claude-plugin").is_dir()
    targets = [p for p in (snap / s if s not in (".",) and "*" not in s else snap for s in skills)]
    report = audit_paths(targets or [snap], snap)

    L = [f"id = {_q(eid)}", f"number = {cat.next_number()}", f"name = {_q(repo.split('/')[1])}",
         'category = "TODO"  # ' + ", ".join(c.id for c in cat.category_order()), T('summary = "TODO: one line — what it is and why"', 'summary = "TODO: одна строка — что это и зачем"'),
         T('description = """\nTODO: what it does, when to use it, what it needs to work.\n"""', 'description = """\nTODO: что делает, когда использовать, что нужно для работы.\n"""'),
         'status = "draft"', 'risk = "TODO: low | medium | high"', "work_safe = false",
         "confirm = false", 'reviewed = ""', "tags = []", "risk_notes = []", "",
         "[source]", f"repo = {_q(repo)}", f"ref = {_q(sha)}", f"license = {_q(lic)}"]
    for s in skills:
        L += ["", "[[skills]]", f"path = {_q(s)}"]
        if s == ".":
            L.append(f"name = {_q(eid)}")
            L.append('exclude = ["README.md", "CONTRIBUTING.md", "CHANGELOG.md", "tests"]')
    if other_skills:
        L += ["", T("# Other SKILL.md files found (check whether they are needed):", "# Другие найденные SKILL.md (проверьте, нужны ли):")]
        L += [f"#   {p}" for p in other_skills[:20]]
    if rules and not skills:
        L += ["", "[rules]", f"path = {_q(rules)}"]
    elif rules:
        L += ["", T(f"# Has {rules}: if these are agent rules, add [rules] path = {_q(rules)}", f"# Есть {rules}: если это правила для агента, добавьте [rules] path = {_q(rules)}")]
    for name, spec in mcp.items():
        if not isinstance(spec, dict):
            continue
        L += ["", f"[mcp.{name}]"]
        if spec.get("url"):
            L.append(f"url = {_q(spec['url'])}")
        if spec.get("command"):
            L.append(f"command = {_q(spec['command'])}")
            L.append("args = [" + ", ".join(_q(str(a)) for a in spec.get("args", [])) + "]")
        if any("CLAUDE_PLUGIN_ROOT" in str(v) for v in spec.get("args", [])):
            L.append(T("# TODO: the command points at plugin files — replace it with an npx/uvx package or drop the MCP", "# TODO: команда ссылается на файлы плагина — замените на npx/uvx-пакет или уберите MCP"))
        env = {k: ("" if re.search(r"key|token|secret", k, re.I) else str(v))
               for k, v in (spec.get("env") or {}).items()}
        if env:
            L.append("env = { " + ", ".join(f"{_q(k)} = {_q(v)}" for k, v in env.items()) + " }")
    if plugin:
        L += ["", "[manual]", "claude-code = [",
              T(f'  "Alternative — as a plugin: /plugin marketplace add {repo}",', f'  "Альтернатива — плагином: /plugin marketplace add {repo}",'), "]"]
    if not (skills or rules or mcp):
        if not plugin:
            L += ["", "[manual]"]
        L += [T('any = ["TODO: install commands from the README"]', 'any = ["TODO: команды установки из README"]')]
    path = cat.dir / f"{eid}.toml"
    path.write_bytes(("\n".join(L) + "\n").encode("utf-8"))
    info = {"repo": repo, "ref": sha, "license": lic, "skills": skills, "others": other_skills,
            "mcp": list(mcp), "rules": rules, "plugin": plugin, "report": report, "snapshot": snap}
    return path, info


def check_updates(entries: list[Entry]) -> list[tuple[Entry, str | None, str | None]]:
    """[(entry, remote_head or None, error or None)] for entries with a repo."""
    def one(e: Entry):
        try:
            return e, remote_head(e.repo), None
        except Exception as ex:  # network errors are reported, not fatal
            return e, None, str(ex)
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(one, [e for e in entries if e.repo]))


def _hashes(root: Path) -> dict[str, str]:
    out = {}
    for f in root.rglob("*"):
        rel = f.relative_to(root)
        if f.is_file() and ".git" not in rel.parts and f.name != ".skillpantry-ok":
            out[rel.as_posix()] = hashlib.sha1(f.read_bytes()).hexdigest()
    return out


def bump(entry: Entry, new_ref: str | None = None) -> dict:
    """Pin the entry to a newer commit and mark it draft until reviewed again."""
    new_ref = new_ref or remote_head(entry.repo)
    if new_ref == entry.ref:
        return {"changed": False, "ref": new_ref}
    old = fetch(entry)
    new = fetch(entry, ref=new_ref)
    a, b = _hashes(old), _hashes(new)
    diff = {
        "added": sorted(set(b) - set(a)),
        "removed": sorted(set(a) - set(b)),
        "modified": sorted(k for k in set(a) & set(b) if a[k] != b[k]),
    }
    report = audit_paths([new], new)
    set_field(entry.file, "ref", new_ref, section="source")
    set_field(entry.file, "status", "draft")
    return {"changed": True, "ref": new_ref, "old": entry.ref, "diff": diff,
            "report": report, "snapshot": new}


def approve(entry: Entry) -> None:
    set_field(entry.file, "status", "verified")
    set_field(entry.file, "reviewed", today())
