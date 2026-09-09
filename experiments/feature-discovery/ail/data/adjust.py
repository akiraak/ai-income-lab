"""⚠ **株式分割・配当の調整**（rules.md 2 章）。

⚠ **提供元（tastytrade / dxFeed）の日足は「調整済みだが、当て方を間違えている」**（2026-09-08 の実測）。
分割そのものは効いている（NVDA 2024 の 10:1 で終値は連続）が、⚠ **効かせる期間がずれた継ぎ目が 63 本ある**。
そのため本モジュールがやることは「分割を当てる」ではなく **「誤って当たっている目盛りを剥がす」** になる。

規約（rules.md 2 章）:
  - ⚠ `raw/` は書き換えず、`adjusted/` に新しく書く
  - ⚠ **価格を割ったら出来高は掛ける**（分割のときだけ。分割でない比率では出来高を触らない）
  - 累積比率で継ぎ目**以前**の全期間に効かせる
  - ⚠ 配当は既定で調整しない（価格リターン）
  - ⚠ **検知は自動、確定は一次情報**。外れ値検知だけで分割と断定しない

⚠ **一番効く判別は「出来高」である。** 目盛りの誤りは値段だけが動いて売買代金も値幅も平年並みだが、
⚠ **本物の暴落は売買代金が 3〜15 倍に跳ねる**。比率が 2:1 に近いだけで分割と決めると、
⚠ **AAPL 2000-09-29（−52%）や PG 2000-03-07（−31%）という実在の暴落を消してしまう。**
"""

from __future__ import annotations

from math import gcd

import numpy as np
import pandas as pd

# ⚠ **分割としてありうる比率だけを候補にする。** 分母を広げると 19:9 のような無意味な比に吸着する
SPLIT_RATIOS = sorted({a / b for b in (1, 2, 3, 4) for a in range(2, 21)
                       if gcd(a, b) == 1 and 1.15 <= a / b <= 25})

# 判別のしきい値（rules.md 2 章の表と対）
GAP_MIN = 0.18          # |log(前日終値 / 当日始値)|。これ未満は見ない（5:4 = 0.223 を拾える）
RATIO_TOL = 0.03        # 整数比とみなす相対誤差。⚠ **その日の実際の値動き（±1〜2%）が段差に乗る**
NEWS_DOLLAR_VOL = 2.0   # 売買代金が平年の何倍を超えたら「実際の変動」とみなすか
NEWS_RANGE = 2.5        # 値幅が平年の何倍を超えたら同上
NEWS_INTRADAY = 0.08    # 日中の値動き（|log(終値/始値)|）がこれを超えたら同上

# ⚠ **自動で直すのは 1.9 倍以上の段差だけ。**
# ⚠ **20% 前後の段差は、実在の暴落（XLE 2020-03-09 の原油戦争）とスピンオフ（XLF 2016-09-19 の XLRE 分離）と
#    提供元の粗が混ざっていて、出来高だけでは分けられない。** 分けられないものを自動で消すと、
# ⚠ **実在した −20% を「何も起きなかった日」に書き換えてしまう。** 直さずに「要確認」として記録する。
REPAIR_MIN_RATIO = 1.9

KIND_SPLIT = "直す（分割比）"
KIND_OTHER = "直す（非整数比）"
KIND_REAL = "直さない（実際の変動）"
KIND_CHECK = "直さない（要確認）"


def _snap(gap: float) -> tuple[float | None, float]:
    """段差を整数比に吸着させる。返り値は (比率 or None, 相対誤差)。"""
    up = max(gap, 1 / gap)
    near = min(SPLIT_RATIOS, key=lambda k: abs(np.log(up / k)))
    err = abs(np.log(up / near))
    return (near if err <= RATIO_TOL else None), err


def detect_scale_breaks(df: pd.DataFrame, window: int = 41) -> pd.DataFrame:
    """目盛りの断層を探す。⚠ **「実際の変動」と「調整の誤り」を分けて返す**（消してよいのは後者だけ）。

    列: `i` `ts` `gap`（前日終値 ÷ 当日始値）`ratio`（吸着した整数比 or NaN）`kind`
    `dollar_vol_x` `range_x` `intraday` `volume_x`（出来高が段差の逆向きに動いたか）
    """
    d = df.reset_index(drop=True)
    c, o, h, l, v = d["close"], d["open"], d["high"], d["low"], d["volume"]
    dv = c * v
    dv_med = dv.rolling(window, center=True, min_periods=10).median()
    rng = np.log((h / l).where(l > 0))
    rng_med = rng.rolling(window, center=True, min_periods=10).median()

    rows = []
    for i in range(1, len(d)):
        if o[i] <= 0 or c[i - 1] <= 0:
            continue
        gap = c[i - 1] / o[i]
        if abs(np.log(gap)) < GAP_MIN:
            continue
        ratio, err = _snap(gap)
        dvx = float(dv[i] / dv_med[i]) if dv_med[i] else np.nan
        rgx = float(rng[i] / rng_med[i]) if rng_med[i] else np.nan
        intraday = float(np.log(c[i] / o[i]))
        news = (dvx > NEWS_DOLLAR_VOL or rgx > NEWS_RANGE or abs(intraday) > NEWS_INTRADAY)
        # 出来高が段差の逆向きに動いているか（提供元が既に出来高も割っている印）
        before = float(v[max(0, i - 5):i].median() or np.nan)
        after = float(v[i:i + 5].median() or np.nan)
        vx = before / after if after else np.nan
        if news:
            kind = KIND_REAL
        elif max(gap, 1 / gap) < REPAIR_MIN_RATIO:
            kind = KIND_CHECK          # ⚠ 一次情報で確かめるまで触らない
        else:
            kind = KIND_SPLIT if ratio else KIND_OTHER
        rows.append({"i": i, "ts": d["ts"][i], "gap": gap, "ratio": ratio if ratio else np.nan,
                     "ratio_err": err, "kind": kind, "dollar_vol_x": dvx, "range_x": rgx,
                     "intraday": intraday, "volume_x": vx})
    return pd.DataFrame(rows, columns=["i", "ts", "gap", "ratio", "ratio_err", "kind",
                                       "dollar_vol_x", "range_x", "intraday", "volume_x"])


def repair_factors(df: pd.DataFrame, breaks: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """継ぎ目から、各足に掛ける「価格の直し」と「出来高の直し」を作る。

    ⚠ **基準は一番新しい足**（＝ いまの実際の値段。SPY の最新終値が本番の約定値と一致することを確認済み）。
    そこから過去へ遡り、継ぎ目より前の全期間に累積で効かせる。

    ⚠ **整数比に吸着したときは吸着後の値を使う。** 実測の段差をそのまま使うと、
    ⚠ **継ぎ目を往復したあとに累積比が 1 に戻らず、系列全体が数 % ずれる。**
    """
    n = len(df)
    pf = np.ones(n)
    vf = np.ones(n)
    usable = breaks[breaks["kind"].isin((KIND_SPLIT, KIND_OTHER))]
    p_cum = v_cum = 1.0
    for _, b in usable.sort_values("i", ascending=False).iterrows():
        i = int(b["i"])
        gap = float(b["gap"])
        ratio = float(b["ratio"]) if not np.isnan(b["ratio"]) else max(gap, 1 / gap)
        step = ratio if gap > 1 else 1 / ratio      # 継ぎ目より前が「何倍の目盛り」か
        p_cum /= step
        # ⚠ **出来高を直すのは分割のときだけ。** スピンオフ・配当の比率は株数を変えない
        v_cum *= step if not np.isnan(b["ratio"]) else 1.0
        pf[:i] = p_cum
        vf[:i] = v_cum
    return pd.Series(pf, index=df.index), pd.Series(vf, index=df.index)


def adjust(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """`raw` の 1 系列を調整する。返り値は (調整後, 継ぎ目の一覧, 記録用の要約)。"""
    d = df.reset_index(drop=True).copy()
    breaks = detect_scale_breaks(d)
    pf, vf = repair_factors(d, breaks)
    out = d.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = d[col] * pf
    out["volume"] = d["volume"] * vf

    summary = {
        "breaks_found": int(len(breaks)),
        "repaired": int(breaks["kind"].isin((KIND_SPLIT, KIND_OTHER)).sum()),
        "repaired_split_ratio": int((breaks["kind"] == KIND_SPLIT).sum()),
        "repaired_other_ratio": int((breaks["kind"] == KIND_OTHER).sum()),
        "kept_as_real_move": int((breaks["kind"] == KIND_REAL).sum()),
        "needs_primary_source": int((breaks["kind"] == KIND_CHECK).sum()),
        "rows_rescaled": int((pf != 1.0).sum()),
        "events": [{"date": str(b["ts"].date()), "kind": b["kind"],
                    "gap": round(float(b["gap"]), 4),
                    "ratio": None if np.isnan(b["ratio"]) else round(float(b["ratio"]), 4),
                    "dollar_vol_x": None if np.isnan(b["dollar_vol_x"]) else round(float(b["dollar_vol_x"]), 2)}
                   for _, b in breaks.iterrows()],
    }
    return out, breaks, summary
