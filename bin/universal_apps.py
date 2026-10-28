"""Read-only version and macOS application probes for setup status."""

from __future__ import annotations

import os
import plistlib
import re
from pathlib import Path
from xml.parsers.expat import ExpatError


def safe_version(raw: str | None) -> str | None:
    """Extract a short version without leaking command output or an interior fragment."""
    if raw is None:
        return None
    match = re.search(r"(?<![A-Za-z0-9.])v?(?P<version>\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.]{1,20})?)(?![A-Za-z0-9.])", raw[:256])
    return match.group("version") if match else None


def obsidian_probe(home: Path) -> tuple[str, str | None, str]:
    """Validate the app executable and bundled public version before readiness."""
    for app in (home / "Applications/Obsidian.app", Path("/Applications/Obsidian.app")):
        if not app.exists() and not app.is_symlink():
            continue
        if app.is_symlink() or not app.is_dir():
            return "blocked", None, "기존 Obsidian 앱을 확인해 주세요."
        try:
            with (app / "Contents/Info.plist").open("rb") as stream:
                info = plistlib.load(stream)
        except (OSError, ValueError, TypeError, plistlib.InvalidFileException, ExpatError):
            return "blocked", None, "Obsidian 앱 정보를 읽을 수 없습니다."
        if not isinstance(info, dict):
            return "blocked", None, "Obsidian 앱 정보를 확인할 수 없습니다."
        name = info.get("CFBundleExecutable")
        raw_version = info.get("CFBundleShortVersionString")
        if not isinstance(name, str) or not name or Path(name).name != name or not isinstance(raw_version, str):
            return "blocked", None, "Obsidian 앱 정보를 확인할 수 없습니다."
        binary = app / "Contents/MacOS" / name
        version = safe_version(raw_version)
        if not binary.is_file() or not os.access(binary, os.X_OK) or version is None:
            return "blocked", None, "Obsidian 실행 파일이나 버전을 확인할 수 없습니다."
        return "ready", version, "설치됐습니다. 지식 보관함을 앱에서 열어 주세요."
    return "missing", None, "설치할 수 있습니다."
