"""トレーダー（Trader）— 予算・銘柄集合・1 本以上の予測モデル・合成規則・閾値を持つ執行の単位。

プラン docs/plans/live-trading-three-models.md §2-1。定義は `config/traders/<名前>.toml` に 1 人 1 ファイル。

⚠ **合成規則は事前固定**（結果を見て変えるのは新しい試行）。⚠ **モデル 1 本のときは「そのまま」に退化し、
既存の閾値売買（feature-discovery rules.md 13-4）と完全に一致する。**

モデルの種類（`kind`）:
  fixed       買い% ／ 出口% を設定値で返す（試験用。予測モデルを呼ばない）
  file        CSV（date,symbol,buy,exit）から日付と銘柄で引く（試験用。手書きの合図・本番の最小額テスト）
  experiment  feature-discovery の実験名。`out/<日付>/predict.jsonl` の行（Phase 1 の `predict.py` が書く）を読む
⚠ **fixed / file のトレーダーは `test = true` を必ず持ち、記録に `test: true` が付く**（判定に混ぜない）。
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
TRADERS_DIR = os.path.join(HERE, "config", "traders")
UNIVERSE_DIR = os.path.normpath(os.path.join(HERE, "..", "feature-discovery", "config", "universe"))

COMBINE_RULES = ("asis", "mean", "majority", "unanimous")
MODEL_KINDS = ("fixed", "file", "experiment")
SIZINGS = ("shares", "notional")


@dataclass(frozen=True)
class ModelSpec:
    kind: str
    name: str
    buy: float | None = None   # fixed
    exit: float | None = None  # fixed
    path: str | None = None    # file
    method: str | None = None  # experiment: 実験の中の手法（例 "T3 QUANT（60日窓）"）。predict.jsonl の行の method と突き合わせる

    @property
    def is_test(self) -> bool:
        return self.kind in ("fixed", "file")


@dataclass(frozen=True)
class Trader:
    name: str
    budget_usd: float
    symbols: tuple[str, ...]
    models: tuple[ModelSpec, ...]
    combine: str = "asis"
    threshold: float = 50.0
    sizing: str = "shares"      # shares: 整数株 ／ notional: 金額指定（Notional Market。dryrun2 で通ったときだけ）
    test: bool = False
    universe: str | None = None
    note: str = ""
    source_path: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def per_symbol_usd(self) -> float:
        """等加重: 予算 ÷ 銘柄数（rules.md 13-7 と同じ割り当て）。"""
        return self.budget_usd / len(self.symbols) if self.symbols else 0.0

    def validate(self) -> None:
        if not self.name or "/" in self.name:
            raise ValueError(f"トレーダー名が不正: {self.name!r}")
        if self.budget_usd <= 0:
            raise ValueError(f"{self.name}: 予算は正の数（{self.budget_usd}）")
        if not self.symbols:
            raise ValueError(f"{self.name}: 銘柄集合が空")
        if len(set(self.symbols)) != len(self.symbols):
            raise ValueError(f"{self.name}: 銘柄が重複")
        if not self.models:
            raise ValueError(f"{self.name}: モデルが 0 本")
        if self.combine not in COMBINE_RULES:
            raise ValueError(f"{self.name}: 合成規則 {self.combine!r} は無い（{COMBINE_RULES}）")
        if self.combine == "asis" and len(self.models) != 1:
            raise ValueError(f"{self.name}: 「そのまま」はモデル 1 本のときだけ（{len(self.models)} 本）")
        if self.combine == "majority" and len(self.models) % 2 == 0:
            raise ValueError(f"{self.name}: 多数決はモデルの本数が奇数のとき（{len(self.models)} 本）")
        if self.threshold < 50.0:
            raise ValueError(f"{self.name}: θ は 50% 以上だけ（rules.md 13-3 の 2。{self.threshold}）")
        if self.sizing not in SIZINGS:
            raise ValueError(f"{self.name}: sizing {self.sizing!r} は無い（{SIZINGS}）")
        for m in self.models:
            if m.kind not in MODEL_KINDS:
                raise ValueError(f"{self.name}: モデル kind {m.kind!r} は無い（{MODEL_KINDS}）")
            if m.kind == "fixed" and (m.buy is None or m.exit is None):
                raise ValueError(f"{self.name}: fixed は buy と exit が要る")
            if m.kind == "file" and not m.path:
                raise ValueError(f"{self.name}: file は path が要る")
            if m.kind == "experiment" and not m.name:
                raise ValueError(f"{self.name}: experiment は name が要る")
        if any(m.is_test for m in self.models) and not self.test:
            raise ValueError(f"{self.name}: fixed / file のモデルを持つトレーダーは test = true が要る（記録を判定に混ぜないため）")


def load_universe(name: str, group: str | None = None) -> tuple[str, ...]:
    """feature-discovery の銘柄集合（`config/universe/<name>.toml` の groups を平らに）。

    `group` を渡すとその群だけ（例 us63 の `company` ＝ 会社株 48 本。T2 のモデルは ETF を予測しない）。
    """
    path = os.path.join(UNIVERSE_DIR, f"{name}.toml")
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    symbols: list[str] = []
    if group is not None:
        if group not in (doc.get("groups") or {}):
            raise ValueError(f"universe {name} に群 {group!r} は無い（{list((doc.get('groups') or {}))}）")
        return tuple(doc["groups"][group])
    for group in (doc.get("groups") or {}).values():
        symbols.extend(group)
    if not symbols:
        symbols = list(doc.get("symbols") or [])
    return tuple(symbols)


def parse_trader(doc: dict, name_hint: str = "", source_path: str = "") -> Trader:
    models = []
    for m in doc.get("models") or []:
        models.append(
            ModelSpec(
                kind=str(m.get("kind", "")),
                name=str(m.get("name", "") or m.get("kind", "")),
                buy=float(m["buy"]) if "buy" in m else None,
                exit=float(m["exit"]) if "exit" in m else None,
                path=(os.path.join(os.path.dirname(source_path), m["path"]) if source_path and not os.path.isabs(m.get("path", "")) else m.get("path")) if m.get("path") else None,
                method=str(m["method"]) if m.get("method") else None,
            )
        )
    universe = doc.get("universe")
    symbols = tuple(doc.get("symbols") or ())
    if universe and not symbols:
        symbols = load_universe(universe, doc.get("universe_group"))
    trader = Trader(
        name=str(doc.get("name") or name_hint),
        budget_usd=float(doc.get("budget_usd", 0)),
        symbols=symbols,
        models=tuple(models),
        combine=str(doc.get("combine", "asis")),
        threshold=float(doc.get("threshold", 50.0)),
        sizing=str(doc.get("sizing", "shares")),
        test=bool(doc.get("test", False)),
        universe=universe,
        note=str(doc.get("note", "")),
        source_path=source_path,
        extra={k: v for k, v in doc.items() if k not in ("name", "budget_usd", "symbols", "universe", "universe_group", "models", "combine", "threshold", "sizing", "test", "note")},
    )
    trader.validate()
    return trader


def load_trader(name: str, traders_dir: str = TRADERS_DIR) -> Trader:
    path = os.path.join(traders_dir, f"{name}.toml")
    with open(path, "rb") as f:
        doc = tomllib.load(f)
    return parse_trader(doc, name_hint=name, source_path=path)


def load_traders(names: list[str], traders_dir: str = TRADERS_DIR) -> list[Trader]:
    traders = [load_trader(n, traders_dir) for n in names]
    seen = set()
    for t in traders:
        if t.name in seen:
            raise ValueError(f"トレーダー名が重複: {t.name}")
        seen.add(t.name)
    return traders


# ---------------------------------------------------------------- 合成規則

def combine(rule: str, buys: list[float], exits: list[float], threshold: float) -> tuple[float, float]:
    """複数モデルの買い% ／ 出口% を 1 本にまとめる（プラン §2-1 の表）。

    戻り値は (買い%, 出口%)。その後ろは `buy > θ` ／ `exit > θ` で読む（状態機械。simulate と同じ向き）。
    多数決・全員一致は 0 か 100 に潰す（θ で 0/1 にした後の規則なので、% としての意味は持たない）。
    """
    if len(buys) != len(exits) or not buys:
        raise ValueError("買い% と出口% の本数が違う、または 0 本")
    if rule == "asis":
        if len(buys) != 1:
            raise ValueError("「そのまま」はモデル 1 本のときだけ")
        return float(buys[0]), float(exits[0])
    if rule == "mean":
        return sum(buys) / len(buys), sum(exits) / len(exits)
    if rule == "majority":
        nb = sum(1 for b in buys if b > threshold)
        ne = sum(1 for e in exits if e > threshold)
        half = len(buys) / 2
        return (100.0 if nb > half else 0.0), (100.0 if ne > half else 0.0)
    if rule == "unanimous":
        # 全モデルが θ を超えたときだけ買う ＝ min > θ。出口は 1 本でも超えたら手仕舞う ＝ max > θ
        return min(buys), max(exits)
    raise ValueError(f"合成規則 {rule!r} は無い（{COMBINE_RULES}）")
