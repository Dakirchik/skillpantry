from pathlib import Path
from unittest.mock import patch

import os
import tempfile
import unittest
from unittest.mock import patch as mock_patch


def raises(error):
    return unittest.TestCase().assertRaises(error)

from skillpantry.cli import main
from skillpantry.installer import InstallError
from skillpantry.sync import AGENTS, apply_sync, plan_sync
from skillpantry.targets import Layout


def skill(root, name="demo"):
    folder = root / name
    (folder / "references").mkdir(parents=True)
    (folder / "SKILL.md").write_bytes(f"---\r\nname: {name}\r\n---\r\nSee references/guide.md\r\n".encode())
    (folder / "references" / "guide.md").write_bytes("РџСЂРёРјРµСЂ\r\n".encode())
    (folder / "scripts").mkdir()
    (folder / "scripts" / "run.py").write_bytes(b"print('hello')\n")
    return folder


def case_transfer_complete_tree_and_idempotence(tmp_path, monkeypatch, agent):
    monkeypatch.setenv("SKILLPANTRY_HOME", str(tmp_path / "data"))
    source = Layout("claude-code", "project", tmp_path / "src")
    target = Layout(agent, "project", tmp_path / "dst")
    original = skill(source.skills_dir(""))
    plan = plan_sync(source, target)
    assert plan[0].status == "new"
    apply_sync(plan)
    copied = target.skills_dir("") / "demo"
    for p in original.rglob("*"):
        if p.is_file():
            assert (copied / p.relative_to(original)).read_bytes() == p.read_bytes()
    assert plan_sync(source, target)[0].status == "unchanged"


def case_conflict_backup_and_no_deletion(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILLPANTRY_HOME", str(tmp_path / "data"))
    a, b = Layout("claude-code", "project", tmp_path), Layout("codex", "project", tmp_path)
    skill(a.skills_dir(""))
    old = skill(b.skills_dir(""))
    (old / "local.txt").write_text("keep in backup")
    extra = skill(b.skills_dir(""), "other")
    plan = plan_sync(a, b)
    with raises(InstallError):
        apply_sync(plan)
    assert (old / "local.txt").exists()
    backup = apply_sync(plan, force=True)
    assert (backup / "original-0-demo" / "local.txt").read_text() == "keep in backup"
    assert extra.exists()
    assert not (old / "local.txt").exists()


def case_selection_legacy_codex_and_overlap(tmp_path):
    a, b = Layout("codex", "global", tmp_path), Layout("gemini", "project", tmp_path)
    skill(tmp_path / ".codex" / "skills")
    skill(a.skills_dir(""), "second")
    assert [x.name for x in plan_sync(a, b, ["demo"])] == ["demo"]
    with raises(InstallError):
        plan_sync(a, b, ["missing"])
    with raises(InstallError):
        plan_sync(a, a, ["second"])


def case_cli_dry_run(tmp_path):
    skill(tmp_path / ".claude" / "skills")
    assert main(["sync", "--from", "claude-code", "--from-path", str(tmp_path),
                 "-t", "codex", "-p", str(tmp_path), "--dry-run"]) == 0
    assert not (tmp_path / ".agents").exists()


def case_stale_plan_and_agent_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILLPANTRY_HOME", str(tmp_path / "data"))
    a, b = Layout("claude-code", "project", tmp_path), Layout("cursor", "project", tmp_path)
    src = skill(a.skills_dir(""))
    (src / "references" / "guide.md").write_text("${CLAUDE_PLUGIN_ROOT}/scripts/run.py")
    plan = plan_sync(a, b)
    assert plan[0].warnings
    (src / "changed.txt").write_text("new")
    with raises(InstallError):
        apply_sync(plan)
    assert not b.skills_dir("").exists()


def case_rollback_on_copy_failure(tmp_path, monkeypatch):
    import shutil
    monkeypatch.setenv("SKILLPANTRY_HOME", str(tmp_path / "data"))
    a, b = Layout("claude-code", "project", tmp_path), Layout("codex", "project", tmp_path)
    skill(a.skills_dir(""))
    dst = skill(b.skills_dir(""))
    (dst / "local.txt").write_text("original")
    plan = plan_sync(a, b)
    real_copy = shutil.copytree
    def fail_once(src, dest, *args, **kwargs):
        if Path(src).name == "stage-0":
            raise OSError("simulated failure")
        return real_copy(src, dest, *args, **kwargs)
    with patch("skillpantry.sync.shutil.copytree", side_effect=fail_once):
        with raises(OSError):
            apply_sync(plan, force=True)
    assert (dst / "local.txt").read_text() == "original"


class SyncTests(unittest.TestCase):
    def test_wizard_claude_to_codex_in_same_project(self):
        from skillpantry.interactive import sync_flow
        from io import StringIO
        with tempfile.TemporaryDirectory() as folder, mock_patch.dict(os.environ):
            root = Path(folder)
            os.environ["SKILLPANTRY_HOME"] = str(root / "data")
            original = skill(root / ".claude" / "skills")
            # Choose agents first, then a project path, then that same project.
            with patch("skillpantry.ui.choose", side_effect=["claude-code", "codex", "other", "same"]) as choose, \
                 patch("skillpantry.ui.ask", side_effect=[str(root), ""]), \
                 patch("skillpantry.ui.confirm", return_value=True), \
                 patch("sys.stdout", new_callable=StringIO):
                sync_flow(None)
            destination = root / ".agents" / "skills" / "demo"
            self.assertEqual((destination / "SKILL.md").read_bytes(), (original / "SKILL.md").read_bytes())
            self.assertEqual(choose.call_args_list[-1].args[1][0][0], "same")

    def test_wizard_global_claude_to_project_codex(self):
        from skillpantry.interactive import sync_flow
        from io import StringIO
        with tempfile.TemporaryDirectory() as folder, mock_patch.dict(os.environ):
            root = Path(folder)
            home = root / "home"
            project = root / "project"
            project.mkdir()
            os.environ["SKILLPANTRY_HOME"] = str(root / "data")
            skill(home / ".claude" / "skills")
            with patch("pathlib.Path.home", return_value=home), \
                 patch("skillpantry.ui.choose", side_effect=["claude-code", "codex", "global", "other"]), \
                 patch("skillpantry.ui.ask", side_effect=[str(project), ""]), \
                 patch("skillpantry.ui.confirm", return_value=True), \
                 patch("sys.stdout", new_callable=StringIO):
                sync_flow(None)
            self.assertTrue((project / ".agents" / "skills" / "demo" / "SKILL.md").exists())

    def run_case(self, fn, *args):
        class Environment:
            def setenv(self, key, value):
                os.environ[key] = value
        with tempfile.TemporaryDirectory() as folder, mock_patch.dict(os.environ):
            fn(Path(folder), *[Environment() if x == "environment" else x for x in args])

    def test_all_agents(self):
        for agent in AGENTS:
            with self.subTest(agent=agent):
                self.run_case(case_transfer_complete_tree_and_idempotence, "environment", agent)

    def test_conflicts(self):
        self.run_case(case_conflict_backup_and_no_deletion, "environment")

    def test_legacy_and_selection(self):
        self.run_case(case_selection_legacy_codex_and_overlap)

    def test_preview(self):
        self.run_case(case_cli_dry_run)

    def test_stale_source(self):
        self.run_case(case_stale_plan_and_agent_warning, "environment")

    def test_rollback(self):
        self.run_case(case_rollback_on_copy_failure, "environment")

    def test_document_name_and_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            a, b = Layout("claude-code", "project", root), Layout("codex", "project", root)
            first = skill(a.skills_dir(""), "original-folder")
            (first / "SKILL.md").write_text("---\nname: portable-name\n---\nBody", encoding="utf-8")
            plan = plan_sync(a, b)
            self.assertEqual(plan[0].destination.name, "portable-name")
            other = skill(a.skills_dir(""), "another-folder")
            (other / "SKILL.md").write_text("---\nname: portable-name\n---\nOther", encoding="utf-8")
            with self.assertRaises(InstallError):
                plan_sync(a, b)

    def test_changed_destination_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder, mock_patch.dict(os.environ):
            root = Path(folder)
            os.environ["SKILLPANTRY_HOME"] = str(root / "data")
            a, b = Layout("claude-code", "project", root), Layout("codex", "project", root)
            skill(a.skills_dir(""))
            plan = plan_sync(a, b)
            destination = skill(b.skills_dir(""))
            (destination / "local.txt").write_text("keep")
            with self.assertRaises(InstallError):
                apply_sync(plan, force=True)
            self.assertEqual((destination / "local.txt").read_text(), "keep")
