"""⚠ **地域の定義を 1 か所に置く。** ⚠ **取得元ごとに違う地域を持つと、割り当てが噛み合わない。**

⚠ **NCEI Storm Events は州の「フルネーム」（`TEXAS`）、IEM の警報は UGC の「2 文字」（`TXC201` → `TX`）**
で州を持つ。⚠ **書式が違うだけで同じ州である**ことを、ここで固定する。

⚠ **地域は業種への割り当ての土台である**（[plan §2-2](../../../../docs/plans/impact-data.md)）。
⚠ **地域を持たないデータは全銘柄で同じ値になり、[§9-4](../../../../docs/specs/experiments/daily-data-sources.md) と同じ結果になる。**
"""

from __future__ import annotations

# ⚠ **地域 → 州（2 文字）。** ⚠ **この 4 地域が `im_` 層の割り当ての軸になる。**
REGIONS: dict[str, tuple[str, ...]] = {
    "湾岸": ("TX", "LA", "MS", "AL", "FL"),          # 保険・製油（メキシコ湾）
    "西部": ("CA", "OR", "WA", "NV", "AZ"),          # 公益・山火事
    "中西部": ("IL", "IA", "KS", "MO", "NE", "OK"),  # 竜巻・農業
    "北東部": ("NY", "NJ", "PA", "MA", "CT"),        # 金融・人口密集
}

# 2 文字 → NCEI が使うフルネーム（⚠ **NCEI の `STATE` 列は大文字のフルネーム**）
FULL_NAME: dict[str, str] = {
    "TX": "TEXAS", "LA": "LOUISIANA", "MS": "MISSISSIPPI", "AL": "ALABAMA", "FL": "FLORIDA",
    "CA": "CALIFORNIA", "OR": "OREGON", "WA": "WASHINGTON", "NV": "NEVADA", "AZ": "ARIZONA",
    "IL": "ILLINOIS", "IA": "IOWA", "KS": "KANSAS", "MO": "MISSOURI", "NE": "NEBRASKA",
    "OK": "OKLAHOMA", "NY": "NEW YORK", "NJ": "NEW JERSEY", "PA": "PENNSYLVANIA",
    "MA": "MASSACHUSETTS", "CT": "CONNECTICUT",
}


def states(region: str) -> tuple[str, ...]:
    return REGIONS[region]


def full_names(region: str) -> tuple[str, ...]:
    """⚠ **NCEI 用。** 2 文字をフルネームに直す。"""
    return tuple(FULL_NAME[s] for s in REGIONS[region])


def region_of(state: str) -> str | None:
    """州（2 文字）→ 地域。⚠ **どの地域にも入らない州は `None`**（全国の集計にだけ入る）。"""
    for name, ss in REGIONS.items():
        if state in ss:
            return name
    return None
