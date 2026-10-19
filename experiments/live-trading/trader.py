"""トレーダー（Trader）— 予算・銘柄集合・1 本以上の予測モデル・合成規則・閾値を持つ執行の単位。

プラン docs/plans/live-trading-three-models.md §2-1。定義は `config/traders/<名前>.toml` に 1 人 1 ファイル。

⚠ **合成規則は事前固定**（結果を見て変えるのは新しい試行）。⚠ **モデル 1 本のときは「そのまま」に退化し、
既存の閾値売買（feature-discovery rules.md 13-4）と完全に一致する。**

モデルの種類（`kind`）:
  fixed       買い% ／ 出口% を設定値で返す（試験用。予測モデルを呼ばない）
  file        CSV（date,symbol,buy,exit）から日付と銘柄で引く（試験用。手書きの合図・本番の最小額テスト）
  experiment  feature-discovery の実験名。`out/<日付>/predict.jsonl` の行（Phase 1 の `predict.py` が書く）を読む
⚠ **fixed / file のトレーダーは `test = true` を必ず持ち、記録に `test: true` が付く**（判定に混ぜない）。

役割（`[[models]] role`。2026-10-18 プラン docs/plans/trader-model-roles.md §4。rules.md 25 章の机上の形を執行器に写した）:
  both   買いにも売りにも入る（既定。⚠ `role` を書かない設定は 1 ビットも変わらない）
  buy    買い係 ＝ 買い% だけに入る（売りには入らない）
  exit   売り係 ＝ 出口% だけに入る。⚠ `min_hold_days` の中（建ててから d 営業日未満）は読まれない
  stop   損切り係 ＝ 出口% を「毎日読む出口%」（`stop`）に入れる（最大）。最短保有の中でも読む。買いには入らない
  watch  見るだけ ＝ 記録に 1 行残す（events の `watch`）。注文・予算・売買履歴に効かない
合わせ方は側ごと: `combine_buy`（買いの側 ＝ both ＋ buy）／ `combine_exit`（売りの側 ＝ both ＋ exit）。書かなければ `combine`。
損切り係は常に最大（1 本でも言えば降りる）。`stop_blocks_buy = true` は「損切り係が言っている日は買わない」（既定 false ＝ 25-4）。
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
ROLES = ("both", "buy", "exit", "stop", "watch")   # [[models]] role（既定 both）。プラン trader-model-roles.md §1・§4
ROLE_LABEL = {"both": "両方", "buy": "買い係", "exit": "売り係", "stop": "損切り係", "watch": "見るだけ"}


@dataclass(frozen=True)
class ModelSpec:
    kind: str
    name: str
    buy: float | None = None   # fixed
    exit: float | None = None  # fixed
    path: str | None = None    # file
    method: str | None = None  # experiment: 実験の中の手法（例 "T3 QUANT（60日窓）"）。predict.jsonl の行の method と突き合わせる
    role: str = "both"         # both ／ buy ／ exit ／ stop ／ watch（上の説明。既定 both ＝ いままでの形）

    @property
    def is_test(self) -> bool:
        return self.kind in ("fixed", "file")

    @property
    def label(self) -> str:
        """記録・事象で人が読む名前。手法があれば添える（買い係と売り係が同じ実験名の別の手法になるため）。"""
        return f"{self.name}〔{self.method}〕" if self.method else self.name


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
    combine_buy: str | None = None      # 買いの側（both ＋ buy）の合わせ方。None ＝ combine
    combine_exit: str | None = None     # 売りの側（both ＋ exit）の合わせ方。None ＝ combine
    min_hold_days: int | None = None    # 最短保有（営業日）。建ててからこの日数未満は売り係の出口% を読まない（損切り係は読む）
    stop_blocks_buy: bool = False       # 損切り係が出口を言っている日は買わない（rules.md 25-4。既定 false ＝ 候補の人も書かない ＝ K9 (a)）

    @property
    def per_symbol_usd(self) -> float:
        """等加重: 予算 ÷ 銘柄数（rules.md 13-7 と同じ割り当て）。"""
        return self.budget_usd / len(self.symbols) if self.symbols else 0.0

    # ---- 役割（プラン trader-model-roles.md §4 の 1）

    @property
    def has_roles(self) -> bool:
        """役割の口を 1 つでも使っているか。False の人は 2026-10-18 より前の経路をそのまま通る（1 ビットも変わらない）。"""
        return (any(m.role != "both" for m in self.models) or self.combine_buy is not None or self.combine_exit is not None
                or self.min_hold_days is not None or self.stop_blocks_buy)

    @property
    def buy_models(self) -> tuple[ModelSpec, ...]:
        return tuple(m for m in self.models if m.role in ("both", "buy"))

    @property
    def exit_models(self) -> tuple[ModelSpec, ...]:
        return tuple(m for m in self.models if m.role in ("both", "exit"))

    @property
    def stop_models(self) -> tuple[ModelSpec, ...]:
        return tuple(m for m in self.models if m.role == "stop")

    @property
    def watch_models(self) -> tuple[ModelSpec, ...]:
        return tuple(m for m in self.models if m.role == "watch")

    @property
    def buy_rule(self) -> str:
        return self.combine_buy or self.combine

    @property
    def exit_rule(self) -> str:
        return self.combine_exit or self.combine

    def _validate_roles(self) -> None:
        """役割を使う人の検査（プラン §4 の 1）: 買いの側・売りの側それぞれ 1 本以上 ／ 側ごとの合わせ方 ／ 最短保有は 1 以上の整数。"""
        if not self.buy_models:
            raise ValueError(f"{self.name}: 買いの側（both ／ buy）のモデルが 0 本（見るだけ・売り係だけでは売買できない）")
        if not self.exit_models and not self.stop_models:
            raise ValueError(f"{self.name}: 売りの側（both ／ exit ／ stop）のモデルが 0 本")
        for side, rule, n in (("買い", self.buy_rule, len(self.buy_models)), ("売り", self.exit_rule, len(self.exit_models))):
            if rule not in COMBINE_RULES:
                raise ValueError(f"{self.name}: {side}の側の合わせ方 {rule!r} は無い（{COMBINE_RULES}）")
            if n == 0:
                continue
            if rule == "asis" and n != 1:
                raise ValueError(f"{self.name}: {side}の側の「そのまま」はモデル 1 本のときだけ（{n} 本）")
            if rule == "majority" and n % 2 == 0:
                raise ValueError(f"{self.name}: {side}の側の多数決はモデルの本数が奇数のとき（{n} 本）")
        if self.min_hold_days is not None and (isinstance(self.min_hold_days, bool) or not isinstance(self.min_hold_days, int) or self.min_hold_days < 1):
            raise ValueError(f"{self.name}: min_hold_days は 1 以上の整数（{self.min_hold_days!r}）")
        if self.stop_blocks_buy and not self.stop_models:
            raise ValueError(f"{self.name}: stop_blocks_buy は損切り係（role = stop）が 1 本以上あるときだけ（rules.md 25-4 の 2）")

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
        for m in self.models:
            if m.role not in ROLES:
                raise ValueError(f"{self.name}: モデル {m.name} の role {m.role!r} は無い（{ROLES}）")
        if self.has_roles:
            self._validate_roles()          # 側ごとに見る（全体の本数では見ない）
        else:
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
                role=str(m.get("role", "both")),
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
        extra={k: v for k, v in doc.items() if k not in ("name", "budget_usd", "symbols", "universe", "universe_group", "models", "combine", "threshold", "sizing", "test", "note",
                                                          "combine_buy", "combine_exit", "min_hold_days", "stop_blocks_buy")},
        combine_buy=(str(doc["combine_buy"]) if doc.get("combine_buy") is not None else None),
        combine_exit=(str(doc["combine_exit"]) if doc.get("combine_exit") is not None else None),
        min_hold_days=doc.get("min_hold_days"),          # 型と範囲は validate が見る（1 以上の整数）
        stop_blocks_buy=bool(doc.get("stop_blocks_buy", False)),
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
    """複数モデルの買い% ／ 出口% を 1 本にまとめる（プラン §2-1 の表）。役割のある人は側ごとにこれを呼ぶ（`signals.collect`）。

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
