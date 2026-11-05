"""上乗せを「いつ持つか」と「どれを持つか」に割る並べ替えの物差し（登録簿 `20261104-perm-split-timing`）。⚠ **読むだけ。**

    python3 -m cli.perm_split --run <実行> [--method <手法>] [--theta 50] [--n 200] [--seed 0] [--json out.json] [--save]
    python3 -m cli.perm_split --all [--limit N] [--n 200]          # buy_pct.parquet のある実行を順に
    python3 -m cli.perm_split --list                                # 当てられる実行の一覧だけ

材料は実行が残した行ごとの買い% ／ 出口%（`buy_pct.parquet`。2026-11-01 から・leak の実行には無い）と、その実行が読んだ表の `y`。
  (a) perm_xs ＝ 同じ fold・同じ日の中で、銘柄をまたいで買い% を並べ替える（日ごとの値の集まりは同じ ＝ タイミングを保ち、選択を壊す）
  (b) perm_ts ＝ 同じ fold・同じ銘柄の中で、日をまたいで並べ替える（銘柄ごとの値の集まりは同じ ＝ 選択を保ち、タイミングを壊す）
出口% を持つ手法は 買い% と出口% を同じ行の組のまま動かす。NaN の行は動かさない（器が銘柄ごと飛ばす集合を変えない）。
各 n 回・種は固定。器は `cli.run.evaluate_trading` の素の行と同じ呼び方（`simulate.simulate` を銘柄ごと → `portfolio_daily` → fold の和）
で、上乗せ ＝ fold ごとの（手法の純利 − 基準 常に上 の純利）の平均（`checks.compute_trading` の `edge_vs_bh` と同じ）。
分位 ＝ (並べ替えの上乗せ < 元 の数 ＋ 同点 × 0.5) ÷ n。B&H は買い% に依らないので、上乗せの分位 ＝ 純利の分位。

⚠ **元の純利を `result.csv` と突き合わせ、合わない手法は出さない**（役割つきの組・最短保有・保有日数の口などは
`_row` が `simulate` に任意引数を渡すので、ここでは再現しない ＝ 「再現せず」と書いて飛ばす）。
⚠ **採否には使わない・検証結果一覧に列を足さない**（列を足すかは利用者）。`n_trials` にも数えない。
⚠ `--save` のときだけ DB の `outputs` の `diag/<時刻>_perm_split/` に JSON を入れる（`runs.put_output`・上書きしない）。
"""

from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd

from ail import runs
from ail.data import store
from ail.validation import simulate as sim

DRIFT = "基準 常に上（ドリフト）"
BUY, EXIT = "買い%|", "出口%|"
KINDS = ("perm_xs", "perm_ts")
GROUP_BY = {"perm_xs": ("fold", "ts"), "perm_ts": ("fold", "symbol")}
TOL_BP = 1e-6                    # result.csv との突き合わせ（to_csv は往復できる桁で書くので、ふつうは 0 で合う）


# ---------------------------------------------------------------- 並べ替え

def group_codes(frame: pd.DataFrame, kind: str) -> np.ndarray:
    """並べ替えの組（(a) fold × 日 ／ (b) fold × 銘柄）の整数の番号。"""
    return frame.groupby(list(GROUP_BY[kind]), sort=True).ngroup().to_numpy()


def permute_within(values: np.ndarray, codes: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """`values`（行 × 列。列は同じ行の組のまま動かす）を、同じ番号の行の中だけで並べ替えた写し。

    NaN を含む行は動かさず、残りの行だけで並べ替える（組ごとの値の集まりは変わらない）。"""
    v = np.asarray(values, dtype=float)
    two = v.ndim == 2
    vv = v if two else v[:, None]
    ok = ~np.isnan(vv).any(axis=1)
    pos = np.flatnonzero(ok)
    g = codes[pos]
    u = rng.random(len(pos))
    src = pos[np.lexsort((u, g))]                  # 組ごとに乱数の順
    dst = pos[np.lexsort((pos, g))]                # 組ごとに元の順
    out = vv.copy()
    out[dst] = vv[src]
    return out if two else out[:, 0]


# ---------------------------------------------------------------- 器（cli.run の素の行と同じ呼び方）

def fold_net(fr: pd.DataFrame, bp: np.ndarray, ex: np.ndarray | None, y: np.ndarray,
             th: float, cost_bp: float) -> float | None:
    """1 fold ぶん（評価分割の並びのまま）の純利 bp。⚠ `cli.run.evaluate_trading._row` の素の枝の写し:
    銘柄ごとに `simulate(bp, y, θ, cost, exit_pct=ex)` → `portfolio_daily` → 和。NaN のある銘柄は飛ばす。"""
    ts = pd.to_datetime(fr["ts"])
    nets: dict[str, pd.Series] = {}
    for s, idx in fr.groupby("symbol").indices.items():
        if np.isnan(bp[idx]).any():
            continue
        if ex is not None and np.isnan(ex[idx]).any():
            continue
        r = sim.simulate(bp[idx], y[idx], th, cost_bp, exit_pct=None if ex is None else ex[idx])
        nets[str(s)] = pd.Series(r["net_bp"], index=ts.iloc[idx].values)
    if not nets:
        return None
    return float(sim.portfolio_daily(nets).sum())


def fold_nets(frame: pd.DataFrame, bp: np.ndarray, ex: np.ndarray | None, y: np.ndarray,
              th: float, cost_bp: float, folds: dict[int, np.ndarray]) -> dict[int, float]:
    out = {}
    for f, idx in folds.items():
        v = fold_net(frame.iloc[idx].reset_index(drop=True), bp[idx], None if ex is None else ex[idx],
                     y[idx], th, cost_bp)
        if v is not None:
            out[int(f)] = v
    return out


def fold_index(frame: pd.DataFrame) -> dict[int, np.ndarray]:
    return {int(f): idx for f, idx in frame.groupby("fold", sort=True).indices.items()}


def edge_of(nets: dict[int, float], drift: dict[int, float]) -> float:
    fs = sorted(set(nets) & set(drift))
    return float(np.mean([nets[f] - drift[f] for f in fs])) if fs else float("nan")


def quantile(orig: float, dist: np.ndarray) -> float:
    d = np.asarray(dist, dtype=float)
    return float(((d < orig).sum() + 0.5 * (d == orig).sum()) / len(d)) if len(d) else float("nan")


def perm_split(frame: pd.DataFrame, y: np.ndarray, method: str, th: float, cost_bp: float,
               n: int = 200, seed: int = 0, drift: dict[int, float] | None = None) -> dict:
    """1 手法 × 1 θ の分位。`drift` を省くと買い% 100 で器を回して作る（＝ 基準 常に上 の行と同じ）。"""
    folds = fold_index(frame)
    bp = frame[BUY + method].to_numpy(dtype=float)
    ex = frame[EXIT + method].to_numpy(dtype=float) if EXIT + method in frame else None
    if drift is None:
        drift = fold_nets(frame, np.full(len(frame), 100.0), None, y, th, cost_bp, folds)
    nets = fold_nets(frame, bp, ex, y, th, cost_bp, folds)
    orig = edge_of(nets, drift)
    vals = bp[:, None] if ex is None else np.column_stack([bp, ex])
    doc = {"method": method, "theta": float(th), "edge_bp": orig, "net_by_fold": nets, "n": int(n), "seed": int(seed)}
    for j, kind in enumerate(KINDS):
        rng = np.random.default_rng([int(seed), j])     # ⚠ 種は (seed, 種類) で固定 ＝ 片方だけ回しても同じ列
        codes = group_codes(frame, kind)
        dist = np.empty(n)
        t0 = time.perf_counter()
        for i in range(n):
            pv = permute_within(vals, codes, rng)
            dist[i] = edge_of(fold_nets(frame, pv[:, 0], None if ex is None else pv[:, 1], y, th, cost_bp, folds), drift)
        doc[kind] = {"quantile": quantile(orig, dist), "mean_bp": float(dist.mean()) if n else float("nan"),
                     "p05_bp": float(np.quantile(dist, 0.05)) if n else float("nan"),
                     "p95_bp": float(np.quantile(dist, 0.95)) if n else float("nan"),
                     "sec_per_perm": (time.perf_counter() - t0) / n if n else float("nan")}
    return doc


# ---------------------------------------------------------------- 実行を読む

def runs_with_buy_pct() -> list[str]:
    return [r for r in runs.list_runs() if runs.exists(r, "buy_pct.parquet")]


def load_y(run: str, frame: pd.DataFrame, cfg: dict, inputs: dict) -> np.ndarray:
    """実行が読んだ表の `y`（翌日始値 ／ 1 日遅れの引けの実行は `cli.run.next_open_returns` で継いだ損益）を行に揃える。"""
    path = os.path.join(store.ROOT, inputs["features_file"])
    if not os.path.exists(path):
        raise SystemExit(f"⚠ {run}: 表 {inputs['features_file']} が無い")
    panel = pd.read_parquet(path, columns=["ts", "symbol", "y"])
    col = "y"
    exe = (cfg.get("trading") or {}).get("execution", "close")
    if exe in ("next_open", "next_close"):
        from cli.run import EXEC_COLUMN, next_open_returns
        period = (inputs.get("features_meta") or {}).get("period") or "d"
        y_exec, _missing = next_open_returns(panel, store.adjusted_dir(period),
                                             column="open" if exe == "next_open" else "close")
        panel = panel.assign(**{EXEC_COLUMN: y_exec})
        col = EXEC_COLUMN
    key = panel[["ts", "symbol", col]].assign(ts=pd.to_datetime(panel["ts"], utc=True), symbol=panel["symbol"].astype(str))
    got = frame[["ts", "symbol"]].assign(ts=pd.to_datetime(frame["ts"], utc=True)).merge(key, on=["ts", "symbol"], how="left")
    if len(got) != len(frame) or got[col].isna().any():
        raise SystemExit(f"⚠ {run}: 表の y が買い% の行に揃わない（欠け {int(got[col].isna().sum())} 行）")
    return got[col].to_numpy(dtype=float)


def analyse_run(run: str, methods: list[str] | None = None, thetas: list[float] | None = None,
                n: int = 200, seed: int = 0, log=print) -> dict:
    frame = runs.read_parquet(run)
    if frame is None:
        raise SystemExit(f"⚠ {run}: buy_pct.parquet が無い（2026-11-01 より前の実行 ／ leak の実行）")
    cfg = runs.read_json(run, "config.json") or {}
    inputs = runs.read_json(run, "inputs.json") or {}
    res = runs.read_csv(run, "result.csv", float_precision="round_trip")   # ⚠ 既定の読み方は末尾の 1 ulp を落とす
    cost_bp = float(cfg.get("cost_bp", 5.0))
    th_all = [float(x) for x in (cfg.get("trading") or {}).get("thresholds", (50.0, 55.0, 60.0))]
    have = [c[len(BUY):] for c in frame.columns if c.startswith(BUY)]
    ms = methods or [m for m in have if not m.startswith("基準 ")]
    y = load_y(run, frame, cfg, inputs)
    folds = fold_index(frame)
    out = {"run": run, "cost_bp": cost_bp, "rows": []}
    for th in (thetas or th_all):
        drift = fold_nets(frame, np.full(len(frame), 100.0), None, y, th, cost_bp, folds)
        for m in ms:
            if m not in have:
                log(f"⚠ {run}: 手法 {m} の買い% が無い")
                continue
            ref = res[(res["手法"] == m) & (res["閾値"] == th)].set_index("fold")["純利bp"]
            bp = frame[BUY + m].to_numpy(dtype=float)
            ex = frame[EXIT + m].to_numpy(dtype=float) if EXIT + m in frame else None
            nets = fold_nets(frame, bp, ex, y, th, cost_bp, folds)
            diff = max((abs(nets.get(int(f), np.nan) - float(v)) for f, v in ref.items()), default=np.nan)
            if not len(ref) or not np.isfinite(diff) or diff > TOL_BP or set(nets) != set(int(f) for f in ref.index):
                log(f"⚠ {run}: {m} θ{th:g} は result.csv を再現しない（最大の差 {diff}）＝ 飛ばす")
                out["rows"].append({"method": m, "theta": th, "reproduced": False, "max_diff_bp": diff})
                continue
            doc = perm_split(frame, y, m, th, cost_bp, n=n, seed=seed, drift=drift)
            doc["reproduced"] = True
            doc["max_diff_bp"] = float(diff)
            doc["exact"] = bool(diff == 0.0)
            out["rows"].append(doc)
    return out


def table(docs: list[dict]) -> pd.DataFrame:
    rows = []
    for d in docs:
        for r in d["rows"]:
            row = {"実行": d["run"], "手法": r["method"], "θ": f"{r['theta']:g}"}
            if not r.get("reproduced"):
                rows.append({**row, "上乗せ": np.nan, "perm_xs": np.nan, "perm_ts": np.nan, "注": "再現せず"})
                continue
            rows.append({**row, "上乗せ": round(r["edge_bp"], 1),
                         "perm_xs": round(r["perm_xs"]["quantile"], 3), "xs平均": round(r["perm_xs"]["mean_bp"], 1),
                         "perm_ts": round(r["perm_ts"]["quantile"], 3), "ts平均": round(r["perm_ts"]["mean_bp"], 1),
                         "秒/回": round((r["perm_xs"]["sec_per_perm"] + r["perm_ts"]["sec_per_perm"]) / 2, 3),
                         "注": "" if r.get("exact") else f"差 {r['max_diff_bp']:.1e}"})
    return pd.DataFrame(rows)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--run", help="実行の名前（runs/<実行> も可）")
    g.add_argument("--all", action="store_true", help="buy_pct.parquet のある実行を全部")
    g.add_argument("--list", action="store_true", help="当てられる実行の一覧だけ出す")
    p.add_argument("--limit", type=int, default=None, help="--all のとき新しい順に N 本")
    p.add_argument("--method", action="append", default=None, help="手法（複数可。省くと `基準 ` 以外の全部）")
    p.add_argument("--theta", type=float, action="append", default=None, help="θ（複数可。省くと実行の全部）")
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--json", default=None, help="結果を JSON に書く道")
    p.add_argument("--save", action="store_true", help="DB の outputs の diag/<時刻>_perm_split/ にも入れる")
    return p


def main() -> None:
    a = _parser().parse_args()
    if a.list:
        for r in runs_with_buy_pct():
            print(r)
        return
    names = [runs.resolve(a.run)] if a.run else runs_with_buy_pct()
    if a.all and a.limit:
        names = names[-a.limit:]
    t0 = time.perf_counter()
    docs = [analyse_run(r, a.method, a.theta, n=a.n, seed=a.seed) for r in names]
    with pd.option_context("display.width", 200, "display.max_columns", 20, "display.unicode.east_asian_width", True):
        print(table(docs).to_string(index=False))
    print(f"\n並べ替え 各 {a.n} 回（種 {a.seed}）・{time.perf_counter() - t0:.1f} 秒。"
          "⚠ 分位は 0〜1（1 に近い ＝ 並べ替えると上乗せが減る）。採否には使わない")
    text = json.dumps({"n": a.n, "seed": a.seed, "runs": docs}, ensure_ascii=False, indent=1, default=float)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"→ {a.json}")
    if a.save:
        print("→ " + runs.put_output(f"diag/{time.strftime('%Y-%m-%dT%H-%M-%S')}_perm_split/perm_split.json", text))


if __name__ == "__main__":
    main()
