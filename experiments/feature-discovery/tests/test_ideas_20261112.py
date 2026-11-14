"""登録簿 `20261112-wiki-pageview-layer`（2026-11-13 の段 2・人の手）: 取得元 `wikimedia` と閲覧数の層 `attn_`。⚠ ネットワークに出ない。

⚠ 見るもの: 無い config は 1 ビットも変わらない（既存の登録名）／ URL と応答の読み方 ・ 題名ごとの系列を日ごとに足す ／
   その足の日 − 1 日までの閲覧数だけ（先の日を見ない）／ 欠けは印にして埋める（行を落とさない）／ 連絡先はコードにも対応表にも無い。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import ail.bootstrap  # noqa: F401
from ail import config, registry
from ail.data.sources import wikimedia as wm
from ail.ideas import i20261112_attn_layer as attn


def test_source_registered_url_parse_merge_and_no_contact_in_repo(monkeypatch):
    assert "wikimedia" in registry.available("source") and "attn" in registry.available("feature")
    for n in ("edgar", "ecb", "treasury"):
        assert n in registry.available("source")
    u = wm.url_of("Johnson & Johnson", "2018-01-01", "2018-01-31")
    assert u.endswith("/en.wikipedia.org/all-access/user/Johnson_%26_Johnson/daily/2018010100/2018013100")
    assert "AT%26T" in wm.url_of("AT&T", "2018-01-01", "2018-01-02") and "Amazon_%28company%29" in wm.url_of("Amazon (company)", "2018-01-01", "2018-01-02")
    doc = {"items": [{"timestamp": "2024030100", "views": 10}, {"timestamp": "2024030200", "views": 0}, {"timestamp": "bad"}]}
    assert wm.parse(doc) == {"2024-03-01": 10.0, "2024-03-02": 0.0}
    assert wm.merged([{"2024-03-01": 10.0}, {"2024-03-01": 5.0, "2024-03-02": 1.0}, {}]) == {"2024-03-01": 15.0, "2024-03-02": 1.0}
    monkeypatch.delenv(wm.CONTACT_ENV, raising=False)
    with pytest.raises(SystemExit):
        wm.contact()                                                            # 連絡先が無ければ 1 本も送らない
    for bad in ("<名前> <メール>", "名前 連絡先", "<name> <mail>"):                  # 見本のまま ・ 全角は 1 本も送らずに止める
        monkeypatch.setenv(wm.CONTACT_ENV, bad)
        with pytest.raises(SystemExit):
            wm.contact()
    monkeypatch.setenv(wm.CONTACT_ENV, "Some Name contact.example")
    assert wm.user_agent(wm.contact()) == "ai-income-lab-research/0.1 (Some Name contact.example) python-urllib"
    arts = wm.articles()
    company = set(config.universe("us63")["groups"]["company"])
    assert set(arts) == company and all(v and all("@" not in t for t in v) for v in arts.values())   # 会社 48 本 ・ ETF は無い
    assert arts["META"] == ["Meta Platforms", "Facebook, Inc."]
    ds = config.dataset("attention_daily")
    assert ds["series"][0]["source"] == "wikimedia" and ds["series"][0]["ids"] == ["PV"] and "@" not in open(wm.ARTICLES, encoding="utf-8").read()


def _views(start="2023-10-01", n=200, seed=0, level=1000.0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="D")
    return pd.Series(level * np.exp(rng.normal(0, 0.2, n)), index=idx)


def _bars(start="2024-01-02", n=40):
    return pd.DataFrame({"ts": pd.date_range(start, periods=n, freq="B", tz="UTC"), "close": 100.0})


def test_attn_layer_uses_views_up_to_the_day_before_and_flags_gaps():
    v = _views()
    bars = _bars()
    x = attn.build_one(bars, v, lag_days=1)
    assert list(x.columns) == list(attn.COLUMNS) and len(x) == len(bars) and not x.isna().any().any() and (x["na"] == 0).all()
    # 定義どおり: 足の日 − 1 日の暦日で、z はその日より前の 60 日に対して・比はその日を含む 5 日 ÷ 20 日
    t = 25
    a = (bars["ts"].iloc[t].tz_convert(None).normalize() - pd.Timedelta(days=1))
    lv = np.log1p(v)
    prev = lv[(lv.index < a) & (lv.index >= a - pd.Timedelta(days=60))]
    assert len(prev) == 60
    assert np.isclose(x["z60"].iloc[t], (lv[a] - prev.mean()) / prev.std())
    assert np.isclose(x["r5_20"].iloc[t], v[a - pd.Timedelta(days=4):a].mean() / v[a - pd.Timedelta(days=19):a].mean())
    # 先の日の閲覧数を変えても、前の足の列は 1 ビットも変わらない（足の当日の閲覧数も使わない）
    v2 = v.copy()
    cut_day = bars["ts"].iloc[20].tz_convert(None).normalize()
    v2[v2.index >= cut_day] *= 50.0
    x2 = attn.build_one(bars, v2, lag_days=1)
    assert x.iloc[:21].equals(x2.iloc[:21]) and not x.iloc[21:].equals(x2.iloc[21:])
    # 欠け: 系列が無い ／ 過去の日数が足りない ／ 途中に無い日がある → 印 1 ・ 値は z 0 ・ 比 1（行は落とさない）
    x0 = attn.build_one(bars, None, lag_days=1)
    assert (x0["na"] == 1).all() and (x0["z60"] == 0).all() and (x0["r5_20"] == attn.R_FILL).all()
    short = attn.build_one(bars, _views("2023-12-20", 60), lag_days=1)
    assert short["na"].iloc[0] == 1 and short["z60"].iloc[0] == 0
    hole = v.drop(v.index[110])                                                  # 1 日ぶんの穴（2024-01-19）
    xh = attn.build_one(bars, hole, lag_days=1)
    assert xh["na"].sum() >= 1 and len(xh) == len(bars)
    spike = v.copy()
    spike.iloc[-1] = 1e12
    assert attn.daily_features(spike)["z60"].iloc[-1] == attn.Z_CLIP             # 上限で切る


def test_attn_layer_refuses_same_day_lag_and_keeps_layer_order(monkeypatch):
    from cli import build as build_mod
    assert build_mod.layer_order(["own", "attn"]) == ["own", "attn"] and "attn" not in build_mod.ORDER
    monkeypatch.setattr(attn, "load_symbol_series", lambda s: _views() if s == "AAA" else None)
    out = attn.layer({"AAA": _bars(), "BBB": _bars()}, {})
    assert list(out["AAA"].columns) == [attn.PREFIX + c for c in attn.COLUMNS] and out["BBB"]["attn_na"].eq(1).all() and out["AAA"]["attn_na"].eq(0).all()
    with pytest.raises(ValueError):
        attn.layer({"AAA": _bars()}, {"attn_lag_days": 0})
