"""レジストリ `20261125-unsup-regime-cluster`（参考なし・枠の外。記録 dev-loop.md）:
ラベルを使わずに **日ごとの断面の指標 6 本** で日を k-means のクラスタに分け、クラスタごとの訓練分割の「先 20 日リターンが正だった割合」
× 100 をそのまま買い% にする検知器 RG1（学習器なし・全銘柄で同じ値）。

- 日ごとの指標（表の `own_ret_1` だけから。その日までの値だけを使う ＝ 評価の日の指標も過去の足だけ）:
  1 断面平均リターン ／ 2 断面のばらつき（標準偏差）／ 3 ボラの水準（銘柄ごとの直近 20 日の標準偏差の断面平均）／
  4 平均相関（直近 20 日。等ウェイト平均の分散から引く近似 ＝ (N²·Var(平均) − Σσ²) ／ ((Σσ)² − Σσ²)）／
  5 直近 20 日の市場リターン（1 の 20 日和）／ 6 上げた銘柄の割合の直近 20 日平均。
  ⚠ レジストリの structure は「新しい層 regime_」だが、指標は日ごとに 1 つで全銘柄に同じ値 ＝ 表に列として足さずに検知器の中で作る
  （`ail/features`・`cli/build.py` の `layer_order` を通さない ＝ 表を作り直さない。入力も判定も structure と同じ）。
- k-means: K ＝ 3 ／ 6（⚠ 事前固定・結果を見て選ばない）・シード 0・n_init 10。訓練分割の日だけで欠損の埋め（訓練の中央値）・標準化・fit。
  評価の日は近い中心へ。
- クラスタの割合: 訓練分割の行ごとに、同じ銘柄の **訓練分割の中の** 次の 20 本の `y`（翌日 log リターン）の和 ＞ 0 か。
  20 本そろわない行（訓練の終わりの 19 本）は数えない（評価の y を読まない）。数える行が 0 のクラスタは 50。
- 買い% ＝ その日のクラスタの割合 × 100。⚠ 較正しない・ctx の model は呼ばない（Ridge は識別項目の綴りのためだけ）。
- leak 対照: 表に LEAK_ 列があれば、その断面平均を 7 本目の指標として足す（先の値がクラスタを分ける）。
- 記録: クラスタごとの訓練の日数・行数・割合・評価の日数・中心・買い% の幅・持つ日の割合。
  `learn_if_null` のため、評価の行のクラスタごとの翌日平均 y も記録する（⚠ 記録だけ ＝ 買い% には使わない。テストが見る）。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler

from ail.registry import register

KS = (3, 6)            # ⚠ 事前固定（レジストリ）
SEED = 0
WINDOW = 20            # 指標の観測期間
FWD = 20               # 先 20 日
INDICATORS = ("断面平均リターン", "断面のばらつき", "ボラの水準", "平均相関20日", "市場リターン20日", "上げた割合20日")


def name_of(k: int) -> str:
    return f"RG1 断面の局面のクラスタごとの先20日の上がる割合（k-means・{k}個）"


def day_indicators(df: pd.DataFrame, leak_cols: list[str] | None = None) -> pd.DataFrame:
    """日ごとの指標（index ＝ 日）。rolling は過去の足だけ。"""
    ts = pd.to_datetime(df["ts"])
    R = pd.DataFrame({"ts": ts.to_numpy(), "s": df["symbol"].astype(str).to_numpy(),
                      "r": df["own_ret_1"].to_numpy(dtype=float)}).pivot_table(index="ts", columns="s", values="r",
                                                                                    aggfunc="mean").sort_index()
    m = R.mean(axis=1)
    out = pd.DataFrame(index=R.index)
    out[INDICATORS[0]] = m
    out[INDICATORS[1]] = R.std(axis=1)
    sd = R.rolling(WINDOW, min_periods=WINDOW).std()
    out[INDICATORS[2]] = sd.mean(axis=1)
    var_i = sd ** 2
    sum_var = var_i.sum(axis=1, min_count=1)
    sum_sd = sd.sum(axis=1, min_count=1)
    n = sd.notna().sum(axis=1).astype(float)
    var_m = m.rolling(WINDOW, min_periods=WINDOW).var()
    den = sum_sd ** 2 - sum_var
    out[INDICATORS[3]] = ((n ** 2) * var_m - sum_var) / den.where(den > 0)
    out[INDICATORS[4]] = m.rolling(WINDOW, min_periods=WINDOW).sum()
    up = (R > 0).astype(float).where(R.notna())
    out[INDICATORS[5]] = up.mean(axis=1).rolling(WINDOW, min_periods=WINDOW).mean()
    for c in leak_cols or []:
        out[c] = pd.Series(df[c].to_numpy(dtype=float), index=ts.to_numpy()).groupby(level=0).mean().reindex(out.index)
    return out


def forward_up(tr: pd.DataFrame, h: int = FWD) -> np.ndarray:
    """訓練分割の行ごとの「同じ銘柄の訓練分割の中の次の h 本の y の和 ＞ 0」（1.0 ／ 0.0 ／ そろわなければ NaN）。"""
    d = pd.DataFrame({"s": tr["symbol"].astype(str).to_numpy(), "ts": pd.to_datetime(tr["ts"]).to_numpy(),
                      "y": tr["y"].to_numpy(dtype=float), "_i": np.arange(len(tr))}).sort_values(["s", "ts"])
    fwd = d.groupby("s", sort=False)["y"].transform(
        lambda y: y[::-1].rolling(h, min_periods=h).sum()[::-1])
    v = (fwd > 0).astype(float).where(fwd.notna())
    out = np.full(len(tr), np.nan)
    out[d["_i"].to_numpy()] = v.to_numpy()
    return out


def regimes(tr, te, feats, k: int):
    """(訓練の行のクラスタ, 評価の行のクラスタ, 記録)。埋め・標準化・k-means は訓練分割の日だけ。"""
    lk = [c for c in feats if c.startswith("LEAK_")]
    both = pd.concat([tr.assign(_s=0), te.assign(_s=1)], ignore_index=True)
    ind = day_indicators(both, lk)
    dtr = np.unique(pd.to_datetime(tr["ts"]).to_numpy())
    dte = np.unique(pd.to_datetime(te["ts"]).to_numpy())
    Itr, Ite = ind.loc[dtr], ind.loc[dte]
    med = Itr.median().fillna(0.0)
    Itr, Ite = Itr.fillna(med), Ite.fillna(med)
    sc = StandardScaler().fit(Itr)
    km = KMeans(n_clusters=k, random_state=SEED, n_init=10).fit(sc.transform(Itr))
    ctr_d = pd.Series(km.labels_.astype(int), index=Itr.index)
    cte_d = pd.Series(km.predict(sc.transform(Ite)).astype(int), index=Ite.index)
    rtr = ctr_d.reindex(pd.to_datetime(tr["ts"]).to_numpy()).to_numpy().astype(int)
    rte = cte_d.reindex(pd.to_datetime(te["ts"]).to_numpy()).to_numpy().astype(int)
    doc = {"K": k, "シード": SEED, "指標": list(INDICATORS) + lk,
           "訓練の日のクラスタ": {str(c): int((ctr_d == c).sum()) for c in range(k)},
           "評価の日のクラスタ": {str(c): int((cte_d == c).sum()) for c in range(k)},
           "中心（標準化）": np.round(km.cluster_centers_, 3).tolist()}
    return rtr, rte, doc


def rates(ctr: np.ndarray, up: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """クラスタごとの (割合 × 100, 数えた行)。数える行が 0 のクラスタは 50。"""
    pct, cnt = np.full(k, 50.0), np.zeros(k, dtype=int)
    for c in range(k):
        u = up[(ctr == c) & np.isfinite(up)]
        cnt[c] = len(u)
        if len(u):
            pct[c] = 100.0 * float(u.mean())
    return pct, cnt


def rg1(k: int, tr, te, feats, ctx):
    ctr, cte, rdoc = regimes(tr, te, feats, k)
    up = forward_up(tr)
    pct, cnt = rates(ctr, up, k)
    buy = pct[cte]
    yte = te["y"].to_numpy(dtype=float) if "y" in te else np.full(len(te), np.nan)
    ev = {}
    for c in range(k):                                   # ⚠ 記録だけ（learn_if_null）。買い% には使わない
        y = yte[(cte == c) & np.isfinite(yte)]
        ev[str(c)] = round(float(y.mean()), 7) if len(y) else None
    doc = {**rdoc, "columns": ["own_ret_1"] + [c for c in feats if c.startswith("LEAK_")],
           "較正": "なし（訓練分割のクラスタごとの先20日の上がる割合）",
           "クラスタごとの数えた行（訓練）": {str(c): int(cnt[c]) for c in range(k)},
           "クラスタごとの買い%": {str(c): round(float(pct[c]), 4) for c in range(k)},
           "買い%の幅": round(float(pct.max() - pct.min()), 3),
           "評価の日で買い%が50を超える割合": round(float((buy > 50).mean()), 4) if len(buy) else None,
           "評価の行のクラスタごとの翌日平均y（記録だけ）": ev}
    return buy, doc


for _k in KS:
    register("detector", name_of(_k))(lambda tr, te, feats, ctx, _k=_k: rg1(_k, tr, te, feats, ctx))
