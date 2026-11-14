"""apply.py <file> [--write] [--spec <py>] [--all] : 個別の規則（spec の SPECIFIC・EXTRA・FENCE）→ 共通の規則。"""
import sys, importlib.util
sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0])
import trlib
from rules_common import COMMON
f = sys.argv[1]
spec, extra, fence = [], [], False
if "--spec" in sys.argv:
    p = sys.argv[sys.argv.index("--spec") + 1]
    sp = importlib.util.spec_from_file_location("spec", p); m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    spec, extra, fence = getattr(m, "SPECIFIC", []), getattr(m, "EXTRA", []), getattr(m, "FENCE", False)
text = open(f, encoding="utf-8").read()
trlib.PROTECT = getattr(m, "PROTECT", []) if "--spec" in sys.argv else []
drop = getattr(m, "DROP", []) if "--spec" in sys.argv else []
rules = spec + [(p, r) for p, r in COMMON if not any(d in p for d in drop)]
new, counts, shown = trlib.apply(text, rules, extra, fence, show={p for p, _ in rules})
if "--write" in sys.argv: open(f, "w", encoding="utf-8").write(new)
print(f"# {f}: {sum(n for _, n in counts)} か所")
if "--quiet" not in sys.argv:
    for s in shown: print(s)
