"""各モデルの「今日の買い% ／ 出口%」を集め、トレーダーごとに合成する。

出力は 2 段:
  predict 行: モデル × 銘柄（`predict.jsonl`。experiment はここに `predict.py` が書いた行を読む）
  signal 行:  トレーダー × 銘柄（合成後の買い% ／ 出口%。`signals.jsonl`）

⚠ 2026-10-18（プラン trader-model-roles.md §4 の 2）: predict の行は **(実験名, 手法, 銘柄)** で引く（買い係と売り係が同じ実験名の別の手法になる）。
役割（`trader.ROLES`）のある人は 買いの側 ／ 売りの側 ／ 損切り係 に分けて合成し、見るだけの係は events に 1 行残す。
⚠ **役割の無い人（`Trader.has_roles` が False）はいままでの経路をそのまま通る**（合図の値・記録の形とも 1 ビットも変わらない）。
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass

from _livefs import livefs
from trader import ModelSpec, Trader, combine


@dataclass(frozen=True)
class Signal:
    trader: str
    symbol: str
    buy: float
    exit: float
    inputs: tuple[tuple, ...]   # (モデル名, 買い%, 出口%)。役割のある人は (モデル名, 買い%, 出口%, 役割, 手法)
    test: bool
    stop: float | None = None   # 毎日読む出口%（損切り係の最大）。損切り係の無い人は None


class SignalError(RuntimeError):
    pass


def _read_file_model(spec: ModelSpec, date: str) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    with open(spec.path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("date") == date:
                out[row["symbol"].strip()] = (float(row["buy"]), float(row["exit"]))
    return out


def _read_predict_rows(path: str, date: str | None = None) -> dict[tuple[str, str], dict[str | None, tuple[float, float]]]:
    """(モデル, 銘柄) → {手法: (買い%, 出口%)}。⚠ **行に日付があり、今日と違えば読まない**（古い予測で売買しない）。"""
    out: dict[tuple[str, str], dict[str | None, tuple[float, float]]] = {}
    for line in livefs.read_lines(path):         # ⚠ 2026-09-21 から DB の lines（run-live.sh が `livefs.py append` で入れる）
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if date is not None and row.get("date") not in (None, date):
            continue
        out.setdefault((row["model"], row["symbol"]), {})[row.get("method")] = (float(row["buy"]), float(row["exit"]))
    return out


def model_outputs(spec: ModelSpec, symbols: tuple[str, ...], date: str, predict_path: str | None) -> dict[str, tuple[float, float]]:
    """1 モデルの銘柄 → (買い%, 出口%)。無い銘柄は返さない（呼ぶ側が「合図なし」として記録する）。

    experiment は (実験名, 手法, 銘柄) で引く。設定に `method` があれば**その手法の行だけ**を取り、その銘柄に別の手法の行しか無ければ止まる
    （設定と予測の食い違いを黙って読まない）。`method` が無ければ 1 本だけある手法を取り、2 本以上あれば止まる（どれを使うか決められない）。
    """
    if spec.kind == "fixed":
        return {s: (float(spec.buy), float(spec.exit)) for s in symbols}
    if spec.kind == "file":
        rows = _read_file_model(spec, date)
        return {s: rows[s] for s in symbols if s in rows}
    if spec.kind == "experiment":
        if not predict_path:
            raise SignalError(f"experiment モデル {spec.name} には predict.jsonl が要る（Phase 1 の predict.py が書く）")
        rows = _read_predict_rows(predict_path, date)
        got: dict[str, tuple[float, float]] = {}
        for s in symbols:
            by_method = rows.get((spec.name, s)) or {}
            if not by_method:
                continue
            if spec.method is not None:
                if spec.method not in by_method:
                    raise SignalError(f"experiment モデル {spec.name}: predict.jsonl の手法 {sorted(by_method, key=str)} が設定の {spec.method!r} と違う")
                got[s] = by_method[spec.method]
            elif len(by_method) > 1:
                raise SignalError(f"experiment モデル {spec.name}: predict.jsonl に {s} の手法が {len(by_method)} 本ある {sorted(by_method, key=str)}（設定に method を書く）")
            else:
                got[s] = next(iter(by_method.values()))
        return got
    raise SignalError(f"モデル kind {spec.kind!r} は無い")


def _would(buy: float, exit_: float, threshold: float, position: int | None) -> str | None:
    """見るだけの係 1 本なら今日どうするか（買う ／ 売る ／ 持つ ／ 見送る）。持ち株が分からなければ None。"""
    if position is None:
        return None
    if position == 0:
        return "buy" if buy > threshold else "skip"
    return "sell" if exit_ > threshold else "hold"


def collect(traders: list[Trader], date: str, predict_path: str | None = None,
            positions: dict[tuple[str, str], int] | None = None) -> tuple[list[Signal], list[dict]]:
    """全トレーダーの合図。戻り値は (合図の一覧, 合図が出せなかった事象 ＋ 見るだけの 1 行)。

    ⚠ **同じモデルを 2 人が使っても読むのは 1 回**（experiment は predict.jsonl の同じ行）。
    `positions` は (トレーダー, 銘柄) → 持ち株 0 ／ 1（見るだけの係の「この係 1 本なら」に使う。無ければ would を書かない）。
    """
    signals: list[Signal] = []
    events: list[dict] = []
    cache: dict[tuple, dict[str, tuple[float, float]]] = {}
    for t in traders:
        for s in t.symbols:
            outs: dict[int, tuple[float, float]] = {}
            missing = []
            for i, m in enumerate(t.models):
                key = (m.kind, m.name, m.path, m.method, t.symbols)
                if key not in cache:
                    cache[key] = model_outputs(m, t.symbols, date, predict_path)
                got = cache[key].get(s)
                if got is None:
                    if m.role == "watch":    # 見るだけの係が無くても売買は止めない（判断を変えない）
                        events.append({"kind": "watch_no_signal", "trader": t.name, "symbol": s, "model": m.name, "method": m.method, "date": date})
                    else:
                        missing.append(m.name)
                else:
                    outs[i] = got
            if missing:
                events.append({"kind": "no_signal", "trader": t.name, "symbol": s, "models": missing, "date": date})
                continue
            if not t.has_roles:
                inputs = tuple((m.name, outs[i][0], outs[i][1]) for i, m in enumerate(t.models))
                buy, exit_ = combine(t.combine, [b for _, b, _ in inputs], [e for _, _, e in inputs], t.threshold)
                signals.append(Signal(t.name, s, buy, exit_, inputs, t.test))
                continue
            # ---- 役割のある人: 側ごとに合わせる（プラン §4 の 2・rules.md 25-2 の 2 と同じ向き）
            picked = [(i, m) for i, m in enumerate(t.models) if i in outs]
            bs = [(i, m) for i, m in picked if m.role in ("both", "buy")]
            es = [(i, m) for i, m in picked if m.role in ("both", "exit")]
            ss = [(i, m) for i, m in picked if m.role == "stop"]
            buy, _ = combine(t.buy_rule, [outs[i][0] for i, _ in bs], [outs[i][1] for i, _ in bs], t.threshold)
            if es:
                _, exit_ = combine(t.exit_rule, [outs[i][0] for i, _ in es], [outs[i][1] for i, _ in es], t.threshold)
            else:
                exit_ = 0.0                                   # 売り係が無い人（損切り係だけで降りる）＝ 出口% は読まれない
            stop = max(outs[i][1] for i, _ in ss) if ss else None
            inputs = tuple((m.name, outs[i][0], outs[i][1], m.role, m.method) for i, m in picked)
            for i, m in picked:
                if m.role == "watch":
                    pos = None if positions is None else positions.get((t.name, s))
                    events.append({"kind": "watch", "trader": t.name, "symbol": s, "model": m.name, "method": m.method,
                                   "buy": outs[i][0], "exit": outs[i][1], "would": _would(outs[i][0], outs[i][1], t.threshold, pos),
                                   "date": date, "note": "見るだけ（この係 1 本なら今日どうするか。注文・予算・売買履歴には効かない）"})
            signals.append(Signal(t.name, s, buy, exit_, inputs, t.test, stop))
    return signals, events
