"""F1 フィルタ型。⚠ **モデルに通さず、説明変数とラベルの関係だけで選ぶ**（速い・弱い）。

⚠ **選別は必ず訓練分割の内側で呼ばれる**（Ambroise-McLachlan 2002）。
⚠ **ここに全期間のデータを渡してはいけない。** それをやると、どの手法でも成績が上がる。

手法を 1 つ足すときは、この下に関数を書いて `@register("selector", "名前")` を付けるだけでよい。
⚠ **配線（cli / 実験の回し方）は触らない。** `config/experiment/*.toml` に名前を足せば比較に入る。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.feature_selection import mutual_info_regression

from ail.registry import register


@register("selector", "全部使う（基準）")
def sel_all(X, y, k, ctx):
    """⚠ **これを超えられない選別手法は、選別する意味が無い。**"""
    return list(X.columns)


@register("selector", "乱択（基準）")
def sel_random(X, y, k, ctx):
    """⚠ **これに負ける手法は「選んでいない」のと同じ**（むしろ害）。"""
    rng = np.random.default_rng(ctx["seed"])
    return list(pd.Index(rng.choice(X.columns, min(k, len(X.columns)), replace=False)))


@register("selector", "F1-1 相関")
def sel_corr(X, y, k, ctx):
    s = X.apply(lambda c: abs(np.corrcoef(c, y)[0, 1]))
    return list(s.nlargest(k).index)


@register("selector", "F1-2 相互情報量")
def sel_mi(X, y, k, ctx):
    m = mutual_info_regression(X, y, random_state=ctx["seed"])
    return list(X.columns[np.argsort(m)[::-1][:k]])


@register("selector", "F1-3 mRMR")
def sel_mrmr(X, y, k, ctx):
    """関連が強く、⚠ **既に選んだものと重複しない**列を貪欲に足す。"""
    rel = X.apply(lambda c: abs(np.corrcoef(c, y)[0, 1])).fillna(0)
    C = X.corr().abs().fillna(0)
    chosen = [rel.idxmax()]
    while len(chosen) < min(k, len(X.columns)):
        cand = [c for c in X.columns if c not in chosen]
        score = {c: rel[c] - C.loc[c, chosen].mean() for c in cand}
        chosen.append(max(score, key=score.get))
    return chosen


@register("selector", "F1-5 検定+FDR")
def sel_fdr(X, y, k, ctx):
    """Benjamini-Hochberg で偽発見率を 10% に抑える。⚠ **k を使わない**（本数は結果として決まる）。"""
    p = X.apply(lambda c: stats.pearsonr(c, y)[1]).fillna(1.0).sort_values()
    m = len(p)
    thr = [(i + 1) / m * 0.10 for i in range(m)]
    keep = [c for i, (c, pv) in enumerate(p.items()) if pv <= thr[i]]
    return keep or [p.index[0]]                          # ⚠ 全部落ちたら 1 本だけ残す
