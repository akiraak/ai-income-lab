"""台帳の空白「大」2 件の検査（プラン `plans/archive/ledger-blanks-large-two.md` §6）。

⚠ **見るのは 5 つ**: (1) ⚠ **行をまたいで情報が漏れていないか**（F5-3 の急所）、
(2) ⚠ **時間の向きを正しく読んでいるか**、(3) 決定性、(4) 列の入れ替え（窓を落とし `own` を残す）、
(5) ⚠ **落とす列を訓練分割だけで決めているか**。

⚠ **`Xtr` は `cli/run.py` が標準化したあとの表**で、⚠ **索引は振り直されている**（`RangeIndex`）。
⚠ **ここのテストも同じ前提で表を作る。**

⚠ **並列は切って回す**（`N_JOBS = 0` / `MP_JOBS = 1`）。プロセスの起動が測定の外で効くため。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail.features import tsfresh as tsf
from ail.selectors import representation as rep
from ail import registry
import ail.bootstrap  # noqa: F401

WIN = 60
NAMES = [tsf.NAME, "F5-3 行列プロファイル（モチーフ）"]


@pytest.fixture(autouse=True)
def _serial(monkeypatch):
    """⚠ **テストの中では並列を切る。** 本番の既定（16）は `config` ではなく定数なので直接差す。"""
    monkeypatch.setattr(tsf, "N_JOBS", 0)
    monkeypatch.setattr(rep, "MP_JOBS", 1)


def _frame(n=24, seed=5):
    """⚠ **窓は `own_seq60_r{k}`（k = 0 が足 i ＝ 最も新しい）。** `own_` の普通の列も混ぜる。"""
    rng = np.random.default_rng(seed)
    x = {f"own_seq60_r{k}": rng.normal(size=n) for k in range(WIN)}
    x["own_ret_1"] = rng.normal(size=n)
    x["own_vol_20"] = rng.normal(size=n)
    return pd.DataFrame(x)


def _apply(name, Xtr, Xte):
    return registry.resolve("transform", name)(Xtr, Xte, {})


# --- 1. ⚠ 行をまたいで情報が漏れていないか -------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_行を並べ替えても各行の出力は変わらない(name):
    """⚠ **これが崩れたら、ある行の特徴量が別の行の中身から作られている。**

    ⚠ **F5-3 の急所**（系列全体に `stumpy.stump` を当てると、ここが必ず落ちる）。
    """
    X = _frame()
    perm = np.random.default_rng(0).permutation(len(X))
    a, _, _ = _apply(name, X, X.iloc[:4])
    b, _, _ = _apply(name, X.iloc[perm].reset_index(drop=True), X.iloc[:4])
    # ⚠ **落とす列は訓練分割から決まる**ので、並べ替えると残る列が変わりうる。共通の列で比べる
    cols = [c for c in a.columns if c in set(b.columns)]
    # ⚠ **F5-3 は 5 列しか作らない**（＋ `own` 2 列）。手法ごとに下限が違う
    assert len(cols) >= (7 if name.startswith("F5-3") else 100)
    np.testing.assert_allclose(a[cols].to_numpy()[perm], b[cols].to_numpy(), rtol=1e-9, atol=1e-9)


# --- 2. ⚠ 時間の向き -----------------------------------------------------

def test_窓は古いものから新しいものへ並べ直される():
    """⚠ **列は `r0` が最新。** そのまま渡すと時間が逆になる。"""
    order = tsf.window_order(_frame().columns)
    assert order[0] == f"own_seq60_r{WIN - 1}" and order[-1] == "own_seq60_r0"
    assert len(order) == WIN


@pytest.mark.parametrize("name", NAMES)
def test_窓を時間方向に反転すると出力が変わる(name):
    """⚠ **向きを読んでいない実装は、反転しても同じ数字を返す。**"""
    X = _frame()
    flip = X.rename(columns={f"own_seq60_r{k}": f"own_seq60_r{WIN - 1 - k}" for k in range(WIN)})
    a, _, _ = _apply(name, X, X.iloc[:4])
    b, _, _ = _apply(name, flip[X.columns], X.iloc[:4])
    cols = [c for c in a.columns if c in set(b.columns) and not c.startswith("own_")]
    assert not np.allclose(a[cols].to_numpy(), b[cols].to_numpy())


def test_窓の列が無ければ止まる():
    """⚠ **黙って素通りさせない**（`seq` 層を入れ忘れた表で回すと結果が別物になる）。"""
    X = pd.DataFrame({"own_ret_1": np.arange(10.0)})
    with pytest.raises(SystemExit):
        tsf.window_order(X.columns)


# --- 3. 決定性 -----------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_同じ入力なら同じ出力(name):
    X = _frame()
    a, at, _ = _apply(name, X, X.iloc[:6])
    b, bt, _ = _apply(name, X, X.iloc[:6])
    pd.testing.assert_frame_equal(a, b)
    pd.testing.assert_frame_equal(at, bt)


# --- 4. 列の入れ替え -----------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_生の窓は落とし_own_は残す(name):
    X = _frame()
    tr, te, doc = _apply(name, X, X.iloc[:6])
    assert not [c for c in tr.columns if c.startswith("own_seq")]
    assert {"own_ret_1", "own_vol_20"} <= set(tr.columns)
    assert list(tr.columns) == list(te.columns)     # ⚠ 訓練と検証で列が揃う
    assert doc["残した元の列"] == 2


def test_F5m3_は_5_列だけ作る():
    """⚠ **生の profile 51 点を全部出さない**（窓をもう 1 度渡したことになる。プラン §2-2）。"""
    X = _frame()
    tr, _, doc = _apply("F5-3 行列プロファイル（モチーフ）", X, X.iloc[:6])
    assert list(rep.MP_COLUMNS) == [c for c in tr.columns if c.startswith("mp_")]
    assert doc["作った列"] == 5 and doc["部分列"] == rep.MP_SUBSEQ


def test_F4m3_は_全部NaN_と_定数の列を落とす():
    """⚠ **窓 60 では出ない計算子がある**（`fft_coefficient` の高次など）。"""
    X = _frame()
    tr, te, doc = _apply(tsf.NAME, X, X.iloc[:6])
    gen = [c for c in tr.columns if c.startswith(tsf.PREFIX)]
    assert doc["生成した列"] > doc["残した生成列"] == len(gen) > 100
    assert doc["落とした列（全部NaN）"] > 0
    assert not tr[gen].isna().to_numpy().any() and not te[gen].isna().to_numpy().any()


# --- 5. ⚠ 落とす列は訓練分割だけで決める ---------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_検証分割の中身は残す列に影響しない(name):
    """⚠ **検証分割を見て列を決めていたら、検証分割を差し替えると列が変わる**（3 章 B 違反）。"""
    X = _frame()
    a, _, _ = _apply(name, X, X.iloc[:6])
    b, _, _ = _apply(name, X, _frame(n=6, seed=99))
    assert list(a.columns) == list(b.columns)
