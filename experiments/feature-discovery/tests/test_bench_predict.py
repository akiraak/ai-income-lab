"""`bench_predict.py`（13500t で予測の時間を測る。docs/plans/predict-timing-13500t.md）の拾い方と比べ方。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import bench_predict as bp  # noqa: E402


def test_picks_the_same_models_as_run_live():
    # run-live.sh と同じ: 各トレーダーの kind = "experiment" を (実験, 手法) で重複なく
    ms = bp.pick_models(["T1", "T2", "T3"], bp.TRADERS_DIR)
    assert [(m["experiment"], m["method"]) for m in ms] == [
        ("trade_own_ridge_a", "全部使う（基準）"),
        ("trade_ownex_lgbm_a", ms[1]["method"]),
        ("trade_ownseq_ridge_a", "T3 QUANT（60日窓）"),
    ]
    assert len(bp.pick_models(["T1", "T1"], bp.TRADERS_DIR)) == 1


def _write(path, buy, th=50.0):
    run = {"kind": "run", "asof": "2026-09-18", "i": 0, "wall_s": 1.0, "ok": True,
           "models": [{"experiment": "e", "method": "m", "rc": 0, "done_s": 1.0, "seconds": {},
                       "input_fingerprint": "f", "threshold": th, "buy": buy,
                       "exit": {s: 100 - v for s, v in buy.items()}}]}
    with open(path, "w", encoding="utf-8") as f:
        for r in ({"kind": "machine"}, run, {"kind": "summary", "asof": "2026-09-18", "ok": True}):
            f.write(json.dumps(r) + "\n")


def test_compare_fails_only_when_a_decision_flips(tmp_path, capsys):
    a, b, c = tmp_path / "a.jsonl", tmp_path / "b.jsonl", tmp_path / "c.jsonl"
    _write(a, {"T": 60.0, "PFE": 40.0})
    _write(b, {"T": 60.000001, "PFE": 40.0})       # 丸めの差だけ ＝ 判定は同じ
    _write(c, {"T": 49.9, "PFE": 40.0})            # T の買いが消える
    assert bp.compare(str(a), str(b)) == 0
    assert bp.compare(str(a), str(c)) == 1
    assert "判定が変わった銘柄 ['T']" in capsys.readouterr().out
