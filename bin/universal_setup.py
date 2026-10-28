"""Allowlisted, local-only setup planner and installer for a new Mac."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from universal_apps import obsidian_probe, safe_version as _safe_version
from universal_files import conflicts_and_pending, install_files, targets, tree_digest
from universal_process import environment, read_version, run_command

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {"git": "git", "node": "node", "uv": "uv", "codex": "codex", "claude": "claude", "graphify": "graphify"}
BREW = {"git": ["brew", "install", "git"], "node": ["brew", "install", "node"], "uv": ["brew", "install", "uv"], "obsidian": ["brew", "install", "--cask", "obsidian"], "claude": ["brew", "install", "--cask", "claude-code"]}
NPM = {"codex": ["npm", "install", "-g", "@openai/codex"]}
AUTH = {"codex": ["codex", "login", "status"], "claude": ["claude", "auth", "status"]}


class UniversalState:
    """Mutable job state with read-only plans and a single serial install lane."""

    def __init__(self, home: Path, *, root: Path | None = None, runner: Callable[[list[str], int], int] | None = None, which: Callable[[str], str | None] | None = None, version_probe: Callable[[list[str]], str | None] | None = None, system: Callable[[], tuple[str, str]] | None = None) -> None:
        self.home = home
        self.root = root or ROOT
        self.which = which or self._which
        self.runner = runner or self._run_local
        self.version_probe = version_probe or self._version_local
        self.system = system or (lambda: (platform.system(), platform.machine()))
        self.catalog_path = self.root / "catalog/components.json"
        self.history_path = home / ".local/share/noleji-harness/setup/history.jsonl"
        self.lock = threading.RLock()
        self._job: dict = {"state": "idle", "componentIds": [], "current": None, "results": {}, "failedComponent": None}
        self._failed_component: str | None = None

    def _search_path(self) -> str:
        paths = [str(self.home / ".local/bin"), "/opt/homebrew/bin", "/usr/local/bin"]
        paths.extend(part for part in os.environ.get("PATH", "").split(os.pathsep) if part and os.path.isabs(part))
        return os.pathsep.join(paths)

    def _which(self, command: str) -> str | None:
        return shutil.which(command, path=self._search_path())

    def _version_local(self, argv: list[str]) -> str | None:
        return read_version(argv, environment(self.home, self._search_path()))

    def _run_local(self, argv: list[str], timeout: int) -> int:
        binary = self.which(argv[0])
        if binary is None:
            return 1
        auth_provider = argv[0] if argv in AUTH.values() else None
        return run_command([binary, *argv[1:]], timeout, environment(self.home, self._search_path(), auth_provider=auth_provider))

    def _supported(self) -> bool:
        operating_system, architecture = self.system()
        return operating_system == "Darwin" and architecture in {"arm64", "x86_64"}

    def _catalog(self) -> list[dict]:
        items = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError("Invalid component catalog")
        return items

    def _probe(self, component_id: str) -> tuple[str, str | None, str]:
        if component_id in {"browser-tools", "media-tools"}:
            return "guide-only", None, "안내를 확인해 필요한 도구를 선택하세요."
        if not self._supported():
            return "blocked", None, "이 준비 도우미는 지원되는 Mac에서만 설치할 수 있습니다."
        if component_id in {"wiki", "noleji-starter"}:
            files = targets(self.home, component_id, self.root)
            if not files:
                return "blocked", None, "시작 스킬 묶음을 찾을 수 없습니다."
            conflicts, pending = conflicts_and_pending(self.home, files)
            if conflicts:
                return "blocked", None, "기존 파일을 확인해 주세요."
            return ("ready", None, "준비됐습니다.") if not pending else ("missing", None, "설치할 수 있습니다.")
        if component_id == "obsidian":
            return obsidian_probe(self.home)
        command = COMMANDS[component_id]
        binary = self.which(command)
        if not binary or self.runner([command, "--version"], 8) != 0:
            return "missing", None, "설치할 수 있습니다."
        version = _safe_version(self.version_probe([binary, "--version"]))
        if version is None:
            return "missing", None, "설치된 도구의 버전을 확인할 수 없습니다."
        if component_id in AUTH and self.runner(AUTH[component_id], 8) != 0:
            return "auth-required", version, "설치됐습니다. 로그인 후 사용하세요."
        return "ready", version, "준비됐습니다."

    def status(self) -> dict:
        """Return public metadata with live, honest component readiness."""
        components = []
        for item in self._catalog():
            state, version, detail = self._probe(item["id"])
            if state == "missing" and item["id"] == self._failed_component:
                state, detail = "failed", "설치 후 확인에 실패했습니다. 다시 시도할 수 있습니다."
            components.append({**item, "status": state, "version": version, "detail": detail, "readinessScope": "installation", "sampleStatus": "not-run"})
        return {"components": components, "job": self.job(), "serviceReadiness": {"ready": False, "sampleStatus": "not-run", "detail": "설치와 로그인 확인은 실제 업무 샘플 통과와 다릅니다. 사용할 작업의 결과를 확인한 뒤 사용 준비를 완료하세요."}}

    def plan(self, component_ids: list[str]) -> dict:
        """Bind the exact selection, source, catalog, and environment to a plan ID."""
        if not isinstance(component_ids, list) or not component_ids or any(not isinstance(item, str) for item in component_ids):
            raise ValueError("하나 이상의 항목을 선택해 주세요")
        catalog = {item["id"]: item for item in self._catalog()}
        selected = sorted(set(component_ids))
        if len(selected) != len(component_ids) or any(item not in catalog for item in selected):
            raise ValueError("알 수 없거나 중복된 항목입니다")
        status = {item["id"]: item for item in self.status()["components"]}
        supported = self._supported()
        missing: list[str] = []
        conflicts: list[str] = []
        steps: list[dict] = []
        file_digest: dict[str, str] = {}
        for component_id in selected:
            item = catalog[component_id]
            for required in item["requires"]:
                if status[required]["status"] not in {"ready", "auth-required"} and required not in selected:
                    missing.append(required)
            if item["installMode"] == "guide":
                continue
            if not supported:
                missing.append("macos")
                continue
            if status[component_id]["status"] in {"ready", "auth-required"}:
                continue
            if component_id == "obsidian" and status[component_id]["status"] == "blocked":
                conflicts.append("Obsidian.app")
                continue
            files = targets(self.home, component_id, self.root)
            if item["installMode"] in {"bundle", "scaffold"}:
                file_digest[component_id] = tree_digest(files, self.home)
                if not files:
                    missing.append(component_id)
                    continue
                problems, pending = conflicts_and_pending(self.home, files)
                conflicts.extend(problems)
                if pending:
                    steps.append({"componentId": component_id, "kind": "files", "label": item["name"]})
                continue
            prerequisite = "brew" if item["installMode"].startswith("homebrew") else "npm" if item["installMode"] == "npm" else "uv"
            if not self.which(prerequisite):
                supplied = (prerequisite == "npm" and "node" in selected and self.which("brew")) or (prerequisite == "uv" and "uv" in selected and self.which("brew"))
                if not supplied:
                    missing.append(prerequisite)
            steps.append({"componentId": component_id, "kind": "command", "label": item["name"]})
        snapshot = {item: status[item]["status"] for item in sorted(set(selected + missing) & catalog.keys())}
        payload = {"componentIds": selected, "components": [status[item] for item in selected], "steps": steps, "missing": sorted(set(missing)), "conflicts": sorted(set(conflicts)), "canInstall": not missing and not conflicts and (bool(steps) or all(catalog[item]["installMode"] != "guide" for item in selected))}
        binding = {"selection": selected, "catalog": hashlib.sha256(self.catalog_path.read_bytes()).hexdigest(), "files": file_digest, "environment": snapshot, "plan": payload}
        plan_id = hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return {**payload, "planId": plan_id}

    def _record(self, state: str, component_ids: list[str]) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps({"at": int(time.time()), "state": state, "componentIds": component_ids}, ensure_ascii=False).encode() + b"\n"
        descriptor = os.open(self.history_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())

    def start(self, component_ids: list[str], plan_id: str) -> dict:
        """Start one background install after the plan is checked again."""
        with self.lock:
            if self._job["state"] == "running":
                raise RuntimeError("이미 준비 작업이 진행 중입니다")
            plan = self.plan(component_ids)
            if plan["planId"] != plan_id:
                raise ValueError("준비 내용이 달라졌습니다. 다시 확인해 주세요")
            if not plan["canInstall"]:
                raise ValueError("필수 도구 또는 기존 파일을 먼저 확인해 주세요")
            selected = plan["componentIds"]
            self._record("started", selected)
            self._failed_component = None
            self._job = {"state": "running", "componentIds": selected, "current": None, "results": {item: "pending" for item in selected}, "failedComponent": None, "startedAt": int(time.time())}
            thread = threading.Thread(target=self._finish, args=(selected,), daemon=True)
            thread.start()
            return self.job()

    def _install_one(self, component_id: str) -> bool:
        current, _, _ = self._probe(component_id)
        if current in {"ready", "auth-required", "guide-only"}:
            return True
        if component_id in {"wiki", "noleji-starter"}:
            install_files(targets(self.home, component_id, self.root), self.home)
        elif component_id in BREW:
            if self.runner(BREW[component_id], 1200) != 0:
                return False
        elif component_id in NPM:
            if self.runner(NPM[component_id], 1200) != 0:
                return False
        elif component_id == "graphify":
            if self.runner(["uv", "tool", "install", "graphifyy"], 1200) != 0:
                return False
        return self._probe(component_id)[0] in {"ready", "auth-required"}

    def _finish(self, selected: list[str]) -> None:
        component_id: str | None = None
        try:
            catalog = {item["id"]: item for item in self._catalog()}
            remaining = set(selected)
            while remaining:
                progress = False
                for component_id in sorted(remaining):
                    if any(required in remaining for required in catalog[component_id]["requires"]):
                        continue
                    with self.lock:
                        self._job["current"] = component_id
                        self._job["results"][component_id] = "running"
                    if not self._install_one(component_id):
                        raise RuntimeError("verification failed")
                    with self.lock:
                        self._job["results"][component_id] = self._probe(component_id)[0]
                    remaining.remove(component_id)
                    progress = True
                if not progress:
                    raise RuntimeError("dependency cycle")
            state = "done"
        except (OSError, RuntimeError, FileExistsError):
            state = "failed"
        try:
            self._record(state, selected)
        except OSError:
            state = "failed"
        with self.lock:
            self._failed_component = component_id if state == "failed" else None
            results = dict(self._job["results"])
            if state == "failed" and component_id is not None:
                results[component_id] = "failed"
            self._job = {"state": state, "componentIds": selected, "current": None, "results": results, "failedComponent": self._failed_component, "finishedAt": int(time.time()), "serviceReady": False, "sampleStatus": "not-run"}

    def job(self) -> dict:
        with self.lock:
            return {**self._job, "componentIds": list(self._job["componentIds"]), "results": dict(self._job["results"])}

    def history(self) -> list[dict]:
        if not self.history_path.is_file():
            return []
        events = []
        known = {item["id"] for item in self._catalog()}
        for line in self.history_path.read_text(encoding="utf-8").splitlines()[-30:]:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and set(event) == {"at", "state", "componentIds"} and type(event["at"]) is int and event["state"] in {"started", "done", "failed"} and isinstance(event["componentIds"], list) and all(isinstance(item, str) and item in known for item in event["componentIds"]):
                events.append(event)
        return events

    def diagnostics(self) -> dict:
        components = [{"id": item["id"], "status": item["status"], "version": item["version"]} for item in self.status()["components"]]
        return {"components": components, "job": self.job(), "historyCount": len(self.history())}
