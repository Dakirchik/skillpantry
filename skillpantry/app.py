"""Full-screen interface: home, install wizard, catalog browser, installed items.

The wizard goes categories → entries → target → location → options → plan.
Planning, auditing and applying reuse the same code as `skillpantry install`, printed
on the normal screen so the result stays in the terminal history.
"""

from __future__ import annotations

import os
import re
from argparse import Namespace
from collections import defaultdict
from pathlib import Path

from skillpantry import ui
from skillpantry.catalog import RISK_LABELS, STATUS_LABELS, TARGET_LABELS, TARGETS, Catalog, Entry
from skillpantry.cli import default_library, install_entries, make_layout
from skillpantry.fetch import FetchError, fetch
from skillpantry.installer import InstallError, installed, list_variants, uninstall
from skillpantry.targets import Layout
from skillpantry.catalog import RISKS, CatalogError
from skillpantry import i18n
from skillpantry.i18n import Labels, T, pick
from skillpantry.tui import (BOLD, CYAN, DIM, GREEN, RED, YELLOW, Field, Item, Line, Quit, Term, ask_text,
                            form, keys_hint, message, select, view)

def target_help(t: str) -> list[str]:
    return pick({
        "claude-code": ["Skills → .claude/skills/", "Rules → a block in CLAUDE.md",
                        "MCP → .mcp.json (global — I will show the claude mcp add command)",
                        "Slash commands → .claude/commands/"],
        "codex": ["Skills → .agents/skills/", "Rules → a block in AGENTS.md", "MCP → .codex/config.toml"],
        "gemini": ["Skills → .gemini/skills/", "Rules → a block in GEMINI.md", "MCP → .gemini/settings.json"],
        "cursor": ["Skills → .cursor/skills/", "Rules → .cursor/rules/<id>.mdc", "MCP → .cursor/mcp.json"],
        "folder": ["Everything in one folder: <folder>/<id>/skills, RULES.md, mcp.json and SKILLPANTRY.md",
                   "with source, license and manual steps — to connect it anywhere yourself"],
    }, {
        "claude-code": ["Скиллы → .claude/skills/", "Правила → блок в CLAUDE.md",
                        "MCP → .mcp.json (глобально — покажу команду claude mcp add)",
                        "Slash-команды → .claude/commands/"],
        "codex": ["Скиллы → .agents/skills/", "Правила → блок в AGENTS.md", "MCP → .codex/config.toml"],
        "gemini": ["Скиллы → .gemini/skills/", "Правила → блок в GEMINI.md", "MCP → .gemini/settings.json"],
        "cursor": ["Скиллы → .cursor/skills/", "Правила → .cursor/rules/<id>.mdc", "MCP → .cursor/mcp.json"],
        "folder": ["Всё в одну папку: <папка>/<id>/skills, RULES.md, mcp.json и SKILLPANTRY.md",
                   "с источником, лицензией и ручными шагами — чтобы подключить самому куда угодно"],
    })[t]


# ---- shared ---------------------------------------------------------------------------

def installs_by_entry() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for rec in installed(None):
        out[rec["id"]].append(rec)
    return out


def short_path(path: str | Path, limit: int = 60) -> str:
    """~ for the home folder; long paths keep their tail: ~/…/work/lk-front."""
    p = str(path)
    home = str(Path.home())
    if p == home or p.startswith(home + os.sep):
        p = "~" + p[len(home):]
    if len(p) <= limit:
        return p
    parts = p.replace("\\", "/").split("/")
    tail = ""
    for part in reversed(parts):
        cand = part + ("/" + tail if tail else "")
        if len(cand) + 4 > limit:
            break
        tail = cand
    head = parts[0] if parts[0] in ("~", "") else parts[0]
    return f"{head}/…/{tail}" if tail else p[-limit:]


def where_label(rec: dict) -> str:
    t = TARGET_LABELS.get(rec["target"], rec["target"])
    if rec["scope"] == "global":
        return T(f"{t} · global", f"{t} · глобально")
    return f"{t} · {short_path(rec['root'])}"


def paragraphs(text: str) -> list[str]:
    """Manifest text is hand-wrapped; join lines back into paragraphs for re-wrapping."""
    return [" ".join(p.split()) for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]


def payload_text(e: Entry) -> str:
    parts = []
    if e.skills:
        parts.append(T("skills", "скиллы"))
    if e.rules:
        parts.append(T("rules", "правила"))
    if e.mcp:
        parts.append("MCP: " + ", ".join(m.name for m in e.mcp))
    if e.commands:
        parts.append(T("slash commands", "slash-команды"))
    if e.variants:
        parts.append(T("a file to pick (", "файл на выбор (") + e.variants["dest"] + ")")
    if e.workspace:
        parts.append(T("the whole workspace folder", "рабочая папка целиком"))
    if not parts and e.manual:
        parts.append(T("manual steps only", "только ручные шаги"))
    return ", ".join(parts) or T("nothing", "ничего")


def entry_details(e: Entry, installs: dict[str, list[dict]], full: bool = False) -> list[Line]:
    risk_style = {"low": GREEN, "medium": YELLOW, "high": RED}[e.risk]
    L: list[Line] = [(e.name, BOLD), (e.summary, ""), ("", "")]
    L.append((T(f"Risk: {RISK_LABELS[e.risk]}", f"Риск: {RISK_LABELS[e.risk]}"), risk_style))
    L.append((T(f"Status: {STATUS_LABELS[e.status]} · work-safe: {'yes' if e.work_safe else 'no'}", f"Статус: {STATUS_LABELS[e.status]} · для рабочих проектов: {'да' if e.work_safe else 'нет'}")
              + (T(f" · license {e.license}", f" · лицензия {e.license}") if e.license not in (None, "own") else "")
              + (T(" · your own entry", " · своя запись") if e.origin == "user" else ""), CYAN if e.origin == "user" else DIM))
    if e.tags:
        L.append((T("Tags: ", "Теги: ") + ", ".join(e.tags), DIM))
    if e.repo or e.local:
        L.append((T(f"Source: {e.source_label}", f"Источник: {e.source_label}"), DIM))
    elif e.url:
        L.append((T(f"Link: {e.url}", f"Ссылка: {e.url}"), DIM))
    L.append((T(f"Installs: {payload_text(e)}", f"Что ставится: {payload_text(e)}"), ""))
    L.append((T("Targets: ", "Цели: ") + "  ".join(f"{TARGET_LABELS[t].split(' ')[0]} {ui.SUPPORT_MARK[e.support(t)]}"
                                    for t in TARGETS), DIM))
    recs = installs.get(e.id, [])
    if recs:
        L += [("", ""), (T("Already installed:", "Уже установлено:"), GREEN)]
        for r in recs:
            stale = e.ref and r.get("ref") != e.ref
            L.append((f"• {where_label(r)}" + ((T("  ↑ the folder changed", "  ↑ папка изменилась") if e.local else T("  ↑ newer available", "  ↑ есть новее"))
                                                if stale else ""), YELLOW if stale else ""))
    if e.description:
        L.append(("", ""))
        for para in paragraphs(e.description):
            L += [(para, ""), ("", "")]
        L.pop()
    if e.risk_notes:
        L += [("", ""), (T("Risks:", "Риски:"), YELLOW)] + [(f"! {n}", YELLOW) for n in e.risk_notes]
    if full or len(e.manual_for("any")) <= 3:
        manual = [(t, s) for t, steps in e.manual.items() for s in steps]
        if manual:
            L += [("", ""), (T("Manual steps:", "Ручные шаги:"), BOLD)]
            for t, s in manual:
                who = "" if t == "any" else f"[{TARGET_LABELS.get(t, t).split(' ')[0]}] "
                L.append((f"• {who}{s}", ""))
    if e.overlaps:
        L += [("", ""), (T("Overlaps with: ", "Пересекается с: ") + ", ".join(e.overlaps) + T(" — usually you pick one", " — обычно ставят что-то одно"), DIM)]
    return L


def entry_item(e: Entry, installs: dict[str, list[dict]], target: str | None = None) -> Item:
    marks = []
    style_ = ""
    recs = installs.get(e.id, [])
    if recs:
        stale = any(e.ref and r.get("ref") != e.ref for r in recs)
        marks.append("↑" if stale else "✓")
        style_ = YELLOW if stale else GREEN
    if e.risk == "high":
        marks.append("⚠")
        style_ = style_ or RED
    if e.origin == "user":
        marks.append("★")
        style_ = style_ or CYAN
    disabled = None
    if e.status == "excluded":
        disabled = T("excluded from installation", "исключено из установки")
    elif e.status == "reference":
        disabled = T("reference entry — nothing to install", "справочная запись — ставить нечего")
    elif e.status == "draft":
        disabled = (T("draft: review the content first — Catalog → entry → Edit → «Reviewed by me»", "черновик: сначала просмотрите содержимое — Каталог → запись → Изменить → «Проверено мной»")
                    if e.origin == "user" else T(f"draft: awaiting review (skillpantry approve {e.id})", f"черновик: ждёт ревью (skillpantry approve {e.id})"))
    elif target and e.support(target) == "none":
        disabled = (T("only as a workspace folder (target «Folder»)", "только как рабочая папка (цель «Папка»)") if e.workspace
                    else T(f"not supported for {TARGET_LABELS[target]}", f"не подходит для {TARGET_LABELS[target]}"))
    elif target and e.support(target) == "manual":
        marks.append("◐")
    return Item(e.id, e.name, " ".join(marks), style_, disabled,
                search=e.search_text)


def grouped(cat: Catalog, entries: list[Entry], installs, target=None) -> list[Item]:
    items: list[Item] = []
    for c in cat.category_order():
        group = [e for e in entries if e.category == c.id]
        if not group:
            continue
        items.append(Item(f"#{c.id}", f"{c.title} ({len(group)})", header=True))
        items += [entry_item(e, installs, target) for e in sorted(group, key=lambda x: x.number)]
    return items


def legend() -> str:
    return T("✓ installed  ↑ newer  ★ mine  ⚠ high risk  ◐ manual only", "✓ стоит  ↑ есть новее  ★ своё  ⚠ высокий риск  ◐ только вручную")


# ---- install wizard ------------------------------------------------------------------------

class Wizard:
    @property
    def STEPS(self) -> tuple[str, ...]:
        return pick(("Categories", "Entries", "Target", "Location", "Options", "Plan"),
                    ("Категории", "Записи", "Куда", "Где", "Параметры", "План"))

    def __init__(self, term: Term, cat: Catalog, preselect: list[str] | None = None):
        self.t = term
        self.cat = cat
        self.cats: list[str] = []
        self.ids: list[str] = list(preselect or [])
        self.target = "claude-code"
        self.layout: Layout | None = None
        self.accepted: set[str] = set()
        self.options: dict[str, dict] = {}
        self.where_key = "project"

    def crumbs(self, i: int) -> str:
        return " › ".join(f"[{s}]" if n == i else s for n, s in enumerate(self.STEPS))

    def run(self) -> None:
        step = 1 if self.ids else 0
        while True:
            if step < 0:
                return
            if step == 0:
                step += 1 if self.step_categories() else -99
            elif step == 1:
                step += 1 if self.step_entries() else -1
            elif step == 2:
                step += 1 if self.step_target() else -1
            elif step == 3:
                step += 1 if self.step_where() else -1
            elif step == 4:
                step += 1 if self.step_options() else -1
            else:
                self.apply()
                return

    # 1 — categories
    def step_categories(self) -> bool:
        installs = installs_by_entry()
        items = []
        for c in self.cat.category_order():
            group = [e for e in self.cat.sorted() if e.category == c.id]
            n_ok = sum(1 for e in group if e.installable)
            if c.id == "excluded":
                continue
            items.append(Item(c.id, c.title, f"{n_ok}", DIM, search=c.description))

        def details(it: Item) -> list[Line]:
            c = self.cat.category(it.key)
            L: list[Line] = [(c.title, BOLD), (" ".join(c.description.split()), ""), ("", ""),
                             (T("Includes:", "Входит:"), DIM)]
            for e in self.cat.sorted():
                if e.category == c.id:
                    mark = "✓ " if installs.get(e.id) else "• "
                    tail = "" if e.installable else f"  ({STATUS_LABELS[e.status]})"
                    L.append((f"{mark}{e.name} — {e.summary}{tail}", DIM if not e.installable else ""))
            return L

        res = select(self.t, items, title=T("Which areas do you need? Tick one or more", "Какие направления нужны? Отметьте одно или несколько"),
                     crumbs=self.crumbs(0), multi=True, details=details, selected=self.cats,
                     hint=keys_hint(multi=True))
        if res.back:
            return False
        self.cats = res.keys
        return True

    # 2 — entries
    def step_entries(self) -> bool:
        installs = installs_by_entry()
        pool = [e for e in self.cat.sorted()
                if (not self.cats or e.category in self.cats) or e.id in self.ids]
        items = grouped(self.cat, pool, installs)
        res = select(self.t, items, title=T("What to install or update? ", "Что поставить или обновить? ") + legend(),
                     crumbs=self.crumbs(1), multi=True,
                     details=lambda it: entry_details(self.cat.get(it.key), installs),
                     selected=self.ids, start=self.ids[0] if self.ids else None)
        if res.back:
            return False
        self.ids = res.keys
        return True

    # 3 — target
    def step_target(self) -> bool:
        entries = [self.cat.get(i) for i in self.ids]
        items = []
        for t in TARGETS:
            n = sum(1 for e in entries if e.support(t) != "none")
            items.append(Item(t, TARGET_LABELS[t], f"{n}/{len(entries)}", GREEN if n == len(entries) else YELLOW))

        def details(it: Item) -> list[Line]:
            t = it.key
            L: list[Line] = [(TARGET_LABELS[t], BOLD)] + [(f"• {h}", "") for h in target_help(t)] + [("", "")]
            for e in entries:
                sup = e.support(t)
                st = {"auto": GREEN, "manual": YELLOW, "none": RED}[sup]
                why = ""
                if sup == "manual":
                    why = T(" — I will show the commands for manual install", " — покажу команды для ручной установки")
                elif sup == "none":
                    why = T(" — workspace folder, «Folder» only", " — рабочая папка, только «Папка»") if e.workspace else T(" — not supported, will be skipped", " — не подходит, будет пропущено")
                L.append((f"{ {'auto': '✓', 'manual': '◐', 'none': '✗'}[sup] } {e.name}{why}", st))
            return L

        res = select(self.t, items, title=T(f"Where to add the selection ({len(entries)})?", f"Куда добавить выбранное ({len(entries)})?"),
                     crumbs=self.crumbs(2), details=details, start=self.target)
        if res.back:
            return False
        self.target = res.keys[0]
        return True

    # 4 — where
    def step_where(self) -> bool:
        cwd = Path(os.getcwd()).resolve()
        home = Path.home().resolve()
        if self.target == "folder":
            lib = default_library()
            items = [Item("library", T(f"Library: {short_path(lib)}", f"Библиотека: {short_path(lib)}")), Item("other", T("Another folder…", "Другая папка…"))]
        else:
            items = [Item("project", T(f"This project: {short_path(cwd, 40)}", f"Этот проект: {short_path(cwd, 40)}"),
                          disabled=T("this is the home folder — choose a project or «global»", "это домашняя папка — выберите проект или «глобально»") if cwd == home else None),
                     Item("other", T("Another project…", "Другой проект…")),
                     Item("global", T("Global — for all projects (~)", "Глобально — для всех проектов (~)"))]

        def details(it: Item) -> list[Line]:
            if it.key == "project":
                found = [n for n in (".git", ".claude", ".agents", ".gemini", ".cursor", "CLAUDE.md",
                                     "AGENTS.md", "package.json") if (cwd / n).exists()]
                return [(T("Current terminal folder", "Текущая папка терминала"), BOLD), (str(cwd), ""), ("", ""),
                        (T("Found: ", "Найдено: ") + (", ".join(found) if found else T("nothing that looks like a project", "ничего похожего на проект")),
                         "" if found else YELLOW),
                        ("", ""), (T("Files go inside the project; commit them so the team " "has the same set.", "Файлы лягут внутрь проекта; их можно закоммитить, чтобы у команды "
                                   "был тот же набор."), DIM)]
            if it.key == "global":
                return [(T("Global", "Глобально"), BOLD), (T("Installed into the home folder, works in every project.", "Ставится в домашнюю папку и работает во всех проектах."), ""),
                        ("", ""), (T("Cursor global rules are set only in the app settings; " "a global MCP for Claude Code — with a command I will show.", "Для Cursor глобальные правила задаются только в настройках приложения; "
                                   "глобальный MCP для Claude Code — командой, которую я покажу."), DIM)]
            if it.key == "library":
                return [(T("A folder for yourself", "Папка для себя"), BOLD), (str(default_library()), ""), ("", ""),
                        (T("Change the default folder: pantryctl install --library <path> " "or library = \"…\" in ~/.skillpantry/config.toml", "Поменять папку по умолчанию: pantryctl install --library <путь> "
                         "или library = \"…\" в ~/.skillpantry/config.toml"), DIM)]
            return [(T("You will enter the path on the next screen.", "Укажете путь на следующем экране."), "")]

        while True:
            res = select(self.t, items, title=T(f"Where exactly — {TARGET_LABELS[self.target]}", f"Куда именно — {TARGET_LABELS[self.target]}"),
                         crumbs=self.crumbs(3), details=details, start=self.where_key)
            if res.back:
                return False
            key = self.where_key = res.keys[0]
            if key == "project":
                self.layout = make_layout(self.target)
            elif key == "global":
                self.layout = make_layout(self.target, global_=True)
            elif key == "library":
                self.layout = make_layout("folder")
            else:
                folder = self.target == "folder"
                path = ask_text(self.t, T("Folder path", "Путь к папке") if folder else T("Project root path", "Путь к корню проекта"),
                                title=T("Another folder", "Другая папка") if folder else T("Another project", "Другой проект"), crumbs=self.crumbs(3),
                                note=[(T("~ at the start is fine. The «Folder» destination is created on install.", "Можно с ~ в начале. Папка для «Папки» будет создана при установке."), DIM)])
                if not path:
                    continue
                p = Path(path).expanduser()
                if not folder and not p.is_dir():
                    message(self.t, [(T(f"No such folder: {p}", f"Нет такой папки: {p}"), RED), ("", ""), (T("Esc — back", "Esc — назад"), DIM)],
                            title=T("Not found", "Не найдено"), crumbs=self.crumbs(3))
                    continue
                self.layout = (make_layout("folder", dest=str(p)) if folder
                               else make_layout(self.target, path=str(p)))
            return True

    # 5 — options (risk, optional skills, variants)
    def step_options(self) -> bool:
        entries = [self.cat.get(i) for i in self.ids if self.cat.get(i).support(self.target) != "none"]
        items: list[Item] = []
        risky = [e for e in entries if e.confirm or e.risk == "high"]
        if risky:
            items.append(Item("#risk", T("Confirm the risks — unticked entries will be skipped", "Подтвердите риски — без отметки запись будет пропущена"), header=True))
            items += [Item(f"risk:{e.id}", T(f"I understand the risks: {e.name}", f"Понимаю риски: {e.name}"), "⚠", RED) for e in risky]
        optional = [(e, i, s) for e in entries for i, s in enumerate(e.skills) if s.optional]
        if optional and any(e.support(self.target) == "auto" for e, _, _ in optional):
            last = None
            for e, i, sp in optional:
                if e.id != last:
                    items.append(Item(f"#opt:{e.id}", T(f"Optional skills: {e.name}", f"Дополнительные скиллы: {e.name}"), header=True))
                    last = e.id
                items.append(Item(f"opt:{e.id}:{i}", (sp.path or "").rstrip("/").split("/")[-1]))
        if items:
            def details(it: Item) -> list[Line]:
                kind, eid = it.key.split(":")[:2]
                e = self.cat.get(eid)
                if kind == "risk":
                    return [(e.name, BOLD), ("", "")] + [(f"! {n}", YELLOW) for n in e.risk_notes]
                return [(e.name, BOLD), (T("An optional skill from this entry.", "Необязательный скилл из этой записи."), ""), ("", ""),
                        (" ".join(e.description.split()), DIM)]
            pre = [f"risk:{i}" for i in self.accepted]
            pre += [f"opt:{eid}:{i}" for eid, o in self.options.items() for i in o.get("include_optional", [])]
            res = select(self.t, items, title=T("Install options", "Параметры установки"), crumbs=self.crumbs(4), multi=True,
                         details=details, selected=pre, empty_means_cursor=False, allow_empty=True,
                         hint=keys_hint(multi=True, search=False))
            if res.back:
                return False
            self.accepted = {k.split(":")[1] for k in res.keys if k.startswith("risk:")}
            self.options = {}
            for k in res.keys:
                if k.startswith("opt:"):
                    _, eid, idx = k.split(":")
                    self.options.setdefault(eid, {}).setdefault("include_optional", []).append(int(idx))
        # one file to pick (e.g. DESIGN.md)
        for e in entries:
            if not e.variants or e.support(self.target) != "auto":
                continue
            if self.layout and self.layout.scope == "global":
                continue
            choice = self.pick_variant(e)
            if choice is None:
                return False
            if choice:
                self.options.setdefault(e.id, {})["variant"] = choice
        return True

    def pick_variant(self, e: Entry) -> str | None:
        cols, rows = self.t.size()
        self.t.draw([T(f" Fetching {e.source_label}…", f" Загружаю {e.source_label}…")] + [""] * (rows - 1))
        try:
            snap = fetch(e)
        except FetchError as ex:
            message(self.t, [(str(ex), RED)], title=e.name, crumbs=self.crumbs(4))
            return ""
        variants = list_variants(e, snap)
        files = dict(variants)
        items = [Item("", T("Do not install a file", "Не ставить файл"))] + [Item(n, n) for n, _ in variants]

        def details(it: Item) -> list[Line]:
            if not it.key:
                return [(T("The entry will be skipped without a file.", "Запись будет пропущена без файла."), DIM)]
            text = files[it.key].read_text(encoding="utf-8", errors="replace").splitlines()[:80]
            return [(f"{e.variants['dest']} ← {it.key}", BOLD), ("", "")] + [(t, "") for t in text]

        res = select(self.t, items, title=T(f"{e.name}: which {e.variants['dest']} goes into the project?", f"{e.name}: какой {e.variants['dest']} положить в проект?"),
                     crumbs=self.crumbs(4), details=details,
                     start=self.options.get(e.id, {}).get("variant"))
        if res.back:
            return None
        return res.keys[0]

    # 6 — plan and apply on the normal screen
    def apply(self) -> None:
        ids = [i for i in self.ids if not (self.cat.get(i).confirm or self.cat.get(i).risk == "high")
               or i in self.accepted]
        skipped = [i for i in self.ids if i not in ids]
        args = Namespace(dry_run=False, yes=False, force=False, accept_risk=False, allow_draft=False,
                         refresh=False, skill=None, skip_skill=None, optional=False, variant=None,
                         interactive=True, accepted=self.accepted, entry_options=self.options)
        with self.t.suspend():
            print(ui.bold(f"\nskillpantry › {TARGET_LABELS[self.target]} · {self.layout.root}"))
            for i in skipped:
                print(ui.yellow(T(f"– {i}: risk not confirmed — skipped", f"– {i}: риск не подтверждён — пропущено")))
            try:
                if ids:
                    install_entries(self.cat, ids, self.layout, args)
                else:
                    print(T("Nothing to install.", "Нечего ставить."))
            except (InstallError, FetchError) as ex:
                print(ui.red(T(f"Error: {ex}", f"Ошибка: {ex}")))
            ui.ask(T("\nEnter — back to the menu", "\nEnter — вернуться в меню"))


# ---- installed --------------------------------------------------------------------------------

def installed_screen(t: Term, cat: Catalog) -> None:
    while True:
        recs = installed(None)
        if not recs:
            message(t, [(T("Nothing has been installed with skillpantry yet.", "Через skillpantry пока ничего не установлено."), ""), ("", ""),
                        (T("Install: home → «Install or update».", "Поставить: главное меню → «Установить или обновить»."), DIM)],
                    title=T("Installed", "Установленное"), crumbs=T("Installed", "Установленное"))
            return
        by_place: dict[str, list[dict]] = defaultdict(list)
        for r in recs:
            place = T("Global (home folder)", "Глобально (домашняя папка)") if r["scope"] == "global" else short_path(r["root"], 44)
            by_place[place].append(r)
        items: list[Item] = []
        index: dict[str, dict] = {}
        for place, group in by_place.items():
            items.append(Item(f"#{place}", place, header=True))
            for r in group:
                key = f"{r['target']}|{r['scope']}|{r['root']}|{r['id']}"
                index[key] = r
                e = cat.entries.get(r["id"])
                stale = bool(e and e.ref and r.get("ref") != e.ref)
                name = e.name if e else r["id"]
                tshort = TARGET_LABELS.get(r["target"], r["target"]).split(" ")[0]
                items.append(Item(key, name, f"{tshort} {'↑' if stale else '✓'}", YELLOW if stale else GREEN,
                                  search=f"{r['id']} {r['root']} {r['target']}"))

        def details(it: Item) -> list[Line]:
            r = index[it.key]
            e = cat.entries.get(r["id"])
            L: list[Line] = [(e.name if e else r["id"], BOLD), (where_label(r), ""), ("", "")]
            L.append((T(f"Installed: {r.get('installed_at', '?').replace('T', ' ')}", f"Установлено: {r.get('installed_at', '?').replace('T', ' ')}"), DIM))
            L.append((T(f"Version: {(r.get('ref') or '?')[:12]}", f"Версия: {(r.get('ref') or '?')[:12]}"), DIM))
            if e and e.ref and r.get("ref") != e.ref:
                L.append((T("Files in the folder changed — «Update»", "Файлы в папке изменились — «Обновить»") if e.local
                          else T(f"Newer in the catalog: {e.ref[:12]} — «Update»", f"В каталоге новее: {e.ref[:12]} — «Обновить»"), YELLOW))
            if r.get("options"):
                L.append((T("Options: ", "Параметры: ") + ", ".join(f"{k}={v}" for k, v in r["options"].items()), DIM))
            L += [("", ""), (T("Changes:", "Изменения:"), BOLD)]
            for a in r.get("actions", []):
                L.append((f"• {a['type']}: {a['path']}", ""))
            if r.get("env"):
                L += [("", ""), (T("Environment variables: ", "Переменные окружения: ") + ", ".join(sorted(set(r["env"]))), CYAN)]
            if r.get("manual"):
                L += [("", ""), (T("Manual steps:", "Ручные шаги:"), BOLD)] + [(f"• {m}", "") for m in r["manual"]]
            return L

        res = select(t, items, title=T("What is installed — tick to update or remove. ↑ — newer in the catalog", "Что стоит — отметьте, чтобы обновить или удалить. ↑ — в каталоге новее"),
                     crumbs=T("Installed", "Установленное"), multi=True, details=details)
        if res.back:
            return
        chosen = [index[k] for k in res.keys]
        stale_n = sum(1 for r in chosen if cat.entries.get(r["id"]) and r.get("ref") != cat.entries[r["id"]].ref)
        act = select(t, [Item("update", T(f"Update / reinstall ({len(chosen)})", f"Обновить / переустановить ({len(chosen)})"),
                              T(f"newer: {stale_n}", f"новее: {stale_n}") if stale_n else ""),
                         Item("remove", T(f"Remove ({len(chosen)})", f"Удалить ({len(chosen)})"), "", RED),
                         Item("back", T("Back", "Назад"))],
                     title=", ".join(r["id"] for r in chosen), crumbs=T("Installed › action", "Установленное › действие"),
                     details=lambda it: {
                         "update": [(T("Reinstalls the catalog version with the same options " "(skills, file variant). I will show the plan and ask first.", "Переустановит с версии из каталога с теми же параметрами "
                                     "(скиллы, вариант файла). Покажу план и спрошу подтверждение."), "")],
                         "remove": [(T("Undoes everything skillpantry did: skill folders, rule blocks, " "MCP entries. Your own files are not touched.", "Отменит всё, что сделал skillpantry: папки скиллов, блоки правил, "
                                     "записи MCP. Ваши собственные файлы не трогаются."), "")],
                         "back": [("", "")]}[it.key])
        if act.back or act.keys[0] == "back":
            continue
        with t.suspend():
            if act.keys[0] == "update":
                groups: dict[tuple, list[dict]] = defaultdict(list)
                for r in chosen:
                    groups[(r["target"], r["scope"], r["root"])].append(r)
                for (target, scope, root), rs in groups.items():
                    layout = _layout_of(target, scope, root)
                    args = Namespace(dry_run=False, yes=False, force=False, accept_risk=True,
                                     allow_draft=False, refresh=False, skill=None, skip_skill=None,
                                     optional=False, variant=None, interactive=True, accepted=set(),
                                     entry_options={r["id"]: r.get("options") or {} for r in rs})
                    try:
                        install_entries(cat, [r["id"] for r in rs if r["id"] in cat.entries], layout, args)
                    except (InstallError, FetchError) as ex:
                        print(ui.red(T(f"Error: {ex}", f"Ошибка: {ex}")))
            else:
                print(ui.bold(T("\nRemove:", "\nУдалить:")))
                for r in chosen:
                    print(f"  {r['id']} — {where_label(r)}")
                if ui.confirm(T("Remove the ticked items?", "Удалить отмеченное?"), False):
                    for r in chosen:
                        try:
                            uninstall(_layout_of(r["target"], r["scope"], r["root"]), r["id"])
                            print(ui.green(T(f"✓ {r['id']}: removed", f"✓ {r['id']}: удалено")))
                        except InstallError as ex:
                            print(ui.red(str(ex)))
            ui.ask(T("\nEnter — back", "\nEnter — назад"))


def _layout_of(target: str, scope: str, root: str) -> Layout:
    if scope == "global":
        return make_layout(target, global_=True)
    if scope == "folder":
        return make_layout("folder", dest=root)
    return make_layout(target, path=root)


# ---- catalog browser ------------------------------------------------------------------------------

def browse_screen(t: Term, cat: Catalog) -> list[str] | None:
    """Returns ids to install when the user asks for it from the browser."""
    cursor = None
    while True:
        installs = installs_by_entry()
        items = grouped(cat, cat.sorted(), installs)
        for it in items:
            it.disabled = None  # browsing: every entry can be opened
        res = select(t, items, title=T(f"Catalog: {len(cat.entries)} entries. {legend()}", f"Каталог: {len(cat.entries)} записей. {legend()}"), crumbs=T("Catalog", "Каталог"),
                     details=lambda it: entry_details(cat.get(it.key), installs), start=cursor,
                     hint=keys_hint(enter=T("actions", "действия")))
        if res.back:
            return None
        cursor = res.keys[0]
        e = cat.get(cursor)
        actions = [Item("details", T("Details", "Подробно"))]
        if e.installable and e.status != "draft":
            actions.insert(0, Item("install", T("Install or update…", "Установить или обновить…")))
        if e.origin == "user":
            actions += [Item("edit", T("Edit…", "Изменить…")), Item("drop", T("Remove from the catalog…", "Удалить из каталога…"), "", RED)]
        act = select(t, actions + [Item("back", T("Back", "Назад"))], title=e.name, crumbs=T("Catalog › ", "Каталог › ") + e.name,
                     details=lambda it: entry_details(e, installs))
        if act.back or act.keys[0] == "back":
            continue
        if act.keys[0] == "details":
            view(t, entry_details(e, installs, full=True), title=e.name, crumbs=T("Catalog › ", "Каталог › ") + e.name)
        elif act.keys[0] == "edit":
            ids = OwnForm(t, cat, editing=e).run()
            if ids:
                return ids
            cursor = cursor if cursor in cat.entries else None
        elif act.keys[0] == "drop":
            if drop_screen(t, cat, e):
                cursor = None
        else:
            return [e.id]



# ---- your own entries --------------------------------------------------------------------------

def source_help(kind: str) -> list[Line]:
    if kind == "local":
        return [(T("Folder or file on this computer", "Папка или файл на этом компьютере"), BOLD), ("", ""),
                (T("Your own skill (a folder with SKILL.md), a folder of skills, a rules file "
                   "(CLAUDE.md, AGENTS.md, any .md) or a project with .mcp.json.",
                   "Ваш собственный скилл (папка со SKILL.md), папка с несколькими скиллами, файл правил "
                   "(CLAUDE.md, AGENTS.md, любой .md) или проект с .mcp.json."), ""), ("", ""),
                (T("Files are taken straight from the folder. Change them and skillpantry shows "
                   "«↑ the folder changed», so installs can be updated.",
                   "Файлы берутся прямо из папки. Поменяете их — skillpantry покажет «↑ папка изменилась», "
                   "и установки можно обновить."), DIM)]
    return [(T("A GitHub repository", "Репозиторий на GitHub"), BOLD), ("", ""),
            (T("A link like https://github.com/owner/repo or just owner/repo. I pin the current "
               "commit, download it and show what is inside, with an audit.",
               "Ссылка вида https://github.com/owner/repo или просто owner/repo. Закреплю текущий "
               "коммит, скачаю и покажу, что внутри, и аудит."), ""), ("", ""),
            (T("The entry goes only into your own catalog. Until you tick «Reviewed by me» it is a "
               "draft and is not installed.",
               "Запись попадёт только в ваш каталог. Пока не отметите «Проверено мной», она черновик и "
               "не ставится."), DIM)]


RISK_TEXT = Labels({
    "low": ("low — text and instructions only", "низкий — только текст и инструкции"),
    "medium": ("medium — scripts, MCP, network requests", "средний — скрипты, MCP, сетевые запросы"),
    "high": ("high — may change the system; I will ask for confirmation on install",
             "высокий — может менять систему; при установке спрошу подтверждение")})


class OwnForm:
    """Add or edit your own entry: source → what is inside → form → save (→ install)."""

    def __init__(self, t: Term, cat: Catalog, editing: Entry | None = None):
        self.t = t
        self.cat = cat
        self.editing = editing
        self.new_category: dict | None = None
        self._audit: dict[tuple, object] = {}

    @property
    def crumbs(self) -> str:
        return T("Catalog › ", "Каталог › ") + self.editing.name + T(" › edit", " › изменить") if self.editing else T("Add your own", "Добавить своё")

    def busy(self, text: str) -> None:
        cols, rows = self.t.size()
        self.t.draw(["", f"  {text}"] + [""] * (rows - 2))

    def ask_source(self):
        from skillpantry import mine
        kind = "local"
        while True:
            res = select(self.t, [Item("local", T("Folder or file on this computer", "Папка или файл на компьютере")),
                                  Item("github", T("GitHub link", "Ссылка на GitHub"))],
                         title=T("Where from?", "Откуда взять?"), crumbs=self.crumbs, details=lambda it: source_help(it.key),
                         start=kind)
            if res.back:
                return None
            kind = res.keys[0]
            note = ([(T("Examples: ~/my-skills/go-review   ~/notes/rules.md   C:\\Users\\me\\skills", "Примеры: ~/my-skills/go-review   ~/notes/rules.md   C:\\Users\\me\\skills"), DIM),
                     (T("You can drag a folder into the terminal window — the path is pasted for you.", "Можно перетащить папку в окно терминала — путь вставится сам."), DIM)]
                    if kind == "local" else
                    [(T("Examples: https://github.com/owner/repo   owner/repo", "Примеры: https://github.com/owner/repo   owner/repo"), DIM)])
            text = ask_text(self.t, T("Path to a folder or file", "Путь к папке или файлу") if kind == "local" else T("Repository link", "Ссылка на репозиторий"),
                            title=T("Your own entry", "Своя запись"), crumbs=self.crumbs, note=note)
            if not text:
                continue
            self.busy(T("Downloading and inspecting the repository…", "Скачиваю и разбираю репозиторий…") if kind == "github" else T("Looking at the folder…", "Смотрю, что в папке…"))
            try:
                src = mine.inspect_source(text)
            except (CatalogError, FetchError, ValueError, OSError) as ex:
                message(self.t, [(str(ex), RED), ("", ""), (T("Esc — try again", "Esc — попробовать ещё раз"), DIM)],
                        title=T("That did not work", "Не получилось"), crumbs=self.crumbs)
                continue
            if src.kind != kind and kind == "github":
                pass  # a path typed on the GitHub screen still works
            return src

    def run(self) -> list[str] | None:
        """Returns [id] when the user chose «save and install»."""
        from skillpantry import mine
        e = self.editing
        if e:
            self.busy(T("Looking at the source…", "Смотрю, что в источнике…"))
            try:
                src = mine.inspect_source(e.local or e.repo, ref=None if e.local else e.ref, entry_id=e.id)
            except (CatalogError, FetchError, ValueError, OSError) as ex:
                message(self.t, [(T(f"Source unavailable: {ex}", f"Источник недоступен: {ex}"), RED), ("", ""),
                                 (T("If the folder was moved, remove the entry and add it again.", "Если папку перенесли — удалите запись и добавьте заново."), DIM)],
                        title=e.name, crumbs=self.crumbs)
                return None
            mine.mark_entry_components(src, e)
            f0 = mine.form_from_entry(e)
        else:
            src = self.ask_source()
            if src is None:
                return None
            name = src.name
            f0 = mine.Form(name=name, id=mine.unique_id(self.cat, mine.slug(name)), category="own",
                           summary=src.summary, description=src.description, tags=[],
                           risk="medium" if src.kind == "github" or any(c.kind == "mcp" and c.on
                                                                       for c in src.components) else "low",
                           work_safe=src.kind == "local", reviewed=src.kind == "local")
        self.src = src
        comps = [c.key for c in src.components if c.on]
        id_auto = not e

        F = {
            "name": Field("name", T("Name", "Название"), f0.name, help=[
                (T("How the entry is called in lists.", "Как запись будет называться в списках."), "")]),
            "id": Field("id", "id", f0.id, help=[
                (T("A short key for commands: skillpantry install <id>. Latin letters, digits, hyphens.", "Короткий ключ для команд: skillpantry install <id>. Латиница, цифры, дефис."), ""),
                (T("Follows the name until you change it yourself.", "Подставляется из названия, пока не поменяете сами."), DIM)]),
            "category": Field("category", T("Category", "Категория"), f0.category, "choice",
                              help=[(T("The area in the first wizard step. Pick an existing one " "or create a new one with its own description.", "Направление в первом шаге мастера. Можно выбрать существующее "
                                     "или создать новое со своим описанием."), "")],
                              show=self.show_category),
            "tags": Field("tags", T("Tags", "Теги"), list(f0.tags), "choice", placeholder="—",
                          help=[(T("Labels for search (/ in lists): existing or new.", "Метки для поиска (/ в списках): существующие или новые."), "")],
                          show=lambda v: ", ".join(v)),
            "summary": Field("summary", T("Summary", "Кратко"), f0.summary, placeholder=T("one line: what it is and why", "одна строка: что это и зачем"),
                             help=[(T("One line — shown next to the name in the wizard and the catalog.", "Одна строка — видна рядом с названием в мастере и каталоге."), "")]),
            "description": Field("description", T("Description", "Описание"), f0.description, placeholder=T("optional", "необязательно"),
                                 help=[(T("More detail: what it does, when to use it, what it needs. " "Shown when the entry has focus.", "Подробнее: что делает, когда брать, что нужно для работы. "
                                        "Показывается при фокусе на записи."), "")]),
            "components": Field("components", T("What to install", "Что ставить"), comps, "choice",
                                help=[(T("Found in the source — tick what to install.", "Найдено в источнике — отметьте, что ставить."), "")],
                                show=lambda v: self.show_components(v)),
            "risk": Field("risk", T("Risk", "Риск"), f0.risk, "choice", show=lambda v: RISK_LABELS.get(v, v),
                          help=[(T("How carefully to install. high — I will ask for confirmation on install.", "Насколько осторожно ставить. high — при установке спрошу подтверждение."), "")]
                          + [(f"• {RISK_TEXT[r]}", DIM) for r in RISKS]),
            "work_safe": Field("work_safe", T("Work-safe", "Для рабочих проектов"), f0.work_safe, "toggle",
                               help=[(T("Whether it can go into work (corporate) projects without " "a warning.", "Можно ли ставить в рабочие (корпоративные) проекты без "
                                      "предупреждения."), "")]),
            "reviewed": Field("reviewed", T("Reviewed by me", "Проверено мной"), f0.reviewed, "toggle",
                              hidden=src.kind == "local",
                              help=[(T("You have read the skills, rules and scripts from the repository. " "Without the tick the entry is a draft and is not installed.", "Вы прочитали скиллы, правила и скрипты из репозитория. "
                                     "Без отметки запись — черновик и не ставится."), "")]),
            "save_install": Field("save_install", T("Save and install…", "Сохранить и установить…"), kind="button",
                                  help=[(T("I will save the entry and open the install wizard with it.", "Сохраню запись и сразу открою мастер установки с ней."), "")]),
            "save": Field("save", T("Save", "Сохранить"), kind="button",
                          help=[(T("The entry appears in the catalog and the install wizard.", "Запись появится в каталоге и мастере установки."), "")]),
            "cancel": Field("cancel", T("Cancel", "Отмена"), kind="button", help=[(T("Save nothing.", "Ничего не сохранять."), "")]),
        }
        fields = list(F.values())
        state = {"id_auto": id_auto}

        def current() -> "mine.Form":
            return mine.Form(name=str(F["name"].value), id=str(F["id"].value).strip(),
                             category=str(F["category"].value), summary=str(F["summary"].value),
                             description=str(F["description"].value), tags=list(F["tags"].value),
                             risk=str(F["risk"].value), work_safe=bool(F["work_safe"].value),
                             reviewed=bool(F["reviewed"].value), components=list(F["components"].value),
                             new_category=self.new_category if F["category"].value == "+new" else None)

        def changed(f: Field) -> None:
            if f.key == "name" and state["id_auto"]:
                F["id"].value = mine.unique_id(self.cat, mine.slug(str(f.value)), e.id if e else None)
            elif f.key == "id":
                state["id_auto"] = False

        def pick(f: Field):
            if f.key == "category":
                return self.pick_category(f.value)
            if f.key == "tags":
                return self.pick_tags(f.value)
            if f.key == "components":
                return self.pick_components(f.value)
            if f.key == "risk":
                res = select(self.t, [Item(r, RISK_LABELS[r]) for r in RISKS], title=T("Risk", "Риск"),
                             crumbs=self.crumbs, start=f.value,
                             details=lambda it: [(RISK_TEXT[it.key], "")])
                return None if res.back else res.keys[0]
            return None

        def extra(f: Field) -> list[Line]:
            if f.key not in ("components", "save", "save_install", "reviewed", "risk"):
                return []
            return [("", "")] + self.source_lines(list(F["components"].value))

        while True:
            pressed = form(self.t, fields, title=(T("Edit: ", "Изменить: ") + e.name) if e else T("New entry of your own", "Новая своя запись"),
                           crumbs=self.crumbs, pick=pick, changed=changed, extra=extra,
                           check=lambda: mine.check_form(self.cat, src, current(), e))
            if pressed is None:
                return None
            try:
                path = mine.save(self.cat, src, current(), e)
            except (CatalogError, OSError) as ex:
                message(self.t, [(T(f"Not saved: {ex}", f"Не сохранилось: {ex}"), RED)], title=T("Error", "Ошибка"), crumbs=self.crumbs)
                continue
            saved = self.cat.get(str(F["id"].value).strip())
            if pressed == "save_install" and saved.status != "draft":
                return [saved.id]
            lines: list[Line] = [(f"✓ {saved.number}. {saved.name}", GREEN), ("", ""),
                                 (T(f"Entry file: {short_path(path, 80)}", f"Файл записи: {short_path(path, 80)}"), DIM), ("", "")]
            if saved.status == "draft":
                lines.append((T("This is a draft: tick «Reviewed by me» once you have read the content " "— then it can be installed.", "Это черновик: отметьте «Проверено мной», когда прочитаете содержимое, — "
                              "тогда запись можно будет поставить."), YELLOW))
            else:
                lines.append((T("Install: home → «Install or update» " f"(category «{self.cat.category(saved.category).title}»).", "Поставить: главное меню → «Установить или обновить» "
                              f"(категория «{self.cat.category(saved.category).title}»)."), ""))
            message(self.t, lines, title=T("Saved", "Сохранено"), crumbs=self.crumbs)
            return None

    # ---- pickers and texts ----
    def show_category(self, v) -> str:
        if v == "+new" and self.new_category:
            return T(f"{self.new_category['title']} (new)", f"{self.new_category['title']} (новая)")
        c = self.cat.categories.get(str(v))
        return c.title if c else T("— choose —", "— выберите —")

    def pick_category(self, value):
        from skillpantry import mine
        cats = sorted(self.cat.categories.values(), key=lambda c: (c.order, c.title))
        items = [Item(c.id, c.title, T("mine", "своя") if c.origin == "user" else "", CYAN, search=c.description)
                 for c in cats if c.id != "excluded"]
        items.append(Item("+new", T("+ New category…", "+ Новая категория…"), "", GREEN))
        counts = defaultdict(int)
        for en in self.cat.entries.values():
            counts[en.category] += 1

        def details(it: Item) -> list[Line]:
            if it.key == "+new":
                return [(T("Your own category", "Своя категория"), BOLD), (T("I will ask for a name and a description — it is shown " "when the category has focus in the wizard.", "Спрошу название и описание — оно показывается "
                                                    "при фокусе на категории в мастере."), "")]
            c = self.cat.category(it.key)
            return [(c.title, BOLD), (" ".join(c.description.split()), ""), ("", ""),
                    (T(f"Entries: {counts[c.id]}", f"Записей: {counts[c.id]}"), DIM)]

        res = select(self.t, items, title=T("Category", "Категория"), crumbs=self.crumbs + T(" › category", " › категория"),
                     details=details, start=value if value != "+new" else "+new")
        if res.back:
            return None
        key = res.keys[0]
        if key != "+new":
            self.new_category = None
            return key
        title = ask_text(self.t, T("Category name", "Название категории"), title=T("New category", "Новая категория"), crumbs=self.crumbs,
                         default=(self.new_category or {}).get("title", ""),
                         note=[(T("For example: «Go backend», «My rules», «Work».", "Например: «Бэкенд на Go», «Мои правила», «Работа»."), DIM)])
        if not title:
            return None
        desc = ask_text(self.t, T("Description: what goes into it (may be empty)", "Описание: что в неё входит (можно пусто)"), title=T(f"New category: {title}", f"Новая категория: {title}"),
                        crumbs=self.crumbs, default=(self.new_category or {}).get("description", ""),
                        note=[(T("Shown when the category has focus in the first wizard step.", "Показывается при фокусе на категории в первом шаге мастера."), DIM)])
        if desc is None:
            return None
        key_ = mine.slug(title, "my-category")
        n = 2
        base = key_
        while key_ in self.cat.categories:
            key_ = f"{base}-{n}"
            n += 1
        self.new_category = {"id": key_, "title": title, "description": desc}
        return "+new"

    def pick_tags(self, value):
        existing = self.cat.all_tags()
        current = list(value)
        pool = list(dict.fromkeys(current + existing))
        counts = defaultdict(int)
        for en in self.cat.entries.values():
            for tg in en.tags:
                counts[tg] += 1
        items = [Item("+new", T("+ New tags…", "+ Новые теги…"), "", GREEN)] + [
            Item(tg, tg, str(counts[tg]) if counts[tg] else T("new", "новый"), DIM) for tg in pool]

        def details(it: Item) -> list[Line]:
            if it.key == "+new":
                return [(T("Tick it with Space and press Enter — I will ask for new tags, comma-separated: " "go, backend, review", "Отметьте пробелом и нажмите Enter — спрошу новые теги через запятую: "
                         "go, backend, ревью"), "")]
            names = [en.name for en in self.cat.sorted() if it.key in en.tags]
            return [(f"#{it.key}", BOLD)] + [(f"• {n}", DIM) for n in names[:20]]

        res = select(self.t, items, title=T("Tags — tick the ones you need", "Теги — отметьте нужные"), crumbs=self.crumbs + T(" › tags", " › теги"),
                     multi=True, details=details, selected=current, empty_means_cursor=False,
                     allow_empty=True)
        if res.back:
            return None
        chosen = [k for k in res.keys if k != "+new"]
        if "+new" in res.keys:
            text = ask_text(self.t, T("New tags, comma-separated", "Новые теги через запятую"), title=T("Tags", "Теги"), crumbs=self.crumbs)
            if text:
                chosen += [x.strip().lower() for x in text.replace(";", ",").split(",") if x.strip()]
        return list(dict.fromkeys(chosen))

    def show_components(self, keys) -> str:
        chosen = self.src.chosen(list(keys))
        if not chosen:
            return T("— nothing —", "— ничего —")
        kinds = defaultdict(int)
        for c in chosen:
            kinds[c.kind] += 1
        names = {"skills": T("skills", "скиллы"), "rules": T("rules", "правила"), "mcp": "MCP", "commands": T("commands", "команды")}
        return ", ".join(f"{names[k]}" + (f" ×{n}" if n > 1 else "") for k, n in kinds.items())

    def pick_components(self, value):
        src = self.src
        if not src.components:
            message(self.t, [(T("No SKILL.md, rules file or .mcp.json in the source.", "В источнике не нашлось ни SKILL.md, ни файла правил, ни .mcp.json."), YELLOW),
                             ("", ""), (T("For a rules file, point at the file itself, e.g. ~/notes/rules.md.", "Для файла правил укажите сам файл, например ~/notes/rules.md."), DIM)],
                    title=T("Nothing to install", "Нечего ставить"), crumbs=self.crumbs)
            return None
        groups = (("skills", T("Skills", "Скиллы")), ("rules", T("Rules (one file)", "Правила (один файл)")), ("mcp", T("MCP servers", "MCP-серверы")),
                  ("commands", T("Slash commands (Claude Code)", "Slash-команды (Claude Code)")))
        items: list[Item] = []
        for kind, title in groups:
            part = [c for c in src.components if c.kind == kind]
            if part:
                items.append(Item(f"#{kind}", title, header=True))
                items += [Item(c.key, c.label) for c in part]

        def details(it: Item) -> list[Line]:
            c = next(c for c in src.components if c.key == it.key)
            p = src.snapshot / c.value if c.kind != "mcp" else None
            L: list[Line] = [(c.label, BOLD), ("", "")]
            if c.kind == "mcp":
                L += [(f"{k}: {v}", "") for k, v in (c.spec or {}).items()]
                L += [("", ""), (T("Secrets are not stored — only environment variable names.", "Секреты не сохраняются — только имена переменных окружения."), DIM)]
            elif p and p.is_file():
                L += [(ln, "") for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()[:60]]
            elif p and (p / "SKILL.md").is_file():
                L += [(ln, "") for ln in (p / "SKILL.md").read_text(encoding="utf-8",
                                                                    errors="replace").splitlines()[:60]]
            else:
                found = sorted(src.snapshot.glob(c.value))[:30]
                L += [(f"• {f.relative_to(src.snapshot).as_posix()}", "") for f in found]
            return L

        res = select(self.t, items, title=T("What to install from the source", "Что ставить из источника"), crumbs=self.crumbs + T(" › contents", " › состав"),
                     multi=True, details=details, selected=list(value), empty_means_cursor=False,
                     allow_empty=True)
        return None if res.back else res.keys

    def source_lines(self, keys: list[str]) -> list[Line]:
        src = self.src
        k = tuple(sorted(keys))
        if k not in self._audit:
            self._audit[k] = src.audit(keys)
        rep = self._audit[k]
        where = (T(f"Folder: {short_path(src.location, 70)}", f"Папка: {short_path(src.location, 70)}") if src.kind == "local"
                 else T(f"GitHub: {src.location} @ {(src.ref or '')[:12]} · license {src.license}", f"GitHub: {src.location} @ {(src.ref or '')[:12]} · лицензия {src.license}"))
        c = rep.counts()
        tone = RED if c["high"] else YELLOW if c["medium"] else GREEN
        L: list[Line] = [(T("Source", "Источник"), BOLD), (where, DIM),
                         (T(f"Audit: files {rep.files}, scripts {len(rep.scripts)}, findings " f"{c['high']}/{c['medium']}/{c['low']} (high/medium/low)", f"Аудит: файлов {rep.files}, скриптов {len(rep.scripts)}, находок "
                          f"{c['high']}/{c['medium']}/{c['low']} (высокие/средние/низкие)"), tone)]
        for f in sorted(rep.findings, key=lambda x: {"high": 0, "medium": 1, "low": 2}[x.severity])[:5]:
            L.append((f"! {f.code}: {f.file}:{f.line}", YELLOW if f.severity != "low" else DIM))
        if rep.hosts:
            L.append((T("Hosts: ", "Адреса: ") + ", ".join(h for h, _ in rep.hosts.most_common(4)), DIM))
        return L


def drop_screen(t: Term, cat: Catalog, e: Entry) -> bool:
    from skillpantry import mine
    recs = [r for r in installed(None) if r["id"] == e.id]
    lines: list[Line] = [(T(f"Entry file: {short_path(e.file, 80)}", f"Файл записи: {short_path(e.file, 80)}"), DIM),
                         (T(f"The source is not touched: {e.source_url}", f"Источник не трогаю: {e.source_url}"), DIM)]
    if recs:
        lines += [("", ""), (T(f"The entry is installed in {len(recs)} place(s) — the installs stay. " "Remove them in «Installed».", f"Запись установлена в {len(recs)} мест(а) — установки останутся. "
                             "Удалить их: «Установленное»."), YELLOW)]
    res = select(t, [Item("keep", T("Keep", "Оставить")), Item("drop", T("Remove from the catalog", "Удалить из каталога"), "", RED)],
                 title=T(f"Remove «{e.name}» from your catalog?", f"Удалить «{e.name}» из своего каталога?"), crumbs=T("Catalog › remove", "Каталог › удалить"),
                 details=lambda it: lines)
    if res.back or res.keys[0] != "drop":
        return False
    try:
        mine.delete(cat, e)
    except CatalogError as ex:
        message(t, [(str(ex), RED)], title=T("Error", "Ошибка"), crumbs=T("Catalog", "Каталог"))
        return False
    return True


class SyncWizard:
    """Keep planning, confirmation and results inside the full-screen interface."""

    def __init__(self, term: Term):
        self.t = term
        self.source_agent = "claude-code"
        self.target_agent = "codex"
        self.source = None
        self.destination = None
        self.chosen: list[str] = []

    def location(self, agent: str, source: bool):
        title = T("Source location", "Откуда читать скиллы") if source else T("Destination location", "Куда записать скиллы")
        items = []
        if not source and self.source.scope == "project":
            items.append(Item("same", T("Same project", "Тот же проект")))
        items += [Item("current", T("Current project", "Текущий проект")),
                  Item("other", T("Another project", "Другой проект")),
                  Item("global", T("Global skills", "Глобальные скиллы"))]
        while True:
            def layout(key):
                return make_layout(agent, global_=key == "global",
                                   path=str(self.source.root) if key == "same" else None)

            res = select(self.t, items, title=f"{title} — {TARGET_LABELS[agent]}",
                         crumbs=T("Sync › location", "Синхронизация › расположение"),
                         details=lambda it: [(T("Enter a project root, not the skills subfolder.",
                                                "Укажите корень проекта, а не подпапку skills."), DIM)]
                         if it.key == "other" else [(str(layout(it.key).skills_dir("")), "")])
            if res.back:
                return None
            key = res.keys[0]
            if key != "other":
                return layout(key)
            path = ask_text(self.t, T("Project root", "Корень проекта"), title=title)
            if not path:
                continue
            p = Path(path.strip().strip('"').strip("'")).expanduser()
            if p.is_dir():
                return make_layout(agent, path=str(p))
            message(self.t, [(T(f"No such folder: {p}", f"Нет такой папки: {p}"), RED)], title=title)

    def run(self) -> None:
        from skillpantry.sync import AGENTS, apply_sync, plan_sync

        step = 0
        while step >= 0:
            try:
                if step in (0, 1):
                    source = step == 0
                    res = select(self.t, [Item(a, TARGET_LABELS[a]) for a in AGENTS],
                                 title=T("Source agent", "Из какого ИИ перенести скиллы") if source else
                                       T("Destination agent", "В какой ИИ перенести скиллы"),
                                 crumbs=T("Sync › agents", "Синхронизация › агенты"),
                                 start=self.source_agent if source else self.target_agent)
                    if not res.back:
                        if source:
                            self.source_agent = res.keys[0]
                        else:
                            self.target_agent = res.keys[0]
                    step += -1 if res.back else 1
                elif step in (2, 3):
                    source = step == 2
                    result = self.location(self.source_agent if source else self.target_agent, source)
                    if result is not None:
                        if source:
                            self.source = result
                        else:
                            self.destination = result
                    step += -1 if result is None else 1
                elif step == 4:
                    plan = plan_sync(self.source, self.destination)
                    labels = {"new": T("new", "новый"), "unchanged": T("unchanged", "без изменений"),
                              "conflict": T("conflict", "конфликт")}

                    def details(it):
                        x = next(x for x in plan if x.name == it.key)
                        return [(str(x.source), DIM), ("→ " + str(x.destination), ""),
                                (labels[x.status], YELLOW if x.status == "conflict" else GREEN)] + [
                                    (warning, YELLOW) for warning in x.warnings]

                    res = select(self.t, [Item(x.name, x.name, labels[x.status],
                                              YELLOW if x.status == "conflict" else "") for x in plan],
                                 title=T("Choose skills", "Выберите скиллы"),
                                 crumbs=T("Sync › skills", "Синхронизация › скиллы"),
                                 multi=True, selected=self.chosen, details=details)
                    if not res.back:
                        self.chosen = res.keys
                        selected = [x for x in plan if x.name in self.chosen]
                    step += -1 if res.back else 1
                else:
                    conflicts = [x for x in selected if x.status == "conflict"]
                    lines = [(f"{self.source.label} → {self.destination.label}", BOLD), ("", "")]
                    for x in selected:
                        lines += [(f"{x.name}: {labels[x.status]} → {x.destination}",
                                   YELLOW if x.status == "conflict" else "")]
                        lines += [(warning, YELLOW) for warning in x.warnings]
                    lines += [("", ""), (T("Full skill folders are copied unchanged. Rules, MCP and hooks are not synchronized.",
                                             "Папки скиллов копируются целиком. Правила, MCP и хуки не синхронизируются."), DIM)]
                    if all(x.status == "unchanged" for x in selected):
                        message(self.t, lines, title=T("Already synchronized", "Уже синхронизировано"))
                        return
                    apply_label = T("Replace conflicts with backups and sync", "Заменить конфликты с резервной копией и перенести") if conflicts else T("Apply sync", "Применить синхронизацию")
                    res = select(self.t, [Item("back", T("Back to skills", "Назад к скиллам")),
                                          Item("apply", apply_label, "", YELLOW if conflicts else GREEN)],
                                 title=T("Review sync plan", "Проверьте план синхронизации"),
                                 crumbs=T("Sync › plan", "Синхронизация › план"), details=lambda it: lines)
                    if res.back or res.keys[0] == "back":
                        step = 4
                        continue
                    backups = apply_sync(selected, force=bool(conflicts))
                    result = [(T("Skills synchronized.", "Скиллы синхронизированы."), GREEN)]
                    if backups:
                        result.append((T("Backups: ", "Резервные копии: ") + str(backups), DIM))
                    message(self.t, result, title=T("Sync complete", "Синхронизация завершена"))
                    return
            except (OSError, ValueError, InstallError) as ex:
                message(self.t, [(str(ex), RED)], title=T("Sync error", "Ошибка синхронизации"))
                # A failed or stale plan must be rebuilt before it can be applied.
                step = 3


# ---- home ----------------------------------------------------------------------------------------

def run(cat: Catalog, term: Term | None = None) -> int:
    t = term or Term()
    try:
        with t:
            home(t, cat)
    except Quit:
        pass
    except KeyboardInterrupt:
        return 130
    return 0


def language_screen(t: Term) -> None:
    from skillpantry.config import set_setting
    items = [Item("en", "English"), Item("ru", "Русский"),
             Item("auto", T("As in the system", "Как в системе"))]
    res = select(t, items, title="Language / Язык", crumbs="Language / Язык", start=i18n.lang(),
                 details=lambda it: [({"en": "Interface and catalog in English.",
                                       "ru": "Интерфейс и каталог на русском.",
                                       "auto": T("Follow the system language.", "По языку системы.")}[it.key], "")])
    if res.back:
        return
    choice = res.keys[0]
    try:
        set_setting("language", "" if choice == "auto" else choice)
    except OSError as ex:
        message(t, [(str(ex), RED)], title="Language / Язык")
    i18n.set_lang(None if choice == "auto" else choice)


def home(t: Term, cat: Catalog) -> None:
    last = "install"
    while True:
        cat.load()  # own folders may have changed since the last screen
        own = cat.user_entries()
        recs = installed(None)
        stale = sum(1 for r in recs if r["id"] in cat.entries and cat.entries[r["id"]].ref
                    and r.get("ref") != cat.entries[r["id"]].ref)
        n_ok = sum(1 for e in cat.entries.values() if e.installable)
        items = [
            Item("install", T("Install or update", "Установить или обновить"), T(f"{n_ok} entries", f"{n_ok} записей")),
            Item("sync", T("Sync skills between agents", "Синхронизация скиллов между агентами")),
            Item("browse", T("Catalog", "Каталог"), f"{len(cat.entries)}"),
            Item("installed", T("Installed", "Установленное"), f"{len(recs)}" + (f" · ↑{stale}" if stale else ""),
                 YELLOW if stale else ""),
            Item("add", T("Add your own", "Добавить своё"), T(f"mine: {len(own)}", f"своих: {len(own)}") if own else "", CYAN),
            Item("doctor", T("Check the environment", "Проверить окружение")),
            Item("lang", "Language / Язык", i18n.NAMES[i18n.lang()], DIM),
            Item("quit", T("Quit", "Выход")),
        ]
        texts = {
            "install": [(T("Install wizard", "Мастер установки"), BOLD), ("", ""),
                        (T("1. Categories — the areas you need", "1. Категории — направления, которые нужны"), ""),
                        (T("2. Entries — skills, plugins, rules and MCP from those categories", "2. Записи — скиллы, плагины, правила и MCP из этих категорий"), ""),
                        (T("3. Target — Claude Code, Codex, Gemini CLI, Cursor or a folder", "3. Куда — Claude Code, Codex, Gemini CLI, Cursor или папка"), ""),
                        (T("4. Location — this project, another project, global", "4. Где — этот проект, другой проект, глобально"), ""),
                        (T("5. Options — risk confirmation, optional skills, a file to pick", "5. Параметры — подтверждение рисков, дополнительные скиллы, файл на выбор"), ""),
                        (T("6. Plan — I show the changes and the audit, then ask", "6. План — покажу изменения и аудит, спрошу подтверждение"), ""), ("", ""),
                        (T("Entries already installed are reinstalled from the catalog version.", "Уже установленные записи переустанавливаются с версии из каталога."), DIM)],
            "sync": [(T("Copy complete skill folders between agents. Choose source, destination and skills; review conflicts before applying.",
                         "Перенос полных папок скиллов между агентами. Выберите источник, назначение и скиллы; проверьте конфликты перед применением."), "")],
            "browse": [(T("The whole catalog by category", "Весь каталог по категориям"), BOLD), ("", ""),
                       (T("Description, risks, source and targets for every entry. " "You can install an entry right from here.", "Описание, риски, источник и куда ставится — у каждой записи. "
                        "Отсюда же можно сразу поставить запись."), "")],
            "installed": [(T("What, where and when was installed", "Что, куда и когда поставлено"), BOLD), ("", ""),
                          (T(f"Installs: {len(recs)}", f"Установок: {len(recs)}") + (T(f", can be updated: {stale}", f", обновить можно: {stale}") if stale else ""), ""),
                          (T("Update or remove the ticked ones here too.", "Здесь же — обновить или удалить отмеченное."), DIM)],
            "add": [(T("Your own entry in the catalog", "Своя запись в каталог"), BOLD), ("", ""),
                    (T("A folder on your computer (your own skill, rules, .mcp.json) or a GitHub link. " "I show what was found and a form: name, category (existing or new), tags, " "description, what to install, risk.", "Папка на компьютере (свой скилл, правила, .mcp.json) или ссылка на GitHub. "
                     "Покажу, что нашлось, и форму: название, категория (своя или новая), теги, "
                     "описание, что ставить, риск."), ""), ("", ""),
                    (T("Your entries are kept apart from the shared catalog and survive skillpantry " "updates. Edit or remove: Catalog → entry (★).", "Свои записи хранятся отдельно от общего каталога и не пропадают при обновлении "
                     "skillpantry. Изменить или удалить: Каталог → запись (★)."), DIM)]
                   + ([("", ""), (T("Your entries:", "Ваши записи:"), BOLD)] + [(f"★ {x.name} — {x.summary}", "") for x in own]
                      if own else []),
            "lang": [("Language / Язык", BOLD), ("", ""),
                     ("English or Russian. Saved in ~/.skillpantry/config.toml (language).", ""),
                     ("Английский или русский. Сохраняется в ~/.skillpantry/config.toml (language).", ""),
                     ("", ""), (T("Catalog descriptions switch too.", "Описания каталога тоже переключаются."), DIM)],
            "doctor": [(T("Environment check", "Проверка окружения"), BOLD), ("", ""),
                       (T("Git, Node, uv, installed agents, catalog and data paths.", "Git, Node, uv, установленные агенты, пути к каталогу и данным."), "")],
            "quit": [("", "")],
        }
        if cat.errors:
            texts["install"] = [(T(f"! Errors in manifests: {', '.join(cat.errors)} — skillpantry validate", f"! Ошибки в манифестах: {', '.join(cat.errors)} — skillpantry validate"), RED),
                                ("", "")] + texts["install"]
        res = select(t, items, title=T("What would you like to do?", "Что сделать?"), crumbs=T("Home", "Главная"), details=lambda it: texts[it.key],
                     start=last, right_enters=True,
                     hint=T("↑↓ move  Enter/→ open  ←/Esc/Q quit", "↑↓ выбор  Enter/→ открыть  ←/Esc/Q выход"))
        if res.back or res.keys[0] == "quit":
            return
        last = res.keys[0]
        if last == "install":
            Wizard(t, cat).run()
        elif last == "sync":
            SyncWizard(t).run()
        elif last == "browse":
            ids = browse_screen(t, cat)
            if ids:
                Wizard(t, cat, preselect=ids).run()
        elif last == "installed":
            installed_screen(t, cat)
        elif last == "add":
            ids = OwnForm(t, cat).run()
            if ids:
                Wizard(t, cat, preselect=ids).run()
        elif last == "lang":
            language_screen(t)
        elif last == "doctor":
            from skillpantry.cli import cmd_doctor
            with t.suspend():
                cmd_doctor(cat, None)
                ui.ask(T("\nEnter — back", "\nEnter — назад"))
