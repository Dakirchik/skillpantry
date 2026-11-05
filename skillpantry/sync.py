"""One-way, explicit skill synchronization; documents are copied byte for byte."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from skillpantry.fetch import home_dir
from skillpantry.installer import DEFAULT_EXCLUDE, InstallError, skill_name
from skillpantry.targets import Layout
from skillpantry.i18n import T

AGENTS = ("claude-code", "codex", "gemini", "cursor")


def linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def fingerprint(root: Path) -> dict[str, str]:
    """Reject nested links rather than silently copying external dependencies."""
    result = {}
    def fail(error):
        raise error
    for folder, dirs, files in os.walk(root, followlinks=False, onerror=fail):
        base = Path(folder)
        dirs[:] = sorted(d for d in dirs if d not in DEFAULT_EXCLUDE)
        for name in dirs + sorted(files):
            p = base / name
            if name in DEFAULT_EXCLUDE:
                continue
            if linked(p):
                raise InstallError(T(f"Nested link requires manual review: {p}", f"Вложенная ссылка требует ручной проверки: {p}"))
            rel = p.relative_to(root).as_posix()
            result[rel] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "directory"
    return result


@dataclass
class Transfer:
    name: str
    source: Path
    destination: Path
    status: str
    source_hash: dict
    destination_hash: dict | None
    warnings: list[str]


def plan_sync(source: Layout, destination: Layout, only: list[str] | None = None) -> list[Transfer]:
    if source.target not in AGENTS or destination.target not in AGENTS:
        raise InstallError(T("Choose an agent, not folder", "Выберите агента, а не folder"))
    src = source.skills_dir("")
    dst = destination.skills_dir("")
    roots = [src]
    # Codex skills can also live in its older, agent-specific directory.
    if source.target == "codex":
        roots.append(source.root / ".codex" / "skills")
    found = {}
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.iterdir()):
            if p.name.startswith(".") or not p.is_dir() or not (p / "SKILL.md").is_file():
                continue
            name = skill_name(p / "SKILL.md") or p.name
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
                raise InstallError(T(f"Invalid skill name: {p}", f"Недопустимое имя скилла: {p}"))
            if only and name not in only:
                continue
            if name in found and found[name].resolve() != p.resolve():
                raise InstallError(T(f"Duplicate skill: {name}", f"Дубликат скилла: {name}"))
            found[name] = p
    if only and set(only) - found.keys():
        raise InstallError(T("Skills not found: ", "Скиллы не найдены: ") + ", ".join(sorted(set(only) - found.keys())))
    if not found:
        raise InstallError(T(f"No skills found in {roots}", f"Скиллы не найдены в {roots}"))
    result = []
    for name, p in found.items():
        target = dst / name
        # Never replace links or write through an agent directory symlink.
        if any(linked(a) for a in (target, *target.parents)):
            raise InstallError(T(f"Destination contains a link: {target}", f"В пути назначения есть ссылка: {target}"))
        s, d = p.resolve(), target.resolve()
        if s == d or s in d.parents or d in s.parents:
            raise InstallError(T("Source and destination overlap", "Источник и назначение пересекаются"))
        if target.exists() and not target.is_dir():
            raise InstallError(T(f"Destination is not a directory: {target}", f"Назначение не является папкой: {target}"))
        sh = fingerprint(p)
        dh = fingerprint(target) if target.exists() else None
        warnings = []
        text = "\n".join((p / rel).read_text(encoding="utf-8", errors="replace")
                         for rel in sh if rel.endswith((".md", ".json", ".yaml", ".yml", ".toml")))
        if re.search(r"CLAUDE_PLUGIN_ROOT|\.claude[/\\]|\.codex[/\\]|\.cursor[/\\]|\.gemini[/\\]|allowed-tools:|context:\s*fork|/plugin\b", text):
            warnings.append(T("Agent-specific paths or features: review compatibility.", "Есть пути или возможности конкретного агента: проверьте совместимость."))
        result.append(Transfer(name, p, target, "new" if dh is None else "unchanged" if sh == dh else "conflict", sh, dh, warnings))
    return result


def apply_sync(plan: list[Transfer], force: bool = False) -> Path | None:
    changes = [x for x in plan if x.status != "unchanged"]
    if any(x.status == "conflict" for x in changes) and not force:
        raise InstallError(T("Conflicts: use --force to replace with backups", "Конфликты: для замены с резервной копией используйте --force"))
    if not changes:
        return None
    backup_root = home_dir() / "backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    transaction = Path(tempfile.mkdtemp(prefix="sync-", dir=backup_root))
    done = []
    try:
        # Stage everything before changing any destination.
        for i, x in enumerate(changes):
            if fingerprint(x.source) != x.source_hash:
                raise InstallError(T("Source changed; build a new plan", "Источник изменился; постройте план заново"))
            if any(linked(a) for a in (x.destination, *x.destination.parents)):
                raise InstallError("Destination contains a link")
            current = fingerprint(x.destination) if x.destination.is_dir() else None
            if current != x.destination_hash or (x.destination.exists() and not x.destination.is_dir()):
                raise InstallError(T("Destination changed; build a new plan", "Назначение изменилось; постройте план заново"))
            stage = transaction / f"stage-{i}"
            shutil.copytree(x.source, stage, ignore=shutil.ignore_patterns(*DEFAULT_EXCLUDE))
            if fingerprint(stage) != x.source_hash:
                raise InstallError("Source changed during copy")
        for i, x in enumerate(changes):
            backup = transaction / f"original-{i}-{x.name}"
            if x.destination.exists():
                shutil.copytree(x.destination, backup)
            done.append((x, backup))
            if x.destination.exists():
                shutil.rmtree(x.destination)
            x.destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(transaction / f"stage-{i}", x.destination)
    except Exception:
        for x, backup in reversed(done):
            if x.destination.exists():
                shutil.rmtree(x.destination)
            if backup.exists():
                shutil.copytree(backup, x.destination)
        raise
    finally:
        for stage in transaction.glob("stage-*"):
            shutil.rmtree(stage)
    return transaction
