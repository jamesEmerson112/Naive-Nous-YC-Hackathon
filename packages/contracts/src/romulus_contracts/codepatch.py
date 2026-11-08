"""Code packages and the `code_patch` format (APIs §2.10, D21).

- A **code version** is named by a `code_rev` string in the platform's registry (not a
  git ref). Its package is a `.tar.gz` of `device/` at that version, served by
  `GET /v1/code/{rev}/package` with its sha256 in the `X-Sha256` header. The device
  installs it like a harness package: hash check, health check, rollback (D7), then
  reports `InstallResult(type="code", version=<rev>, ...)`.
- A **`code_patch`** (in `Overlay.code_patch`) is a unified diff against the overlay's
  base `code_rev`, with paths relative to the `device/` root and the usual `a/` and `b/`
  prefixes: `--- a/control/gripper.py` / `+++ b/control/gripper.py`. New and deleted
  files use `/dev/null` on one side. `git diff` output (run inside `device/`) fits.
- The base `code_rev` is the code the device ran when it made the patch. Send it as
  `running.code_rev` in the incident (proposed; see README).

Patches are stored raw. These helpers *report* problems; nothing here rejects an incident.
Standard library only.
"""

import re
from pathlib import PurePosixPath

CODE_ROOT = "device"
"""The directory a code package contains and `code_patch` paths are relative to."""

_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_DEV_NULL = "/dev/null"


def _header_path(line: str) -> str:
    # "--- a/x.py\t2026-11-07 22:01:00" -> "a/x.py"
    return line[4:].split("\t", 1)[0].strip()


def _parse(code_patch: str) -> tuple[list[tuple[int, str, str]], list[str]]:
    """File headers as (line number, old path, new path), plus structural problems."""
    headers: list[tuple[int, str, str]] = []
    problems: list[str] = []
    lines = code_patch.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            headers.append((i + 1, _header_path(line), _header_path(lines[i + 1])))
            i += 2
            continue
        match = _HUNK.match(line)
        if match:
            old_left = int(match.group(1) or 1)
            new_left = int(match.group(2) or 1)
            start = i + 1
            i += 1
            while (old_left > 0 or new_left > 0) and i < len(lines):
                body = lines[i]
                if body.startswith("\\"):  # "\ No newline at end of file"
                    i += 1
                    continue
                if body.startswith("-"):
                    old_left -= 1
                elif body.startswith("+"):
                    new_left -= 1
                elif body.startswith(" ") or body == "":
                    old_left -= 1
                    new_left -= 1
                else:
                    break
                i += 1
            if old_left > 0 or new_left > 0:
                problems.append(f"hunk at line {start} is shorter than its header says")
            continue
        i += 1
    return headers, problems


def _path_problems(line_no: int, raw: str, prefix: str) -> tuple[str | None, list[str]]:
    """The path relative to device/ (None for /dev/null), and any problems with it."""
    if raw == _DEV_NULL:
        return None, []
    if not raw.startswith(prefix):
        return None, [f"line {line_no}: path {raw!r} should start with {prefix!r}"]
    rel = raw[len(prefix) :]
    path = PurePosixPath(rel)
    problems = []
    if not rel or path.is_absolute() or ".." in path.parts:
        problems.append(f"line {line_no}: path {raw!r} must stay inside {CODE_ROOT}/")
    elif path.parts[0] == CODE_ROOT:
        problems.append(
            f"line {line_no}: path {raw!r} starts with {CODE_ROOT}/; "
            f"paths are relative to {CODE_ROOT}/ itself"
        )
    return rel, problems


def validate_code_patch(code_patch: str) -> list[str]:
    """Every problem with a `code_patch` (empty list means it fits APIs §2.10)."""
    if not code_patch.strip():
        return ["code_patch is empty"]
    headers, problems = _parse(code_patch)
    if not headers:
        return ["no '--- a/<path>' / '+++ b/<path>' file headers found", *problems]
    for line_no, old, new in headers:
        if old == _DEV_NULL and new == _DEV_NULL:
            problems.append(f"line {line_no}: both sides are /dev/null")
        problems += _path_problems(line_no, old, "a/")[1]
        problems += _path_problems(line_no + 1, new, "b/")[1]
    return problems


def patch_paths(code_patch: str) -> list[str]:
    """The files a `code_patch` touches, relative to `device/`, sorted and de-duplicated.

    Paths that break the format (see `validate_code_patch`) are left out. A renamed
    file contributes both its old and new path.
    """
    headers, _ = _parse(code_patch)
    paths: set[str] = set()
    for line_no, old, new in headers:
        for raw, prefix in ((old, "a/"), (new, "b/")):
            rel, problems = _path_problems(line_no, raw, prefix)
            if rel and not problems:
                paths.add(rel)
    return sorted(paths)
