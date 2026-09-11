"""検証の画面（/experiments）。

⚠ **スコアは 1 つの数字なので、取り違えると順位がそのまま嘘になる。**
⚠ **とくに (1) 基準線をスコアにしない (2) 先読みの実行を一覧に混ぜない、の 2 つを検査する。**
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import experiments as exp
from app.main import create_app
from tests.conftest import write_experiment

POOL = {"name": "own_only_h1", "dataset": "daily", "horizon": 1, "cost_bp": 5.0,
        "bar_minutes": 1440.0, "feature_layers": ["own"], "k": 8}
CROSS = {**POOL, "name": "cross_section_h1", "feature_layers": ["own", "cs", "rel", "ll"], "k": 32}
MIN1 = {**POOL, "name": "min1", "dataset": "min1", "horizon": 390, "bar_minutes": 1.0}


def rows(best_net=2.30):
    return [("F1-2 相互情報量", 32.0, 0.5076, 0.0519, best_net + 5.0, best_net),
            ("基準 常に上（ドリフト）", 0.0, 0.5166, 0.0, 9.9, 9.9),
            ("全部使う（基準）", 134.0, 0.4994, 0.0298, 2.2, -2.8)]


# --- タイトルと種類 -----------------------------------------------------

@pytest.mark.parametrize("config,layer,want", [
    (POOL, "adjusted", "プーリング・日足・1 日先・調整後"),
    (POOL, "raw", "プーリング・日足・1 日先・調整前"),
    (CROSS, "adjusted", "断面・日足・1 日先・調整後"),
    (MIN1, "raw", "プーリング・1 分足・1 取引日先・調整前"),
])
def test_title_is_built_from_the_config(config, layer, want):
    """⚠ **タイトルは手で付けない。** 設定から組み立てるので実行のたびにずれない。"""
    assert exp.title_of(config, {"layer": layer}, leak=False) == want


def test_cross_section_is_not_confused_with_pooling():
    """⚠ **`cs` `rel` `ll` が 1 つでもあれば断面。** 取り違えると比較の意味が消える。"""
    assert exp.kind_of(POOL, leak=False) == "プーリング"
    assert exp.kind_of(CROSS, leak=False) == "断面"
    assert exp.kind_of({**POOL, "feature_layers": ["own", "ll"]}, leak=False) == "断面"
    assert exp.kind_of(CROSS, leak=True) == "先読みの検査"


def test_leak_title_keeps_the_experiment_it_controls():
    """⚠ **「先読みの検査」だけでは、断面のものかプーリングのものか分からない。**"""
    assert exp.title_of(CROSS, {"layer": "adjusted"}, leak=True) == "断面・日足・1 日先・調整後（先読みの検査）"
    assert exp.title_of(POOL, {"layer": "adjusted"}, leak=True) == "プーリング・日足・1 日先・調整後（先読みの検査）"
    assert exp.kind_of(CROSS, leak=True) == "先読みの検査"


# --- スコア -------------------------------------------------------------

def test_score_is_the_best_non_baseline_net(tmp_path):
    """⚠ **「常に上」の 9.9 がスコアになってはいけない。**"""
    write_experiment(tmp_path, "2026-09-08T10-00-00_cross_section_h1", config=CROSS,
                     inputs={"layer": "adjusted", "features": 134},
                     summary=rows(), checks={"best": {"method": "F1-2 相互情報量", "純利bp": 2.30,
                                                      "粗利bp": 7.30, "的中率": 0.5076,
                                                      "IC": 0.0519, "本数": 32.0}})
    ex = exp.index(tmp_path)
    assert ex["runs"][0]["score"] == 2.30
    assert ex["runs"][0]["best"]["method"] == "F1-2 相互情報量"


def test_runs_are_sorted_by_score(tmp_path):
    for i, net in enumerate([-1.42, 2.30, -6.20]):
        write_experiment(tmp_path, f"2026-09-08T1{i}-00-00_own_only_h1", config=POOL,
                         inputs={"layer": "adjusted"}, summary=rows(net),
                         checks={"best": {"method": "F1-2 相互情報量", "純利bp": net,
                                          "粗利bp": net + 5, "的中率": 0.5, "IC": 0.01,
                                          "本数": 8.0}})
    scores = [r["score"] for r in exp.index(tmp_path)["runs"]]
    assert scores == sorted(scores, reverse=True) == [2.30, -1.42, -6.20]


def test_leak_runs_are_kept_out_of_the_ranking(tmp_path):
    """⚠ **的中率 99% の検証がスコア 1 位に出ると、一覧全体が嘘になる。**"""
    write_experiment(tmp_path, "2026-09-08T10-00-00_cross_section_h1", config=CROSS,
                     inputs={"layer": "adjusted"}, summary=rows(2.30),
                     checks={"best": {"method": "F1-2 相互情報量", "純利bp": 2.30, "粗利bp": 7.3,
                                      "的中率": 0.5076, "IC": 0.05, "本数": 32.0}})
    write_experiment(tmp_path, "2026-09-08T11-00-00_cross_section_h1_leak", config=CROSS,
                     inputs={"layer": "adjusted"}, summary=rows(136.71),
                     checks={"leak": True,
                             "best": {"method": "F1-1 相関", "純利bp": 136.71, "粗利bp": 141.7,
                                      "的中率": 0.9973, "IC": 1.0, "本数": 32.0}})
    ex = exp.index(tmp_path)
    assert [r["run_id"] for r in ex["runs"]] == ["2026-09-08T10-00-00_cross_section_h1"]
    assert len(ex["leak_runs"]) == 1 and ex["leak_runs"][0]["leak"] is True
    assert ex["total"] == 1


# --- 検査の印 -----------------------------------------------------------

def test_marks_flag_split_folds_and_raw_daily(tmp_path):
    """⚠ **純利が正でも fold が割れていれば ✅ にしない。**"""
    write_experiment(tmp_path, "2026-09-08T10-00-00_cross_section_h1", config=CROSS,
                     inputs={"layer": "raw"}, summary=rows(2.30),
                     checks={"best": {"method": "F1-2 相互情報量", "純利bp": 2.30, "粗利bp": 7.3,
                                      "的中率": 0.5, "IC": 0.05, "本数": 32.0},
                             "folds": {"positive": 2, "folds": 5, "pattern": "＋−−＋−",
                                       "values": [19.5, -5.0, -1.5, 0.7, -2.2]},
                             "edge_vs_drift": {"t": 0.71, "mean_bp": 2.55, "positive": 3,
                                               "folds": 5, "pattern": "＋＋−＋−", "values": []},
                             "dsr": {"DSR": 0.9276, "n_trials": 36, "n_obs": 10870}})
    r = exp.index(tmp_path)["runs"][0]
    assert r["marks"]["純利"] == "✅"        # 純利は正
    assert r["marks"]["fold"] == "⚠"        # ⚠ だが fold は割れている
    assert r["marks"]["上乗せ"] == "⚠"      # t = 0.71 は 3.0 に届かない
    assert r["marks"]["DSR"] == "⚠"         # 0.9276 は 0.95 に届かない
    assert r["marks"]["層"] == "⚠"          # ⚠ 日足 × raw は無効


def test_missing_checks_are_reported_not_guessed(tmp_path):
    """⚠ **検査が無い実行を「0」や「✅」で埋めない。**"""
    write_experiment(tmp_path, "2026-09-08T10-00-00_own_only_h1", config=POOL,
                     inputs={"layer": "adjusted"}, summary=rows(1.0), checks=None)
    ex = exp.index(tmp_path)
    r = ex["runs"][0]
    assert r["score"] is None and r["marks"]["fold"] == "⏳" and r["marks"]["DSR"] == "⏳"
    assert ex["missing_checks"] == ["2026-09-08T10-00-00_own_only_h1"]


# --- 画面 ---------------------------------------------------------------

def test_page_renders_with_no_runs_at_all(settings):
    """⚠ **`runs/` は git 管理外。** 別環境で空でも落とさない。"""
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as c:
        r = c.get("/experiments")
        assert r.status_code == 200 and "検証の記録が無い" in r.text


def test_page_and_detail_render(settings):
    write_experiment(settings.runs_dir, "2026-09-08T10-00-00_cross_section_h1", config=CROSS,
                     inputs={"layer": "adjusted", "features": 134, "symbols": 48,
                             "rows_before_sample": 96769},
                     summary=rows(2.30),
                     checks={"best": {"method": "F1-2 相互情報量", "純利bp": 2.30, "粗利bp": 7.3,
                                      "的中率": 0.5076, "IC": 0.0519, "本数": 32.0},
                             "folds": {"positive": 2, "folds": 5, "pattern": "＋−−＋−",
                                       "values": [19.5, -5.0, -1.5, 0.7, -2.2]},
                             "edge_vs_drift": {"t": 0.71, "mean_bp": 2.55, "positive": 3,
                                               "folds": 5, "pattern": "＋＋−＋−",
                                               "values": [15.8, 1.5, -2.9, 3.0, -4.5]},
                             "breadth": {"系列数": 48, "実効系列数": 6.47, "t値の割引": 0.367,
                                         "パネルの時刻": 1901, "検証の行": 80641,
                                         "実効観測数": 10870},
                             "drift_粗利bp": 4.7489,
                             "dsr": {"DSR": 0.9276, "n_trials": 36, "n_obs": 10870}})
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as c:
        r = c.get("/experiments")
        assert r.status_code == 200
        assert "断面・日足・1 日先・調整後" in r.text and "+2.30" in r.text
        assert "2/5" in r.text                      # fold の符号が出ている
        d = c.get("/experiments/2026-09-08T10-00-00_cross_section_h1")
        assert d.status_code == 200 and "6.47" in d.text and "0.9276" in d.text
        # ⚠ 上乗せの相手（「常に上」自身の粗利）も出す。無いと上乗せの意味が読めない
        assert "「常に上」自身の粗利 +4.75bp" in d.text
        j = c.get("/api/experiments")
        assert j.status_code == 200 and j.json()["runs"][0]["score"] == 2.30


def test_unknown_and_traversing_run_ids_are_404(settings):
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as c:
        assert c.get("/experiments/nope").status_code == 404
        assert c.get("/experiments/..%2F..%2Fetc").status_code in (404, 400)


def test_one_rejects_path_traversal(tmp_path):
    assert exp.one(tmp_path, "../secrets") is None
    assert exp.one(tmp_path, ".hidden") is None
    assert exp.one(tmp_path, "") is None


def test_demo_banner_says_the_validation_screen_is_not_mock(settings):
    """⚠ **デモの帯は「モックのデータ」と書く。** 検証の数字は本物なので、そのままだと嘘になる。"""
    settings.demo = True
    write_experiment(settings.runs_dir, "2026-09-08T10-00-00_cross_section_h1", config=CROSS,
                     inputs={"layer": "adjusted"}, summary=rows(2.30),
                     checks={"best": {"method": "F1-2 相互情報量", "純利bp": 2.30, "粗利bp": 7.3,
                                      "的中率": 0.5, "IC": 0.05, "本数": 32.0}})
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as c:
        e = c.get("/experiments").text
        assert "デモ" in e and "この「検証」の画面はデモの対象外" in e
        assert str(settings.runs_dir) in e          # 出所は runs/ を出す
        # ⚠ 他の画面では今までどおり（余計な断りを出さない）
        assert "この「検証」の画面はデモの対象外" not in c.get("/records").text


# --- 閾値つき売買（rules.md 13 章） --------------------------------------

import json  # noqa: E402

TRADE = {**POOL, "name": "trade_own_ridge_a", "model": "Ridge",
         "trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"}}
TRADE_B = {**TRADE, "trading": {**TRADE["trading"], "form": "per_symbol"}}


def _th_entry(th, net):
    return {"best": {"method": "全部使う（基準）", "純利bp": net, "粗利bp": net + 3.0,
                     "的中率": 0.52, "本数": 35.0, "取引回数": 60.0, "保有日率": 0.5},
            "edge_vs_bh": {"pattern": "＋＋＋＋＋", "positive": 5, "folds": 5,
                           "values": [1.0, 2.0, 0.5, 0.8, 0.7], "mean_bp": 1.0, "t": 3.5},
            "bh_純利bp": net - 1.0,
            "dsr": {"SR": 0.02, "SR0": 0.01, "DSR": 0.97, "n_trials": 91, "n_obs": 1800,
                    "歪度": -0.3, "尖度": 5.2},
            "per_symbol": {"銘柄数": 63, "中央値bp": 3.2, "四分位bp": [-5.0, 12.0],
                           "勝ち銘柄": 40}}


def trade_checks():
    by = {"50": _th_entry(50, 10.0), "55": _th_entry(55, 12.0), "60": _th_entry(60, 8.0)}
    return {"leak": False, "style": "threshold", "form": "shared", "cost_bp": 5.0,
            "thresholds": [50.0, 55.0, 60.0], "by_threshold": by,
            "best": {**by["55"]["best"], "閾値": 55.0},
            "folds": {k: by["55"]["edge_vs_bh"][k]
                      for k in ("pattern", "positive", "folds", "values")},
            "edge_vs_bh": by["55"]["edge_vs_bh"], "bh_純利bp": 11.0,
            "dsr": by["55"]["dsr"], "per_symbol": by["55"]["per_symbol"]}


def write_trading_experiment(runs_dir, run_id, config=None, checks_doc=None):
    d = write_experiment(runs_dir, run_id, config=config or TRADE,
                         inputs={"layer": "adjusted", "features": 35, "symbols": 63,
                                 "rows_before_sample": 135962},
                         summary=[], checks=checks_doc or trade_checks())
    lines = ["手法,閾値,本数,的中率,IC,粗利bp,純利bp,取引回数,保有日率,fold数"]
    for th, net in ((50.0, 10.0), (55.0, 12.0), (60.0, 8.0)):
        lines.append(f"全部使う（基準）,{th},35.0,0.52,0.03,{net + 3.0},{net},60.0,0.5,5")
        lines.append(f"基準 常に上（ドリフト）,{th},0.0,0.52,0.0,{net + 4.0},{net - 1.0},63.0,1.0,5")
    (d / "summary.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def test_trading_title_names_the_style_and_form():
    """閾値売買の実行はタイトルで見分けられる（形式 (A) 共通 / (B) 銘柄別も）。"""
    assert exp.title_of(TRADE, {"layer": "adjusted"}, leak=False) \
        == "プーリング・日足・1 日先・調整後・閾値売買・共通"
    assert exp.title_of(TRADE_B, {"layer": "adjusted"}, leak=False) \
        == "プーリング・日足・1 日先・調整後・閾値売買・銘柄別"


def test_trading_edge_comes_from_edge_vs_bh(tmp_path):
    """⚠ **新方式の上乗せは対 B&H**（edge_vs_bh）。旧鍵（edge_vs_drift）が無くても読める。"""
    write_trading_experiment(tmp_path, "2026-09-10T10-00-00_trade_own_ridge_a")
    r = exp.index(tmp_path)["runs"][0]
    assert r["style"] == "threshold" and r["form"] == "shared"
    assert r["edge"]["t"] == 3.5
    assert r["score"] == 12.0 and r["best"]["閾値"] == 55.0
    assert set(r["by_threshold"]) == {"50", "55", "60"}
    assert r["marks"]["上乗せ"] == "✅" and r["marks"]["DSR"] == "✅" and r["marks"]["fold"] == "✅"


def test_trading_summary_keeps_the_threshold_columns(tmp_path):
    write_trading_experiment(tmp_path, "2026-09-10T10-00-00_trade_own_ridge_a")
    r = exp.index(tmp_path)["runs"][0]
    row = r["summary"][0]
    assert row["閾値"] == 50.0 and row["取引回数"] == 60.0 and row["保有日率"] == 0.5


def test_trading_detail_page_shows_all_three_thresholds(settings):
    """⚠ **3 閾値とも画面に出す**（rules.md 13-3。checks.json の写しを出すだけ）。"""
    write_trading_experiment(settings.runs_dir, "2026-09-10T10-00-00_trade_own_ridge_a")
    with TestClient(create_app(settings), client=("127.0.0.1", 50000)) as c:
        r = c.get("/experiments")
        assert r.status_code == 200 and "閾値売買・共通" in r.text
        d = c.get("/experiments/2026-09-10T10-00-00_trade_own_ridge_a")
        assert d.status_code == 200
        assert "閾値ごとの成績" in d.text
        for th in ("50%", "55%", "60%"):
            assert th in d.text
        assert "対 B&amp;H の上乗せ" in d.text
        assert "40/63" in d.text                     # 銘柄別の勝ち銘柄（成果物の要約）
        assert "θ=55%" in d.text                     # 最良の閾値
