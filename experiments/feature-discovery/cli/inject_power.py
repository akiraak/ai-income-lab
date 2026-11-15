"""本物のリターンに既知の上乗せを埋めて、シミュレーターと判定式がどこから「採る」を出すかを測る（レジストリ `20261108-signal-inject-power`）。⚠ **読むだけ。**

    python3 -m cli.inject_power [--kind iid|persist|oracle] [--experiment trade_own_ridge_a] [--seeds 5] [--json out.json] [--save]

`cli.planted_power`（レジストリ `20261106-planted-signal-power`）との違いは 1 つ: **リターンを合成（多変量正規）に差し替えず、本物の `y` のまま** 埋める
＝ 本物の太い裾・ボラの塊・fold ごとの偏りが残る（11/6 の版が「検出力を甘く見積もる側」と書いた穴）。

作り方（レジストリの structure・水準は回す前に固定）:
  - 本物の表（既定 `trade_own_ridge_a` ＝ own_2018・63 本）の行・特徴量の列・fold の分割点をそのまま使う。
  - 表の外で作った乱数の列 `own_planted`（行ごとに独立な N(0,1) ＝ 本物の列と相関しない）を足し、
    `y` ← 本物の `y` ＋ k bp × 列の符号（訓練分割と評価分割の両方）。⚠ 同じシードでは k をまたいで列が同じ（対の曲線）。
  - k の 3 段 ＝ 検出限界 L の 0.1 倍 ／ 1 倍 ／ 10 倍。**L ＝ 300 bp/fold ÷ 評価期間 1 つの営業日数の平均**
    （契約 §2「机上で測れるのは数百 bp/fold の上乗せ」の「数百」を 300 と読んだ ＝ 2026-11-13 回す前に固定。日数は表の分割点から数える）。
  - 各段 × θ 50 ／ 55 ／ 60 × シード 5。シミュレーター ・ 純利 ・ 判定は `cli.planted_power.one` をそのまま呼ぶ
    （素の own × Ridge ＝ `全部使う（基準）`・形式 (A)・B&H ＝ 買い% 100 の同じシミュレーター・採る ＝ 平均 > 0 で fold 全部が正）。

**種類 `--kind persist`（2026-11-14・レジストリ `20261114-persist-signal-power`。既定は `iid` ＝ 上の形のまま・既存の結果は変わらない）**:
植える列を、行ごとに独立な乱数から **何十日も続く局面（2 状態の対称なマルコフ連鎖の ±1）** に替える。読み手・シミュレーター・判定は同じ。
  - 連鎖: 1 日ごとに確率 1 ／ L で反対の状態へ（期待滞在 L ＝ 20 ／ 60 営業日）。最初の状態は ±1 を半々。
  - 列の単位 2 つ: `common` ＝ 表の日付の上に 1 本（その日の全銘柄が同じ状態）／ `each` ＝ 銘柄ごとに別の連鎖（その銘柄の行を日付順に詰めて数える）。
  - `y` ← 本物の `y` ＋ k bp × 状態。k ＝ L₀ の 0 ／ 0.1 ／ 1 ／ 10 倍（0 ＝ 列は足すが y は本物のまま）。
  - 対の相手 ＝ 同じ k の `iid`（同じ実行の中で回し直す）。出力に 持つ日の割合・取引回数（回/fold）・列が ＋1 の行の割合 を足す。
  - 読みは回す前に固定した（`docs/plans/archive/persist-signal-power.md` §2-1）。
  - `--kind oracle` ＝ **事後に足した読み**（persist の結果を見た後）: 学習器を通さず局面そのものを買い% にする（＋1 の日 100 ／ −1 の日 0）。
    読み手と、シミュレーター ＋ 判定のどちらが拾えないのかを分けるため。レジストリの structure の外。

⚠ **検証結果一覧に行を作らない・`n_trials` に数えない・`runs/` を作らない**（偽の信号の行 ＝ leak 対照 ・ 基準線と同じ扱い）。
⚠ `--save` のときだけ DB の `outputs` の `diag/<時刻>_inject_power/`（persist は `…_inject_power_persist/`）に JSON を入れる（`runs.put_output`・上書きしない）。
"""

from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from ail import config, runs
from ail.contracts import META_COLUMNS
from ail.validation import splits
from cli import planted_power as pp

LIMIT_BP_PER_FOLD = 300.0          # ⚠ 事前固定（契約 §2 の「数百 bp/fold」）
MULTIPLES = (0.1, 1.0, 10.0)       # ⚠ 事前固定（検出限界の 0.1 倍 ／ 1 倍 ／ 10 倍）
THETAS = (50.0, 55.0, 60.0)        # ⚠ 事前固定
SEEDS = 5                          # ⚠ 事前固定
# ---- 種類 persist（レジストリ 20261114-persist-signal-power。⚠ 事前固定 ＝ 回す前に決めた）
PERSIST_MULTIPLES = (0.0, 0.1, 1.0, 10.0)   # k = 0 ＝ 植えない行（列は足す）
UNITS = ("common", "each")                  # 全銘柄に共通の局面 ／ 銘柄ごとに独立な局面
DWELLS = (20, 60)                           # 期待滞在（営業日）


def inject(panel: pd.DataFrame, k_bp: float, seed: int) -> pd.DataFrame:
    """本物の y に k bp × 乱数の列の符号を足したコピー。⚠ シードが同じなら k によらず列は同じ。"""
    x = np.random.default_rng([int(seed), 20261108]).standard_normal(len(panel))
    y = panel["y"].to_numpy(dtype=float) + float(k_bp) * 1e-4 * np.sign(x)
    out = panel.assign(y=y, **{pp.PLANTED: x})
    if "y_sign" in out:
        out["y_sign"] = np.sign(y)
    return out


def chain(n: int, dwell: int, rng: np.random.Generator) -> np.ndarray:
    """2 状態の対称なマルコフ連鎖の ±1（長さ n）。1 歩ごとに確率 1 ／ dwell で反対へ ＝ 期待滞在 dwell。最初は ±1 を半々。"""
    if dwell < 1:
        raise ValueError(f"期待滞在 {dwell} は受けない（1 以上）")
    first = 1.0 if rng.random() < 0.5 else -1.0
    flip = rng.random(n) < 1.0 / float(dwell)
    if n:
        flip[0] = False
    return first * np.where(np.cumsum(flip) % 2 == 0, 1.0, -1.0)


def persist_column(panel: pd.DataFrame, seed: int, unit: str, dwell: int) -> np.ndarray:
    """表の行の並びのままの局面の列（±1）。⚠ k によらない（シード・単位・期待滞在だけで決まる）。"""
    if unit not in UNITS:
        raise ValueError(f"列の単位 {unit!r} は受けない（{' ／ '.join(UNITS)}）")
    rng = np.random.default_rng([int(seed), 20261114, int(dwell), UNITS.index(unit)])
    ts = pd.to_datetime(panel["ts"]).to_numpy()
    if unit == "common":
        days = np.sort(np.unique(ts))
        return chain(len(days), dwell, rng)[np.searchsorted(days, ts)]
    x = np.empty(len(panel), dtype=float)
    for _s, idx in sorted(panel.groupby("symbol").indices.items(), key=lambda kv: str(kv[0])):
        idx = idx[np.argsort(ts[idx], kind="stable")]
        x[idx] = chain(len(idx), dwell, rng)
    return x


def inject_persist(panel: pd.DataFrame, k_bp: float, seed: int, unit: str, dwell: int) -> pd.DataFrame:
    """本物の y に k bp × 局面（±1）を足したコピー。⚠ シードが同じなら k によらず列は同じ。"""
    x = persist_column(panel, seed, unit, dwell)
    y = panel["y"].to_numpy(dtype=float) + float(k_bp) * 1e-4 * x
    out = panel.assign(y=y, **{pp.PLANTED: x})
    if "y_sign" in out:
        out["y_sign"] = np.sign(y)
    return out


def eval_days(panel: pd.DataFrame, exp: dict, edges_dates) -> list[int]:
    v = exp.get("validation", {})
    return [int(te["ts"].nunique()) for _f, _tr, te in splits.folds_by_dates(
        panel, edges_dates, float(exp["horizon_min"]), int(v.get("embargo_bars", 0)), float(exp.get("bar_minutes", 0.0)))]


def levels(days: list[int]) -> tuple[float, list[float]]:
    """(検出限界 L〔bp/日〕, k の 3 段〔bp/日〕)。"""
    L = LIMIT_BP_PER_FOLD / float(np.mean(days))
    return L, [m * L for m in MULTIPLES]


def curve(experiment: str = "trade_own_ridge_a", seeds: int = SEEDS, thetas=THETAS, log=print) -> dict:
    from cli.run import load_panel

    exp = config.resolve_experiment(experiment)
    ds = exp["_dataset"]
    exp.setdefault("horizon_min", 1440.0 if ds["period"] == "d" else float(exp["horizon"]))
    panel, path = load_panel(experiment, ds["period"], False, exp.get("features_from"))
    feats = [c for c in panel.columns if c not in META_COLUMNS] + [pp.PLANTED]
    edges_dates = splits.date_edges(panel["ts"], int(exp.get("validation", {}).get("folds", 5)),
                                    splits.cut_offset(exp.get("validation", {})))
    days = eval_days(panel, exp, edges_dates)
    L, ks = levels(days)
    out = {"experiment": experiment, "table": path, "rows": len(panel), "symbols": int(panel["symbol"].nunique()),
           "eval_days_per_fold": days, "limit_bp_per_fold": LIMIT_BP_PER_FOLD, "limit_bp_per_day": L, "multiples": list(MULTIPLES),
           "k_bp": ks, "thetas": list(thetas), "seeds": seeds, "runs": [], "summary": []}
    log(f"評価期間の営業日数 {days}（平均 {np.mean(days):.0f}）→ 検出限界 L ＝ {L:.3f} bp/日・k ＝ " + " ／ ".join(f"{k:.3f}" for k in ks))
    t0 = time.perf_counter()
    for mult, k in zip(MULTIPLES, ks):
        tables = [inject(panel, k, seed) for seed in range(seeds)]
        for th in thetas:
            got = []
            for seed, tab in enumerate(tables):
                r = pp.one(tab, feats, exp, edges_dates, th)
                r.update(k_bp=float(k), multiple=float(mult), theta=float(th), seed=seed)
                got.append(r)
                out["runs"].append(r)
            e = np.array([np.nanmean(r["edges"]) for r in got])
            vs = [r["verdict"] for r in got]
            row = {"multiple": float(mult), "k_bp": float(k), "theta": float(th), "採る": vs.count("採る") / seeds,
                   "保留": vs.count("保留") / seeds, "落とす": vs.count("落とす") / seeds, "edge_mean_bp": float(np.mean(e)),
                   "edge_min_bp": float(np.min(e)), "edge_max_bp": float(np.max(e)),
                   "corr_buy_planted": float(np.mean([r["corr_buy_planted"] for r in got]))}
            out["summary"].append(row)
            log(f"k {k:>6.3f} bp/日（L の {mult:g} 倍）・θ {th:g}: 採る {row['採る']:.0%} ／ 保留 {row['保留']:.0%} ／ 落とす {row['落とす']:.0%}・"
                f"上乗せ 平均 {row['edge_mean_bp']:+.0f}bp/fold（{row['edge_min_bp']:+.0f}〜{row['edge_max_bp']:+.0f}）・"
                f"買い%×列 ρ {row['corr_buy_planted']:.3f}・{time.perf_counter() - t0:.0f} 秒")
    out["seconds"] = time.perf_counter() - t0
    return out


def _summary(got: list[dict], seeds: int, **head) -> dict:
    """シードをまたいだ 1 行（persist と、その対の相手の iid で同じ形）。"""
    e = np.array([np.nanmean(r["edges"]) for r in got])
    vs = [r["verdict"] for r in got]
    return {**head, "採る": vs.count("採る") / seeds, "保留": vs.count("保留") / seeds, "落とす": vs.count("落とす") / seeds,
            "edge_mean_bp": float(np.mean(e)), "edge_min_bp": float(np.min(e)), "edge_max_bp": float(np.max(e)),
            "corr_buy_planted": float(np.mean([r["corr_buy_planted"] for r in got])),
            "hold_ratio": float(np.mean([r["hold_ratio"] for r in got])),
            "trades_per_fold": float(np.mean([r["trades_per_fold"] for r in got])),
            "planted_pos_share": float(np.mean([r["planted_pos_share"] for r in got]))}


def _line(row: dict) -> str:
    return (f"採る {row['採る']:.0%} ／ 保留 {row['保留']:.0%} ／ 落とす {row['落とす']:.0%}・上乗せ 平均 {row['edge_mean_bp']:+.0f}bp/fold"
            f"（{row['edge_min_bp']:+.0f}〜{row['edge_max_bp']:+.0f}）・買い%×列 ρ {row['corr_buy_planted']:.3f}・"
            f"持つ日 {row['hold_ratio']:.2f}・取引 {row['trades_per_fold']:.0f} 回/fold")


def curve_persist(experiment: str = "trade_own_ridge_a", seeds: int = SEEDS, thetas=THETAS, units=UNITS, dwells=DWELLS,
                  multiples=PERSIST_MULTIPLES, pair: bool = True, log=print) -> dict:
    """何十日も続く局面の植え込み（単位 × 期待滞在 × k × θ × シード）と、対の相手（同じ k の iid）。"""
    from cli.run import load_panel

    exp = config.resolve_experiment(experiment)
    ds = exp["_dataset"]
    exp.setdefault("horizon_min", 1440.0 if ds["period"] == "d" else float(exp["horizon"]))
    panel, path = load_panel(experiment, ds["period"], False, exp.get("features_from"))
    feats = [c for c in panel.columns if c not in META_COLUMNS] + [pp.PLANTED]
    edges_dates = splits.date_edges(panel["ts"], int(exp.get("validation", {}).get("folds", 5)),
                                    splits.cut_offset(exp.get("validation", {})))
    days = eval_days(panel, exp, edges_dates)
    L = LIMIT_BP_PER_FOLD / float(np.mean(days))
    out = {"kind": "persist", "idea": "20261114-persist-signal-power", "experiment": experiment, "table": path, "rows": len(panel),
           "symbols": int(panel["symbol"].nunique()), "eval_days_per_fold": days, "limit_bp_per_fold": LIMIT_BP_PER_FOLD,
           "limit_bp_per_day": L, "multiples": list(multiples), "k_bp": [m * L for m in multiples], "units": list(units),
           "dwells": list(dwells), "thetas": list(thetas), "seeds": seeds, "runs": [], "summary": [], "pair_iid": []}
    log(f"評価期間の営業日数 {days}（平均 {np.mean(days):.0f}）→ 検出限界 L ＝ {L:.3f} bp/日・k ＝ "
        + " ／ ".join(f"{m * L:.3f}" for m in multiples))
    t0 = time.perf_counter()
    for unit in units:
        for dwell in dwells:
            for mult in multiples:
                k = mult * L
                tables = [inject_persist(panel, k, seed, unit, dwell) for seed in range(seeds)]
                for th in thetas:
                    got = []
                    for seed, tab in enumerate(tables):
                        r = pp.one(tab, feats, exp, edges_dates, th, detail=True)
                        r.update(unit=unit, dwell=int(dwell), k_bp=float(k), multiple=float(mult), theta=float(th), seed=seed)
                        got.append(r)
                        out["runs"].append(r)
                    row = _summary(got, seeds, unit=unit, dwell=int(dwell), multiple=float(mult), k_bp=float(k), theta=float(th))
                    out["summary"].append(row)
                    log(f"{unit:>6}・L {dwell:>2}・k {k:>6.3f} bp/日（{mult:g} 倍）・θ {th:g}: {_line(row)}・{time.perf_counter() - t0:.0f} 秒")
    if pair:
        for mult in [m for m in multiples if m > 0]:
            k = mult * L
            tables = [inject(panel, k, seed) for seed in range(seeds)]
            for th in thetas:
                got = [pp.one(tab, feats, exp, edges_dates, th, detail=True) for tab in tables]
                row = _summary(got, seeds, unit="iid", dwell=1, multiple=float(mult), k_bp=float(k), theta=float(th))
                out["pair_iid"].append(row)
                log(f"   iid・k {k:>6.3f} bp/日（{mult:g} 倍）・θ {th:g}: {_line(row)}・{time.perf_counter() - t0:.0f} 秒")
    out["seconds"] = time.perf_counter() - t0
    return out


def oracle_one(tab: pd.DataFrame, exp: dict, edges_dates, cost_bp: float) -> dict:
    """植えた局面そのものを買い%（＋1 の日 100 ／ −1 の日 0）にしてシミュレーターと判定に通す。戻り値は `pp.one(detail=True)` と同じ項目。"""
    from cli.perm_split import fold_net

    v = exp.get("validation", {})
    edges, extra = [], []
    for _f, _tr, te in splits.folds_by_dates(tab, edges_dates, float(exp["horizon_min"]), int(v.get("embargo_bars", 0)),
                                             float(exp.get("bar_minutes", 0.0))):
        te = te.reset_index(drop=True)
        y = te["y"].to_numpy(dtype=float)
        bp = np.where(te[pp.PLANTED].to_numpy() > 0, 100.0, 0.0)
        net = fold_net(te, bp, None, y, 50.0, cost_bp)
        bh = fold_net(te, np.full(len(te), 100.0), None, y, 50.0, cost_bp)
        edges.append(float("nan") if net is None or bh is None else net - bh)
        extra.append((*pp.hold_stats(te, bp, y, 50.0, cost_bp), float(np.mean(bp > 0))))
    return {"edges": edges, "verdict": pp.verdict(edges), "corr_buy_planted": 1.0,
            "hold_ratio": float(np.mean([e[0] for e in extra])), "trades_per_fold": float(np.mean([e[1] for e in extra])),
            "planted_pos_share": float(np.mean([e[2] for e in extra]))}


def oracle(experiment: str = "trade_own_ridge_a", seeds: int = SEEDS, units=UNITS, dwells=DWELLS,
           multiples=PERSIST_MULTIPLES, log=print) -> dict:
    """⚠ **事後に足した読み（2026-11-14・persist の結果を見た後。レジストリの structure の外・数えない）**:
    学習器を通さず、植えた局面そのものを買い% にする（＋1 の日 100 ／ −1 の日 0）＝ 列を完全に読めた読み手。
    同じシミュレーター（`fold_net`）と判定（`pp.verdict`）に通し、「読み手が列を拾えない」のか「シミュレーターと判定が拾えない」のかを分ける。
    買い% が 0 ／ 100 だけなので θ（50 以上）によらない。"""
    from cli.run import load_panel

    exp = config.resolve_experiment(experiment)
    ds = exp["_dataset"]
    exp.setdefault("horizon_min", 1440.0 if ds["period"] == "d" else float(exp["horizon"]))
    panel, path = load_panel(experiment, ds["period"], False, exp.get("features_from"))
    v = exp.get("validation", {})
    cost_bp = float(exp.get("cost_bp", 5.0))
    edges_dates = splits.date_edges(panel["ts"], int(v.get("folds", 5)), splits.cut_offset(v))
    L = LIMIT_BP_PER_FOLD / float(np.mean(eval_days(panel, exp, edges_dates)))
    out = {"kind": "persist-oracle", "idea": "20261114-persist-signal-power", "post_hoc": True, "experiment": experiment, "table": path,
           "limit_bp_per_day": L, "multiples": list(multiples), "units": list(units), "dwells": list(dwells), "seeds": seeds,
           "cost_bp": cost_bp, "runs": [], "summary": []}
    t0 = time.perf_counter()
    for unit in units:
        for dwell in dwells:
            for mult in multiples:
                k = mult * L
                got = []
                for seed in range(seeds):
                    r = oracle_one(inject_persist(panel, k, seed, unit, dwell), exp, edges_dates, cost_bp)
                    r.update(unit=unit, dwell=int(dwell), k_bp=float(k), multiple=float(mult), seed=seed)
                    got.append(r)
                    out["runs"].append(r)
                row = _summary(got, seeds, unit=unit, dwell=int(dwell), multiple=float(mult), k_bp=float(k))
                row["folds_positive"] = float(np.mean([np.mean(np.asarray(r["edges"]) > 0) for r in got]))
                out["summary"].append(row)
                log(f"完全な読み手・{unit:>6}・L {dwell:>2}・k {k:>6.3f} bp/日（{mult:g} 倍）: {_line(row)}・"
                    f"正の fold {row['folds_positive']:.0%}・{time.perf_counter() - t0:.0f} 秒")
    out["seconds"] = time.perf_counter() - t0
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="本物のリターンに既知の上乗せを埋めて検出力を測る（読むだけ・n_trials に数えない）")
    p.add_argument("--kind", choices=("iid", "persist", "oracle"), default="iid",
                   help="iid ＝ 日ごとに独立な信号（既定・2026-11-13 の形）／ persist ＝ 何十日も続く局面（2026-11-14）／ "
                        "oracle ＝ persist の局面そのものを買い% にした読み（事後に足した・学習器なし）")
    p.add_argument("--experiment", default="trade_own_ridge_a")
    p.add_argument("--seeds", type=int, default=SEEDS)
    p.add_argument("--json")
    p.add_argument("--save", action="store_true", help="DB の outputs の diag/<時刻>_inject_power[_persist]/ にも入れる")
    a = p.parse_args()
    doc = {"iid": curve, "persist": curve_persist, "oracle": oracle}[a.kind](a.experiment, a.seeds)
    text = json.dumps(doc, ensure_ascii=False, indent=1, default=float)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text)
    if a.save:
        name = {"iid": "inject_power", "persist": "inject_power_persist", "oracle": "inject_power_oracle"}[a.kind]
        print("→ " + runs.put_output(f"diag/{time.strftime('%Y-%m-%dT%H-%M-%S')}_{name}/{name}.json", text))


if __name__ == "__main__":
    main()
