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


def make_run(runs_dir: Path, name: str, leak: bool = False, score: float = 1.5,
             positive: int = 5, t: float = 3.5, dsr: float = 0.97,
             layer: str = "adjusted", layers: tuple = ("own", "cs")) -> Path:
    d = runs_dir / name
    d.mkdir(parents=True)
    (d / "summary.csv").write_text(
        "手法,本数,的中率,IC,粗利bp,純利bp,fold数\n"
        f"全部使う（基準）,36,0.51,0.02,{score + 4.0},{score + 3.0},5\n"
        f"F3-1 Lasso,12,0.52,0.03,{score + 1.0},{score},5\n",
        encoding="utf-8")
    (d / "config.json").write_text(json.dumps(
        {"dataset": "daily", "horizon": 1, "k": 5, "cost_bp": 5.0,
         "feature_layers": list(layers)}), encoding="utf-8")
    (d / "inputs.json").write_text(json.dumps(
        {"layer": layer, "features": 100, "symbols": 63, "rows_before_sample": 131250}),
        encoding="utf-8")
    (d / "env.json").write_text(json.dumps(
        {"seed": 7, "git_commit": "abc1234", "started_at": "2026-09-09T12:00:00"}),
        encoding="utf-8")
    (d / "checks.json").write_text(json.dumps({
        "leak": leak,
        "best": {"method": "F3-1 Lasso", "純利bp": score},
        "folds": {"positive": positive, "folds": 5},
        "edge_vs_drift": {"t": t},
        "dsr": {"DSR": dsr},
        "breadth": {"系列数": 63, "実効系列数": 4.7, "実効観測数": 4087},
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
    (exp / "data" / "manifests" / "exog_noaa.json").write_text(json.dumps({
        "layer": "exog", "period": "series", "source": "noaa", "dataset": "weather_2018",
        "symbols": 2, "role": "偽薬", "totals": {"rows": 200},
        "series": {"ex_tmax": {"rows": 100, "oldest_ms": 1514764800000,
                               "newest_ms": 1750000000000},
                   "ex_prcp": {"rows": 100}},
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
        'name = "us63"\nselected_on = "2026-09-01"\nsurvivorship_bias = true\n'
        '[groups]\ncompany = ["AAPL"]\netf = ["SPY"]\n',
        encoding="utf-8")
    (exp / "config" / "exposure").mkdir(parents=True)
    (exp / "config" / "exposure" / "us63.toml").write_text(
        'name = "us63"\nhindsight = true\n[[channel]]\nname = "hurricane"\n'
        'source = "ncei"\nseries = "im_tropical"\nkind = "region"\nweights = "hurricane"\n'
        'hypothesis = "保険"\n[weights.hurricane]\nALL = 0.5\n', encoding="utf-8")
    (exp / "config" / "sources.toml").write_text(
        '[usgs]\nlabel = "USGS 地震"\nlag_days = 1\nterms = "公開データ"\n'
        'terms_note = "米政府の著作物"\npublish_note = "翌日に確定"\n'
        '[noaa]\nlabel = "NOAA 気象"\nlag_days = 2\nterms = "公有"\n', encoding="utf-8")
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


# ---------------------------------------------------------------- まとめの 3 節


def test_exp_overview_methods_and_traits(runs_dir):
    body = vibetab.exp_overview_html(runs_dir)
    assert "検証方法とその特徴" in body
    assert "検証方法ごとの成績" in body
    # fixture の実検証は feature_layers に cs があるので断面。組の表に組が出る
    assert "断面・日足・1 日先・調整後" in body
    assert "先読みの検査・日足・1 日先・調整後" in body
    assert "4,087" in body                       # breadth の実効観測数（写し）


def test_exp_overview_analysis_pass(runs_dir):
    """fixture は先読みが跳ね、実検証も 4 検査 ✅ → 両方の ✅ 分岐。"""
    body = vibetab.exp_overview_html(runs_dir)
    assert "配線は働いている" in body
    assert "4 検査を満たす実行が 1 件" in body


def test_exp_overview_analysis_no_leak_no_pass(tmp_path):
    """先読みの検査が無く、検査も通らない → ⏳ と ⚠ の分岐。"""
    runs = tmp_path / "runs"
    make_run(runs, "2026-09-09T12-00-00_own", score=-1.0, positive=2, t=0.5, dsr=0.3)
    body = vibetab.exp_overview_html(runs)
    assert "配線の確認がまだ無い" in body
    assert "「発見あり」と言える検証はまだ無い" in body


def test_exp_overview_analysis_leak_not_jumping(tmp_path):
    """跳ねない先読みの検査 → 配線を疑う分岐。"""
    runs = tmp_path / "runs"
    make_run(runs, "2026-09-09T12-00-00_own_leak", leak=True,
             score=1.0, positive=2, t=0.5, dsr=0.3)
    body = vibetab.exp_overview_html(runs)
    assert "跳ねない先読みの検査がある" in body


def test_exp_overview_raw_layer_excluded(tmp_path):
    """日足 × raw は層 ⚠ → 有効性の比較から外す。"""
    runs = tmp_path / "runs"
    make_run(runs, "2026-09-09T12-00-00_raw", layer="raw", score=2.0)
    body = vibetab.exp_overview_html(runs)
    assert "有効性の比較から外す" in body
    assert "調整前" in body


def test_exp_overview_compares_two_methods(tmp_path):
    """層 ✅ の組が 2 つ → スコアの上での比較の文が出る（最良が先）。"""
    runs = tmp_path / "runs"
    make_run(runs, "2026-09-09T12-00-00_cs", score=2.0, positive=2, t=0.7, dsr=0.5)
    make_run(runs, "2026-09-09T13-00-00_own", score=1.0, positive=2, t=0.6, dsr=0.4,
             layers=("own",))
    body = vibetab.exp_overview_html(runs)
    assert "スコアの上では <b>断面・日足・1 日先・調整後</b> が最良" in body
    assert "プーリング・日足・1 日先・調整後" in body


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


def test_fmt_only_trims_zeros_after_the_point():
    """⚠ **整数部の 0 を削らない**（20.0 を「2」と出すと水準の表示が嘘になる）。"""
    assert vibetab.fmt(20.0, 0) == "20"
    assert vibetab.fmt(20.0) == "20"
    assert vibetab.fmt(1650.0) == "1,650"
    assert vibetab.fmt(0.5034, 3) == "0.503"
    assert vibetab.fmt(3.20, 1) == "3.2"
    assert vibetab.fmt(None) == "—"


# --------------------------------------------- 門前の実行（rules.md 14-5）


def make_gated_run(runs_dir: Path, name: str) -> Path:
    """⚠ **summary.csv を書かない**（前置きの門で閾値売買を回していない実行）。"""
    d = runs_dir / name
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(
        {"dataset": "daily", "horizon": 1, "k": 16, "cost_bp": 5.0, "feature_layers": ["own"],
         "trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"}}),
        encoding="utf-8")
    (d / "inputs.json").write_text(json.dumps(
        {"layer": "adjusted", "features": 35, "symbols": 63, "rows_before_sample": 135962}),
        encoding="utf-8")
    (d / "env.json").write_text(json.dumps(
        {"seed": 0, "git_commit": "abc1234", "started_at": "2026-09-11T11:00:00"}),
        encoding="utf-8")
    (d / "checks.json").write_text(json.dumps({
        "leak": False, "style": "threshold", "form": "shared", "cost_bp": 5.0,
        "thresholds": [50.0, 55.0, 60.0],
        "gate": {"auc_min": 0.52, "width_min_pt": 20.0, "form": "shared",
                 "methods": {"全部使う（基準）": {"auc": 0.5034, "width_pt": 3.2,
                                                  "auc_folds": [0.5, 0.51, None, 0.49, 0.5],
                                                  "width_folds": [3.2] * 5, "passed": False}},
                 "passed": [], "blocked": ["全部使う（基準）"]},
    }, ensure_ascii=False), encoding="utf-8")
    return d


def test_gated_run_is_listed_apart_and_not_counted(runs_dir):
    """⚠ **門前は目次とまとめに出るが、「検証 N 件」には数えない**（rules.md 14-5）。"""
    make_gated_run(runs_dir, "2026-09-11T11-00-00_trade_own_lgbm_a")
    items = vibetab.exp_sidebar(runs_dir)["items"]
    by_id = {i["id"]: i for i in items}
    g = by_id["2026-09-11T11-00-00_trade_own_lgbm_a"]
    assert g["badge"] == "門前" and "門前" in g["group"]
    assert "検証 1 件" in by_id["overview"]["sub"]          # ⚠ 門前を足して 2 件にしない
    body = vibetab.exp_overview_html(runs_dir)
    assert "門前（前置きの門を通らず、検証を回していない）" in body
    assert "0.503" in body and "3.2" in body                # 門の 2 値（写し）


def test_gated_run_page_shows_the_gate_not_an_empty_score(runs_dir):
    make_gated_run(runs_dir, "2026-09-11T11-00-00_trade_own_lgbm_a")
    body = vibetab.exp_run_html(runs_dir, "2026-09-11T11-00-00_trade_own_lgbm_a")
    assert body is not None
    assert "回していない" in body and "前置きの門" in body
    assert "0.503" in body and "閾値売買・共通" in body
    assert "スコア（最良手法の純利 bp）" not in body        # ⚠ 空の成績を出さない


# ---------------------------------------------------------------- データタブ


def test_data_sidebar_sections(exp_dir):
    items = vibetab.data_sidebar(vibetab.ExpPaths(exp_dir))["items"]
    assert [i["id"] for i in items] == [sec for sec, _ in vibetab.DATA_SECTIONS]
    by_id = {i["id"]: i for i in items}
    assert "1,500" in by_id["overview"]["sub"]   # manifest の rows の合計（写し）


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
    assert "後知恵あり" in vibetab.data_section_html(paths, "exposures")  # 後知恵
    assert vibetab.data_section_html(paths, "nazo") is None


# --------------------------------------------- データタブの情報量（data.html と同等）


def test_data_external_details_and_placebo(exp_dir):
    body = vibetab.data_section_html(vibetab.ExpPaths(exp_dir), "external")
    assert "系列の一覧" in body                       # manifest ごとの系列の details
    assert "ex_tmax" in body                          # 系列の一覧の中身
    assert "値動きと因果を想定しない（偽薬）" in body   # 仮説なしの偽薬の既定文
    assert "公開データ" in body                        # 規約の列（sources.toml の写し）
    assert "偽発見率" in body                          # 脚注


def test_data_sources_columns(exp_dir):
    body = vibetab.data_section_html(vibetab.ExpPaths(exp_dir), "sources")
    assert "1 日ずらす" in body                        # 太字のずらし幅
    assert "米政府の著作物" in body                    # 根拠（terms_note）の列
    assert "翌日に確定" in body                        # 公表の遅れ（publish_note）の列
    assert "FRED・Open-Meteo・SILSO" in body           # 「要判断」の脚注


def test_data_exposures_note_and_channels(exp_dir):
    body = vibetab.data_section_html(vibetab.ExpPaths(exp_dir), "exposures")
    assert "重みは全部【推測】" in body                 # 注記の箱
    assert "地域ごと" in body                          # 経路の形（kind = region）
    assert "ncei" in body                              # 経路の取得元
    assert "im_tropical" in body                       # 経路の系列
    assert "im_scramble" in body                       # 脚注（偽薬 ＝ 割り当ての入れ替え）


def test_data_bars_features_universes_notes(exp_dir):
    paths = vibetab.ExpPaths(exp_dir)
    bars = vibetab.data_section_html(paths, "bars")
    assert "adjusted_d.json" in bars                   # manifest のファイル名
    assert "銘柄名から推測しない" in bars              # 種別の脚注
    assert "先読みの検査用の表" in vibetab.data_section_html(paths, "features")
    assert "選定時点で存在する銘柄" in vibetab.data_section_html(paths, "universes")


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
    # 用語（正本は dashboard/glossary.toml。リポジトリの実物を読む）
    status, body = _get(f"{server}/glossary/api/sidebar")
    assert status == 200 and json.loads(body)["items"][0]["id"] == "all"
    assert _get(f"{server}/glossary/view?item=all")[0] == 200
    assert _get(f"{server}/glossary/view?item=nai")[0] == 404


# ---------------------------------------------------------------- 見張り


def test_exp_fingerprint_moves_on_checks_update(runs_dir):
    before = vibetab.exp_fingerprint(runs_dir)
    p = runs_dir / "2026-09-09T12-00-00_own_2018" / "checks.json"
    import os
    os.utime(p, (p.stat().st_atime, p.stat().st_mtime + 10))  # ⚠ dir の mtime は動かない上書き
    after = vibetab.exp_fingerprint(runs_dir)
    assert after != before
    assert set(after) == set(before)


# ---------------------------------------------------------------- 閾値つき売買（rules.md 13 章）


def make_trading_run(runs_dir: Path, name: str) -> Path:
    d = runs_dir / name
    d.mkdir(parents=True)
    head = "手法,閾値,本数,的中率,IC,粗利bp,純利bp,取引回数,保有日率,fold数\n"
    rows = []
    for th, net in ((50.0, 10.0), (55.0, 12.0), (60.0, 8.0)):
        rows.append(f"全部使う（基準）,{th},35.0,0.52,0.03,{net + 3.0},{net},60.0,0.5,5")
        rows.append(f"基準 常に上（ドリフト）,{th},0.0,0.52,0.0,{net + 4.0},{net - 1.0},63.0,1.0,5")
    (d / "summary.csv").write_text(head + "\n".join(rows) + "\n", encoding="utf-8")
    (d / "config.json").write_text(json.dumps(
        {"dataset": "daily", "horizon": 1, "k": 16, "cost_bp": 5.0, "model": "Ridge",
         "feature_layers": ["own"],
         "trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"}}),
        encoding="utf-8")
    (d / "inputs.json").write_text(json.dumps(
        {"layer": "adjusted", "features": 35, "symbols": 63, "rows_before_sample": 135962}),
        encoding="utf-8")
    (d / "env.json").write_text(json.dumps(
        {"seed": 0, "git_commit": "abc1234", "started_at": "2026-09-10T12:00:00"}),
        encoding="utf-8")
    entry = {"best": {"method": "全部使う（基準）", "純利bp": 12.0, "粗利bp": 15.0,
                      "的中率": 0.52, "本数": 35.0, "取引回数": 60.0, "保有日率": 0.5},
             "edge_vs_bh": {"pattern": "＋＋＋＋＋", "positive": 5, "folds": 5,
                            "values": [1.0, 2.0, 0.5, 0.8, 0.7], "mean_bp": 1.0, "t": 3.5},
             "bh_純利bp": 11.0,
             "dsr": {"DSR": 0.97, "n_trials": 91, "n_obs": 1800},
             "per_symbol": {"銘柄数": 63, "中央値bp": 3.2, "四分位bp": [-5.0, 12.0],
                            "勝ち銘柄": 40}}
    (d / "checks.json").write_text(json.dumps({
        "leak": False, "style": "threshold", "form": "shared", "cost_bp": 5.0,
        "thresholds": [50.0, 55.0, 60.0],
        "by_threshold": {"50": entry, "55": entry, "60": entry},
        "best": {**entry["best"], "閾値": 55.0},
        "folds": {k: entry["edge_vs_bh"][k] for k in ("pattern", "positive", "folds", "values")},
        "edge_vs_bh": entry["edge_vs_bh"], "bh_純利bp": 11.0,
        "dsr": entry["dsr"], "per_symbol": entry["per_symbol"],
    }, ensure_ascii=False), encoding="utf-8")
    return d


def test_exp_run_html_shows_thresholds(tmp_path):
    """⚠ 3 閾値とも出す・上乗せは対 B&H・銘柄別は要約だけ（checks.json の写し）。"""
    runs = tmp_path / "runs"
    make_trading_run(runs, "2026-09-10T12-00-00_trade_own_ridge_a")
    body = vibetab.exp_run_html(runs, "2026-09-10T12-00-00_trade_own_ridge_a")
    assert body is not None
    assert "閾値ごとの成績" in body
    assert body.count("55%") >= 1 and "50%" in body and "60%" in body
    assert "対 B&H の上乗せ" in body.replace("&amp;", "&")
    assert "勝ち 40/63" in body
    assert "θ=55%" in body                      # 最良の閾値
    assert "閾値売買・共通" in body              # タイトルで形式が読める


# ---------------------------------------------------------------- 用語（glossary.toml）

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def small_glossary(tmp_path: Path) -> Path:
    p = tmp_path / "g.toml"
    p.write_text(
        '[[section]]\nid = "units"\nlabel = "検証の単位"\nnote = "単位の語"\n\n'
        '[[section.term]]\nname = "試行"\nshort = "台帳の 1 行"\n'
        'doc = "docs/specs/experiments/feature-discovery/units.md"\nwhere = "§5"\n\n'
        '[[section.term]]\nname = "TODO の語"\nshort = "カテゴリの外にある文書"\n'
        'doc = "CLAUDE.md"\nwhere = "進め方"\n', encoding="utf-8")
    return p


def test_glossary_sidebar_has_index_first(small_glossary):
    items = vibetab.glossary_sidebar(small_glossary)["items"]
    assert items[0]["id"] == "all" and items[0]["sub"] == "2 語"
    assert [i["id"] for i in items[1:]] == ["units"]


def test_glossary_sidebar_missing_file(tmp_path):
    """⚠ 読めないときは空（仮の説明で埋めない）。"""
    items = vibetab.glossary_sidebar(tmp_path / "nai.toml")["items"]
    assert [i["id"] for i in items] == ["all"] and items[0]["sub"] == "0 語"


def test_glossary_view_links_to_vibeboard_hash(small_glossary):
    body = vibetab.glossary_html("units", small_glossary)
    assert body is not None
    assert "試行" in body and "台帳の 1 行" in body
    # docs/specs は Specs タブ、カテゴリの外は Files タブ。⚠ iframe から出るので target=_top
    assert 'href="/#specs/experiments/feature-discovery/units.md" target="_top"' in body
    assert 'href="/#files/CLAUDE.md" target="_top"' in body
    assert "§5" in body


def test_glossary_view_unknown_item(small_glossary):
    assert vibetab.glossary_html("nai", small_glossary) is None


def test_glossary_view_all_covers_every_section(small_glossary):
    body = vibetab.glossary_html("all", small_glossary)
    assert body is not None and "すべての用語" in body and "検証の単位" in body


def test_glossary_fingerprint_moves_on_edit(small_glossary):
    before = vibetab.glossary_fingerprint(small_glossary)
    import os
    st = small_glossary.stat()
    os.utime(small_glossary, (st.st_atime, st.st_mtime + 10))
    assert vibetab.glossary_fingerprint(small_glossary) != before


# --- ここから下は「実物の glossary.toml」の検査（索引であることを固定する） ---


def test_real_glossary_links_all_exist():
    """⚠ リンク切れは索引の価値を消す。文書を移したらここが赤くなる。"""
    missing = [(t["name"], t["doc"]) for s in vibetab.load_glossary()
               for t in s["term"] if not (REPO_ROOT / t["doc"]).exists()]
    assert missing == []


def test_real_glossary_terms_are_short_and_unique():
    """⚠ 1 語 1〜2 行の索引であって解説書ではない（長い説明は doc 側に置く）。"""
    sections = vibetab.load_glossary()
    assert len(sections) >= 2
    names = [t["name"] for s in sections for t in s["term"]]
    assert len(names) == len(set(names))          # 節をまたいで重複しない
    for s in sections:
        assert s.get("label") and s.get("term")
        for t in s["term"]:
            assert t["name"] and t["short"] and t["doc"]
            assert len(t["short"]) <= 90, (t["name"], len(t["short"]))


def test_real_glossary_covers_the_words_that_block_reading():
    """入口として最低限引けること（語が消えたら足し直す合図）。"""
    names = {t["name"] for s in vibetab.load_glossary() for t in s["term"]}
    for w in ("試行", "DSR（デフレーテッド SR）", "上乗せ", "門前", "ex_ / im_", "cert（sandbox）"):
        assert w in names
