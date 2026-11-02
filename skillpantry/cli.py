"""Command-line interface. Run `skillpantry` with no arguments for the interactive menu."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from skillpantry import __version__
from skillpantry import ui
from skillpantry.i18n import T
from skillpantry.audit import audit_paths, format_report
from skillpantry.catalog import (RISK_LABELS, STATUS_LABELS, TARGET_LABELS, TARGETS, Catalog,
                                CatalogError, Entry)
from skillpantry.fetch import FetchError, clear_cache, fetch, home_dir
from skillpantry.installer import (InstallError, execute, install_key, installed, list_variants,
                                  load_state,
                                  plan_install, uninstall)
from skillpantry.targets import Layout

TARGET_ALIASES = {"claude": "claude-code", "cc": "claude-code", "gemini-cli": "gemini",
                  "dir": "folder", "self": "folder"}


def default_library() -> Path:
    from skillpantry.config import setting_path
    return setting_path("SKILLPANTRY_LIBRARY", "library", Path.home() / "skillpantry-library")


def make_layout(target: str, global_: bool = False, path: str | None = None,
                dest: str | None = None) -> Layout:
    target = TARGET_ALIASES.get(target, target)
    if target not in TARGETS:
        raise CatalogError(T(f"Unknown target '{target}'. Options: {', '.join(TARGETS)}", f"Неизвестная цель '{target}'. Варианты: {', '.join(TARGETS)}"))
    if target == "folder":
        return Layout("folder", "folder", Path(dest or path or default_library()).expanduser().resolve())
    if global_:
        return Layout(target, "global", Path.home())
    return Layout(target, "project", Path(path or os.getcwd()).expanduser().resolve())


# ---- printing -------------------------------------------------------------------

def entry_line(e: Entry, target: str | None = None) -> str:
    mark = ui.STATUS_MARK[e.status]
    risk = ui.RISK_COLOR[e.risk](f"{RISK_LABELS[e.risk]:<7}")
    sup = ""
    if target:
        sup = ui.SUPPORT_MARK[e.support(target)] + " "
    else:
        sup = "".join(ui.SUPPORT_MARK[e.support(t)] for t in TARGETS) + " "
    w = ui.width()
    name = ui.clip(e.id, 22)
    text = ui.clip(e.summary, max(20, w - 52))
    return f"{e.number:>3} {mark} {sup}{name:<22} {risk} {ui.dim(text)}"


def print_legend(target: str | None) -> None:
    cols = (TARGET_LABELS[target] if target
            else " ".join(f"{i + 1}={TARGET_LABELS[t].split(' ')[0]}" for i, t in enumerate(TARGETS)))
    print(ui.dim(T(f"  ✓ verified  ✎ draft  ⓘ reference  ✗ excluded  ·  " f"● auto  ◐ manual  · none   ·  columns: {cols}", f"  ✓ проверено  ✎ черновик  ⓘ справка  ✗ исключено  ·  "
                 f"● авто  ◐ вручную  · нет   ·  колонки: {cols}")))


def show_entry(e: Entry) -> None:
    print(ui.bold(f"{e.number}. {e.name}") + ui.dim(f"  ({e.id})"))
    print(e.summary)
    print(ui.dim(T(f"Status: {STATUS_LABELS[e.status]} · risk: ", f"Статус: {STATUS_LABELS[e.status]} · риск: ")) + ui.RISK_COLOR[e.risk](RISK_LABELS[e.risk])
          + ui.dim(T(f" · work-safe: {'yes' if e.work_safe else 'no'} · category: {e.category}", f" · для рабочих проектов: {'да' if e.work_safe else 'нет'} · категория: {e.category}")))
    if e.repo or e.local:
        print(ui.dim(T(f"Source: {e.source_label}", f"Источник: {e.source_label}") + (T(f" · license {e.license}", f" · лицензия {e.license}") if e.license not in (None, "own") else "")
                     + (T(f" · reviewed {e.reviewed}", f" · проверено {e.reviewed}") if e.reviewed else "")
                     + (T(" · your own entry", " · своя запись") if e.origin == "user" else "")))
    elif e.url:
        print(ui.dim(T(f"Link: {e.url}", f"Ссылка: {e.url}")))
    if e.description:
        print()
        print(e.description)
    if e.skills:
        print()
        print(ui.bold(T("Skills:", "Скиллы:")))
        for s in e.skills:
            p = s.path or ", ".join(f"{k}: {v}" for k, v in s.paths.items())
            print(f"  - {p}" + (ui.dim(T("  (optional: --optional)", "  (по желанию: --optional)")) if s.optional else ""))
    if e.rules:
        print(ui.bold(T("Rules: ", "Правила: ")) + e.rules["path"])
    if e.mcp:
        print(ui.bold("MCP: ") + ", ".join(m.name + (f" ({m.url})" if m.url else f" ({m.command} {' '.join(m.args)})")
                                         for m in e.mcp))
    if e.commands:
        print(ui.bold(T("Commands: ", "Команды: ")) + ", ".join(e.commands))
    if e.variants:
        print(ui.bold(T("Variant: ", "Вариант: ")) + f"{e.variants['glob']} → {e.variants['dest']}")
    print()
    print(ui.bold(T("Targets: ", "Цели: ")) + "  ".join(f"{TARGET_LABELS[t]} {ui.SUPPORT_MARK[e.support(t)]}" for t in TARGETS))
    if e.risk_notes:
        print()
        print(ui.bold(T("Risks:", "Риски:")))
        for n in e.risk_notes:
            print("  " + ui.yellow("! ") + n)
    if any(e.manual.values()):
        print()
        print(ui.bold(T("Manual steps:", "Ручные шаги:")))
        for t, steps in e.manual.items():
            who = T("all targets", "все цели") if t == "any" else TARGET_LABELS.get(t, t)
            for s in steps:
                print(f"  [{who}] {s}")
    if e.overlaps:
        print()
        print(ui.dim(T("Overlaps with: ", "Пересекается с: ") + ", ".join(e.overlaps) + T(" — usually you pick one", " — обычно ставят что-то одно")))


# ---- install ----------------------------------------------------------------------

def check_entry_allowed(e: Entry, target: str, args) -> str | None:
    """Return a reason the entry cannot be installed, or None."""
    if e.status == "excluded":
        return T("the entry is excluded from installation:\n  ", "запись исключена из установки:\n  ") + e.summary
    if e.status == "reference":
        return T("reference entry, nothing to install", "справочная запись, ставить нечего") + (f" ({e.url})" if e.url else "")
    if e.status == "draft" and not getattr(args, "allow_draft", False):
        return T("draft — review it and run skillpantry approve first (or use --allow-draft)", "черновик — сначала ревью и skillpantry approve (или флаг --allow-draft)")
    if e.support(target) == "none":
        if e.workspace:
            return T("installs only as a workspace folder: -t folder", "ставится только как рабочая папка: -t folder")
        return T(f"no way to install for {TARGET_LABELS[target]}", f"для цели {TARGET_LABELS[target]} нет способа установки")
    return None


def risk_gate(e: Entry, args) -> bool:
    if not (e.confirm or e.risk == "high"):
        return True
    print(ui.red(ui.bold(T(f"\n{e.name}: elevated risk", f"\n{e.name}: повышенный риск"))))
    for n in e.risk_notes:
        print("  " + ui.yellow("! ") + n)
    if getattr(args, "accept_risk", False) or e.id in (getattr(args, "accepted", None) or ()):
        return True
    if not (sys.stdin.isatty() or getattr(args, "interactive", False)):
        print(ui.red(T("  The --accept-risk flag is required (non-interactive mode)", "  Нужен флаг --accept-risk (неинтерактивный режим)")))
        return False
    return ui.ask(T("  To continue, type the entry id", "  Чтобы продолжить, введите id записи")) == e.id


def pick_variant(e: Entry, snap: Path, given: str | None, interactive: bool = False) -> str | None:
    if not e.variants or given:
        return given
    options = list_variants(e, snap)
    if not options or not (sys.stdin.isatty() or interactive):
        return None
    print(ui.bold(T(f"\n{e.name}: choose a variant ({len(options)})", f"\n{e.name}: выберите вариант ({len(options)})")))
    names = [n for n, _ in options]
    for i in range(0, len(names), 6):
        print("  " + "  ".join(f"{j + 1:>2}.{names[j]:<14}" for j in range(i, min(i + 6, len(names)))))
    ans = ui.ask(T("Number or name (empty — skip)", "Номер или имя (пусто — пропустить)"))
    if ans.isdigit() and 1 <= int(ans) <= len(names):
        return names[int(ans) - 1]
    return ans if ans in names else None


def print_plan(plan, audit_rep) -> None:
    e = plan.entry
    print(ui.bold(f"\n▸ {e.name}") + ui.dim(f"  → {plan.layout.label}"))
    if e.repo or e.local:
        print(ui.dim(f"  {e.source_label}" + (T(f" · license {e.license}", f" · лицензия {e.license}") if e.license not in (None, "own") else "")))
    def rel(p: Path) -> str:
        try:
            return p.relative_to(plan.layout.root).as_posix()
        except ValueError:
            return str(p)
    groups: dict[str, list] = {}
    for a in plan.actions:
        kind = a.describe.split(" ", 1)[0] if a.describe.startswith(("skill ", "command ", "скилл ", "команда ")) else a.describe
        groups.setdefault(kind, []).append(a)
    for kind, acts in groups.items():
        if len(acts) > 4:
            names = [a.describe.split(" ", 1)[1] for a in acts]
            where = rel(acts[0].path.parent)
            print("  " + ui.green("+ ") + f"{len(acts)} × {kind}: " + ", ".join(names[:5])
                  + (f" … (+{len(names) - 5})" if len(names) > 5 else "") + ui.dim(f"  → {where}/"))
        else:
            for a in acts:
                print("  " + ui.green("+ ") + a.describe + ui.dim(f"  → {rel(a.path)}"))
    for n in plan.notes:
        if n.startswith("⟳"):
            print("  " + ui.cyan(n))
    for s in plan.skipped:
        print("  " + ui.dim("· " + s))
    for c in plan.conflicts:
        print("  " + ui.red("✗ " + c))
    if audit_rep is not None and audit_rep.files:
        c = audit_rep.counts()
        tone = {"high": ui.red, "medium": ui.yellow}.get(audit_rep.worst, ui.dim)
        print("  " + tone(T(f"audit: files {audit_rep.files}, scripts {len(audit_rep.scripts)}, " f"findings {c['high']}/{c['medium']}/{c['low']} (high/medium/low)", f"аудит: файлов {audit_rep.files}, скриптов {len(audit_rep.scripts)}, "
                          f"находок {c['high']}/{c['medium']}/{c['low']} (высокие/средние/низкие)"))
              + ui.dim(T(f" — details: skillpantry audit {e.id}", f" — подробно: skillpantry audit {e.id}")))
    if not e.work_safe and plan.layout.scope == "project" and plan.actions:
        print("  " + ui.yellow(T("! not marked as safe for work projects — see the risks", "! не отмечено как безопасное для рабочих проектов — см. риски")))
    if plan.env:
        print("  " + ui.cyan(T("environment variables: ", "переменные окружения: ") + ", ".join(sorted(set(plan.env)))))


def print_after(plan) -> None:
    if plan.manual:
        print(ui.bold(T("  Manual steps:", "  Ручные шаги:")))
        for m in plan.manual:
            print("    " + ui.cyan(m))
    for n in plan.notes:
        if not n.startswith("⟳"):
            print("  " + ui.dim(n))


def install_entries(cat: Catalog, ids: list[str], layout: Layout, args) -> int:
    plans = []
    batch: dict[str, str] = {}
    for key in ids:
        e = cat.get(key)
        reason = check_entry_allowed(e, layout.target, args)
        if reason:
            print(ui.yellow(f"– {e.id}: {reason}"))
            continue
        if not risk_gate(e, args):
            print(ui.yellow(T(f"– {e.id}: skipped", f"– {e.id}: пропущено")))
            continue
        snap = None
        rep = None
        if e.support(layout.target) == "auto":
            print(ui.dim(T(f"  {'' if e.local else 'fetching '}{e.source_label}…", f"  {'' if e.local else 'загрузка '}{e.source_label}…")))
            snap = fetch(e, refresh=getattr(args, "refresh", False))
        opts = (getattr(args, "entry_options", None) or {}).get(e.id, {})
        given = opts.get("variant") or getattr(args, "variant", None)
        variant = (pick_variant(e, snap, given, getattr(args, "interactive", False)) if snap else None)
        only = set(opts.get("skills") or getattr(args, "skill", None) or []) or None
        skip = set(opts.get("skip_skills") or getattr(args, "skip_skill", None) or []) or None
        previous = load_state()["installs"].get(install_key(layout, e.id))
        plan = plan_install(e, layout, snap, only_skills=only,
                            with_optional=bool(opts.get("optional") or getattr(args, "optional", False)),
                            variant=variant, force=getattr(args, "force", False), batch=batch,
                            skip_skills=skip, include_optional=set(opts.get("include_optional") or []) or None)
        if previous:
            old = (previous.get("ref") or "")[:12]
            new = (e.ref or "")[:12]
            plan.notes.insert(0, T(f"⟳ installed since {previous.get('installed_at', '?')[:16].replace('T', ' ')} — ", f"⟳ уже стоит с {previous.get('installed_at', '?')[:16].replace('T', ' ')} — ")
                                 + (T(f"will update {old} → {new}", f"обновится {old} → {new}") if old != new else T("will reinstall (same version)", "переустановится (версия та же)")))
        for a in plan.actions:
            if a.kind in ("dir", "file"):
                batch.setdefault(str(a.path), e.id)
        srcs = [a.src for a in plan.actions if a.src]
        if srcs:
            rep = audit_paths(srcs, snap)
        print_plan(plan, rep)
        plans.append(plan)
    if not plans:
        return 1
    actionable = [p for p in plans if p.actions]
    if getattr(args, "dry_run", False):
        print(ui.dim(T("\n--dry-run: nothing changed", "\n--dry-run: ничего не изменено")))
        for p in plans:
            print_after(p)
        return 0
    blocked = [p for p in plans if p.conflicts and not args.force]
    if blocked:
        print(ui.red(T("\nThere are conflicts — nothing changed. Resolve them or repeat with --force.", "\nЕсть конфликты — ничего не изменено. Уберите их или повторите с --force.")))
        return 2
    if actionable and not getattr(args, "yes", False):
        if not (sys.stdin.isatty() or getattr(args, "interactive", False)):
            print(ui.red(T("\nConfirmation needed: add -y", "\nНужно подтверждение: добавьте -y")))
            return 2
        if not ui.confirm(T(f"\nApply ({sum(len(p.actions) for p in actionable)} changes)?", f"\nПрименить ({sum(len(p.actions) for p in actionable)} изменений)?"), True):
            print(T("Cancelled", "Отменено"))
            return 1
    for p in plans:
        if p.actions:
            execute(p, force=args.force)
            print(ui.green(T(f"✓ {p.entry.id}: installed", f"✓ {p.entry.id}: установлено")))
        print_after(p)
    return 0


# ---- commands -------------------------------------------------------------------

def cmd_list(cat: Catalog, args) -> int:
    target = TARGET_ALIASES.get(args.target, args.target) if args.target else None
    entries = cat.sorted()
    if args.status:
        entries = [e for e in entries if e.status == args.status]
    if args.category:
        entries = [e for e in entries if e.category == args.category]
    if args.work:
        entries = [e for e in entries if e.work_safe]
    if target and not args.all:
        entries = [e for e in entries if e.support(target) != "none"]
    print_legend(target)
    for e in entries:
        print(entry_line(e, target))
    print(ui.dim(T(f"  {len(entries)} of {len(cat.entries)} · details: skillpantry show <id or number>", f"  {len(entries)} из {len(cat.entries)} · подробно: skillpantry show <id или номер>")))
    return 0


def cmd_show(cat, args) -> int:
    for k in args.ids:
        show_entry(cat.get(k))
        print()
    return 0


def cmd_search(cat, args) -> int:
    res = cat.search(" ".join(args.text))
    for e in res:
        print(entry_line(e))
    if not res:
        print(T("Nothing found", "Ничего не найдено"))
    return 0 if res else 1


def cmd_install(cat, args) -> int:
    layout = make_layout(args.target, args.global_, args.path, args.dest)
    return install_entries(cat, args.ids, layout, args)


def cmd_uninstall(cat, args) -> int:
    layout = make_layout(args.target, args.global_, args.path, args.dest)
    code = 0
    for k in args.ids:
        eid = cat.get(k).id if (k in cat.entries or k.isdigit()) else k
        try:
            rec = uninstall(layout, eid)
            print(ui.green(T(f"✓ {eid}: removed ({len(rec['actions'])} changes undone)", f"✓ {eid}: удалено ({len(rec['actions'])} изменений отменено)")))
        except InstallError as ex:
            print(ui.red(str(ex)))
            code = 1
    return code


def cmd_status(cat, args) -> int:
    root = None if args.all else Path(args.path or os.getcwd()).resolve()
    recs = installed(root)
    if not recs:
        print(T("Nothing here was installed with skillpantry", "Здесь ничего не установлено через skillpantry") + ("" if args.all else T(" (all places: --all)", " (все места: --all)")))
        return 0
    for r in recs:
        e = cat.entries.get(r["id"])
        stale = e is not None and e.ref and r.get("ref") != e.ref
        flag = ui.yellow("  ↑ " + (T("the folder changed", "папка изменилась") if e.local else T("newer in the catalog", "в каталоге новее")) + T(" — reinstall", " — переустановите")) if stale else ""
        print(f"{r['id']:<22} {r['target']:<12} {r['scope']:<8} {ui.dim(r['root'])}{flag}")
        if args.verbose:
            for a in r["actions"]:
                print(ui.dim(f"    {a['type']:<10} {a['path']}"))
    return 0


def cmd_fetch(cat, args) -> int:
    ids = [e.id for e in cat.sorted() if e.installable and e.has_payload()] if args.all else args.ids
    code = 0
    for k in ids:
        e = cat.get(k)
        try:
            p = fetch(e, refresh=args.refresh)
            print(ui.green(f"✓ {e.id}") + ui.dim(f"  {p}"))
        except FetchError as ex:
            print(ui.red(f"✗ {e.id}: {ex}"))
            code = 1
    return code


def cmd_audit(cat, args) -> int:
    e = cat.get(args.id)
    snap = fetch(e)
    targets = []
    for s in e.skills:
        for p in {s.path, *s.paths.values()} - {None}:
            targets += list(snap.glob(p)) if any(c in p for c in "*?[") else [snap / p]
    if e.rules:
        targets.append(snap / e.rules["path"])
    targets = [t for t in targets if t.exists()] or [snap]
    rep = audit_paths(targets, snap)
    print(ui.bold(T(f"Audit {e.id} @ {(e.ref or '?')[:12]}", f"Аудит {e.id} @ {(e.ref or '?')[:12]}")) + ui.dim(f"  ({snap})"))
    print(format_report(rep, limit=10_000 if args.all else 12))
    if rep.scripts and args.all:
        print(ui.bold(T("Scripts:", "Скрипты:")))
        for s in rep.scripts:
            print("  " + s)
    print(ui.dim(T("Findings are a reason to read the line, not a verdict. Review rules: CONTRIBUTING.md", "Находки — повод прочитать строку, а не вердикт. Правила ревью: CONTRIBUTING.md")))
    return 0


def cmd_outdated(cat, args) -> int:
    from skillpantry.maintain import check_updates
    entries = [cat.get(k) for k in args.ids] if args.ids else [e for e in cat.sorted() if e.repo]
    print(ui.dim(T(f"Checking {len(entries)} repositories…", f"Проверяю {len(entries)} репозиториев…")))
    newer = 0
    for e, head, err in check_updates(entries):
        if err:
            print(ui.red(f"✗ {e.id}: {err}"))
        elif head != e.ref:
            newer += 1
            print(ui.yellow(f"↑ {e.id:<22}") + ui.dim(f" {e.ref[:12]} → {head[:12]}   skillpantry bump {e.id}"))
    state = load_state()
    for r in state["installs"].values():
        e = cat.entries.get(r["id"])
        if e and e.ref and r.get("ref") != e.ref:
            print(ui.cyan(T(f"⟳ {r['id']}: installed {r['ref'][:12]}, catalog has {e.ref[:12]} " f"({r['target']}, {r['root']})", f"⟳ {r['id']}: установлен {r['ref'][:12]}, в каталоге {e.ref[:12]} "
                          f"({r['target']}, {r['root']})")))
    print(T(f"Updates in sources: {newer}", f"Обновлений в источниках: {newer}"))
    return 0


def cmd_bump(cat, args) -> int:
    from skillpantry.maintain import bump
    e = cat.get(args.id)
    if e.local:
        print(T(f"{e.id}: your own folder — the version updates itself when files change ({(e.ref or '?')[:8]})", f"{e.id}: своя папка — версия обновляется сама, когда меняются файлы ({(e.ref or '?')[:8]})"))
        return 0
    res = bump(e, args.ref)
    if not res["changed"]:
        print(T(f"{e.id}: already on the latest commit {res['ref'][:12]}", f"{e.id}: уже на последнем коммите {res['ref'][:12]}"))
        return 0
    d = res["diff"]
    print(ui.bold(f"{e.id}: {res['old'][:12]} → {res['ref'][:12]}") + ui.dim(T("  (in tracked paths)", "  (в отслеживаемых путях)")))
    for kind, label in (("added", "+"), ("removed", "-"), ("modified", "~")):
        for f in d[kind][:30]:
            print(f"  {label} {f}")
        if len(d[kind]) > 30:
            print(ui.dim(T(f"  … {len(d[kind]) - 30} more", f"  … ещё {len(d[kind]) - 30}")))
    if args.diff:
        import difflib
        old_snap = fetch(e, ref=res["old"])
        for f in d["modified"]:
            if not f.endswith((".md", ".mdc", ".json", ".toml", ".txt", ".sh", ".py", ".js", ".mjs", ".ps1")):
                continue
            a = (old_snap / f).read_text(encoding="utf-8", errors="replace").splitlines()
            b = (res["snapshot"] / f).read_text(encoding="utf-8", errors="replace").splitlines()
            lines = list(difflib.unified_diff(a, b, f"a/{f}", f"b/{f}", lineterm="", n=2))
            for ln in lines[:200]:
                color = ui.green if ln.startswith("+") else ui.red if ln.startswith("-") else ui.dim
                print(color(ln if len(ln) <= 240 else ln[:239] + "…"))
            if len(lines) > 200:
                print(ui.dim(T(f"… {len(lines) - 200} more lines", f"… ещё {len(lines) - 200} строк")))
    print(format_report(res["report"]))
    print(ui.yellow(T(f"\nStatus → draft. After reviewing the changes: skillpantry approve {e.id}", f"\nСтатус → draft. После ревью изменений: skillpantry approve {e.id}")))
    return 0


def cmd_approve(cat, args) -> int:
    from skillpantry.maintain import approve
    e = cat.get(args.id)
    problems = [p for p in cat.errors.get(e.id, [])]
    if problems:
        print(ui.red(T("The manifest does not pass validation:", "Манифест не проходит проверку:")))
        for p in problems:
            print("  - " + p)
        return 1
    snap = fetch(e)
    rep = audit_paths([snap], snap)
    print(format_report(rep))
    print(ui.bold(T("\nReview checklist (CONTRIBUTING.md):", "\nЧек-лист ревью (CONTRIBUTING.md):")))
    for item in (T("every SKILL.md and rules file read in full", "прочитан каждый SKILL.md и файл правил целиком"),
                 T("all scripts and hooks that reach the user looked through", "просмотрены все скрипты и хуки, которые попадут к пользователю"),
                 T("clear where data goes (external hosts above)", "понятно, куда уходят данные (внешние адреса выше)"),
                 T("the license allows use; risk and risk_notes are filled in honestly", "лицензия позволяет использование; risk и risk_notes заполнены честно"),
                 T("manual commands contain no curl | sh and pin versions where possible", "manual-команды не содержат curl | sh и ставят закреплённые версии, где возможно")):
        print("  □ " + item)
    if not args.yes and not ui.confirm(T("Everything checked — mark as verified?", "Всё проверено — отметить как verified?")):
        return 1
    approve(e)
    print(ui.green(T(f"✓ {e.id}: verified, reviewed = today", f"✓ {e.id}: verified, reviewed = сегодня")))
    return 0


def cmd_add_mine(cat, args) -> int:
    from skillpantry import mine
    print(ui.dim(T("Looking at the source…", "Разбираю источник…")))
    src = mine.inspect_source(args.url)
    name = args.name or src.name
    f = mine.Form(name=name, id=args.id or mine.unique_id(cat, mine.slug(name)),
                  category=args.category or "own", summary=args.summary or src.summary or name,
                  description=src.description, tags=[t for t in (args.tags or "").split(",") if t.strip()],
                  risk=args.risk, work_safe=src.kind == "local", reviewed=bool(args.reviewed))
    if f.category not in cat.categories:
        f.new_category = {"id": f.category, "title": args.category_title or f.category,
                          "description": ""}
    errs = mine.check_form(cat, src, f)
    if errs:
        for k, v in errs.items():
            print(ui.red(f"✗ {k}: {v}"))
        return 1
    path = mine.save(cat, src, f)
    e = cat.get(f.id)
    print(ui.green(T(f"✓ Your entry {e.number}. {e.name} ({e.id})", f"✓ Своя запись {e.number}. {e.name} ({e.id})")) + ui.dim(f"  {path}"))
    print(f"  {e.source_label}")
    for c in src.chosen():
        print(f"  + {c.label}")
    rep = src.audit()
    print(format_report(rep))
    if e.status == "draft":
        print(ui.yellow(T(f"  draft: after reviewing — skillpantry approve {e.id}", f"  черновик: после просмотра — skillpantry approve {e.id}")))
    print(ui.dim(T(f"  Install: skillpantry install {e.id} -t claude-code   ·   edit: skillpantry (Catalog → {e.name})", f"  Поставить: skillpantry install {e.id} -t claude-code   ·   изменить: skillpantry (Каталог → {e.name})")))
    return 0


def cmd_drop(cat, args) -> int:
    from skillpantry import mine
    e = cat.get(args.id)
    if e.origin != "user":
        print(ui.red(T(f"{e.id} is from the shared catalog; only your own entries can be removed from the catalog", f"{e.id} — из общего каталога; удалить из каталога можно только свои записи")))
        return 1
    recs = [r for r in installed(None) if r["id"] == e.id]
    if recs:
        print(ui.yellow(T(f"! {e.id} is installed in {len(recs)} place(s) — the installs stay, " f"remove them with: skillpantry uninstall {e.id} -t <target> …", f"! {e.id} установлена в {len(recs)} мест(а) — установки останутся, "
                        f"удалить их: skillpantry uninstall {e.id} -t <цель> …")))
    if not args.yes and not ui.confirm(T(f"Remove your entry {e.name} ({e.file})?", f"Удалить свою запись {e.name} ({e.file})?"), False):
        return 1
    mine.delete(cat, e)
    print(ui.green(T(f"✓ {e.id}: removed from the catalog (the source folder is untouched)", f"✓ {e.id}: удалена из каталога (сама папка-источник не тронута)")))
    return 0


def cmd_add(cat, args) -> int:
    from skillpantry.mine import resolve_location
    if args.mine or resolve_location(args.url)[0] == "local":
        return cmd_add_mine(cat, args)
    from skillpantry.maintain import draft_manifest
    print(ui.dim(T("Downloading and inspecting the repository…", "Скачиваю и разбираю репозиторий…")))
    path, info = draft_manifest(cat, args.url, args.id)
    print(ui.green(T(f"✓ Draft: {path}", f"✓ Черновик: {path}")))
    print(T(f"  {info['repo']} @ {info['ref'][:12]} · license: {info['license']}", f"  {info['repo']} @ {info['ref'][:12]} · лицензия: {info['license']}"))
    print(T(f"  skills: {', '.join(info['skills']) or 'none found'}", f"  скиллы: {', '.join(info['skills']) or 'не найдены'}")
          + (ui.dim(T(f" (+{len(info['others'])} elsewhere)", f" (+{len(info['others'])} в других местах)")) if info['others'] else ""))
    print(T(f"  MCP: {', '.join(info['mcp']) or 'none'} · rules: {info['rules'] or 'none'} · " f"Claude plugin: {'yes' if info['plugin'] else 'no'}", f"  MCP: {', '.join(info['mcp']) or 'нет'} · правила: {info['rules'] or 'нет'} · "
          f"плагин Claude: {'да' if info['plugin'] else 'нет'}"))
    print(format_report(info["report"]))
    print(ui.bold(T("\nNext:", "\nДальше:")))
    print(T(f"  1. Fill in the TODOs in {path.name} (summary, category, risk, description, manual steps)", f"  1. Заполните TODO в {path.name} (summary, category, risk, описание, ручные шаги)"))
    print(T(f"  2. skillpantry validate      — schema check", f"  2. skillpantry validate      — проверка схемы"))
    print(T(f"  3. skillpantry audit {path.stem} --all   — read the findings and scripts", f"  3. skillpantry audit {path.stem} --all   — прочитать находки и скрипты"))
    print(T(f"  4. skillpantry install {path.stem} -t folder --allow-draft --dry-run   — see the result", f"  4. skillpantry install {path.stem} -t folder --allow-draft --dry-run   — посмотреть результат"))
    print(T(f"  5. skillpantry approve {path.stem}   — after the review", f"  5. skillpantry approve {path.stem}   — после ревью"))
    return 0


def cmd_validate(cat, args) -> int:
    if not cat.errors:
        print(ui.green(T(f"✓ {len(cat.entries)} manifests, no errors", f"✓ {len(cat.entries)} манифестов, ошибок нет")))
        return 0
    for k, errs in cat.errors.items():
        print(ui.red(f"✗ {k}"))
        for e in errs:
            print("    - " + e)
    return 1


def cmd_export(cat, args) -> int:
    from skillpantry.export import render
    text = render(cat)
    if args.output:
        out = Path(args.output).expanduser()
        if out.is_dir():
            out = out / "AI-skills-catalog.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(text.encode("utf-8"))
        print(ui.green(f"✓ {out}"))
    else:
        sys.stdout.write(text)
    return 0


def cmd_cache(cat, args) -> int:
    n = clear_cache(args.id)
    print(T(f"Snapshots removed: {n}", f"Удалено снимков: {n}"))
    return 0


def cmd_doctor(cat, args) -> int:
    ok = True
    print(f"skillpantry {__version__} · Python {sys.version.split()[0]} · {sys.platform}")
    git = shutil.which("git")
    if git:
        v = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout.strip()
        print(ui.green("✓ ") + v)
    else:
        ok = False
        print(ui.red(T("✗ git not found — needed to fetch sources", "✗ git не найден — нужен для загрузки источников")))
    for tool in ("node", "npx", "uv", "uvx", "pipx", "claude", "codex", "gemini", "cursor"):
        print((ui.green("✓ ") if shutil.which(tool) else ui.dim("· ")) + tool)
    own = cat.user_entries()
    print(T(f"Catalog: {cat.dir} ({len(cat.entries) - len(own)} entries, errors: {len(cat.errors)})", f"Каталог: {cat.dir} ({len(cat.entries) - len(own)} записей, ошибок: {len(cat.errors)})"))
    if cat.user_dir:
        print(T(f"Mine:    {cat.user_dir} ({len(own)} entries", f"Свои:    {cat.user_dir} ({len(own)} записей") + ("" if cat.user_dir.is_dir() else T(", no folder yet", ", папки пока нет"))
              + T(") — add: skillpantry add <folder|link>", ") — добавить: skillpantry add <папка|ссылка>"))
    from skillpantry.config import config_file
    print(T(f"Data:    {home_dir()}  (cache, install journal, backups)", f"Данные:  {home_dir()}  (кэш, журнал установок, резервные копии)"))
    print(T(f"Config:  {config_file()}", f"Конфиг:  {config_file()}") + ("" if config_file().is_file() else T("  (none — defaults are used)", "  (нет — используются значения по умолчанию)")))
    print(T(f"Default folder for -t folder: {default_library()}", f"Папка по умолчанию для -t folder: {default_library()}"))
    return 0 if ok else 1


# ---- parser -----------------------------------------------------------------------

def cmd_lang(cat, args) -> int:
    from skillpantry import i18n
    from skillpantry.config import set_setting
    if args.value:
        f = set_setting("language", "" if args.value == "auto" else args.value)
        i18n.set_lang()
        print(ui.green(T(f"✓ Language: {i18n.NAMES[i18n.lang()]}", f"✓ Язык: {i18n.NAMES[i18n.lang()]}"))
              + ui.dim(f"  ({f})"))
        return 0
    src = (T("--lang flag", "флаг --lang") if args.lang else "SKILLPANTRY_LANG" if os.environ.get("SKILLPANTRY_LANG")
           else "config.toml" if i18n.configured() else T("system language", "язык системы"))
    print(f"{i18n.NAMES[i18n.lang()]} ({i18n.lang()}) — {src}")
    print(ui.dim(T("Change: skillpantry lang en | ru | auto", "Поменять: skillpantry lang en | ru | auto")))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="skillpantry",
        description=T("A pantry of reviewed skills, rules and MCP for Claude Code, Codex, Gemini CLI, Cursor " "or a plain folder. Without arguments — the interactive menu.", "Кладовая проверенных скиллов, правил и MCP для Claude Code, Codex, Gemini CLI, Cursor "
                    "или обычной папки. Без аргументов — интерактивное меню."))
    p.add_argument("--version", action="version", version=f"skillpantry {__version__}")
    p.add_argument("--catalog", help=T("folder with manifests (default: built-in or $SKILLPANTRY_CATALOG)", "папка с манифестами (по умолчанию встроенная или $SKILLPANTRY_CATALOG)"))
    p.add_argument("--lang", choices=("en", "ru"),
                   help=T("interface language for this run (save it: skillpantry lang en|ru)",
                          "язык интерфейса на этот запуск (сохранить: skillpantry lang en|ru)"))
    p.add_argument("--plain", action="store_true",
                   help=T("simple text menu instead of the full-screen one (or SKILLPANTRY_PLAIN=1)", "простое текстовое меню вместо полноэкранного (или SKILLPANTRY_PLAIN=1)"))
    sub = p.add_subparsers(dest="cmd", metavar=T("command", "команда"))

    def where(sp, folder=True):
        sp.add_argument("-t", "--target", required=True,
                        help="claude-code | codex | gemini | cursor | folder")
        g = sp.add_mutually_exclusive_group()
        g.add_argument("-g", "--global", dest="global_", action="store_true",
                       help=T("into the user's home folder instead of a project", "в домашнюю папку пользователя, а не в проект"))
        g.add_argument("-p", "--path", help=T("project root (default: current folder)", "корень проекта (по умолчанию текущая папка)"))
        g.add_argument("-d", "--dest", help=T("for -t folder: where to put things (default ~/skillpantry-library)", "для -t folder: куда складывать (по умолчанию ~/skillpantry-library)"))

    s = sub.add_parser("list", help=T("list entries", "список записей"))
    s.add_argument("-t", "--target", help=T("show support for one target", "показать поддержку для одной цели"))
    s.add_argument("--status", choices=["verified", "draft", "reference", "excluded"])
    s.add_argument("--category")
    s.add_argument("--work", action="store_true", help=T("only entries safe for work projects", "только безопасные для рабочих проектов"))
    s.add_argument("--all", action="store_true", help=T("with -t: show unsupported ones too", "с -t: показать и неподдерживаемые"))
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help=T("entry details", "подробно о записи"))
    s.add_argument("ids", nargs="+")
    s.set_defaults(func=cmd_show)

    s = sub.add_parser("search", help=T("search names, descriptions, tags", "поиск по названию, описанию, тегам"))
    s.add_argument("text", nargs="+")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("install", help=T("install entries into the chosen AI or a folder", "поставить записи в выбранный ИИ или папку"))
    s.add_argument("ids", nargs="+", help=T("entry ids or numbers", "id или номера записей"))
    where(s)
    s.add_argument("--skill", action="append", help=T("only this skill (repeatable)", "только этот скилл (можно несколько раз)"))
    s.add_argument("--skip-skill", action="append", help=T("skip this skill (repeatable)", "не ставить этот скилл (можно несколько раз)"))
    s.add_argument("--optional", action="store_true", help=T("including optional skills", "включая необязательные скиллы"))
    s.add_argument("--variant", help=T("file variant (e.g. for awesome-design-md: stripe)", "вариант файла (например, для awesome-design-md: stripe)"))
    s.add_argument("-n", "--dry-run", action="store_true", help=T("show the plan, change nothing", "показать план, ничего не менять"))
    s.add_argument("-y", "--yes", action="store_true", help=T("do not ask for confirmation", "не спрашивать подтверждение"))
    s.add_argument("--force", action="store_true", help=T("replace existing files (with a backup)", "заменять существующие файлы (с резервной копией)"))
    s.add_argument("--accept-risk", action="store_true", help=T("confirm high-risk entries", "подтвердить записи с высоким риском"))
    s.add_argument("--allow-draft", action="store_true", help=T("allow drafts (for review)", "разрешить черновики (для ревью)"))
    s.add_argument("--refresh", action="store_true", help=T("fetch the source again", "перекачать источник"))
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("uninstall", help=T("remove what skillpantry installed", "удалить то, что поставил skillpantry"))
    s.add_argument("ids", nargs="+")
    where(s)
    s.set_defaults(func=cmd_uninstall)

    s = sub.add_parser("status", help=T("what is installed", "что установлено"))
    s.add_argument("-p", "--path", help=T("project root (default: current folder)", "корень проекта (по умолчанию текущая папка)"))
    s.add_argument("--all", action="store_true", help=T("all places", "все места"))
    s.add_argument("-v", "--verbose", action="store_true", help=T("with files", "с файлами"))
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("fetch", help=T("fetch sources in advance (to work offline)", "скачать источники заранее (для работы офлайн)"))
    s.add_argument("ids", nargs="*")
    s.add_argument("--all", action="store_true")
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("audit", help=T("static audit of an entry's content", "статический аудит содержимого записи"))
    s.add_argument("id")
    s.add_argument("--all", action="store_true", help=T("all findings and scripts", "все находки и скрипты"))
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("outdated", help=T("are there new commits in the sources", "есть ли новые коммиты в источниках"))
    s.add_argument("ids", nargs="*")
    s.set_defaults(func=cmd_outdated)

    s = sub.add_parser("bump", help=T("pin an entry to a new commit (becomes a draft until reviewed)", "закрепить запись на новом коммите (станет draft до ревью)"))
    s.add_argument("id")
    s.add_argument("--ref", help=T("a specific SHA (default HEAD)", "конкретный SHA (по умолчанию HEAD)"))
    s.add_argument("--diff", action="store_true", help=T("show changes in text files", "показать изменения в текстовых файлах"))
    s.set_defaults(func=cmd_bump)

    s = sub.add_parser("approve", help=T("mark an entry as reviewed", "отметить запись проверенной после ревью"))
    s.add_argument("id")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(func=cmd_approve)

    s = sub.add_parser("add", help=T("your own entry from a folder (or GitHub with --mine); without --mine — a draft for the shared catalog", "своя запись из папки (или GitHub с --mine); без --mine — черновик в общий каталог"))
    s.add_argument("url", help=T("folder or file on this computer, or a GitHub link / owner/repo", "папка или файл на компьютере, либо ссылка на GitHub / owner/repo"))
    s.add_argument("--id", help=T("entry id (default: from the name)", "id записи (по умолчанию из названия)"))
    s.add_argument("--mine", action="store_true", help=T("GitHub repo — into your own catalog, not the shared one", "GitHub-репозиторий — в свой каталог, а не в общий"))
    s.add_argument("--name", help=T("name (default: from SKILL.md or the folder name)", "название (по умолчанию из SKILL.md или имени папки)"))
    s.add_argument("--summary", help=T("one line: what it is and why", "одна строка: что это и зачем"))
    s.add_argument("--category", help=T("category: an existing key or a new one (default own)", "категория: существующий ключ или новый (по умолчанию own)"))
    s.add_argument("--category-title", help=T("name of the new category", "название новой категории"))
    s.add_argument("--tags", help=T("comma-separated tags", "теги через запятую"))
    s.add_argument("--risk", choices=("low", "medium", "high"), default="low")
    s.add_argument("--reviewed", action="store_true", help=T("GitHub: content reviewed — verified right away", "GitHub: содержимое просмотрено — сразу verified"))
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("drop", help=T("remove your own entry from the catalog (the source stays)", "удалить свою запись из каталога (источник не трогается)"))
    s.add_argument("id")
    s.add_argument("-y", "--yes", action="store_true")
    s.set_defaults(func=cmd_drop)

    s = sub.add_parser("validate", help=T("validate all manifests", "проверить все манифесты"))
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("export-md", help=T("catalog as Markdown for Obsidian", "каталог в Markdown для Obsidian"))
    s.add_argument("-o", "--output", help=T("file or vault folder", "файл или папка vault"))
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("cache-clear", help=T("clear the cache of fetched sources", "очистить кэш скачанных источников"))
    s.add_argument("id", nargs="?")
    s.set_defaults(func=cmd_cache)

    s = sub.add_parser("lang", help=T("show or save the interface language", "показать или сохранить язык интерфейса"))
    s.add_argument("value", nargs="?", choices=("en", "ru", "auto"),
                   help=T("en, ru, or auto (follow the system)", "en, ru или auto (как в системе)"))
    s.set_defaults(func=cmd_lang)

    s = sub.add_parser("doctor", help=T("check the environment", "проверить окружение"))
    s.set_defaults(func=cmd_doctor)
    return p


def tui_available(args) -> bool:
    """Full-screen UI needs a real interactive terminal of a usable size."""
    if getattr(args, "plain", False) or os.environ.get("SKILLPANTRY_PLAIN"):
        return False
    if not (sys.stdin.isatty() and sys.stdout.isatty()) or os.environ.get("TERM") == "dumb":
        return False
    size = shutil.get_terminal_size((0, 0))
    if size.columns < 60 or size.lines < 15:
        return False
    from skillpantry.tui import supported
    return supported()


def main(argv: list[str] | None = None) -> int:
    ui.setup_stdio()
    from skillpantry import i18n
    argv = list(sys.argv[1:] if argv is None else argv)
    # the language must be known before the parser builds its help texts
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            i18n.set_lang(argv[i + 1])
        elif a.startswith("--lang="):
            i18n.set_lang(a.split("=", 1)[1])
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        cat = Catalog(Path(args.catalog) if args.catalog else None)
        if args.cmd is None:
            if tui_available(args):
                from skillpantry.app import run
                return run(cat)
            from skillpantry.interactive import run
            return run(cat)
        if cat.errors and args.cmd not in ("validate", "add", "approve", "doctor", "drop", "lang"):
            bad = ", ".join(cat.errors)
            print(ui.yellow(T(f"! there are errors in manifests ({bad}) — skillpantry validate", f"! в манифестах есть ошибки ({bad}) — skillpantry validate")), file=sys.stderr)
        return args.func(cat, args)
    except (CatalogError, FetchError, InstallError, ValueError) as ex:
        print(ui.red(T(f"Error: {ex}", f"Ошибка: {ex}")), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(T("\nInterrupted", "\nПрервано"))
        return 130
    except BrokenPipeError:  # output piped into head/less that exited early
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
