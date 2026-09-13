"""台帳の空白を埋める「手間 小」の 4 件の検査（プラン `plans/selectors-small-four.md`）。

⚠ **見るのは 4 つ**: (1) 契約を守る（`X` の列名の部分集合・本数 ≤ k）、(2) ⚠ **ラベルを見ているか**
（並べ替えると選択が変わる。F1-4 だけは**変わらない**のが正しい）、(3) 決定性、
(4) ⚠ **その手法が主張していることを実際にやっているか**（F1-7 は「大きさ」ではなく「符号の安定性」で選ぶ）。

⚠ **`Xtr` は `cli/run.py` が標準化したあとの表で、行は時刻順**（`splits.folds_by_dates` が並べ替える）。
⚠ **ここのテストも同じ前提で表を作る。** 崩すと F1-7 は「時系列の」安定性を測っていないことになる。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import registry
from ail.validation import prep
import ail.bootstrap  # noqa: F401

# ⚠ **4 件のうち 3 件は `selector`、F5-1 だけ `transform`**（§0-1 の穴 1）。種類が違うので分けて測る
SELECTORS = ["F2-2 RFE", "F1-7 IC の安定性", "F1-4 分散しきい値"]
# ⚠ **ラベルを見る手法だけ**。F1-4 は見ないのが定義（台帳 §3）なので別に扱う
SUPERVISED = ["F2-2 RFE", "F1-7 IC の安定性"]
# ⚠ **k で本数が決まる手法だけ。** F1-4 は ⚠ **k を使わない**（しきい値で本数が決まる。
# F1-5 検定+FDR・F2-3 Boruta と同じ）ので、k の検査から外す
USES_K = ["F2-2 RFE", "F1-7 IC の安定性"]


def _panel(n=2000, seed=3):
    """⚠ **行は時刻順**（0 が最も古い）。4 区間に割れる長さにしてある。

    列の作り分け:
      - `own_stable` … ⚠ **弱いが 4 区間とも同じ符号**
      - `own_flip`   … ⚠ **強いが最後の区間だけ符号が反転**（全体の相関は大きいまま）
      - `own_n*`     … 雑音
    """
    rng = np.random.default_rng(seed)
    y = rng.normal(scale=0.01, size=n)
    seg = np.array_split(np.arange(n), 4)
    flip = np.ones(n)
    flip[seg[3]] = -1.0                       # ⚠ 4 区間のうち 1 つだけ向きが逆（一致率 0.5）
    X = pd.DataFrame({
        "own_stable": 0.30 * y + rng.normal(scale=0.01, size=n),
        "own_flip": 3.00 * y * flip + rng.normal(scale=0.01, size=n),
        **{f"own_n{i}": rng.normal(size=n) for i in range(6)},
    })
    # ⚠ **標準化して渡す。** `cli/run.py` は `StandardScaler` を当てた表を selector に渡すので、
    # ⚠ **尺度がばらばらの表でテストすると本番と違う挙動を測ることになる**
    # （F2-2 は Ridge の係数を見るので、⚠ **尺度が揃っていないと尺度だけで順位が決まる**）
    return (X - X.mean()) / X.std(ddof=0), y


CTX = {"seed": 0}


def test_4件とも登録されている():
    """⚠ **名前の打ち間違いは実行時まで分からない**ので、`resolve_all` をテストで先に通す。"""
    assert set(registry.resolve_all("selector", SELECTORS)) == set(SELECTORS)
    assert set(registry.resolve_all("transform", ["F5-1 PCA"])) == {"F5-1 PCA"}


@pytest.mark.parametrize("name", SELECTORS)
def test_契約を守る(name):
    """⚠ **返すのは `X` の列名の部分集合**（`cli/run.py` が `Xtr[cols]` を取るため）。"""
    X, y = _panel()
    cols = registry.resolve("selector", name)(X, y, 3, dict(CTX))
    assert cols, f"{name} が 1 本も返さなかった"
    assert set(cols) <= set(X.columns), f"{name} が X に無い列を返した: {set(cols) - set(X.columns)}"
    assert len(cols) == len(set(cols)), f"{name} が同じ列を 2 回返した"
    if name in USES_K:
        assert len(cols) <= 3, f"{name} が k=3 を超えて {len(cols)} 本返した"


@pytest.mark.parametrize("name", SELECTORS)
def test_決定性(name):
    """⚠ **同じ種・同じ入力なら 1 本も違わない**（rules.md 10 章。再現の前提）。"""
    X, y = _panel()
    fn = registry.resolve("selector", name)
    assert fn(X, y, 4, dict(CTX)) == fn(X, y, 4, dict(CTX)), f"{name} が呼ぶたび違う列を返す"


@pytest.mark.parametrize("name", SUPERVISED)
def test_ラベルを見ている(name):
    """⚠ **y を並べ替えたら選択が変わるはず。** 変わらなければラベルを使っていない。"""
    X, y = _panel()
    fn = registry.resolve("selector", name)
    shuffled = np.random.default_rng(11).permutation(y)
    assert fn(X, y, 2, dict(CTX)) != fn(X, shuffled, 2, dict(CTX)), \
        f"{name} が y を並べ替えても同じ列を選んだ（ラベルを見ていない）"


def test_F1_7は大きさではなく符号の安定性で選ぶ():
    """⚠ **この手法の主張そのものの検査。**

    `own_flip` は ⚠ **相関は大きいが 4 区間のうち 1 つで符号が逆**、`own_stable` は
    ⚠ **相関は小さいが 4 区間とも同じ符号**。F1-1（相関）と F1-7 で ⚠ **選ぶ列が割れる**のが正しい。
    割れなければ、F1-7 は F1-1 の言い換えでしかない。
    """
    X, y = _panel()
    ic7 = registry.resolve("selector", "F1-7 IC の安定性")(X, y, 1, dict(CTX))
    corr = registry.resolve("selector", "F1-1 相関")(X, y, 1, dict(CTX))
    assert ic7 == ["own_stable"], f"F1-7 が符号の安定した列を選ばなかった: {ic7}"
    assert corr == ["own_flip"], f"対照が崩れている（F1-1 が強い列を選ばなかった）: {corr}"


def test_F1_7は区間に割れなくても落ちない():
    """⚠ **(B) 銘柄別は訓練が薄い**（rules.md 13-6 の 5）。区間が取れないときは 1 区間に退化する。"""
    X, y = _panel(n=40)
    cols = registry.resolve("selector", "F1-7 IC の安定性")(X, y, 2, dict(CTX))
    assert len(cols) == 2 and set(cols) <= set(X.columns)


# --- F1-4 分散しきい値 — ⚠ **退化を先に測る**（プラン §0-1 の穴 2）----------------

def test_F1_4はラベルを見ない():
    """⚠ **これは欠陥ではなく定義**（台帳 §3）。⚠ **見ていたら実装のほうが間違っている。**"""
    X, y = _panel()
    fn = registry.resolve("selector", "F1-4 分散しきい値")
    shuffled = np.random.default_rng(5).permutation(y)
    assert fn(X, y, 3, dict(CTX)) == fn(X, shuffled, 3, dict(CTX))


def test_F1_4は標準化された表では退化する():
    """⚠ **回す前に書いた検査**（プラン §0-1 の穴 2）。

    ⚠ **本番の `Xtr` は `StandardScaler` 済みで分散が全列ちょうど 1** なので、
    ⚠ **落とせる列が 1 本も無い ＝ 出力は「全部使う」と 1 列も違わない**。
    ⚠ **これを結果として報告せず、前提として先に固定しておく。**
    ⚠ **k を変えても同じ**（この手法は k を使わない）ことまで見る。
    """
    X, y = _panel()
    assert np.allclose(X.var(ddof=0).values, 1.0), "対照が崩れている（表が標準化されていない）"
    fn = registry.resolve("selector", "F1-4 分散しきい値")
    allcols = registry.resolve("selector", "全部使う（基準）")(X, y, X.shape[1], dict(CTX))
    for k in (2, 8, X.shape[1]):
        assert fn(X, y, k, dict(CTX)) == allcols, \
            f"⚠ 標準化された表で F1-4 が「全部使う」と違う列を返した（k={k}。退化の前提が崩れている）"


def test_F1_4は定数列だけを落とす():
    """⚠ **唯一残る意味の検査。** `StandardScaler` は分散 0 の列を全ゼロにするので、そこだけ拾える。"""
    X, y = _panel()
    X = X.assign(own_const=0.0)
    cols = registry.resolve("selector", "F1-4 分散しきい値")(X, y, X.shape[1], dict(CTX))
    assert "own_const" not in cols, "⚠ 定数列を落とせていない"
    assert set(cols) == set(X.columns) - {"own_const"}, "⚠ 定数列以外も落ちた"


# --- F5-1 PCA（`transform`）と配線 -------------------------------------------

def test_F5_1は成分をk本作る():
    X, _ = _panel()
    Xtr, Xte, doc = registry.resolve("transform", "F5-1 PCA")(
        X.iloc[:1500], X.iloc[1500:], dict(CTX, k=4))
    assert list(Xtr.columns) == [f"pc{i}" for i in range(4)]
    assert list(Xte.columns) == list(Xtr.columns)
    assert len(Xtr) == 1500 and len(Xte) == 500
    # ⚠ **`fitted/` に残す中身**（rules.md 3 章 B の再現の規約）
    assert len(doc["explained_variance_ratio"]) == 4
    assert doc["columns"] == list(X.columns)


def test_F5_1は検証分割を見ないでfitしている():
    """⚠ **この検査がこの手法の要**（rules.md 3 章 B）。

    ⚠ **検証分割を丸ごと別物に差し替えても、訓練分割の変換結果が 1 ビットも変わらない**なら、
    ⚠ **fit に検証分割が入っていない**ということ。入っていたら成分が動いて結果が変わる。
    """
    X, _ = _panel()
    tr, te = X.iloc[:1500], X.iloc[1500:]
    fn = registry.resolve("transform", "F5-1 PCA")
    a, _, _ = fn(tr, te, dict(CTX, k=4))
    b, _, _ = fn(tr, te * 1000.0 + 7.0, dict(CTX, k=4))    # ⚠ 検証分割だけを壊す
    assert np.array_equal(a.values, b.values), \
        "⚠ 検証分割を変えたら訓練分割の主成分が動いた（fit に検証分割が入っている）"


def test_変換が無ければ配線は素通りする():
    """⚠ **足した 1 段が既存の実行に影響しない根拠。**

    ⚠ **同じオブジェクトが返ることまで見る**（コピーすら作らない）。
    ⚠ **ここが崩れると、配線を足したことが静かに全実行に効く。**
    """
    X, _ = _panel(n=100)
    tr, te = X.iloc[:80], X.iloc[80:]
    a, b, doc = prep.apply({}, tr, te, dict(CTX))
    assert a is tr and b is te and doc is None
    assert prep.label({}, "全部使う（基準）") == "全部使う（基準）"


def test_変換があれば手法名に混ざる():
    """⚠ **台帳は手法名の先頭から ID を読む**（`ail/catalog.py` の `_ID`）。

    ⚠ **混ざらないと「全部使う（基準）」の行に化けて、その試行が数えられない**（rules.md 11 章 規約 4）。
    """
    from ail import catalog
    lab = prep.label({"transform": "F5-1 PCA"}, "全部使う（基準）")
    assert lab == "F5-1 PCA ＋ 全部使う（基準）"
    assert catalog.canonical(lab)[0] == "F5-1", "台帳が F5-1 の試行として読めない"
    assert catalog.is_trial({"検証方式": "閾値売買", "手法名": lab}), "試行に数えられない"
