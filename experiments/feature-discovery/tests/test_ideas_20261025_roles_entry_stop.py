"""登録簿 `20261025-roles-entry-stop`: 役割つきの組の損切りを「建値から −x%」（simulate の形 B）にする口（2026-11-01）。

⚠ 見るもの: 省けば 1 ビットも変わらない ／ `simulate_topk` の形 B は 1 銘柄・K 1 で `simulate` の形 B と同じ売買 ／
`[[trading.mix]] entry_stop_pct` は roles だけ・値域の検査。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail.validation import simulate as sim
from cli import run


def _one(n=300, seed=4):
    rng = np.random.default_rng(seed)
    b = rng.uniform(30, 70, n)
    e = rng.uniform(30, 70, n)
    y = rng.normal(-0.01, 0.03, n)              # 下げ気味 ＝ 建値から −10% に届く取引がある
    d = pd.date_range("2021-01-01", periods=n, freq="B")
    return b, e, y, d


def test_topk_absent_is_unchanged():
    b, e, y, d = _one()
    a = sim.simulate_topk(b, y, d, ["X"] * len(b), 50, 1, exit_pct=e, min_hold_days=3)
    c = sim.simulate_topk(b, y, d, ["X"] * len(b), 50, 1, exit_pct=e, min_hold_days=3, stop_loss_pct=None)
    np.testing.assert_array_equal(a["pos"], c["pos"])
    pd.testing.assert_series_equal(a["port_net_bp"], c["port_net_bp"])


@pytest.mark.parametrize("hold", [None, 5])
def test_topk_entry_stop_matches_simulate_for_one_symbol(hold):
    b, e, y, d = _one()
    r1 = sim.simulate(b, y, 50, 5.0, exit_pct=e, stop_loss_pct=10, min_hold_days=hold)
    rk = sim.simulate_topk(b, y, d, ["X"] * len(b), 50, 1, 5.0, exit_pct=e, stop_loss_pct=10, min_hold_days=hold)
    np.testing.assert_array_equal(r1["pos"], rk["pos"])
    assert r1["trades"] == rk["trades"]
    plain = sim.simulate(b, y, 50, 5.0, exit_pct=e, min_hold_days=hold)
    assert not np.array_equal(plain["pos"], r1["pos"])          # 損切りが実際に効いている（沈黙していない）


def _mix(**extra):
    return {"mix": [{"name": "Rz", "combine": "roles", "min_hold_days": 10,
                     "members": [{"method": "A", "role": "buy"}, {"method": "B", "role": "exit"}], **extra}]}


def test_mix_specs_entry_stop():
    assert "entry_stop_pct" not in run.mix_specs(_mix())[0]
    assert run.mix_specs(_mix(entry_stop_pct=10))[0]["entry_stop_pct"] == 10.0
    for bad in (0, 100, -5, True, "10"):
        with pytest.raises(SystemExit):
            run.mix_specs(_mix(entry_stop_pct=bad))
    with pytest.raises(SystemExit):
        run.mix_specs({"mix": [{"name": "Mz", "combine": "mean", "entry_stop_pct": 10,
                                "members": [{"method": "A"}, {"method": "B"}]}]})
