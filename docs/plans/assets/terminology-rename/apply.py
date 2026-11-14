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
if "--spec" in sys.argv and hasattr(m, "PRE"): text = m.PRE(text)
drop = getattr(m, "DROP", []) if "--spec" in sys.argv else []
rules = spec + [(p, r) for p, r in COMMON if not any(d in p for d in drop)]
new, counts, shown = trlib.apply(text, rules, extra, fence, show={p for p, _ in rules})
if "--write" in sys.argv: open(f, "w", encoding="utf-8").write(new)
print(f"# {f}: {sum(n for _, n in counts)} か所")
RISKY = ("門(", "帯", "束", "尻", "種", "機械", "道", "木", "印", "器", "口", "段", "控え", "写し", "置き場", "土台", "切れ目", "札", "日誌", "指紋")
if "--risky" in sys.argv:
    import collections
    c = collections.Counter(x.split(" ", 1)[1].strip()[:64] for x in shown if any(k in x.split(" ", 1)[0] for k in RISKY))
    for x, n in sorted(c.items(), key=lambda kv: kv[0][kv[0].find("【"):]): print(f"{n:3d} {x}")
elif "--quiet" not in sys.argv:
    for s in shown: print(s)
