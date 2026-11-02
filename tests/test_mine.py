"""Your own entries: local folders and GitHub repos in a personal catalog, via API, CLI and the form."""

import io
import tomllib

import pytest

from conftest import snapshot_tree
from skillpantry import mine
from skillpantry.catalog import Catalog, CatalogError
from skillpantry.cli import main
from skillpantry.tui import Term, strip_ansi


@pytest.fixture
def own(env, tmp_path):
    skill = tmp_path / "my-skills" / "go-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: go-review\ndescription: Ревью Go-кода. Проверяет ошибки.\n---\n# Go\n",
                                    encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "team-rules.md").write_text("# Правила команды\nПиши тесты.\n", encoding="utf-8")
    proj = tmp_path / "proj"
    proj.mkdir()
    return {**env, "skill": skill, "rules": tmp_path / "notes" / "team-rules.md", "proj": proj}


def form_for(src, **over):
    data = dict(name=src.name, id=mine.slug(src.name), category="own", summary=src.summary or "s",
                description=src.description)
    data.update(over)
    return mine.Form(**data)


def test_local_folder_entry_installs_tracks_changes_and_uninstalls(own):
    cat = Catalog()
    src = mine.inspect_source(str(own["skill"]))
    assert src.kind == "local" and src.name == "go-review"
    assert [c.key for c in src.components if c.on] == ["skills:."]
    assert src.summary == "Ревью Go-кода."
    path = mine.save(cat, src, form_for(src, tags=["go", "Ревью"]))
    assert path.parent == own["tmp"] / "ss" / "catalog"           # user catalog, not the shared one
    e = cat.get("go-review")
    assert (e.origin, e.status, e.number, e.tags) == ("user", "verified", 101, ["go", "ревью"])
    assert cat.errors == {}

    before = snapshot_tree(own["proj"])
    assert main(["install", "go-review", "-t", "claude-code", "-p", str(own["proj"]), "-y"]) == 0
    assert (own["proj"] / ".claude" / "skills" / "go-review" / "SKILL.md").exists()
    old_ref = Catalog().get("go-review").ref
    (own["skill"] / "SKILL.md").write_text("---\nname: go-review\n---\nnew\n", encoding="utf-8")
    assert Catalog().get("go-review").ref != old_ref               # folder changed → "↑"
    assert main(["install", "go-review", "-t", "claude-code", "-p", str(own["proj"]), "-y"]) == 0
    assert "new" in (own["proj"] / ".claude" / "skills" / "go-review" / "SKILL.md").read_text(encoding="utf-8")
    assert main(["uninstall", "go-review", "-t", "claude-code", "-p", str(own["proj"])]) == 0
    assert snapshot_tree(own["proj"]) == before


def test_rules_file_new_category_and_tags(own):
    cat = Catalog()
    src = mine.inspect_source(str(own["rules"]))
    assert [(c.kind, c.value, c.on) for c in src.components] == [("rules", "team-rules.md", True)]
    f = form_for(src, name="Правила команды", id="", category="", tags=["team"],
                 new_category={"id": "work", "title": "Работа", "description": "Всё для работы"})
    assert "id" in mine.check_form(cat, src, f)
    f.id = mine.slug(f.name)
    assert f.id == "pravila-komandy"
    mine.save(cat, src, f)
    assert cat.category("work").title == "Работа" and cat.category("work").origin == "user"
    assert cat.get("pravila-komandy").category == "work"
    assert "work" not in Catalog(user_dir=False).categories        # shared catalog untouched
    assert main(["install", "pravila-komandy", "-t", "codex", "-p", str(own["proj"]), "-y"]) == 0
    assert "Пиши тесты." in (own["proj"] / "AGENTS.md").read_text(encoding="utf-8")


def test_form_checks(own):
    cat = Catalog()
    src = mine.inspect_source(str(own["skill"]))
    errs = mine.check_form(cat, src, form_for(src, id="kit", summary=" ", components=[]))
    assert set(errs) == {"id", "summary", "components"}           # kit is taken by the shared catalog
    errs = mine.check_form(cat, src, form_for(src, category="nope"))
    assert set(errs) == {"category"}


def test_github_entry_is_draft_until_reviewed(own):
    assert main(["add", "acme/kit", "--mine", "--id", "my-kit", "--tags", "x,y"]) == 0
    e = Catalog().get("my-kit")
    assert (e.origin, e.status, e.repo, e.ref) == ("user", "draft", "acme/kit", own["sha"])
    assert {s.path for s in e.skills} == {"skills/*"}
    assert main(["install", "my-kit", "-t", "claude-code", "-p", str(own["proj"]), "-y"]) == 1
    assert main(["approve", "my-kit", "-y"]) == 0
    assert Catalog().get("my-kit").status == "verified"
    assert main(["install", "my-kit", "-t", "claude-code", "-p", str(own["proj"]), "-y"]) == 0
    assert (own["proj"] / ".claude" / "skills" / "alpha" / "SKILL.md").exists()


def test_cli_add_folder_and_drop(own, capsys):
    assert main(["add", str(own["skill"]), "--category", "backend", "--category-title", "Бэкенд"]) == 0
    cat = Catalog()
    assert cat.get("go-review").category == "backend" and cat.category("backend").title == "Бэкенд"
    assert main(["drop", "kit", "-y"]) == 1                       # shared entries are not dropped
    assert main(["drop", "go-review", "-y"]) == 0
    assert "go-review" not in Catalog().entries
    assert own["skill"].is_dir()                                  # the source folder stays


def test_toml_writer_round_trips_awkward_text():
    data = {"id": "x", "name": 'Кавычки " и \\ слэш', "description": 'Строка\nвторая """ тройные\nC:\\Users\\Я',
            "tags": ["a", "б"], "source": {"path": "C:\\Users\\Я\\skills", "license": "own"},
            "skills": [{"path": ".", "exclude": ["README.md"]}],
            "mcp": {"srv": {"command": "npx", "args": ["-y", "pkg"], "env": {"API-KEY": ""}}}}
    back = tomllib.loads(mine.dump_toml(data, comment="тест"))
    assert back == data


def test_ids_from_cyrillic_and_unique():
    assert mine.slug("Мои правила для Go!") == "moi-pravila-dlya-go"
    assert mine.slug("!!!") == "my-entry"


def test_user_entry_cannot_shadow_shared_id(own):
    d = own["tmp"] / "ss" / "catalog"
    d.mkdir(parents=True)
    (d / "kit.toml").write_text('id = "kit"\nnumber = 101\nname = "K"\ncategory = "own"\nsummary = "s"\n'
                                'status = "reference"\nrisk = "low"\n', encoding="utf-8")
    cat = Catalog()
    assert cat.get("kit").origin == "main"
    assert any("уже есть" in m for m in cat.errors["kit"])


def test_missing_local_folder_is_reported(own):
    cat = Catalog()
    src = mine.inspect_source(str(own["skill"]))
    mine.save(cat, src, form_for(src))
    import shutil
    shutil.rmtree(own["skill"])
    cat = Catalog()
    assert any("не найдена" in m for m in cat.errors["go-review"])
    with pytest.raises(CatalogError):
        mine.inspect_source(str(own["skill"]))


def test_form_in_the_full_screen_ui(own, monkeypatch):
    from skillpantry.app import run
    cat = Catalog()
    keys = (["down", "down", "down", "enter",                # home → Добавить своё
             "enter"] + list(str(own["skill"])) + ["enter"]  # source: folder, path
            + ["backspace"] * 9 + list("Ревью Go")            # name; id follows it
            + ["down", "down", "enter", "end", "enter"]      # category → + Новая категория
            + list("Бэкенд") + ["enter"] + list("Go") + ["enter"]
            + ["down", "enter", "space", "enter"] + list("go, ревью") + ["enter"]  # tags: + Новые
            + ["down"] * 7 + ["enter"]                         # → [ Сохранить ]
            + ["esc", "q"])
    t = Term(keys=keys, out=io.StringIO(), size=(110, 30))
    assert run(cat, t) == 0
    e = Catalog().get("revyu-go")
    assert (e.name, e.category, e.tags) == ("Ревью Go", "bekend", ["go", "ревью"])
    assert Catalog().category("bekend").description == "Go"
    frames = strip_ansi(t.out.getvalue())
    assert "Сохранено" in frames and "Новая своя запись" in frames
