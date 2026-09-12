"""前置きの門（rules.md 14-5）の検査。

⚠ **門は「回すかどうか」の足切りにだけ使い、採否には使わない。** 水準は事前固定
（2026-09-11 利用者決定）。⚠ **検証 fold には特徴量にも触れない**（だから門前は
n_trials に数えない）。実測の形は validation-power.md §5-1（雑音は幅で落ち、leak は大差で通る）。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from ail import catalog, runs
from ail.validation import gate, splits
from cli.run import apply_gate
import ail.bootstrap  # noqa: F401


def _panel(n_days=800, n_sym=3, seed=0, leak=False):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2019-01-02", periods=n_days, freq="D", tz="UTC")
    rows = []
    for i in range(n_sym):
        y = rng.normal(0.0002, 0.01, n_days)
        d = pd.DataFrame({"symbol": f"S{i}", "ts": ts, "y": y,
                          "own_ret_1": np.r_[0.0, y[:-1]],
                          "noise": rng.normal(0, 1, n_days)})
        if leak:
            d["LEAK_y"] = y                            # ⚠ わざとした先読み
        rows.append(d)
    return pd.concat(rows, ignore_index=True)


def _exp():
    return {"trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"},
            "validation": {"split": "walk_forward_dates", "folds": 5, "seed": 0},
            "k": 2, "cost_bp": 5.0, "horizon_min": 1440.0, "bar_minutes": 1440.0,
            "selectors": ["全部使う（基準）", "乱択（基準）"], "model": "Ridge",
            "baselines": ["常に上（ドリフト）"]}


def _feats(panel):
    return [c for c in panel.columns if c not in ("symbol", "ts", "y")]


# --- 門の計算（gate.evaluate_gate） ---------------------------------------

def test_levels_are_pinned():
    """⚠ **水準は事前固定**（2026-09-11 利用者決定）。動かすなら rules.md 14-5 に追記してから。"""
    assert gate.AUC_MIN == 0.52
    assert gate.WIDTH_MIN_PT == 20.0


def test_baselines_are_not_gated():
    """基準線は門に掛けない（採否の対象ではない）。「全部使う」は手法（13-9 の 4）。"""
    assert gate.is_gated("全部使う（基準）")
    assert not gate.is_gated("乱択（基準）")
    assert not gate.is_gated("基準 常に上（ドリフト）")


def test_noise_is_blocked():
    """雑音の表は門前（買い% がほぼ定数 ＝ 幅が水準に届かない。§5-1 の本番 8 実行と同じ形）。"""
    panel = _panel()
    doc = gate.evaluate_gate(panel, _feats(panel), _exp())
    d = doc["methods"]["全部使う（基準）"]
    assert d["passed"] is False
    assert doc["blocked"] == ["全部使う（基準）"] and doc["passed"] == []
    assert d["width_pt"] < gate.WIDTH_MIN_PT
    assert "乱択（基準）" not in doc["methods"]        # 基準線は門に掛けない


def test_leak_passes_by_a_wide_margin():
    """先読みの列は AUC ~1・幅 ~100 で通る（§5-1: leak は両方を大差で通る）。"""
    panel = _panel(leak=True)
    doc = gate.evaluate_gate(panel, _feats(panel), _exp())
    d = doc["methods"]["全部使う（基準）"]
    assert d["passed"] is True and doc["blocked"] == []
    assert d["auc"] > 0.9
    assert d["width_pt"] > 50.0


def test_gate_never_touches_validation_folds():
    """⚠ **検証 fold には特徴量にも触れない**（14-5。門が n_trials に入らない根拠）。

    最終 fold の検証区間はどの fold の訓練にも入らないので、そこを壊しても門の値は 1 桁も動かない。
    """
    panel = _panel()
    exp = _exp()
    before = gate.evaluate_gate(panel, _feats(panel), exp)
    edges = splits.date_edges(panel["ts"], 5)
    broken = panel.copy()
    tail = pd.to_datetime(broken["ts"]) >= edges[-2]
    assert tail.sum() > 0
    broken.loc[tail, ["y", "own_ret_1", "noise"]] = -9.9
    after = gate.evaluate_gate(broken, _feats(broken), exp)
    assert before == after


# --- run.py への適用（apply_gate） ----------------------------------------

@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))       # ⚠ 本物の runs/ を汚さない
    return runs.Run("test_gate", {}, seed=0)


def _gate_doc(passed=(), blocked=()):
    return {"auc_min": 0.52, "width_min_pt": 20.0, "form": "shared",
            "methods": {n: {"auc": 0.5, "width_pt": 0.0, "passed": n in passed}
                        for n in (*passed, *blocked)},
            "passed": list(passed), "blocked": list(blocked)}


def test_apply_gate_stops_when_everything_is_blocked(run):
    """全手法が門前なら None ＝ 閾値売買を回さない（規律 1: 門が先）。"""
    exp = {**_exp(), "selectors": ["全部使う（基準）"]}
    assert apply_gate(exp, _gate_doc(blocked=("全部使う（基準）",)), False, run) is None


def test_apply_gate_filters_only_the_blocked_selectors(run):
    """一部が門前なら、通った手法と基準線だけ残して回す。"""
    exp = {**_exp(), "selectors": ["A", "B", "乱択（基準）"]}
    out = apply_gate(exp, _gate_doc(passed=("A",), blocked=("B",)), False, run)
    assert out["selectors"] == ["A", "乱択（基準）"]
    assert exp["selectors"] == ["A", "B", "乱択（基準）"]   # 元の exp は変えない


def test_ignore_gate_runs_everything_and_marks_forced(run):
    """--ignore-gate（規律 3 の「後から回す」）: 全部回し、gate に forced が付く。"""
    exp = {**_exp(), "selectors": ["A", "B"]}
    doc = _gate_doc(passed=("A",), blocked=("B",))
    out = apply_gate(exp, doc, True, run)
    assert out["selectors"] == ["A", "B"]
    assert doc["forced"] is True


# --- 台帳の「門前」判定（catalog） ----------------------------------------

def _gated_run(with_summary=False):
    """checks.gate を持つ実行（catalog._run_trials の入力の形）。"""
    summ = pd.DataFrame()
    if with_summary:
        # ⚠ --ignore-gate で後から回した形（門前の手法が summary に載っている）
        summ = pd.DataFrame([{"手法": "全部使う（基準）", "閾値": 50.0, "本数": 2.0,
                              "的中率": 0.5, "IC": 0.0, "粗利bp": 1.0, "純利bp": -1.0,
                              "取引回数": 3.0, "保有日率": 0.5, "fold数": 5}]).set_index("手法")
    return {"実行": "g", "leak": False,
            "config": {"dataset": "daily", "bar_minutes": 1440.0, "horizon": 1, "cost_bp": 5.0,
                       "model": "Ridge", "feature_layers": ["own"],
                       "trading": {"style": "threshold", "thresholds": [50, 55, 60],
                                   "form": "shared"}},
            "inputs": {"layer": "adjusted", "features_meta": {"layer": "adjusted"}},
            "checks": {"gate": {"auc_min": 0.52, "width_min_pt": 20.0,
                                "methods": {"全部使う（基準）":
                                            {"auc": 0.501, "width_pt": 0.0, "passed": False}},
                                "passed": [], "blocked": ["全部使う（基準）"]}},
            "summary": summ, "result": None}


def test_pregate_row_has_no_numbers_and_one_row_per_method():
    """門前の行は検証の数字を持たない（回していない）。閾値は「—」で 1 手法 1 行。"""
    rows = catalog._run_trials(_gated_run())
    assert len(rows) == 1
    r = rows[0]
    assert r["門前"] == {"auc": 0.501, "width_pt": 0.0}
    assert r["検証方式"] == "閾値売買" and r["閾値"] == "—"
    assert r["純利bp"] is None and r["粗利bp"] is None and r["fold"] is None


def test_pregate_row_is_not_a_trial_and_judged_pregate():
    """⚠ **門前は n_trials に数えない**（14-5）。判定は「門前」で実測値と水準を書く。"""
    r = catalog._run_trials(_gated_run())[0]
    r["ID"], r["鍵"] = catalog.canonical(r["手法名"])
    assert catalog.is_trial(r) is False
    verdict, why = catalog.judge(r, {"全部使う（基準）", "乱択（基準）"})
    assert verdict == "門前"
    assert "0.501" in why and "0.0" in why
    assert "回していない" in why and "14-5" in why and "数えない" in why


def test_rerun_method_becomes_a_normal_trial_row():
    """⚠ 後から回した手法（--ignore-gate）は普通の行になり、普通に数える（規律 3）。"""
    rows = catalog._run_trials(_gated_run(with_summary=True))
    assert all(not r.get("門前") for r in rows)
    r = rows[0]
    r["ID"], r["鍵"] = catalog.canonical(r["手法名"])
    assert r["純利bp"] == -1.0
    assert catalog.is_trial(r) is True


def _write_gated_dir(d):
    d.mkdir()
    cfg = {"dataset": "daily", "bar_minutes": 1440.0, "horizon": 1, "cost_bp": 5.0,
           "model": "Ridge", "feature_layers": ["own"],
           "trading": {"style": "threshold", "thresholds": [50, 55, 60], "form": "shared"}}
    checks = {"style": "threshold",
              "gate": {"auc_min": 0.52, "width_min_pt": 20.0,
                       "methods": {"全部使う（基準）":
                                   {"auc": 0.501, "width_pt": 0.0, "passed": False}},
                       "passed": [], "blocked": ["全部使う（基準）"]}}
    (d / "config.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    (d / "checks.json").write_text(json.dumps(checks, ensure_ascii=False), encoding="utf-8")
    (d / "inputs.json").write_text(json.dumps({"layer": "adjusted"}), encoding="utf-8")


def test_read_run_accepts_a_fully_gated_run(tmp_path, monkeypatch):
    """summary の無い実行でも、gate で全手法が落ちたものは台帳のために拾う（隠さない）。"""
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    _write_gated_dir(tmp_path / "2026-09-11T00-00-00_gated")
    run = catalog._read_run("2026-09-11T00-00-00_gated")
    assert run is not None and len(run["summary"]) == 0
    assert run["checks"]["gate"]["blocked"] == ["全部使う（基準）"]


def test_read_run_still_rejects_a_crashed_run(tmp_path, monkeypatch):
    """gate の無い summary 無し実行（途中で落ちた）は従来どおり読まない。"""
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    d = tmp_path / "2026-09-11T00-00-00_crashed"
    d.mkdir()
    (d / "config.json").write_text("{}", encoding="utf-8")
    assert catalog._read_run("2026-09-11T00-00-00_crashed") is None


def test_a_forced_run_that_crashed_is_not_read_as_pregate(tmp_path, monkeypatch):
    """⚠ `--ignore-gate` で summary が無いのは「回したのに落ちた」。門前として拾わない。"""
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    d = tmp_path / "2026-09-11T00-00-00_forced"
    _write_gated_dir(d)
    ch = json.loads((d / "checks.json").read_text(encoding="utf-8"))
    ch["gate"]["forced"] = True
    (d / "checks.json").write_text(json.dumps(ch, ensure_ascii=False), encoding="utf-8")
    assert catalog._read_run("2026-09-11T00-00-00_forced") is None


def test_forced_run_makes_no_pregate_rows():
    """⚠ `--ignore-gate` の実行に門前の行は作らない（回すと決めた実行）。"""
    run = _gated_run(with_summary=True)
    run["checks"]["gate"]["forced"] = True
    run["checks"]["gate"]["blocked"] = ["全部使う（基準）", "回らなかった手法"]
    assert all(not r.get("門前") for r in catalog._run_trials(run))


def test_ledger_renders_pregate_rows(tmp_path, monkeypatch):
    """台帳の markdown に門前の行と内訳が出る（§0・§2。試行にも基準線にも数えない）。"""
    from cli import ledger
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    # ⚠ 実 runs/ を外すので、実 runs/ の保留行に当たる [[closed]] は空にする
    monkeypatch.setattr(catalog, "closed_notes", lambda path=None: [])
    _write_gated_dir(tmp_path / "2026-09-11T00-00-00_gated")
    md = ledger.build()
    assert "門前が 1 行" in md
    assert "＋ 1 行（門前）" in md
    assert "**門前**" in md
    assert "門を通らず" in md
