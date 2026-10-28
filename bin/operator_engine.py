"""Managed Markdown releases for the local Noleji Operator companion."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import tomllib
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

COMPANION_VERSION = "0.3.0"
MAX_FEED_BYTES = 2_000_000
PROFILE_IDS = {"web-app", "game", "knowledge", "documents", "media", "operations"}
TRUSTED_CONNECTIONS = {
    "notion": "https://mcp.notion.com/mcp",
    "supabase": "https://mcp.supabase.com/mcp?read_only=true",
    "github": "https://api.githubcopilot.com/mcp/",
}
CLIENT_ROOTS = {"codex": ".agents/skills", "claude": ".claude/skills"}
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _exact_keys(value: dict, expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ValueError(f"invalid {name} fields")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _version(value: object) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("invalid version")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]


def _text(value: object, name: str, maximum: int = 20_000) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise ValueError(f"invalid {name}")
    return value


def validate_release(raw: object) -> dict:
    """Validate the complete untrusted release before any part is used."""
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise ValueError("unsupported release schema")
    release = copy.deepcopy(raw)
    _exact_keys(release, {"schemaVersion", "revision", "version", "publishedAt", "minCompanionVersion", "summary", "profiles", "connections", "tools", "changelog"}, "release")
    revision = release.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        raise ValueError("invalid revision")
    _version(release.get("version"))
    if _version(release.get("minCompanionVersion")) > _version(COMPANION_VERSION):
        raise ValueError("companion update required")
    published = _text(release.get("publishedAt"), "publishedAt", 64)
    try:
        parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid publishedAt") from error
    if parsed.tzinfo is None:
        raise ValueError("invalid publishedAt")
    _text(release.get("summary"), "summary", 2_000)
    for key in ("changelog",):
        if not isinstance(release.get(key), list) or any(not isinstance(v, str) or len(v) > 2_000 for v in release[key]):
            raise ValueError(f"invalid {key}")

    profiles = release.get("profiles")
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= len(PROFILE_IDS):
        raise ValueError("invalid profiles")
    seen: set[str] = set()
    total = 0
    for profile in profiles:
        if not isinstance(profile, dict) or profile.get("id") not in PROFILE_IDS or profile["id"] in seen:
            raise ValueError("invalid profile id")
        _exact_keys(profile, {"id", "name", "description", "outcome", "componentIds", "connectionIds", "skillId", "files", "steps", "firstRequest", "limitations"}, "profile")
        seen.add(profile["id"])
        if profile.get("skillId") != f"noleji-operator-{profile['id']}":
            raise ValueError("invalid skill id")
        for key in ("name", "description", "outcome", "firstRequest"):
            _text(profile.get(key), key, 10_000)
        for key in ("componentIds", "connectionIds", "steps", "limitations"):
            values = profile.get(key)
            if not isinstance(values, list) or any(not isinstance(value, str) or not value or len(value) > 2_000 for value in values):
                raise ValueError(f"invalid {key}")
        if any(not ID_RE.fullmatch(value) for value in profile["componentIds"]):
            raise ValueError("invalid component id")
        if any(value not in TRUSTED_CONNECTIONS for value in profile["connectionIds"]):
            raise ValueError("untrusted connection")
        files = profile.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= 50:
            raise ValueError("invalid files")
        paths: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("invalid file")
            _exact_keys(item, {"path", "content", "sha256"}, "file")
            path = item.get("path")
            if not isinstance(path, str) or path in paths or (path != "SKILL.md" and not re.fullmatch(r"references/[A-Za-z0-9._-]+\.md", path)):
                raise ValueError("invalid managed path")
            pure = PurePosixPath(path)
            if pure.is_absolute() or ".." in pure.parts:
                raise ValueError("invalid managed path")
            paths.add(path)
            content = _text(item.get("content"), "content", 500_000)
            encoded = content.encode("utf-8")
            total += len(encoded)
            if not isinstance(item.get("sha256"), str) or not SHA_RE.fullmatch(item["sha256"]) or _digest(encoded) != item["sha256"]:
                raise ValueError("invalid file checksum")
        if "SKILL.md" not in paths:
            raise ValueError("missing SKILL.md")
    if total > MAX_FEED_BYTES:
        raise ValueError("release too large")

    connections = release.get("connections")
    if not isinstance(connections, list):
        raise ValueError("invalid connections")
    connection_ids: set[str] = set()
    for item in connections:
        if not isinstance(item, dict) or item.get("id") not in TRUSTED_CONNECTIONS or item["id"] in connection_ids:
            raise ValueError("invalid connection")
        _exact_keys(item, {"id", "name", "url", "guideUrl", "description", "access"}, "connection")
        connection_ids.add(item["id"])
        if item.get("url") != TRUSTED_CONNECTIONS[item["id"]]:
            raise ValueError("connection URL changed")
        for key in ("name", "guideUrl", "description", "access"):
            value = _text(item.get(key), key, 4_000)
            if key == "guideUrl" and not value.startswith("https://"):
                raise ValueError("invalid guide URL")
    if connection_ids != {value for profile in profiles for value in profile["connectionIds"]}:
        raise ValueError("connection list mismatch")
    tools = release.get("tools")
    if not isinstance(tools, list) or len(tools) > 100:
        raise ValueError("invalid tools")
    tool_ids: set[str] = set()
    for item in tools:
        if not isinstance(item, dict):
            raise ValueError("invalid tool")
        _exact_keys(item, {"id", "name", "provider", "sourceUrl", "guideUrl"}, "tool")
        if item.get("id") in tool_ids or not isinstance(item.get("id"), str) or not ID_RE.fullmatch(item["id"]):
            raise ValueError("invalid tool id")
        tool_ids.add(item["id"])
        for key in ("id", "name", "provider", "sourceUrl", "guideUrl"):
            value = _text(item.get(key), key, 4_000)
            if key.endswith("Url") and not value.startswith("https://"):
                raise ValueError("invalid tool URL")
    return release


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ValueError("feed redirect refused")


def fetch_release() -> bytes:
    raise ValueError("No default release feed. Supply an explicitly reviewed fetcher.")


class OperatorEngine:
    def __init__(self, home: Path, *, root: Path | None = None, fetcher: Callable[[], bytes] | None = None, install_busy: Callable[[], bool] | None = None, mutation_lock=None) -> None:
        self.home = home
        self.root = root or Path(__file__).resolve().parents[1]
        self.fetcher = fetcher or fetch_release
        self.install_busy = install_busy or (lambda: False)
        self.mutation_lock = mutation_lock or threading.RLock()
        self.state_dir = home / ".local/share/noleji-operator"
        self.ledger_path = self.state_dir / "ledger.json"
        self.cache_path = self.state_dir / "release.json"
        self.lock = threading.RLock()
        self.ledger = self._load_ledger()
        self.release: dict | None = None
        self.feed_status = "unavailable"
        self._load_initial_release()

    def _assert_safe(self, path: Path) -> None:
        try:
            path.relative_to(self.home)
        except ValueError as error:
            raise OSError("path outside home") from error
        if self.home.is_symlink():
            raise OSError("home is symlink")
        current = self.home
        for part in path.relative_to(self.home).parts:
            current = current / part
            if current.is_symlink():
                raise OSError("symlink refused")

    def _write(self, path: Path, data: bytes) -> None:
        self._assert_safe(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._assert_safe(path)
        temporary = path.parent / f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(temporary, "xb") as stream:
                os.chmod(temporary, 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists() and (not path.is_file() or path.is_symlink()):
                raise OSError("unsafe target")
            os.replace(temporary, path)
            if path.read_bytes() != data:
                raise OSError("write verification failed")
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _load_json(self, path: Path) -> object | None:
        self._assert_safe(path)
        if not path.exists():
            return None
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_FEED_BYTES:
            raise OSError("unsafe state file")
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_ledger(self) -> dict:
        blank = {"preferences": {"autoCheck": True, "autoApply": False}, "managed": {}, "connections": {}, "history": [], "lastCheck": None}
        value = self._load_json(self.ledger_path)
        if value is None:
            return blank
        if not isinstance(value, dict) or not isinstance(value.get("preferences"), dict) or not isinstance(value.get("managed"), dict):
            raise ValueError("invalid operator ledger")
        preferences = value["preferences"]
        if type(preferences.get("autoCheck")) is not bool or type(preferences.get("autoApply")) is not bool:
            raise ValueError("invalid operator preferences")
        for profile_id, record in value["managed"].items():
            if profile_id not in PROFILE_IDS or not isinstance(record, dict) or not isinstance(record.get("files"), dict) or not isinstance(record.get("rollback"), dict):
                raise ValueError("invalid managed ledger")
            if set(record["files"]) != set(record["rollback"]):
                raise ValueError("invalid managed ledger")
            for relative, checksum in record["files"].items():
                allowed = any(re.fullmatch(re.escape(root) + rf"/noleji-operator-{re.escape(profile_id)}/(?:SKILL\.md|references/[A-Za-z0-9._-]+\.md)", relative) for root in CLIENT_ROOTS.values())
                rollback = record["rollback"][relative]
                if not allowed or not isinstance(checksum, str) or not SHA_RE.fullmatch(checksum) or (rollback is not None and not isinstance(rollback, str)):
                    raise ValueError("invalid managed ledger")
        connections = value.get("connections", {})
        if not isinstance(connections, dict):
            raise ValueError("invalid connection ledger")
        for key in connections:
            parts = key.split(":", 1)
            if len(parts) != 2 or parts[0] not in CLIENT_ROOTS or parts[1] not in TRUSTED_CONNECTIONS:
                raise ValueError("invalid connection ledger")
        for key, default in blank.items():
            value.setdefault(key, default)
        return value

    def _save_ledger(self) -> None:
        self._write(self.ledger_path, json.dumps(self.ledger, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())

    def _load_initial_release(self) -> None:
        candidates = [self.cache_path, self.root / "catalog/operator-release.json"]
        best: dict | None = None
        for path in candidates:
            try:
                raw = self._load_json(path) if path.is_relative_to(self.home) else json.loads(path.read_text(encoding="utf-8"))
                if raw is not None:
                    candidate = validate_release(raw)
                    if best is None or candidate["revision"] > best["revision"]:
                        best = candidate
            except (OSError, ValueError, json.JSONDecodeError):
                continue
        self.release = best
        self.feed_status = "not-checked" if best else "unavailable"

    def _public_release(self) -> dict | None:
        if self.release is None:
            return None
        result = copy.deepcopy(self.release)
        for profile in result["profiles"]:
            profile["files"] = [{"path": item["path"], "sha256": item["sha256"]} for item in profile["files"]]
        return result

    def _profile(self, profile_id: str) -> dict:
        if self.release is None:
            raise ValueError("release unavailable")
        for profile in self.release["profiles"]:
            if profile["id"] == profile_id:
                return profile
        raise ValueError("unknown profile")

    def _targets(self, profile: dict) -> dict[str, bytes]:
        result: dict[str, bytes] = {}
        for client_root in CLIENT_ROOTS.values():
            for item in profile["files"]:
                relative = f"{client_root}/{profile['skillId']}/{item['path']}"
                result[relative] = item["content"].encode("utf-8")
        return result

    def _profile_state(self, profile: dict) -> tuple[str, str, int | None]:
        managed = self.ledger["managed"].get(profile["id"])
        targets = self._targets(profile)
        if not managed:
            if any((self.home / path).exists() or (self.home / path).is_symlink() for path in targets):
                return "conflict", "기존 파일이 있어 자동으로 덮어쓰지 않습니다.", None
            return "available", "선택하면 두 에이전트의 스킬 폴더에 추가합니다.", None
        files = managed.get("files", {})
        for relative, checksum in files.items():
            path = self.home / relative
            try:
                if not path.is_file() or path.is_symlink() or _digest(path.read_bytes()) != checksum:
                    return "modified", "설치 후 파일이 변경되어 자동 업데이트를 멈췄습니다.", managed.get("revision")
            except OSError:
                return "modified", "관리 파일 상태를 확인할 수 없습니다.", managed.get("revision")
        wanted = {path: _digest(content) for path, content in targets.items()}
        if set(files) != set(wanted):
            return "conflict", "관리 파일 구성이 바뀌어 자동으로 추가하거나 삭제하지 않습니다.", managed.get("revision")
        if files == wanted and managed.get("revision") == self.release["revision"]:
            return "installed", "최신 관리 버전입니다.", managed.get("revision")
        return "update-available", "새 관리 버전을 적용할 수 있습니다.", managed.get("revision")

    def _connection_state(self, connection_id: str, client: str) -> tuple[str, str]:
        key = f"{client}:{connection_id}"
        record = self.ledger.get("connections", {}).get(key)
        if isinstance(record, dict):
            path = self.home / (".codex/config.toml" if client == "codex" else ".claude.json")
            try:
                self._assert_safe(path)
                if not path.is_file() or path.is_symlink():
                    return "modified", "연결 설정 파일이 변경되었습니다. 현재 설정을 직접 확인하세요."
                raw = path.read_bytes()
                if client == "codex":
                    parsed = tomllib.loads(raw.decode("utf-8"))
                    entry = parsed.get("mcp_servers", {}).get(f"noleji-{connection_id}") if isinstance(parsed.get("mcp_servers", {}), dict) else None
                    configured = isinstance(entry, dict) and entry.get("url") == TRUSTED_CONNECTIONS[connection_id] and entry.get("type", "http") == "http"
                else:
                    parsed = json.loads(raw)
                    entry = parsed.get("mcpServers", {}).get(f"noleji-{connection_id}") if isinstance(parsed, dict) and isinstance(parsed.get("mcpServers", {}), dict) else None
                    configured = isinstance(entry, dict) and entry.get("type") == "http" and entry.get("url") == TRUSTED_CONNECTIONS[connection_id]
                if not configured:
                    return "modified", "연결 설정 파일이 변경되었습니다. 현재 설정을 직접 확인하세요."
            except (OSError, UnicodeError, tomllib.TOMLDecodeError, json.JSONDecodeError):
                return "modified", "연결 설정 파일을 확인할 수 없습니다."
            return "configured", "설정되었습니다. 로그인과 접근 권한은 에이전트에서 확인하세요."
        return "not-configured", "연결 설정을 추가할 수 있습니다. 로그인은 별도로 진행합니다."

    def snapshot(self) -> dict:
        with self.lock:
            profiles = []
            if self.release:
                for profile in self.release["profiles"]:
                    status, detail, revision = self._profile_state(profile)
                    profiles.append({"id": profile["id"], "installedRevision": revision, "status": status, "detail": detail})
            connections = []
            ids = [item["id"] for item in self.release["connections"]] if self.release else sorted(TRUSTED_CONNECTIONS)
            for connection_id in ids:
                for client in CLIENT_ROOTS:
                    status, detail = self._connection_state(connection_id, client)
                    connections.append({"id": connection_id, "client": client, "status": status, "detail": detail})
            return {"release": self._public_release(), "preferences": copy.deepcopy(self.ledger["preferences"]), "profiles": profiles, "connections": connections, "lastCheck": self.ledger.get("lastCheck"), "feedStatus": self.feed_status, "history": copy.deepcopy(self.ledger.get("history", []))[-50:]}

    def check(self) -> dict:
        with self.lock:
            checked = _now()
            try:
                data = self.fetcher()
                if not isinstance(data, bytes) or len(data) > MAX_FEED_BYTES:
                    raise ValueError("invalid feed bytes")
                candidate = validate_release(json.loads(data.decode("utf-8")))
                if self.release and candidate["revision"] < self.release["revision"]:
                    raise ValueError("release revision rollback refused")
                if self.release and candidate["revision"] == self.release["revision"] and candidate != self.release:
                    raise ValueError("release revision content changed")
                self._write(self.cache_path, json.dumps(candidate, ensure_ascii=False, sort_keys=True).encode())
                self.release = candidate
                self.feed_status = "current"
            except (OSError, ValueError, UnicodeError, json.JSONDecodeError):
                self.feed_status = "stale" if self.release else "unavailable"
                self._record_history("check-failed", [])
            self.ledger["lastCheck"] = checked
            self._save_ledger()
            return self.snapshot()

    def preferences(self, payload: dict) -> dict:
        if set(payload) != {"autoCheck", "autoApply"} or any(type(payload[key]) is not bool for key in payload):
            raise ValueError("invalid preferences")
        with self.lock:
            self.ledger["preferences"] = dict(payload)
            self._record_history("preferences", [])
            self._save_ledger()
            return self.snapshot()

    def plan(self, profile_ids: object) -> dict:
        if not isinstance(profile_ids, list) or not profile_ids or any(not isinstance(value, str) for value in profile_ids) or len(set(profile_ids)) != len(profile_ids):
            raise ValueError("invalid profiles")
        with self.lock:
            if self.release is None:
                raise ValueError("release unavailable")
            changes, conflicts = [], []
            for profile_id in profile_ids:
                profile = self._profile(profile_id)
                status, _, _ = self._profile_state(profile)
                action = "install" if status == "available" else "update" if status == "update-available" else "none"
                if status in {"conflict", "modified"}:
                    conflicts.append(profile_id)
                    action = "conflict"
                for path in self._targets(profile):
                    changes.append({"profileId": profile_id, "path": path, "action": action})
            basis = {"profileIds": profile_ids, "changes": changes, "conflicts": conflicts, "revision": self.release["revision"]}
            plan_id = _digest(json.dumps(basis, sort_keys=True, separators=(",", ":")).encode())
            return {"planId": plan_id, **basis, "canApply": not conflicts and any(item["action"] in {"install", "update"} for item in changes)}

    def _record_history(self, action: str, profile_ids: list[str]) -> None:
        self.ledger.setdefault("history", []).append({"at": _now(), "action": action, "profileIds": profile_ids, "revision": self.release["revision"] if self.release else None})
        self.ledger["history"] = self.ledger["history"][-50:]

    def apply(self, profile_ids: object, plan_id: object) -> dict:
        with self.mutation_lock, self.lock:
            if self.install_busy():
                raise RuntimeError("universal install is running")
            current_plan = self.plan(profile_ids)
            if not isinstance(plan_id, str) or plan_id != current_plan["planId"] or not current_plan["canApply"]:
                raise ValueError("stale or empty plan")
            originals: dict[Path, bytes | None] = {}
            old_ledger = copy.deepcopy(self.ledger)
            backup_dir = self.state_dir / "backups" / f"{int(time.time_ns())}"
            try:
                for profile_id in profile_ids:  # type: ignore[union-attr]
                    profile = self._profile(profile_id)
                    targets = self._targets(profile)
                    for relative in targets:
                        path = self.home / relative
                        self._assert_safe(path)
                        originals[path] = path.read_bytes() if path.exists() else None
                    manifest = {path: (_digest(value) if value is not None else None) for path, value in ((relative, originals[self.home / relative]) for relative in targets)}
                    self._write(backup_dir / f"{profile_id}.json", json.dumps(manifest, sort_keys=True).encode())
                    for relative, content in targets.items():
                        previous = originals[self.home / relative]
                        if previous is not None:
                            self._write(backup_dir / f"{profile_id}-{hashlib.sha256(relative.encode()).hexdigest()}.md", previous)
                        self._write(self.home / relative, content)
                    previous_record = copy.deepcopy(self.ledger["managed"].get(profile_id))
                    self.ledger["managed"][profile_id] = {"revision": self.release["revision"], "files": {path: _digest(content) for path, content in targets.items()}, "rollback": {path: value.decode("utf-8") if value is not None else None for path, value in ((relative, originals[self.home / relative]) for relative in targets)}, "previous": previous_record, "installedAt": _now()}
                self._record_history("apply", list(profile_ids))  # type: ignore[arg-type]
                self._save_ledger()
            except Exception:
                self.ledger = old_ledger
                for path, content in originals.items():
                    try:
                        if content is None:
                            if path.exists() and not path.is_symlink():
                                path.unlink()
                        else:
                            self._write(path, content)
                    except OSError:
                        pass
                raise
            return self.snapshot()

    def rollback(self, profile_id: object) -> dict:
        if not isinstance(profile_id, str):
            raise ValueError("invalid profile")
        with self.mutation_lock, self.lock:
            if self.install_busy():
                raise RuntimeError("universal install is running")
            record = self.ledger["managed"].get(profile_id)
            if not isinstance(record, dict) or not isinstance(record.get("rollback"), dict):
                raise ValueError("no managed snapshot")
            for relative, checksum in record.get("files", {}).items():
                path = self.home / relative
                if not path.is_file() or path.is_symlink() or _digest(path.read_bytes()) != checksum:
                    raise ValueError("managed file changed")
            old_ledger = copy.deepcopy(self.ledger)
            originals = {self.home / relative: (self.home / relative).read_bytes() for relative in record["files"]}
            try:
                for relative, content in record["rollback"].items():
                    path = self.home / relative
                    self._assert_safe(path)
                    if content is None:
                        path.unlink()
                    elif isinstance(content, str):
                        self._write(path, content.encode("utf-8"))
                    else:
                        raise ValueError("invalid rollback")
                if record.get("previous") is None:
                    del self.ledger["managed"][profile_id]
                else:
                    self.ledger["managed"][profile_id] = record["previous"]
                self.ledger["preferences"]["autoApply"] = False
                self._record_history("rollback", [profile_id])
                self._save_ledger()
            except Exception:
                self.ledger = old_ledger
                for path, content in originals.items():
                    self._write(path, content)
                raise
            return self.snapshot()

    def connect(self, connection_id: object, client: object) -> dict:
        if not isinstance(connection_id, str) or connection_id not in TRUSTED_CONNECTIONS or client not in CLIENT_ROOTS:
            raise ValueError("invalid connection")
        if connection_id == "github":
            raise ValueError("github connection is guide-only")
        name = f"noleji-{connection_id}"
        url = TRUSTED_CONNECTIONS[connection_id]
        with self.mutation_lock, self.lock:
            if self.install_busy():
                raise RuntimeError("universal install is running")
            path = self.home / (".codex/config.toml" if client == "codex" else ".claude.json")
            self._assert_safe(path)
            if path.exists() and (not path.is_file() or path.is_symlink()):
                raise OSError("unsafe config")
            if client == "codex":
                old = path.read_bytes() if path.exists() else b""
                try:
                    parsed = tomllib.loads(old.decode("utf-8")) if old else {}
                except (UnicodeError, tomllib.TOMLDecodeError) as error:
                    raise ValueError("invalid Codex config") from error
                servers = parsed.get("mcp_servers", {})
                if not isinstance(servers, dict) or name in servers:
                    raise ValueError("connection name collision")
                addition = f'\n[mcp_servers.{name}]\nurl = {json.dumps(url)}\n'.encode()
                new = old + (b"" if not old or old.endswith(b"\n") else b"\n") + addition.lstrip(b"\n")
                tomllib.loads(new.decode("utf-8"))
            else:
                old = path.read_bytes() if path.exists() else b""
                try:
                    parsed = json.loads(old) if old else {}
                except (UnicodeError, json.JSONDecodeError) as error:
                    raise ValueError("invalid Claude config") from error
                if not isinstance(parsed, dict) or not isinstance(parsed.get("mcpServers", {}), dict):
                    raise ValueError("invalid Claude config")
                servers = parsed.setdefault("mcpServers", {})
                if name in servers:
                    raise ValueError("connection name collision")
                servers[name] = {"type": "http", "url": url}
                new = (json.dumps(parsed, ensure_ascii=False, indent=2) + "\n").encode()
            backup = self.state_dir / "connection-backups" / f"{client}-{int(time.time_ns())}.bak"
            self._write(backup, old)
            old_ledger = copy.deepcopy(self.ledger)
            try:
                self._write(path, new)
                self.ledger.setdefault("connections", {})[f"{client}:{connection_id}"] = {"configuredAt": _now(), "configDigest": _digest(new)}
                self._record_history("connect", [])
                self._save_ledger()
            except Exception:
                self.ledger = old_ledger
                if old:
                    self._write(path, old)
                elif path.exists() and not path.is_symlink():
                    path.unlink()
                raise
            return self.snapshot()

    def automatic_cycle(self) -> dict:
        with self.lock:
            preferences = dict(self.ledger["preferences"])
        if preferences["autoCheck"]:
            self.check()
        if preferences["autoApply"] and self.feed_status == "current" and not self.install_busy() and self.release:
            selected = [profile["id"] for profile in self.release["profiles"] if profile["id"] in self.ledger["managed"] and self._profile_state(profile)[0] == "update-available"]
            if selected:
                plan = self.plan(selected)
                if plan["canApply"]:
                    self.apply(selected, plan["planId"])
        return self.snapshot()

    def start_daemon(self, interval: float = 21600) -> threading.Thread:
        def run() -> None:
            while True:
                try:
                    self.automatic_cycle()
                except (OSError, ValueError, RuntimeError):
                    with self.lock:
                        try:
                            self._record_history("automatic-error", [])
                            self._save_ledger()
                        except OSError:
                            pass
                time.sleep(interval)

        thread = threading.Thread(target=run, name="noleji-operator-refresh", daemon=True)
        thread.start()
        return thread
