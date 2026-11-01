"""登録簿 `20261025-h5-pick-centered`: H5 の 4 本を土台差（25-7）で比べて期間を選ぶ検知器（`ail/ideas/`）。

⚠ 既存の H5（25-5）は 1 ビットも変わらない（土台差を引かない最大のまま）／ 新しい検知器の買い% は土台差の 4 本の最大・保有日数はその期間・出口% は 0。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import contracts, registry
from ail.detectors import fwd
from ail.features import labels
from ail.ideas import i20261025_h5_pick_centered as idea


def _panel(n_days=700, n_sym=4, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2015-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        r = rng.normal(0.0006, 0.011, n_days)
        close = 100 * np.exp(np.cumsum(r))
        bars = pd.DataFrame({"ts": ts, "close": close})
        x = pd.DataFrame({"own_ret_1": np.r_[0.0, r[:-1]], "own_ret_5": pd.Series(r).rolling(5).sum().shift(1).to_numpy(),
                          "own_vol_20": pd.Series(r).rolling(20).std().shift(1).to_numpy()})
        y = labels.build_scales(bars, list(fwd.PICK_WINDOWS))
        d = pd.concat([bars, x, y], axis=1)
        d["symbol"] = f"S{i}"
        d["y"] = np.r_[r[1:], np.nan]
        rows.append(d)
    return pd.concat(rows, ignore_index=True).sort_values(["ts", "symbol"]).dropna().reset_index(drop=True)


def _split(p, q=0.7):
    cut = pd.to_datetime(p["ts"]).quantile(q)
    return p[p["ts"] < cut], p[p["ts"] >= cut]


def _ctx():
    return {"seed": 0, "model": registry.resolve("model", "Ridge"), "k": 8}


def test_centered_pick_is_argmax_of_centered_gates_and_h5_is_unchanged():
    p = _panel()
    tr, te = _split(p)
    feats = contracts.feature_columns(p)
    fn = registry.resolve("detector", idea.NAME)
    buy, ex, hold, doc = fn(tr, te, feats, _ctx())
    assert len(buy) == len(te) and np.all((buy >= 0) & (buy <= 100)) and np.all(ex == 0)
    assert set(np.unique(hold)) <= set(fwd.PICK_WINDOWS) and doc["中心化"] is True and set(doc["土台"]) == {str(w) for w in fwd.PICK_WINDOWS}
    cent = np.vstack([fwd.forward_gate(w, tr, te, feats, _ctx(), center=True)[0] for w in fwd.PICK_WINDOWS])
    pick = np.argmax(cent, axis=0)
    assert np.allclose(buy, cent[pick, np.arange(len(te))]) and np.array_equal(hold, np.asarray(fwd.PICK_WINDOWS)[pick])
    # ⚠ 既存の H5 は土台差を引かない最大のまま
    b5, _e, h5, d5 = fwd.h5_pick(tr, te, feats, _ctx())
    raw = np.vstack([fwd.forward_gate(w, tr, te, feats, _ctx())[0] for w in fwd.PICK_WINDOWS])
    assert np.allclose(b5, raw.max(axis=0)) and "中心化" not in d5
    assert doc["選んだ期間の分布"] != d5["選んだ期間の分布"] or not np.allclose(buy, b5)   # 選ぶ量が違う


def test_spelling_registered():
    from ail import names
    assert names.table()["method"][idea.NAME] == "h5-gate-pick-centered"
    assert names.table()["mix"]["R14 買い係が期間を選ぶ（土台差）・損切り"] == "roles-pick-centered-stop"
