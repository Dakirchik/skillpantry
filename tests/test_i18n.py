"""English and Russian: language choice, both interface languages, English catalog texts."""

import io
import re
import tomllib
from pathlib import Path

import pytest

from skillpantry import i18n
from skillpantry.catalog import Catalog
from skillpantry.cli import main
from skillpantry.tui import Term, strip_ansi

BUNDLED = Path(__file__).resolve().parents[1] / "skillpantry" / "catalog"
CYR = re.compile("[А-Яа-яЁё]")


@pytest.fixture
def english(monkeypatch):
    monkeypatch.setenv("SKILLPANTRY_LANG", "en")
    i18n.set_lang()


def test_every_bundled_entry_and_category_has_english():
    for f in sorted(BUNDLED.glob("*.toml")):
        data = tomllib.loads(f.read_text(encoding="utf-8"))
        if f.name == "_categories.toml":
            for key, spec in data.items():
                assert spec.get("title_en") and spec.get("description_en"), key
            continue
        assert data.get("summary_en") and data.get("description_en"), f.stem
        assert len(data.get("risk_notes_en", [])) == len(data.get("risk_notes", [])), f.stem
        manual = data.get("manual", {})
        if CYR.search(str(manual)):
            en = data.get("manual_en", {})
            assert {k: len(v) for k, v in en.items()} == {k: len(v) for k, v in manual.items()}, f.stem
        for text in (data["summary_en"], data["description_en"], *data.get("risk_notes_en", []),
                     *[s for v in data.get("manual_en", {}).values() for s in v]):
            assert not CYR.search(text.replace("modes/ru/", "")), (f.stem, text)


def test_texts_follow_the_language(english):
    cat = Catalog(BUNDLED, user_dir=False)
    e = cat.get("context7")
    assert e.summary.startswith("Up-to-date") and not CYR.search(e.description)
    assert cat.category("dev-process").title == "Development and process"
    i18n.set_lang("ru")
    assert CYR.search(e.summary) and cat.category("dev-process").title == "Разработка и процесс"
    # search finds an entry in either language
    assert cat.get("context7") in cat.search("документация") and cat.get("context7") in cat.search("version-specific")


def test_priority_flag_env_config_system(monkeypatch, env):
    from skillpantry.config import set_setting
    monkeypatch.delenv("SKILLPANTRY_LANG")
    monkeypatch.setattr(i18n, "_system_lang", lambda: "ru")   # Windows asks the OS, not LANG
    assert i18n.set_lang() == "ru"                      # system
    set_setting("language", "en")
    assert i18n.set_lang() == "en"                      # config
    monkeypatch.setenv("SKILLPANTRY_LANG", "ru")
    assert i18n.set_lang() == "ru"                      # environment
    assert i18n.set_lang("en") == "en"                  # flag


def test_lang_command_writes_config_and_keeps_other_lines(env, monkeypatch, capsys):
    from skillpantry.config import config_file
    monkeypatch.delenv("SKILLPANTRY_LANG")
    f = config_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text('# my notes\nlibrary = "/x"\n\n[extra]\nk = 1\n', encoding="utf-8")
    assert main(["lang", "en"]) == 0
    text = f.read_text(encoding="utf-8")
    assert text.startswith('# my notes\nlibrary = "/x"\n') and 'language = "en"' in text
    assert tomllib.loads(text) == {"library": "/x", "language": "en", "extra": {"k": 1}}
    assert main(["lang", "ru"]) == 0
    assert tomllib.loads(f.read_text(encoding="utf-8"))["language"] == "ru"
    assert "Язык: Русский" in capsys.readouterr().out


def test_cli_help_in_english(capsys):
    with pytest.raises(SystemExit):
        main(["--lang", "en", "--help"])
    out = capsys.readouterr().out
    assert "install entries into the chosen AI or a folder" in out and not CYR.search(out)


def test_english_install_output(env, english, tmp_path, capsys):
    proj = tmp_path / "p"
    proj.mkdir()
    assert main(["install", "kit", "-t", "codex", "-p", str(proj), "-y", "--variant", "one"]) == 0
    out = capsys.readouterr().out
    assert "✓ kit: installed" in out and not CYR.search(out)


def test_language_switch_in_the_full_screen_ui(env, english, monkeypatch):
    from skillpantry.app import run
    keys = ["end", "up", "enter",          # home → Language / Язык
            "down", "enter",               # Русский
            "q"]
    t = Term(keys=keys, out=io.StringIO(), size=(110, 30))
    assert run(Catalog(), t) == 0
    frames = [strip_ansi(f) for f in t.out.getvalue().split("\x1b[H")]
    assert any("What would you like to do?" in f for f in frames)
    assert "Что сделать?" in frames[-1]
    from skillpantry.config import settings
    assert settings()["language"] == "ru"
    english_home = next(f for f in frames if "What would you like to do?" in f)
    leftover = set(CYR.findall(english_home.replace("Language / Язык", "").replace("Русский", "")))
    assert not leftover
