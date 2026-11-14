"""fix_where.py [--write] : glossary.toml の where（仕様書の見出しのコピー）に、その仕様書と同じ規則を当てる。
当てた後に、where の文字が仕様書の見出しにあるかを見る（当てる前から無かったものは「前から」と出す）。"""
import sys, re, importlib.util, subprocess
sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0])
import trlib
from rules_common import COMMON
A = sys.argv[0].rsplit("/", 1)[0]
SPEC = {"rules.md": "rules", "live-trading.md": "live", "dashboard.md": "dashboard", "dev-loop.md": "devloop", "howto-model-trader.md": "howto",
        "validation-power.md": "vpower", "units.md": "generic", "dsr.md": "generic", "reverse-trading.md": "generic", "holding-days.md": "generic", "ledger-role.md": "generic"}
def rules_for(doc):
    name = SPEC.get(doc.rsplit("/", 1)[-1])
    if not name: return None
    sp = importlib.util.spec_from_file_location("s", f"{A}/spec_{name}.py"); m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
    drop = getattr(m, "DROP", [])
    return getattr(m, "SPECIFIC", []) + [(p, r) for p, r in COMMON if not any(d in p for d in drop)]
def heads(text): return [l for l in text.split("\n") if l.lstrip().startswith("#")]
def found(piece, hs):
    t = re.sub(r"^§[0-9A-Za-z\-\.]+\s*", "", piece).strip()
    return (not t) or any(t in h for h in hs)
p = "dashboard/glossary.toml"; lines = open(p, encoding="utf-8").read().split("\n")
doc, n, miss = None, 0, []
for i, l in enumerate(lines):
    m = re.match(r'\s*doc\s*=\s*"([^"]*)"', l)
    if m: doc = m.group(1); continue
    m = re.match(r'(\s*where\s*=\s*")([^"]*)(".*)', l)
    if not m or not doc: continue
    rules = rules_for(doc)
    if rules is None: continue
    trlib.PROTECT = []
    new, _, _ = trlib.apply(m.group(2), rules)
    try:
        hs_new = heads(open(doc, encoding="utf-8").read())
        hs_old = heads(subprocess.run(["git", "show", f"HEAD:{doc}"], capture_output=True, text=True).stdout)
    except FileNotFoundError: continue
    for a, b in zip(m.group(2).split(" ／ "), new.split(" ／ ")):
        if not found(b, hs_new): miss.append((doc.rsplit("/", 1)[-1], b, "前から" if not found(a, hs_old) else "⚠ 新しく"))
    if new != m.group(2):
        n += 1; lines[i] = m.group(1) + new + m.group(3)
        print(f"  {m.group(2)}  →  {new}")
print(f"# where を替えた: {n} 行")
for d, b, k in miss: print(f"  見出しに無い（{k}）: {d}: {b}")
if "--write" in sys.argv: open(p, "w", encoding="utf-8").write("\n".join(lines))
