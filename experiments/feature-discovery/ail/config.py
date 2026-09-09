"""`config/` の TOML を読む。⚠ **コードを書かずに実験を増やす場所**（rules.md 10 章）。

    config/universe/<名前>.toml    銘柄の集合
    config/dataset/<名前>.toml     粒度・期間・調整
    config/experiment/<名前>.toml  1 実験 1 ファイル

⚠ **名前の打ち間違いは実行時まで分からない**ので、`resolve_experiment` が
⚠ **走り出す前に選別手法・モデル・分割の名前を全部 registry で解決する**（解決できなければ即止める）。
"""

from __future__ import annotations

import os
import tomllib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CONFIG = os.path.join(ROOT, "config")


def _load(kind: str, name: str) -> dict:
    path = os.path.join(CONFIG, kind, f"{name}.toml")
    if not os.path.exists(path):
        have = sorted(f[:-5] for f in os.listdir(os.path.join(CONFIG, kind)) if f.endswith(".toml"))
        raise SystemExit(f"config/{kind}/{name}.toml が無い。あるのは: {', '.join(have)}")
    with open(path, "rb") as f:
        return tomllib.load(f)


def universe(name: str) -> dict:
    return _load("universe", name)


def dataset(name: str) -> dict:
    return _load("dataset", name)


def experiment(name: str) -> dict:
    return _load("experiment", name)


def exposure(name: str) -> dict:
    """⚠ **災害を銘柄に割り当てる表**（`im_` 層が読む）。⚠ **重みは全部【推測】である。**"""
    return _load("exposure", name)


def symbols_of(universe_name: str) -> list[str]:
    """⚠ **ETF が先、会社が後**の順で返す（断面の並びを実行ごとに変えないため）。"""
    u = universe(universe_name)
    g = u.get("groups", {})
    return list(g.get("etf", [])) + list(g.get("company", []))


def market_proxy(universe_name: str) -> tuple[str, dict[str, str]]:
    """`rel_` が使う市場代表とセクター ETF を返す。"""
    m = universe(universe_name).get("market_proxy", {})
    return m.get("market", "SPY"), dict(m.get("sectors", {}))


def resolve_experiment(name: str) -> dict:
    """実験の設定を読み、⚠ **参照している名前を全部その場で解決する。**

    ⚠ **ここで落とすのが目的。** 5 分回してから「名前が違う」で止まるのが一番もったいない。
    """
    from ail import registry
    import ail.bootstrap  # noqa: F401  （登録を全部走らせる）

    exp = experiment(name)
    ds = dataset(exp["dataset"])
    exp["_dataset"] = ds
    exp["_symbols"] = symbols_of(ds["universe"])
    registry.resolve_all("selector", exp.get("selectors", []))
    registry.resolve_all("model", [exp.get("model", "Ridge")])
    registry.resolve_all("model", exp.get("baselines", []))
    registry.resolve_all("split", [exp.get("validation", {}).get("split", "walk_forward")])
    for layer in exp.get("feature_layers", []):
        registry.resolve("feature", layer)
    return exp
