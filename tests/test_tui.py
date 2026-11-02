"""Widgets and the install wizard driven by scripted keys (no real terminal)."""

import io

import pytest

from skillpantry.tui import Item, Quit, Term, fit, select, strip_ansi, width


def term(keys, size=(110, 30)):
    return Term(keys=keys, out=io.StringIO(), size=size)


def last_frame(t: Term) -> str:
    frames = t.out.getvalue().split("\x1b[H")
    return strip_ansi(frames[-1]).replace("\r\n", "\n")


ITEMS = [Item("#g", "Group", header=True), Item("a", "Alpha"), Item("b", "Beta"),
         Item("c", "Gamma", disabled="nope"), Item("d", "Delta")]


def test_width_and_fit():
    assert width("Дакир") == 5 and width("日本") == 4
    assert fit("Привет мир", 6) == "Приве…"
    assert width(fit("abc", 5)) == 5


def test_checkboxes_skip_headers_and_disabled():
    t = term(["space", "space", "space", "enter"])  # tick Alpha, Beta; Gamma is disabled
    res = select(t, ITEMS, multi=True)
    assert res.keys == ["a", "b"]


def test_enter_without_ticks_takes_cursor_row():
    assert select(term(["down", "enter"]), ITEMS, multi=True).keys == ["b"]


def test_select_all_and_back():
    assert select(term(["a", "enter"]), ITEMS, multi=True).keys == ["a", "b", "d"]
    assert select(term(["esc"]), ITEMS, multi=True).back


def test_filter_typing_cyrillic():
    items = [Item("x", "Дизайн"), Item("y", "Код")]
    res = select(term(["/", "К", "о", "enter", "enter"]), items)
    assert res.keys == ["y"]


def test_quit_key():
    with pytest.raises(Quit):
        select(term(["q"]), ITEMS)


def test_details_follow_cursor_two_columns():
    t = term(["down", "esc"])
    select(t, ITEMS, details=lambda it: [(f"about {it.label}", "")])
    assert "about Beta" in last_frame(t)


def test_details_below_on_narrow_terminal():
    t = term(["esc"], size=(70, 24))
    select(t, ITEMS, details=lambda it: [(f"about {it.label}", "")])
    frame = last_frame(t)
    assert "────" in frame and "about Alpha" in frame


def test_wizard_installs_and_reinstall_updates(env, monkeypatch, tmp_path):
    from skillpantry import ui
    from skillpantry.app import Wizard
    from skillpantry.catalog import Catalog

    proj = tmp_path / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)
    answers = iter(["д", "", "д", ""])
    monkeypatch.setattr(ui, "ask", lambda *a, **k: next(answers))
    monkeypatch.setattr("builtins.input", lambda *a: next(answers))
    cat = Catalog()
    # categories: Enter takes the row under the cursor; entries: tick kit; target: Codex;
    # where: this project; options: none; variant: pick "one"
    keys = ["enter", "space", "enter", "down", "enter", "enter", "down", "enter"]
    t = term(keys)
    t.start()
    Wizard(t, cat).run()
    assert (proj / ".agents" / "skills" / "alpha" / "SKILL.md").exists()
    assert (proj / "DESIGN.md").read_text(encoding="utf-8") == "design one\n"
    # the remembered variant is reused on reinstall
    from skillpantry.installer import load_state
    rec = next(iter(load_state()["installs"].values()))
    assert rec["options"]["variant"] == "one"


def test_arrows_and_backspace_navigate():
    t = term(["right", "left", "backspace"])
    res = select(t, ITEMS, details=lambda it: [(f"about {it.label}", "")])
    assert res.back                                    # → opened details, ← closed them, Backspace went back
    assert "about Alpha" in strip_ansi(t.out.getvalue())
    frame = last_frame(t)
    assert "→ подробно" in frame and "Backspace" in frame
    assert select(term(["right"]), ITEMS, right_enters=True).keys == ["a"]
