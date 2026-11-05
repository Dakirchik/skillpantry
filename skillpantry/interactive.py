"""Interactive menu: pick entries, pick a target, review the plan, apply."""

from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path

from skillpantry import ui
from skillpantry.i18n import T
from skillpantry.catalog import TARGET_LABELS, TARGETS, Catalog
from skillpantry.cli import (default_library, entry_line, install_entries, make_layout,
                            print_legend, show_entry)
from skillpantry.installer import installed, uninstall


def _pick_entries(cat: Catalog, target: str) -> list[str]:
    entries = [e for e in cat.sorted() if e.support(target) != "none"]
    shown = entries
    while True:
        print()
        print_legend(target)
        for e in shown:
            print(entry_line(e, target))
        print(ui.dim(T("  Numbers separated by spaces or ranges (1 3 5-7) · /text — search · " "?N — details · work — work-safe only · * — all · empty — back", "  Номера через пробел или диапазоны (1 3 5-7) · /текст — поиск · "
                     "?N — подробно · work — только для работы · * — все · пусто — назад")))
        ans = ui.ask(T("Choice", "Выбор"))
        if not ans:
            return []
        if ans.startswith("/"):
            q = ans[1:].strip().lower()
            shown = [e for e in entries if q in " ".join(
                [e.id, e.name, e.search_text, e.category]).lower()] or entries
            continue
        if ans.startswith("?"):
            try:
                show_entry(cat.get(ans[1:].strip()))
            except Exception as ex:
                print(ui.red(str(ex)))
            continue
        if ans == "work":
            shown = [e for e in entries if e.work_safe]
            continue
        if ans == "*":
            return [e.id for e in shown]
        nums = ui.parse_selection(ans, max(e.number for e in entries))
        by_num = {e.number: e for e in entries}
        picked = [by_num[n].id for n in nums if n in by_num]
        missing = [n for n in nums if n not in by_num]
        if missing:
            print(ui.yellow(T(f"  Not available for {TARGET_LABELS[target]}: {missing}", f"  Недоступны для {TARGET_LABELS[target]}: {missing}")))
        if picked:
            print(ui.green(T("  Selected: ", "  Выбрано: ")) + ", ".join(picked))
            return picked


def _pick_layout(target: str):
    if target == "folder":
        dest = ui.ask(T("Folder", "Папка"), str(default_library()))
        return make_layout("folder", dest=dest)
    scope = ui.choose(T("Where", "Куда"), [("project", T(f"Into the project: {os.getcwd()}", f"В проект: {os.getcwd()}")),
                               ("other", T("Into another project (enter a path)", "В другой проект (указать путь)")),
                               ("global", T("Globally (home folder, for all projects)", "Глобально (домашняя папка, для всех проектов)"))], "project")
    if scope == "global":
        return make_layout(target, global_=True)
    if scope == "other":
        path = ui.ask(T("Project path", "Путь к проекту"))
        if not Path(path).expanduser().is_dir():
            print(ui.red(T("  No such folder", "  Такой папки нет")))
            return None
        return make_layout(target, path=path)
    return make_layout(target)


def _install_flow(cat: Catalog) -> None:
    print()
    target = ui.choose(T("Install into", "Куда ставим"), [(t, TARGET_LABELS[t]) for t in TARGETS], "claude-code")
    ids = _pick_entries(cat, target)
    if not ids:
        return
    layout = _pick_layout(target)
    if layout is None:
        return
    optional = any(any(s.optional for s in cat.get(i).skills) for i in ids) and \
        ui.confirm(T("Install optional skills too?", "Ставить и необязательные скиллы?"), False)
    args = Namespace(dry_run=False, yes=False, force=False, accept_risk=False, allow_draft=False,
                     refresh=False, skill=None, skip_skill=None, optional=optional, variant=None,
                     interactive=True)
    install_entries(cat, ids, layout, args)


def _status_flow(cat: Catalog) -> None:
    recs = installed(None)
    if not recs:
        print(T("Nothing has been installed with skillpantry yet", "Через skillpantry пока ничего не установлено"))
        return
    for i, r in enumerate(recs, 1):
        print(f"  {i:>2}. {r['id']:<22} {r['target']:<12} {r['scope']:<8} {ui.dim(r['root'])}")
    ans = ui.ask(T("Numbers to remove (empty — back)", "Номера для удаления (пусто — назад)"))
    for n in ui.parse_selection(ans, len(recs)):
        r = recs[n - 1]
        layout = make_layout(r["target"], global_=r["scope"] == "global",
                             path=r["root"] if r["scope"] == "project" else None,
                             dest=r["root"] if r["scope"] == "folder" else None)
        if ui.confirm(T(f"Remove {r['id']} from {layout.label}?", f"Удалить {r['id']} из {layout.label}?"), False):
            uninstall(layout, r["id"])
            print(ui.green(T(f"  ✓ {r['id']} removed", f"  ✓ {r['id']} удалён")))


def _add_flow(cat: Catalog) -> None:
    """Text-mode version of the «Добавить своё» form."""
    from skillpantry import mine
    from skillpantry.catalog import RISKS
    text = ui.ask(T("Folder, file or GitHub link (empty — back)", "Папка, файл или ссылка на GitHub (пусто — назад)"))
    if not text:
        return
    print(ui.dim(T("  Looking at the source…", "  Разбираю источник…")))
    src = mine.inspect_source(text)
    if not src.components:
        print(ui.yellow(T("  No SKILL.md, rules file or .mcp.json found", "  Не нашлось ни SKILL.md, ни файла правил, ни .mcp.json")))
        return
    print(ui.bold(T("  Found:", "  Найдено:")))
    for i, c in enumerate(src.components, 1):
        print(f"   {i:>2}. [{'x' if c.on else ' '}] {c.label}")
    ans = ui.ask(T("What to install — numbers (empty — the ticked ones)", "Что ставить — номера (пусто — отмеченное)"))
    keys = ([src.components[n - 1].key for n in ui.parse_selection(ans, len(src.components))]
            if ans else None)
    name = ui.ask(T("Name", "Название"), src.name)
    eid = ui.ask(T("id (Latin letters)", "id (латиница)"), mine.unique_id(cat, mine.slug(name)))
    cats = sorted((c for c in cat.categories.values() if c.id != "excluded"),
                  key=lambda c: (c.order, c.title))
    for i, c in enumerate(cats, 1):
        print(f"   {i:>2}. {c.title}" + ui.dim(f"  ({c.id})"))
    print(T(f"   {len(cats) + 1:>2}. + new category", f"   {len(cats) + 1:>2}. + новая категория"))
    ans = ui.ask(T("Category — number", "Категория — номер"), "1" if cats else str(len(cats) + 1))
    new_cat = None
    category = "own"
    if ans.isdigit() and 1 <= int(ans) <= len(cats):
        category = cats[int(ans) - 1].id
    elif ans.isdigit() and int(ans) == len(cats) + 1:
        title = ui.ask(T("New category name", "Название новой категории"))
        new_cat = {"id": mine.slug(title, "my-category"), "title": title,
                   "description": ui.ask(T("Category description (may be empty)", "Описание категории (можно пусто)"))}
    tags = ui.ask(T("Tags, comma-separated", "Теги через запятую") + ui.dim(T(f" (in use: {', '.join(cat.all_tags()[:12])}…)", f" (есть: {', '.join(cat.all_tags()[:12])}…)")))
    f = mine.Form(name=name, id=eid, category=category, summary=ui.ask(T("Summary", "Кратко"), src.summary),
                  description=src.description, tags=[t for t in tags.split(",") if t.strip()],
                  risk=ui.choose(T("Risk", "Риск"), [(r, r) for r in RISKS], "low" if src.kind == "local" else "medium"),
                  work_safe=ui.confirm(T("OK for work projects?", "Можно в рабочие проекты?"), src.kind == "local"),
                  reviewed=src.kind == "local" or ui.confirm(T("Content reviewed (otherwise a draft)?", "Содержимое просмотрено (иначе черновик)?"), False),
                  components=keys, new_category=new_cat)
    errs = mine.check_form(cat, src, f)
    if errs:
        for v in errs.values():
            print(ui.red("  ✗ " + v))
        return
    path = mine.save(cat, src, f)
    print(ui.green(f"  ✓ {cat.get(f.id).name}") + ui.dim(f"  {path}"))


def _sync_location(target: str, *, source: bool, same_project: Path | None = None):
    label = TARGET_LABELS[target]
    prompt = (T(f"Source location — {label}", f"Откуда читать скиллы — {label}") if source else
              T(f"Destination location — {label}", f"Куда записать скиллы — {label}"))
    print(ui.bold("\n" + prompt))
    options = []
    if same_project is not None:
        options.append(("same", T(f"Same project: {same_project}", f"В тот же проект: {same_project}")))
    options += [
        ("other", T("Choose a project folder (enter path)", "Указать папку проекта")),
        ("current", T(f"Current folder: {Path.cwd()}", f"Текущая папка: {Path.cwd()}")),
        ("global", T("Global skills (home folder)", "Глобальные скиллы (домашняя папка)")),
        ("back", T("Back to menu", "Назад в меню")),
    ]
    choice = ui.choose(prompt, options, "same" if same_project is not None else "other")
    if choice == "back":
        return None
    if choice == "same":
        layout = make_layout(target, path=str(same_project))
    elif choice == "global":
        layout = make_layout(target, global_=True)
    elif choice == "current":
        layout = make_layout(target)
    else:
        text = ui.ask(T("Project root (not the skills subfolder; empty — back)",
                        "Корень проекта (не подпапка skills; пусто — назад)"))
        if not text.strip():
            return None
        path = Path(text.strip().strip('"').strip("'")).expanduser()
        if not path.is_dir():
            print(ui.red(T("No such project folder", "Такой папки проекта нет")))
            return None
        layout = make_layout(target, path=str(path))
    print(ui.dim(T("Skills folder: ", "Папка скиллов: ") + str(layout.skills_dir(""))))
    return layout


def sync_flow(cat: Catalog) -> None:
    from skillpantry.sync import AGENTS, plan_sync
    from skillpantry.cli import cmd_sync
    agents = [(a, TARGET_LABELS[a]) for a in AGENTS]
    source = ui.choose(T("Source agent", "Из какого ИИ перенести скиллы"), agents, "claude-code")
    target = ui.choose(T("Destination agent", "В какой ИИ перенести скиллы"), agents, "codex")
    print(ui.bold(f"\n{TARGET_LABELS[source]} → {TARGET_LABELS[target]}"))
    source_layout = _sync_location(source, source=True)
    if source_layout is None:
        return
    destination = _sync_location(target, source=False,
                                 same_project=source_layout.root if source_layout.scope == "project" else None)
    if destination is None:
        return
    plan = plan_sync(source_layout, destination)
    for i, x in enumerate(plan, 1):
        print(f"  {i}. {x.name} [{x.status}]")
        for warning in x.warnings:
            print(ui.yellow("    ! " + warning))
    answer = ui.ask(T("Skill numbers (empty — all)", "Номера скиллов (пусто — все)"))
    chosen = [plan[i - 1].name for i in ui.parse_selection(answer, len(plan))] if answer else None
    if answer and not chosen:
        return
    conflicts = [x for x in plan if x.status == "conflict" and (chosen is None or x.name in chosen)]
    force = bool(conflicts) and ui.confirm(T("Replace conflicting folders with backups?", "Заменить конфликтующие папки с резервной копией?"), False)
    if conflicts and not force:
        return
    cmd_sync(cat, Namespace(source=source, source_global=source_layout.scope == "global",
                           source_path=str(source_layout.root) if source_layout.scope != "global" else None,
                           target=target, global_=destination.scope == "global",
                           path=str(destination.root) if destination.scope != "global" else None,
                           skill=chosen, force=force, dry_run=False, yes=False, interactive=True))


def run(cat: Catalog) -> int:
    print(ui.bold("skillpantry") + ui.dim(T(f" · {len(cat.entries)} entries · catalog {cat.dir}", f" · {len(cat.entries)} записей · каталог {cat.dir}")))
    if cat.errors:
        print(ui.yellow(T(f"! errors in manifests: {', '.join(cat.errors)} (skillpantry validate)", f"! ошибки в манифестах: {', '.join(cat.errors)} (skillpantry validate)")))
    while True:
        print()
        action = ui.choose(T("Action", "Действие"), [
            ("install", T("Install skills / plugins / rules", "Поставить скиллы / плагины / правила")),
            ("sync", T("Sync skills between agents", "Синхронизация скиллов между агентами")),
            ("browse", T("Browse the catalog", "Посмотреть каталог")),
            ("status", T("Installed (and removal)", "Установленное (и удаление)")),
            ("add", T("Add your own (folder or GitHub)", "Добавить своё (папка или GitHub)")),
            ("quit", T("Quit", "Выход"))], "install")
        try:
            if action == "install":
                _install_flow(cat)
            elif action == "sync":
                sync_flow(cat)
            elif action == "browse":
                print_legend(None)
                for e in cat.sorted():
                    print(entry_line(e))
                key = ui.ask(T("Number or id for details (empty — back)", "Номер или id для подробностей (пусто — назад)"))
                if key:
                    show_entry(cat.get(key))
            elif action == "status":
                _status_flow(cat)
            elif action == "add":
                _add_flow(cat)
            else:
                return 0
        except KeyboardInterrupt:
            print()
        except Exception as ex:  # keep the menu alive on recoverable errors
            print(ui.red(T(f"Error: {ex}", f"Ошибка: {ex}")))
