"""`cli/predict.py`（実売買の「今日の買い%」）: 決定性・先読みをしない・3 人のモデルが通る・代役の足。

⚠ **本物の `data/` を読まない**（`store.DATA` を tmp に向け、作り物の足を置く）。⚠ **`runs/` も作らない**。
"""

from __future__ import annotations

import os
import shutil

import numpy as np
import pandas as pd
import pytest

from ail import config
from ail.data import store
from cli import predict as P

ASOF = "2026-08-14"
DAYS = pd.bdate_range("2025-03-03", "2026-08-31", tz="UTC")     # asof の後ろにも足がある（先読みの検査に使う）

T1 = ("trade_own_ridge_a", "全部使う（基準）")
T2 = ("trade_ownex_lgbm_a", "全部使う（基準）")
T3 = ("trade_ownseq_ridge_a", "T3 QUANT（60日窓）")


def _bars(seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0003, 0.015, len(DAYS))
    c = 50.0 * np.exp(np.cumsum(r))
    o = c * np.exp(rng.normal(0, 0.004, len(DAYS)))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.004, len(DAYS))))
    l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.004, len(DAYS))))
    v = rng.integers(1_000_000, 5_000_000, len(DAYS)).astype(float)
    return pd.DataFrame({"time_ms": (DAYS.asi8 // 1_000_000), "open": o, "high": h, "low": l, "close": c, "volume": v})


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    real = store.DATA
    monkeypatch.setattr(store, "DATA", str(tmp_path))
    symbols = config.symbols_of(config.dataset("daily")["universe"])
    for i, s in enumerate(symbols):
        store.write_bars(store.adjusted_dir("d"), s, _bars(i))
    # ⚠ 外部系列（T2 の ex 層）は本物の写しを使う（作り物にすると系列の一覧を二重に持つことになる）。無ければ T2 は飛ばす
    for src in ("ecb", "treasury", "noaa", "usgs"):
        d = os.path.join(real, "raw", src, "series")
        if os.path.isdir(d):
            shutil.copytree(d, os.path.join(str(tmp_path), "raw", src, "series"))
    return tmp_path


def _key(rows):
    return [(r["symbol"], r["buy"], r["exit"]) for r in rows]


def test_same_input_gives_the_same_output(data_dir):
    a, ma = P.predict(*T1[:1], ASOF, T1[1])
    b, mb = P.predict(*T1[:1], ASOF, T1[1])
    assert _key(a) == _key(b) and ma["input_fingerprint"] == mb["input_fingerprint"]
    assert len(a) == 63 and all(0.0 <= r["buy"] <= 100.0 and abs(r["buy"] + r["exit"] - 100.0) < 1e-4 for r in a)
    assert all(r["date"] == ASOF and r["model"] == T1[0] and r["method"] == T1[1] for r in a)


def test_training_stops_before_the_label_touches_asof(data_dir):
    _rows, meta = P.predict(*T1[:1], ASOF, T1[1])
    # 2026-08-14（金）の前の営業日 08-13 の行はラベルが asof の終値を含む ＝ 訓練に入らない
    assert meta["train_end"] == "2026-08-12"


@pytest.mark.parametrize("exp,method", [T1, T3])
def test_future_bars_do_not_change_the_output(data_dir, exp, method):
    """`asof` より後の足をどう壊しても出力が変わらない（＝ `asof` の行の y も、その先も見ていない）。"""
    before, m0 = P.predict(exp, ASOF, method)
    cut = int(pd.Timestamp(ASOF, tz="UTC").value // 1_000_000)
    for s in config.symbols_of(config.dataset("daily")["universe"]):
        # ⚠ **`asof` までの行は 1 バイトも変えない**（CSV を読んで書き直すと、pandas の既定の読み方では
        # 末尾の桁が 1 ulp 動くことがある ＝ 先読みではないのに指紋が変わる）。後ろの行だけ文字のまま書き換える
        path = store.path_of(store.adjusted_dir("d"), s)
        lines = open(path, encoding="utf-8").read().splitlines()
        out = [lines[0]]
        for line in lines[1:]:
            f = line.split(",")
            if int(f[0]) > cut:
                f = [f[0]] + [repr(float(x) * 3.0) for x in f[1:5]] + [repr(float(f[5]) * 7.0)]
            out.append(",".join(f))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out) + "\n")
    after, m1 = P.predict(exp, ASOF, method)
    assert _key(before) == _key(after) and m0["input_fingerprint"] == m1["input_fingerprint"]


def test_asof_close_does_change_the_output(data_dir):
    """逆向きの対照: `asof` の足（終値の代役）は効く。効かなければ今日の行を読めていない。"""
    before, _ = P.predict(*T1[:1], ASOF, T1[1])
    b = store.read_bars(store.adjusted_dir("d"), "SPY")
    row = b[b["ts"] == pd.Timestamp(ASOF, tz="UTC")].iloc[0]
    proxy = {"SPY": {"open": row["open"], "high": row["high"] * 1.05, "low": row["low"],
                     "close": row["close"] * 1.05, "volume": row["volume"]}}
    after, meta = P.predict(*T1[:1], ASOF, T1[1], proxy=proxy)
    assert meta["proxy_symbols"] == ["SPY"]
    d0 = {r["symbol"]: r["buy"] for r in before}
    d1 = {r["symbol"]: r["buy"] for r in after}
    assert d0["SPY"] != d1["SPY"]
    assert all(d0[s] == d1[s] for s in d0 if s != "SPY")        # own 層だけなので他の銘柄は動かない


def test_proxy_equal_to_the_bar_is_a_noop_and_can_append_a_missing_day(data_dir):
    base, _ = P.predict(*T1[:1], ASOF, T1[1])
    a = pd.Timestamp(ASOF, tz="UTC")
    proxy = {}
    for s in config.symbols_of(config.dataset("daily")["universe"]):
        b = store.read_bars(store.adjusted_dir("d"), s)
        row = b[b["ts"] == a].iloc[0]
        proxy[s] = {c: float(row[c]) for c in ("open", "high", "low", "close", "volume")}
        store.write_bars(store.adjusted_dir("d"), s, b[b["ts"] < a])      # 置き場は前の日まで（実売買の 15:50 の形）
    with pytest.raises(SystemExit):
        P.predict(*T1[:1], ASOF, T1[1])                                   # 今日の足が無ければ止まる
    again, meta = P.predict(*T1[:1], ASOF, T1[1], proxy=proxy)
    assert len(meta["proxy_symbols"]) == 63 and all(r["proxy"] for r in again)
    assert [(r["symbol"], round(r["buy"], 4)) for r in again] == [(r["symbol"], round(r["buy"], 4)) for r in base]


def test_lgbm_trader_predicts_company_stocks_only(data_dir):
    if not os.path.isdir(os.path.join(str(data_dir), "raw", "ecb", "series")):
        pytest.skip("外部系列の写しが無い")
    rows, meta = P.predict(*T2[:1], ASOF, T2[1])
    company = set(config.universe(config.dataset("daily")["universe"])["groups"]["company"])
    assert rows and {r["symbol"] for r in rows} <= company


def test_method_must_be_named_when_there_are_several(data_dir):
    with pytest.raises(SystemExit):
        P.predict(T3[0], ASOF, None)
    with pytest.raises(SystemExit):
        P.predict(T3[0], ASOF, "無い手法")


def test_write_rows_replaces_the_same_day_and_model(tmp_path):
    path = str(tmp_path / "predict.jsonl")
    P.write_rows(path, [{"date": "d1", "model": "a", "symbol": "X", "buy": 1.0, "exit": 99.0}])
    P.write_rows(path, [{"date": "d1", "model": "b", "symbol": "X", "buy": 2.0, "exit": 98.0}])
    P.write_rows(path, [{"date": "d1", "model": "a", "symbol": "X", "buy": 3.0, "exit": 97.0}])
    lines = [l for l in open(path, encoding="utf-8").read().splitlines() if l]
    assert len(lines) == 2 and '"buy": 3.0' in lines[1] and '"model": "b"' in lines[0]
