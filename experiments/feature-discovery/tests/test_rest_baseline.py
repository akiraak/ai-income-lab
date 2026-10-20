"""基準線「弱い相場は休む」（モデルなし。rules.md 25-8・記録 horizon-roles-trader.md §9）の配線。

⚠ **ここで落としたいのは 4 つ。**
  1. 4 本の登録名（先 5 ／ 10 ／ 20 ／ 40 日）と綴りがあり、検証結果一覧では「基準 」＝ 数えない（13-9 の 4）
  2. 規則が事前固定のとおり（直近 N 日の上がった割合 ≥ π で買う・下回れば休む）＝ 素朴なループと 1 ビットも違わない
  3. ⚠ **位置 t − W より後のラベルを読まない**（末尾 W 日のラベルを壊しても出力が変わらない）
  4. `fold_buy_pct` の基準線の口に並べて 0 ／ 100 の買い% になり、既存の 2 本（常に上・直前符号）は変わらない
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail import catalog, names, registry
from ail.models import baselines
from cli.run import fold_buy_pct
import ail.bootstrap  # noqa: F401

W = 10
NAME = baselines.rest_name_of(W)


def _panel(n_days=700, n_sym=3, seed=3):
    """`own` 風の列 2 本 ＋ `y_fwd_10` を持つ合成パネル（相場の強弱が変わるよう、ドリフトを時期で変える）。"""
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2016-01-04", periods=n_days, freq="B", tz="UTC")
    drift = np.where((np.arange(n_days) // 120) % 2 == 0, 0.002, -0.002)
    rows = []
    for i in range(n_sym):
        r = rng.normal(0.0, 0.01, n_days) + drift
        lc = np.cumsum(r)
        d = pd.DataFrame({"ts": ts, "symbol": f"S{i}", "close": np.exp(lc + 4.6),
                          "own_ret_1": np.r_[0.0, r[:-1]],
                          "own_vol_20": pd.Series(r).rolling(20).std().shift(1).fillna(0.01).to_numpy(),
                          "y": np.r_[r[1:], 0.0]})
        d[f"y_fwd_{W}"] = np.r_[lc[W:] - lc[:-W], [np.nan] * W]
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def _split(p, q=0.65):
    cut = p["ts"].quantile(q)
    return p[p["ts"] < cut].reset_index(drop=True), p[p["ts"] >= cut].reset_index(drop=True)


def _reference(w, n, tr, te):
    """素朴なループ（事前固定の文をそのまま書いたもの）。"""
    col = f"y_fwd_{w}"
    pi = float((tr[col].dropna() > 0).mean())
    both = pd.concat([tr, te], ignore_index=True)
    dates = sorted(both["ts"].unique())
    share = {}
    for d in dates:
        y = both.loc[both["ts"] == d, col].dropna()
        share[d] = float((y > 0).mean()) if len(y) else np.nan
    idx = {d: i for i, d in enumerate(dates)}
    out = []
    for d in te["ts"]:
        i = idx[d]
        win = [share[dates[j]] for j in range(max(0, i - w - n + 1), i - w + 1)]
        win = [v for v in win if not np.isnan(v)]
        r = float(np.mean(win)) if win else np.nan
        out.append(1.0 if (np.isnan(r) or r >= pi) else 0.0)
    return np.asarray(out)


def test_names_spellings_and_not_counted():
    assert baselines.REST_WINDOW == 252 and baselines.REST_HORIZONS == (5, 10, 20, 40)
    assert set(registry.resolve_all("model", baselines.REST_NAMES)) == set(baselines.REST_NAMES)
    for w in baselines.REST_HORIZONS:
        nm = baselines.rest_name_of(w)
        assert nm == f"弱い相場は休む（先{w}日・直近252日）"
        assert names.method_slug(nm) == f"rest-weak{w}-n252"
        assert catalog.is_trial({"手法名": f"基準 {nm}", "検証方式": "閾値売買"}) is False
    # 既存の 2 本の綴りはそのまま
    assert names.method_slug("常に上（ドリフト）") == "drift" and names.method_slug("直前リターンの符号") == "lastsign"


def test_rule_matches_the_naive_loop_and_rests_sometimes():
    p = _panel()
    tr, te = _split(p)
    for n in (60, baselines.REST_WINDOW):
        got = baselines.rest_when_weak(W, tr, te, n=n)
        assert got.shape == (len(te),) and set(np.unique(got)) <= {0.0, 1.0}
        assert np.array_equal(got, _reference(W, n, tr, te))
    got = baselines.rest_when_weak(W, tr, te, n=60)
    assert 0.05 < got.mean() < 0.95            # 休む日も買う日もある（規則が働いている）
    # 登録した関数は同じ値（N は 252）
    fn = registry.resolve("model", NAME)
    assert np.array_equal(fn(tr, te, [], {}), baselines.rest_when_weak(W, tr, te))


def test_labels_after_t_minus_w_are_never_read():
    p = _panel()
    tr, te = _split(p)
    base = baselines.rest_when_weak(W, tr, te, n=60)
    tail = sorted(te["ts"].unique())[-W:]            # 末尾 W 日のラベルは、どの行の窓にも入らない
    te2 = te.copy()
    te2.loc[te2["ts"].isin(tail), f"y_fwd_{W}"] = -5.0
    assert np.array_equal(base, baselines.rest_when_weak(W, tr, te2, n=60))
    # 1 日だけ前（位置 −W−1 ＝ 窓の終わりの日）は読む ＝ 壊せば変わりうる（規則が「確定した日まで」を見ている）
    te3 = te.copy()
    te3[f"y_fwd_{W}"] = np.where(te3["ts"] < tail[0], -5.0, te3[f"y_fwd_{W}"])
    assert not np.array_equal(base, baselines.rest_when_weak(W, tr, te3, n=60))


def test_fold_buy_pct_baseline_slot_gives_0_or_100_and_keeps_the_old_two():
    p = _panel()
    tr, te = _split(p)
    feats = [c for c in p.columns if c.startswith("own_")]
    exp = {"trading": {"style": "threshold", "thresholds": [50], "form": "shared"}, "k": 8}
    ctx = {"seed": 0, "model": registry.resolve("model", "Ridge"), "k": 8}
    bl = registry.resolve_all("model", ["常に上（ドリフト）", "直前リターンの符号", NAME])
    buy, exits, n_cols, doc = fold_buy_pct(tr, te, feats, exp, ctx, model=ctx["model"], selectors={}, detectors={},
                                           baselines=bl, form="shared", k=8, groups=te.groupby("symbol").indices,
                                           f=1, picked=[], log=lambda *_: None)
    assert set(buy) == {"基準 常に上（ドリフト）", "基準 直前リターンの符号", f"基準 {NAME}"}
    assert np.array_equal(buy["基準 常に上（ドリフト）"], np.full(len(te), 100.0))
    assert np.array_equal(buy[f"基準 {NAME}"], 100.0 * baselines.rest_when_weak(W, tr, te))
    assert n_cols[f"基準 {NAME}"] == 0.0
