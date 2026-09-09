"""名前で手法を引く登録表。⚠ **この 1 ファイルが「柔軟な形」の核である。**

手法を足すときは、担当のファイルに関数を 1 つ書いて `@register` を付けるだけでよい。
⚠ **配線（cli / 実験の回し方）は触らない。** `config/experiment/*.toml` に名前を書けば比較に入る。

    from ail.registry import register, resolve

    @register("selector", "F1-8 条件付き相互情報量")
    def cmi(X, y, k, ctx): ...

    fn = resolve("selector", "F1-8 条件付き相互情報量")

⚠ **名前の打ち間違いは実行時まで分からない。** そのため `resolve_all` を用意し、
⚠ **実験を始める前に config の名前を全部解決してから走る**（解決できなければ即止める）。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

# 種類 -> 名前 -> 実体
_REGISTRY: dict[str, dict[str, Callable[..., Any]]] = {}

# 登録できる種類。⚠ **ここに無い種類は受け付けない**（打ち間違いを早く落とすため）
KINDS = ("source", "feature", "selector", "model", "split", "metric", "stat")


def register(kind: str, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    if kind not in KINDS:
        raise ValueError(f"種類が不正: {kind!r}（{KINDS} のどれか）")

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        table = _REGISTRY.setdefault(kind, {})
        if name in table:
            raise ValueError(f"{kind} の名前が重複: {name!r}")
        table[name] = fn
        fn.__ail_kind__ = kind          # type: ignore[attr-defined]
        fn.__ail_name__ = name          # type: ignore[attr-defined]
        return fn

    return deco


def resolve(kind: str, name: str) -> Callable[..., Any]:
    table = _REGISTRY.get(kind, {})
    if name not in table:
        near = ", ".join(sorted(table)) or "（1 つも登録されていない）"
        raise KeyError(f"{kind} に {name!r} が無い。登録済み: {near}")
    return table[name]


def resolve_all(kind: str, names: Iterable[str]) -> dict[str, Callable[..., Any]]:
    """⚠ **走り出す前に全部解決する。** 1 つでも欠ければここで止まる。"""
    return {n: resolve(kind, n) for n in names}


def available(kind: str) -> list[str]:
    return sorted(_REGISTRY.get(kind, {}))


def summary() -> dict[str, list[str]]:
    return {k: sorted(v) for k, v in sorted(_REGISTRY.items())}
