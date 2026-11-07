"""fold の内側の並べ替え検定（cli.perm_test）と検証結果一覧の列 2 本（cli.ledger.attach_perm）。

⚠ 見るもの: 入れ替えが銘柄ごとの値の集まりを保つ・全銘柄に同じ日付・塊の中の並びを保つ ／ 分位と Fisher の p ／
   列はキャッシュが無ければ「—」・上乗せが合わない行は埋めない・判定と n_trials は変わらない（DB に書かない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from cli import ledger
from cli import perm_test as pt


def _frame(n_days=23, syms=("A", "B", "C"), drop=None):
    ts = pd.date_range("2021-01-04", periods=n_days, freq="B", tz="UTC")
    rows = []
    for j, s in enumerate(syms):
        for i, t in enumerate(ts):
            if drop and (s, i) in drop:
                continue
            rows.append({"fold": 1, "ts": t, "symbol": s, "買い%|M": 100.0 * j + i})
    return pd.DataFrame(rows)


def test_block_order_keeps_blocks_and_is_a_permutation():
    rng = np.random.default_rng(0)
    o = pt.block_order(23, 5, rng)
    assert sorted(o) == list(range(23))
    assert int((np.diff(o) != 1).sum()) <= 4                  # 塊 5 つ ＝ 切れ目は 4 か所まで
    assert (pt.N, pt.BLOCK, pt.SEED) == (200, 5, 0)            # ⚠ 事前固定（登録簿）


def test_same_date_shuffle_for_all_symbols_keeps_each_symbols_values():
    fr = _frame()
    fi = pt.FoldIndex(fr, np.arange(len(fr)))
    order = pt.block_order(len(fi.days), 5, np.random.default_rng(3))
    src, miss = fi.source(order)
    v = fr["買い%|M"].to_numpy()[src]
    out = fr.assign(v=v)
    assert miss == 0
    for s, g in out.groupby("symbol"):
        assert sorted(g["v"]) == sorted(g["買い%|M"])           # 銘柄ごとの値の集まりは同じ
    a = out[out.symbol == "A"]["v"].to_numpy()
    b = out[out.symbol == "B"]["v"].to_numpy()
    assert np.array_equal(b - a, np.full(len(a), 100.0))       # 全銘柄に同じ日付の入れ替え
    assert not np.array_equal(a, np.arange(23.0))


def test_missing_target_rows_keep_their_own_value():
    fr = _frame(drop={("C", 0)})
    fi = pt.FoldIndex(fr, np.arange(len(fr)))
    order = np.r_[np.arange(5, 23), np.arange(5)]              # 日 0 を借りる行が出る
    src, miss = fi.source(order)
    assert miss == 1
    pos = fr.index[(fr.symbol == "C") & (fr.ts == fi.days[18])][0]
    assert src[pos] == pos


def test_fisher_and_ledger_columns_from_cache_only():
    assert pt.fisher_p([1.0, 1.0]) == pytest.approx(1.0)
    assert pt.fisher_p([0.01, 0.01, 0.01]) < 0.001
    doc = {"rows": [{"method": "M1", "theta": 50.0, "reproduced": True, "edge_bp": 12.5,
                     "q_by_fold": {"1": 0.62, "2": 0.48, "10": 0.9}, "fisher_p": 0.2},
                    {"method": "M2", "theta": 50.0, "reproduced": False}]}
    d = {"rows": [
        {"実行": "R", "手法名": "M1", "閾値": "50", "上乗せbp": 12.5, "判定": "落とす"},
        {"実行": "R", "手法名": "M1", "閾値": "55", "上乗せbp": 1.0, "判定": "落とす"},      # θ が無い
        {"実行": "R", "手法名": "M2", "閾値": "50", "上乗せbp": 1.0, "判定": "落とす"},      # 再現せず
        {"実行": "R", "手法名": "M1", "閾値": "50", "上乗せbp": 99.0, "判定": "落とす"},     # 上乗せが合わない
        {"実行": "S", "手法名": "M1", "閾値": "50", "上乗せbp": 12.5, "判定": "落とす"},     # キャッシュなし
        {"実行": "R", "手法名": "M1", "閾値": "50", "上乗せbp": 12.5, "判定": "基準"},       # 基準線
    ]}
    n = ledger.attach_perm(d, reader=lambda run: doc if run == "R" else None)
    assert n == 1
    assert d["rows"][0][ledger.PERM_Q] == "62/48/90" and d["rows"][0][ledger.PERM_P] == 0.2
    assert all(r[ledger.PERM_Q] is None and r[ledger.PERM_P] is None for r in d["rows"][1:])
    assert [r["判定"] for r in d["rows"]] == ["落とす"] * 5 + ["基準"]                   # 判定は触らない
    assert ledger._p(None) == "—" and ledger._p(0.0004) == "<0.001" and ledger._p(0.2) == "0.200"
