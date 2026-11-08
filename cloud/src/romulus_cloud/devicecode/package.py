"""Package `device/` from git as a code version (blocker B1, APIs §2.10, D21).

A code package is a deterministic `.tar.gz` of `device/` (`registry.build_tarball`). Here its
files come from `git archive`, so only tracked files at one commit go in: no `.venv`, logs,
`__pycache__` or local edits. The rev defaults to the short sha of the last commit that
touched the folder, so registering unchanged code again names the same version.

Only regular files are packaged. A symlink, hard link or device file in the archive is
refused: unpacked on the cloud side, a link could point the replay outside its scratch copy.
"""

import io
import os
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from romulus_contracts.codepatch import CODE_ROOT

# Host git ignores the user's global and system config (hooks, aliases, fsmonitor).
_GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


class PackageError(Exception):
    """The folder or archive can't become a code package."""


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        env={**os.environ, **_GIT_ENV},
    )
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip()[:300]
        raise PackageError(f"git {args[0]} failed: {err}")
    return proc.stdout


def repo_root(path: Path) -> Path:
    return Path(_git(path, "rev-parse", "--show-toplevel").decode().strip())


def last_commit(repo: Path, folder: str = CODE_ROOT) -> str:
    """Short sha of the last commit that touched `folder`."""
    rev = _git(repo, "log", "-1", "--format=%h", "--abbrev=7", "--", folder).decode().strip()
    if not rev:
        raise PackageError(f"no commit touches {folder}/ in {repo}")
    return rev


def uncommitted(repo: Path, folder: str = CODE_ROOT) -> list[str]:
    """Changed or untracked files under `folder` that a package from git leaves out."""
    out = _git(repo, "status", "--porcelain", "--", folder).decode()
    return [line[3:] for line in out.splitlines() if line.strip()]


def git_archive(repo: Path, rev: str, folder: str = CODE_ROOT) -> bytes:
    """`git archive` of `folder` at `rev`: a plain tar of its tracked files."""
    return _git(repo, "archive", "--format=tar", rev, "--", folder)


def files_from_tar(data: bytes, root: str = CODE_ROOT) -> dict[str, bytes]:
    """The regular files under `root/` in a tar (e.g. `git archive` output), by path."""
    files: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as tar:
            for member in tar:
                parts = PurePosixPath(member.name.removeprefix("./")).parts
                if not parts or parts[0] != root:
                    continue
                if member.isdir():
                    continue
                if ".." in parts or not member.isfile():
                    raise PackageError(f"{member.name}: only regular files can be packaged")
                fh = tar.extractfile(member)
                files[str(PurePosixPath(*parts))] = fh.read() if fh else b""
    except tarfile.TarError as exc:
        raise PackageError(f"not a readable tar: {exc}") from exc
    if not files:
        raise PackageError(f"the archive has no files under {root}/")
    return files


def from_git(
    repo: Path, rev: str | None = None, folder: str = CODE_ROOT
) -> tuple[str, dict[str, bytes]]:
    """(rev, files) for `folder` at `rev` (default: the last commit that touched it)."""
    rev = rev or last_commit(repo, folder)
    return rev, files_from_tar(git_archive(repo, rev, folder), root=folder)


def unpack(files: dict[str, bytes], dest: Path) -> Path:
    """Write a package's files under `dest` (no links in a package); returns `dest/device`."""
    for name, data in files.items():
        parts = PurePosixPath(name).parts
        if ".." in parts or PurePosixPath(name).is_absolute():
            raise PackageError(f"{name}: not a plain relative path")
        path = dest.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return dest / CODE_ROOT
