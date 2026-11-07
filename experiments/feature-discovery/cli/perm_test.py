"""fold の内側の並べ替え検定（登録簿 `20261025-permutation-yardstick`・買い% の保存の段 2）。⚠ **判定には使わない。**

    python3 -m cli.perm_test --run <実行> [--force-print]     # 1 本を計算して DB に入れる（既にあれば読むだけ）
    python3 -m cli.perm_test --missing [--jobs 8]             # buy_pct.parquet のある実行のうち、まだのものを全部
    python3 -m cli.perm_test --list                           # 計算済み ／ まだ の一覧

作り方（水準は事前固定 ＝ [プラン save-buy-pct.md §6](../../../docs/plans/archive/save-buy-pct.md)）:
  - 材料は実行が残した行ごとの買い% ／ 出口%（`buy_pct.parquet`。2026-11-01 から・leak の実行には無い）と、その実行が読んだ表の `y`。
  - 各 fold の評価分割の日付を 5 営業日の塊に切り、塊の順を入れ替える（種 0・200 回）。⚠ **全銘柄に同じ日付の入れ替えを当てる**
    ＝ 銘柄ごとの買い% の集まりと保有日率は保ち、「日付の並び」だけを壊す。買い% と出口% は同じ行の組で動かす。
    入れ替え先の日にその銘柄の行が無ければ元の値のまま（数を `fallback_rows` に残す）。
  - 器は `cli.perm_split.fold_net`（`cli.run.evaluate_trading._row` の素の枝の写し）。元の純利を `result.csv` と突き合わせ、
    合わない手法（役割つきの組・最短保有・保有日数の口など）は「再現せず」で飛ばす。
  - fold ごと: 上乗せ ＝ 純利 − 基準 常に上 の純利。分位 ＝（並べ替え < 元 ＋ 同点 × 0.5）÷ n。
    片側の p ＝（並べ替え ≥ 元 の数 ＋ 1）÷（n ＋ 1）。合成 ＝ Fisher（−2 Σ ln p ～ χ²(2 × fold 数)）の片側の p。

⚠ **検証結果一覧の列 2 本（並べ替え分位・並べ替えp）の材料。判定・`n_trials`・既存の列は変えない**（登録簿の failure_modes 4）。
⚠ 結果は DB の `outputs` の `perm/n200_b5_s0/<実行>.json`（上書きしない ＝ 閉じた実行は変わらない）。`cli.report` は読むだけ。
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from ail import rundb, runs
from cli import perm_split as ps

N = 200            # ⚠ 事前固定（登録簿）
BLOCK = 5          # ⚠ 事前固定（営業日）
SEED = 0
PREFIX = f"perm/n{N}_b{BLOCK}_s{SEED}/"


def cache_path(run: str) -> str:
    return f"{PREFIX}{run}.json"


def read_cache(run: str, conn=None) -> dict | None:
    b = rundb.get_output(conn or rundb.shared(runs.db_path()), cache_path(run))
    return None if b is None else json.loads(b.decode("utf-8"))


def cached_runs(conn=None) -> set[str]:
    return {p[len(PREFIX):-len(".json")] for p in rundb.list_outputs(conn or rundb.shared(runs.db_path()), PREFIX)}


# ---------------------------------------------------------------- 入れ替え

def block_order(n_days: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """日の番号 0..n−1 を block 日の塊に切り、塊の順を入れ替えた並び（i 番目の日は order[i] 番目の日の値を借りる）。"""
    blocks = [np.arange(i, min(i + block, n_days)) for i in range(0, n_days, block)]
    perm = rng.permutation(len(blocks))
    return np.concatenate([blocks[j] for j in perm]) if n_days else np.arange(0)


class FoldIndex:
    """1 fold の評価分割の (銘柄, 日) → 行の位置の表（入れ替えを速く当てるため）。"""

    def __init__(self, frame: pd.DataFrame, idx: np.ndarray):
        sub = frame.iloc[idx]
        ts = pd.to_datetime(sub["ts"]).to_numpy()
        self.days = np.unique(ts)
        self.dcode = np.searchsorted(self.days, ts)
        syms, self.scode = np.unique(sub["symbol"].astype(str).to_numpy(), return_inverse=True)
        self.grid = np.full((len(syms), len(self.days)), -1, dtype=np.int64)
        self.grid[self.scode, self.dcode] = np.arange(len(idx))

    def source(self, order: np.ndarray) -> tuple[np.ndarray, int]:
        """(行ごとの借り先の位置〔fold の中の番号〕, 借り先が無く元のままにした行の数)。"""
        src = self.grid[self.scode, order[self.dcode]]
        miss = src < 0
        own = np.arange(len(src))
        return np.where(miss, own, src), int(miss.sum())


# ---------------------------------------------------------------- 1 手法 × 1 θ

def fisher_p(ps_: list[float]) -> float:
    from scipy.stats import chi2

    p = np.clip(np.asarray(ps_, dtype=float), 1e-300, 1.0)
    return float(chi2.sf(-2.0 * np.log(p).sum(), 2 * len(p))) if len(p) else float("nan")


def perm_test(frame: pd.DataFrame, y: np.ndarray, method: str, th: float, cost_bp: float, drift: dict[int, float],
              n: int = N, block: int = BLOCK, seed: int = SEED, fidx: dict | None = None) -> dict:
    folds = ps.fold_index(frame)
    fidx = fidx or {f: FoldIndex(frame, idx) for f, idx in folds.items()}
    bp = frame[ps.BUY + method].to_numpy(dtype=float)
    ex = frame[ps.EXIT + method].to_numpy(dtype=float) if ps.EXIT + method in frame else None
    out = {"method": method, "theta": float(th), "edge_by_fold": {}, "q_by_fold": {}, "p_by_fold": {},
           "perm_mean_by_fold": {}, "fallback_rows": 0}
    for f, idx in folds.items():
        if f not in drift:
            continue
        fr = frame.iloc[idx].reset_index(drop=True)
        b, yy = bp[idx], y[idx]
        e = None if ex is None else ex[idx]
        orig = ps.fold_net(fr, b, e, yy, th, cost_bp)
        if orig is None:
            continue
        rng = np.random.default_rng([int(seed), int(f)])      # ⚠ 種は (seed, fold) ＝ 手法・θ をまたいで同じ入れ替え
        fi = fidx[f]
        dist = np.empty(n)
        for i in range(n):
            src, miss = fi.source(block_order(len(fi.days), block, rng))
            out["fallback_rows"] += miss
            v = ps.fold_net(fr, b[src], None if e is None else e[src], yy, th, cost_bp)
            dist[i] = np.nan if v is None else v - drift[f]
        o = orig - drift[f]
        d = dist[np.isfinite(dist)]
        out["edge_by_fold"][int(f)] = float(o)
        out["q_by_fold"][int(f)] = ps.quantile(o, d)
        out["p_by_fold"][int(f)] = float(((d >= o).sum() + 1) / (len(d) + 1))
        out["perm_mean_by_fold"][int(f)] = float(d.mean()) if len(d) else float("nan")
    out["edge_bp"] = float(np.mean(list(out["edge_by_fold"].values()))) if out["edge_by_fold"] else float("nan")
    out["fisher_p"] = fisher_p(list(out["p_by_fold"].values()))
    return out


# ---------------------------------------------------------------- 実行

def analyse_run(run: str, n: int = N, block: int = BLOCK, seed: int = SEED, log=print) -> dict:
    frame = runs.read_parquet(run)
    if frame is None:
        raise SystemExit(f"⚠ {run}: buy_pct.parquet が無い（2026-11-01 より前の実行 ／ leak の実行）")
    cfg = runs.read_json(run, "config.json") or {}
    inputs = runs.read_json(run, "inputs.json") or {}
    res = runs.read_csv(run, "result.csv", float_precision="round_trip")
    cost_bp = float(cfg.get("cost_bp", 5.0))
    thetas = [float(x) for x in (cfg.get("trading") or {}).get("thresholds", (50.0, 55.0, 60.0))]
    have = [c[len(ps.BUY):] for c in frame.columns if c.startswith(ps.BUY)]
    methods = [m for m in have if not m.startswith("基準 ")]     # 基準線の行は数えない ＝ 列も埋めない
    y = ps.load_y(run, frame, cfg, inputs)
    folds = ps.fold_index(frame)
    fidx = {f: FoldIndex(frame, idx) for f, idx in folds.items()}
    t0 = time.perf_counter()
    out = {"run": run, "n": n, "block_days": block, "seed": seed, "cost_bp": cost_bp, "rows": []}
    for th in thetas:
        drift = ps.fold_nets(frame, np.full(len(frame), 100.0), None, y, th, cost_bp, folds)
        for m in methods:
            ref = res[(res["手法"] == m) & (res["閾値"] == th)].set_index("fold")["純利bp"]
            bp = frame[ps.BUY + m].to_numpy(dtype=float)
            ex = frame[ps.EXIT + m].to_numpy(dtype=float) if ps.EXIT + m in frame else None
            nets = ps.fold_nets(frame, bp, ex, y, th, cost_bp, folds)
            diff = max((abs(nets.get(int(f), np.nan) - float(v)) for f, v in ref.items()), default=np.nan)
            if not len(ref) or not np.isfinite(diff) or diff > ps.TOL_BP or set(nets) != {int(f) for f in ref.index}:
                out["rows"].append({"method": m, "theta": th, "reproduced": False, "max_diff_bp": float(diff)})
                continue
            doc = perm_test(frame, y, m, th, cost_bp, drift, n=n, block=block, seed=seed, fidx=fidx)
            doc.update(reproduced=True, max_diff_bp=float(diff))
            out["rows"].append(doc)
    out["seconds"] = time.perf_counter() - t0
    log(f"{run}: {sum(r['reproduced'] for r in out['rows'])} ／ {len(out['rows'])} 行・{out['seconds']:.0f} 秒")
    return out


def _worker(run: str) -> tuple[str, str | None, str | None]:
    try:
        return run, json.dumps(analyse_run(run, log=lambda *a: None), ensure_ascii=False, default=float), None
    except BaseException as e:                     # noqa: BLE001 ＝ 1 本の失敗で全部を止めない
        return run, None, f"{type(e).__name__}: {e}"


def compute(names: list[str], jobs: int = 1, log=print) -> int:
    conn = rundb.connect(runs.db_path())
    done = 0
    if jobs <= 1:
        it = map(_worker, names)
    else:
        from multiprocessing import get_context

        pool = get_context("spawn").Pool(jobs)
        it = pool.imap_unordered(_worker, names)
    for run, text, err in it:
        if err:
            log(f"⚠ {run}: {err}")
            continue
        rundb.put_output(conn, cache_path(run), text.encode("utf-8"))
        d = json.loads(text)
        done += 1
        log(f"✅ {run}: {sum(r['reproduced'] for r in d['rows'])} ／ {len(d['rows'])} 行・{d['seconds']:.0f} 秒")
    return done


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--run")
    g.add_argument("--missing", action="store_true")
    g.add_argument("--list", action="store_true")
    p.add_argument("--jobs", type=int, default=1)
    return p


def main() -> None:
    a = _parser().parse_args()
    have = ps.runs_with_buy_pct()
    done = cached_runs()
    if a.list:
        for r in have:
            print(("✅ " if r in done else "・ ") + r)
        return
    if a.run:
        run = runs.resolve(a.run)
        if run in done:
            print(json.dumps(read_cache(run), ensure_ascii=False, indent=1)[:4000])
            return
        compute([run])
        return
    todo = [r for r in have if r not in done]
    print(f"まだ {len(todo)} 本（済み {len(done & set(have))} 本）")
    t0 = time.perf_counter()
    n = compute(todo, jobs=a.jobs)
    print(f"{n} 本を入れた・{time.perf_counter() - t0:.0f} 秒")


if __name__ == "__main__":
    main()
