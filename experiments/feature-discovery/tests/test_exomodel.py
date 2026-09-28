"""「目的の系列 ＋ 外生系列」の深層学習 2 本の配線（[記録](../../../docs/specs/experiments/exog-deep-models.md) §0）。

⚠ **ここで落としたいのは 6 つ。**
  1. TimeXer の移植の形（窓 60 → 6 パッチ ＋ 大域 1・パラメータ数・leak の項は重み 0 で始まる）と、⚠ **同じ種なら 1 ビットも違わない**こと
  2. ⚠ **呼び方の守り**（窓の配列以外では止まる ／ 検知器は組のモデル以外と組むと止まる）
  3. 共変量の組み方: 他銘柄の道筋は「同じ日付の行」から・自分の枠は 0（TimeXer）／ 自分を除く（Chronos）・
     外部系列は ⚠ **t − 1 日までの値だけ**（as-of）で、道筋の最後の点は 0
  4. 検知器の出力の契約（買い% 0〜100・検証の行数ぶん）と、⚠ **fit が訓練だけで行われること**
  5. Chronos-2 の分位点 → 確率 の読み方（単調化・補間・端）
  6. ⚠ **leak 対照で上乗せが跳ねること**（TimeXer ＝ 線形の項 ／ Chronos-2 ＝ 訓練の行での最小二乗）

⚠ **Chronos-2 の重みは要らない**（`_pipeline` を作り物に差し替える）。本物の重みでの確認は `AIL_CHRONOS_REAL=1` のときだけ。
⚠ **暦と外部系列は差し替える**（`calendar`・`external`。本物の日足を読まない）。⚠ **テストは CPU で回す。**
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

from ail import contracts, registry, runs
from ail.detectors import exomodel
from ail.features import labels, seq
from ail.models import chronos2, timexer
from cli.run import evaluate_trading
from tests.test_tsc import _bars
import ail.bootstrap  # noqa: F401

# ⚠ **公式 `TimeXer.Model`（MS・層 2・D 512・F 2048・パッチ 10・外生 77・予測 1）と同じ数**（2026-09-27 に数えた）
N_PARAMS_77 = 8449537
N_EXT = 3


@pytest.fixture(autouse=True)
def _cpu_and_fake_world(monkeypatch):
    monkeypatch.setenv("AIL_TORCH_DEVICE", "cpu")
    days = pd.date_range("2014-09-01", "2018-12-31", freq="D").to_numpy().astype("datetime64[D]")
    rng = np.random.default_rng(7)
    M = np.cumsum(rng.normal(0, 0.05, size=(len(days), N_EXT)), axis=0).astype(np.float32) + 3.0
    real_external = exomodel.external.__wrapped__          # lru_cache の中身（ずらしの検査に使う）
    monkeypatch.setattr(exomodel, "calendar", lambda: days)
    monkeypatch.setattr(exomodel, "external", lambda: ([f"e{i}" for i in range(N_EXT)], M))
    yield days, M, real_external


def _panel(n_days=600, n_sym=4, seed=0, leak=False):
    """`seq` 層 ＋ `y_fwd_10` ＋ `y` を持つ合成パネル（本物の表と同じ列の形）。"""
    rows = []
    for i in range(n_sym):
        b = _bars(n_days, seed + i)
        d = pd.concat([b, seq.build_one(b), labels.build_one(b, 1), labels.build_scales(b, [exomodel.HORIZON], leak=leak)], axis=1)
        d["own_ret_1"] = np.log(b["close"]).diff()           # 基準線「直前リターンの符号」が読む列
        d["symbol"] = f"S{i}"
        rows.append(d)
    out = pd.concat(rows, ignore_index=True).sort_values(["ts", "symbol"])
    return out.dropna().reset_index(drop=True)


def _split(p, q=0.7):
    cut = pd.to_datetime(p["ts"]).quantile(q)
    return p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


def _half_by_date(te) -> int:
    """検証の行を日付の境で半分に切る位置。"""
    days = pd.to_datetime(te["ts"])
    cut = days.iloc[len(te) // 2]
    return int((days < cut).sum())


def _ctx(model, **kw):
    return {"seed": 0, "model": registry.resolve("model", model), "k": 60, "timexer_epochs": 2, **kw}


class _StubPipeline:
    """Chronos-2 の代わり: 文脈の傾き × 10 を中心に、水準ごとに広げた分位点を返す（決定的・共変量は無視）。"""

    quantiles = [0.01, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5,
                 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 0.99]
    calls: list = []

    def predict_quantiles(self, inputs, prediction_length, quantile_levels, batch_size):
        import torch
        self.calls.append((len(inputs), sorted((inputs[0].get("past_covariates") or {}).keys())))
        z = np.array([-2.33, -1.64, -1.28, -1.04, -0.84, -0.67, -0.52, -0.39, -0.25, -0.13, 0.0,
                      0.13, 0.25, 0.39, 0.52, 0.67, 0.84, 1.04, 1.28, 1.64, 2.33])
        out = []
        for d in inputs:
            t = np.asarray(d["target"], dtype=float)
            slope = (t[-1] - t[0]) / (len(t) - 1)
            sd = float(np.std(np.diff(t))) or 1e-3
            steps = np.arange(1, prediction_length + 1)[:, None]
            q = t[-1] + slope * steps + sd * np.sqrt(steps) * z[None, :]
            out.append(torch.as_tensor(q, dtype=torch.float32).unsqueeze(0))
        return out, [o[:, :, 10] for o in out]


@pytest.fixture
def stub(monkeypatch):
    s = _StubPipeline()
    s.calls = []
    monkeypatch.setattr(chronos2, "_pipeline", lambda device: s)
    return s


# --- 1. TimeXer の移植の形と決定性 -------------------------------------------------------------

def test_timexer_shape_and_parameter_count():
    import torch

    assert timexer.patch_num(seq.WINDOW) == 6
    net = timexer.build(seq.WINDOW, 77)
    assert sum(p.numel() for p in net.parameters()) == N_PARAMS_77
    net.eval()
    out = net(torch.randn(5, seq.WINDOW, 78))
    assert out.shape == (5,) and bool(torch.isfinite(out).all())
    assert net.exog is None
    assert float(timexer.build(seq.WINDOW, 3, n_exog=1).exog.weight.detach().abs().sum()) == 0.0
    with pytest.raises(ValueError):
        timexer.build(seq.WINDOW, 0)


def test_lr_type1_halves_every_epoch():
    assert [timexer._lr_type1(e, 1e-4) for e in (1, 2, 3)] == [1e-4, 5e-5, 2.5e-5]


def _timexer_arrays(n=1400, n_exo=3, seed=3):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, 0.01, size=(n, seq.WINDOW * (1 + n_exo)))
    y = rng.normal(0.0, 0.03, size=n)
    return r.astype(np.float32), y


def test_timexer_same_seed_same_prediction_and_fit_record():
    X, y = _timexer_arrays()
    fn = registry.resolve("model", "TimeXer")
    Xtr, ytr, Xte = X[:1100], y[:1100], X[1100:]
    c1 = _ctx("TimeXer", seq_window=seq.WINDOW, n_exo=3)
    c2 = _ctx("TimeXer", seq_window=seq.WINDOW, n_exo=3)
    p1 = np.asarray(fn(Xtr, ytr, Xte, c1))
    p2 = np.asarray(fn(Xtr, ytr, Xte, c2))
    assert p1.shape == (len(Xte),) and np.isfinite(p1).all()
    assert np.array_equal(p1, p2), "TimeXer が同じ種で違う予測を出した"
    doc = c1[timexer.DOC_KEY]
    assert doc["epochs_run"] == 2 and doc["device"] == "cpu" and doc["patch_num"] == 6 and doc["n_exo"] == 3
    assert doc["holdout"] == "rows" and doc["検証の行"] > 0 and 1 <= doc["best_epoch"] <= 2
    assert doc["batch"] == 16 and doc["patience"] == 3 and doc["lradj"] == "type1"


# --- 2. 呼び方の守り -----------------------------------------------------------------------------

def test_models_refuse_calls_without_windows():
    X, y = _timexer_arrays(n=700)
    for name in ("TimeXer", "Chronos-2"):
        fn = registry.resolve("model", name)
        with pytest.raises(SystemExit):
            fn(X[:600], y[:600], X[600:], {"seed": 0})
    with pytest.raises(SystemExit):                       # 形が違う（外生の本数が合わない）
        registry.resolve("model", "TimeXer")(X[:600], y[:600], X[600:], _ctx("TimeXer", seq_window=seq.WINDOW, n_exo=5))


def test_detectors_refuse_other_models():
    p = _panel()
    tr, te = _split(p)
    feats = contracts.feature_columns(p)
    with pytest.raises(SystemExit):
        exomodel.timexer_exo(tr, te, feats, _ctx("Ridge"))
    with pytest.raises(SystemExit):
        exomodel.chronos_cov(tr, te, feats, _ctx("TimeXer"))
    # ⚠ 学習の対象 `y_fwd_10` が無い表では止まる
    with pytest.raises(SystemExit):
        exomodel.timexer_exo(tr.drop(columns=[f"y_fwd_{exomodel.HORIZON}"]), te, feats, _ctx("TimeXer"))


# --- 3. 共変量の組み方 ------------------------------------------------------------------------------

def test_external_windows_are_past_only_and_end_at_zero(_cpu_and_fake_world):
    days, M, _ = _cpu_and_fake_world
    ts = pd.to_datetime(pd.Series([days[200], days[300]]).astype("datetime64[ns]")).dt.tz_localize("UTC")
    names, w = exomodel.external_windows(ts)
    assert names == ["e0", "e1", "e2"] and w.shape == (2, N_EXT, seq.WINDOW)
    assert np.all(w[:, :, -1] == 0.0)
    _, lv = exomodel.external_windows(ts, levels=True)
    # `external()` が返す表は暦の日ごとに「その日に使える値」（ずらし済み）。窓はその 60 日を古い → 新しいで並べる
    assert np.allclose(lv[0, :, -1], M[200]) and np.allclose(lv[0, :, 0], M[200 - (seq.WINDOW - 1)])
    assert np.allclose(w[1, :, 0], M[300 - (seq.WINDOW - 1)] - M[300])
    with pytest.raises(SystemExit):                        # 暦の頭より前の窓
        exomodel.external_windows(pd.to_datetime(pd.Series([days[10]]).astype("datetime64[ns]")).dt.tz_localize("UTC"))


def test_external_table_is_lagged_one_day_and_backfilled_at_the_head(_cpu_and_fake_world, monkeypatch):
    """⚠ **暦の日 k に貼るのは k − 1 日までに出ている最新の値**（`ex` 層と同じ as-of）。系列の始まりより前は最初の値。"""
    days, _, real_external = _cpu_and_fake_world
    idx = pd.DatetimeIndex(days.astype("datetime64[ns]"))
    every = pd.Series(np.arange(len(idx), dtype=float), index=idx, name="a")
    sparse = every.iloc[::2].copy()                        # 1 日おきにしか出ない
    sparse.name = "b"
    monkeypatch.setattr(exomodel.exog, "load_series", lambda sources, zero_fill: ({"b": sparse, "a": every}, {"a": "x", "b": "x"}))
    names, M = real_external()
    assert names == ["a", "b"] and M.shape == (len(days), 2)
    assert M[0, 0] == 0.0 and np.allclose(M[1:, 0], np.arange(len(days) - 1))         # k → k − 1（頭は最初の値）
    assert M[5, 1] == 4.0 and M[6, 1] == 4.0                                            # 5 日目は 4 日目の値・6 日目もまだ 4（as-of）


def test_symbol_panel_and_inputs():
    p = _panel(n_days=200, n_sym=3)
    feats = contracts.feature_columns(p)
    slots = exomodel.symbol_slots(p)
    assert slots == ["S0", "S1", "S2"]
    X, names = exomodel.build_inputs(p, feats, [], slots, "timexer")
    n_cov = len(slots) + N_EXT
    assert X.shape == (len(p), seq.WINDOW * (1 + n_cov)) and names[:3] == ["sym:S0", "sym:S1", "sym:S2"]
    own = exomodel.tsc.window_paths(p, feats)[:, 0, :]
    assert np.allclose(X[:, :seq.WINDOW], own)
    cov = X[:, seq.WINDOW:].reshape(len(p), n_cov, seq.WINDOW)
    slot = p["symbol"].map({s: i for i, s in enumerate(slots)}).to_numpy()
    assert np.all(cov[np.arange(len(p)), slot] == 0.0), "自分の枠は 0"
    # 同じ日の別の銘柄の道筋が、その銘柄の枠に入っている
    i = 10
    j = p.index[(p["ts"] == p.loc[i, "ts"]) & (p["symbol"] != p.loc[i, "symbol"])][0]
    assert np.allclose(cov[i, slot[j]], own[j])
    Xc, nc = exomodel.build_inputs(p, feats, [], slots, "chronos")
    assert Xc.shape == (len(p), seq.WINDOW * (1 + (len(slots) - 1) + N_EXT)) and len(nc) == len(slots) - 1 + N_EXT
    assert np.allclose(Xc[:, :seq.WINDOW], np.exp(own)) and np.allclose(Xc[:, seq.WINDOW - 1], 1.0)
    covc = Xc[:, seq.WINDOW:seq.WINDOW * len(slots)].reshape(len(p), len(slots) - 1, seq.WINDOW)
    # 自分を除いた順: S1 の行なら 0 番目の共変量は S0 の相対価格
    i1 = p.index[p["symbol"] == "S1"][5]
    j0 = p.index[(p["ts"] == p.loc[i1, "ts"]) & (p["symbol"] == "S0")][0]
    assert np.allclose(covc[i1, 0], np.exp(own[j0]))
    Xn, nn = exomodel.build_inputs(p, feats, [], slots, "chronos_nocov")
    assert Xn.shape == (len(p), seq.WINDOW) and nn == []


# --- 4. 検知器の契約と fit の範囲 ----------------------------------------------------------------------

def test_timexer_detector_contract_and_training_only_fit():
    p = _panel()
    tr, te = _split(p)
    feats = contracts.feature_columns(p)
    fn = registry.resolve("detector", exomodel.NAME_S4)
    buy, doc = fn(tr, te, feats, _ctx("TimeXer"))
    assert len(buy) == len(te) and float(np.min(buy)) >= 0.0 and float(np.max(buy)) <= 100.0
    assert doc["target"] == f"y_fwd_{exomodel.HORIZON}" and doc["columns"] == [seq.column(k) for k in range(seq.WINDOW)]
    assert doc["n_cov"] == 4 + N_EXT and doc["学習（本番）"]["holdout"] == "date" and doc["学習（較正用）"] is not None
    assert doc["source"] == "holdout" and doc["学習（本番）"]["exog_weight"] is None
    half = _half_by_date(te)                                 # ⚠ 日付の境で切る（日の途中で切ると同じ日の他銘柄の道筋が欠ける）
    part, _ = fn(tr, te.iloc[:half].reset_index(drop=True), feats, _ctx("TimeXer"))
    assert np.allclose(buy[:half], part, rtol=0, atol=1e-4)


def test_chronos_detectors_contract_with_stub(stub):
    p = _panel()
    tr, te = _split(p)
    feats = contracts.feature_columns(p)
    buy, doc = registry.resolve("detector", exomodel.NAME_S2)(tr, te, feats, _ctx("Chronos-2"))
    assert len(buy) == len(te) and float(np.min(buy)) >= 0.0 and float(np.max(buy)) <= 100.0
    assert doc["n_cov"] == 3 + N_EXT and doc["推論（本番）"]["予測した行"] == len(te)
    assert doc["推論（本番）"]["step"] == 10 and doc["推論（本番）"]["leak_weight"] is None
    # ⚠ 共変量は他銘柄 3 ＋ 外部 3 が渡っている ／ 較正の推論は訓練の尻だけ（全部の訓練行を推論していない）
    assert stub.calls[-1][1] == sorted([f"sym:{i}" for i in range(3)] + [f"ext:e{i}" for i in range(N_EXT)])
    assert stub.calls[0][0] < len(tr)
    buy3, doc3 = registry.resolve("detector", exomodel.NAME_S3)(tr, te, feats, _ctx("Chronos-2"))
    assert len(buy3) == len(te) and doc3["n_cov"] == 0 and stub.calls[-1][1] == []
    half = _half_by_date(te)
    part, _ = registry.resolve("detector", exomodel.NAME_S3)(tr, te.iloc[:half].reset_index(drop=True), feats, _ctx("Chronos-2"))
    assert np.allclose(buy3[:half], part, rtol=0, atol=1e-6)


# --- 5. 分位点 → 確率 ----------------------------------------------------------------------------

def test_chronos_prediction_follows_the_context_not_the_level(stub):
    """⚠ **相対価格（最後 1.0）で渡しても、読むのは「いまの値からの上下」**。上がり調子の行は下がり調子の行より買い寄り。

    ⚠ 2026-09-27 の最初の実行はこれを忘れ、P(上) が全行 0.99（＝ 1.0 > 0）になった。
    """
    up = np.exp(np.linspace(-0.3, 0.0, seq.WINDOW)).astype(np.float32)      # 上がって最後 1.0
    down = np.exp(np.linspace(0.3, 0.0, seq.WINDOW)).astype(np.float32)     # 下がって最後 1.0
    X = np.stack([up, down])
    c = _ctx("Chronos-2", seq_window=seq.WINDOW, n_cov=0)
    pred = registry.resolve("model", "Chronos-2")(X, np.zeros(2), X, c)
    assert pred[0] > 0 > pred[1], pred
    assert 0.05 < c[chronos2.DOC_KEY]["p_up_mean"] < 0.95 and c[chronos2.DOC_KEY]["p_up_sd"] > 0.1


def test_prob_up_reads_the_cdf_at_zero():
    lv = _StubPipeline.quantiles
    q = np.array([np.linspace(-1, 1, 21),          # 中央値 0 → P ＝ 0.5
                  np.linspace(0.1, 2, 21),         # 全部 0 より上 → 0.99（端）
                  np.linspace(-2, -0.1, 21),       # 全部 0 より下 → 0.01（端）
                  np.linspace(-1, 3, 21)])         # 0 は 0.25 の水準（−1 ＋ 4 × 0.25 ＝ 0）→ P ＝ 0.75
    p = chronos2.prob_up(q, lv)
    assert np.allclose(p, [0.5, 0.99, 0.01, 0.75], atol=1e-6)
    # ⚠ 単調でない分位点は単調に直してから読む（例外にしない）
    q2 = np.array([[0.5, -0.5] + [1.0] * 19])
    assert 0.01 <= float(chronos2.prob_up(q2, lv)[0]) <= 0.99


# --- 6. leak 対照 ------------------------------------------------------------------------------------

@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_exomodel", {}, seed=0)


def _exp(detector, model, **model_args):
    return {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"},
            "validation": {"split": "walk_forward_dates", "folds": 3, "seed": 0},
            "k": 60, "cost_bp": 5.0, "horizon_min": 21600.0, "bar_minutes": 1440.0,
            "detectors": [detector], "model": model, "model_args": model_args,
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


def _edge(res, name):
    m = res[(res["手法"] == name) & (res["閾値"] == 50.0)].set_index("fold")["純利bp"]
    bh = res[(res["手法"] == "基準 常に上（ドリフト）") & (res["閾値"] == 50.0)].set_index("fold")["純利bp"]
    return m - bh


def test_leak_column_reaches_the_timexer_output():
    """⚠ **`LEAK_` 列（＝ 答え）を 1 本足すと、予測が答えとほぼ一致する**（線形の項の配線。本番の表には無い列）。"""
    X, y = _timexer_arrays()
    XL = np.hstack([X, y[:, None].astype(np.float32)])
    fn = registry.resolve("model", "TimeXer")
    c = _ctx("TimeXer", seq_window=seq.WINDOW, n_exo=3, n_exog=1, timexer_epochs=10, timexer_lr=1e-2)
    p = np.asarray(fn(XL[:1100], y[:1100], XL[1100:], c))
    assert np.corrcoef(p, y[1100:])[0, 1] > 0.9
    assert c[timexer.DOC_KEY]["exog_weight"][0] > 0.3


def test_leak_makes_timexer_edge_jump(run):
    """⚠ **leak 対照で上乗せが跳ねる**（rules.md 13-10 の配線の検査を検知器 → シミュレータまで通す）。

    ⚠ **合成データは小さいので、網を小さくし epoch と学習率を上げている**（`timexer_d_model` ほかテスト用の口。
    本番は D 512・F 2048・10 epoch・1e-4）。8.4M パラメータのままだと小さい表では雑音が大きく、跳ねを測れない。
    """
    p = _panel(n_days=700, n_sym=4, leak=True)
    res, *_ = evaluate_trading(p, contracts.feature_columns(p),
                               _exp(exomodel.NAME_S4, "TimeXer", timexer_epochs=30, timexer_lr=1e-2,
                                    timexer_d_model=16, timexer_d_ff=64), run)
    edge = _edge(res, exomodel.NAME_S4)
    assert (edge > 1000.0).all(), edge.to_dict()


def test_leak_makes_chronos_edge_jump_and_clean_does_not(run, stub):
    p = _panel(n_days=700, n_sym=4, leak=True)
    res, *_ = evaluate_trading(p, contracts.feature_columns(p), _exp(exomodel.NAME_S2, "Chronos-2"), run)
    edge = _edge(res, exomodel.NAME_S2)
    assert (edge > 1000.0).all(), edge.to_dict()
    p0 = _panel(n_days=700, n_sym=4, leak=False)
    res0, *_ = evaluate_trading(p0, contracts.feature_columns(p0), _exp(exomodel.NAME_S3, "Chronos-2"), run)
    assert not (_edge(res0, exomodel.NAME_S3) > 1000.0).all()


# --- 7. 本物の重み（任意） --------------------------------------------------------------------------------

@pytest.mark.skipif(os.environ.get("AIL_CHRONOS_REAL") != "1", reason="本物の重みは AIL_CHRONOS_REAL=1 のときだけ")
def test_real_chronos_runs_on_a_small_panel():
    p = _panel(n_days=300, n_sym=3)
    tr, te = _split(p)
    feats = contracts.feature_columns(p)
    buy, doc = registry.resolve("detector", exomodel.NAME_S2)(tr, te, feats, _ctx("Chronos-2"))
    assert len(buy) == len(te) and doc["推論（本番）"]["revision"]
    assert 0.0 < doc["推論（本番）"]["p_up_mean"] < 1.0
