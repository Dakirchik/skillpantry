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


@pytest.mark.parametrize("conflict,apply", [(False, True), (True, True), (True, False)])
def test_sync_wizard_stays_in_ui(tmp_path, monkeypatch, conflict, apply):
    from skillpantry.app import SyncWizard

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SKILLPANTRY_HOME", str(tmp_path / "data"))
    source = tmp_path / ".claude" / "skills" / "demo"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: demo\n---\nNew content\n", encoding="utf-8")
    (source / "helper.py").write_text("print('ok')", encoding="utf-8")
    destination = tmp_path / ".agents" / "skills" / "demo"
    if conflict:
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text("Old content", encoding="utf-8")

    def no_prompt(*args, **kwargs):
        pytest.fail("Full-screen sync must not use terminal prompts or suspend the UI")

    monkeypatch.setattr("builtins.input", no_prompt)
    monkeypatch.setattr(Term, "suspend", no_prompt)
    # Default agents, current source project, same destination project, select demo.
    keys = ["enter"] * 5
    keys += ["down", "enter", "enter"] if apply else ["esc"] * 6
    t = term(keys)
    SyncWizard(t).run()
    output = strip_ansi(t.out.getvalue())
    assert "demo" in output
    if apply:
        assert (destination / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()
        assert (destination / "helper.py").is_file()
        assert "Скиллы синхронизированы" in output
        if conflict:
            backups = list((tmp_path / "data" / "backups").glob("sync-*/original-0-demo/SKILL.md"))
            assert len(backups) == 1
            assert backups[0].read_text(encoding="utf-8") == "Old content"
    else:
        assert (destination / "SKILL.md").read_text(encoding="utf-8") == "Old content"
        assert not (tmp_path / "data").exists()


def test_width_and_fit():
    assert width("Дакир") == 5 and width("日本") == 4
    assert fit("Привет мир", 6) == "Приве…"
    assert width(fit("abc", 5)) == 5


def test_home_opens_catalog_by_label(env, monkeypatch):
    from skillpantry.app import run
    from skillpantry.catalog import Catalog

    def no_prompt(*args, **kwargs):
        pytest.fail("Catalog navigation must not enter a terminal prompt")

    monkeypatch.setattr("builtins.input", no_prompt)
    t = term(["/", *"Каталог", "enter", "enter", "down", "q"])
    assert run(Catalog(), t) == 0
    frames = [strip_ansi(frame) for frame in t.out.getvalue().split("\x1b[H")]
    assert any("Каталог" in frame and "Kit" in frame for frame in frames)


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
