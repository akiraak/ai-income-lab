"""段 2b の 3 処置（rules.md 28 章。2026-10-22）。ネットワークを使わない。

⚠ **落としたいのは 5 つ。**
  1. 層 `ernf` ＝ `ern` の 3 列の欠損を印の列 3 本（1 ＝ 無い）に写し、値を埋める（日数 ＝ stale の上限・前年同期比 ＝ 0）。欠損が残らない
  2. `ern` 層は 1 ビットも変わらない（欠損のまま）・系列の無い銘柄も `ernf` では表に残る
  3. 登録と並び（`ern` の後・`cal` の前）・`ern` と `ernf` を両方書いた config は止まる
  4. 費用 5bp 以外は検証方式の変種 `閾値売買（費用20bp）`（数える・判定は閾値売買のまま）・5bp は「閾値売買」のまま
  5. 予測モデル名は `~cost20`（翌日始値などの変種と並ぶ）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ail import catalog, names, registry
from ail.features import earnings
from cli import build
import ail.bootstrap  # noqa: F401


def _bars(dates):
    ts = pd.to_datetime(dates, utc=True)
    return pd.DataFrame({"ts": ts, "close": np.arange(len(ts), dtype=float)})


def _s(pairs):
    idx = pd.to_datetime([d for d, _ in pairs])
    return pd.Series([v for _, v in pairs], index=idx, dtype=float)


SERIES = {"ANN": _s([("2024-04-25", 1.0), ("2024-07-24", 1.0)]),
          "EPSYOY": _s([("2024-04-25", 0.4), ("2024-07-24", -0.2)]),
          "REVYOY": _s([("2024-04-25", 0.1)])}
DATES = ["2024-04-24", "2024-04-25", "2024-04-26", "2024-04-29", "2024-07-24", "2024-07-25", "2025-09-10"]


def test_flags_mark_missing_and_fill_values():
    x = earnings.build_one(_bars(DATES), SERIES)
    f = earnings.flag_missing(x)
    assert tuple(f.columns) == earnings.COLUMNS + earnings.FLAG_COLUMNS and len(f) == len(DATES)
    assert not f.isna().any().any()
    # 提出日の当日まで（0・1 行目）は無い ＝ 印 1・日数は上限 400・前年同期比は 0
    assert list(f["ern_na_ann"]) == [1, 1, 0, 0, 0, 0, 1] and list(f["ern_days_since_ann"].iloc[[0, 1, 6]]) == [400.0] * 3
    assert list(f["ern_na_eps"]) == [1, 1, 0, 0, 0, 0, 1] and list(f["ern_eps_yoy"].iloc[[0, 6]]) == [0.0, 0.0]
    assert list(f["ern_na_rev"]) == [1, 1, 0, 0, 0, 0, 1] and f["ern_rev_yoy"].iloc[2] == 0.1
    # 値のある行は 1 ビットも変わらない
    ok = x.notna().all(axis=1)
    pd.testing.assert_frame_equal(f.loc[ok, list(earnings.COLUMNS)], x.loc[ok])
    # stale の上限を変えれば埋め値も変わる
    assert earnings.flag_missing(x, max_stale_days=90)["ern_days_since_ann"].iloc[0] == 90.0


def test_ernf_keeps_symbols_without_series_and_ern_is_unchanged(monkeypatch):
    monkeypatch.setattr(earnings, "load_symbol_series", lambda s: SERIES if s == "AAA" else {})
    panel = {"AAA": _bars(DATES), "BBB": _bars(DATES)}
    plain = earnings.layer(panel, {})
    flagged = earnings.layer_flag(panel, {})
    assert plain["BBB"].isna().all().all() and tuple(plain["BBB"].columns) == earnings.COLUMNS     # ern はそのまま
    assert not flagged["BBB"].isna().any().any() and (flagged["BBB"][list(earnings.FLAG_COLUMNS)] == 1).all().all()
    pd.testing.assert_frame_equal(flagged["AAA"], earnings.flag_missing(plain["AAA"]))
    with pytest.raises(ValueError, match="1 以上"):
        earnings.layer_flag({"AAA": _bars(DATES)}, {"ern_lag_days": 0})


def test_ernf_registered_after_ern_and_both_is_refused(monkeypatch):
    assert registry.resolve("feature", "ernf") is earnings.layer_flag
    assert build.ORDER.index("ernf") == build.ORDER.index("ern") + 1 and build.ORDER[-1] == "cal"
    # 両方書いた config は表を作る前に止まる（層を解決した直後 ＝ 足を読む前に止めるため resolve_experiment を差し替える）
    monkeypatch.setattr(build.config, "resolve_experiment",
                        lambda name: {"feature_layers": ["own", "ern", "ernf"], "_dataset": {"period": "d", "source": "x", "universe": "u"},
                                      "horizon": 1, "_symbols": []})
    with pytest.raises(SystemExit, match="両方"):
        build.assemble("x")


def test_cost_is_a_validation_style_variant():
    base = {"trading": {"style": "threshold"}}
    assert catalog.threshold_style({**base, "cost_bp": 5.0}) == "閾値売買"
    assert catalog.threshold_style(base) == "閾値売買"                                   # 無ければ既定 5bp
    style = catalog.threshold_style({**base, "cost_bp": 20.0})
    assert style == "閾値売買（費用20bp）" and catalog.is_threshold(style)
    both = catalog.threshold_style({"trading": {"style": "threshold", "execution": "next_open"}, "cost_bp": 20.0})
    assert both == "閾値売買（翌日始値・費用20bp）"


def test_cost_variant_in_model_name():
    row = {"鍵": "全部使う（基準）", "モデル": "Ridge", "粒度": "日足", "地平": "1 本（1 日）",
           "特徴量の層": "own cs ll", "層": "adjusted", "期間": "2018-06-27", "銘柄": "50",
           "検証方式": "閾値売買（費用20bp）", "形式": "共通", "較正": "std", "閾値": "50"}
    assert names.trial_name(row) == "own-cs-ll.all.ridge.shared~p2018-06-27~n50~cost20@50"
    assert names.model_name({**row, "検証方式": "閾値売買（翌日始値・費用7.5bp）"}).endswith("~exec-open~cost7p5")
    assert names.model_name({**row, "検証方式": "閾値売買"}) == "own-cs-ll.all.ridge.shared~p2018-06-27~n50"
