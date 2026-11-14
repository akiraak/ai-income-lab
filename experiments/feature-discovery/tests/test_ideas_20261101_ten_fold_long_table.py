"""評価期間を 10 個に切る閾値売買の変種（開発ループのレジストリ `20261101-ten-fold-long-table`。2026-11-13・サンドボックス）。

⚠ **`[validation] folds` が 10 でない config は 1 ビットも変わらない**（検証方式の名前・切れ目・`checks.json` の項目）。
変種 ＝ 検証方式 `閾値売買（評価期間10）`・予測モデル名 `~fold10`。判定の式は同じ（上乗せ > 0 かつ全部の評価期間で正）。
正の評価期間の数の二項検定（p = 0.5）は診断として `by_threshold.<θ>.sign_test` に置く（採否には使わない）。
"""

from __future__ import annotations

import copy

import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import catalog, config, names, runs
from ail.validation import checks, splits
from cli.run import _checks_line_trading, evaluate_trading
from tests import _fingerprint as fp

BH = "基準 常に上（ドリフト）"
ALL = "全部使う（基準）"
RAND = "乱択（基準）"
STYLE = "閾値売買（評価期間10）"
NAME = "dev_fold10_trade_own_1995_ridge_a"
SRC = "trade_own_1995_ridge_a"


def _days(panel):
    return pd.Series(pd.to_datetime(panel["ts"]).unique()).sort_values().reset_index(drop=True)


def _feats(p):
    return [c for c in p.columns if c not in ("symbol", "ts", "y")]


def _exp(folds=None):
    e = fp.experiment()
    e["trading"]["thresholds"] = [50]
    if folds is not None:
        e["validation"]["folds"] = folds
    return e


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_ten_fold", {}, seed=0)


# --- ① オプションの無い config は同じ値 -----------------------------------------------------------

@pytest.mark.parametrize("v", [{}, {"folds": 5}, {"folds": 3}, {"folds": 7}, {"folds": 20}, {"folds": "10"},
                               {"folds": True}, {"folds": None}, {"folds": 10.5}])
def test_other_fold_counts_keep_the_plain_style(v):
    cfg = {"trading": {"style": "threshold"}, "validation": v}
    assert catalog.fold_variant(cfg) is None
    assert catalog.threshold_style(cfg) == "閾値売買"                 # 既存の行は割れない
    assert catalog.threshold_style({"trading": {"style": "threshold"}}) == "閾値売買"   # [validation] が無い config


def test_level_is_only_ten():
    assert catalog.FOLD_VARIANTS == (10,)                             # ⚠ 事前固定。足すなら新しい id で先に登録
    assert catalog.fold_variant({"validation": {"folds": 10}}) == 10
    assert catalog.fold_variant({"validation": {"folds": 10.0}}) == 10   # 実行は int() で読む ＝ 同じ 10 個


def test_default_checks_have_no_sign_test(run):
    """folds = 5（既定の経路）では診断の項目ごと書かない ＝ checks.json とログの行は今までと同じ。"""
    p = fp.panel()
    e = _exp()
    res, per_sym, g, daily, extra = evaluate_trading(p, _feats(p), e, run)
    assert sorted(res["fold"].unique()) == [1, 2, 3, 4, 5]
    doc = checks.compute_trading(res, g, per_sym, daily, e, n_trials=10, extra=extra)
    for entry in doc["by_threshold"].values():
        assert "sign_test" not in entry
    assert "sign_test" not in doc and "二項" not in _checks_line_trading(doc)


# --- ② 新しい処置の中身 ---------------------------------------------------------------------------

def test_ten_folds_cut_the_dates_into_eleven_groups():
    p = fp.panel(n_days=1600)
    days = _days(p)
    edges = splits.date_edges(p["ts"], 10)
    assert len(edges) == 12 and edges[0] == days.iloc[0] and edges[-1] > days.iloc[-1]
    assert all(a < b for a, b in zip(edges, edges[1:]))
    folds = list(splits.folds_by_dates(p, edges, 1440.0))
    assert [f for f, _, _ in folds] == list(range(1, 11))
    te_days = pd.concat([pd.Series(pd.to_datetime(te["ts"]).unique()) for _, _, te in folds])
    assert te_days.is_unique and set(te_days) == set(days[days >= edges[1]])   # 重ならず隙間も無い
    for f, tr, te in folds:
        # パージ（ラベルの地平 1 日）: 訓練の最後の日は評価期間の初日より 1 日以上前
        assert pd.to_datetime(tr["ts"]).max() < pd.to_datetime(te["ts"]).min() - pd.Timedelta(minutes=1440.0) + pd.Timedelta(1, "ns")
        assert pd.to_datetime(te["ts"]).min() == edges[f]
    sizes = [len(pd.to_datetime(te["ts"]).unique()) for _, _, te in folds]
    assert max(sizes) - min(sizes) <= 1                                # 日付の数でほぼ等分


def test_trading_runs_on_ten_folds_and_writes_the_sign_test(run):
    p = fp.panel(n_days=1600)
    days = _days(p)
    e = _exp(10)
    res, per_sym, g, daily, extra = evaluate_trading(p, _feats(p), e, run)
    assert sorted(res["fold"].unique()) == list(range(1, 11))
    edges = splits.date_edges(p["ts"], 10)
    want = [int(((days >= edges[f]) & (days < edges[f + 1])).sum()) for f in range(1, 11)]
    n = lambda m: list(res[res["手法"] == m].sort_values("fold")["検証日数"])
    assert n(BH) == want and n(ALL) == want                            # 基準線も手法も同じ 10 個の評価期間
    doc = checks.compute_trading(res, g, per_sym, daily, e, n_trials=10, extra=extra)
    entry = doc["by_threshold"]["50"]
    ed, st = entry["edge_vs_bh"], entry["sign_test"]
    assert ed["folds"] == 10 and len(ed["pattern"]) == 10
    assert st == checks.sign_test(ed["positive"], 10)                  # 正の数は上乗せ（対 B&H）の符号から
    line = _checks_line_trading(doc)
    assert f"符号 {ed['positive']}/10" in line and "二項 p" in line and "診断" in line


def test_sign_test_values():
    a = checks.sign_test(10, 10)
    assert a["positive"] == 10 and a["folds"] == 10
    assert a["p_upper"] == pytest.approx(1 / 1024, abs=1e-6)          # 10 個とも正は偶然なら 1/1024
    assert a["p_two_sided"] == pytest.approx(2 / 1024, abs=1e-6)
    assert checks.sign_test(5, 5)["p_upper"] == pytest.approx(1 / 32, abs=1e-6)   # 5 つとも正は 1/32
    b = checks.sign_test(5, 10)
    assert b["p_upper"] == pytest.approx(638 / 1024, abs=1e-6) and b["p_two_sided"] == 1.0
    c = checks.sign_test(8, 10)
    assert c["p_upper"] == pytest.approx(56 / 1024, abs=1e-6) and c["p_two_sided"] == pytest.approx(112 / 1024, abs=1e-6)
    z = checks.sign_test(0, 10)
    assert z["p_upper"] == 1.0 and z["p_two_sided"] == pytest.approx(2 / 1024, abs=1e-6)
    assert "採否には使わない" in a["注記"]
    for bad in ((11, 10), (-1, 10), (0, 0)):
        with pytest.raises(ValueError):
            checks.sign_test(*bad)


def test_judgement_is_the_same_formula_on_ten_folds():
    """判定の式は同じ（上乗せ > 0 かつ全部の評価期間で正）。二項検定の値は判定に入らない。"""
    row = {"手法名": ALL, "検証方式": STYLE, "粒度": "日足", "層": "adjusted", "上乗せbp": 12.0}
    assert catalog.judge({**row, "上乗せfold": "10/10 " + "＋" * 10}, set())[0] == "採る"
    assert catalog.judge({**row, "上乗せfold": "9/10 " + "＋" * 9 + "−"}, set())[0] == "保留"
    assert catalog.judge({**row, "上乗せbp": -1.0, "上乗せfold": "9/10 " + "＋" * 9 + "−"}, set())[0] == "落とす"


# --- ③ 検証方式の名前と予測モデル名の綴り ---------------------------------------------------------

def test_style_and_name_and_counting():
    cfg = {"trading": {"style": "threshold"}, "validation": {"folds": 10}}
    style = catalog.threshold_style(cfg)
    assert style == STYLE and catalog.is_threshold(style)
    assert catalog.is_trial({"手法名": ALL, "検証方式": style})         # 検証方式が処置 ＝ 全部使うも数える
    assert not catalog.is_trial({"手法名": RAND, "検証方式": style})
    assert not catalog.is_trial({"手法名": BH, "検証方式": style})
    key = dict(鍵=ALL, モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own", 層="adjusted",
               期間="1995-02-10", 銘柄="63", 検証方式=style, 形式="共通", 較正="std", 閾値="50")
    assert names.trial_name(key) == "own.all.ridge.shared~p1995-02-10~fold10@50"
    assert names.model_name(key) == "own.all.ridge.shared~p1995-02-10~fold10"
    assert names.trial_name({**key, "検証方式": "閾値売買"}) == "own.all.ridge.shared~p1995-02-10@50"   # 5 つの行の名前は同じ
    # ほかの変種とは `・` でつなぐ（評価期間の数が先頭）
    both = catalog.threshold_style({"trading": {"style": "threshold", "execution": "next_open"},
                                    "validation": {"folds": 10, "max_train_days": 2520, "cut_offset": "1/3"}})
    assert both == "閾値売買（評価期間10・訓練直近2520日・切れ目ずらし1/3・翌日始値）"
    assert names.trial_name({**key, "検証方式": both}).endswith("~fold10~train2520~cut1of3~exec-open@50")
    with pytest.raises(SystemExit):                                    # 綴りの無い数は名前が付かない
        names.trial_name({**key, "検証方式": "閾値売買（評価期間7）"})


def test_experiment_config_differs_only_by_name_and_folds():
    a, b = copy.deepcopy(config.experiment(SRC)), copy.deepcopy(config.experiment(NAME))
    assert a.pop("name") == SRC and b.pop("name") == NAME
    assert a["validation"].pop("folds") == 5 and b["validation"].pop("folds") == 10
    assert a == b                                                      # 表・特徴量・モデル・θ・コストは同じ
    full = config.experiment(NAME)
    style = catalog.threshold_style(full)
    assert style == STYLE and full["features_from"] == "own_1995"
    counted = [s for s in full["selectors"] if catalog.is_trial({"手法名": s, "検証方式": style})]
    assert counted == [ALL] and full["trading"]["thresholds"] == [50, 55, 60]   # 数える検証 ＝ 1 × 3 ＝ 3（n_trials_planned）
