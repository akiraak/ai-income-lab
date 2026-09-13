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


# ⚠ **しきい値は事前固定**（プラン `plans/selectors-small-four.md` §1）。
# ⚠ **「定数を落とす」以上の意味を持たせない**（水準を上げると「小さく動く列」を捨てる別の手法になる）
VAR_MIN = 1e-12


@register("selector", "F1-4 分散しきい値")
def sel_variance(X, y, k, ctx):
    """⚠ **ラベルを見ない。** 分散が `VAR_MIN` 以下の列を落とし、残りを全部返す。

    ⚠ **k を使わない**（本数は結果として決まる。F1-5 検定+FDR・F2-3 Boruta と同じ）。
    ⚠ **上位 k 本に絞ると別の手法になる** — この手法は「しきい値を下回る列を落とす」だけで、
    ⚠ **順位を付ける手法ではない**（`sklearn.feature_selection.VarianceThreshold`）。

    ⚠ **現在の配線では定義から退化する**（プラン §0-1 の穴 2。⚠ **回す前に書いた**）:
    `cli/run.py` は ⚠ **`StandardScaler` を当てた表を渡す**ので、⚠ **訓練分割での分散は全列ちょうど 1**。
    ⚠ **順位を分けるのは数値誤差だけで、それは「乱択（基準）」と区別がつかない。**

    ⚠ **残る意味は「訓練分割で定数だった列」の検出だけ**（`StandardScaler` は分散 0 の列に
    `scale_ = 1` を当てるので、変換後は全ゼロになる）。⚠ **その列が 0 本なら出力は「全部使う」と同一。**
    ⚠ **これは欠陥ではなく、台帳 §3 の「ラベルを見ないので単独では判定できない」を
    ⚠ **配線まで降ろした形である。**
    """
    v = X.var(ddof=0)
    keep = [c for c in X.columns if v[c] > VAR_MIN]   # ⚠ 元の列順を保つ
    return keep or list(X.columns)      # ⚠ 全部が定数なら落とさない（この手法では判定できない）


# ⚠ **区間の数は事前固定**（プラン `plans/selectors-small-four.md` §1）。結果を見て動かさない
IC_SEGMENTS = 4
# ⚠ 区間 1 つの下限。これを割る区間は捨てる（順位相関が意味を持たなくなる）
IC_MIN_ROWS = 30


def _ic(Xs: pd.DataFrame, ys: np.ndarray) -> pd.Series:
    """区間 1 つの情報係数（Spearman）。⚠ **順位に直してから相関を取る。**

    ⚠ **定数列は相関が定義できない**（`np.corrcoef` が nan）。呼び出し側で 0 に寄せる。
    """
    yr = pd.Series(ys).rank().values
    return Xs.rank().apply(lambda c: np.corrcoef(c.values, yr)[0, 1])


@register("selector", "F1-7 IC の安定性")
def sel_ic_stability(X, y, k, ctx):
    """⚠ **訓練分割をさらに時間で 4 等分し、IC の符号がどれだけ揃うか**で選ぶ。

    ⚠ **平均の IC が大きい列ではなく、符号がぶれない列**を選ぶのがこの手法の主張である
    （大きさで選ぶのは F1-1 相関の仕事）。同点は |IC| の平均で割る。

    ⚠ **行の並びが時刻順であることを前提にする**（`splits.folds_by_dates` が `ts` で並べ替え、
    `cli/run.py` がその順のまま `DataFrame` を作る）。⚠ **並びが崩れると「時系列の」安定性ではなくなる。**
    ⚠ **区間はどれも訓練分割の内側**なので、検証分割には触れない（rules.md 9 章 規約 1）。
    """
    segs = [s for s in np.array_split(np.arange(len(X)), IC_SEGMENTS) if len(s) >= IC_MIN_ROWS]
    if not segs:
        # ⚠ **区間に割れないときは 1 区間にする**（= 符号の一致は測れず、|IC| 順に退化する）。
        # ⚠ **黙って別の手法に落とさない**ため、ここで退化させて名前は変えない
        segs = [np.arange(len(X))]
    T = pd.concat([_ic(X.iloc[s], np.asarray(y)[s]) for s in segs], axis=1).fillna(0.0)
    score = pd.DataFrame({"一致": T.apply(lambda r: abs(np.sign(r).sum()) / len(r), axis=1),
                          "強さ": T.abs().mean(axis=1)})
    order = score.sort_values(["一致", "強さ"], ascending=False).index
    return list(order[:min(int(k), X.shape[1])])


@register("selector", "F1-5 検定+FDR")
def sel_fdr(X, y, k, ctx):
    """Benjamini-Hochberg で偽発見率を 10% に抑える。⚠ **k を使わない**（本数は結果として決まる）。"""
    p = X.apply(lambda c: stats.pearsonr(c, y)[1]).fillna(1.0).sort_values()
    m = len(p)
    thr = [(i + 1) / m * 0.10 for i in range(m)]
    keep = [c for i, (c, pv) in enumerate(p.items()) if pv <= thr[i]]
    return keep or [p.index[0]]                          # ⚠ 全部落ちたら 1 本だけ残す
