#!/usr/bin/env python3
"""シミュレーションの `kind = "experiment"` のトレーダーに渡す予測の作り置き（live-trading.md §0-7 (k)）。

    python simpredict.py make sim3 --jobs 8     # 出どころの日付 × 実験ごとに cli.predict を回して作り置く（出来ているものは飛ばす ＝ 再開できる）
    python simpredict.py status sim3            # 何日ぶん出来ているか

  - シミュレーションは「k 番目の仮の営業日 ＝ 出どころの k 番目の足」（simdata.build）。予測は **出どころの日付** で作り（`cli.predict --asof`）、
    運転手（simrun.py）が木を作るときに `install()` が行の日付を **仮の日付** に書き換えて `sim/<名前>/out/<仮の日付>/predict.jsonl` に置く。
    執行器（run_day.py）は既定の場所から読むだけ ＝ 本物と同じコードのまま。
  - 入力は研究用の `data/`（`AIL_DATA_DIR` を外して回す）＝ シミュレーションの値段の出どころと同じ足。⚠ **読むだけ**。
    予測の `proxy_close` とその日の気配を置くときに突き合わせ、食い違いを数える（日付の当て方のずれの検知）。
  - 作り置きは `sim-predict/<出どころの日付>/<実験>.jsonl`（git 管理外。`LT_SIM_PREDICT_DIR`）。`--fresh` で消える木の外に置く。
    LightGBM の無い機械（Sx360）へは titan で作ったこのディレクトリを写す。
⚠ tastytrade に繋がない・資格情報の `.env` を読まない。⚠ `runs/` を作らない（試行ではない ＝ 台帳の n_trials は動かない）。
⚠ 日付は仮・値段は過去の実物 ＝ 損益に意味は無い。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import simdata  # noqa: E402
from trader import load_traders  # noqa: E402

FD_DIR = os.path.normpath(os.path.join(HERE, "..", "feature-discovery"))
MARKS = {"sim": True, "mock": True, "test": True}


class SimPredictError(Exception):
    pass


def predict_dir() -> str:
    return os.environ.get("LT_SIM_PREDICT_DIR") or os.path.join(HERE, "sim-predict")


def experiment_models(traders) -> list[tuple[str, str | None]]:
    """トレーダーが使う (実験名, 手法)。同じものは 1 回だけ。"""
    seen: list[tuple[str, str | None]] = []
    for t in traders:
        for m in t.models:
            if m.kind == "experiment" and (m.name, m.method) not in seen:
                seen.append((m.name, m.method))
    return seen


def cache_path(base: str, source_date: str, experiment: str, method: str | None) -> str:
    """同じ実験を違う手法で使う人がいても取り違えないよう、手法があれば名前に短い指紋を足す。"""
    tag = f"{experiment}@{hashlib.sha1(method.encode('utf-8')).hexdigest()[:8]}" if method else experiment
    return os.path.join(base, source_date, f"{tag}.jsonl")


def read_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def plan_jobs(name: str, traders_dir: str | None = None, base: str | None = None,
              data_dir: str | None = None) -> tuple[list[tuple[str, str, str | None, str]], int]:
    """(まだ無い仕事の一覧 [(出どころの日付, 実験, 手法, 置き場)], 全部の数)。"""
    cfg = simdata.load_config(name)
    traders = load_traders(cfg.traders, traders_dir) if traders_dir else load_traders(cfg.traders)
    models = experiment_models(traders)
    symbols = sorted({s for t in traders for s in t.symbols})
    data = simdata.build(cfg, symbols, data_dir=data_dir or simdata.DATA_DIR)
    base = base or predict_dir()
    sources = sorted(set(data["source"].values()))
    todo = [(src, exp, method, cache_path(base, src, exp, method)) for src in sources for exp, method in models]
    return [j for j in todo if not os.path.exists(j[3])], len(todo)


def _predict_one(job: tuple[str, str, str | None, str], python: str) -> tuple[tuple, int, float, str]:
    src, exp, method, path = job
    os.makedirs(os.path.dirname(path), exist_ok=True)
    part = path + ".part"
    for p in (part, part + ".tmp"):
        if os.path.exists(p):
            os.remove(p)
    # ⚠ 研究用の data/ を読む（AIL_DATA_DIR を外す）。鍵・資格情報・モードの変数は子へ渡さない
    env = {k: v for k, v in os.environ.items() if k != "AIL_DATA_DIR" and not k.startswith(("TT_", "LT_"))}
    cmd = [python, "-m", "cli.predict", "--experiment", exp, "--asof", src, "--out", part, "--meta-out", path[:-6] + ".meta.json"]
    if method:
        cmd += ["--method", method]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=FD_DIR, env=env, capture_output=True, text=True)
    ok = proc.returncode == 0 and os.path.exists(part) and os.path.getsize(part) > 0
    if ok:
        os.replace(part, path)      # ⚠ 出来上がってから名前を付ける ＝ 途中で死んだものを「出来ている」と数えない
    return job, (0 if ok else proc.returncode or 1), time.time() - t0, (proc.stderr or proc.stdout or "").strip()[-400:]


def make(name: str, jobs: int = 8, python: str | None = None, limit: int | None = None) -> int:
    python = python or os.path.join(FD_DIR, ".venv", "bin", "python")
    todo, total = plan_jobs(name)
    print(f"[simpredict] {name}: 作り置き {total - len(todo)}/{total}・これから {len(todo)} 本（{jobs} 並列）→ {predict_dir()}", flush=True)
    if limit is not None:
        todo = todo[:limit]
    failed, t0 = 0, time.time()
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [pool.submit(_predict_one, j, python) for j in todo]
        for n, fut in enumerate(as_completed(futures), 1):
            (src, exp, _method, _path), rc, sec, tail = fut.result()
            failed += rc != 0
            print(f"[simpredict] {n:>3}/{len(todo)} {src} {exp} rc={rc} {sec:.0f}s" + (f"  {tail}" if rc else ""), flush=True)
    print(f"[simpredict] 終わり: 失敗 {failed}・{time.time() - t0:.0f} 秒", flush=True)
    return 1 if failed else 0


def install(root: str, cfg: simdata.SimConfig, traders_dir: str, data: dict, base: str | None = None) -> dict | None:
    """作り置きを仮の日付に書き換えて木に置く。`experiment` のモデルが無ければ何もしない（None）。

    ⚠ 1 日でも欠けていたら止まる（予測の無い日は「合図なし」で静かに何もしない ＝ 通ったように見えてしまう）。
    """
    traders = load_traders(cfg.traders, traders_dir)
    models = experiment_models(traders)
    if not models:
        return None
    base = base or predict_dir()
    missing = [(data["source"][day], exp) for day in data["days"] for exp, method in models
               if not os.path.exists(cache_path(base, data["source"][day], exp, method))]
    if missing:
        head = ", ".join(f"{s} {e}" for s, e in missing[:4])
        raise SimPredictError(f"予測の作り置きが {len(missing)} 本足りない（{head}{' …' if len(missing) > 4 else ''}）。"
                              f"先に python simpredict.py make {cfg.name}（LightGBM の無い機械では titan の {os.path.basename(base)}/ を写す）")
    rows_total, mismatch, no_quote = 0, [], 0
    for day in data["days"]:
        src = data["source"][day]
        out_rows = []
        for exp, method in models:
            for row in read_rows(cache_path(base, src, exp, method)):
                if row.get("date") != src:
                    raise SimPredictError(f"作り置き {src} {exp} の行の日付が {row.get('date')!r}（出どころの日付と違う）")
                quote = data["quotes"][day].get(row["symbol"])
                close = row.get("proxy_close")
                if quote is None:
                    no_quote += 1                       # そのトレーダーが持たない銘柄の行（T2 以外の実験は 63 本ぶん出す）
                elif close is not None and abs(float(close) - quote) > 1e-6 * max(1.0, abs(quote)):
                    mismatch.append({"date": day, "source_date": src, "symbol": row["symbol"], "proxy_close": close, "quote": quote})
                out_rows.append({**row, "date": day, "source_date": src, **MARKS})
        day_dir = os.path.join(root, "out", day)
        os.makedirs(day_dir, exist_ok=True)
        with open(os.path.join(day_dir, "predict.jsonl"), "w", encoding="utf-8") as f:
            for row in out_rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        rows_total += len(out_rows)
    summary = {"sim": True, "days": len(data["days"]), "models": [{"experiment": e, "method": m} for e, m in models], "rows": rows_total,
               "rows_without_quote": no_quote, "close_mismatch": len(mismatch), "close_mismatch_head": mismatch[:10], "predict_dir": base}
    os.makedirs(os.path.join(root, "sim"), exist_ok=True)
    with open(os.path.join(root, "sim", "predict_install.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description="シミュレーションの experiment トレーダーに渡す予測の作り置き")
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make")
    mk.add_argument("name")
    mk.add_argument("--jobs", type=int, default=8)
    mk.add_argument("--limit", type=int, default=None, help="この回に作る本数（試し）")
    mk.add_argument("--python", default=None, help="feature-discovery の Python（既定はその .venv）")
    st = sub.add_parser("status")
    st.add_argument("name")
    args = ap.parse_args()
    if args.cmd == "make":
        return make(args.name, args.jobs, args.python, args.limit)
    todo, total = plan_jobs(args.name)
    print(f"{args.name}: 作り置き {total - len(todo)}/{total}（{predict_dir()}）" + (f"・無いものの先頭 {todo[0][0]} {todo[0][1]}" if todo else ""))
    return 0 if not todo else 1


if __name__ == "__main__":
    sys.exit(main())
