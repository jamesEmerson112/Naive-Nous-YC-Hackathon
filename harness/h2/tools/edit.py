#!/usr/bin/env python3
"""Replace one exact piece of text in a file.

  python3 ../harness/tools/edit.py FILE 'old text' 'new text'

The old text must appear in FILE exactly once, spaces included. Use '' as the new text to delete it.
Python and TOML files are checked after the edit; an edit that would break them is refused.
"""
import difflib
import sys
import tomllib
from pathlib import Path


def closest(text, old):
    """The file's lines that look most like the first line of `old`, numbered."""
    lines = text.splitlines()
    first = next((line for line in old.splitlines() if line.strip()), old).strip()
    best = sorted(range(len(lines)), key=lambda i: -difflib.SequenceMatcher(None, lines[i].strip(), first).ratio())
    return [f"{i + 1:4}| {lines[i]}" for i in sorted(best[:3])]


def broken(path, text):
    try:
        if path.suffix == ".py":
            compile(text, str(path), "exec")
        elif path.suffix == ".toml":
            tomllib.loads(text)
    except (SyntaxError, tomllib.TOMLDecodeError) as e:
        return f"{type(e).__name__}: {e}"
    return None


def main():
    if len(sys.argv) != 4:
        print(__doc__.strip())
        sys.exit(2)
    path, old, new = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    if not path.is_file():
        sys.exit(f"edit.py: there is no file {path}. Nothing changed.")
    text = path.read_text()
    if old and text.count(old) == 0 and "\\n" in old:  # small models often write \n instead of a line break
        unescaped = old.replace("\\n", "\n").replace("\\t", "\t")
        if text.count(unescaped) == 1:
            old, new = unescaped, new.replace("\\n", "\n").replace("\\t", "\t")
    count = text.count(old) if old else 0
    if count == 0:
        print(f"edit.py: the old text is not in {path}. Nothing changed. The closest lines are:")
        print("\n".join(closest(text, old)))
        sys.exit("Copy the old text exactly from `cat -n` output, spaces included, without the line numbers.")
    if count > 1:
        sys.exit(f"edit.py: the old text appears {count} times in {path}. Nothing changed. "
                 "Include more of the lines around it so it appears once.")
    updated = text.replace(old, new)
    error = broken(path, updated)
    if error:
        sys.exit(f"edit.py: that edit would break {path} ({error}). Nothing changed.")
    path.write_text(updated)
    print("\n".join(difflib.unified_diff(text.splitlines(), updated.splitlines(), str(path), str(path),
                                         n=1, lineterm="")))
    print(f"edit.py: edited {path}. Now run python3 ../harness/tools/health.py")


if __name__ == "__main__":
    main()
