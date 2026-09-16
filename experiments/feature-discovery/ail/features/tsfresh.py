"""F4-3 生成型 — ⚠ **tsfresh の総当たりを窓 60 に当てる**（[プラン](../../../../docs/plans/archive/ledger-blanks-large-two.md)）。

⚠ **`feature` 層ではなく `transform` である。** 台帳の「次の一手」は層と書いてあったが変えた:
⚠ **`catalog.implemented()` が見るのは `selector` と `transform` の registry 名だけ**なので、
⚠ **層にすると回しても台帳 §3 で「未実施」のままになる**（プラン §1-1）。

    窓（`own_seq60_r59` … `r0` ＝ 古い → 新しい）→ tsfresh 783 列 → 落として 446 列

⚠ **1 行ずつ独立に変換する。** 窓は `shift(k≥0)` だけで作られている（`ail/features/seq.py`）ので、
⚠ **その中しか読まないこの変換は定義から未来に触れられない**（プラン §2-1）。

⚠ **落とす列は訓練分割だけで決める**（rules.md 3 章 B）。⚠ **検証分割を見て決めたら選別を外でやったことになる。**

⚠ **`import tsfresh` はこのファイル自身ではなく site-packages を指す**（Python 3 の絶対 import）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.data.transforms.fitted import FittedTransform
from ail.registry import register

# ⚠ **窓の列の接頭辞**（`ail/features/seq.py` が作る `own_seq60_r{k}`。k = 0 が足 i）
SEQ_PREFIX = "own_seq"
# ⚠ **生成した列の接頭辞。** `own_` と混ぜない（どちらが処置かを後から見分けるため）
PREFIX = "tsf_"
# ⚠ **並列の分割数は事前固定**（プラン §7-1 の実測。⚠ **結果を見て動かさない**）
N_JOBS = 16
# ⚠ **名前の先頭の ID で台帳が寄せる**（`ail/catalog.py` の `_ID`）。
# ⚠ **カタログの「794 特徴量」は出ない。実際は 783 列**【実測 2026-09-16】なので実数を書く
NAME = "F4-3 tsfresh の総当たり（783 列）"


def window_order(columns) -> list[str]:
    """窓の列を **古い → 新しい**の順で返す。⚠ **無ければ止める**（黙って素通りさせない）。"""
    win = [c for c in columns if c.startswith(SEQ_PREFIX)]
    if not win:
        raise SystemExit(f"⚠ F4-3: 窓の列（{SEQ_PREFIX}…）が表に無い。"
                         "`feature_layers` に \"seq\" を入れた表を `features_from` で読む")
    # ⚠ **列は r0 = 足 i の「新しい順」で並んでいる**ので、逆に並べ替えて時間の向きを直す
    return sorted(win, key=lambda c: int(c.rsplit("_r", 1)[1]), reverse=True)


def extract(X: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    """窓 60 列 → tsfresh の 783 列。⚠ **行ごとに独立**（行をまたいだ情報は使わない）。"""
    from tsfresh import extract_features
    from tsfresh.feature_extraction import ComprehensiveFCParameters

    w = X[order].to_numpy(dtype=float)
    n, m = w.shape
    long = pd.DataFrame({"id": np.repeat(np.arange(n), m),
                         "time": np.tile(np.arange(m), n),
                         "value": w.reshape(-1)})
    out = extract_features(long, column_id="id", column_sort="time", column_value="value",
                           default_fc_parameters=ComprehensiveFCParameters(),
                           n_jobs=N_JOBS, disable_progressbar=True)
    # ⚠ **`extract_features` は id の順を保証しない。** 並べ直してから元の索引に戻す
    out = out.sort_index()
    out.index = X.index
    # ⚠ **tsfresh は ±inf も返す**（比や指数の計算子。2026-09-16 にテストで踏んだ）。
    # ⚠ **NaN と同じ扱いに寄せる** — 残すと `StandardScaler` が「inf がある」で止まる
    return out.replace([np.inf, -np.inf], np.nan).add_prefix(PREFIX)


@register("transform", NAME)
def tf_tsfresh(Xtr, Xte, ctx):
    """⚠ **窓を tsfresh の総当たりに置き換える。**

    ⚠ **生の窓 60 列は落とす** — 残すと同じ情報を 2 度渡すことになり、「tsfresh の効果」ではなくなる。
    ⚠ **`own` の 35 列はそのまま残す**（比較の相手が「own だけ」なので、足した分だけを処置にする）。
    ⚠ **列の始末はプラン §7-3 の 5 つ。** 全部 NaN と定数を落とし、⚠ **訓練分割の平均で埋め、訓練分割で標準化する。**
    """
    order = window_order(Xtr.columns)
    rest = [c for c in Xtr.columns if not c.startswith(SEQ_PREFIX)]

    tr, te = extract(Xtr, order), extract(Xte, order)
    n_raw = int(tr.shape[1])

    # ⚠ **落とす列は訓練分割だけで決める**（3 章 B）。⚠ **検証分割には同じ列名を当てるだけ**
    n_bad_tr = int(tr.isna().to_numpy().sum())      # ⚠ NaN ＋ inf（`extract` が寄せたあと）
    allnan = [c for c in tr.columns if tr[c].isna().all()]
    tr, te = tr.drop(columns=allnan), te.drop(columns=allnan)
    const = [c for c in tr.columns if tr[c].nunique(dropna=True) <= 1]
    tr, te = tr.drop(columns=const), te.drop(columns=const)

    # ⚠ **残った NaN は訓練分割の平均で埋める**（検証分割にだけ出たときの保険。訓練 3,000 窓では 0 件）
    mu = tr.mean()
    n_nan_te = int(te.isna().to_numpy().sum())
    tr, te = tr.fillna(mu), te.fillna(mu)

    # ⚠ **桁が揃っていないので訓練分割で標準化する**（Ridge の L2 罰則は尺度に依存する。§7-3 の 4）
    sc = FittedTransform("standardize").fit(tr)
    tr, te = sc.transform(tr), sc.transform(te)

    out_tr = pd.concat([Xtr[rest], tr], axis=1)
    out_te = pd.concat([Xte[rest], te], axis=1)
    return out_tr, out_te, {"窓の列": len(order), "生成した列": n_raw,
                            "落とした列（全部NaN）": len(allnan), "落とした列（定数）": len(const),
                            "残した生成列": int(tr.shape[1]), "残した元の列": len(rest),
                            "⚠ 訓練分割の NaN・inf": n_bad_tr,
                            "⚠ 検証分割で埋めた NaN・inf": n_nan_te,
                            "標準化": sc.coefficients()}
