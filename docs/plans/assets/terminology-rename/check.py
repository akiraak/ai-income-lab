"""check.py <file>... : HEAD と比べて ① 行数が同じ ② ASCII が変わっていない ③ 替わった見出し（旧 → 新）を出す。"""
import subprocess, sys, re
strip = lambda s: re.sub(r"[^\x00-\x7f]", "", s)
for f in sys.argv[1:]:
    old = subprocess.run(["git", "show", f"HEAD:{f}"], capture_output=True, text=True).stdout.split("\n")
    new = open(f, encoding="utf-8").read().split("\n")
    if len(old) != len(new): print(f"⚠ {f}: 行数が違う {len(old)} → {len(new)}"); continue
    bad = [i + 1 for i, (a, b) in enumerate(zip(old, new)) if strip(a) != strip(b)]
    ch = sum(a != b for a, b in zip(old, new))
    print(f"{f}: 変わった行 {ch} ／ ASCII が変わった行 {bad[:12]}")
    for a, b in zip(old, new):
        if a != b and a.lstrip().startswith("#"): print(f"  H\t{a.strip()}\t{b.strip()}")
