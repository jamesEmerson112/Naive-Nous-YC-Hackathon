"""Packaging device/ from git (B1): tracked regular files only, deterministic, named by commit."""

import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

import pytest

from romulus_cloud.devicecode import package
from romulus_cloud.devicecode.package import PackageError
from romulus_cloud.services import registry

REPO = Path(__file__).resolve().parents[4]


def _tar(*members: tuple[str, bytes | None, bytes]) -> bytes:
    """(name, data or None, type) members; type tarfile.REGTYPE / DIRTYPE / SYMTYPE ..."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.SYMTYPE:
                info.linkname = "../../etc/passwd"
            info.size = len(data or b"")
            tar.addfile(info, io.BytesIO(data or b"") if kind == tarfile.REGTYPE else None)
    return buf.getvalue()


def test_files_from_tar_keeps_regular_files_under_device_only():
    data = _tar(
        ("device/", None, tarfile.DIRTYPE),
        ("device/robot/app.py", b"print(1)\n", tarfile.REGTYPE),
        ("./device/README.md", b"hi\n", tarfile.REGTYPE),
        ("bench/tasks.py", b"x\n", tarfile.REGTYPE),
    )
    assert package.files_from_tar(data) == {
        "device/robot/app.py": b"print(1)\n",
        "device/README.md": b"hi\n",
    }


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_files_from_tar_refuses_links_and_special_files(kind):
    data = _tar(("device/a.py", b"x\n", tarfile.REGTYPE), ("device/evil", None, kind))
    with pytest.raises(PackageError, match="only regular files"):
        package.files_from_tar(data)


def test_files_from_tar_refuses_an_empty_or_unreadable_archive():
    with pytest.raises(PackageError, match="no files under device/"):
        package.files_from_tar(_tar(("bench/x.py", b"x\n", tarfile.REGTYPE)))
    with pytest.raises(PackageError, match="not a readable tar"):
        package.files_from_tar(b"not a tar at all")


def test_the_real_device_package_is_the_tracked_files_at_the_last_device_commit():
    rev, files = package.from_git(REPO)
    tracked = subprocess.run(
        ["git", "-C", str(REPO), "ls-tree", "-r", "--name-only", rev, "--", "device"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert rev == package.last_commit(REPO)
    assert sorted(files) == sorted(tracked)
    assert {"device/pytest.ini", "device/robot/control.py", "device/tests/test_spec.py"} <= set(
        files
    )
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in files)


def test_the_real_device_package_tarball_is_deterministic():
    _, first = package.from_git(REPO)
    _, again = package.from_git(REPO)
    one, two = registry.build_tarball(first), registry.build_tarball(again)
    assert hashlib.sha256(one).hexdigest() == hashlib.sha256(two).hexdigest()


def test_unpack_writes_the_package_and_refuses_escapes(tmp_path):
    device = package.unpack({"device/robot/app.py": b"x\n"}, tmp_path)
    assert (device / "robot" / "app.py").read_bytes() == b"x\n"
    with pytest.raises(PackageError):
        package.unpack({"device/../../evil": b"x"}, tmp_path / "other")
