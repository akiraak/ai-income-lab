"""入口と出口を別の窓で持つ検知器（rules.md 16 章）。

⚠ **出力は 2 本**（入口%・出口%）。未保有の日は入口% だけ、保有中の日は出口% だけが読まれる
（[16-1](../../../../docs/specs/experiments/feature-discovery/rules.md)）。⚠ **だから優先規則は要らない。**

⚠ **非対称性は窓の違いからしか生まれない**（16-2）。Platt 較正はロジスティック回帰 1 変数なので、
ラベルを反転すると係数が反転するだけで σ(−a·p − b) = 1 − σ(a·p + b) が厳密に成り立つ。
⚠ **つまり「同じ窓で出口% を独立に較正」しても 100 − 入口% そのものになる**（`tests/test_pairs.py` で縛った）。
だからここでは ⚠ **出口側の窓で較正を 1 本 fit し、その補数を出口% とする** — これが
16-3 の 2「出口% = P(`y_fwd_{W_out}` ≤ 0) の Platt 較正」と同じものである。
⚠ **入口の較正を使い回してはいない**（窓ごとに別の fit。`fitted/` に 2 本残る）。

| 系統 | 入口 | 出口 | 学習 |
| --- | --- | --- | --- |
| D 系（`入口D…×出口D…`） | P(先 W_in 本が上げ) | P(先 W_out 本が下げ) | ⚠ 訓練分割の内側（窓ごとに 2 本） |
| C 系（`入口C…×出口C…`） | SMA W_in の上なら入る | SMA W_out の下なら出る | ⚠ **しない**（公表された古典フィルタ） |

⚠ **構成は 6 つだけ**（16-4 で事前固定）。対称な窓（W_in = W_out）は 16-2 で退化するので**置かない**。
⚠ **逆向き 3 つ（入口が長期・出口が短期）は対照である** — 同じだけ効いたら、効いているのは
非対称性ではなく「出力を 2 本にしたこと」そのものだということ。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.detectors.scale import SCALES, classic_filter, scale_gate
from ail.registry import register

# ⚠ **事前固定**（rules.md 16-4）。⚠ **結果を見て足さない・減らさない。**
# 前半 3 つが仮説（入口は短期・出口は長期）、⚠ **後半 3 つはその対照**（逆向き）
PAIRS: tuple[tuple[int, int], ...] = ((20, 60), (20, 200), (60, 200),
                                      (60, 20), (200, 20), (200, 60))

_INDEX = {w: i + 1 for i, w in enumerate(SCALES.values())}   # 20→1 / 60→2 / 200→3


def name_of(kind: str, w_in: int, w_out: int) -> str:
    """台帳に出る手法名。⚠ **構成を名前に入れる**（rules.md 16-6 の 1）。

    ⚠ **鍵に列は足さない。** 名前が `catalog.canonical` を通って鍵に落ちるので、
    ⚠ **名前に入れ忘れると行がまとまり、差が「再現の幅」に化ける**（段 1 で踏んだ形）。
    """
    return (f"入口{kind}{_INDEX[w_in]}({w_in})×出口{kind}{_INDEX[w_out]}({w_out})"
            + ("（学習）" if kind == "D" else "（古典）"))


def pair_gate(w_in: int, w_out: int, tr: pd.DataFrame, te: pd.DataFrame, feats, ctx: dict):
    """D 系: 入口% は W_in のゲート、⚠ **出口% は W_out のゲートの補数**（rules.md 16-3）。"""
    entry, doc_in = scale_gate(w_in, tr, te, feats, ctx)
    buy_out, doc_out = scale_gate(w_out, tr, te, feats, ctx)
    exit_pct = 100.0 - np.asarray(buy_out, dtype=float)
    cols = sorted(set(doc_in["columns"]) | set(doc_out["columns"]))
    return np.asarray(entry, dtype=float), exit_pct, {
        "入口の窓": w_in, "出口の窓": w_out, "columns": cols,
        "入口": doc_in, "出口": doc_out,
        "source": f"入口 {doc_in.get('source')} ／ 出口 {doc_out.get('source')}（補数）",
    }


def pair_classic(w_in: int, w_out: int, tr: pd.DataFrame, te: pd.DataFrame, feats, ctx: dict):
    """C 系: ⚠ **SMA W_in の上で入り、SMA W_out の下で出る。** 学習しない。

    ⚠ **入口% も出口% も 0 か 100 しか取らない**ので θ の 3 水準で結果が同じになる。
    ⚠ **それでも 3 試行として数える**（rules.md 13-3 の 5）。
    """
    entry, doc_in = classic_filter(w_in, tr, te, feats, ctx)
    buy_out, doc_out = classic_filter(w_out, tr, te, feats, ctx)
    exit_pct = 100.0 - np.asarray(buy_out, dtype=float)
    return np.asarray(entry, dtype=float), exit_pct, {
        "入口の窓": w_in, "出口の窓": w_out, "columns": [],
        "入口": doc_in, "出口": doc_out,
        "source": "none（学習しない）",
    }


def _register_pair(w_in: int, w_out: int) -> None:
    @register("detector", name_of("D", w_in, w_out))
    def _learned(tr, te, feats, ctx, _i=w_in, _o=w_out):
        return pair_gate(_i, _o, tr, te, feats, ctx)

    @register("detector", name_of("C", w_in, w_out))
    def _classic(tr, te, feats, ctx, _i=w_in, _o=w_out):
        return pair_classic(_i, _o, tr, te, feats, ctx)


for _in, _out in PAIRS:
    _register_pair(_in, _out)
