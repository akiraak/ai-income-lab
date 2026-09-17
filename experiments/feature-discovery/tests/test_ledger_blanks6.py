"""台帳の空白 6 件（F1-6・F2-1・F3-4・F3-6・F4-4・F5-4）の配線（[プラン](../../../docs/plans/ledger-blanks-six.md) §4）。

⚠ **ここで落としたいのは 5 つ。**
  1. 選別の契約（返すのは入力にある列・k 本以内。k を使わない手法は本数が信号）
  2. ⚠ **距離相関が「線形相関では見えない依存」を拾えること**（拾えないなら実装が誤っている）
  3. ⚠ **SHAP が効く列を 1 位にすること**（LightGBM の `pred_contrib` を正しく切り出せているか）
  4. ⚠ **knockoffs の枠が働くこと**（雑音だらけの表で、選んだ中の偽物が目標の近くに収まる）
  5. 変換の形（多項式 665 列 ／ ウェーブレットは窓を係数に置き換え、own 列はそのまま）

⚠ **登録名は F 番号で始める**（`ail/catalog.py` の `_ID` が読む。⚠ **外れると台帳に試行として載らない**）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import catalog, registry
from ail.selectors import embedded, filter as filt, representation
import ail.bootstrap  # noqa: F401

NAMES = ["F1-6 距離相関・HSIC", "F2-1 前進選択・後退除去", "F3-4 SHAP", "F3-6 Model-X knockoffs"]
TRANSFORMS = ["F4-4 多項式・交互作用の展開", "F5-4 ウェーブレット・スペクトル"]


def _ctx(**kw):
    return {"seed": 0, "k": 8, "lgbm_estimators": 60, **kw}


def _linear(n=1200, p=10, seed=0):
    """`own_f0` だけが線形に効く表（標準化済みの表を模す）。"""
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, p)), columns=[f"own_f{i}" for i in range(p)])
    y = 0.8 * X["own_f0"].to_numpy() + rng.normal(scale=0.3, size=n)
    return X, y


def _nonlinear(n=1500, p=8, seed=1):
    """⚠ **`own_f0` は `y = |x|` の関係**（線形相関はほぼ 0 だが独立ではない）。"""
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, p)), columns=[f"own_f{i}" for i in range(p)])
    y = np.abs(X["own_f0"].to_numpy()) + rng.normal(scale=0.2, size=n)
    return X, y


# --- 1. 契約 ---------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_selector_returns_columns_of_the_input(name):
    X, y = _linear()
    cols = registry.resolve("selector", name)(X, y, 8, _ctx())
    assert cols, f"{name} が 1 本も返していない"
    assert set(cols) <= set(X.columns)
    assert len(cols) == len(set(cols))
    # ⚠ **k を使う手法（F1-6・F2-1・F3-4）は k 本以内**。F3-6 は枠が本数を決めるので上限だけ見る
    assert len(cols) <= X.shape[1]
    if name != "F3-6 Model-X knockoffs":
        assert len(cols) <= 8


@pytest.mark.parametrize("name", NAMES + TRANSFORMS)
def test_registered_name_starts_with_a_catalog_id(name):
    """⚠ **台帳は手法名の先頭から ID を読む**（外れると試行として数えられない）。"""
    assert catalog._ID.match(name), name


# --- 2. F1-6 距離相関 -------------------------------------------------------

def test_distance_correlation_sees_a_nonlinear_dependence():
    """⚠ **線形相関では見えない `y = |x|` を拾えること。** 拾えないなら実装が誤っている。"""
    X, y = _nonlinear()
    d0 = filt.distance_correlation(X["own_f0"].to_numpy(), y)
    others = [filt.distance_correlation(X[c].to_numpy(), y) for c in X.columns[1:]]
    assert d0 > max(others) * 1.5, (d0, max(others))
    # ⚠ **線形相関は同じ列をほとんど見つけられない**（この対比が手法の主張そのもの）
    r = abs(np.corrcoef(X["own_f0"].to_numpy(), y)[0, 1])
    assert r < 0.15, r
    picked = registry.resolve("selector", "F1-6 距離相関・HSIC")(X, y, 3, _ctx())
    assert picked[0] == "own_f0"


def test_distance_correlation_subsamples_and_is_reproducible():
    """⚠ **部分標本は種で決まる**（同じ種なら同じ結果）。⚠ **大きさは事前固定で動かさない。**"""
    assert filt.DCOR_ROWS == 5000
    X, y = _nonlinear(n=200)
    a = registry.resolve("selector", "F1-6 距離相関・HSIC")(X, y, 4, _ctx())
    b = registry.resolve("selector", "F1-6 距離相関・HSIC")(X, y, 4, _ctx())
    assert a == b


# --- 3. F2-1 前進選択 -------------------------------------------------------

def test_forward_selection_finds_the_useful_column():
    X, y = _linear(n=600, p=6)
    cols = registry.resolve("selector", "F2-1 前進選択・後退除去")(X, y, 2, _ctx(sfs_jobs=1))
    assert "own_f0" in cols and len(cols) == 2


# --- 4. F3-4 SHAP -----------------------------------------------------------

def test_shap_ranks_the_useful_column_first():
    X, y = _linear(n=800, p=6)
    cols = registry.resolve("selector", "F3-4 SHAP")(X, y, 3, _ctx())
    assert cols[0] == "own_f0"


# --- 5. F3-6 knockoffs ------------------------------------------------------

def test_knockoffs_keep_the_false_discoveries_low():
    """⚠ **効く列 3 本 ＋ 雑音 27 本。** ⚠ **選んだ中の雑音が多すぎないこと**（枠が働いている証拠）。"""
    rng = np.random.default_rng(3)
    n, p = 2000, 30
    X = pd.DataFrame(rng.normal(size=(n, p)), columns=[f"own_f{i}" for i in range(p)])
    y = (1.2 * X["own_f0"] + 1.0 * X["own_f1"] + 0.8 * X["own_f2"]).to_numpy() \
        + rng.normal(scale=0.5, size=n)
    cols = registry.resolve("selector", "F3-6 Model-X knockoffs")(X, y, 8, _ctx())
    true = {"own_f0", "own_f1", "own_f2"}
    assert true & set(cols), cols                       # ⚠ 本物を 1 本は拾う
    noise = [c for c in cols if c not in true]
    assert len(noise) / max(1, len(cols)) <= 0.5, cols  # ⚠ 偽物だらけにならない
    assert embedded.KNOCKOFF_Q == 0.1                   # ⚠ 目標 FDR は事前固定


# --- 6. F4-4 多項式 ---------------------------------------------------------

def test_polynomial_expansion_column_count_and_train_only_fit():
    X, y = _linear(n=400, p=35)
    tr, te, doc = registry.resolve("transform", TRANSFORMS[0])(X.iloc[:300], X.iloc[300:], _ctx())
    # ⚠ 35 → 35（1 次）＋ 35（2 乗）＋ 595（交互作用）＝ 665
    assert tr.shape[1] == 665 and te.shape[1] == 665 and doc["出力の列"] == 665
    assert list(tr.columns) == list(te.columns)
    # ⚠ **検証の行を減らしても訓練側の出力は変わらない**（検証で何かを学んでいない）
    tr2, _, _ = registry.resolve("transform", TRANSFORMS[0])(X.iloc[:300], X.iloc[300:350], _ctx())
    assert np.array_equal(tr.to_numpy(), tr2.to_numpy())


# --- 7. F5-4 ウェーブレット --------------------------------------------------

def _seq_frame(n=300, window=60, seed=5):
    rng = np.random.default_rng(seed)
    r = rng.normal(scale=0.01, size=(n, window))
    cols = {f"own_seq60_r{k}": r[:, window - 1 - k] for k in range(window)}   # r0 が足 i（新しい順）
    cols["own_ret_1"] = r[:, -1]
    return pd.DataFrame(cols)


def test_wavelet_replaces_the_window_and_keeps_own_columns():
    import pywt

    X = _seq_frame()
    tr, te, doc = registry.resolve("transform", TRANSFORMS[1])(X.iloc[:200], X.iloc[200:], _ctx())
    assert doc["wavelet"] == representation.WAVELET and doc["level"] == representation.WAVELET_LEVEL
    assert doc["窓の列"] == 60 and doc["残した列"] == 1
    # ⚠ 生の窓は残っていない ／ own の列はそのまま
    assert not [c for c in tr.columns if c.startswith("own_seq")]
    assert "own_ret_1" in tr.columns and list(tr.columns) == list(te.columns)
    # ⚠ **古い → 新しいの向きで変換している**（逆に並べると係数が変わる）
    w = X.iloc[:200][[f"own_seq60_r{k}" for k in range(59, -1, -1)]].to_numpy(dtype=float)
    expect = np.hstack(pywt.wavedec(w, representation.WAVELET,
                                    level=representation.WAVELET_LEVEL, axis=1))
    got = tr[[c for c in tr.columns if c.startswith("wv_")]].to_numpy()
    assert got.shape == expect.shape and np.allclose(got, expect)
    assert doc["係数の列"] == expect.shape[1]


def test_wavelet_stops_when_the_window_is_missing():
    X, _ = _linear(n=50, p=4)
    with pytest.raises(SystemExit):
        registry.resolve("transform", TRANSFORMS[1])(X.iloc[:40], X.iloc[40:], _ctx())


def test_knockoffs_are_not_copies_of_the_original_columns():
    """⚠ **2026-09-15 に踏んだ罠の回帰テスト。**

    ⚠ **`E` を `default_rng(seed)` で引くと、同じ種で作った合成データと E が一致し、
    ⚠ **偽物が本物のコピーになる**（対角の相関 0.99）。⚠ **そうなると枠は何も検出できない。**
    ⚠ **データの種と ctx の種をわざと同じにして確かめる。**
    """
    n, p, seed = 800, 6, 0
    X = np.random.default_rng(seed).normal(size=(n, p))       # ⚠ ctx の種と同じ流れ
    Xt = embedded.gaussian_knockoffs(X, seed)
    corr = np.array([abs(np.corrcoef(X[:, j], Xt[:, j])[0, 1]) for j in range(p)])
    assert corr.max() < 0.3, corr                              # ⚠ コピーになっていない
    # ⚠ **偽物どうしの相関の構造は本物と同じであるべき**（等相関構成の前提）
    assert abs(float(np.corrcoef(X[:, 0], X[:, 1])[0, 1]) - float(np.corrcoef(Xt[:, 0], Xt[:, 1])[0, 1])) < 0.2
