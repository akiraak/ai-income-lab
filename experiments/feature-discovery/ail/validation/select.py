"""訓練分割の成績で銘柄を選ぶ（rules.md 29 章。2026-10-22 利用者「小型中型から成績を良い銘柄を選ぶ処理を行う」）。

⚠ **純粋関数。読むのは呼び元が渡した訓練分割（`tr`）だけ** ＝ 評価期間には触れない（14-11「選抜は訓練分割の内側」と同じ置き方。
  評価期間の銘柄別 bp で選んで同じ fold で測り直すのは循環 ＝ 13-7・14-9）。
⚠ **候補は「買える株」だけ**（`pool`: 訓練分割の最後の終値が枠 ＝ 予算 ÷ K 以下。実売買の D3「1 株単位で買えるように」と同じ考え）。
⚠ **数は 1 水準ずつ事前固定**（窓 252 営業日・最低 126 行・尻の最低 20 行）。結果を見て動かさない（14-9・17-7 の 3）。

物差し（`BY`）:
  `ret` ＝ 株自身の成績: 訓練分割の最後の 252 営業日の累積対数リターン（`y` の和）の大きい順
  `net` ＝ モデルの成績: 訓練分割を頭 ／ 尻に割り（`tail_holdout`。門 14-5 と同じ割り方）、頭で fit したモデルの尻の予測を Platt
          （holdout 較正）で買い% にし、尻で 13 章の閾値売買を銘柄ごとに回した純利 bp の大きい順（θ ごとに選ぶ）。
          ⚠ 尻で 1 回も取引しなかった銘柄は成績なし（候補から外す）。候補が 0 本なら何も選ばない（全期間 現金）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BY = ("ret", "net")
RET_WINDOW = 252       # 「株自身の成績」の窓（営業日。1 水準）
RET_MIN_ROWS = 126     # 窓の中の行がこれ未満の銘柄は候補から外す
NET_MIN_ROWS = 20      # 訓練の尻にこれ未満の行しか無い銘柄は候補から外す
SCORE_COLUMNS = ["symbol", "閾値", "rows", "score", "chosen"]


def pool(tr: pd.DataFrame, cap: float | None) -> list[str]:
    """候補 ＝ 訓練分割の最後の終値が枠 `cap` 以下の銘柄（`cap` が None か `close` 列が無ければ全部）。銘柄名の順。"""
    syms = tr["symbol"].astype(str)
    if cap is None or "close" not in tr:
        return sorted(set(syms))
    last = tr.assign(symbol=syms).sort_values("ts", kind="stable").groupby("symbol")["close"].last()
    return sorted(str(s) for s, px in last.items() if np.isfinite(px) and float(px) <= float(cap))


def top(scores: dict[str, float], k: int) -> list[str]:
    """大きい順に k 本。同点は銘柄名の昇順。NaN は候補から外す。"""
    items = [(s, float(v)) for s, v in scores.items() if v is not None and np.isfinite(v)]
    items.sort(key=lambda x: (-x[1], x[0]))
    return [s for s, _v in items[:int(k)]]


def by_return(tr: pd.DataFrame, k: int, cap: float | None = None,
              window: int = RET_WINDOW, min_rows: int = RET_MIN_ROWS) -> tuple[list[str], pd.DataFrame]:
    """株自身の成績で上位 k 本。戻り値 (選んだ銘柄, 表〔symbol・rows・score・chosen〕)。"""
    cands = set(pool(tr, cap))
    d = tr[tr["symbol"].astype(str).isin(cands)].sort_values("ts", kind="stable")
    scores: dict[str, float] = {}
    rows = []
    for s, g in d.groupby(d["symbol"].astype(str), sort=True):
        tail = g["y"].to_numpy(dtype=float)[-int(window):]
        tail = tail[~np.isnan(tail)]
        score = float(tail.sum()) if len(tail) >= int(min_rows) else float("nan")
        scores[s] = score
        rows.append({"symbol": s, "閾値": np.nan, "rows": int(len(tail)), "score": score})
    chosen = top(scores, k)
    table = pd.DataFrame(rows, columns=SCORE_COLUMNS[:-1])
    table["chosen"] = table["symbol"].isin(chosen)
    return chosen, table


def by_model_net(tr: pd.DataFrame, feats: list[str], exp: dict, ctx: dict, model, selector, k_cols: int,
                 thresholds, cost_bp: float, k: int, cap: float | None = None,
                 min_rows: int = NET_MIN_ROWS) -> tuple[dict[float, list[str]], pd.DataFrame]:
    """モデルの成績で上位 k 本（θ ごと）。戻り値 ({θ: 選んだ銘柄}, 表〔symbol・閾値・rows・score・chosen〕)。

    ⚠ 門（`ail/validation/gate.py` の `_fold_selectors`）と同じ手順で尻の買い% を作る: 訓練全体で scaler ／ 変換 ／ 選別を fit し、
    頭で fit したモデルの尻の予測を holdout 較正で買い% にする。尻が切れないとき（訓練が短い）は何も選ばない。
    """
    from sklearn.preprocessing import StandardScaler

    from ail.models import calibrate, tuned
    from ail.models.holdout import tail_holdout
    from ail.validation import prep
    from ail.validation import simulate as sim

    ths = [float(x) for x in thresholds]
    empty = pd.DataFrame(columns=SCORE_COLUMNS)
    cands = set(pool(tr, cap))
    if len(tr) == 0 or not cands:
        return {th: [] for th in ths}, empty
    sc = StandardScaler().fit(tr[feats])
    Xtr = pd.DataFrame(sc.transform(tr[feats]), columns=feats)
    ytr = np.asarray(tr["y"].values, dtype=float)
    Xtr, _, _ = prep.apply(exp, Xtr, Xtr, ctx, ytr, ts_tr=tr["ts"].to_numpy())
    (Xh, yh), holdout = tail_holdout(Xtr, ytr)
    if holdout is None:
        return {th: [] for th in ths}, empty
    Xv, yv = holdout
    cols = selector(Xtr, ytr, int(k_cols), ctx)
    pred = np.asarray(model(Xh[cols], yh, Xv[cols], ctx), dtype=float)
    tuned.drain(ctx)                                   # 内側選抜の記録は選抜では捨てる（門と同じ。14-12）
    buy = np.asarray(calibrate.fit_from_predictions(pred, yv, "holdout").buy_pct(pred), dtype=float)
    n_val = len(yv)
    tail = tr.iloc[len(tr) - n_val:]
    syms = tail["symbol"].astype(str).to_numpy()
    ts = pd.to_datetime(tail["ts"]).to_numpy()
    rows: list[dict] = []
    chosen: dict[float, list[str]] = {}
    for th in ths:
        scores: dict[str, float] = {}
        for s in sorted(set(syms) & cands):
            idx = np.where(syms == s)[0]
            idx = idx[np.argsort(ts[idx], kind="stable")]
            if len(idx) < int(min_rows):
                scores[s] = float("nan")
                rows.append({"symbol": s, "閾値": th, "rows": int(len(idx)), "score": float("nan"), "trades": 0})
                continue
            r = sim.simulate(buy[idx], yv[idx], th, cost_bp)
            # ⚠ 尻で 1 回も取引しなかった銘柄は「成績なし」＝ 候補から外す（29-3。純利 0 の同点を銘柄名の順で選ぶ退化を防ぐ ＝
            #    2026-10-22 の 1 回目で θ55 ／ 60 の候補 44 本が全部 0 で同点になった。記録 §1-0）
            scores[s] = float(np.sum(r["net_bp"])) if int(r["trades"]) > 0 else float("nan")
            rows.append({"symbol": s, "閾値": th, "rows": int(len(idx)), "score": scores[s], "trades": int(r["trades"])})
        chosen[th] = top(scores, k)
    table = pd.DataFrame(rows, columns=SCORE_COLUMNS[:-1] + ["trades"])
    table["chosen"] = [s in chosen.get(float(t), []) for s, t in zip(table["symbol"], table["閾値"])]
    return chosen, table


def random_sets(cands: list[str], k: int, seed: int, n_sets: int) -> list[list[str]]:
    """対照「乱択選抜」: 同じ候補から k 本を一様に引く（種 `seed + q`。`n_sets` 組）。候補が k 本未満なら全部。"""
    cs = sorted(str(s) for s in cands)
    out = []
    for q in range(int(n_sets)):
        rng = np.random.default_rng(int(seed) + q)
        take = min(int(k), len(cs))
        out.append(sorted(rng.choice(cs, size=take, replace=False).tolist()) if take else [])
    return out
