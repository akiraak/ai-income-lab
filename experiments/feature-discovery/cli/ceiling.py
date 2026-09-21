"""⚠ **回す前に上限を見積もる**（[rules.md 14-2](../../../docs/specs/experiments/feature-discovery/rules.md)）。

    python3 -m cli.ceiling --run runs/2026-09-12T15-15-12_trend_scales_1995

⚠ **保存済みの実行を読むだけで、新しい実行も新しい試行も作らない。**

退出（出口）と再進入（入口）のどちらにいくら残っているかを、⚠ **エピソード級の神託**で測る。
神託が知るのは **山と谷の日付だけ**で、日ごとの正解は知らない。

⚠ **B&H は検証期間ずっと買い持ちなので、持っている日には上乗せが生まれない**（差はコストだけ）。
だから ⚠ **谷 → 回復とそれ以外の区間で神託が取れる上限はちょうど 0** になり、上限は全部 山 → 谷 に載る。

出すもの（[entry-timing.md](../../../docs/specs/experiments/entry-timing.md)）:

| 方策 | 中身 |
| --- | --- |
| (i) 出口だけ完全 | 山で抜ける・戻りは現行規則のまま |
| (ii) 入口だけ完全 | 抜けは現行規則のまま・谷で戻る。⚠ **上昇トレンドを追う価値の上限** |
| (iii) 両方完全 | 山で抜けて谷で戻る |

⚠ **神託の符号（5/5 など）を「通った」と読まない。** 神託はその標本に合わせてあるので当然そうなる。
読むのは ⚠ **P(符号 k/k)** と ⚠ **神託の何 % を取れれば壁を越えるか**のほう。
"""

from __future__ import annotations

import argparse
import json
import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from ail import runs

DRIFT = "基準 常に上（ドリフト）"
MIN_DROP_BP = 1000.0
COST_ONEWAY_BP = 2.5

PEAK_TROUGH, TROUGH_RECOVER, OTHER = 1, 2, 0


def episode_spans(bh: pd.Series, min_drop_bp: float = MIN_DROP_BP) -> list[tuple[int, int, int | None]]:
    """山 → 谷 → 回復 を**添字**で返す。⚠ `checks._episodes` と同じ算法（そちらは日付で返す）。"""
    eq = bh.cumsum().to_numpy()
    spans: list[tuple[int, int, int | None]] = []
    peak, peak_i, trough_i, live = eq[0], 0, 0, False
    for i in range(len(eq)):
        if eq[i] >= peak:
            if live:
                spans.append((peak_i, trough_i, i))
                live = False
            peak, peak_i = eq[i], i
        elif not live and eq[i] - peak <= -min_drop_bp:
            live, trough_i = True, i
        elif live and eq[i] < eq[trough_i]:
            trough_i = i
    if live:
        spans.append((peak_i, trough_i, None))
    return spans


def windows(n: int, spans) -> np.ndarray:
    """各日を 山→谷 / 谷→回復 / それ以外 に割る。⚠ **区間は (lo, hi] と (hi, rec]**（損益は翌日に載るため）。"""
    w = np.full(n, OTHER, dtype=int)
    for lo, hi, rec in spans:
        w[lo + 1:hi + 1] = PEAK_TROUGH
        if rec is not None:
            w[hi + 1:rec + 1] = TROUGH_RECOVER
    return w


def oracles(edge: np.ndarray, bh: np.ndarray, w: np.ndarray,
            n_episodes: int, cost_oneway_bp: float = COST_ONEWAY_BP):
    """現行規則の日次上乗せ `edge` から、神託 3 種の日次上乗せと合計を作る。

    ⚠ **山 → 谷 で休むと上乗せは `-bh`**（B&H が取った損をそのまま取らずに済む）。
    ⚠ **谷 → 回復で完全に持つと上乗せはちょうど 0**（B&H と同じ持ち方になる）。
    """
    cost = n_episodes * 2 * cost_oneway_bp
    series = {
        "現行規則": edge,
        "(i) 出口だけ完全": np.where(w == PEAK_TROUGH, -bh, edge),
        "(ii) 入口だけ完全": np.where(w == TROUGH_RECOVER, 0.0, edge),
        "(iii) 両方完全": np.where(w == PEAK_TROUGH, -bh, 0.0),
    }
    # ⚠ **神託は 12 往復ぶん余計に売買する。** 現行規則との差だけを引く
    extra = {"現行規則": 0.0, "(i) 出口だけ完全": cost, "(ii) 入口だけ完全": 0.0,
             "(iii) 両方完全": cost}
    totals = {k: float(v.sum()) - extra[k] for k, v in series.items()}
    return series, totals


def _wide(panel: pd.DataFrame, dates: pd.DatetimeIndex, col: str, scale: float = 1.0) -> pd.DataFrame:
    """表の 1 列を 日 × 銘柄 の行列にして検証日に揃える。

    ⚠ **表の `ts` は UTC 付き・`daily.csv` の `ts` は素**なので、揃えないと全部 NaN になって
    ⚠ **「エピソード 0 回」という静かな誤りになる**（2026-09-12 に踏んだ）。
    """
    ts = pd.to_datetime(panel["ts"])
    if (ts.dt.tz is None) != (dates.tz is None):
        ts = ts.dt.tz_localize(None) if ts.dt.tz is not None else ts.dt.tz_localize(dates.tz)
    wide = panel.assign(ts=ts).pivot(index="ts", columns="symbol", values=col).reindex(dates) * scale
    if wide.notna().to_numpy().sum() == 0:
        raise SystemExit(f"⚠ 表と実行の日付が 1 日も噛み合わない（列 {col}・表が違う可能性）")
    return wide


def wide_returns(panel: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """日次リターンを bp の行列にする（対数リターン × 10,000）。"""
    return _wide(panel, dates, "y", 1e4)


def _portfolio_bp(pos: np.ndarray, y: np.ndarray, avail: np.ndarray,
                  fold_days: list[int], cost_oneway_bp: float) -> np.ndarray:
    """建玉（日 × 銘柄の 0/1）から ⚠ **その日に存在する銘柄の等加重**の日次純利 bp を作る（13-7）。

    ⚠ **fold の頭と尻で強制清算**（13-4 規約 4）。⚠ **建てた日と手仕舞った日に片道 2.5bp**（規約 3）。
    """
    p = np.where(avail, pos, 0.0)
    edges = np.cumsum([0] + fold_days)
    prev = np.zeros_like(p)
    for s, e in zip(edges[:-1], edges[1:]):
        prev[s] = 0.0                                  # ⚠ fold の頭は必ず未保有から
        prev[s + 1:e] = p[s:e - 1]
    trades = np.abs(p - prev)
    close = np.zeros_like(p)
    for e in edges[1:]:
        close[e - 1] = p[e - 1]                        # ⚠ fold の尻で強制清算
    live = np.maximum(avail.sum(axis=1), 1)
    gross = np.where(avail, p * y, 0.0).sum(axis=1)
    cost = ((trades + close) * cost_oneway_bp).sum(axis=1)
    return (gross - cost) / live


def per_symbol_policies(panel: pd.DataFrame, dates: pd.DatetimeIndex, fold_days: list[int],
                        rule_col: str, min_drop_bp: float = MIN_DROP_BP,
                        cost_oneway_bp: float = COST_ONEWAY_BP):
    """⚠ **銘柄別の神託 3 種** — 銘柄が**自分の**山と谷を知っている場合。

    ⚠ **現行規則は銘柄ごとに動くので、その族の上限はここで測る。**
    市場タイミングの神託（`oracles`）は 63 銘柄を一斉に出し入れするため ⚠ **上限を低く見積もる。**
    """
    y = wide_returns(panel, dates)
    avail = y.notna().to_numpy()
    yv = np.nan_to_num(y.to_numpy())
    sig = _wide(panel, dates, rule_col).to_numpy()

    # ⚠ **古典フィルタは買い% が 0 か 100 しか取らない**ので、θ に依らず建玉 = 乖離が正か
    pos_rule = np.where(np.isnan(sig), 0.0, (sig > 0.0).astype(float))
    pos_out = pos_rule.copy()     # (i) 出口だけ完全: 自分の 山 → 谷 では必ず休む
    pos_in = pos_rule.copy()      # (ii) 入口だけ完全: 自分の 谷 → 回復 では必ず持つ
    pos_both = np.ones_like(pos_rule)
    n_ep = 0
    for j in range(y.shape[1]):
        idx = np.flatnonzero(avail[:, j])
        if len(idx) < 2:
            continue
        col = pd.Series(yv[idx, j], index=dates[idx])
        for lo, hi, rec in episode_spans(col, min_drop_bp):
            n_ep += 1
            down = idx[lo + 1:hi + 1]
            pos_out[down, j] = 0.0
            pos_both[down, j] = 0.0
            if rec is not None:
                pos_in[idx[hi + 1:rec + 1], j] = 1.0

    bh = _portfolio_bp(np.ones_like(pos_rule), yv, avail, fold_days, cost_oneway_bp)
    out = {}
    for label, pos in (("現行規則（表から組み直し）", pos_rule), ("(i) 出口だけ完全", pos_out),
                       ("(ii) 入口だけ完全", pos_in), ("(iii) 両方完全", pos_both)):
        out[label] = _portfolio_bp(pos, yv, avail, fold_days, cost_oneway_bp) - bh
    return out, n_ep


def detection_limit(daily_edge: np.ndarray, fold_days: list[int], prob: float = 0.8) -> dict:
    """検出限界（14-2）。⚠ **σ は方策ごとに違うので、借りずに毎回この系列から出す。**"""
    k = len(fold_days)
    sd = float(np.std(daily_edge, ddof=1))
    sigma_fold = sd * math.sqrt(float(np.mean(fold_days)))
    nd = NormalDist()
    return {"σ日次bp": sd, "σ_fold_bp": sigma_fold,
            "限界_符号_bp/fold": nd.inv_cdf(prob ** (1.0 / k)) * sigma_fold,
            "限界_t2_bp/fold": 2.0 * sigma_fold / math.sqrt(k)}


def needed_share(total: float, actual: float, limit_per_fold: float, n_folds: int) -> float:
    """⚠ **神託の何 % を取れれば壁を越えるか。** ⚠ **1 を超えたら定義から不可能。**"""
    gain = total - actual
    return float("inf") if gain <= 0 else (limit_per_fold * n_folds - actual) / gain


def _load_daily(run: str) -> pd.DataFrame:
    df = pd.read_csv(f"{run}/daily.csv")
    df = df[df["手法"] != "手法"]
    df["ts"] = pd.to_datetime(df["ts"])
    df["値"] = pd.to_numeric(df["値"], errors="coerce")
    df["閾値"] = pd.to_numeric(df["閾値"], errors="coerce")
    return df


def _series(df: pd.DataFrame, name: str, th: float, kind: str = "純利bp") -> pd.Series:
    s = df[(df["手法"] == name) & (df["閾値"] == th) & (df["系列"] == kind)]
    return s.set_index("ts")["値"].sort_index()


def _fold_days(run: str) -> list[int]:
    """⚠ **検証 fold は時間順に連続していて日付が空かない。**
    ⚠ **日付の空きで切ると先頭が短くなり符号の数が変わる**ので、`result.csv` の `検証日数` を正本にする。"""
    r = pd.read_csv(f"{run}/result.csv")
    th = float(r["閾値"].min())
    return r[r["閾値"] == th].groupby("fold")["検証日数"].first().sort_index().astype(int).tolist()


def main() -> int:
    ap = argparse.ArgumentParser(description="回す前に上限を見積もる（rules.md 14-2）")
    ap.add_argument("--run", required=True, help="実行の名前（daily.csv と result.csv を読む。記録は runs/research.sqlite）")
    ap.add_argument("--rule", default="C3 長期 SMA200 フィルタ", help="現行規則とみなす手法")
    ap.add_argument("--threshold", type=float, default=50.0)
    ap.add_argument("--min-drop-bp", type=float, default=MIN_DROP_BP)
    ap.add_argument("--table", help="銘柄別の神託も出す（表の parquet。⚠ 市場タイミングの上限より必ず高い）")
    ap.add_argument("--rule-col", default="own_trend200_dist",
                    help="現行規則を表から組み直すための列（古典フィルタは「この列 > 0 なら持つ」）")
    ap.add_argument("--json", help="結果を書き出す先")
    a = ap.parse_args()

    a.run = runs.resolve(a.run)
    with runs.materialized(a.run) as d:            # ⚠ 読むための写し（出たら消す）
        df = _load_daily(d)
        folds = _fold_days(d)
    bh_s = _series(df, DRIFT, a.threshold)
    me_s = _series(df, a.rule, a.threshold)
    if len(bh_s) == 0 or len(me_s) != len(bh_s):
        raise SystemExit(f"⚠ 手法 {a.rule!r} θ={a.threshold} の日次が取れない")
    bh, edge = bh_s.to_numpy(), me_s.to_numpy() - bh_s.to_numpy()
    n = len(bh)

    spans = episode_spans(bh_s, a.min_drop_bp)
    w = windows(n, spans)
    if sum(folds) != n:
        raise SystemExit(f"⚠ fold の合計 {sum(folds)} が検証日数 {n} と合わない")
    nf = len(folds)

    parts = {"山→谷": float(edge[w == PEAK_TROUGH].sum()),
             "谷→回復": float(edge[w == TROUGH_RECOVER].sum()),
             "それ以外": float(edge[w == OTHER].sum())}
    decline = float(-bh[w == PEAK_TROUGH].sum())
    series, totals = oracles(edge, bh, w, len(spans))
    actual = totals["現行規則"]

    share = parts["山→谷"] / decline

    if a.table:
        panel = pd.read_parquet(a.table, columns=["symbol", "ts", "y", a.rule_col])
        # ⚠ **検算 1: 表から組み直した B&H が実行の記録と合うか**（合わなければ集計の仕方が違う）
        recon = wide_returns(panel, bh_s.index).mean(axis=1).to_numpy()
        print(f"\n⚠ 検算: 表から組み直した B&H が記録と一致した日 "
              f"{int(np.isclose(recon, bh, atol=0.05).sum())}/{n}（残りは fold 端の片道コスト 2.5bp）")
        sym, n_sym_ep = per_symbol_policies(panel, bh_s.index, folds, a.rule_col, a.min_drop_bp)
        # ⚠ **検算 2: 表から組み直した現行規則が記録と合うか**（合わなければ規則の読み違い）
        rebuilt = float(sym["現行規則（表から組み直し）"].sum())
        print(f"⚠ 検算: 表から組み直した {a.rule} の上乗せ {rebuilt:.0f}bp "
              f"／ 記録 {actual:.0f}bp（差 {rebuilt - actual:+.0f}）")
        print(f"⚠ 銘柄別のエピソードは {n_sym_ep} 回（市場タイミングの {len(spans)} 回に対して）\n")
        for label in ("(i) 出口だけ完全", "(ii) 入口だけ完全", "(iii) 両方完全"):
            series[f"銘柄別 {label}"] = sym[label]
            totals[f"銘柄別 {label}"] = float(sym[label].sum())

    print(f"検証 {n} 日  {bh_s.index[0].date()} 〜 {bh_s.index[-1].date()}  fold {folds}")
    print(f"エピソード {len(spans)} 回  日数 山→谷 {(w == PEAK_TROUGH).sum()} / "
          f"谷→回復 {(w == TROUGH_RECOVER).sum()} / それ以外 {(w == OTHER).sum()}")
    print(f"現行規則 {a.rule} θ={a.threshold:.0f}: "
          f"全期間 {actual:.0f} = 山→谷 {parts['山→谷']:.0f} / 谷→回復 {parts['谷→回復']:.0f} "
          f"/ それ以外 {parts['それ以外']:.0f}")
    print(f"⚠ 山 → 谷 を丸ごと避けた上限 {decline:.0f}bp ／ 現行規則の取り分 {share:.1%}\n")

    print(f"{'方策':<20}{'合計bp':>10}{'bp/fold':>10}{'÷壁(符号)':>11}{'÷壁(t2)':>10}"
          f"{'P(符号)':>9}{'要る取り分':>11}")
    out = []
    nd = NormalDist()
    for label, tot in totals.items():
        lim = detection_limit(series[label], folds)
        per = tot / nf
        need = needed_share(tot, actual, lim["限界_符号_bp/fold"], nf) if label != "現行規則" else float("nan")
        row = {"方策": label, "合計bp": round(tot, 1), "bp/fold": round(per, 1),
               "bp/日": round(tot / n, 3),
               **{k: round(v, 3) for k, v in lim.items()},
               "÷限界_符号": round(per / lim["限界_符号_bp/fold"], 3),
               "÷限界_t2": round(per / lim["限界_t2_bp/fold"], 3),
               "P(符号)": round(nd.cdf(per / lim["σ_fold_bp"]) ** nf, 4),
               "要る取り分_符号": None if math.isnan(need) else round(need, 3),
               "要る取り分_t2": (None if label == "現行規則" else
                              round(needed_share(tot, actual, lim["限界_t2_bp/fold"], nf), 3)),
               "fold別": [round(float(series[label][s:e].sum()), 1)
                          for s, e in zip(np.cumsum([0] + folds[:-1]), np.cumsum(folds))]}
        out.append(row)
        flag = "" if math.isnan(need) or need <= 1.0 else "  ⚠ 1 超 ＝ 不可能"
        print(f"{label:<20}{tot:>10.0f}{per:>10.0f}{row['÷限界_符号']:>11.2f}"
              f"{row['÷限界_t2']:>10.2f}{row['P(符号)']:>9.3f}"
              f"{'—' if math.isnan(need) else f'{need:>10.2f}'}{flag}")

    print("\n⚠ 神託の符号を「通った」と読まない（標本に合わせてあるので当然そうなる）。"
          "読むのは P(符号) と要る取り分のほう")

    if a.json:
        json.dump({"run": a.run, "rule": a.rule, "threshold": a.threshold,
                   "検証日数": n, "fold": folds, "エピソード回数": len(spans),
                   "山谷合計bp": round(decline, 1), "現行規則の取り分": round(share, 4),
                   "分解": {k: round(v, 1) for k, v in parts.items()}, "方策": out},
                  open(a.json, "w"), ensure_ascii=False, indent=2)
        print(f"→ {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
