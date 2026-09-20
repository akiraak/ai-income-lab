"""シミュレーションの仮データ ＝ 取得済みの実データの日足の再生（live-trading.md §0-7 (c)・(d)）。

  - 値段: `feature-discovery/data/adjusted/d/<銘柄>.csv` の終値。`source_start` から営業日の順に 1 本ずつ、仮の期間の営業日へ当てる
    （k 番目の仮の営業日 ＝ 出どころの k 番目の足）。⚠ **日付は仮・値段は過去の実物** ＝ 損益に意味は無い
  - 合図（⚠ 成績のためのものではない。売買が適度に起きればよい）:
        mN の買い% ＝ 50 ＋ 10 ×（終値 − N 日移動平均）÷ N 日標準偏差 を 0〜100 に切る ／ 出口% ＝ 100 − 買い%
    その日の終値（＝ 15:50 の気配の代役）まで使う。標準偏差は標本（N−1）。0 なら 50
  - 決定的（乱数なし）。新しい取得はしない
"""

from __future__ import annotations

import csv
import json
import os
import statistics
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))
if SAMPLE_DIR not in sys.path:
    sys.path.insert(0, SAMPLE_DIR)

import market_calendar  # noqa: E402

DATA_DIR = os.environ.get("LT_SIM_DATA_DIR") or os.path.normpath(os.path.join(HERE, "..", "feature-discovery", "data", "adjusted", "d"))
SIM_CONFIG_DIR = os.environ.get("LT_SIM_CONFIG_DIR") or os.path.join(HERE, "config", "sim")


@dataclass
class SimConfig:
    name: str
    traders: list[str]
    start: date
    end: date
    source_start: date
    windows: list[str] = field(default_factory=lambda: ["15:45:30"])
    speed: int | str = 60
    skip_outside_window: bool = True
    spread_bp: float = 2.0
    fill_noise: float = 0.003
    seed: int = 0
    events: list[dict] = field(default_factory=list)   # 筋書き（故障の注入）。Phase 3


def load_config(name: str, config_dir: str | None = None) -> SimConfig:
    with open(os.path.join(config_dir or SIM_CONFIG_DIR, f"{name}.toml"), "rb") as f:
        doc = tomllib.load(f)
    if doc.get("name") != name:
        raise ValueError(f"config/sim/{name}.toml の name が {doc.get('name')!r}")
    return SimConfig(name=name, traders=list(doc["traders"]), start=date.fromisoformat(doc["start"]), end=date.fromisoformat(doc["end"]),
                     source_start=date.fromisoformat(doc["source_start"]), windows=list(doc.get("windows") or ["15:45:30"]),
                     speed=doc.get("speed", 60), skip_outside_window=bool(doc.get("skip_outside_window", True)),
                     spread_bp=float(doc.get("spread_bp", 2.0)), fill_noise=float(doc.get("fill_noise", 0.003)), seed=int(doc.get("seed", 0)),
                     events=list(doc.get("events") or []))


def sim_days(cfg: SimConfig) -> list[date]:
    """仮の期間の営業日（半日立会の日も入る ＝ 執行器が「発注しない」と記録する日。休場日は入らない）。"""
    return market_calendar.nyse().trading_days(cfg.start, cfg.end)


def load_closes(symbol: str, data_dir: str = DATA_DIR) -> list[tuple[date, float]]:
    out = []
    # ファイル名は `BRK/B` → `BRK-B.csv`（feature-discovery の流儀。paper.py と同じ）
    with open(os.path.join(data_dir, f"{symbol.replace('/', '-')}.csv"), encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            out.append((datetime.fromtimestamp(int(row["time_ms"]) / 1000, timezone.utc).date(), float(row["close"])))
    return out


def signal(closes: list[float], n: int) -> float:
    """買い%。closes は古い順で、最後がその日の終値。"""
    if len(closes) < n:
        return 50.0
    window = closes[-n:]
    sd = statistics.stdev(window)
    if sd == 0:
        return 50.0
    return max(0.0, min(100.0, 50.0 + 10.0 * (window[-1] - statistics.fmean(window)) / sd))


def build(cfg: SimConfig, symbols: list[str], lookbacks=(20, 10), data_dir: str = DATA_DIR) -> dict:
    """{"days": [仮の日付…], "quotes": {日付: {銘柄: 終値}}, "signals": {N: [(日付, 銘柄, 買い%, 出口%)…]}, "source": {日付: 出どころの日付}}"""
    days = sim_days(cfg)
    quotes: dict[str, dict[str, float]] = {d.isoformat(): {} for d in days}
    signals: dict[int, list[tuple]] = {n: [] for n in lookbacks}
    source: dict[str, str] = {}
    for sym in symbols:
        bars = load_closes(sym, data_dir)
        first = next((i for i, (d, _) in enumerate(bars) if d >= cfg.source_start), None)
        if first is None or len(bars) - first < len(days):
            have = 0 if first is None else len(bars) - first
            raise ValueError(f"{sym}: {cfg.source_start} からの足が {have} 本しかない（仮の営業日は {len(days)} 日）。日足を取り直すか期間を縮める")
        for k, day in enumerate(days):
            src_day, close = bars[first + k]
            quotes[day.isoformat()][sym] = close
            source.setdefault(day.isoformat(), src_day.isoformat())
            history = [c for _, c in bars[: first + k + 1]]
            for n in lookbacks:
                buy = round(signal(history, n), 2)
                signals[n].append((day.isoformat(), sym, buy, round(100.0 - buy, 2)))
    return {"days": [d.isoformat() for d in days], "quotes": quotes, "signals": signals, "source": source}


def write_tree(root: str, cfg: SimConfig, traders_dir: str, data_dir: str = DATA_DIR) -> dict:
    """シミュレーションの木に、トレーダーの設定の写し・合図の CSV・日ごとの気配を書く。⚠ `sim_` で始まらないトレーダーは置けない。"""
    import shutil

    from mode import SIM_TRADER_PREFIX
    from trader import load_traders

    bad = [t for t in cfg.traders if not t.startswith(SIM_TRADER_PREFIX)]
    if bad:
        raise ValueError(f"シミュレーションのトレーダーは名前が {SIM_TRADER_PREFIX} で始まること: {bad}")
    traders = load_traders(cfg.traders, traders_dir)
    symbols = sorted({s for t in traders for s in t.symbols})
    data = build(cfg, symbols, data_dir=data_dir)
    os.makedirs(os.path.join(root, "config", "traders"), exist_ok=True)
    os.makedirs(os.path.join(root, "config", "signals"), exist_ok=True)
    os.makedirs(os.path.join(root, "sim"), exist_ok=True)
    for name in cfg.traders:
        shutil.copyfile(os.path.join(traders_dir, f"{name}.toml"), os.path.join(root, "config", "traders", f"{name}.toml"))
    for n, rows in data["signals"].items():
        with open(os.path.join(root, "config", "signals", f"sim_m{n}.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "symbol", "buy", "exit"])
            w.writerows(sorted(rows))
    with open(os.path.join(root, "sim", "data.json"), "w", encoding="utf-8") as f:
        json.dump({"days": data["days"], "quotes": data["quotes"], "source": data["source"], "symbols": symbols}, f, ensure_ascii=False)
    return data
