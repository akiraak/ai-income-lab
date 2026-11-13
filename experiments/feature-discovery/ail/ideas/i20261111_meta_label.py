"""登録簿 `20261111-meta-label-filter`（参考あり・枠の内。記録 dev-loop.md）:
一段目（既存の own × Ridge）の買いの合図が「当たるか」を二段目の学習器で当て、当たりそうな合図だけで買う検知器 M1（メタラベル）。

- 一段目（評価に使う 1 本）: 既存の own × Ridge そのまま（訓練分割の全部で学ぶ・既存の較正 `calibrate.fit`）。⚠ 手を入れない。
- 二段目の学習行: 訓練分割を `tail_holdout`（14-12 と同じ尻 10%）で頭 ／ 尻に割り、**尻で学んだ一段目** で頭の行を当てる
  ＝ 一段目の予測は学習していない行のものだけ（登録簿のとおり）。頭の行のうち一段目の買い% > 50 の行だけを使う。
  ⚠ 検知器は θ を知らない ＝ 合図の線は θ の 3 水準の最小の 50 と読んだ（どの水準の合図も含む。売りの合図も 買い% < 50）。
- 二段目のラベル: その行で建て、同じ銘柄の次の「買い% < 50」の日の前まで持ったときの y の和 − 5bp が正か（頭の行の中だけで閉じる）。
- 二段目の入力: own 35 列 ＋ 一段目の買い% ＋ 一段目の直近 20 日の当たりの割合（⚠ その日より前の日だけ・同じ銘柄・最初の日は 0.5）。
- 二段目の学習器 ＝ ctx の model（config の `model`。Ridge ／ LightGBM・水準は既定）にラベル ±1 を当てさせ、既存の較正で確率にする。
- 出力: 一段目の買い% > 50 かつ 二段目の確率 > 土台（二段目の学習行での当たりの割合）の行だけ 一段目の買い%・ほかは 50。
  出口% は一段目のまま（100 − 一段目の買い%）。
- 頭 ／ 尻に割れない・二段目の学習行が 500 行に満たない・ラベルが片側だけのときは、間引かない（一段目そのまま）と記録する。
- leak 対照: LEAK_ 列を一段目と二段目の入力に足す。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail import registry
from ail.models import calibrate
from ail.models.holdout import tail_holdout
from ail.registry import register

LINE = 50.0            # ⚠ 事前固定: 一段目の合図の線（θ の最小）
COST = 5.0e-4          # ⚠ 事前固定: 5bp（登録簿）
HIT_WINDOW = 20        # ⚠ 事前固定（登録簿）
MIN_ROWS = 500         # 二段目の学習行の下限
STAGE1 = "Ridge"
NAME = "M1 一段目の合図の当たりを当てる二段目（own・学習・土台差）"
B1, HIT = "__buy1", "__hit20"


def _by_symbol(symbol, ts) -> tuple[np.ndarray, np.ndarray]:
    """(銘柄ごと・日付の順に並べる添字, 銘柄の切れ目の印〔並べた後・その行が銘柄の最後なら True〕)。"""
    s = np.asarray(symbol).astype(str)
    order = np.lexsort((np.arange(len(s)), pd.to_datetime(ts).to_numpy(), s))
    ss = s[order]
    last = np.r_[ss[1:] != ss[:-1], True] if len(ss) else np.zeros(0, dtype=bool)
    return order, last


def trade_gross(symbol, ts, buy1, y, line: float = LINE) -> np.ndarray:
    """行ごとの「その日に建て、同じ銘柄の次の 買い% < line の日の前まで持った y の和」（並びの最後で打ち切る）。"""
    order, last = _by_symbol(symbol, ts)
    b, r = np.asarray(buy1, dtype=float)[order], np.asarray(y, dtype=float)[order]
    f = np.zeros(len(r))
    for i in range(len(r) - 1, -1, -1):
        f[i] = r[i] + (0.0 if last[i] or b[i + 1] < line else f[i + 1])
    out = np.empty(len(r))
    out[order] = f
    return out


def hit_rate(symbol, ts, buy1, y, n: int = HIT_WINDOW, line: float = LINE) -> np.ndarray:
    """行ごとの「同じ銘柄の、その日より前の n 日の当たりの割合」（当たり ＝ 買い% − line と y の符号が同じ。前の日が無ければ 0.5）。"""
    df = pd.DataFrame({"s": np.asarray(symbol).astype(str), "t": pd.to_datetime(ts),
                       "h": (np.sign(np.asarray(buy1, dtype=float) - line) == np.sign(np.asarray(y, dtype=float))).astype(float)})
    df["_i"] = np.arange(len(df))
    df = df.sort_values(["s", "t", "_i"], kind="stable")
    v = df.groupby("s", sort=False)["h"].transform(lambda x: x.shift(1).rolling(n, min_periods=1).mean()).fillna(0.5)
    out = np.empty(len(df))
    out[df["_i"].to_numpy()] = v.to_numpy()
    return out


def m1(tr, te, feats, ctx):
    cols = [c for c in feats if c.startswith(("own_", "LEAK_"))]
    stage1 = registry.resolve("model", STAGE1)
    sc = StandardScaler().fit(tr[cols])
    Xtr = pd.DataFrame(sc.transform(tr[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    ytr = tr["y"].to_numpy(dtype=float)
    cal1 = calibrate.fit(stage1, Xtr, ytr, ctx)
    buy1 = np.asarray(cal1.buy_pct(stage1(Xtr, ytr, Xte, ctx)), dtype=float)
    exit1 = 100.0 - buy1
    doc = {"columns": cols, "一段目": STAGE1, "合図の線": LINE, "一段目の較正": cal1.doc, "一段目の合図の割合": round(float((buy1 > LINE).mean()), 4)}

    def plain(why: str):
        return buy1, exit1, {**doc, "間引き": f"なし（{why}）", "通した合図の割合": 1.0}

    (Xh, yh), hold = tail_holdout(Xtr, ytr)
    if hold is None:
        return plain("訓練が薄く頭 ／ 尻に割れない")
    Xt, yt = hold
    nh = len(Xh)
    cal_t = calibrate.fit(stage1, Xt, yt, ctx)
    b_h = np.asarray(cal_t.buy_pct(stage1(Xt, yt, Xh, ctx)), dtype=float)
    sym_h, ts_h = tr["symbol"].to_numpy()[:nh], tr["ts"].to_numpy()[:nh]
    sig = b_h > LINE
    lab = np.where(trade_gross(sym_h, ts_h, b_h, yh) - COST > 0, 1.0, -1.0)
    doc.update({"頭の行": int(nh), "尻の行": int(len(Xt)), "二段目の学習行": int(sig.sum())})
    if sig.sum() < MIN_ROWS:
        return plain(f"二段目の学習行が {int(sig.sum())} 行（下限 {MIN_ROWS}）")
    base = float((lab[sig] > 0).mean())
    if base in (0.0, 1.0):
        return plain("二段目のラベルが片側だけ")
    F_h = Xh.reset_index(drop=True).assign(**{B1: b_h, HIT: hit_rate(sym_h, ts_h, b_h, yh)})[sig].reset_index(drop=True)
    F_te = Xte.assign(**{B1: buy1, HIT: hit_rate(te["symbol"].to_numpy(), te["ts"].to_numpy(), buy1, te["y"].to_numpy(dtype=float))})
    c2 = cols + [B1, HIT]
    sc2 = StandardScaler().fit(F_h[c2])
    F2tr = pd.DataFrame(sc2.transform(F_h[c2]), columns=c2)
    F2te = pd.DataFrame(sc2.transform(F_te[c2]), columns=c2)
    model2 = ctx["model"]
    cal2 = calibrate.fit(model2, F2tr, lab[sig], ctx)
    p2 = np.asarray(cal2.buy_pct(model2(F2tr, lab[sig], F2te, ctx)), dtype=float) / 100.0
    signal = buy1 > LINE
    keep = signal & (p2 > base)
    buy = np.where(keep, buy1, 50.0)
    doc.update({"土台（二段目の学習行での当たりの割合）": round(base, 5), "二段目の較正": cal2.doc,
                "通した合図の割合": round(float(keep.sum() / max(signal.sum(), 1)), 4), "間引き": "あり",
                "二段目の確率 − 土台の 5〜95%": [round(float(np.quantile(p2 - base, q)), 4) for q in (0.05, 0.5, 0.95)], **cal1.doc})
    return buy, exit1, doc


register("detector", NAME)(m1)
