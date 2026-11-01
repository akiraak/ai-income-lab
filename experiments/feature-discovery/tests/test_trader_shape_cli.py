"""`cli.trader_shape` ＝ 検証結果一覧の行から rules.md 20 章の config を機械で書く（howto §3-2 の手作業の写し）。

⚠ 既存の `trader_*.toml`（手で写したもの）と**意味が同じ**（tomllib で読んで等しい）＝ 新しい規約ではない。
"""

from __future__ import annotations

import os
import tomllib

import pytest

import ail.bootstrap  # noqa: F401
from ail import rundb, runs
from cli import trader_shape as ts

# （元の実験, 手法, θ, 2a の予算, 手で写した既存の config）。予算 $300 ＝ 2026-10-02 までの形
CASES = [
    ("trade_own_ridge_a", ["全部使う（基準）"], "50", 300, "trader_trade_own_ridge_a"),
    ("trade_ownex_lgbm_a", ["全部使う（基準）"], "55", 300, "trader_trade_ownex_lgbm_a"),
    ("trade_ownseq_ridge_a", ["T3 QUANT（60日窓）"], "50", 300, "trader_trade_ownseq_ridge_a"),      # 表の持ち主 ＝ features_from を足す
    ("trade_own_stop_ridge_a", ["L1 高値20日から−10%で降りる"], "50", 300, "trader_trade_own_stop_ridge_a"),   # position_exits を外す
    ("sel_small4_1995", ["F2-2 RFE", "F1-7 IC の安定性"], "50", 300, "trader_sel_small4_1995"),       # 2 本を 1 つに
]


@pytest.mark.parametrize("src,methods,theta,budget,want", CASES)
def test_matches_the_hand_written_config(src, methods, theta, budget, want):
    text = ts.shape_config(ts.source_text(src), src=src, methods=methods, theta=theta, budget=budget)
    assert tomllib.loads(text) == tomllib.loads(ts.source_text(want))


def test_default_budget_is_600_and_tags_are_spelled(tmp_path):
    text = ts.shape_config(ts.source_text("trade_own_ridge_a"), src="trade_own_ridge_a", methods=["全部使う（基準）"], theta="50")
    doc = tomllib.loads(text)
    conds = doc["trading"]["trader"]
    assert [c["tag"] for c in conds] == ["5本・$600", ts.TAG_ALL]
    assert conds[0]["symbols"] == ts.SYMBOLS and conds[0]["budget_usd"] == 600 and "methods" not in conds[0]
    assert doc["name"] == "trader_trade_own_ridge_a" and doc["trading"]["thresholds"] == [50]
    assert doc["features_from"] == "own_2018"                      # 元にあるものはそのまま


def test_mix_keeps_members_and_applies_conditions_to_the_mix_only():
    text = ts.shape_config(ts.source_text("trader_mix_t4"), src="trader_mix_t4", methods=["M1 3本の平均"], theta="50", name="x")
    doc = tomllib.loads(text)
    assert [m["name"] for m in doc["trading"]["mix"]] == ["M1 3本の平均"]
    assert doc["selectors"] == ["全部使う（基準）"]                   # メンバーが要るので絞らない
    assert all(c["methods"] == ["M1 3本の平均"] for c in doc["trading"]["trader"]) and len(doc["trading"]["trader"]) == 2


def test_refuses_unknown_method_mixed_kinds_bad_tag_and_non_threshold():
    with pytest.raises(SystemExit):
        ts.shape_config(ts.source_text("trade_own_ridge_a"), src="trade_own_ridge_a", methods=["無い手法"], theta="50")
    with pytest.raises(SystemExit):
        ts.shape_config(ts.source_text("trade_own_ridge_a"), src="trade_own_ridge_a", methods=["全部使う（基準）"], theta="50", budget=123)
    with pytest.raises(SystemExit):
        ts.shape_config(ts.source_text("own_2018"), src="own_2018", methods=["全部使う（基準）"], theta="50")


def test_main_writes_once_and_refuses_to_overwrite(tmp_path, capsys):
    out = tmp_path / "t.toml"
    ts.main(["--experiment", "trade_own_ridge_a", "--method", "全部使う（基準）", "--theta", "50", "--out", str(out)])
    assert tomllib.loads(out.read_text(encoding="utf-8"))["name"] == "trader_trade_own_ridge_a"
    with pytest.raises(SystemExit):
        ts.main(["--experiment", "trade_own_ridge_a", "--method", "全部使う（基準）", "--theta", "50", "--out", str(out)])
    ts.main(["--experiment", "trade_own_ridge_a", "--method", "全部使う（基準）", "--theta", "50", "--out", str(out), "--force"])
    with pytest.raises(SystemExit):
        ts.main(["--from", "x@50", "--experiment", "trade_own_ridge_a"])


# --- 本物の DB（あれば）: 検証名 → 元の実験 -----------------------------------------------------------

def _real_db():
    p = runs.db_path()
    if not os.path.exists(p):
        pytest.skip("研究の DB が無い")
    return rundb.connect(p, readonly=True)


def test_resolve_trial_prefers_the_run_without_trader_shape():
    c = _real_db()
    try:
        got = ts.resolve_trial(c, "own.all.ridge.shared@50")
        assert (got["experiment"], got["method"], got["theta"]) == ("trade_own_ridge_a", "全部使う（基準）", "50")
        got = ts.resolve_trial(c, "own.f1-7.ridge.shared~p1995-02-10@50")
        assert (got["experiment"], got["method"], got["theta"]) == ("sel_small4_1995", "F1-7 IC の安定性", "50")
        with pytest.raises(SystemExit):
            ts.resolve_trial(c, "own.f1-1.ridge.shared~rt~raw~p-na~n-na")      # 毎日往復の行
        with pytest.raises(SystemExit):
            ts.resolve_trial(c, "no.such.row@50")
    finally:
        c.close()


def test_from_two_rows_of_the_same_experiment_and_theta(tmp_path, capsys):
    _real_db().close()
    out = tmp_path / "t.toml"
    ts.main(["--from", "own.f2-2.ridge.shared~p1995-02-10@50", "--from", "own.f1-7.ridge.shared~p1995-02-10@50",
             "--budget", "300", "--out", str(out)])
    assert tomllib.loads(out.read_text(encoding="utf-8")) == tomllib.loads(ts.source_text("trader_sel_small4_1995"))
    with pytest.raises(SystemExit):   # θ が違う行は別々に
        ts.main(["--from", "own.f2-2.ridge.shared~p1995-02-10@50", "--from", "own.f2-2.ridge.shared~p1995-02-10@55", "--print"])
