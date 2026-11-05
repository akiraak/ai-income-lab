"""登録簿 `20261101-c3-as-stop-role`（参考あり・枠の内。記録 dev-loop.md）:
遅い出口 C3（終値が 200 日移動平均を割ったら降りる）を **役割つきの組の損切り係** に置くための薄い口。

`C3 長期 SMA200 フィルタ`（`ail/detectors/scale.py`）は買い% を 0 ／ 100 で返すだけで出口% を返さないので、合わせる口の
損切り係（出口% が要る ＝ `cli/run.py` `_roles_fold`）には置けない。⇒ 同じ規則を **出口だけ** の形で登録する
（買い% ＝ 常に 100・出口% ＝ 終値 < SMA200 なら 100、上なら 0）。学習しない（公表された古典の規則そのまま）。

⚠ 借りる表（`trade_own_fwdmulti_a`）には `own_trend200_dist` が無い（trend 層は 20 日だけ）ので、表の `close` から
   SMA200 を作る: 訓練分割と評価分割の行を銘柄ごとに日付順に並べ、200 本の単純平均（200 本そろわない行は出口 0 ＝ 損切りしない）。
   使うのはその行までの終値だけ（先を見ない）。⚠ 評価分割の頭の行は、パージで抜けた日をまたいで 200 本を数える
   （暦で 200 営業日より少し長い平均になる）＝ 記録の `doc` に残す。
⚠ 登録するだけ ＝ この検知器を名指さない config は 1 ビットも変わらない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.registry import register

NAME = "X7 出口だけ 終値がSMA200を割ったら降りる"
WINDOW = 200


def sma_below(tr: pd.DataFrame, te: pd.DataFrame, window: int = WINDOW) -> tuple[np.ndarray, dict]:
    """評価分割の行ごとに「終値 < その行までの SMA(window)」なら 1.0、上か SMA が無ければ 0.0。並びは te のまま。"""
    if "close" not in te or "close" not in tr:
        raise SystemExit("⚠ X7: 表に close が無い（SMA200 を作れない）")
    a = tr[["symbol", "ts", "close"]].assign(_te=-1)
    b = te[["symbol", "ts", "close"]].assign(_te=np.arange(len(te)))
    df = pd.concat([a, b], ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts"])
    # ⚠ 同じ (銘柄, 日) が両方にあれば評価分割の行を残す（walk-forward では起きない）
    df = df.sort_values(["symbol", "ts", "_te"]).drop_duplicates(["symbol", "ts"], keep="last")
    sma = df.groupby("symbol", sort=False)["close"].transform(lambda s: s.rolling(window, min_periods=window).mean())
    below = (df["close"] < sma) & sma.notna()
    out = np.zeros(len(te), dtype=float)
    m = df["_te"].to_numpy() >= 0
    out[df["_te"].to_numpy()[m]] = below.to_numpy()[m].astype(float)
    doc = {"rule": f"close < SMA{window}（表の close から・学習しない）", "SMA の無い評価行": int((m & sma.isna().to_numpy()).sum()),
           "割れている評価行の割合": round(float(out.mean()), 4) if len(out) else None,
           "注意": "評価分割の頭はパージで抜けた日をまたいで 200 本を数える"}
    return out, doc


@register("detector", NAME)
def x7_exit_sma200(tr, te, feats, ctx):
    below, doc = sma_below(tr, te)
    return np.full(len(te), 100.0), 100.0 * below, {**doc, "入口": "常に 100（出口だけ）", "source": "none（学習しない）", "columns": []}
