"""Fixed local knowledge and starter-skill files for the universal setup service."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


WIKI_FILES = {
    "index.md": "# 내 지식 보관함\n\n- [[raw/index|원본 문서]]\n- [[wiki/index|정리한 지식]]\n- [[projects/index|프로젝트]]\n",
    "raw/index.md": "# 원본 문서\n\n가져온 문서를 이 폴더에 보관하세요.\n",
    "wiki/index.md": "# 정리한 지식\n\n문서 사이의 관계는 명시적인 Markdown 링크로 연결하세요.\n",
    "projects/index.md": "# 프로젝트\n\n프로젝트별 지식을 정리하세요.\n",
}


def tree_files(source: Path) -> dict[str, bytes]:
    """Return safe bundled regular files, rejecting links and unusual entries."""
    if not source.is_dir() or source.is_symlink():
        return {}
    found: dict[str, bytes] = {}
    for path in sorted(source.rglob("*")):
        if path.is_symlink():
            return {}
        if path.is_file():
            found[path.relative_to(source).as_posix()] = path.read_bytes()
        elif not path.is_dir():
            return {}
    return found if "SKILL.md" in found else {}


def starter_files(root: Path) -> dict[str, bytes]:
    return tree_files(root / "portable/noleji-starter")


def targets(home: Path, component_id: str, root: Path) -> dict[Path, bytes]:
    """Map an allowlisted component to fixed destinations and content."""
    if component_id == "wiki":
        base = home / "Documents/Noleji Knowledge"
        return {base / name: value.encode() for name, value in WIKI_FILES.items()}
    if component_id == "noleji-starter":
        source = starter_files(root)
        bases = (
            home / "Library/Application Support/Noleji/skills/noleji-starter",
            home / ".agents/skills/noleji-starter",
            home / ".claude/skills/noleji-starter",
        )
        return {base / name: value for base in bases for name, value in source.items()}
    return {}


def conflicts_and_pending(home: Path, files: dict[Path, bytes]) -> tuple[list[str], list[Path]]:
    conflicts: list[str] = []
    pending: list[Path] = []
    for path, content in files.items():
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent == path or home in parent.parents):
            conflicts.append(path.name)
        elif path.exists():
            if not path.is_file() or path.read_bytes() != content:
                conflicts.append(path.relative_to(home).as_posix().split("Noleji Knowledge/")[-1])
        else:
            pending.append(path)
    return sorted(conflicts), pending


def tree_digest(files: dict[Path, bytes], home: Path) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(files.items()):
        digest.update(path.relative_to(home).as_posix().encode())
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


def install_files(files: dict[Path, bytes], home: Path) -> None:
    """Create fixed files through directory descriptors; never follow parent links."""
    if home.is_symlink():
        raise FileExistsError(home.name)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
    for path, content in files.items():
        parts = path.relative_to(home).parts
        if any(parent.is_symlink() for parent in (path, *path.parents) if parent == path or home in parent.parents):
            raise FileExistsError(path.name)
        descriptor = os.open(home, directory_flags)
        try:
            for part in parts[:-1]:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                next_descriptor = os.open(part, directory_flags, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_descriptor
            filename = parts[-1]
            try:
                leaf = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=descriptor)
            except FileNotFoundError:
                leaf = None
            if leaf is not None:
                with os.fdopen(leaf, "rb") as stream:
                    if stream.read() != content:
                        raise FileExistsError(filename)
                continue
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            leaf = os.open(filename, flags, 0o600, dir_fd=descriptor)
            with os.fdopen(leaf, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
