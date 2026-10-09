"""ハイパーパラメータを訓練分割の内側で選ぶ段（`ail/models/tuned.py`。rules.md 14-12 規約 7）。

⚠ **規約は破れても気づけないが、この検査は破れば落ちる**:
(a) 評価分割を差し替えても champion が変わらない ／ (b) 候補が既定だけなら基底のモデルと予測が同一 ／
(c) 新しいモデル名の無い config は指紋が動かない（`tests/test_trading_run.py` の既存の指紋がそのまま効く）。
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import catalog, registry, runs
from ail.models import deep, linear, trees, tuned
from ail.models.holdout import MIN_TRAIN

N, P = 2000, 5


def _data(seed: int = 0, n: int = N):
    rng = np.random.default_rng(seed)
    X = pd.DataFrame(rng.normal(size=(n, P)), columns=[f"c{i}" for i in range(P)])
    y = (X["c0"] * 0.5 - X["c1"] * 0.3 + rng.normal(scale=1.0, size=n)).to_numpy()
    return X, y


# --- 候補の一覧は事前固定どおり（rules.md 14-12-1 の写し） --------------------------------------

def test_candidates_match_the_prefixed_levels():
    assert [c["alpha"] for c in tuned.RIDGE] == [1.0, 10.0, 100.0, 1e3, 1e4, 1e5, 1e6]
    assert len(tuned.LGBM) == 27 and tuned.LGBM[0] == {"lgbm_leaves": 31, "lgbm_lr": 0.05, "lgbm_min_child": 100}
    assert {c["lgbm_leaves"] for c in tuned.LGBM} == {7, 31, 127}
    assert {c["lgbm_lr"] for c in tuned.LGBM} == {0.02, 0.05, 0.1}
    assert {c["lgbm_min_child"] for c in tuned.LGBM} == {20, 100, 500}
    assert len(tuned.MLP) == 45 and tuned.MLP[0] == {"mlp_hidden": (64, 32), "mlp_dropout": 0.2, "mlp_lr": 1e-3}
    assert {c["mlp_hidden"] for c in tuned.MLP} == {(64,), (32, 16), (64, 32), (128, 64), (128, 64, 32)}
    assert {c["mlp_dropout"] for c in tuned.MLP} == {0.0, 0.2, 0.5}
    assert {c["mlp_lr"] for c in tuned.MLP} == {3e-4, 1e-3, 3e-3}
    for name, cands in tuned.CANDIDATES.items():
        assert len({tuple(sorted(c.items())) for c in cands}) == len(cands), f"{name}: 候補が重複"
    assert sorted(m for m in registry.available("model") if "内側選抜" in m) == \
        ["LightGBM（内側選抜）", "MLP（内側選抜）", "Ridge（内側選抜）"]


# --- (b) 既定だけの候補 ＝ 基底のモデルと同一 ------------------------------------------------------

def test_default_only_ridge_equals_the_base_model():
    X, y = _data()
    Xte = X.iloc[:100] * 2.0
    ctx = {"seed": 0}
    want = linear.ridge(X, y, Xte, ctx)
    got = tuned.inner_select(linear.ridge, X, y, Xte, dict(ctx), [tuned.RIDGE_DEFAULT])
    assert np.array_equal(np.asarray(want), np.asarray(got))


def test_default_only_lightgbm_equals_the_base_model():
    pytest.importorskip("lightgbm")
    X, y = _data()
    Xte = X.iloc[:100] * 2.0
    ctx = {"seed": 0, "lgbm_estimators": 60}
    want = trees.lightgbm_gbdt(X, y, Xte, ctx)
    got = tuned.inner_select(trees.lightgbm_gbdt, X, y, Xte, dict(ctx), [tuned.LGBM_DEFAULT])
    assert np.array_equal(np.asarray(want), np.asarray(got))


def test_default_only_mlp_equals_the_base_model(monkeypatch):
    pytest.importorskip("torch")
    monkeypatch.setenv("AIL_TORCH_DEVICE", "cpu")
    X, y = _data()
    Xte = X.iloc[:100] * 2.0
    ctx = {"seed": 0, "mlp_epochs": 15}
    want = deep.mlp(X, y, Xte, ctx)
    got = tuned.inner_select(deep.mlp, X, y, Xte, dict(ctx), [tuned.MLP_DEFAULT])
    assert np.array_equal(np.asarray(want), np.asarray(got))


def test_base_models_read_the_levels_from_ctx(monkeypatch):
    """ctx に項目があれば基底のモデルの形が変わる（無ければ既定 ＝ 指紋テストが縛る）。"""
    X, y = _data()
    Xte = X.iloc[:100]
    a = linear.ridge(X, y, Xte, {"seed": 0})
    b = linear.ridge(X, y, Xte, {"seed": 0, "alpha": 1e6})
    assert not np.array_equal(a, b)
    lgb = pytest.importorskip("lightgbm")  # noqa: F841
    a = trees.lightgbm_gbdt(X, y, Xte, {"seed": 0, "lgbm_estimators": 30})
    b = trees.lightgbm_gbdt(X, y, Xte, {"seed": 0, "lgbm_estimators": 30, "lgbm_leaves": 7, "lgbm_min_child": 500})
    assert not np.array_equal(a, b)
    pytest.importorskip("torch")
    monkeypatch.setenv("AIL_TORCH_DEVICE", "cpu")
    a = deep.mlp(X, y, Xte, {"seed": 0, "mlp_epochs": 5})
    b = deep.mlp(X, y, Xte, {"seed": 0, "mlp_epochs": 5, "mlp_hidden": (128, 64, 32), "mlp_dropout": 0.0})
    assert not np.array_equal(a, b)


# --- (a) 評価分割を差し替えても champion が変わらない ------------------------------------------------

def test_replacing_the_evaluation_split_does_not_change_the_champion():
    """⚠ **これが「訓練の内側だけで選んだ」ことの機械的な証明である**（14-11 規約 3 と同型）。"""
    X, y = _data()
    te1 = X.iloc[:100].copy()
    te2 = pd.DataFrame(np.random.default_rng(9).normal(size=(100, P)) * 7.5 + 3.0, columns=X.columns)
    c1, c2 = {"seed": 0}, {"seed": 0}
    tuned.inner_select(linear.ridge, X, y, te1, c1, tuned.RIDGE)
    tuned.inner_select(linear.ridge, X, y, te2, c2, tuned.RIDGE)
    d1, d2 = tuned.drain(c1)[0], tuned.drain(c2)[0]
    assert d1["champion"] == d2["champion"] and d1["champion_index"] == d2["champion_index"]
    assert d1["ρ"] == d2["ρ"] and d1["既定のρ"] == d2["既定のρ"]
    assert d1["尻の行数"] > 0 and not d1["切れなかった"]
    assert all(r is not None for r in d1["ρ"])
    assert d1["ρ"][d1["champion_index"]] == max(d1["ρ"])


def test_ties_pick_the_first_candidate_which_is_the_default():
    """全候補の予測が定数（y が定数）＝ ρ は全部最低点 → 先頭（既定）。"""
    X, _ = _data()
    y = np.full(N, 0.01)
    ctx = {"seed": 0}
    tuned.inner_select(linear.ridge, X, y, X.iloc[:10], ctx, tuned.RIDGE)
    d = tuned.drain(ctx)[0]
    assert d["champion_index"] == 0 and d["champion"] == tuned.RIDGE_DEFAULT
    assert d["ρ"] == [None] * len(tuned.RIDGE) and d["既定のρ"] is None


def test_spearman_is_signed_and_none_for_degenerate_predictions():
    y = np.arange(50, dtype=float)
    assert tuned.spearman(y, y) == pytest.approx(1.0)
    assert tuned.spearman(-y, y) == pytest.approx(-1.0)
    assert tuned.spearman(np.zeros(50), y) is None
    assert tuned.spearman(np.r_[np.nan, y[1:]], y) is None


# --- 尻が切れないときは既定 ---------------------------------------------------------------------

def test_small_sample_falls_back_to_the_default_and_records_it():
    X, y = _data(n=MIN_TRAIN - 50)
    ctx = {"seed": 0}
    got = tuned.inner_select(linear.ridge, X, y, X.iloc[:10], ctx, tuned.RIDGE)
    want = linear.ridge(X, y, X.iloc[:10], {"seed": 0})
    assert np.array_equal(np.asarray(want), np.asarray(got))
    d = tuned.drain(ctx)[0]
    assert d["切れなかった"] is True and d["champion_index"] == 0 and d["尻の行数"] == 0


# --- 記録の形（fitted_doc）と取り残し ------------------------------------------------------------

def _panel(n_days=800, n_sym=3, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        y = rng.normal(0.0002, 0.01, n_days)
        rows.append(pd.DataFrame({"symbol": f"S{i}", "ts": ts, "y": y,
                                  "own_ret_1": np.r_[0.0, y[:-1]], "noise": rng.normal(0, 1, n_days)}))
    return pd.concat(rows, ignore_index=True)


def _exp(model: str, form: str = "shared"):
    return {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": form},
            "validation": {"split": "walk_forward_dates", "folds": 5, "seed": 0},
            "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "selectors": ["全部使う（基準）"], "model": model,
            "baselines": ["常に上（ドリフト）", "直前リターンの符号"]}


@pytest.mark.parametrize("form", ["shared", "per_symbol"])
def test_fitted_doc_records_the_inner_selection_only_for_the_new_model(tmp_path, monkeypatch, form):
    from cli.run import evaluate_trading

    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    panel = _panel()
    feats = ["own_ret_1", "noise"]

    run = runs.Run("test_tuned_plain", {}, seed=0)
    evaluate_trading(panel, feats, _exp("Ridge", form), run)
    f = sorted(x for x in runs.files(run.name, "fitted/") if x.startswith("fitted/calibration_f"))[0]
    plain = runs.read_json(run.name, f)["全部使う（基準）"]
    entries = list(plain.values()) if form == "per_symbol" else [plain]
    assert all(set(e) == {"a", "b", "source"} for e in entries)     # ⚠ 既存の記録に鍵が増えない

    run = runs.Run("test_tuned_sel", {}, seed=0)
    evaluate_trading(panel, feats, _exp("Ridge（内側選抜）", form), run)
    f = sorted(x for x in runs.files(run.name, "fitted/") if x.startswith("fitted/calibration_f"))[-1]
    doc = runs.read_json(run.name, f)["全部使う（基準）"]
    entries = list(doc.values()) if form == "per_symbol" else [doc]
    for e in entries:
        assert set(e) == {"a", "b", "source", "内側選抜"}
        sel = e["内側選抜"]
        assert set(sel) == {"較正", "本番"}                           # 較正 → 本番の順に 2 回呼ばれる
        for d in sel.values():
            assert d["候補"] == [{"alpha": a} for a in [1.0, 10.0, 100.0, 1e3, 1e4, 1e5, 1e6]]
            assert d["既定"] == {"alpha": 1.0} and len(d["ρ"]) == 7
            assert d["champion"] in d["候補"] and d["候補"][d["champion_index"]] == d["champion"]
            assert "既定のρ" in d and "尻の行数" in d and "切れなかった" in d


def test_ctx_has_no_leftover_after_fold_buy_pct():
    from cli.run import fold_buy_pct

    panel = _panel()
    feats = ["own_ret_1", "noise"]
    model = registry.resolve("model", "Ridge（内側選抜）")
    ctx = {"seed": 0, "model": model, "k": 2}
    tr = panel[panel["ts"] < "2020-06-01"].reset_index(drop=True)
    te = panel[panel["ts"] >= "2020-06-01"].reset_index(drop=True)
    sel = registry.resolve_all("selector", ["全部使う（基準）"])
    _buy, _ex, _n, doc = fold_buy_pct(tr, te, feats, _exp("Ridge（内側選抜）"), ctx, model=model, selectors=sel,
                                      detectors={}, baselines={}, form="shared", k=2,
                                      groups=te.groupby("symbol").indices, f=0, picked=[], log=lambda *_: None)
    assert tuned.DOC_KEY not in ctx
    assert "内側選抜" in doc["全部使う（基準）"]


def test_gate_discards_the_inner_selection_docs(tmp_path, monkeypatch):
    from ail.validation import gate

    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    panel = _panel()
    feats = ["own_ret_1", "noise"]
    run = runs.Run("test_tuned_gate", {}, seed=0)
    exp = _exp("Ridge（内側選抜）")
    out = gate.evaluate_gate(panel, feats, exp, run)
    assert out is not None


# --- 検証結果一覧の数え方（champion × θ ＝ 1 モデル 3 検証） ----------------------------------------

def test_threshold_rows_of_the_new_model_count_as_trials():
    """「全部使う × Ridge（内側選抜）」は閾値売買の行として θ ごとに 1 検証（rules.md 13-9・14-12 規約 6）。"""
    row = {"手法名": "全部使う（基準）", "鍵": "全部使う（基準）", "モデル": "Ridge（内側選抜）", "検証方式": "閾値売買"}
    assert catalog.is_trial(row)
    assert catalog.is_trial({**row, "モデル": "Ridge"})
    assert not catalog.is_trial({**row, "手法名": "乱択（基準）", "鍵": "乱択（基準）"})
    assert not catalog.is_trial({**row, "手法名": "基準 持ち続ける"})


def test_names_toml_spells_the_new_models():
    from ail import names

    assert names.learner_slug("Ridge（内側選抜）") == "ridge-tuned"
    assert names.learner_slug("LightGBM（内側選抜）") == "lgbm-tuned"
    assert names.learner_slug("MLP（内側選抜）") == "mlp-tuned"


def test_configs_exist_and_resolve():
    import tomllib

    here = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
    for m, model in (("ridge", "Ridge"), ("lgbm", "LightGBM"), ("mlp", "MLP")):
        with open(os.path.join(here, "experiment", f"trade_own_{m}_tuned_a.toml"), "rb") as f:
            cfg = tomllib.load(f)
        assert cfg["model"] == f"{model}（内側選抜）" and cfg["selectors"] == ["全部使う（基準）"]
        assert cfg["features_from"] == "own_2018" and cfg["trading"]["thresholds"] == [50, 55, 60]
        registry.resolve("model", cfg["model"])
    with open(os.path.join(here, "queue", "hp_inner.toml"), "rb") as f:
        q = tomllib.load(f)
    assert q["experiments"] == ["trade_own_ridge_tuned_a", "trade_own_lgbm_tuned_a", "trade_own_mlp_tuned_a"]
    assert q["leak"] is True
