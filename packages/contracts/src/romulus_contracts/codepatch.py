"""Code packages and the `code_patch` format (APIs §2.10, D21).

- A **code version** is named by a `code_rev` string in the platform's registry (not a
  git ref). Its package is a `.tar.gz` of `device/` at that version, served by
  `GET /v1/code/{rev}/package` with its sha256 in the `X-Sha256` header. The device
  installs it like a harness package: hash check, health check, rollback (D7), then
  reports `InstallResult(type="code", version=<rev>, ...)`.
- A **`code_patch`** (in `Overlay.code_patch`) is a unified diff against the overlay's
  base `code_rev`, with paths relative to the `device/` root and the usual `a/` and `b/`
  prefixes: `--- a/control/gripper.py` / `+++ b/control/gripper.py`. New and deleted
  files use `/dev/null` on one side. `git diff` output (run inside `device/`) fits,
  including its `diff --git`, rename, copy and mode lines.
- **Also accepted (B3):** the same paths with a leading `device/` (`--- a/device/control/
  gripper.py`), which is what `git diff` run from the repo root gives. A leading `device/`
  always means the package root. `summarize_code_patch` reads it in one place
  (`_Collector.path`): `paths` come back relative to `device/` either way, so
  `touches_safety` and the `verify/` holdout see the same names, and `PatchSummary.strip`
  tells `git apply` how many components to drop. A patch that mixes the two forms is a
  problem (fail closed).
- The base `code_rev` is the code the device ran when it made the patch. Send it as
  `running.code_rev` in the incident (proposed; see README).

Patches are stored raw. These helpers *report* problems; nothing here rejects an incident.

**Fail closed (K17).** Any problem `summarize_code_patch` reports (a path it could not
parse, a path outside `device/`, an unprefixed path, a binary change, a symlink or
submodule (git mode 120000 or 160000), a truncated hunk) means the patch's file list is
not trustworthy, so `touches_safety` must be treated as True. `touches_safety()` below
implements exactly that rule; use it rather than checking `patch_paths()` against a prefix
list yourself.

Paths are read from every place git puts them: `diff --git a/X b/Y`, `--- a/X` /
`+++ b/Y`, `rename from/to`, `copy from/to` and `Binary files a/X and b/Y differ`.
C-quoted paths (`"a/vis\\303\\266r.py"`) are unquoted. Paths are normalized (`./` and `//`
collapse), but `..` is never normalized away: a path with `..`, or an absolute path, is a
problem and lands in `rejected_paths`. Standard library only.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

CODE_ROOT = "device"
"""The directory a code package contains and `code_patch` paths are relative to."""

MCU_ROOT = "firmware"
"""Top-level directory of STM32 C code, if it lives inside `device/` (open I0 question)."""

_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_DEV_NULL = "/dev/null"
# git mode lines. A symlink or a submodule gitlink is never a valid code_patch: applied on a
# host, a symlink makes later writes or reads escape the code dir.
# Any digit count: `git apply` reads the mode as octal, so `0120000` is a symlink too.
_MODE_LINE = re.compile(r"^(?:new file mode|deleted file mode|new mode|old mode) (\d+)\s*$")
_INDEX_MODE = re.compile(r"^index [0-9a-f]+\.\.[0-9a-f]+ (\d+)\s*$")
_FILE_TYPE = 0o170000
_LINK_MODES = {0o120000: "symlink", 0o160000: "submodule (gitlink)"}


def _mode_problem(raw: str) -> str | None:
    """Why a git mode can't be in a code_patch: a symlink, a gitlink, or not octal at all."""
    try:
        kind = _LINK_MODES.get(int(raw, 8) & _FILE_TYPE)
    except ValueError:
        return f"unreadable mode {raw!r}"
    return f"{kind} (mode {raw})" if kind else None


_ESCAPES = {"a": 7, "b": 8, "t": 9, "n": 10, "v": 11, "f": 12, "r": 13, '"': 34, "\\": 92}


@dataclass(frozen=True)
class PatchSummary:
    """What a `code_patch` touches, and everything wrong with it.

    - `paths`: files touched, normalized, relative to `device/`, sorted, unique.
      Includes best-effort readings of unprefixed paths (each also reported in `problems`).
    - `problems`: every format problem. Non-empty means fail closed (`touches_safety`).
    - `rejected_paths`: raw paths that cannot be placed inside `device/` (absolute, `..`).
    - `notes`: things worth surfacing that are not format problems (e.g. STM32 paths).
    - `root_prefixed`: the patch names files as `device/...` (`--- a/device/robot/x.py`, what
      `git diff` run from the repo root gives); `paths` already have that prefix removed.
    """

    paths: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    rejected_paths: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    root_prefixed: bool = False

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def strip(self) -> int:
        """`git apply -p<strip>` from inside `device/`: 2 drops `a/device/`, 1 drops `a/`."""
        return 2 if self.root_prefixed else 1


@dataclass
class _Collector:
    paths: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    forms: dict[str, str] = field(default_factory=dict)  # "prefixed"/"relative" -> first raw

    def path(self, line_no: int, raw: str | None, prefix: str | None) -> None:
        """Record one path as it appeared on line `line_no`; `prefix` is "a/", "b/" or None."""
        if raw is None or raw == _DEV_NULL:
            return
        rel = raw
        if prefix is not None:
            if raw.startswith(prefix):
                rel = raw[len(prefix) :]
            else:
                self.problems.append(f"line {line_no}: path {raw!r} should start with {prefix!r}")
        parts = PurePosixPath(rel).parts
        if not rel or rel.startswith("/") or ".." in parts:
            hint = ""
            if MCU_ROOT in parts:
                hint = f" (STM32 code outside {CODE_ROOT}/ is an open I0 question)"
            self.problems.append(
                f"line {line_no}: path {raw!r} must stay inside {CODE_ROOT}/{hint}"
            )
            if raw not in self.rejected:
                self.rejected.append(raw)
            return
        norm = str(PurePosixPath(rel))
        if norm == ".":
            self.problems.append(f"line {line_no}: path {raw!r} names no file")
            self.rejected.append(raw)
            return
        # The one place a leading `device/` is read (B3): it names the package root, so
        # `device/robot/x.py` is `robot/x.py`. Checked after `..` above, so it never escapes.
        form = "relative"
        if PurePosixPath(norm).parts[0] == CODE_ROOT:
            form, norm = "prefixed", str(PurePosixPath(*PurePosixPath(norm).parts[1:]))
            if norm == ".":
                self.problems.append(f"line {line_no}: path {raw!r} names no file")
                self.rejected.append(raw)
                return
        self.forms.setdefault(form, raw)
        top = PurePosixPath(norm).parts[0]
        if top == MCU_ROOT and norm not in self.paths:
            self.notes.append(f"{norm}: STM32 code; needs a rebuild and reflash")
        self.paths.add(norm)


def _unquote(s: str) -> tuple[str | None, str]:
    """Parse a git C-quoted string at the start of `s`. Returns (path or None, rest)."""
    out = bytearray()
    i = 1
    while i < len(s):
        c = s[i]
        if c == '"':
            return out.decode("utf-8", errors="replace"), s[i + 1 :]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt in _ESCAPES:
                out.append(_ESCAPES[nxt])
                i += 2
                continue
            if re.fullmatch(r"[0-7]{3}", s[i + 1 : i + 4]):
                out.append(int(s[i + 1 : i + 4], 8) & 0xFF)
                i += 4
                continue
            return None, s
        out += c.encode("utf-8")
        i += 1
    return None, s


def _header_path(s: str) -> str | None:
    """The path on a `---`/`+++` line (after the marker). Drops a trailing tab + timestamp."""
    if s.startswith('"'):
        path, _ = _unquote(s)
        return path
    return s.split("\t", 1)[0].rstrip()


def _strip_prefix(p: str) -> str:
    return p[2:] if len(p) > 2 and p[1] == "/" else p


def _diff_git_paths(rest: str) -> tuple[str, str] | None:
    """The two paths of a `diff --git <a> <b>` line, or None if they can't be told apart."""
    if rest.startswith('"'):
        a, tail = _unquote(rest)
        tail = tail.lstrip(" ")
        b = _unquote(tail)[0] if tail.startswith('"') else tail
        return (a, b) if a is not None and b else None
    if ' "' in rest:
        left, right = rest.split(' "', 1)
        b, _ = _unquote('"' + right)
        return (left, b) if b is not None else None
    spaces = [i for i, c in enumerate(rest) if c == " "]
    for i in spaces:  # same path on both sides (the usual, non-rename case)
        a, b = rest[:i], rest[i + 1 :]
        if _strip_prefix(a) == _strip_prefix(b):
            return a, b
    if len(spaces) == 1:
        return rest[: spaces[0]], rest[spaces[0] + 1 :]
    return None


def _binary_paths(line: str) -> tuple[str | None, str | None]:
    body = line[len("Binary files ") : -len(" differ")]
    if body.startswith('"'):
        a, tail = _unquote(body)
        tail = tail.removeprefix(" and ")
        b = _unquote(tail)[0] if tail.startswith('"') else tail
        return a, b
    if " and " in body:
        a, b = body.split(" and ", 1)
        return a, (_unquote(b)[0] if b.startswith('"') else b)
    return None, None


def summarize_code_patch(code_patch: str | None) -> PatchSummary:
    """Parse a `code_patch`: the files it touches and every problem with it."""
    if code_patch is None or not code_patch.strip():
        return PatchSummary(problems=("code_patch is empty",))
    c = _Collector()
    lines = [line.removesuffix("\r") for line in code_patch.split("\n")]
    saw_file = False
    i = 0
    while i < len(lines):
        line, n = lines[i], i + 1
        if line.startswith("diff --git "):
            saw_file = True
            pair = _diff_git_paths(line[len("diff --git ") :])
            if pair is None:
                c.problems.append(f"line {n}: can't read the paths in {line!r}")
            else:
                c.path(n, pair[0], "a/")
                c.path(n, pair[1], "b/")
        elif line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            saw_file = True
            for offset, prefix in ((0, "a/"), (1, "b/")):
                raw = _header_path(lines[i + offset][4:])
                if raw is None:
                    c.problems.append(f"line {n + offset}: can't read the quoted path")
                c.path(n + offset, raw, prefix)
            i += 2
            continue
        elif line.startswith(("rename from ", "rename to ", "copy from ", "copy to ")):
            saw_file = True
            raw = line.split(" ", 2)[2]
            path = _unquote(raw)[0] if raw.startswith('"') else raw
            if path is None:
                c.problems.append(f"line {n}: can't read the quoted path")
            c.path(n, path, None)
        elif line.startswith("Binary files ") and line.endswith(" differ"):
            saw_file = True
            a, b = _binary_paths(line)
            if a is None and b is None:
                c.problems.append(f"line {n}: can't read the paths in {line!r}")
            c.path(n, a, "a/")
            c.path(n, b, "b/")
            c.problems.append(f"line {n}: binary change; a code_patch must be a text diff")
        elif line == "GIT binary patch":
            c.problems.append(f"line {n}: binary change; a code_patch must be a text diff")
        elif mode := _MODE_LINE.match(line) or _INDEX_MODE.match(line):
            problem = _mode_problem(mode.group(1))
            if problem:
                c.problems.append(
                    f"line {n}: {problem}; a code_patch may only change regular files"
                )
        else:
            match = _HUNK.match(line)
            if match:
                if not saw_file:
                    c.problems.append(f"line {n}: hunk has no file header before it")
                old_left = int(match.group(1) or 1)
                new_left = int(match.group(2) or 1)
                i += 1
                while (old_left > 0 or new_left > 0) and i < len(lines):
                    body = lines[i]
                    if body.startswith("\\"):  # "\ No newline at end of file"
                        pass
                    elif body.startswith("-"):
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
                    c.problems.append(f"hunk at line {n} is shorter than its header says")
                continue
        i += 1
    if not saw_file:
        c.problems.insert(0, "no file headers found ('diff --git' or '--- a/<path>' / '+++ b/')")
    if len(c.forms) > 1:
        c.problems.append(
            f"paths mix the two forms ({c.forms['prefixed']!r} has the {CODE_ROOT}/ prefix, "
            f"{c.forms['relative']!r} doesn't); use one, so the patch applies one way"
        )
    return PatchSummary(
        paths=tuple(sorted(c.paths)),
        problems=tuple(c.problems),
        rejected_paths=tuple(c.rejected),
        notes=tuple(c.notes),
        root_prefixed=set(c.forms) == {"prefixed"},
    )


def validate_code_patch(code_patch: str | None) -> list[str]:
    """Every problem with a `code_patch` (empty list means it fits APIs §2.10)."""
    return list(summarize_code_patch(code_patch).problems)


def patch_paths(code_patch: str | None) -> list[str]:
    """The files a `code_patch` touches, relative to `device/`, sorted and de-duplicated.

    Not enough on its own for safety decisions: a path that cannot be placed inside
    `device/` is not listed here. Check `validate_code_patch` too, or use `touches_safety`.
    """
    return list(summarize_code_patch(code_patch).paths)


def is_under(path: str, prefix: str) -> bool:
    """True if `path` is `prefix` or inside it, compared after normalization and
    case-insensitively: on a case-insensitive filesystem (macOS, Docker Desktop mounts)
    `SAFETY/x.py` and `./safety/x.py` are the same file as `safety/x.py`."""
    pre = str(PurePosixPath(prefix.removeprefix("./"))).casefold()
    norm = str(PurePosixPath(path.removeprefix("./"))).casefold()
    return pre == "." or norm == pre or norm.startswith(pre + "/")


def touches_safety(code_patch: str | None, safety_paths: Iterable[str]) -> bool:
    """True if the patch touches any of `safety_paths` (files or directories under `device/`).

    **Fails closed:** any problem with the patch returns True, because its file list
    cannot be trusted (K17). An absent patch (None) touches nothing.
    """
    if code_patch is None:
        return False
    summary = summarize_code_patch(code_patch)
    if summary.problems or summary.rejected_paths:
        return True
    prefixes = list(safety_paths)
    return any(is_under(p, pre) for p in summary.paths for pre in prefixes)
