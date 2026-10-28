import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from universal_setup import UniversalState


class UniversalSetupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "home"
        self.home.mkdir()
        self.root = Path(self.temp.name) / "root"
        (self.root / "catalog").mkdir(parents=True)
        (self.root / "catalog/components.json").write_bytes((ROOT / "catalog/components.json").read_bytes())
        starter = self.root / "portable/noleji-starter"
        starter.mkdir(parents=True)
        (starter / "SKILL.md").write_text("# Starter\n", encoding="utf-8")
        self.available: set[str] = set()
        self.calls: list[list[str]] = []
        self.results: dict[str, int] = {}

        def which(command: str) -> str | None:
            return f"/fake/{command}" if command in self.available else None

        def runner(argv: list[str], timeout: int) -> int:
            self.calls.append(argv)
            return self.results.get(" ".join(argv), 1 if argv == ["codex", "login", "status"] or argv == ["claude", "auth", "status"] else 0)

        self.state = UniversalState(self.home, root=self.root, runner=runner, which=which, version_probe=lambda argv: "tool 1.2.3", system=lambda: ("Darwin", "arm64"))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def finish(self) -> dict:
        for _ in range(100):
            job = self.state.job()
            if job["state"] != "running":
                return job
            time.sleep(0.01)
        self.fail("job did not finish")

    def test_catalog_and_plan_are_json_safe_and_read_only(self) -> None:
        status = self.state.status()
        self.assertIn("codex", [item["id"] for item in status["components"]])
        plan = self.state.plan(["wiki"])
        json.dumps(plan)
        self.assertTrue(plan["canInstall"])
        self.assertFalse((self.home / "Documents").exists())

    def test_stale_plan_rejected_on_environment_change(self) -> None:
        plan = self.state.plan(["git"])
        self.available.add("git")
        with self.assertRaises(ValueError):
            self.state.start(["git"], plan["planId"])
        self.assertEqual(self.calls, [["git", "--version"]])

    def test_command_failure_and_false_success_do_not_claim_ready(self) -> None:
        self.available.add("brew")
        self.results["brew install git"] = 1
        plan = self.state.plan(["git"])
        self.state.start(["git"], plan["planId"])
        self.assertEqual(self.finish()["state"], "failed")
        self.assertEqual(self.state.status()["components"][0]["status"], "failed")
        self.results["brew install git"] = 0
        plan = self.state.plan(["git"])
        self.state.start(["git"], plan["planId"])
        self.assertEqual(self.finish()["state"], "failed")

    def test_dependency_and_conflict_block(self) -> None:
        self.assertIn("node", self.state.plan(["codex"])["missing"])
        target = self.home / "Documents/Noleji Knowledge/raw"
        target.mkdir(parents=True)
        (target / "index.md").write_text("my notes", encoding="utf-8")
        plan = self.state.plan(["wiki"])
        self.assertFalse(plan["canInstall"])
        self.assertIn("raw/index.md", plan["conflicts"])

    def test_wiki_idempotence_and_audit_sanitization(self) -> None:
        plan = self.state.plan(["wiki"])
        self.state.start(["wiki"], plan["planId"])
        self.assertEqual(self.finish()["state"], "done")
        self.assertEqual(next(item for item in self.state.status()["components"] if item["id"] == "wiki")["status"], "ready")
        self.assertEqual(self.state.plan(["wiki"])["steps"], [])
        history = self.state.history()
        self.assertEqual([event["state"] for event in history], ["started", "done"])
        self.assertNotIn(str(self.home), json.dumps(self.state.diagnostics()))
        self.assertNotIn(str(self.home), json.dumps(history))

    def test_starter_copy_is_idempotent_and_preserves_conflicts(self) -> None:
        plan = self.state.plan(["noleji-starter"])
        self.assertTrue(plan["canInstall"])
        self.state.start(["noleji-starter"], plan["planId"])
        self.assertEqual(self.finish()["state"], "done")
        self.assertEqual(self.state.plan(["noleji-starter"])["steps"], [])
        conflict = self.home / ".claude/skills/noleji-starter/SKILL.md"
        conflict.write_text("personal skill", encoding="utf-8")
        plan = self.state.plan(["noleji-starter"])
        self.assertFalse(plan["canInstall"])
        self.assertEqual(conflict.read_text(encoding="utf-8"), "personal skill")

    def test_one_job_at_a_time(self) -> None:
        import threading
        gate = threading.Event()
        self.available.add("brew")

        def runner(argv: list[str], timeout: int) -> int:
            if argv == ["brew", "install", "git"]:
                gate.wait(2)
            return 0

        self.state.runner = runner
        plan = self.state.plan(["git"])
        self.state.start(["git"], plan["planId"])
        with self.assertRaises(RuntimeError):
            self.state.start(["git"], plan["planId"])
        gate.set()
        self.assertEqual(self.finish()["state"], "failed")

    def test_npm_and_uv_use_fixed_packages_then_probe(self) -> None:
        self.available.update({"node", "npm", "uv"})

        def runner(argv: list[str], timeout: int) -> int:
            self.calls.append(argv)
            if argv == ["npm", "install", "-g", "@openai/codex"]:
                self.available.add("codex")
            if argv == ["uv", "tool", "install", "graphifyy"]:
                self.available.add("graphify")
            if argv == ["codex", "login", "status"]:
                return 1
            return 0

        self.state.runner = runner
        for component_id in ("codex", "graphify"):
            plan = self.state.plan([component_id])
            self.assertTrue(plan["canInstall"])
            self.state.start([component_id], plan["planId"])
            self.assertEqual(self.finish()["state"], "done")
        self.assertIn(["npm", "install", "-g", "@openai/codex"], self.calls)
        self.assertIn(["uv", "tool", "install", "graphifyy"], self.calls)
        self.assertEqual(next(item for item in self.state.status()["components"] if item["id"] == "codex")["status"], "auth-required")

    def test_agent_installed_requires_login_probe(self) -> None:
        self.available.add("codex")
        states = {item["id"]: item["status"] for item in self.state.status()["components"]}
        self.assertEqual(states["codex"], "auth-required")
        self.results["codex login status"] = 0
        states = {item["id"]: item["status"] for item in self.state.status()["components"]}
        self.assertEqual(states["codex"], "ready")

class UniversalReviewTest(unittest.TestCase):
    setUp = UniversalSetupTest.setUp
    tearDown = UniversalSetupTest.tearDown
    finish = UniversalSetupTest.finish

    def test_selected_prerequisites_are_planned_in_install_order(self) -> None:
        self.available.add("brew")
        plan = self.state.plan(["codex", "node", "graphify", "uv"])
        self.assertTrue(plan["canInstall"])
        self.assertEqual(plan["missing"], [])
        calls: list[list[str]] = []
        def runner(argv: list[str], timeout: int) -> int:
            calls.append(argv)
            if argv[:2] == ["brew", "install"]:
                self.available.add(argv[-1])
                if argv[-1] == "node":
                    self.available.add("npm")
            if argv == ["npm", "install", "-g", "@openai/codex"]:
                self.available.add("codex")
            if argv == ["uv", "tool", "install", "graphifyy"]:
                self.available.add("graphify")
            return 1 if argv == ["codex", "login", "status"] else 0
        self.state.runner = runner
        self.state.start(["codex", "node", "graphify", "uv"], plan["planId"])
        job = self.finish()
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["results"]["codex"], "auth-required")
        self.assertLess(calls.index(["brew", "install", "node"]), calls.index(["npm", "install", "-g", "@openai/codex"]))
        self.assertLess(calls.index(["brew", "install", "uv"]), calls.index(["uv", "tool", "install", "graphifyy"]))

    def test_version_parser_keeps_full_node_and_codex_versions(self) -> None:
        from universal_setup import _safe_version
        self.assertEqual(_safe_version("v26.7.0"), "26.7.0")
        self.assertEqual(_safe_version("codex-cli 0.156.0"), "0.156.0")
        self.assertEqual(_safe_version("node v26.7.0"), "26.7.0")
        self.assertIsNone(_safe_version("secret=v26.7.0x"))

    def test_obsidian_requires_real_executable_and_plist_version(self) -> None:
        import plistlib
        app = self.home / "Applications/Obsidian.app"
        app.mkdir(parents=True)
        status = next(item for item in self.state.status()["components"] if item["id"] == "obsidian")
        self.assertEqual((status["status"], status["version"]), ("blocked", None))
        self.assertIn("Obsidian.app", self.state.plan(["obsidian"])["conflicts"])
        contents = app / "Contents"
        binary = contents / "MacOS/Obsidian"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "Obsidian", "CFBundleShortVersionString": "1.13.7"}))
        status = next(item for item in self.state.status()["components"] if item["id"] == "obsidian")
        self.assertEqual(status["status"], "blocked")
        binary.chmod(0o755)
        status = next(item for item in self.state.status()["components"] if item["id"] == "obsidian")
        self.assertEqual((status["status"], status["version"]), ("ready", "1.13.7"))
        self.assertEqual(self.state.plan(["obsidian"])["steps"], [])

    def test_unparseable_version_and_unsupported_system_fail_closed(self) -> None:
        self.available.add("git")
        self.state.version_probe = lambda argv: "token=secret local=/private/home"
        item = next(item for item in self.state.status()["components"] if item["id"] == "git")
        self.assertEqual((item["status"], item["version"]), ("missing", None))
        self.assertNotIn("secret", json.dumps(self.state.diagnostics()))
        self.state.system = lambda: ("Linux", "x86_64")
        self.assertFalse(self.state.plan(["wiki"])["canInstall"])
        self.assertIn("macos", self.state.plan(["wiki"])["missing"])

    def test_job_reports_failed_component_and_sanitized_progress(self) -> None:
        self.available.add("brew")
        self.results["brew install git"] = 1
        plan = self.state.plan(["git"])
        self.state.start(["git"], plan["planId"])
        job = self.finish()
        self.assertEqual(job["failedComponent"], "git")
        self.assertEqual(job["results"]["git"], "failed")
        self.assertEqual(job["current"], None)
        self.assertNotIn(str(self.home), json.dumps(job))

    def test_install_process_environment_excludes_provider_credentials(self) -> None:
        import os
        from unittest.mock import patch
        from universal_process import environment
        with patch.dict(os.environ, {"OPENAI_API_KEY": "fake-secret", "ANTHROPIC_AUTH_TOKEN": "fake-other", "NODE_OPTIONS": "--require unsafe.js"}):
            install_env = environment(self.home, "/opt/homebrew/bin")
            auth_env = environment(self.home, "/opt/homebrew/bin", auth_provider="codex")
        self.assertEqual(install_env["HOME"], str(self.home))
        self.assertNotIn("OPENAI_API_KEY", install_env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", install_env)
        self.assertNotIn("NODE_OPTIONS", install_env)
        self.assertEqual(auth_env["OPENAI_API_KEY"], "fake-secret")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", auth_env)
        self.assertNotIn("NODE_OPTIONS", auth_env)

    def test_default_command_lookup_includes_local_tool_bin(self) -> None:
        local_bin = self.home / ".local/bin"
        local_bin.mkdir(parents=True)
        tool = local_bin / "graphify"
        tool.write_text("#!/bin/sh\necho graphify 0.9.63\n", encoding="utf-8")
        tool.chmod(0o755)
        state = UniversalState(self.home, root=self.root, system=lambda: ("Darwin", "arm64"))
        self.assertEqual(state._which("graphify"), str(tool))
        status, version, _ = state._probe("graphify")
        self.assertEqual((status, version), ("ready", "0.9.63"))

    def test_tampered_history_is_excluded_from_diagnostics(self) -> None:
        self.state.history_path.parent.mkdir(parents=True)
        self.state.history_path.write_text(json.dumps({"at": 1, "state": "/private/token", "componentIds": ["git"]}) + "\n" + json.dumps({"at": 2, "state": "done", "componentIds": ["../../secret"]}) + "\n", encoding="utf-8")
        self.assertEqual(self.state.history(), [])
        self.assertEqual(self.state.diagnostics()["historyCount"], 0)

    def test_guide_only_is_not_reported_as_installed(self) -> None:
        item = next(item for item in self.state.status()["components"] if item["id"] == "browser-tools")
        self.assertEqual(item["status"], "guide-only")
        plan = self.state.plan(["browser-tools"])
        self.assertFalse(plan["canInstall"])
        with self.assertRaises(ValueError):
            self.state.start(["browser-tools"], plan["planId"])

    def test_file_install_rechecks_parent_symlink(self) -> None:
        from universal_files import install_files
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.home / "Documents"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(FileExistsError):
            install_files({link / "Noleji Knowledge/index.md": b"safe"}, self.home)
        self.assertFalse((outside / "Noleji Knowledge/index.md").exists())
