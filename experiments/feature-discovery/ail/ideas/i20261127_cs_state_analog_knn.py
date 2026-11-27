"""レジストリ `20261127-cs-state-analog-knn`（参考なし・枠の外。記録 dev-loop.md）:
ラベルを使わずに **日ごとの市場の状態（5 成分）** を作り、状態が似ている過去の日（訓練分割の中の近い K 日）の
「その後の先 20 日に上がった割合」× 100 を、そのまま銘柄ごとの買い% にする検知器 KN1（学習器なし・近傍法）。

- 状態（日ごとに 1 本・全銘柄で同じ値。表の `own_ret_1` だけから・その日までの足だけを使う）:
  1 200 日移動平均より上にいる銘柄の割合 ／ 2 20 日リターンの断面の標準偏差 ／ 3 60 日ボラ（日次リターンの標準偏差）の断面平均 ／
  4 60 日リターンの断面平均 ／ 5 60 日高値から 5% 以内にいる銘柄の割合（⚠ 「近く」＝ 5% は事前固定・振らない）。
  価格の水準は `own_ret_1` を銘柄ごとに累積した対数の比（移動平均・高値との比較は比なので目盛りに依らない）。
  ⚠ 表に列として足さない（日ごとに 1 つの値 ＝ 検知器の中で集約する。`ail/features`・`cli/build.py` を通さない ＝ 表を作り直さない）。
  ⚠ パージで抜けた日は足が無い日として飛ばす（その 1 日ぶんのリターンは水準に入らない ＝ RG1 と同じ近似）。
- 標準化: 訓練分割の日の平均と標準偏差（評価の日で欠けた成分は訓練の平均 ＝ 0）。
- 近い日: 候補 ＝ 訓練分割の日のうち、状態が 5 成分ともそろい、先 20 日が訓練分割の中で確定している日（＝ 訓練の終わりの 19 日は
  候補にならない ＝ パージ 20 本。評価の y を読まない）。K ＝ round(√訓練分割の日数)（⚠ 1 水準・事前固定）。距離はユークリッド。
- 買い% ＝ 100 × 近い K 日のうち、その銘柄の「訓練分割の中の次の 20 本の y（翌日 log リターン）の和 ＞ 0」だった日の割合。
  その銘柄の値が K 日に 1 つも無ければ、その K 日の全銘柄の割合。候補が 0 日なら 50。⚠ 較正しない・ctx の model は呼ばない。
- 基準線（数えない）「乱数で取った過去の日の先20日の上がる割合（近傍法の対照）」: 状態を見ずに、同じ候補から乱数で K 日を取って
  同じ割合を引く（＝ 銘柄ごとのベースレート）。⚠ 乱数の K 日は fold ごとに 1 度だけ引く（日ごとに引き直すと乱数の揺れで売買が増え、
  基準線だけが費用を払う）。シード ＝ config のシード。`model` として登録 ＝ `fold_buy_pct` が 買い% ＞ 50 を 100 ／ 0 にする（θ 50 の行と比べる）。
- leak 対照: 表に LEAK_ 列があれば、その断面平均を状態の成分として足す（倍率 ＝ √(5 ÷ LEAK_ の本数) ＝ 5 成分の合計と同じ重み）。
- 記録: K・候補の日数・近い日の塊の数（20 営業日より離れた塊）・買い% の幅・θ ごとの買い% が超える割合。
  `learn_if_null` のため、評価の日ごとの買い% の平均と市場の先 20 日リターンの相関も記録する（⚠ 記録だけ ＝ 買い% には使わない。テストが見る）。
⚠ 登録するだけ ＝ この検知器 ／ 基準線を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.registry import register

FWD = 20               # 先 20 日（⚠ 事前固定）
MA_WINDOW = 200        # 成分 1
RET_WINDOW = 20        # 成分 2
LONG_WINDOW = 60       # 成分 3〜5
NEAR_HIGH = 0.05       # 成分 5 の「近く」＝ 60 日高値から 5% 以内（⚠ 事前固定）
THETAS = (50, 55, 60)  # 記録のためだけ（売買の θ は config）
COMPONENTS = ("200日線より上の割合", "20日リターンの断面のばらつき", "60日ボラの断面平均", "60日リターンの断面平均", "60日高値の近くの割合")
NAME = "KN1 断面の状態が似た過去の日の先20日の上がる割合（近傍法）"
BASELINE = "乱数で取った過去の日の先20日の上がる割合（近傍法の対照）"


def _keys(frame: pd.DataFrame) -> np.ndarray:
    """行ごとの日の識別子（UTC の ns の整数。tz の有無に依らない）。"""
    return np.asarray(pd.DatetimeIndex(pd.to_datetime(frame["ts"])).asi8, dtype=np.int64)


def _num(x, nd: int = 4):
    x = float(x)
    return round(x, nd) if np.isfinite(x) else None


def returns_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """(日 × 銘柄) の日次リターン。行の無いマスは NaN。"""
    d = pd.DataFrame({"d": _keys(df), "s": df["symbol"].astype(str).to_numpy(),
                      "r": df["own_ret_1"].to_numpy(dtype=float)})
    R = d.pivot_table(index="d", columns="s", values="r", aggfunc="mean")
    return R.reindex(np.unique(d["d"].to_numpy())).sort_index()


def day_state(df: pd.DataFrame, leak_cols: list[str] | None = None) -> pd.DataFrame:
    """日ごとの状態（index ＝ 日の識別子）。rolling は過去の足だけ。"""
    R = returns_matrix(df)
    has = R.notna()
    started = has.astype(int).cummax().astype(bool)
    logp = R.fillna(0.0).cumsum().where(started)          # 銘柄ごとの対数の水準（最初の足を 0 とした比）
    price = np.exp(logp)
    out = pd.DataFrame(index=R.index)
    ma = price.rolling(MA_WINDOW, min_periods=MA_WINDOW).mean()
    out[COMPONENTS[0]] = (price > ma).astype(float).where(ma.notna() & has).mean(axis=1)
    out[COMPONENTS[1]] = R.rolling(RET_WINDOW, min_periods=RET_WINDOW).sum().std(axis=1)
    out[COMPONENTS[2]] = R.rolling(LONG_WINDOW, min_periods=LONG_WINDOW).std().mean(axis=1)
    out[COMPONENTS[3]] = R.rolling(LONG_WINDOW, min_periods=LONG_WINDOW).sum().mean(axis=1)
    hi = logp.rolling(LONG_WINDOW, min_periods=LONG_WINDOW).max()
    out[COMPONENTS[4]] = (logp >= hi + np.log(1.0 - NEAR_HIGH)).astype(float).where(hi.notna() & has).mean(axis=1)
    for c in leak_cols or []:
        out[c] = pd.Series(df[c].to_numpy(dtype=float), index=_keys(df)).groupby(level=0).mean().reindex(out.index)
    return out


def forward_up(tr: pd.DataFrame, h: int = FWD) -> np.ndarray:
    """訓練分割の行ごとの「同じ銘柄の訓練分割の中の次の h 本の y の和 ＞ 0」（1.0 ／ 0.0 ／ そろわなければ NaN）。"""
    d = pd.DataFrame({"s": tr["symbol"].astype(str).to_numpy(), "d": _keys(tr),
                      "y": tr["y"].to_numpy(dtype=float), "_i": np.arange(len(tr))}).sort_values(["s", "d"])
    fwd = d.groupby("s", sort=False)["y"].transform(
        lambda y: y[::-1].rolling(h, min_periods=h).sum()[::-1])
    v = (fwd > 0).astype(float).where(fwd.notna())
    out = np.full(len(tr), np.nan)
    out[d["_i"].to_numpy()] = v.to_numpy()
    return out


def nearest(Zc: np.ndarray, Zq: np.ndarray, k: int) -> np.ndarray:
    """問い合わせの日ごとに、候補の日のうち近い k 日の位置（距離の小さい順・同じ距離は古い日が先）。"""
    d2 = ((Zq[:, None, :] - Zc[None, :, :]) ** 2).sum(axis=2)
    return np.argsort(d2, axis=1, kind="stable")[:, :k]


def analog(tr, te, feats, ctx, *, random_days: bool = False):
    """(評価の行ごとの買い%, 記録)。random_days ＝ 状態を見ずに乱数で K 日を取る（基準線）。"""
    lk = [c for c in feats if c.startswith("LEAK_")]
    cols = ["ts", "symbol", "own_ret_1"] + lk
    st = day_state(pd.concat([tr[cols], te[cols]], ignore_index=True), lk)
    ktr, kte = _keys(tr), _keys(te)
    dtr, dte = np.unique(ktr), np.unique(kte)
    Str, Ste = st.loc[dtr], st.loc[dte]
    mu = Str.mean().fillna(0.0)
    sd = Str.std(ddof=0)
    sd = sd.where(sd > 0, 1.0).fillna(1.0)
    w = pd.Series(1.0, index=st.columns)
    if lk:
        w[lk] = float(np.sqrt(len(COMPONENTS) / len(lk)))
    Ztr = (Str - mu) / sd * w
    Zte = ((Ste - mu) / sd * w).fillna(0.0)

    k_plan = max(1, int(round(float(np.sqrt(len(dtr))))))
    seed = int(ctx.get("seed", 0)) if hasattr(ctx, "get") else 0
    buy = np.full(len(te), 50.0)
    doc = {"columns": ["own_ret_1"] + lk, "成分": list(COMPONENTS) + lk,
           "較正": "なし（近い K 日の先20日の上がる割合）", "先の日数": FWD,
           "K": k_plan, "使ったK": 0, "訓練の日数": int(len(dtr)), "候補の日数": 0, "評価の日数": int(len(dte)),
           "日の取り方": "乱数（fold ごとに 1 度）" if random_days else "状態の近い順", "シード": seed}

    rows = pd.DataFrame({"d": ktr, "s": tr["symbol"].astype(str).to_numpy(), "u": forward_up(tr)}).dropna(subset=["u"])
    if len(rows):
        U = rows.pivot_table(index="d", columns="s", values="u", aggfunc="mean").sort_index()
        ok = np.isfinite(Ztr.reindex(U.index).to_numpy(dtype=float)).all(axis=1)
        U = U.loc[ok]
        if len(U):
            k = min(k_plan, len(U))
            if random_days:
                pick = np.sort(np.random.default_rng(seed).choice(len(U), size=k, replace=False))
                idx = np.tile(pick, (len(dte), 1))
            else:
                idx = nearest(Ztr.loc[U.index].to_numpy(dtype=float), Zte.to_numpy(dtype=float), k)
            sub = U.to_numpy(dtype=float)[idx]                       # (評価の日, k, 銘柄)
            fin = np.isfinite(sub)
            num = np.where(fin, sub, 0.0).sum(axis=1)
            den = fin.sum(axis=1)
            tot = den.sum(axis=1)
            day_all = np.where(tot > 0, 100.0 * num.sum(axis=1) / np.maximum(tot, 1), 50.0)
            pct = np.where(den > 0, 100.0 * num / np.maximum(den, 1), day_all[:, None])
            pos = np.searchsorted(dte, kte)
            col = pd.Index([str(c) for c in U.columns]).get_indexer(te["symbol"].astype(str).to_numpy())
            buy = np.where(col >= 0, pct[pos, np.maximum(col, 0)], day_all[pos]).astype(float)
            srt = np.sort(idx, axis=1)
            runs = 1 + (np.diff(srt, axis=1) > FWD).sum(axis=1)
            doc.update({"使ったK": int(k), "候補の日数": int(len(U)),
                        "近い日の塊の数（20営業日より離れた塊・評価の日の平均）": _num(runs.mean(), 3)})

    doc["買い%の幅"] = _num(buy.max() - buy.min(), 3) if len(buy) else None
    doc["評価の行で買い%が閾値を超える割合"] = {str(t): (_num((buy > t).mean()) if len(buy) else None) for t in THETAS}
    if len(buy):
        g = pd.Series(buy).groupby(kte)
        doc["日の中の買い%の標準偏差（評価の日の平均）"] = _num(g.std().mean(), 3)
        corr = None
        if "y" in te:                                                # ⚠ 記録だけ（learn_if_null）。買い% には使わない
            my = pd.Series(te["y"].to_numpy(dtype=float)).groupby(kte).mean()
            fwd = my[::-1].rolling(FWD, min_periods=FWD).sum()[::-1]
            m = fwd.notna().to_numpy()
            a, b = g.mean().to_numpy(dtype=float)[m], fwd.to_numpy(dtype=float)[m]
            if len(a) >= 3 and a.std() > 0 and b.std() > 0:          # 定数どうしの相関は出さない（0 割りの警告を出さない）
                corr = _num(np.corrcoef(a, b)[0, 1])
        doc["評価の日ごとの買い%の平均と市場の先20日リターンの相関（記録だけ）"] = corr
    return buy, doc


def kn1(tr, te, feats, ctx):
    return analog(tr, te, feats, ctx)


register("detector", NAME)(kn1)


@register("model", BASELINE)
def random_days_baseline(tr, te, feats, ctx):
    """基準線（数えない）: 買い% ＞ 50 なら正（`fold_buy_pct` が 100 ／ 0 にする）。"""
    buy, _doc = analog(tr, te, feats, ctx, random_days=True)
    return buy - 50.0
