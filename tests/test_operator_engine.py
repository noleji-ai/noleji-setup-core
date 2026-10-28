"""Isolated lifecycle and adversarial checks for managed Operator releases."""

import copy
import hashlib
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from operator_engine import OperatorEngine, validate_release


def release(revision: int, content: str = "# Managed skill\n") -> dict:
    return {
        "schemaVersion": 1,
        "revision": revision,
        "version": "0.3.0",
        "publishedAt": "2026-09-29T00:00:00Z",
        "minCompanionVersion": "0.3.0",
        "summary": "A release",
        "profiles": [{
            "id": "web-app", "name": "Web", "description": "Build", "outcome": "Working app",
            "componentIds": ["codex"], "connectionIds": ["notion"], "skillId": "noleji-operator-web-app",
            "files": [{"path": "SKILL.md", "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()}],
            "steps": ["Choose"], "firstRequest": "Build it", "limitations": ["Local only"],
        }],
        "connections": [{"id": "notion", "name": "Notion", "url": "https://mcp.notion.com/mcp", "guideUrl": "https://www.notion.so/help", "description": "Notes", "access": "Login separately"}],
        "tools": [], "changelog": ["Changed"],
    }


def encoded(value: dict) -> bytes:
    return json.dumps(value).encode()


class OperatorEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.root = self.base / "kit"
        (self.root / "catalog").mkdir(parents=True)
        (self.root / "catalog/operator-release.json").write_text(json.dumps(release(1)), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def engine(self, **kwargs) -> OperatorEngine:
        return OperatorEngine(self.home, root=self.root, **kwargs)

    def target(self, client: str = ".agents") -> Path:
        return self.home / client / "skills/noleji-operator-web-app/SKILL.md"

    def test_install_upgrade_and_rollback_restore_previous_managed_snapshot(self) -> None:
        next_release = release(2, "# Version two\n")
        engine = self.engine(fetcher=lambda: encoded(next_release))
        first = engine.plan(["web-app"])
        applied = engine.apply(["web-app"], first["planId"])
        self.assertEqual((self.target().read_text(), applied["profiles"][0]["status"]), ("# Managed skill\n", "installed"))
        self.assertEqual(self.target(".claude").read_text(), "# Managed skill\n")

        engine.check()
        update = engine.plan(["web-app"])
        self.assertEqual({item["action"] for item in update["changes"]}, {"update"})
        engine.apply(["web-app"], update["planId"])
        self.assertEqual(self.target().read_text(), "# Version two\n")
        engine.preferences({"autoCheck": False, "autoApply": True})
        rolled = engine.rollback("web-app")
        self.assertEqual(self.target().read_text(), "# Managed skill\n")
        self.assertEqual((rolled["profiles"][0]["installedRevision"], rolled["profiles"][0]["status"]), (1, "update-available"))
        self.assertFalse(rolled["preferences"]["autoApply"])
        engine.automatic_cycle()
        self.assertEqual(self.target().read_text(), "# Managed skill\n")
        self.assertTrue(any((self.home / ".local/share/noleji-operator/backups").rglob("*.json")))

    def test_modified_and_unmanaged_files_are_conflicts_and_rollback_refuses_edits(self) -> None:
        unmanaged = self.engine()
        self.target().parent.mkdir(parents=True)
        self.target().write_text("# Mine\n")
        plan = unmanaged.plan(["web-app"])
        self.assertFalse(plan["canApply"])
        self.assertEqual(plan["conflicts"], ["web-app"])

        self.temp.cleanup()
        self.setUp()
        engine = self.engine()
        plan = engine.plan(["web-app"])
        engine.apply(["web-app"], plan["planId"])
        self.target().write_text("# User edit\n")
        self.assertEqual(engine.snapshot()["profiles"][0]["status"], "modified")
        with self.assertRaises(ValueError):
            engine.rollback("web-app")
        self.assertEqual(self.target().read_text(), "# User edit\n")

    def test_bad_feeds_and_revision_rollback_keep_last_good_release(self) -> None:
        engine = self.engine(fetcher=lambda: b"not json")
        self.assertEqual(engine.snapshot()["feedStatus"], "not-checked")
        self.assertEqual(engine.check()["feedStatus"], "stale")
        self.assertEqual(engine.snapshot()["history"][-1]["action"], "check-failed")
        self.assertEqual(engine.snapshot()["release"]["revision"], 1)

        malicious = release(2)
        malicious["profiles"][0]["files"][0]["path"] = "../escape.md"
        engine.fetcher = lambda: encoded(malicious)
        self.assertEqual(engine.check()["feedStatus"], "stale")

        older = release(0)
        older["revision"] = 1
        changed_same_revision = release(1, "# Replaced in place\n")
        engine.fetcher = lambda: encoded(changed_same_revision)
        self.assertEqual(engine.check()["feedStatus"], "stale")
        self.assertEqual(engine.snapshot()["release"]["profiles"][0]["files"][0]["sha256"], release(1)["profiles"][0]["files"][0]["sha256"])

        engine.release = validate_release(release(2, "# New\n"))
        engine.fetcher = lambda: encoded(older)
        self.assertEqual(engine.check()["feedStatus"], "stale")
        self.assertEqual(engine.snapshot()["release"]["revision"], 2)

    def test_path_symlink_and_invalid_flags_fail_closed(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (self.home / ".agents").symlink_to(outside, target_is_directory=True)
        engine = self.engine()
        plan = engine.plan(["web-app"])
        with self.assertRaises(OSError):
            engine.apply(["web-app"], plan["planId"])
        self.assertEqual(list(outside.rglob("*.md")), [])
        (outside / "config.toml").write_text('secret = "outside"\n', encoding="utf-8")
        (self.home / ".codex").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            engine.connect("notion", "codex")
        self.assertFalse((self.home / ".local/share/noleji-operator/connection-backups").exists())
        with self.assertRaises(ValueError):
            engine.preferences({"autoCheck": 1, "autoApply": False})
        with self.assertRaises(ValueError):
            engine.preferences({"autoCheck": True, "autoApply": False, "extra": True})
        invalid = release(2)
        invalid["profiles"][0]["files"][0]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            validate_release(invalid)

    def test_busy_install_blocks_apply_and_preferences_persist(self) -> None:
        engine = self.engine(install_busy=lambda: True)
        plan = engine.plan(["web-app"])
        with self.assertRaises(RuntimeError):
            engine.apply(["web-app"], plan["planId"])
        with self.assertRaises(RuntimeError):
            engine.connect("notion", "codex")
        engine.install_busy = lambda: False
        engine.apply(["web-app"], plan["planId"])
        engine.install_busy = lambda: True
        with self.assertRaises(RuntimeError):
            engine.rollback("web-app")
        engine.preferences({"autoCheck": False, "autoApply": True})
        reopened = self.engine()
        self.assertEqual(reopened.snapshot()["preferences"], {"autoCheck": False, "autoApply": True})

    def test_automatic_cycle_updates_only_unchanged_installed_profile(self) -> None:
        upcoming = release(2, "# Automatic two\n")
        engine = self.engine(fetcher=lambda: encoded(upcoming))
        plan = engine.plan(["web-app"])
        engine.apply(["web-app"], plan["planId"])
        engine.preferences({"autoCheck": True, "autoApply": True})
        engine.automatic_cycle()
        self.assertEqual(self.target().read_text(), "# Automatic two\n")

        self.target().write_text("# My protected edit\n")
        engine.fetcher = lambda: encoded(release(3, "# Automatic three\n"))
        snapshot = engine.automatic_cycle()
        self.assertEqual(self.target().read_text(), "# My protected edit\n")
        self.assertEqual(snapshot["profiles"][0]["status"], "modified")

    def test_stale_feed_never_triggers_automatic_apply(self) -> None:
        responses = [encoded(release(2, "# Automatic two\n")), b"broken"]
        engine = self.engine(fetcher=lambda: responses.pop(0))
        plan = engine.plan(["web-app"])
        engine.apply(["web-app"], plan["planId"])
        engine.check()
        engine.preferences({"autoCheck": True, "autoApply": True})
        snapshot = engine.automatic_cycle()
        self.assertEqual(snapshot["feedStatus"], "stale")
        self.assertEqual(self.target().read_text(), "# Managed skill\n")

    def test_changed_release_file_set_is_an_explicit_conflict(self) -> None:
        changed = release(2, "# Version two\n")
        reference = "# Reference\n"
        changed["profiles"][0]["files"].append({"path": "references/new.md", "content": reference, "sha256": hashlib.sha256(reference.encode()).hexdigest()})
        engine = self.engine(fetcher=lambda: encoded(changed))
        plan = engine.plan(["web-app"])
        engine.apply(["web-app"], plan["planId"])
        engine.check()
        update = engine.plan(["web-app"])
        self.assertEqual((update["canApply"], update["conflicts"]), (False, ["web-app"]))
        self.assertFalse((self.home / ".agents/skills/noleji-operator-web-app/references/new.md").exists())

    def test_shared_mutation_lock_serializes_apply(self) -> None:
        mutation_lock = threading.RLock()
        engine = self.engine(mutation_lock=mutation_lock)
        plan = engine.plan(["web-app"])
        finished = threading.Event()
        errors = []

        def run() -> None:
            try:
                engine.apply(["web-app"], plan["planId"])
            except Exception as error:  # pragma: no cover - asserted below
                errors.append(error)
            finally:
                finished.set()

        mutation_lock.acquire()
        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.05)
        self.assertFalse(finished.is_set())
        mutation_lock.release()
        thread.join(timeout=2)
        self.assertTrue(finished.is_set())
        self.assertEqual(errors, [])
        self.assertTrue(self.target().is_file())

    def test_tampered_ledger_path_is_rejected_before_file_access(self) -> None:
        engine = self.engine()
        plan = engine.plan(["web-app"])
        engine.apply(["web-app"], plan["planId"])
        ledger_path = self.home / ".local/share/noleji-operator/ledger.json"
        ledger = json.loads(ledger_path.read_text())
        record = ledger["managed"]["web-app"]
        checksum = next(iter(record["files"].values()))
        record["files"] = {"../../outside.md": checksum}
        record["rollback"] = {"../../outside.md": None}
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.engine()

    def test_connection_merge_preserves_config_and_never_claims_authentication(self) -> None:
        codex = self.home / ".codex/config.toml"
        codex.parent.mkdir()
        codex.write_text('model = "gpt"\n', encoding="utf-8")
        engine = self.engine()
        snapshot = engine.connect("notion", "codex")
        content = codex.read_text()
        self.assertTrue(content.startswith('model = "gpt"\n'))
        self.assertIn("[mcp_servers.noleji-notion]", content)
        status = next(item for item in snapshot["connections"] if item["id"] == "notion" and item["client"] == "codex")
        self.assertEqual(status["status"], "configured")
        self.assertNotIn("authenticated", json.dumps(snapshot))
        engine.connect("supabase", "codex")
        self.assertEqual(engine._connection_state("notion", "codex")[0], "configured")
        with self.assertRaises(ValueError):
            engine.connect("notion", "codex")
        with self.assertRaises(ValueError):
            engine.connect("github", "claude")

        claude = self.home / ".claude.json"
        claude.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
        engine.connect("notion", "claude")
        merged = json.loads(claude.read_text())
        self.assertEqual(merged["theme"], "dark")
        self.assertEqual(merged["mcpServers"]["noleji-notion"]["url"], "https://mcp.notion.com/mcp")
        self.assertGreaterEqual(len(list((self.home / ".local/share/noleji-operator/connection-backups").glob("*.bak"))), 2)


if __name__ == "__main__":
    unittest.main()
