"""条件付き WGAN-GP と CRPS（`ail/scenario/model.py`・`metrics.py`。[プラン](../../../docs/plans/archive/cgan-scenario-forecast.md) §8 のテスト 4〜7 のうちモデルの分）。

⚠ **データは合成・学習は数 epoch・CPU**（配線の検査だけ。⚠ 多様性の最低限の動作確認であって、品質の保証ではない）。
"""

from __future__ import annotations

import numpy as np
import pytest

from ail.scenario import metrics, model

torch = pytest.importorskip("torch")
CPU = torch.device("cpu")
CFG = {"encoder": "gru", "hidden": 8, "layers": 1, "latent_dim": 4, "lr": 1e-3, "betas": [0.5, 0.9],
       "n_critic": 2, "gp_lambda": 10.0, "batch": 32, "max_epochs": 4, "eval_every": 2,
       "eval_scenarios": 20, "patience": 8}


def _toy(n=96, window=12, features=3, horizon=5, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, window, features)).astype(np.float32)
    Y = (0.5 * X[:, -1, :1] + rng.normal(size=(n, horizon))).astype(np.float32)   # 条件（起点の足）に依る未来
    return X, Y


def test_cumulative_returns_definition():
    r = np.log(np.array([[1.1, 1 / 1.1, 0.5]]))
    assert np.allclose(metrics.cumulative_returns(r), [[0.1, 0.0, -0.5]])


def test_crps_known_values_and_brute_force():
    assert np.allclose(metrics.crps_samples(np.array([[0.3, 0.3, 0.3]]), np.array([0.3])), 0.0)
    assert np.allclose(metrics.crps_samples(np.array([[0.2]]), np.array([0.5])), 0.3)     # 1 本なら絶対誤差
    assert np.allclose(metrics.crps_samples(np.array([[0.0, 1.0]]), np.array([0.5])), 0.25)
    rng = np.random.default_rng(0)
    s, y = rng.normal(size=(7, 50)), rng.normal(size=7)
    brute = np.abs(s - y[:, None]).mean(1) - 0.5 * np.abs(s[:, :, None] - s[:, None, :]).mean((1, 2))
    assert np.allclose(metrics.crps_samples(s, y), brute)
    # ⚠ 低いほどよい: 真の分布からの標本は、ずらした標本より小さい
    truth = rng.normal(size=(400, 200))
    obs = rng.normal(size=400)
    assert metrics.crps_samples(truth, obs).mean() < metrics.crps_samples(truth + 1.0, obs).mean()


def test_generate_shape_finite_varies_with_z_and_is_deterministic():
    """プラン §8 のテスト 5。"""
    X, Y = _toy()
    torch.manual_seed(0)
    gen, _ = model.build(X.shape[2], Y.shape[1], CFG)
    a = model.generate(gen, X[:10], n=30, seed=1, device=CPU)
    assert a.shape == (10, 30, 5) and np.isfinite(a).all()
    assert a.std(axis=1).min() > 0                                   # 同じ条件でも z が違えば違う未来
    assert np.array_equal(a, model.generate(gen, X[:10], n=30, seed=1, device=CPU))
    assert not np.array_equal(a, model.generate(gen, X[:10], n=30, seed=2, device=CPU))
    # 刻み方（chunk）を変えても、起点 0 の 30 本は同じ z から出る
    assert np.allclose(a[:4], model.generate(gen, X[:10], n=30, seed=1, device=CPU, chunk=4)[:4], atol=1e-6)


def test_critic_returns_real_scores_and_penalty_is_on_future_only():
    X, Y = _toy()
    torch.manual_seed(0)
    gen, critic = model.build(X.shape[2], Y.shape[1], CFG)
    x, y = torch.as_tensor(X[:16]).requires_grad_(True), torch.as_tensor(Y[:16])
    score = critic(x, y)
    assert score.shape == (16,) and (score.min() < 0 or score.max() > 1)     # 確率ではない（sigmoid なし）
    fake = gen(x, torch.randn(16, CFG["latent_dim"])).detach()
    gp = model.gradient_penalty(critic, x, y, fake, torch.rand(16, 1))
    assert gp.ndim == 0 and torch.isfinite(gp)
    gp.backward()
    # 罰則は Critic の重みまで届く（⚠ 最後の層の bias は Y に関する勾配に現れないので None のまま）
    assert critic.head[0].weight.grad is not None and critic.enc.gru.weight_ih_l0.grad is not None
    assert all(p.grad is None for p in gen.parameters())                     # ⚠ detach した生成器には届かない


def test_diagnose_flags_collapse_and_extremes():
    rng = np.random.default_rng(0)
    healthy = rng.normal(size=(20, 50, 5))
    d = model.diagnose(healthy, real_5d_sigma_scaled=5 ** 0.5)
    assert 0.8 < d["diversity_ratio"] < 1.2 and not d["collapsed"] and not d["extreme"] and d["finite"]
    same = np.repeat(rng.normal(size=(20, 1, 5)), 50, axis=1)                # z を変えても同じ未来
    assert model.diagnose(same, 5 ** 0.5)["collapsed"]
    assert model.diagnose(healthy * 20, 5 ** 0.5)["extreme"]


def test_fit_selects_checkpoint_by_val_crps_and_save_load_reproduces(tmp_path):
    """プラン §8 のテスト 6・7（小さなデータで 学習 → 保存 → 読込 → 推論 → 評価）。"""
    X, Y = _toy()
    lines = []
    gen, hist = model.fit(X[:64], Y[:64], X[64:], Y[64:], CFG, seed=0, y_scale=0.01,
                          log=lines.append, device=CPU)
    assert [h["epoch"] for h in hist] == [2, 4] and len(lines) == 2
    assert sum(h["selected"] for h in hist) == 1
    assert min(hist, key=lambda h: h["val_crps"])["selected"]                # 選ぶのは検証の CRPS（損失ではない）
    assert all(np.isfinite(h["val_crps"]) and h["finite"] for h in hist)

    again, hist2 = model.fit(X[:64], Y[:64], X[64:], Y[64:], CFG, seed=0, y_scale=0.01,
                             log=lambda _t: None, device=CPU)
    assert [h["val_crps"] for h in hist] == [h["val_crps"] for h in hist2]   # 同じ種 ＝ 同じ学習（CPU）

    path = str(tmp_path / "f1_seed0.pt")
    scaler_doc = {"features": ["a", "b", "c"], "mean": [0.0] * 3, "std": [1.0] * 3, "y_scale": 0.01,
                  "fit_until": "2020-01-01"}
    model.save(path, gen, scaler_doc, CFG, X.shape[2], Y.shape[1], {"fold": "f1", "seed": 0})
    back, doc = model.load(path, device=CPU)
    assert doc["scaler"] == scaler_doc and doc["meta"] == {"fold": "f1", "seed": 0}
    a = model.generate(gen, X[64:], n=25, seed=7, device=CPU)
    assert np.array_equal(a, model.generate(back, X[64:], n=25, seed=7, device=CPU))
    assert np.isfinite(metrics.crps_5d(a * 0.01, Y[64:] * 0.01))


def test_unknown_encoder_is_refused():
    with pytest.raises(SystemExit):
        model.build(3, 5, {**CFG, "encoder": "tcn"})
