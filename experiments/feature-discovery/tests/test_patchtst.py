"""PatchTST（系列モデル 1 本）の配線（[プラン](../../../docs/plans/patchtst-threshold.md) §4）。

⚠ **ここで落としたいのは 5 つ。**
  1. 移植の形（長さ 60 → 7 パッチ・パラメータ数・末尾の埋め方が `ReplicationPad1d` と同じ値）
  2. ⚠ **同じ種・同じ入力なら 1 ビットも違わない**（CPU。再現の前提）
  3. ⚠ **呼び方の守り**（窓の配列以外では止まる ／ 検知器は `PatchTST` 以外のモデルと組むと止まる）
  4. 検知器の出力の契約と、⚠ **fit が訓練だけで行われること**
  5. ⚠ **`LEAK_` 列の線形の項が効いて、leak 対照で上乗せが跳ねること**

⚠ **公式実装と同じ重みで出力が一致するかは、公式のコードを使う 1 回きりの検査**で確かめた（記録 §5。ここには公式のコードを置かない）。
⚠ **テストは CPU で回す**（`test_models.py` と同じ）。⚠ **epoch は少なくする**（`patchtst_epochs`。本番は 100 のまま）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import contracts, registry, runs
from ail.detectors import seqmodel
from ail.features import seq
from ail.models import patchtst
from cli.run import evaluate_trading
from tests.test_tsc import _panel
import ail.bootstrap  # noqa: F401

# ⚠ **公式の `PatchTST_backbone`（縮小側・1 変数・予測の長さ 1）と同じ数**（2026-09-15 に照合。記録 §5）
N_PARAMS = 16676


@pytest.fixture(autouse=True)
def _cpu(monkeypatch):
    monkeypatch.setenv("AIL_TORCH_DEVICE", "cpu")


def _ctx(**kw):
    return {"seed": 0, "model": registry.resolve("model", "PatchTST"), "k": 60,
            "patchtst_epochs": 3, **kw}


def _windows(n=1400, seed=3):
    """道筋の窓（古い → 新しい・最後の点 0）と次のリターン。"""
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, 0.01, size=(n, seq.WINDOW + 1))
    path = np.cumsum(r[:, :seq.WINDOW], axis=1)
    path -= path[:, -1:]
    return path.astype(np.float32), r[:, seq.WINDOW]


# --- 1. 移植の形 ------------------------------------------------------------

def test_shape_patches_and_parameter_count():
    import torch

    assert patchtst.patch_num(seq.WINDOW) == 7
    net = patchtst.build(seq.WINDOW)
    assert sum(p.numel() for p in net.parameters()) == N_PARAMS
    net.eval()
    out = net(torch.randn(5, seq.WINDOW))
    assert out.shape == (5,) and bool(torch.isfinite(out).all())
    # ⚠ **leak の項は `LEAK_` 列があるときだけ作り、重み 0 で始める**（本番は公式の構造そのまま）
    assert net.exog is None
    assert float(patchtst.build(seq.WINDOW, n_exog=1).exog.weight.detach().abs().sum()) == 0.0


def test_end_padding_equals_replication_pad():
    import torch

    z = torch.randn(4, 1, seq.WINDOW)
    net = patchtst.build(seq.WINDOW)
    assert torch.equal(net.pad_end(z), torch.nn.ReplicationPad1d((0, patchtst.ARCH["stride"]))(z))


def test_lr_type3_matches_the_official_schedule():
    lr0 = 1e-4
    assert [patchtst._lr_type3(e, lr0) for e in (1, 2, 3)] == [lr0, lr0, lr0]
    assert np.isclose(patchtst._lr_type3(4, lr0), lr0 * 0.9)
    assert np.isclose(patchtst._lr_type3(13, lr0), lr0 * 0.9 ** 10)


# --- 2. 決定性 ---------------------------------------------------------------

def test_same_seed_same_prediction_and_fit_record():
    X, y = _windows()
    Xtr, ytr, Xte = X[:1100], y[:1100], X[1100:]
    fn = registry.resolve("model", "PatchTST")
    c1, c2 = _ctx(seq_window=seq.WINDOW), _ctx(seq_window=seq.WINDOW)
    p1 = np.asarray(fn(Xtr, ytr, Xte, c1))
    p2 = np.asarray(fn(Xtr, ytr, Xte, c2))
    assert p1.shape == (len(Xte),) and np.isfinite(p1).all()
    assert np.array_equal(p1, p2), "PatchTST が同じ種で違う予測を出した"
    doc = c1[patchtst.DOC_KEY]
    assert doc["params"] == N_PARAMS and doc["epochs_run"] == 3 and doc["device"] == "cpu"
    # ⚠ 1,100 行なら訓練の尻 10% が検証に切れる（tail_holdout）。記録に最良 epoch と「0 と予測した」MSE が残る
    assert doc["検証の行"] > 0 and 1 <= doc["best_epoch"] <= 3 and doc["val_mse_zero"] > 0
    assert len(doc["val_mse_by_epoch"]) == 3


# --- 3. 呼び方の守り ------------------------------------------------------------

def test_model_refuses_calls_without_windows():
    X, y = _windows(n=700)
    fn = registry.resolve("model", "PatchTST")
    # ⚠ 選別 × モデルの経路（ctx に窓の長さが無い）
    with pytest.raises(SystemExit):
        fn(X[:600], y[:600], X[600:], {"seed": 0})
    # ⚠ 標準化した 35 列を窓と偽って渡しても止まる
    t = pd.DataFrame(np.zeros((700, 35)), columns=[f"own_f{i}" for i in range(35)])
    with pytest.raises(SystemExit):
        fn(t.iloc[:600], y[:600], t.iloc[600:], _ctx(seq_window=seq.WINDOW))


@pytest.fixture(scope="module")
def split():
    p = _panel()
    cut = pd.to_datetime(p["ts"]).quantile(0.7)
    return p, p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


def test_detector_refuses_other_models(split):
    p, tr, te = split
    with pytest.raises(SystemExit):
        seqmodel.patchtst_detector(tr, te, contracts.feature_columns(p),
                                   {"seed": 0, "model": registry.resolve("model", "Ridge")})


# --- 4. 検知器の契約 -------------------------------------------------------------

def test_detector_returns_one_buy_pct_per_test_row(split):
    p, tr, te = split
    fn = registry.resolve("detector", seqmodel.NAME)
    buy, doc = fn(tr, te, contracts.feature_columns(p), _ctx())
    assert len(buy) == len(te)
    assert float(np.min(buy)) >= 0.0 and float(np.max(buy)) <= 100.0
    # ⚠ **使う列は窓だけ**（own 列を混ぜると時系列分類器と入力が揃わない）
    assert doc["columns"] == [seq.column(k) for k in range(seq.WINDOW)]
    assert doc["学習（本番）"]["params"] == N_PARAMS and doc["学習（較正用）"] is not None
    assert doc["学習（本番）"]["exog_weight"] is None


def test_fit_uses_only_the_training_rows(split):
    """⚠ **検証の行を半分に減らしても、残った行の買い% は変わらない**（`test_tsc` と同じ許容差 1e-4 点）。"""
    p, tr, te = split
    fn = registry.resolve("detector", seqmodel.NAME)
    feats = contracts.feature_columns(p)
    full, _ = fn(tr, te, feats, _ctx())
    half = len(te) // 2
    part, _ = fn(tr, te.iloc[:half].reset_index(drop=True), feats, _ctx())
    assert np.allclose(full[:half], part, rtol=0, atol=1e-4)


# --- 5. leak 対照 ------------------------------------------------------------------

@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_patchtst", {}, seed=0)


def test_leak_column_is_passed_and_makes_the_edge_jump(run):
    """⚠ **`LEAK_` の線形の項が効いて上乗せが跳ねる**（rules.md 13-10）。

    ⚠ **合成データは小さいので学習率を上げている**（`patchtst_lr`。テスト用の口。本番は 1e-4・100 epoch）。
    """
    p = _panel(n_days=600, n_sym=4, leak=True)
    feats = contracts.feature_columns(p)
    exp = {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"},
           "validation": {"split": "walk_forward_dates", "folds": 3, "seed": 0},
           "k": 60, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
           "detectors": [seqmodel.NAME], "model": "PatchTST",
           "model_args": {"patchtst_epochs": 30, "patchtst_lr": 1e-2},
           "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}
    res, *_ = evaluate_trading(p, feats, exp, run)
    m = res[(res["手法"] == seqmodel.NAME) & (res["閾値"] == 50.0)].set_index("fold")["純利bp"]
    bh = res[(res["手法"] == "基準 常に上（ドリフト）") & (res["閾値"] == 50.0)].set_index("fold")["純利bp"]
    edge = m - bh
    assert (edge > 1000.0).all(), edge.to_dict()
