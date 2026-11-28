"""レジストリ `20261128-triple-barrier-gate20`: 先 20 日のパスが上下の線のどちらに先に当たるかを当てる検知器（`ail/ideas/`）。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の H3 の出力・`layer_order`）／ ラベルは訓練分割の先 20 本だけで決まる（頭 20 行・末尾 20 行は使わない）／
   出力の契約（3 つ返す・行数・0〜100・列は対の相手 H3 の own の絞りと同じ）／ 表記。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import contracts, names, registry
from ail.detectors import fwd
from ail.features import labels
from ail.ideas import i20261128_triple_barrier_gate20 as idea
from cli import build


def _brute(y, a=idea.A, ahead=idea.AHEAD, win=idea.SIGMA_WINDOW):
    """ラベルの定義をそのまま書いた遅い実装（照合の相手）。"""
    n = len(y)
    side, use = np.zeros(n), np.zeros(n, dtype=bool)
    for t in range(n):
        if t < win or t + ahead >= n:                 # σ が取れない頭 ／ 行 t+20 が無い末尾 20 行
            continue
        b = a * np.std(y[t - win:t], ddof=1) * np.sqrt(ahead)
        use[t], c = True, 0.0
        for k in range(ahead):
            c += y[t + k]
            if c >= b:
                side[t] = 1.0
                break
            if c <= -b:
                side[t] = -1.0
                break
    return side, use


def test_barrier_label_matches_definition_and_drops_head_and_tail():
    rng = np.random.default_rng(0)
    n = 160
    ts = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    ya, yb = rng.normal(0.0, 0.01, n), rng.normal(0.0005, 0.02, n)
    tr = pd.concat([pd.DataFrame({"symbol": "A", "ts": ts, "y": ya}), pd.DataFrame({"symbol": "B", "ts": ts, "y": yb})],
                   ignore_index=True).sample(frac=1.0, random_state=1)        # 並びを崩しても行で返す
    side, use, doc = idea.barrier_label(tr)
    got = pd.DataFrame({"symbol": tr["symbol"].to_numpy(), "ts": tr["ts"].to_numpy(), "side": side, "use": use}).sort_values(["symbol", "ts"])
    for sym, y in (("A", ya), ("B", yb)):
        want_side, want_use = _brute(y)
        g = got[got["symbol"] == sym]
        assert np.array_equal(g["use"].to_numpy(), want_use)
        assert np.array_equal(g["side"].to_numpy()[want_use], want_side[want_use])
        assert not g["use"].to_numpy()[:idea.SIGMA_WINDOW].any() and not g["use"].to_numpy()[-idea.AHEAD:].any()
    assert set(np.unique(side)) <= {-1.0, 0.0, 1.0} and (side > 0).any() and (side < 0).any() and (side[use] == 0).any()
    assert doc["使える行"] == int(use.sum()) == 2 * (n - idea.SIGMA_WINDOW - idea.AHEAD)
    assert abs(doc["上に先の割合"] + doc["下に先の割合"] + doc["どちらにも当たらない割合"] - 1.0) < 1e-3


def test_barrier_label_sees_only_the_next_twenty_rows():
    n = 80
    ts = pd.date_range("2020-01-01", periods=n, freq="D", tz="UTC")
    y = 0.01 * np.where(np.arange(n) % 2 == 0, 1.0, -1.0)      # 行ったり来たり ＝ どちらにも当たらない
    y[40] = 0.2                                                # 41 本目の y だけ大きく上げる
    side, use, _doc = idea.barrier_label(pd.DataFrame({"symbol": "A", "ts": ts, "y": y}))
    assert use[20:60].all() and not use[:20].any() and not use[60:].any()
    assert (side[21:41] == 1.0).all()                          # 行 21〜40 は y_40 を先 20 本に含む
    assert side[20] == 0.0 and (side[41:60] == 0.0).all()      # 行 20 は y_39 まで ／ 行 41 から先は含まない
    down = y.copy()
    down[40] = -0.2
    side2, _use2, _doc2 = idea.barrier_label(pd.DataFrame({"symbol": "A", "ts": ts, "y": down}))
    assert (side2[21:41] == -1.0).all() and side2[20] == 0.0


def _panel(n_days=700, n_sym=4, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2015-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        r = rng.normal(0.0006, 0.011, n_days)
        close = 100 * np.exp(np.cumsum(r))
        bars = pd.DataFrame({"ts": ts, "close": close})
        x = pd.DataFrame({"own_ret_1": np.r_[0.0, r[:-1]], "own_ret_5": pd.Series(r).rolling(5).sum().shift(1).to_numpy(),
                          "own_vol_20": pd.Series(r).rolling(20).std().shift(1).to_numpy(),
                          "own_trend20_dd": pd.Series(close).rolling(20).max().to_numpy() / close - 1.0})
        y = labels.build_scales(bars, [idea.AHEAD])
        d = pd.concat([bars, x, y], axis=1)
        d["symbol"] = f"S{i}"
        d["y"] = np.r_[r[1:], np.nan]
        rows.append(d)
    return pd.concat(rows, ignore_index=True).sort_values(["ts", "symbol"]).dropna().reset_index(drop=True)


def _split(p, q=0.7):
    cut = pd.to_datetime(p["ts"]).quantile(q)
    return p[p["ts"] < cut], p[p["ts"] >= cut]


def _ctx(**kw):
    return {"seed": 0, "model": registry.resolve("model", "Ridge"), "k": 8, **kw}


def test_detector_contract_and_same_columns_as_h3_own():
    p = _panel()
    tr, te = _split(p)
    feats = contracts.feature_columns(p)
    out = registry.resolve("detector", idea.NAME)(tr, te, feats, _ctx())
    assert len(out) == 3                                       # 買い% ／ 出口% ／ 記録（保有日数は返さない）
    buy, ex, doc = out
    assert len(buy) == len(te) == len(ex)
    assert np.all((buy >= 0) & (buy <= 100)) and np.all((ex >= 0) & (ex <= 100))
    assert np.isfinite(buy).all() and np.isfinite(ex).all()
    _b3, d3 = fwd.forward_gate(idea.AHEAD, tr, te, feats, _ctx(fwd_columns="own"))
    assert doc["columns"] == d3["columns"]                     # 対の相手（H3・own の絞り）と同じ列
    assert not any(c.startswith("own_trend") or c.startswith(labels.SCALE_PREFIX) for c in doc["columns"])
    assert doc["訓練の行"] == doc["使える行"] < len(tr) and doc["scale"] == 20 and doc["a"] == 1.0


def test_existing_paths_are_unchanged():
    """⚠ 無い config は 1 ビットも変わらない: 登録するだけ・既存の登録名を上書きしない・既存の検知器の出力は前と同じ。"""
    assert idea.NAME in registry.available("detector")
    old = {fwd.name_of("H3", 20), fwd.PICK_NAME, *fwd.EXTRA_NAMES, *fwd.CENTERED_NAMES, f"H1 先{fwd.WINDOW}日ゲート（全列・学習）"}
    assert idea.NAME not in old and old <= set(registry.available("detector"))
    assert build.layer_order(["own"]) == ["own"]
    p = _panel()
    tr, te = _split(p)
    feats = contracts.feature_columns(p)
    for kw in ({}, {"fwd_columns": "own"}):
        got, _d = registry.resolve("detector", fwd.name_of("H3", 20))(tr, te, feats, _ctx(**kw))
        want, _w = fwd.forward_gate(20, tr, te, feats, _ctx(**kw))
        assert np.array_equal(np.asarray(got), np.asarray(want))
    snap = tr.copy()
    registry.resolve("detector", idea.NAME)(tr, te, feats, _ctx())
    pd.testing.assert_frame_equal(tr, snap)                    # 検知器は渡されたテーブルを書き換えない


def test_spelling_registered():
    assert names.method_slug(idea.NAME) == "tb20-barrier"
    assert names.table()["method"][fwd.name_of("H3", 20)] == "h3-gate20"   # 既存の行は触っていない
