"""登録簿 `20261025-mkt-layer`: 市場全体の状態の層 `mkt_`（`ail/ideas/i20261025_mkt_layer.py`）。

⚠ 見るもの: 無い config は 1 ビットも変わらない（並び・登録名）／ 全銘柄に同じ値 ／ 足 i の値は足 i までの足だけで決まる（先読みなし）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import registry
from ail.ideas import i20261025_mkt_layer as mkt
from cli import build


def _panel(n_sym=8, n=200, seed=0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    panel = {}
    for k in range(n_sym):
        c = 50 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        df = pd.DataFrame({"ts": ts, "close": c})
        if k == 0:
            df = df.drop(index=[30, 31]).reset_index(drop=True)      # 休場の穴（前方埋めしない）
        panel[f"S{k}"] = df
    return panel


def test_absent_config_is_unchanged():
    assert "mkt" in registry.available("feature")
    assert "mkt" not in build.ORDER
    assert build.layer_order(["own", "cs"]) == ["own", "cs"]
    assert build.layer_order(["own", "mkt", "cs"]) == ["own", "cs", "mkt"]


def test_same_value_for_all_symbols_and_columns():
    out = registry.resolve("feature", "mkt")(_panel(), {})
    cols = [f"mkt_{c}" for c in mkt.COLUMNS]
    assert all(list(v.columns) == cols for v in out.values())
    a, b = out["S1"].iloc[100], out["S5"].iloc[100]
    pd.testing.assert_series_equal(a, b, check_names=False)
    b20 = out["S1"]["mkt_breadth_ma20"].dropna()
    assert ((b20 >= 0) & (b20 <= 1)).all()


def test_no_lookahead():
    panel = _panel()
    full = mkt.market_state(panel)
    t = full.index[150]
    cut = {s: df[df["ts"].values <= np.datetime64(t)] for s, df in panel.items()}
    part = mkt.market_state(cut)
    pd.testing.assert_series_equal(full.loc[t], part.loc[t])
