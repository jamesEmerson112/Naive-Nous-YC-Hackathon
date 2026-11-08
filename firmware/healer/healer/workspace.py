"""The firmware source the LLM may read and edit: var/workspace/pod, its own git repository.

History starts at tag v1.0 (the known-good release). Whatever was deployed
after that is a later commit, so `git log` / `git diff v1.0` show what changed,
just like on a real project. Builds embed `git describe --dirty`, so an image
built from uncommitted edits says so in its build ID.
"""
import re
import subprocess
from pathlib import Path

from . import config, images

LLM_AUTHOR = {"GIT_AUTHOR_NAME": "healer-agent", "GIT_AUTHOR_EMAIL": "healer@pod.local",
              "GIT_COMMITTER_NAME": "healer-agent", "GIT_COMMITTER_EMAIL": "healer@pod.local"}


class WorkspaceError(RuntimeError):
    pass


def ws():
    if not (config.WORKSPACE / ".git").exists():
        raise WorkspaceError("no workspace at %s; run: firmware/demo/scenario setup" % config.WORKSPACE)
    return config.WORKSPACE


def git(*args, env=None, check=True):
    import os
    p = subprocess.run(["git", "-C", str(ws()), *args], capture_output=True, text=True,
                       env=(os.environ | env) if env else None)
    if check and p.returncode != 0:
        raise WorkspaceError("git %s: %s" % (" ".join(args), (p.stderr or p.stdout).strip()))
    return p.stdout


def _path(rel):
    root = ws().resolve()
    p = (root / rel).resolve()
    if root != p and root not in p.parents:
        raise WorkspaceError("path escapes the workspace: %s" % rel)
    parts = p.relative_to(root).parts
    if parts and parts[0] in (".git", "build"):
        raise WorkspaceError("not allowed: %s" % rel)
    return p


def list_files(path="."):
    files = git("ls-files", "-co", "--exclude-standard").split()
    prefix = "" if path in (".", "", "/") else path.rstrip("/") + "/"
    out = []
    for f in files:
        if f.startswith(prefix):
            p = ws() / f
            out.append({"path": f, "lines": sum(1 for _ in open(p, errors="replace")) if p.is_file() else None})
    return out


def read(path, start=None, end=None):
    p = _path(path)
    if not p.is_file():
        raise WorkspaceError("no such file: %s" % path)
    lines = p.read_text(errors="replace").splitlines()
    s = max(1, int(start or 1))
    e = min(len(lines), int(end or len(lines)))
    return {"path": path, "total_lines": len(lines), "start": s, "end": e,
            "text": "\n".join("%5d  %s" % (i, lines[i - 1]) for i in range(s, e + 1))}


def search(pattern, path=None, ignore_case=False):
    args = ["grep", "-n", "-E", "--untracked"] + (["-i"] if ignore_case else []) + ["-e", pattern]
    if path:
        args += ["--", path]
    out = git(*args, check=False)
    hits = []
    for line in out.splitlines()[:200]:
        m = re.match(r"([^:]+):(\d+):(.*)", line)
        if m:
            hits.append({"path": m.group(1), "line": int(m.group(2)), "text": m.group(3)})
    return hits


def edit(path, old, new, replace_all=False):
    p = _path(path)
    text = p.read_text()
    n = text.count(old)
    if n == 0:
        raise WorkspaceError("old text not found in %s (it must match exactly, including whitespace)" % path)
    if n > 1 and not replace_all:
        raise WorkspaceError("old text occurs %d times in %s; add context to make it unique or set replace_all" % (n, path))
    p.write_text(text.replace(old, new) if replace_all else text.replace(old, new, 1))
    return {"path": path, "replacements": n if replace_all else 1, "diff": git("diff", "--", path)}


def write(path, content):
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    p.write_text(content)
    return {"path": path, "created": not existed, "bytes": len(content)}


def diff(rev_a=None, rev_b=None, path=None, stat=False):
    args = ["diff"] + (["--stat"] if stat else [])
    revs = [r for r in (rev_a, rev_b) if r]
    args += revs or ["HEAD"]          # no revs: everything uncommitted, staged or not
    if path:
        args += ["--", path]
    return git(*args)


def log(n=15):
    return git("log", "--decorate", "--date=iso", "-n", str(n),
               "--format=%h %ad %an%d%n    %s")


def show(rev, stat_only=False):
    return git("show", "--stat" if stat_only else "--patch", "--format=%h %ad %an%d%n%n    %s%n%n%b", "--date=iso", rev)


def status():
    return {"head": git("describe", "--always", "--dirty", "--tags").strip(),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD").strip(),
            "changes": git("status", "--short")}


def commit(message):
    git("add", "-A")
    if not git("status", "--porcelain").strip():
        raise WorkspaceError("nothing to commit")
    git("commit", "-q", "-m", message, env=LLM_AUTHOR)
    return {"commit": git("rev-parse", "--short", "HEAD").strip(), "describe": git("describe", "--always", "--tags").strip()}


def revert(path=None):
    if path:
        _path(path)
        git("checkout", "HEAD", "--", path)
    else:
        git("checkout", "HEAD", "--", ".")
        git("clean", "-fdq", "-e", "build")
    return status()


def restore(rev):
    """Make the working tree match `rev` (files only; history and branch stay). Commit to keep it."""
    git("rev-parse", "--verify", rev + "^{commit}")
    git("checkout", rev, "--", ".")
    return status()


def build(clean=False):
    root = ws()
    if clean:
        subprocess.run(["make", "-C", str(root), "clean"], capture_output=True)
    p = subprocess.run(["make", "-C", str(root), "TOOLKIT_DIR=" + str(config.TOOLKIT)],
                       capture_output=True, text=True, timeout=300)
    out = p.stdout + p.stderr
    warnings = [l for l in out.splitlines() if "warning:" in l]
    errors = [l for l in out.splitlines() if "error" in l.lower() and ("error:" in l or "Error" in l)]
    mem = {k: " ".join(v.split()) for k, v in re.findall(r"^\s+(FLASH|RAM):\s+(.+?%)\s*$", out, re.M)}
    res = {"ok": p.returncode == 0, "warnings": warnings, "errors": errors[:30], "memory": mem}
    if p.returncode != 0:
        res["log_tail"] = out[-3000:]
        return res
    elf = root / "build" / "pod.elf"
    st = status()
    meta = images.register(elf, status="candidate", origin="workspace", source_rev=st["head"],
                           patch=git("diff", "HEAD"), note="built from workspace %s" % st["head"],
                           src_dir=root / "src")
    res["image"] = images.public(meta)
    return res
