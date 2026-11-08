"""config.manager.yaml as `dotted.key<TAB>value` lines, for setup.sh's `hermes -p manager config set`.

    python3 config_pairs.py config.manager.yaml

Standard library only (setup.sh runs it with the Mac's python3, outside the arm-sim venv), and only for
that file's shape: nested maps indented by two spaces, scalar values, one-line flow lists, `#` comments.
A flow list comes out as JSON (`["terminal", "file"]`), the inline list literal `hermes config set`
accepts (docs/raw/hermes-llms-full.txt H:L7581-7585). Anything else (a multi-line list, a quoted value,
a tab) is refused, so a value is never set half-parsed.
"""
import json
import re
import sys

KEY = re.compile(r"^( *)([A-Za-z_][A-Za-z0-9_-]*):(?:\s+(.*))?$")


def pairs(text):
    """[(dotted key, value text)] in file order. Raises ValueError on a line this parser doesn't handle."""
    out, path = [], []                    # path: [(indent, key)] of the open sections
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if "\t" in line or '"' in line or "'" in line:
            raise ValueError(f"line {n}: tabs and quoted values aren't handled: {raw!r}")
        m = KEY.match(line)
        if not m:
            raise ValueError(f"line {n}: not `key: value`: {raw!r}")
        indent, key, value = len(m.group(1)), m.group(2), (m.group(3) or "").strip()
        if indent % 2:
            raise ValueError(f"line {n}: indent of {indent} spaces is not a multiple of 2: {raw!r}")
        while path and path[-1][0] >= indent:
            path.pop()
        if path and indent != path[-1][0] + 2 or not path and indent:
            raise ValueError(f"line {n}: indented {indent} spaces under {'.'.join(k for _, k in path) or 'nothing'}")
        if not value:
            path.append((indent, key))
            continue
        if value.startswith("["):
            if not value.endswith("]"):
                raise ValueError(f"line {n}: a flow list must close on its line: {raw!r}")
            items = [v.strip() for v in value[1:-1].split(",") if v.strip()]
            value = json.dumps(items)
        out.append((".".join([k for _, k in path] + [key]), value))
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python3 config_pairs.py config.manager.yaml")
    with open(sys.argv[1], encoding="utf-8") as f:
        for k, v in pairs(f.read()):
            print(f"{k}\t{v}")
