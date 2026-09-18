"""各モデルの「今日の買い% ／ 出口%」を集め、トレーダーごとに合成する。

出力は 2 段:
  predict 行: モデル × 銘柄（`predict.jsonl`。experiment はここに `predict.py` が書いた行を読む）
  signal 行:  トレーダー × 銘柄（合成後の買い% ／ 出口%。`signals.jsonl`）
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass

from trader import ModelSpec, Trader, combine


@dataclass(frozen=True)
class Signal:
    trader: str
    symbol: str
    buy: float
    exit: float
    inputs: tuple[tuple[str, float, float], ...]  # (モデル名, 買い%, 出口%)
    test: bool


class SignalError(RuntimeError):
    pass


def _read_file_model(spec: ModelSpec, date: str) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    with open(spec.path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("date") == date:
                out[row["symbol"].strip()] = (float(row["buy"]), float(row["exit"]))
    return out


def _read_predict_rows(path: str) -> dict[tuple[str, str], tuple[float, float]]:
    out: dict[tuple[str, str], tuple[float, float]] = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            out[(row["model"], row["symbol"])] = (float(row["buy"]), float(row["exit"]))
    return out


def model_outputs(spec: ModelSpec, symbols: tuple[str, ...], date: str, predict_path: str | None) -> dict[str, tuple[float, float]]:
    """1 モデルの銘柄 → (買い%, 出口%)。無い銘柄は返さない（呼ぶ側が「合図なし」として記録する）。"""
    if spec.kind == "fixed":
        return {s: (float(spec.buy), float(spec.exit)) for s in symbols}
    if spec.kind == "file":
        rows = _read_file_model(spec, date)
        return {s: rows[s] for s in symbols if s in rows}
    if spec.kind == "experiment":
        if not predict_path:
            raise SignalError(f"experiment モデル {spec.name} には predict.jsonl が要る（Phase 1 の predict.py が書く）")
        rows = _read_predict_rows(predict_path)
        return {s: rows[(spec.name, s)] for s in symbols if (spec.name, s) in rows}
    raise SignalError(f"モデル kind {spec.kind!r} は無い")


def collect(traders: list[Trader], date: str, predict_path: str | None = None) -> tuple[list[Signal], list[dict]]:
    """全トレーダーの合図。戻り値は (合図の一覧, 合図が出せなかった事象の一覧)。

    ⚠ **同じモデルを 2 人が使っても読むのは 1 回**（experiment は predict.jsonl の同じ行）。
    """
    signals: list[Signal] = []
    events: list[dict] = []
    cache: dict[tuple, dict[str, tuple[float, float]]] = {}
    for t in traders:
        for s in t.symbols:
            inputs = []
            missing = []
            for m in t.models:
                key = (m.kind, m.name, m.path, t.symbols)
                if key not in cache:
                    cache[key] = model_outputs(m, t.symbols, date, predict_path)
                got = cache[key].get(s)
                if got is None:
                    missing.append(m.name)
                else:
                    inputs.append((m.name, got[0], got[1]))
            if missing:
                events.append({"kind": "no_signal", "trader": t.name, "symbol": s, "models": missing, "date": date})
                continue
            buy, exit_ = combine(t.combine, [b for _, b, _ in inputs], [e for _, _, e in inputs], t.threshold)
            signals.append(Signal(t.name, s, buy, exit_, tuple(inputs), t.test))
    return signals, events
