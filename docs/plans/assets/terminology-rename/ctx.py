import sys, collections
sys.path.insert(0, sys.argv[0].rsplit("/",1)[0])
import trlib
f = sys.argv[1]; pats = sys.argv[2:]
text = open(f, encoding="utf-8").read()
EXTRA = []
if f.endswith("CLAUDE.md"):
    EXTRA = [("- **技術用語は、プログラミングで普通に使われる言い方で書く", "✅ 実売買の執行器（`run_day.py`・`reconcile.py`・`recovery.py`）のメッセージは 2026-10-08 に「売買履歴」へ直した")]
for p in pats:
    c = trlib.contexts(text, p, EXTRA)
    print(f"=== {p}  ({len(c)})")
    for x, n in collections.Counter(c).items(): print("  " + x + (f"  ×{n}" if n > 1 else ""))
