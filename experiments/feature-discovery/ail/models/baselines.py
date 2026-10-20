"""⚠ **基準線。これを超えない予測は「何も学んでいない」。**

⚠ **「常に上」は株のドリフト（上がる日のほうが多い）だけで的中率が 50% を超える。**
⚠ **これを超えない予測に価値は無い。** ただし「常に上」はほとんど回転しないので
⚠ **本当はコストを払わない**（純利の比較では、この差を必ず併記する）。

基準線は説明変数を見ないので、⚠ **`predict(te)` だけを持つ**（学習しない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.features import labels
from ail.registry import register


@register("model", "常に上（ドリフト）")
def always_up(tr, te, feats, ctx):
    return np.ones(len(te))


@register("model", "直前リターンの符号")
def last_return(tr, te, feats, ctx):
    """⚠ **モメンタムの最も素朴な形。** これに負ける「モメンタム手法」は名前だけである。"""
    col = "own_ret_1" if "own_ret_1" in te else "ret_1"
    return np.sign(te[col].values)


# --- 基準線「弱い相場は休む」（モデルなし。rules.md 25-8。2026-10-19 利用者「モデルなしの基準線を 1 本登録して回して」） ---
#
# ⚠ 土台差（25-7）の「買わない日」を決めたのはモデルの買い% か、相場の側かを分けるための基準線。
#    直近 N 日の「先 W 日で上がった日の割合」（全銘柄・確定した日だけ）が訓練の上がる割合 π を下回る間は休む（0）、
#    それ以外は買う（1）。説明変数を読まない・乱数なし。⚠ **既存の 2 本（常に上・直前符号）は 1 文字も変えない。**
# ⚠ N は 1 水準だけ（25-8 の 3）。結果を見て 126 ／ 504 を足さない（期間を振るのは θ の言い換え ＝ モデルの検証にならない）。

REST_WINDOW = 252                 # ⚠ 事前固定（25-8 の 3）
REST_HORIZONS = (5, 10, 20, 40)   # 先 W 日（25-1 の 4 本と同じ）


def rest_name_of(w: int) -> str:
    return f"弱い相場は休む（先{int(w)}日・直近{REST_WINDOW}日）"


def rest_when_weak(w: int, tr: pd.DataFrame, te: pd.DataFrame, n: int = REST_WINDOW) -> np.ndarray:
    """検証の行ごとに 1（買う）／ 0（休む）。rules.md 25-8 の 1。

    r_t ＝ 位置 t − W までの直近 n 日について、日ごとの「`y_fwd_W > 0` の銘柄の割合」の平均（ラベルの無い日は数えない）。
    π ＝ 訓練の行の `y_fwd_W > 0` の割合（`fwd.forward_gate` の「上がる割合」と同じ式）。r_t ≥ π なら 1・下回れば 0・窓が空なら 1。
    ⚠ 日付は訓練 ＋ 検証にある日付の位置で数える（パージで抜けた日付は飛ばす）。⚠ 位置 t − W より後のラベルは読まない。
    """
    col = f"{labels.SCALE_PREFIX}{int(w)}"
    for what, frame in (("訓練", tr), ("検証", te)):
        if col not in frame or "ts" not in frame:
            raise SystemExit(f"⚠ 基準線 {rest_name_of(w)!r} は{what}の行に `{col}` と `ts` が要る（label_scales に {w} を入れた表で回す）")
    ytr = tr[col].dropna()
    pi = float((ytr > 0).mean()) if len(ytr) else 0.5
    both = pd.concat([tr[["ts", col]], te[["ts", col]]], ignore_index=True)
    up = (both[col] > 0).astype(float).where(both[col].notna())       # ラベルの無い行は NaN のまま
    per_date = up.groupby(both["ts"]).mean().sort_index()               # 日ごとの上がった銘柄の割合（全部 NaN なら NaN）
    vals = per_date.to_numpy(dtype=float)
    ok = ~np.isnan(vals)
    cs = np.r_[0.0, np.cumsum(np.where(ok, vals, 0.0))]
    cn = np.r_[0, np.cumsum(ok.astype(int))]
    pos = per_date.index.get_indexer(te["ts"])
    hi = np.clip(pos - int(w) + 1, 0, len(vals))          # 窓の終わり（含まない側）＝ 位置 pos − w までを含む
    lo = np.clip(pos - int(w) - int(n) + 1, 0, len(vals))
    cnt = cn[hi] - cn[lo]
    r = np.where(cnt > 0, (cs[hi] - cs[lo]) / np.maximum(cnt, 1), np.nan)
    return np.where(np.isnan(r) | (r >= pi), 1.0, 0.0)


def _register_rest(w: int):
    @register("model", rest_name_of(w))
    def _rest(tr, te, feats, ctx, _w=w):
        return rest_when_weak(_w, tr, te)
    return _rest


REST_NAMES = tuple(rest_name_of(w) for w in REST_HORIZONS)
for _w in REST_HORIZONS:
    _register_rest(_w)
