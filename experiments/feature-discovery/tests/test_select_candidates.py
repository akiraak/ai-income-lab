"""`cli.select_candidates` ＝ howto-model-trader.md §3-1 の物差し（2026-09-30 固定）を機械で当てる（読むだけ）。

⚠ 物差しは 1 つも変えない: 決まり 1（閾値売買・保留）／ 2（基準線・上位 K・無効・較正旧・トレーダーの形を除く）／ 3（fold の正の数 → 上乗せ）／
4（同じ予測モデル名は 1 行）／ 5（上位 N）／ 6（印）。⚠ 本物の DB があれば 2026-09-30 の実測（745 行 → 17 行 → F1-7・F2-2・D2）を再現する。
"""

from __future__ import annotations

import json
import os

import pytest

import ail.bootstrap  # noqa: F401
from ail import rundb, runs
from cli import select_candidates as sc


def _doc(method: str, *, verdict="保留", edge=100.0, fold="3/5 ＋＋−−＋", theta="50", style="閾値売買", layer="adjusted",
         cal="std", period="2018-01-31", symbols="63", sset="daily", model="Ridge", layers="own", runs_=("2026-10-01T00-00-00_x",)):
    return {"手法名": method, "判定": verdict, "上乗せbp": edge, "上乗せfold": fold, "fold": fold, "閾値": theta, "検証方式": style,
            "粒度": "日足", "層": layer, "較正": cal, "期間": period, "銘柄": symbols, "集合": sset, "モデル": model,
            "特徴量の層": layers, "実行一覧": list(runs_)}


def _insert(conn, name: str, doc: dict, *, is_trial=1, closed=0, first_run="2026-10-01"):
    model = name.split("@")[0]
    conn.execute("INSERT INTO ledger_rows (leak, trial_name, model_name, is_trial, verdict, closed, first_run, last_run, runs, doc)"
                 " VALUES (0, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (name, model, is_trial, doc["判定"], closed, first_run, first_run, json.dumps(doc["実行一覧"]), json.dumps(doc, ensure_ascii=False)))


@pytest.fixture
def conn(tmp_path):
    c = rundb.connect(str(tmp_path / "research.sqlite"))
    c.execute("INSERT OR REPLACE INTO meta VALUES ('ledger_n_trials', '100')")
    c.execute("INSERT OR REPLACE INTO meta VALUES ('ledger_built_at', '2026-10-25T00-00-00')")
    # 残る行
    _insert(c, "own.f9-1.ridge.shared@50", _doc("F9-1 試し", edge=100, fold="3/5 ＋＋−−＋"))
    _insert(c, "own.f9-1.ridge.shared@55", _doc("F9-1 試し", edge=300, fold="2/5 ＋−＋−0", theta="55"))        # 同じ予測モデル名 ＝ 1 行（決まり 4）
    _insert(c, "own.f9-2.ridge.shared@50", _doc("F9-2 試し", edge=50, fold="3/5 ＋−＋−＋"))                     # 同じ 3/5 なら上乗せで下
    _insert(c, "own-cs.mix3-mean.lgbm.shared~n48@50", _doc("M1 3本の平均", edge=20, fold="3/5 ＋＋＋−−", symbols="48"))   # 合成は含める
    _insert(c, "own.f9-3.ridge.shared~p1995-02-10~n74~us74@50", _doc("F9-3 試し", edge=10, fold="3/5 ＋＋＋−−", period="1995-02-10",
                                                                       symbols="74", sset="daily74"))
    _insert(c, "own.f9-4.ridge.shared@50", _doc("F9-4 試し", edge=999, fold="4/5 ＋＋＋＋−"), first_run="2026-10-20")   # asof で外れる
    # 除く行（決まり 2）
    _insert(c, "own.all.ridge.shared@50", _doc("全部使う（基準）", edge=500, fold="4/5 ＋＋＋＋−"))
    _insert(c, "own.rand.ridge.shared@50", _doc("乱択（基準）", edge=500, fold="4/5 ＋＋＋＋−"))
    _insert(c, "own.f9-1-top3-frac.ridge.shared@50", _doc("F9-1 試し〔上位3・端数〕", edge=900, fold="5/5 ＋＋＋＋＋"))
    _insert(c, "own.f9-5.ridge.shared~raw@50", _doc("F9-5 試し", edge=900, fold="5/5 ＋＋＋＋＋", layer="raw"))
    _insert(c, "own.f9-6.ridge.shared~cal-old@50", _doc("F9-6 試し", edge=900, fold="5/5 ＋＋＋＋＋", cal="旧"))
    _insert(c, "own.f9-1-tr5.ridge.shared@50", _doc("F9-1 試し〔トレーダー・5本・$300〕", edge=900, fold="5/5 ＋＋＋＋＋"))
    # 決まり 1 で外れる行
    _insert(c, "own.f9-7.ridge.shared~exec-open@50", _doc("F9-7 試し", edge=900, fold="5/5 ＋＋＋＋＋", style="閾値売買（翌日始値）"))
    _insert(c, "own.f9-8.ridge.shared@50", _doc("F9-8 試し", verdict="落とす", edge=-1, fold="0/5 −−−−−"))
    _insert(c, "own.f9-9.ridge.shared@50", _doc("F9-9 試し", verdict="採る", edge=900, fold="5/5 ＋＋＋＋＋"))
    _insert(c, "own.f9-10.ridge.shared", _doc("F9-10 試し", style="毎日往復", theta="—"))
    _insert(c, "own.base.ridge.shared@50", _doc("基準 常に上（ドリフト）", verdict="基準"), is_trial=0)      # 数えない行は読まない
    c.commit()
    yield c
    c.close()


def test_rules_1_to_5_in_order(conn):
    res = sc.apply_rules(sc.load_rows(conn), top=2)
    assert res["counted"] == 16 and res["rule1"] == 12
    assert res["excluded"] == {"基準線": 2, "上位 K": 1, "無効・要再測": 1, "較正「旧」": 1, "トレーダーの形": 1}
    assert res["rule2"] == 6 and res["rule4"] == 5
    names_ = [r.name for r in res["ranked"]]
    # 決まり 3: fold の正の数が先（F9-4 4/5）→ 同じ 3/5 は上乗せ順 → 決まり 4: F9-1 は θ 50（3/5）の 1 行だけ（θ 55 は 2/5 なので下）
    assert names_ == ["own.f9-4.ridge.shared@50", "own.f9-1.ridge.shared@50", "own.f9-2.ridge.shared@50",
                      "own-cs.mix3-mean.lgbm.shared~n48@50", "own.f9-3.ridge.shared~p1995-02-10~n74~us74@50"]
    assert [r.name for r in res["top"]] == names_[:2]


def test_asof_keeps_rows_whose_first_run_is_on_or_before(conn):
    res = sc.apply_rules(sc.load_rows(conn), asof="2026-10-10", top=3)
    assert "own.f9-4.ridge.shared@50" not in [r.name for r in res["ranked"]]
    assert [r.name for r in res["top"]] == ["own.f9-1.ridge.shared@50", "own.f9-2.ridge.shared@50", "own-cs.mix3-mean.lgbm.shared~n48@50"]


def test_marks_are_informational(conn):
    res = sc.apply_rules(sc.load_rows(conn), top=10)
    by = {r.name: r for r in res["ranked"]}
    assert by["own.f9-1.ridge.shared@50"].marks == []
    assert any("合成" in m for m in by["own-cs.mix3-mean.lgbm.shared~n48@50"].marks)
    m = by["own.f9-3.ridge.shared~p1995-02-10~n74~us74@50"].marks
    assert any("1995 年からの表" in x for x in m) and any("74 本・daily74" in x for x in m)


def test_render_and_json(conn):
    res = sc.select(conn, top=3, diag=False)
    text = sc.render(res)
    assert "n_trials 100" in text and "`own.f9-4.ridge.shared@50`" in text and "採用ではない" in text
    doc = json.loads(json.dumps(sc.to_json(res), ensure_ascii=False))
    assert doc["n_trials"] == 100 and [r["name"] for r in doc["top"]][0] == "own.f9-4.ridge.shared@50"


# --- 本物の DB（あれば）: 2026-09-30 の実測の再現 --------------------------------------------------

def _real_db():
    p = runs.db_path()
    if not os.path.exists(p):
        pytest.skip("研究の DB が無い")
    c = rundb.connect(p, readonly=True)
    try:
        if c.execute("SELECT COUNT(*) FROM ledger_rows WHERE leak = 0").fetchone()[0] == 0:
            pytest.skip("ledger_rows が空（cli.report --catalog を先に）")
    except Exception:
        pytest.skip("ledger_rows が無い")
    return c


def test_reproduces_the_2026_09_30_application():
    """howto §3-1 の実測（2026-09-30）: 数える行 745 → 決まり 1・2 で 17 行 → 上位 3 ＝ F1-7・F2-2（θ 50）・D2 us74（θ 55）。T3 QUANT は 8 位。"""
    c = _real_db()
    try:
        res = sc.apply_rules(sc.load_rows(c), asof="2026-09-30", top=3)
    finally:
        c.close()
    assert res["counted"] == 745 and res["rule2"] == 17
    assert [r.name for r in res["top"]] == ["own.f1-7.ridge.shared~p1995-02-10@50", "own.f2-2.ridge.shared~p1995-02-10@50",
                                            "own-trend.d2-gate60.ridge.shared~p1995-08-31~n74~us74@55"]
    assert [r.name for r in res["ranked"]].index("own-seq.t3-quant60.ridge.shared@50") == 7


def test_diagnostics_read_the_run_and_do_not_change_the_order():
    c = _real_db()
    try:
        res = sc.select(c, asof="2026-09-30", top=2, diag=True)
    finally:
        c.close()
    d = res["top"][0].diag
    assert d and d["run"] == "2026-09-12T20-39-38_sel_small4_1995" and d["folds"] == 5
    assert abs(d["edge_mean"] - res["top"][0].edge) < 0.05          # 上乗せの平均は検証結果一覧の値と同じ
    assert d["edge_drop_max"] < d["edge_mean"] and 0 <= d["dsr"] <= 1 and d["n_trials"] == res["n_trials"]
