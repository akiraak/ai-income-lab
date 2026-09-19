"""比較対象・補助指標・採用基準の判定・入口（`ail/scenario/{baselines,metrics,evaluate}.py`・`cli/scenario.py`）。

⚠ **足は合成・学習は数 epoch・CPU**（配線の検査だけ。プラン §8 のテスト 4・7）。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from ail import runs
from ail.scenario import baselines, data, evaluate, metrics

torch = pytest.importorskip("torch")


def test_known_short_series_cumulative_quantiles_and_drop_event():
    """プラン §8 のテスト 4。"""
    r = np.log(np.array([[1.00, 0.94, 1.02, 1.00, 1.05],      # 2 日目の終値で −6% → 下落事象
                         [1.01, 1.01, 1.01, 1.01, 1.01],
                         [0.96, 0.99, 1.00, 1.00, 1.10]]))     # 2 日目で 0.9504 ＝ −4.96% → ⚠ 事象ではない
    cum = metrics.cumulative_returns(r)
    assert np.allclose(cum[0], [0.0, -0.06, -0.0412, -0.0412, 0.00674], atol=1e-5)
    assert metrics.drop_event(r).tolist() == [True, False, False]
    out = metrics.summarize_paths(r)
    assert out["probability_up_5d"] == pytest.approx(3 / 3)
    assert out["probability_close_below_minus_5pct"] == pytest.approx(1 / 3)
    assert out["return_quantiles_by_day"]["50%"][0] == pytest.approx(0.0)      # 1 日目の中央値
    assert len(out["return_quantiles_by_day"]) == 7 and len(out["return_quantiles_by_day"]["2.5%"]) == 5


def test_coverage_brier_reliability():
    rng = np.random.default_rng(0)
    s, y = rng.normal(size=(2000, 400)), rng.normal(size=2000)
    c = metrics.coverage(s, y)
    assert abs(c["cover_50"] - 0.5) < 0.04 and abs(c["cover_80"] - 0.8) < 0.03 and abs(c["cover_95"] - 0.95) < 0.02
    assert c["width_50"] < c["width_80"] < c["width_95"]
    assert metrics.coverage(s * 3, y)["cover_80"] > 0.99                        # 広げれば被覆率は上がる ＝ 幅も見る
    assert metrics.brier([1, 0, 0.5], [1, 0, 1]) == pytest.approx(0.25 / 3)
    rel = metrics.reliability(np.array([0.1, 0.45, 0.45, 0.9]), np.array([0, 1, 0, 1]))
    assert [b["件数"] for b in rel] == [1, 2, 0, 0, 1] and rel[1]["実際"] == 0.5 and rel[2]["予想"] is None


def test_block_bootstrap_is_deterministic_and_wider_for_dependent_data():
    rng = np.random.default_rng(0)
    iid = rng.normal(0.1, 1, 1000)
    a, b = metrics.block_bootstrap_mean(iid, 10, 500, 0), metrics.block_bootstrap_mean(iid, 10, 500, 0)
    assert a == b and a["ci_low"] < a["mean"] < a["ci_high"]
    sticky = np.repeat(rng.normal(0.1, 1, 100), 10)                             # 10 日ずつ同じ値 ＝ 独立な観測は 1/10
    wide = metrics.block_bootstrap_mean(sticky, 10, 500, 0)
    naive = 1.96 * sticky.std() / np.sqrt(len(sticky))
    assert (wide["ci_high"] - wide["ci_low"]) / 2 > 2 * naive                   # 素朴な区間よりずっと広い


def test_historical_baseline_keeps_blocks_and_ignores_condition():
    pool = np.arange(40, dtype=np.float32).reshape(8, 5)
    h = baselines.historical(pool, n_origins=3, n=50, seed=0)
    assert h.shape == (3, 50, 5) and np.array_equal(h[0], h[2])                 # どの起点にも同じ分布
    assert all(any(np.array_equal(row, p) for p in pool) for row in h[0])       # 塊の中の順番を保つ
    assert np.array_equal(h, baselines.historical(pool, 3, 50, seed=0))


def test_light_and_ridge_baselines_shapes():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(300, 12, 3)).astype(np.float32)
    Y = (0.004 * X[:, -1, :1] + 0.01 * rng.normal(size=(300, 5))).astype(np.float32)
    lt = baselines.light(X[:250], Y[:250], X[250:], n=100)
    assert lt["prob_up"].shape == (50,) and ((lt["prob_up"] >= 0) & (lt["prob_up"] <= 1)).all()
    assert lt["samples_5d"].shape == (50, 100) and (np.diff(lt["samples_5d"], axis=1) >= 0).all()
    assert np.corrcoef(lt["prob_up"], X[250:, -1, 0])[0, 1] > 0.5               # 条件（起点の足）を見ている
    assert baselines.ridge_point(X[:250], Y[:250], X[250:]).shape == (50,)


def _frame(gan_shift: float, n=300, folds=("f1", "f2", "f3", "f4", "f5"), seeds=(0, 1, 2), width=1.0):
    """hist の CRPS を 1.0 前後、cgan を `gan_shift` だけずらした起点ごとの表。"""
    rng = np.random.default_rng(0)
    rows = []
    for f in folds:
        origin = pd.bdate_range("2020-01-01", periods=n).strftime("%Y-%m-%d")
        base = {"fold": f, "origin": origin, "y5": rng.normal(size=n), "drop_real": False}
        noise = rng.normal(0, 0.05, n)
        cov = {"in_50": 0.5, "in_80": (rng.random(n) < 0.8 * width).astype(float), "in_95": (rng.random(n) < 0.95).astype(float),
               "width_50": 1.0, "width_80": 2.0, "width_95": 3.0, "prob_up": 0.5, "median": 0.0, "prob_drop": 0.0}
        rows.append(pd.DataFrame({**base, "model": "hist", "seed": np.nan, "crps": 1.0 + noise, **cov}))
        rows.append(pd.DataFrame({**base, "model": "light", "seed": np.nan, "crps": 1.0 + noise, **cov}))
        for s in seeds:
            rows.append(pd.DataFrame({**base, "model": "cgan", "seed": s,
                                      "crps": 1.0 + noise + gan_shift + rng.normal(0, 0.01, n), **cov}))
    return pd.concat(rows, ignore_index=True)


EV = {"block_len": 10, "n_boot": 300, "boot_seed": 0, "min_blocks_better": 4,
      "coverage_80": [0.72, 0.88], "coverage_95": [0.90, 0.99]}


def test_judge_follows_the_preregistered_rules():
    good = evaluate.judge(_frame(-0.05), EV, flagged=[])
    assert good["verdict"] == "採る" and all(good["ok"].values()) and good["b_better"] == 5
    assert good["a_diff_vs_hist"]["ci_high"] < 0 and good["a_diff_vs_hist"]["non_overlapping"]["n"] == 300
    assert evaluate.judge(_frame(+0.05), EV, [])["verdict"] == "落とす"
    # 差は負でも、印が付いたら (d) が欠けて「保留」止まり
    held = evaluate.judge(_frame(-0.05), EV, flagged=["f3_seed1"])
    assert held["verdict"] == "保留" and not held["ok"]["d"] and held["ok"]["a"]
    # 区間が狭すぎる（被覆率が帯の外）でも「採る」にならない
    narrow = evaluate.judge(_frame(-0.05, width=0.5), EV, [])
    assert narrow["verdict"] == "保留" and not narrow["ok"]["d"]


def test_summary_marks_unavailable_metrics_as_nan():
    df = _frame(-0.05, n=40, folds=("f1",), seeds=(0,))
    ridge = df[df["model"] == "hist"].assign(model="ridge")[["fold", "origin", "y5", "drop_real", "model", "seed", "median"]]
    out = evaluate.summary(pd.concat([df, ridge], ignore_index=True))
    r = out[(out["model"] == "ridge") & (out["fold"] == "all")].iloc[0]
    assert np.isnan(r["crps"]) and np.isnan(r["brier_up"]) and np.isnan(r["cover_80"]) and np.isfinite(r["mae_median"])
    assert set(out["fold"]) == {"f1", "all"}


def test_cli_run_and_predict_end_to_end(tmp_path, monkeypatch, capsys):
    """プラン §8 のテスト 7: 小さなデータで 学習 → 保存 → 読込 → 推論 → 評価（⚠ 合成データは動作確認だけ）。"""
    from cli import scenario

    rng = np.random.default_rng(0)
    n = 700
    ts = pd.bdate_range("2019-01-01", periods=n, tz="UTC")
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    bars = pd.DataFrame({"ts": ts, "open": c, "high": c, "low": c, "close": c, "volume": rng.uniform(1e6, 2e6, n)})
    cfg = {"name": "toy", "symbol": "TOY", "period": "d", "market": "TOY", "window": 20, "horizon": 5,
           "n_scenarios": 60, "seeds": [0, 1],
           "split": {"test_starts": ["2020-09-01", "2021-03-01"], "test_end": "2021-08-01", "val_years": 1},
           "model": {"encoder": "gru", "hidden": 8, "layers": 1, "latent_dim": 4, "lr": 1e-3, "betas": [0.5, 0.9],
                     "n_critic": 1, "gp_lambda": 10.0, "batch": 64, "max_epochs": 2, "eval_every": 1,
                     "eval_scenarios": 20, "patience": 8, "aux_loss": "none"},
           "eval": {**EV, "min_blocks_better": 2}}
    monkeypatch.setattr(scenario.config, "scenario", lambda name: json.loads(json.dumps(cfg)))
    monkeypatch.setattr(data, "load", lambda cf: (bars, data.build_features(bars), data.BASE_FEATURES))
    monkeypatch.setattr(data, "fingerprint", lambda period, symbol: {"symbol": symbol, "sha256": "toy"})
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    monkeypatch.setenv("AIL_TORCH_DEVICE", "cpu")

    import argparse
    scenario.run(argparse.Namespace(config="toy", folds=None, seeds=None, max_epochs=None))
    run_dir = capsys.readouterr().out.strip().splitlines()[-1]
    have = set(p.name for p in (tmp_path / run_dir.split("/")[-1]).iterdir())
    assert {"config.json", "inputs.json", "env.json", "origins.csv", "scores.csv", "history.csv", "quality.csv",
            "verdict.json", "log.txt", "fitted"} <= have
    assert "summary.csv" not in have                                  # ⚠ 台帳・検証タブに出さない（記録 §0 決定 6）
    verdict = json.load(open(f"{run_dir}/verdict.json", encoding="utf-8"))
    assert verdict["verdict"] in ("採る", "保留", "落とす") and set(verdict["ok"]) == set("abcde")
    scores = pd.read_csv(f"{run_dir}/scores.csv")
    assert set(scores["model"]) == {"cgan", "hist", "light", "ridge"}
    assert scores[(scores["model"] == "ridge")]["crps"].isna().all()

    scenario.predict(argparse.Namespace(run=run_dir, latest=True, asof=None, seed=0))
    out = json.loads(capsys.readouterr().out)
    assert out["as_of"] == str(ts[-1].date()) and out["n_scenarios"] == 60 and out["return_basis"]
    assert out["model_version"].endswith("f2_seed0.pt") and 0 <= out["probability_up_5d"] <= 1
    assert out["training_cutoff"] < "2020-03-01"                       # f2 の学習は検証（2020-03-01〜）の前で終わる
    # 同じ重み・同じ種なら同じ予測（保存 → 読込の再現）
    scenario.predict(argparse.Namespace(run=run_dir, latest=True, asof=None, seed=0))
    assert json.loads(capsys.readouterr().out)["median_return_5d"] == out["median_return_5d"]
    with pytest.raises(SystemExit):                                    # 最初のテストの始まりより前は、使える重みが無い
        scenario.predict(argparse.Namespace(run=run_dir, latest=False, asof="2020-06-01", seed=0))

    # 再現の確認と図（`cli.scenario_report`）: 保存した重みから作り直した CRPS が origins.csv と合う・SVG が壊れていない
    import sys
    import xml.etree.ElementTree as ET
    from cli import scenario_report

    monkeypatch.setattr(sys, "argv", ["x", "--run", run_dir, "--out", str(tmp_path / "fig"), "--fold", "f2", "--seed", "1"])
    scenario_report.main()
    text = capsys.readouterr().out
    assert "== 再現: f2 種 1" in text
    rep = scenario_report.reproduce(run_dir, pd.read_csv(f"{run_dir}/origins.csv"), "f2", 1)
    assert rep["max_abs_diff"] < 1e-9                                  # CPU では 1 ビットも違わないはず（許容は丸めの分）
    for name in ("cgan-calibration.svg", "cgan-intervals.svg"):
        root = ET.parse(tmp_path / "fig" / name).getroot()
        texts = [e for e in root.iter("{http://www.w3.org/2000/svg}text")]
        assert texts and not any(set((e.get("class") or "").split()) & {"s1", "s2", "s3"} for e in texts)   # 文字は系列の色を着ない

    # 一部だけの実行は判定を出さない
    scenario.run(argparse.Namespace(config="toy", folds="f1", seeds="0", max_epochs=1))
    part = capsys.readouterr().out.strip().splitlines()[-1]
    assert part.endswith("_scn_toy_partial") and not (tmp_path / part.split("/")[-1] / "verdict.json").exists()
