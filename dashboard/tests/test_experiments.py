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
