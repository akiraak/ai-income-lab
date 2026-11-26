"""レジストリ `20261126-rank-tail-rest`（参考あり・枠の内。記録 dev-loop.md）:
その銘柄の直近 252 営業日の自分の予測値の中での順位が **下の端（q ＝ 1 割 ／ 2 割）に入ったときだけ休み**、
順位が 0.5 を超えるまで休んだまま、ほかは持ち続ける検知器 4 本（元の予測値 2 種類 × q 2 水準）。

- 元の予測値（どちらも own の列 × ctx の model ＝ Ridge）:
  - `ret` ＝ 素の own × Ridge の翌日の予測値（TR1 と同じ 1 本。予測値は TR1 と 1 ビットも違わない）。
  - `fwd20` ＝ 先 20 日ゲート H3 と同じ対象（先 20 本の累積対数リターン）を学んだ、**較正に渡す前の予測値**。
    ⚠ 順位は単調な変換で変わらない ＝ Platt 較正（傾きが正）の後の確率の順位と同じ。
    ⚠ 表は own_2018 を借りる（`y_fwd_20` の列は無い）ので、対象は **訓練分割の中の `y`（翌日対数リターン）の次の 20 本の和** で作る
    （20 本そろわない行 ＝ 訓練の終わりの 19 本は学習から外す ＝ 評価の y を読まない）。`ail/detectors/fwd.py` は呼ばない・変えない。
- 順位の作り方は TR1 と同じ（`trailing_rank`・その行と前の日の予測値だけ・同じ値は平均の順位）。
  評価期間の頭の 252 日ぶんは、訓練の末尾 252 日を学習から外して学んだ 1 本で末尾を当てた予測値で埋める。
  `fwd20` の頭の 1 本の対象は **頭の行だけ** で作る（末尾の y を読まない ＝ 末尾の予測は末尾を知らない）。
- 買い%: 順位 ÷ 100 ＜ q の日に休みに入る（買い% 0）→ 順位 ÷ 100 ＞ 0.5 の日に戻る（買い% 100）。ほかの日は前の日の状態のまま。
  最初の状態は「持つ」。状態は銘柄ごとに、末尾 ＋ 評価の並びを日付の順にたどる（評価の初日の状態は末尾の並びで決まる）。
  買い% は 0 か 100 だけ ＝ θ は 50 の 1 水準（config）。出口% は 100 − 買い%（シミュレーターの既定）。
- 較正はしない。leak 対照: LEAK_ 列を学習器の入力に足す（予測値が先を知る ＝ 休む日が先を知る）。
⚠ 事前固定: N ＝ 252・q ＝ 0.1 ／ 0.2・戻る線 0.5・先 20 日（レジストリ）。結果を見て振らない。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail.ideas import i20261113_score_trailing_rank as trk
from ail.registry import register

N = trk.N              # 252（TR1 と同じ）
QS = (0.1, 0.2)        # ⚠ 事前固定（レジストリ）
BACK = 0.5             # 戻る線（順位がこれを超えたら持つに戻る）
FWD = 20               # 先 20 日（H3 と同じ対象）
KINDS = ("ret", "fwd20")


def name_of(kind: str, q: float) -> str:
    w = f"{int(round(q * 10))}割"
    if kind == "ret":
        return f"TR2 自分の直近{N}日の予測値の順位が下の{w}で休む（own・学習）"
    return f"TR3 先{FWD}日の予測値の直近{N}日の順位が下の{w}で休む（own・学習）"


NAMES = {(k, q): name_of(k, q) for k in KINDS for q in QS}


def forward_sum(symbol, ts, y, h: int = FWD) -> np.ndarray:
    """行ごとの「同じ銘柄の、渡した行の中の次の h 本（その行を含む）の y の和」。h 本そろわない行は NaN。入力の並びのまま返す。"""
    d = pd.DataFrame({"s": np.asarray(symbol).astype(str), "t": pd.to_datetime(ts), "y": np.asarray(y, dtype=float)})
    d["_i"] = np.arange(len(d))
    d = d.sort_values(["s", "t", "_i"], kind="stable")
    out = np.full(len(d), np.nan)
    idx = d["_i"].to_numpy()
    yy = d["y"].to_numpy()
    start = 0
    for n in d.groupby("s", sort=False).size().to_numpy():
        if n >= h:
            out[idx[start:start + n - h + 1]] = np.lib.stride_tricks.sliding_window_view(yy[start:start + n], h).sum(axis=1)
        start += n
    return out


def rest_state(symbol, ts, rank, q: float, back: float = BACK) -> np.ndarray:
    """行ごとの買い%（0 ／ 100）。rank は 0〜100。rank ＜ 100q で休みに入り、rank ＞ 100·back で戻る。最初は持つ。入力の並びのまま返す。"""
    d = pd.DataFrame({"s": np.asarray(symbol).astype(str), "t": pd.to_datetime(ts), "r": np.asarray(rank, dtype=float)})
    d["_i"] = np.arange(len(d))
    d = d.sort_values(["s", "t", "_i"], kind="stable")
    r = d["r"].to_numpy()
    ev = np.where(r < 100.0 * q, 0.0, np.where(r > 100.0 * back, 100.0, np.nan))
    st = pd.Series(ev).groupby(d["s"].to_numpy(), sort=False).ffill().fillna(100.0).to_numpy()
    out = np.empty(len(d))
    out[d["_i"].to_numpy()] = st
    return out


def _switches(symbol, ts, buy) -> int:
    d = pd.DataFrame({"s": np.asarray(symbol).astype(str), "t": pd.to_datetime(ts), "b": np.asarray(buy, dtype=float)})
    d = d.sort_values(["s", "t"], kind="stable")
    return int((d.groupby("s", sort=False)["b"].diff().abs() > 0).sum())


def predictions(kind: str, tr, te, feats, ctx):
    """(末尾の行の印, 末尾の予測値, 評価の予測値, 記録)。`ret` は TR1 と同じ 2 本。`fwd20` は対象だけが違う。"""
    cols = [c for c in feats if c.startswith(("own_", "LEAK_"))]
    y = tr["y"].to_numpy(dtype=float)
    ts_tr = pd.to_datetime(tr["ts"]).to_numpy()
    sym_tr = tr["symbol"].to_numpy()
    tail = np.isin(ts_tr, trk.tail_days(ts_tr))
    head = ~tail
    if kind == "ret":
        y_full, y_head = y, y
    elif kind == "fwd20":
        y_full = forward_sum(sym_tr, ts_tr, y)
        y_head = np.full(len(tr), np.nan)
        y_head[head] = forward_sum(sym_tr[head], ts_tr[head], y[head])     # ⚠ 頭の行だけで作る（末尾の y を読まない）
    else:
        raise SystemExit(f"⚠ kind = {kind!r} は受けない（{KINDS}）")
    ok_full = np.isfinite(y_full)
    ok_head = head & np.isfinite(y_head)
    if not ok_full.any():
        raise SystemExit(f"⚠ 訓練分割に学習できる行が無い（kind = {kind}・先 {FWD} 本そろう行が 0）")
    sc = StandardScaler().fit(tr.loc[ok_full, cols])                      # ⚠ 標準化も訓練の内側（`ret` は全行 ＝ TR1 と同じ）
    Xtr = pd.DataFrame(sc.transform(tr[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    model = ctx["model"]
    if ok_head.any() and tail.any():
        p_tail = np.asarray(model(Xtr[ok_head].reset_index(drop=True), y_head[ok_head],
                                  Xtr[tail].reset_index(drop=True), ctx), dtype=float)
    else:                                                                  # 訓練が薄くて頭を学べない ＝ 評価の行だけで並びが伸びる
        tail = np.zeros(len(tr), dtype=bool)
        p_tail = np.empty(0)
    p_te = np.asarray(model(Xtr[ok_full].reset_index(drop=True), y_full[ok_full], Xte, ctx), dtype=float)
    doc = {"columns": cols, "元の予測値": kind, "N": N, "尻の日数": int(len(trk.tail_days(ts_tr))), "尻の行": int(tail.sum()),
           "頭の学習の行": int(ok_head.sum()), "全部の学習の行": int(ok_full.sum())}
    return tail, p_tail, p_te, doc


def detect(kind: str, q: float, tr, te, feats, ctx):
    tail, p_tail, p_te, doc = predictions(kind, tr, te, feats, ctx)
    n_tail = int(tail.sum())
    sym = np.r_[tr["symbol"].to_numpy()[tail], te["symbol"].to_numpy()]
    ts = np.r_[pd.to_datetime(tr["ts"]).to_numpy()[tail], pd.to_datetime(te["ts"]).to_numpy()]
    rank = trk.trailing_rank(sym, ts, np.r_[p_tail, p_te])
    state = rest_state(sym, ts, rank, q)
    buy, rk = state[n_tail:], rank[n_tail:]
    doc = {**doc, "q": q, "戻る線": BACK, "較正": "なし（順位が下の端で休む・0 ／ 100）",
           "休む行の割合": round(float((buy == 0).mean()), 4) if len(buy) else None,
           "順位が q を割った行の割合": round(float((rk < 100.0 * q).mean()), 4) if len(rk) else None,
           "評価の初日に休んでいる行の割合": _first_day_rest(te, buy),
           "切り替えの回数": _switches(te["symbol"].to_numpy(), pd.to_datetime(te["ts"]).to_numpy(), buy),
           "予測値の違う値の数（評価）": int(len(np.unique(p_te)))}
    return buy, doc


def _first_day_rest(te, buy):
    if not len(buy):
        return None
    t = pd.to_datetime(te["ts"]).to_numpy()
    first = t == t.min()
    return round(float((buy[first] == 0).mean()), 4)


for (_kind, _q), _name in NAMES.items():
    register("detector", _name)(lambda tr, te, feats, ctx, _kind=_kind, _q=_q: detect(_kind, _q, tr, te, feats, ctx))
