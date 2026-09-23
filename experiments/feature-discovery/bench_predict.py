"""「今日の買い%」の予測にかかる時間を測る（[プラン](../../docs/plans/predict-timing-13500t.md)）。

    AIL_DATA_DIR=data-live .venv/bin/python bench_predict.py --asof 2026-09-18,2026-09-22 --repeat 6 --out <置き場>/bench.jsonl
    .venv/bin/python bench_predict.py --compare a.jsonl b.jsonl      # 2 台の結果を並べる（時間と売買の判定の一致）

`run-live.sh` の予測の段と同じ形 ＝ トレーダーの設定から 実験 × 手法 を拾い、**`cli.predict` を並列に起こして全部終わるまで**を
1 回と数える（プラン §2-1）。1 回 1 行の JSON（経過時間・各本の `seconds`・指紋・全銘柄の買い% ／ 出口%）と、先頭に機械の仕様を 1 行。

⚠ **測るだけ**: `cli.predict` の `--out` は一時ディレクトリ（本物の `live.sqlite` にも `out/` にも書かない）。資格情報は読まない。
⚠ `cli.predict` と `cli.build.assemble`・`cli.run.fold_buy_pct` には触らない（別のプロセスとして起こすだけ）。
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import tomllib

FD = os.path.dirname(os.path.abspath(__file__))
TRADERS_DIR = os.path.join(FD, "..", "live-trading", "config", "traders")


def pick_models(traders: list[str], traders_dir: str) -> list[dict]:
    """`run-live.sh` と同じ拾い方: 各トレーダーの `kind = "experiment"` を (実験, 手法) で重複なく。θ も持っておく（判定の一致に使う）。"""
    seen: list[dict] = []
    for name in traders:
        with open(os.path.join(traders_dir, f"{name}.toml"), "rb") as f:
            doc = tomllib.load(f)
        for m in doc.get("models", []):
            if m.get("kind") != "experiment":
                continue
            key = (m["name"], m.get("method") or "")
            if all((s["experiment"], s["method"]) != key for s in seen):
                seen.append({"experiment": key[0], "method": key[1], "trader": name,
                             "threshold": float(doc.get("threshold", 50.0))})
    return seen


def machine() -> dict:
    def cpu_model() -> str | None:
        try:
            with open("/proc/cpuinfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("model name"):
                        return line.split(":", 1)[1].strip()
        except OSError:
            return None
        return None

    def mem_gib() -> float | None:
        try:
            with open("/proc/meminfo", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("MemTotal"):
                        return round(int(line.split()[1]) / 1024 / 1024, 1)
        except OSError:
            return None
        return None

    vers = {}
    for mod in ("numpy", "pandas", "sklearn", "lightgbm", "numba", "aeon"):
        try:
            vers[mod] = __import__(mod).__version__
        except Exception as exc:                     # 版が読めなくても測る
            vers[mod] = f"?({type(exc).__name__})"
    return {"kind": "machine", "cpu": cpu_model(), "cpus": os.cpu_count(), "mem_gib": mem_gib(),
            "python": platform.python_version(), "platform": platform.platform(), "packages": vers,
            "container": os.path.exists("/.dockerenv"),
            "numba_cache_dir": os.environ.get("NUMBA_CACHE_DIR"),
            "data_dir": os.environ.get("AIL_DATA_DIR"),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}


def run_once(models: list[dict], asof: str, tmp: str, i: int) -> dict:
    procs = []
    t0 = time.perf_counter()
    for n, m in enumerate(models):
        out, meta = os.path.join(tmp, f"p{i}_{n}.jsonl"), os.path.join(tmp, f"m{i}_{n}.json")
        cmd = [sys.executable, "-m", "cli.predict", "--experiment", m["experiment"], "--asof", asof,
               "--out", out, "--meta-out", meta]
        if m["method"]:
            cmd += ["--method", m["method"]]
        procs.append((m, out, meta, subprocess.Popen(cmd, cwd=FD, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                                     text=True)))
    each = []
    for m, out, meta, p in procs:
        _, err = p.communicate()
        done = time.perf_counter() - t0
        row = {"experiment": m["experiment"], "method": m["method"], "rc": p.returncode, "done_s": round(done, 2)}
        if p.returncode != 0:
            row["error"] = err.strip().splitlines()[-1:] if err else []
        else:
            with open(meta, encoding="utf-8") as f:
                md = json.loads(f.readline())
            with open(out, encoding="utf-8") as f:
                preds = [json.loads(line) for line in f if line.strip()]
            row.update({"seconds": md["seconds"], "input_fingerprint": md["input_fingerprint"],
                        "train_rows": md.get("train_rows"), "threshold": m["threshold"],
                        "buy": {r["symbol"]: r["buy"] for r in preds},
                        "exit": {r["symbol"]: r["exit"] for r in preds}})
        each.append(row)
    wall = time.perf_counter() - t0
    return {"kind": "run", "asof": asof, "i": i, "wall_s": round(wall, 2),
            "ok": all(r["rc"] == 0 for r in each), "models": each}


def summarize(runs: list[dict]) -> list[dict]:
    """asof ごとに 1 回目（冷えた状態）と 2 回目以降の中央値（温まった状態）。"""
    out = []
    for asof in dict.fromkeys(r["asof"] for r in runs):
        rs = [r for r in runs if r["asof"] == asof and r["ok"]]
        if not rs:
            out.append({"kind": "summary", "asof": asof, "ok": False})
            continue
        warm = [r["wall_s"] for r in rs[1:]]
        out.append({"kind": "summary", "asof": asof, "ok": True, "n": len(rs), "cold_s": rs[0]["wall_s"],
                    "warm_median_s": round(statistics.median(warm), 2) if warm else None,
                    "warm_min_s": min(warm) if warm else None, "warm_max_s": max(warm) if warm else None})
    return out


def compare(a_path: str, b_path: str) -> int:
    """2 台の結果を並べる。売買の判定（buy > θ ／ exit > θ）が 1 銘柄でも違えば rc=1（プラン §2-3）。"""
    def load(p):
        with open(p, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        mach = next((r for r in rows if r["kind"] == "machine"), {})
        # asof × 実験 ごとに最後の成功した回（中身は回によらず同じはず ＝ 回の間の揺れも別に見る）
        last, drift = {}, []
        for r in rows:
            if r["kind"] != "run":
                continue
            for m in r["models"]:
                if m["rc"] != 0:
                    continue
                k = (r["asof"], m["experiment"], m["method"])
                if k in last and last[k]["buy"] != m["buy"]:
                    drift.append(k)
                last[k] = m
        return mach, last, [r for r in rows if r["kind"] == "summary"], drift

    ma, la, sa, da = load(a_path)
    mb, lb, sb, db = load(b_path)
    print(f"A: {ma.get('cpu')}（{ma.get('cpus')} 論理 CPU・{ma.get('mem_gib')} GiB・Python {ma.get('python')}）")
    print(f"B: {mb.get('cpu')}（{mb.get('cpus')} 論理 CPU・{mb.get('mem_gib')} GiB・Python {mb.get('python')}）")
    for x, y in zip(sa, sb):
        print(f"  asof {x['asof']}: 冷えた A {x.get('cold_s')} 秒 ／ B {y.get('cold_s')} 秒 ・"
              f" 温まった中央値 A {x.get('warm_median_s')} 秒 ／ B {y.get('warm_median_s')} 秒")
    bad = 0
    for tag, d in (("A", da), ("B", db)):
        for k in d:
            print(f"  ⚠ {tag} の中で回ごとに買い% が揺れた: {k}")
            bad = 1
    for k in sorted(set(la) | set(lb)):
        if k not in la or k not in lb:
            print(f"  ⚠ 片方にしか無い: {k}")
            bad = 1
            continue
        x, y = la[k], lb[k]
        th = x["threshold"]
        fp = "同じ" if x["input_fingerprint"] == y["input_fingerprint"] else f"⚠ 違う {x['input_fingerprint']} ／ {y['input_fingerprint']}"
        syms = sorted(set(x["buy"]) | set(y["buy"]))
        maxdiff, flips = 0.0, []
        for s in syms:
            if s not in x["buy"] or s not in y["buy"]:
                flips.append(f"{s}(片方だけ)")
                continue
            maxdiff = max(maxdiff, abs(x["buy"][s] - y["buy"][s]), abs(x["exit"][s] - y["exit"][s]))
            if (x["buy"][s] > th) != (y["buy"][s] > th) or (x["exit"][s] > th) != (y["exit"][s] > th):
                flips.append(s)
        print(f"  {k[0]} {k[1]} {k[2] or ''}: 入力の指紋 {fp} ・ {len(syms)} 銘柄 ・ 最大の差 {maxdiff:.6g} ・"
              f" 判定が変わった銘柄 {flips or 'なし'}")
        bad |= bool(flips)
    return 1 if bad else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", help="YYYY-MM-DD をカンマ区切り")
    ap.add_argument("--repeat", type=int, default=6, help="asof ごとの回数（1 回目 ＝ 冷えた状態）")
    ap.add_argument("--traders", default="T1,T2,T3")
    ap.add_argument("--traders-dir", default=TRADERS_DIR)
    ap.add_argument("--out", help="結果の JSONL（1 行目に機械の仕様・最後に要約）")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="2 つの結果を並べる")
    args = ap.parse_args()

    if args.compare:
        sys.exit(compare(*args.compare))
    if not args.asof or not args.out:
        ap.error("--asof と --out が要る（または --compare A B）")

    models = pick_models([t.strip() for t in args.traders.split(",") if t.strip()], args.traders_dir)
    if not models:
        ap.error("実験のモデルが 1 本も無い")
    rows = [machine() | {"models": [{k: m[k] for k in ("trader", "experiment", "method")} for m in models]}]
    runs = []
    with tempfile.TemporaryDirectory(prefix="bench_predict_") as tmp:
        for asof in [a.strip() for a in args.asof.split(",") if a.strip()]:
            for i in range(args.repeat):
                r = run_once(models, asof, tmp, i)
                runs.append(r)
                detail = " ／ ".join(
                    f"{m['experiment']} {m['done_s']}秒" + ("" if m["rc"] == 0 else f"⚠ rc={m['rc']} {m.get('error')}")
                    for m in r["models"])
                print(f"asof {asof} #{i + 1}: {r['wall_s']} 秒（{detail}）", file=sys.stderr)
    rows += runs + summarize(runs)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    for s in summarize(runs):
        print(json.dumps(s, ensure_ascii=False), file=sys.stderr)
    sys.exit(0 if all(r["ok"] for r in runs) else 1)


if __name__ == "__main__":
    main()
