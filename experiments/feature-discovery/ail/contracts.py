"""層のあいだで受け渡すものの約束。⚠ **最小限にとどめる。基底クラスは作らない。**

抽象を増やすほど、手法を 1 つ試すのに追う場所が増える。
ここでは「DataFrame のどの列が何を意味するか」だけを決め、それ以外は素の pandas で扱う。
"""

from __future__ import annotations

# --- L-D データ層（raw / adjusted）---
BAR_COLUMNS = ("time_ms", "open", "high", "low", "close", "volume")
# time_ms は **足の開始時刻**・UNIX ミリ秒・UTC

# --- L-F 特徴量層 ---
# ⚠ 接頭辞が「どの層の特徴量か」を表す。選別の結果を読むときにこれが効く
FEATURE_PREFIXES = {
    "own_": "その銘柄自身の履歴だけから作る",
    "cs_":  "⚠ 同じ時刻の全銘柄を見て作る（断面）",
    "rel_": "市場・セクターに対する相対（残差・ベータ調整後）",
    "ll_":  "⚠ 他銘柄の遅れた値（リードラグ）",
}

# 特徴量ではない列（検証で説明変数から外す）
META_COLUMNS = ("symbol", "ts", "close", "y", "y_sign", "y_elapsed_min")


def feature_columns(df) -> list[str]:
    """説明変数の列だけを返す。⚠ **ラベルとメタを必ず外す。**"""
    return [c for c in df.columns if c not in META_COLUMNS]


def layer_of(column: str) -> str:
    for pre, meaning in FEATURE_PREFIXES.items():
        if column.startswith(pre):
            return pre
    return "?"
