"""vibeboard カスタムタブのサーバ（`dashboard/vibetab.py`）の検査。

一時ディレクトリに最小の run / manifest / config を作って、
sidebar と view が 200 相当の中身を返すこと・知らない item を弾くことを見る。
⚠ 実験の実データ（git 管理外）には依存しない。
"""

import json
import threading
import urllib.request
from pathlib import Path

import pytest

import vibetab


# ---------------------------------------------------------------- 最小のデータ


def make_run(runs_dir: Path, name: str, leak: bool = False, score: float = 1.5) -> Path:
    d = runs_dir / name
    d.mkdir(parents=True)
    (d / "summary.csv").write_text(
        "手法,本数,的中率,IC,粗利bp,純利bp,fold数\n"
        f"全部使う（基準）,36,0.51,0.02,{score + 4.0},{score + 3.0},5\n"
        f"F3-1 Lasso,12,0.52,0.03,{score + 1.0},{score},5\n",
        encoding="utf-8")
    (d / "config.json").write_text(json.dumps(
        {"dataset": "daily", "horizon": 1, "k": 5, "cost_bp": 5.0,
         "feature_layers": ["own", "cs"]}), encoding="utf-8")
    (d / "inputs.json").write_text(json.dumps(
        {"layer": "adjusted", "features": 100, "symbols": 63, "rows_before_sample": 131250}),
        encoding="utf-8")
    (d / "env.json").write_text(json.dumps(
        {"seed": 7, "git_commit": "abc1234", "started_at": "2026-09-09T12:00:00"}),
        encoding="utf-8")
    (d / "checks.json").write_text(json.dumps({
        "leak": leak,
        "best": {"method": "F3-1 Lasso", "純利bp": score},
        "folds": {"positive": 5, "folds": 5},
        "edge_vs_drift": {"t": 3.5},
        "dsr": {"DSR": 0.97},
        "breadth": {"系列数": 63, "実効系列数": 4.7},
        "drift_粗利bp": 0.02,
        "panel": "パネルの注意書き",
    }, ensure_ascii=False), encoding="utf-8")
    return d


@pytest.fixture()
def runs_dir(tmp_path: Path) -> Path:
    runs = tmp_path / "runs"
    make_run(runs, "2026-09-09T12-00-00_own_2018", score=1.5)
    make_run(runs, "2026-09-09T13-00-00_own_2018_leak", leak=True, score=99.0)
    return runs


@pytest.fixture()
def exp_dir(tmp_path: Path) -> Path:
    exp = tmp_path / "exp"
    (exp / "data" / "manifests").mkdir(parents=True)
    (exp / "data" / "manifests" / "adjusted_d.json").write_text(json.dumps({
        "layer": "adjusted", "period": "d", "source": "stooq", "dataset": "daily",
        "symbols": 2, "totals": {"rows": 1000, "gaps": 3},
        "series": {"SPY": {"rows": 500, "oldest_ms": 1514764800000, "newest_ms": 1750000000000},
                   "AAPL": {"rows": 500, "oldest_ms": 1514764800000, "newest_ms": 1750000000000}},
    }), encoding="utf-8")
    (exp / "data" / "manifests" / "exog.json").write_text(json.dumps({
        "layer": "exog", "period": "series", "source": "usgs", "dataset": "impact_2018",
        "symbols": 3, "role": "本命", "totals": {"rows": 300}, "series": {},
    }), encoding="utf-8")
    (exp / "data" / "features" / "own_2018").mkdir(parents=True)
    (exp / "data" / "features" / "own_2018" / "d.meta.json").write_text(json.dumps({
        "experiment": "own_2018", "layer": "adjusted", "period": "d",
        "rows": 131250, "features": 100, "built_at": "2026-09-09"}), encoding="utf-8")
    (exp / "config" / "dataset").mkdir(parents=True)
    (exp / "config" / "dataset" / "impact.toml").write_text(
        'name = "impact_2018"\n[[series]]\nsource = "usgs"\nrole = "本命"\n'
        'hypothesis = "災害は保険と操業停止に効く"\n', encoding="utf-8")
    (exp / "config" / "universe").mkdir(parents=True)
    (exp / "config" / "universe" / "us63.toml").write_text(
        'name = "us63"\nselected_on = "2026-09-01"\n[groups]\ncompany = ["AAPL"]\netf = ["SPY"]\n',
        encoding="utf-8")
    (exp / "config" / "exposure").mkdir(parents=True)
    (exp / "config" / "exposure" / "us63.toml").write_text(
        'name = "us63"\nhindsight = true\n[[channel]]\nname = "hurricane"\n'
        'hypothesis = "保険"\n[weights.hurricane]\nALL = 0.5\n', encoding="utf-8")
    (exp / "config" / "sources.toml").write_text(
        '[usgs]\nlabel = "USGS 地震"\nlag_days = 1\nterms = "公開データ"\n', encoding="utf-8")
    return exp


# ---------------------------------------------------------------- 検証タブ


def test_exp_sidebar_groups_and_badge(runs_dir):
    items = vibetab.exp_sidebar(runs_dir)["items"]
    assert items[0]["id"] == "overview"
    by_id = {i["id"]: i for i in items}
    real = by_id["2026-09-09T12-00-00_own_2018"]
    assert real["group"] == "断面"          # feature_layers に cs があるので断面
    assert real["badge"] == "+1.50bp"
    leak = by_id["2026-09-09T13-00-00_own_2018_leak"]
    assert leak["group"] == "先読みの検査"   # ⚠ 一覧に混ぜない


def test_exp_sidebar_missing_dir(tmp_path):
    items = vibetab.exp_sidebar(tmp_path / "nai")["items"]
    assert [i["id"] for i in items] == ["overview"]


def test_exp_overview_html(runs_dir):
    body = vibetab.exp_overview_html(runs_dir)
    assert "検証のまとめ" in body
    assert "+1.50" in body
    assert "先読みの検査" in body


def test_exp_run_html(runs_dir):
    body = vibetab.exp_run_html(runs_dir, "2026-09-09T12-00-00_own_2018")
    assert body is not None
    assert "F3-1 Lasso" in body            # 最良手法
    assert "3.5" in body                   # 上乗せ t
    assert "パネルの注意書き" in body
    assert "全部使う（基準）" in body       # summary.csv の写し


def test_exp_run_html_rejects_unknown_and_traversal(runs_dir):
    assert vibetab.exp_run_html(runs_dir, "nai") is None
    assert vibetab.exp_run_html(runs_dir, "../secret") is None
    assert vibetab.exp_run_html(runs_dir, "") is None


# ---------------------------------------------------------------- データタブ


def test_data_sidebar_sections(exp_dir):
    items = vibetab.data_sidebar(vibetab.ExpPaths(exp_dir))["items"]
    assert [i["id"] for i in items] == [sec for sec, _ in vibetab.DATA_SECTIONS]
    by_id = {i["id"]: i for i in items}
    assert "1,300" in by_id["overview"]["sub"]   # manifest の rows の合計（写し）


@pytest.mark.parametrize("section", [sec for sec, _ in vibetab.DATA_SECTIONS])
def test_data_sections_render(exp_dir, section):
    body = vibetab.data_section_html(vibetab.ExpPaths(exp_dir), section)
    assert body is not None and body.startswith("<!doctype html>")


def test_data_section_contents(exp_dir):
    paths = vibetab.ExpPaths(exp_dir)
    assert "gaps 3" in vibetab.data_section_html(paths, "bars")          # 検査の引っかかり
    external = vibetab.data_section_html(paths, "external")
    assert "災害は保険と操業停止に効く" in external                       # 仮説は宣言の写し
    assert "USGS 地震" in external
    assert "⚠ あり" in vibetab.data_section_html(paths, "exposures")     # 後知恵
    assert vibetab.data_section_html(paths, "nazo") is None


def test_data_missing_exp_dir(tmp_path):
    body = vibetab.data_section_html(vibetab.ExpPaths(tmp_path / "nai"), "overview")
    assert body is not None and "空" in body


# ---------------------------------------------------------------- HTTP の通し


@pytest.fixture()
def server(runs_dir, exp_dir):
    srv = vibetab.ThreadingHTTPServer(
        ("127.0.0.1", 0), vibetab.make_handler(runs_dir, vibetab.ExpPaths(exp_dir)))
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, ""


def test_http_routes(server):
    assert _get(f"{server}/")[0] == 200                       # sidecar の疎通判定用
    assert _get(f"{server}/experiments")[0] == 200
    status, body = _get(f"{server}/experiments/api/sidebar")
    assert status == 200 and json.loads(body)["items"]
    status, body = _get(f"{server}/experiments/view?item=overview")
    assert status == 200 and "検証のまとめ" in body
    assert _get(f"{server}/data/view?item=bars")[0] == 200
    assert _get(f"{server}/experiments/view?item=nai")[0] == 404
    assert _get(f"{server}/nazo/api/sidebar")[0] == 404


# ---------------------------------------------------------------- 見張り


def test_exp_fingerprint_moves_on_checks_update(runs_dir):
    before = vibetab.exp_fingerprint(runs_dir)
    p = runs_dir / "2026-09-09T12-00-00_own_2018" / "checks.json"
    import os
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))  # ⚠ dir の mtime は動かない上書き
    after = vibetab.exp_fingerprint(runs_dir)
    assert after != before
    assert set(after) == set(before)
