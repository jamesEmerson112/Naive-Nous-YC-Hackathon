"""heal: command-line front end to the LLM tools (healer/tools.py).

  heal                         list tools
  heal tools --json            JSON manifest (name, description, risk, input_schema) for a harness
  heal <tool> [key=value ...]  call a tool; values are parsed as JSON when possible
  heal <tool> --json '{...}'   call a tool with JSON arguments
Output is JSON on stdout. Exit status 1 if the result has "ok": false.
"""
import json
import re
import sys

from . import tools


def _parse_kv(items):
    args = {}
    for it in items:
        if "=" not in it:
            raise SystemExit("argument %r is not key=value" % it)
        k, v = it.split("=", 1)
        if re.fullmatch(r"-?(0|[1-9]\d{0,8})", v):          # plain integers; '0123' and '12E4' stay strings
            args[k] = int(v)
        elif re.fullmatch(r"-?\d+\.\d+", v):
            args[k] = float(v)
        elif v in ("true", "false", "null") or v[:1] in "[{":
            args[k] = json.loads(v)
        else:
            args[k] = v
    return args


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        w = max(len(n) for n in tools.TOOLS)
        for t in tools.TOOLS.values():
            print("  %-*s  [%s] %s" % (w, t["name"], t["risk"], t["description"].split(". ")[0]))
        return 0
    if argv[0] == "tools":
        if "--json" in argv:
            print(json.dumps(tools.manifest(), indent=2))
        else:
            for t in tools.manifest():
                print("%s [%s]\n    %s\n    params: %s\n" % (t["name"], t["risk"], t["description"],
                                                             json.dumps(t["input_schema"]["properties"])))
        return 0
    name, rest = argv[0], argv[1:]
    if rest[:1] == ["--json"]:
        args = json.loads(rest[1]) if len(rest) > 1 else {}
    else:
        args = _parse_kv(rest)
    res = tools.call(name, args)
    print(json.dumps(res, indent=2, default=str))
    return 1 if isinstance(res, dict) and res.get("ok") is False else 0
