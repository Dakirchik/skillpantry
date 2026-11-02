import json
import tomllib
from pathlib import Path

import pytest

from conftest import snapshot_tree
from skillpantry.audit import audit_paths
from skillpantry.catalog import Catalog, parse_entry, set_field, validate_entry
from skillpantry.cli import main, make_layout
from skillpantry.installer import _replace_block, execute, plan_install
from skillpantry.fetch import fetch
from skillpantry.targets import mcp_codex_toml, mcp_json

BUNDLED = Path(__file__).resolve().parents[1] / "skillpantry" / "catalog"


# ---- bundled catalog ---------------------------------------------------------------

def test_bundled_catalog_is_valid():
    cat = Catalog(BUNDLED, user_dir=False)
    assert cat.errors == {}
    assert len(cat.entries) == 44
    assert sorted(e.number for e in cat.entries.values()) == list(range(1, 45))


def test_bundled_catalog_pins_full_shas_and_marks_high_risk():
    for e in Catalog(BUNDLED, user_dir=False).entries.values():
        if e.status in ("verified", "draft"):
            assert e.ref and len(e.ref) == 40
        if e.risk == "high":
            assert e.confirm, e.id
    assert Catalog(BUNDLED, user_dir=False).get("watermarks-remover").status == "excluded"


# ---- validation ----------------------------------------------------------------------

def _entry(tmp_path, **over):
    data = {"id": "x", "number": 1, "name": "X", "category": "c", "summary": "s",
            "status": "verified", "risk": "low", "reviewed": "2026-01-01",
            "source": {"repo": "a/b", "ref": "a" * 40, "license": "MIT"},
            "skills": [{"path": "skills/x"}]}
    data.update(over)
    return parse_entry(data, tmp_path / "x.toml")


def test_validation_catches_common_mistakes(tmp_path):
    assert validate_entry(_entry(tmp_path)) == []
    assert any("SHA" in e for e in validate_entry(_entry(tmp_path, source={"repo": "a/b", "ref": "main", "license": "MIT"})))
    assert any("небезопасный" in e for e in validate_entry(_entry(tmp_path, skills=[{"path": "../etc"}])))
    assert any("confirm" in e for e in validate_entry(_entry(tmp_path, risk="high")))
    assert any("секрет" in e for e in validate_entry(_entry(tmp_path, mcp={"m": {"command": "x", "env": {"API_KEY": "sk-123"}}})))
    assert any("TODO" in e for e in validate_entry(_entry(tmp_path, summary="TODO: fill")))


def test_set_field_keeps_layout(tmp_path):
    f = tmp_path / "x.toml"
    f.write_text('id = "x"\nstatus = "draft"  # note\n\n[source]\nref = "old"\n', encoding="utf-8")
    set_field(f, "status", "verified")
    set_field(f, "ref", "new", section="source")
    assert f.read_text(encoding="utf-8") == 'id = "x"\nstatus = "verified"  # note\n\n[source]\nref = "new"\n'


# ---- formats ---------------------------------------------------------------------------

def test_mcp_formats_never_inline_secrets(env):
    e = Catalog().get("kit")
    docs, remote = e.mcp
    assert mcp_json(docs, "claude")["env"] == {"DOCS_TOKEN": "${DOCS_TOKEN}", "MODE": "fast"}
    assert mcp_json(docs, "cursor")["env"]["DOCS_TOKEN"] == "${env:DOCS_TOKEN}"
    assert mcp_json(remote, "gemini") == {"httpUrl": "https://example.com/mcp",
                                          "headers": {"x-api-key": "${REMOTE_KEY}"}}
    parsed = tomllib.loads(mcp_codex_toml(docs) + mcp_codex_toml(remote))
    assert parsed["mcp_servers"]["docs"]["env_vars"] == ["DOCS_TOKEN"]
    assert parsed["mcp_servers"]["remote"]["env_http_headers"] == {"x-api-key": "REMOTE_KEY"}


def test_managed_block_is_idempotent():
    begin, end = "<!-- >>> skillpantry:a -->", "<!-- <<< skillpantry:a -->"
    once = _replace_block("# Mine\n", begin, end, "rules v1")
    twice = _replace_block(once, begin, end, "rules v2")
    assert twice.count(begin) == 1 and "rules v2" in twice and "rules v1" not in twice
    assert _replace_block(twice, begin, end, None) == "# Mine\n"


# ---- install / uninstall roundtrip ---------------------------------------------------------

@pytest.mark.parametrize("target", ["claude-code", "codex", "gemini", "cursor"])
def test_install_uninstall_restores_project(env, target):
    proj = env["tmp"] / "proj"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("# Mine\n", encoding="utf-8")
    (proj / "AGENTS.md").write_text("# Mine\n", encoding="utf-8")
    before = snapshot_tree(proj)
    assert main(["install", "kit", "-t", target, "-p", str(proj), "-y", "--variant", "one"]) == 0
    after = snapshot_tree(proj)
    assert after != before
    skills_dir = {"claude-code": ".claude/skills", "codex": ".agents/skills",
                  "gemini": ".gemini/skills", "cursor": ".cursor/skills"}[target]
    assert f"{skills_dir}/alpha/SKILL.md" in after and f"{skills_dir}/beta/SKILL.md" in after
    assert f"{skills_dir}/gamma/SKILL.md" in after  # repo-root glob; folder named from SKILL.md
    assert (proj / "DESIGN.md").read_text(encoding="utf-8") == "design one\n"
    # reinstall is idempotent
    assert main(["install", "kit", "-t", target, "-p", str(proj), "-y", "--variant", "one"]) == 0
    assert snapshot_tree(proj) == after
    assert main(["uninstall", "kit", "-t", target, "-p", str(proj)]) == 0
    assert snapshot_tree(proj) == before


def test_claude_specific_outputs(env):
    proj = env["tmp"] / "p"
    proj.mkdir()
    assert main(["install", "kit", "-t", "claude", "-p", str(proj), "-y"]) == 0
    mcp = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp["mcpServers"]["remote"]["type"] == "http"
    assert (proj / ".claude" / "commands" / "go.md").exists()
    text = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "# Kit" in text and "# CLAUDE.md" not in text


def test_conflict_blocks_then_force_backs_up(env):
    proj = env["tmp"] / "p"
    (proj / ".claude" / "skills" / "alpha").mkdir(parents=True)
    (proj / ".claude" / "skills" / "alpha" / "SKILL.md").write_text("mine", encoding="utf-8")
    assert main(["install", "kit", "-t", "claude-code", "-p", str(proj), "-y"]) == 2
    assert (proj / ".claude" / "skills" / "alpha" / "SKILL.md").read_text(encoding="utf-8") == "mine"
    assert main(["install", "kit", "-t", "claude-code", "-p", str(proj), "-y", "--force"]) == 0
    backups = list((env["tmp"] / "ss" / "backups").rglob("SKILL.md"))
    assert any(b.read_text(encoding="utf-8") == "mine" for b in backups)


def test_global_claude_mcp_is_manual_and_home_untouched_outside_claude(env):
    assert main(["install", "kit", "-t", "claude-code", "-g", "-y"]) == 0
    home = env["home"]
    assert (home / ".claude" / "skills" / "alpha" / "SKILL.md").exists()
    assert not (home / ".claude.json").exists()


def test_folder_target_writes_readme(env):
    lib = env["tmp"] / "lib"
    assert main(["install", "kit", "-t", "folder", "-d", str(lib), "-y", "--variant", "one"]) == 0
    assert (lib / "kit" / "skills" / "alpha" / "helper.md").exists()
    assert (lib / "kit" / "RULES.md").exists()
    assert "Коммит" in (lib / "kit" / "SKILLPANTRY.md").read_text(encoding="utf-8")
    assert main(["uninstall", "kit", "-t", "folder", "-d", str(lib)]) == 0
    assert not (lib / "kit").exists()


def test_skill_filter(env):
    e = Catalog().get("kit")
    snap = fetch(e)
    plan = plan_install(e, make_layout("codex", path=str(env["tmp"])), snap, only_skills={"beta"})
    assert [a.path.name for a in plan.actions if a.kind == "dir"] == ["beta"]


def test_fetch_is_pinned(env):
    e = Catalog().get("kit")
    snap = fetch(e)
    assert (snap / ".skillpantry-ok").read_text(encoding="utf-8") == env["sha"]


def test_audit_flags_pipe_to_shell(env):
    e = Catalog().get("kit")
    snap = fetch(e)
    rep = audit_paths([snap], snap)
    assert any(f.code == "pipe-to-shell" for f in rep.findings)


def test_add_drafts_manifest(env):
    assert main(["add", "acme/kit", "--id", "kit2"]) == 0
    text = (env["catalog"] / "kit2.toml").read_text(encoding="utf-8")
    assert 'status = "draft"' in text and 'path = "skills/*"' in text and 'license = "MIT"' in text
    assert main(["validate"]) == 1  # TODO fields must be filled before approval


def test_export_lists_every_entry():
    from skillpantry.export import render
    cat = Catalog(BUNDLED, user_dir=False)
    md = render(cat)
    for e in cat.entries.values():
        assert f"### {e.number}. {e.name}" in md


def test_glob_without_matches_is_an_error(env, tmp_path):
    from skillpantry.installer import InstallError, expand_skills
    e = Catalog().get("kit")
    snap = fetch(e)
    e.skills[0].path = "nothing-*"
    with pytest.raises(InstallError):
        expand_skills(e, snap, "codex", None, False)


def test_line_endings_are_preserved(env):
    proj = env["tmp"] / "crlf"
    proj.mkdir()
    (proj / "CLAUDE.md").write_bytes(b"# Mine\r\nline two\r\n")
    assert main(["install", "kit", "-t", "claude-code", "-p", str(proj), "-y"]) == 0
    raw = (proj / "CLAUDE.md").read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert main(["uninstall", "kit", "-t", "claude-code", "-p", str(proj)]) == 0
    assert (proj / "CLAUDE.md").read_bytes() == b"# Mine\r\nline two\r\n"


def test_set_field_keeps_crlf(tmp_path):
    f = tmp_path / "x.toml"
    f.write_bytes(b'id = "x"\r\nstatus = "draft"\r\n')
    set_field(f, "status", "verified")
    assert f.read_bytes() == b'id = "x"\r\nstatus = "verified"\r\n'


def test_config_toml_sets_library(env, monkeypatch):
    from skillpantry.cli import default_library
    data = env["tmp"] / "ss"
    data.mkdir(exist_ok=True)
    (data / "config.toml").write_text('library = "D:\\\\Skills"\n', encoding="utf-8")
    assert str(default_library()).endswith("Skills")
    monkeypatch.setenv("SKILLPANTRY_LIBRARY", str(env["tmp"] / "envlib"))
    assert default_library() == env["tmp"] / "envlib"


def test_bom_is_preserved(env):
    proj = env["tmp"] / "bom"
    proj.mkdir()
    original = b"\xef\xbb\xbf# Mine\r\n"
    (proj / "CLAUDE.md").write_bytes(original)
    assert main(["install", "kit", "-t", "claude-code", "-p", str(proj), "-y"]) == 0
    assert (proj / "CLAUDE.md").read_bytes().startswith(b"\xef\xbb\xbf# Mine\r\n")
    assert main(["uninstall", "kit", "-t", "claude-code", "-p", str(proj)]) == 0
    assert (proj / "CLAUDE.md").read_bytes() == original


# ---- the project's old name (skillshelf, before 0.5.0) -------------------------------------------

def test_blocks_written_under_the_old_name_are_updated_and_removed(env, tmp_path):
    proj = tmp_path / "old"
    proj.mkdir()
    assert main(["install", "kit", "-t", "claude-code", "-p", str(proj), "-y", "--variant", "one"]) == 0
    claude = proj / "CLAUDE.md"
    claude.write_text(claude.read_text(encoding="utf-8").replace("skillpantry:kit", "skillshelf:kit"), encoding="utf-8")
    assert main(["install", "kit", "-t", "claude-code", "-p", str(proj), "-y", "--variant", "one"]) == 0
    text = claude.read_text(encoding="utf-8")
    assert text.count("Be careful.") == 1 and "skillshelf:" not in text
    claude.write_text(text.replace("skillpantry:kit", "skillshelf:kit"), encoding="utf-8")
    assert main(["uninstall", "kit", "-t", "claude-code", "-p", str(proj)]) == 0
    assert not claude.exists()


def test_old_data_folder_moves_on_first_run(monkeypatch, tmp_path):
    from skillpantry.config import home_dir
    monkeypatch.delenv("SKILLPANTRY_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    old = tmp_path / ".skillshelf"
    (old / "catalog").mkdir(parents=True)
    (old / "catalog" / "mine.toml").write_text("x", encoding="utf-8")
    (old / "installs.json").write_text(json.dumps({"installs": {"k": {"backup": str(old / "backups" / "a")}}}), encoding="utf-8")
    new = home_dir()
    assert new == tmp_path / ".skillpantry" and not old.exists()
    assert (new / "catalog" / "mine.toml").exists()
    assert json.loads((new / "installs.json").read_text(encoding="utf-8"))["installs"]["k"]["backup"] == str(new / "backups" / "a")
