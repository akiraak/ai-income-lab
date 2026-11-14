"""登録簿 `20261109-null-shuffle-top-rows`（2026-11-13 の段 2・人の手）: 偽の実行の物差しを上位の行と「採る」の行に当てる（cli.null_rows）。

⚠ 見るもの: 入れ替えは fold の切り口だけ（with を抜けたら元に戻る）／ 同じ (種, fold, 分割) と同じ日付の集合なら表が違っても同じ入れ替え ／
   断面を保つ・y の集合は同じ ／ 対象の config は要らない部品だけを外した写し（元の config は変えない）／ 記録の口は何も書かない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import config
from ail.validation import splits
from cli import null_rows as nr
from cli import null_shuffle as ns


def _panel(n_days=400, syms=("A", "B", "C"), seed=0, extra=0.0):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2020-01-01", periods=n_days, freq="B", tz="UTC")
    rows = []
    for j, s in enumerate(syms):
        y = np.arange(n_days) * 1.0 + j * 1000.0 + extra          # 日と銘柄が読める y
        rows.append(pd.DataFrame({"symbol": s, "ts": ts, "y": y, "y_sign": 1.0, "own_x": rng.normal(size=n_days)}))
    return pd.concat(rows, ignore_index=True).sort_values(["ts", "symbol"]).reset_index(drop=True)


def _folds(panel):
    edges = splits.date_edges(panel["ts"], 5, None)
    return list(splits.folds_by_dates(panel, edges, 1440.0, 0, 1440.0))


def test_patch_only_inside_with_and_none_seed_does_nothing():
    orig = splits.folds_by_dates
    with nr.shuffled_folds(None):
        assert splits.folds_by_dates is orig
    with nr.shuffled_folds(3):
        assert splits.folds_by_dates is not orig
    assert splits.folds_by_dates is orig
    with pytest.raises(RuntimeError):
        with nr.shuffled_folds(3):
            raise RuntimeError("途中で落ちても")
    assert splits.folds_by_dates is orig                                        # 元に戻る


def test_shuffle_keeps_cross_section_and_is_the_same_for_tables_with_the_same_days():
    p = _panel()
    plain = _folds(p)
    with nr.shuffled_folds(7) as sf:
        a = _folds(p)
        b = _folds(_panel(syms=("A", "B"), seed=1))                             # 外のメンバーの表（銘柄と列は違う・日付は同じ）
        assert sf.borrowed == 0
    with nr.shuffled_folds(7):
        a2 = _folds(p)
    with nr.shuffled_folds(8):
        c = _folds(p)
    assert len(a) == len(plain) == 5
    moved, other = [], []
    for (f, tr, te), (f0, tr0, te0), (_f, tr2, te2), (_g, trb, teb), (_h, trc, tec) in zip(a, plain, a2, b, c):
        assert f == f0
        for x, x0, x2, xb, xc in ((tr, tr0, tr2, trb, trc), (te, te0, te2, teb, tec)):
            assert len(x) == len(x0) and np.array_equal(x["own_x"].to_numpy(), x0.reset_index(drop=True)["own_x"].to_numpy())   # 特徴量は動かない
            assert sorted(x["y"]) == sorted(x0["y"])                           # y の集合は同じ
            moved.append(not np.array_equal(x["y"].to_numpy(), x0["y"].to_numpy()))
            other.append(not np.array_equal(x["y"].to_numpy(), xc["y"].to_numpy()))
            day = (x["y"] % 1000).groupby(x["ts"].to_numpy()).nunique()
            assert (day == 1).all()                                             # 同じ日の全銘柄が同じ日から y を借りる
            assert (x["y"] // 1000).tolist() == [("A", "B", "C").index(s) for s in x["symbol"]]                                # 自分の銘柄の y
            assert np.array_equal(x["y"].to_numpy(), x2["y"].to_numpy())        # 同じ種なら同じ
            src = (x["y"] % 1000).groupby(x["ts"].to_numpy()).first()
            src_b = (xb["y"] % 1000).groupby(xb["ts"].to_numpy()).first()
            assert src.equals(src_b)                                            # 表が違っても、日付の集合が同じなら同じ入れ替え
    assert sum(moved) >= 8 and sum(other) >= 8                                  # 並びは変わる・種が違えば違う（短い分割は偶然同じになりうる）
    # 単独の表では cli.null_shuffle.shuffle_y と同じ入れ替え（11/7 の物差しと同じ差し替え方）
    f1, tr1, _te1 = plain[0]
    want = ns.shuffle_y(tr1.reset_index(drop=True), ns.BLOCK, np.random.default_rng([7, int(f1), 0, 20261109]))
    assert np.array_equal(a[0][1]["y"].to_numpy(), want["y"].to_numpy())
    # 日付の集合が違う表（持ち主だけ訓練が 1 日短い、など）: 最初の表の対応を借り、対応に無い日 ・ 借り先の無い行だけを落とす
    with nr.shuffled_folds(7) as sf:
        first = _folds(p)
        short = p[p["ts"] != p["ts"].unique()[100]]
        second = _folds(short)
        assert sf.borrowed > 0
    for (_f, tr, _te), (_g, trs, _tes) in zip(first, second):
        m = tr.merge(trs, on=["symbol", "ts"], suffixes=("", "_s"))
        assert len(m) >= len(trs) - 3 and np.array_equal(m["y"].to_numpy(), m["y_s"].to_numpy())   # 共通の行は同じ y を借りる


def test_trimmed_configs_keep_only_what_the_target_rows_need():
    full = config.resolve_experiment("sel_small4_1995")
    e = nr.trimmed("sel1995")
    assert e["selectors"] == ["F2-2 RFE", "F1-7 IC の安定性"] and e["trading"]["thresholds"] == [50.0] and e["baselines"] == ["常に上（ドリフト）"]
    assert len(full["selectors"]) == 5 and full["trading"]["thresholds"] == [50, 55, 60]             # 元の config は変えない
    assert e["model"] == full["model"] and e["k"] == full["k"] and e["validation"] == full["validation"]
    m = nr.trimmed("mix")
    assert "trader" not in m["trading"] and [x["name"] for x in m["trading"]["mix"]] == ["M1 3本の平均"]
    assert len(m["trading"]["mix"][0]["members"]) == 3 and m["selectors"] == ["全部使う（基準）"]
    s = nr.trimmed("sel600")
    assert [c["tag"] for c in s["trading"]["trader"]] == [nr.SEL_TAG] and s["trading"]["trader"][0]["select"] == {"by": "ret", "k": 5}
    assert s["trading"]["trader"][0]["budget_usd"] == 3000
    t = nr._targets()
    assert t["sel600"]["rows"] == [("全部使う（基準）〔トレーダー・選抜5・訓練リターン・1本$600〕", "基準 持ち続ける〔トレーダー・選抜5・訓練リターン・1本$600〕")]
    assert nr.THETA == 50.0 and ns.BLOCK == 20 and ns.PERMS == 200                # 11/7 と同じ水準


def test_summary_counts_quantile_and_verdicts_and_norun_writes_nothing():
    orig = {"r": {"edges": [1.0, 2.0, 3.0], "verdict": "採る", "mean": 2.0}}
    null = [{"r": {"edges": [k - 2.0, k - 2.0], "verdict": "採る" if k > 2 else ("落とす" if k < 2 else "保留"), "mean": k - 2.0}} for k in range(5)]
    s = nr.summarize(null, orig, "r")                                           # 偽の平均 −2, −1, 0, 1, 2
    assert s["perms"] == 5 and s["元の分位"] == pytest.approx(0.9) and s["採る"] == 0.4 and s["落とす"] == 0.4 and s["保留"] == 0.2
    assert s["全fold正の割合"] == 0.4 and s["null_p50"] == 0.0 and s["元のfold"] == [1.0, 2.0, 3.0]
    run = nr._NoRun()
    assert run.log("x") is None and run.fitted("a", {}) is None and run.selected(None) is None
