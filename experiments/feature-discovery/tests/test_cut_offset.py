"""評価期間の切れ目をずらす口 `[validation] cut_offset`（rules.md 33 章。2026-11-11・登録簿 `20261111-fold-cut-shift`）。

⚠ **無い config は 1 ビットも変わらない**（切れ目・fold の行。既定経路の指紋は `test_trading_run.py`）。
ずらし ＝ fold g（1〜5）の初日を、その群の日付の数 L_g × 分子 ÷ 分母（切り捨て）個ぶん後ろへ。最初の日と最後の端は不変。
門・合わせる口のメンバーも同じ切れ目。検証方式の変種 `閾値売買（切れ目ずらし1/3）`・予測モデル名 `~cut1of3`。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import catalog, config, names, runs
from ail.validation import gate, splits
from cli.run import evaluate_trading
from tests import _fingerprint as fp
from tests import test_mix as tm

BH = "基準 常に上（ドリフト）"
ALL = "全部使う（基準）"


def _days(panel):
    return pd.Series(pd.to_datetime(panel["ts"]).unique()).sort_values().reset_index(drop=True)


def test_none_is_the_same_edges_and_folds():
    p = fp.panel()
    a, b = splits.date_edges(p["ts"], 5), splits.date_edges(p["ts"], 5, None)
    assert a == b and splits.cut_offset({}) is None and splits.cut_offset({"folds": 5}) is None
    fa = list(splits.folds_by_dates(p, a, 1440.0))
    fb = list(splits.folds_by_dates(p, splits.date_edges(p["ts"], 5, splits.cut_offset({})), 1440.0))
    assert len(fa) == len(fb) == 5
    for (f1, tr1, te1), (f2, tr2, te2) in zip(fa, fb):
        assert f1 == f2 and tr1.equals(tr2) and te1.equals(te2)


@pytest.mark.parametrize("text,num,den", [("1/3", 1, 3), ("2/3", 2, 3)])
@pytest.mark.parametrize("n_days", [800, 803, 7919 // 10])
def test_starts_move_back_by_the_floor_of_the_share(text, num, den, n_days):
    p = fp.panel(n_days=n_days)
    days = _days(p)
    base = splits.date_edges(p["ts"], 5)
    off = splits.date_edges(p["ts"], 5, splits.cut_offset({"cut_offset": text}))
    assert len(off) == len(base) == 7                         # fold は 5 つのまま
    assert off[0] == base[0] and off[-1] == base[-1]          # 最初の日と最後の端は動かさない
    pos = lambda t: int(days.searchsorted(t))
    for g in range(1, 6):
        L = pos(base[g + 1]) - pos(base[g]) if g < 5 else len(days) - pos(base[g])
        assert pos(off[g]) - pos(base[g]) == L * num // den   # 表の日付で数える・切り捨て
        assert base[g] < off[g] < base[g + 1]                 # 後ろへ・元の群の中
    assert all(a < b for a, b in zip(off, off[1:]))
    # 評価期間は重ならず隙間も無い ＝ ずらした fold 1 の初日から最後までの全部の日がちょうど 1 度ずつ
    folds = list(splits.folds_by_dates(p, off, 1440.0))
    assert [f for f, _, _ in folds] == [1, 2, 3, 4, 5]
    te_days = pd.concat([pd.Series(pd.to_datetime(te["ts"]).unique()) for _, _, te in folds])
    assert te_days.is_unique and set(te_days) == set(days[days >= off[1]])
    for f, tr, te in folds:
        assert pd.to_datetime(tr["ts"]).max() < off[f] <= pd.to_datetime(te["ts"]).min()
    # 訓練は減らない（どの fold も元の版より後ろまで学ぶ）・最後の評価期間は縮む
    for (f, tr, _), (_, tr0, _) in zip(folds, splits.folds_by_dates(p, base, 1440.0)):
        assert len(tr) > len(tr0)
    assert len(folds[-1][2]) < len(list(splits.folds_by_dates(p, base, 1440.0))[-1][2])


@pytest.mark.parametrize("bad", ["1/2", "3/3", "0/3", "", " 1/3", 0.33, 1, True, ["1/3"]])
def test_bad_value_stops(bad):
    with pytest.raises(SystemExit, match="cut_offset"):
        splits.cut_offset({"cut_offset": bad})


def test_levels_are_the_two_fixed_ones():
    assert splits.CUT_OFFSETS == {"1/3": (1, 3), "2/3": (2, 3)}    # ⚠ rules.md 33-2 の写し。足すなら新しい id で先に登録


def test_style_and_name_and_counting():
    base = {"trading": {"style": "threshold"}, "validation": {"folds": 5}}
    assert catalog.threshold_style(base) == "閾値売買"                # 無い config は割れない
    for text, slug in (("1/3", "cut1of3"), ("2/3", "cut2of3")):
        style = catalog.threshold_style({"trading": {"style": "threshold"}, "validation": {"cut_offset": text}})
        assert style == f"閾値売買（切れ目ずらし{text}）" and catalog.is_threshold(style)
        assert catalog.is_trial({"手法名": "F2-2 RFE", "検証方式": style})
        assert catalog.is_trial({"手法名": ALL, "検証方式": style})    # ⚠ 検証方式が処置 ＝ 全部使うも数える（33-2 の追記）
        assert not catalog.is_trial({"手法名": "乱択（基準）", "検証方式": style})
        assert not catalog.is_trial({"手法名": BH, "検証方式": style})
        key = dict(鍵="F2-2", モデル="Ridge", 粒度="日足", 地平="1 本（1 日）", 特徴量の層="own", 層="adjusted",
                   期間="1995-02-10", 銘柄="63", 検証方式=style, 形式="共通", 較正="std", 閾値="50")
        assert names.trial_name(key) == f"own.f2-2.ridge.shared~p1995-02-10~{slug}@50"     # 33-3 の例
        assert names.trial_name({**key, "検証方式": "閾値売買"}) == "own.f2-2.ridge.shared~p1995-02-10@50"
    both = catalog.threshold_style({"trading": {"style": "threshold", "execution": "next_open"},
                                    "validation": {"cut_offset": "1/3"}})
    assert both == "閾値売買（切れ目ずらし1/3・翌日始値）"            # ほかの変種とは `・` でつなぐ（33-3）
    with pytest.raises(SystemExit):                                  # 綴りの無い量は名前が付かない
        names.trial_name({**key, "検証方式": "閾値売買（切れ目ずらし1/2）"})


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_cut_offset", {}, seed=0)


def _exp(text=None):
    e = fp.experiment()
    e["trading"]["thresholds"] = [50]
    if text:
        e["validation"]["cut_offset"] = text
    return e


def _feats(p):
    return [c for c in p.columns if c not in ("symbol", "ts", "y")]


def test_trading_runs_on_the_shifted_folds(run):
    p = fp.panel()
    days = _days(p)
    a, *_ = evaluate_trading(p, _feats(p), _exp(), run)
    b, *_ = evaluate_trading(p, _feats(p), _exp("1/3"), run)
    off = splits.date_edges(p["ts"], 5, (1, 3))
    n = lambda r, m: r[r["手法"] == m].sort_values("fold")["検証日数"].to_numpy()
    assert sorted(b["fold"].unique()) == [1, 2, 3, 4, 5]
    want = [int(((days >= off[g]) & (days < off[g + 1])).sum()) for g in range(1, 6)]
    assert list(n(b, BH)) == want and list(n(b, ALL)) == want       # 基準線も同じ切れ目（33-1 の 6）
    assert n(b, BH).sum() < n(a, BH).sum() and n(b, BH)[-1] < n(a, BH)[-1]   # 最後の評価期間が縮む
    net = lambda r, m: r[r["手法"] == m].sort_values("fold")["純利bp"].to_numpy()
    assert not np.allclose(net(a, BH), net(b, BH)) and not np.allclose(net(a, ALL), net(b, ALL))


def test_gate_and_body_use_the_same_edges(run, monkeypatch):
    """門（`evaluate_gate`）と本体（`evaluate_trading`）が同じ切れ目で fold を切る（33-1 の 4）。"""
    seen: list = []
    real = splits.folds_by_dates

    def spy(panel, edges, *a, **k):
        seen.append(list(edges))
        return real(panel, edges, *a, **k)

    monkeypatch.setattr(splits, "folds_by_dates", spy)
    p = fp.panel()
    for text, off in ((None, None), ("1/3", (1, 3)), ("2/3", (2, 3))):
        seen.clear()
        gate.evaluate_gate(p, _feats(p), {**_exp(text), "selectors": [ALL]})
        evaluate_trading(p, _feats(p), _exp(text), run)
        assert len(seen) == 2 and seen[0] == seen[1] == splits.date_edges(p["ts"], 5, off)


def _host(text=None, mix=None):
    e = tm._exp(mix)
    if text:
        e["validation"]["cut_offset"] = text
    return e


def test_mix_members_are_cut_by_the_owner(run):
    """合わせる口（33-1 の 5）: メンバーの config に `cut_offset` は無い ＝ 持ち主のずらしで切る。同じ表・同じ config の
    メンバーを平均すると持ち主の行と同じ ＝ メンバーもずらした fold を学んだ証拠。"""
    panel = tm._panel()
    loader = tm._loader({"ext": panel})
    assert "cut_offset" not in loader("ext", False)[0]["validation"]
    res, *_ = evaluate_trading(panel, tm._feats(panel), _host("2/3", tm._mix()), run, mix_loader=loader)
    assert len(tm._net(res, tm.MIX)) == 5
    np.testing.assert_allclose(tm._net(res, tm.MIX), tm._net(res, ALL), rtol=0, atol=1e-9)
    plain, *_ = evaluate_trading(panel, tm._feats(panel), _host(None, tm._mix()), run, mix_loader=loader)
    assert not np.allclose(tm._net(res, tm.MIX), tm._net(plain, tm.MIX))     # ずらしが合成の行に効いている


def test_mix_member_with_other_edges_still_stops(run):
    panel = tm._panel()
    with pytest.raises(SystemExit, match="切れ目"):
        evaluate_trading(panel, tm._feats(panel), _host("1/3", tm._mix()), run,
                         mix_loader=tm._loader({"ext": tm._panel(shift_days=40)}))


# --- config 4 本（rules.md 33-2 の写し） -----------------------------------

CONFIGS = {"cut13_sel_small4_1995": ("sel_small4_1995", "1/3"), "cut23_sel_small4_1995": ("sel_small4_1995", "2/3"),
           "cut13_mix_t4": ("trader_mix_t4", "1/3"), "cut23_mix_t4": ("trader_mix_t4", "2/3")}


@pytest.mark.parametrize("name", sorted(CONFIGS))
def test_configs_are_copies_with_the_offset(name):
    """元の config の写しで、違いは `name`・`cut_offset`・θ 50 だけ・手法の絞り（sel）／ トレーダーの形を外す（mix）だけ。"""
    src, text = CONFIGS[name]
    a, b = config.experiment(src), config.experiment(name)
    assert b["name"] == name and b["validation"].pop("cut_offset") == text
    assert b["trading"].pop("thresholds") == [50]
    a["trading"].pop("thresholds")
    if src == "sel_small4_1995":
        keep = ["F2-2 RFE", "F1-7 IC の安定性", "乱択（基準）"]                    # F1-4・全部使うは回さない（33-2）
        assert b.pop("selectors") == keep
        assert [x for x in a.pop("selectors") if x in keep] == keep            # 元の並びのまま
    else:
        assert a["trading"].pop("trader") and "trader" not in b["trading"]     # 素の行だけ
        for m in b["trading"]["mix"][0]["members"]:
            if m.get("experiment"):                                            # メンバーの config には書かない（33-1 の 5）
                assert "cut_offset" not in config.experiment(m["experiment"]).get("validation", {})
    a.pop("name"), b.pop("name")
    assert a == b


def test_queue_runs_the_four_with_leak():
    q = config._load("queue", "fold_cut_shift")
    assert sorted(q["experiments"]) == sorted(CONFIGS) and q["leak"] is True      # ⚠ leak 対照はずらした版ごと（33-1 の 7）
