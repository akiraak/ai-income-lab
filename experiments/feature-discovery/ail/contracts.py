"""層のあいだで受け渡すものの約束。⚠ **最小限にとどめる。基底クラスは作らない。**

抽象を増やすほど、手法を 1 つ試すのに追う場所が増える。
ここでは「DataFrame のどの列が何を意味するか」だけを決め、それ以外は素の pandas で扱う。
"""

from __future__ import annotations

# --- L-D データ層（raw / adjusted）---
BAR_COLUMNS = ("time_ms", "open", "high", "low", "close", "volume")
# time_ms は **足の開始時刻**・UNIX ミリ秒・UTC

# --- L-D 外部系列（外から持ってくる 1 値の日次系列）---
# ⚠ **足（OHLCV）とは形が違う。** 混ぜると `BAR_COLUMNS` の検査が通らないので層を分ける
SERIES_COLUMNS = ("time_ms", "value")
# time_ms は **その日の 00:00 UTC**。⚠ **発表の遅れがあるので、特徴量にするときは必ずずらす**

# --- L-F 特徴量層 ---
# ⚠ 接頭辞が「どの層の特徴量か」を表す。選別の結果を読むときにこれが効く
FEATURE_PREFIXES = {
    "own_": "その銘柄自身の履歴だけから作る",
    "cs_":  "⚠ 同じ時刻の全銘柄を見て作る（断面）",
    "rel_": "市場・セクターに対する相対（残差・ベータ調整後）",
    "ll_":  "⚠ 他銘柄の遅れた値（リードラグ）",
    "ex_":  "⚠ 価格の外から来る系列（為替・金利・気象・地震）。**必ず 1 日以上ずらす**",
    # ⚠ **`im_pb_` を先に置く。** `layer_of` は先に当たった接頭辞を返すので、逆順だと偽薬が本命に化ける
    "im_pb_": "⚠ **`im_` の偽薬**（割り当てを入れ替えたもの）。選ばれた割合がそのまま偽発見率になる",
    "im_":  "⚠ 災害を地域・業種に割り当てたもの。**`ex_` と違い銘柄ごとに値が変わる**",
}

# 特徴量ではない列（検証で説明変数から外す）
META_COLUMNS = ("symbol", "ts", "close", "y", "y_sign", "y_elapsed_min")
# ⚠ **接頭辞で外すもの。** スケールのラベル `y_fwd_{W}`（先 W 本の累積リターン ＝ 学習の対象）は
# ⚠ **窓の数だけ増える**ので、名前を数え上げずに接頭辞で外す。
# ⚠ **外し忘れると「学習の対象がそのまま説明変数」になる** — 先読みの中でも最悪の形である
META_PREFIXES = ("y_fwd_",)


def is_meta(column: str) -> bool:
    """説明変数から外す列か。⚠ **判定はここ 1 か所**（`cli/build.py` も `cli/run.py` もこれを使う）。"""
    return column in META_COLUMNS or column.startswith(META_PREFIXES)


def feature_columns(df) -> list[str]:
    """説明変数の列だけを返す。⚠ **ラベルとメタを必ず外す。**"""
    return [c for c in df.columns if not is_meta(c)]


def layer_of(column: str) -> str:
    for pre, meaning in FEATURE_PREFIXES.items():
        if column.startswith(pre):
            return pre
    return "?"
