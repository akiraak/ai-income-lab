"""シナリオ（標本）から測る指標と、経路のまとめ方（プラン §6・§7）。

⚠ **CRPS は低いほどよい。** 単位は対象と同じ（ここでは 5 営業日の累積単純リターン）。
⚠ **本数を 1,000 本に増やしても評価日数は増えない**（減るのは標本近似の誤差だけ。プラン §6）。
"""

from __future__ import annotations

import numpy as np


def cumulative_returns(r: np.ndarray) -> np.ndarray:
    """日次対数リターン `[..., horizon]` → k 日目までの累積単純リターン `exp(sum(r_1..r_k)) − 1`（プラン §7）。"""
    return np.expm1(np.cumsum(r, axis=-1))


def crps_samples(samples: np.ndarray, y: np.ndarray) -> np.ndarray:
    """標本からの CRPS ＝ E|X − y| − ½ E|X − X'|。`samples` `[n, m]`・`y` `[n]` → `[n]`。

    ⚠ **第 2 項は並べ替えて O(m log m) で出す**（総当たりの m² を作らない）:
    ½ E|X − X'| ＝ Σ_i (2i − m − 1) x_(i) / m²（i は 1 始まりの順位）。
    """
    s = np.sort(np.asarray(samples, dtype=np.float64), axis=1)
    m = s.shape[1]
    w = 2 * np.arange(1, m + 1) - m - 1
    spread = (s * w).sum(axis=1) / (m * m)
    return np.abs(s - np.asarray(y, dtype=np.float64)[:, None]).mean(axis=1) - spread


def crps_5d(paths: np.ndarray, y: np.ndarray) -> float:
    """主指標: 最終日の累積単純リターンの CRPS の平均。`paths` `[n, m, horizon]`・`y` `[n, horizon]`（どちらも日次対数）。"""
    return float(crps_samples(cumulative_returns(paths)[..., -1], cumulative_returns(y)[..., -1]).mean())


# ---------------------------------------------------------------- 補助指標（プラン §6。⚠ 判定に使うのは記録 §3-2 の (d) だけ）

INTERVALS = (0.50, 0.80, 0.95)
QUANTILES = (0.025, 0.10, 0.25, 0.50, 0.75, 0.90, 0.975)      # 出力の `return_quantiles_by_day`（プラン §7）
DROP = -0.05                                                   # 「起点から 5% 以上の下落」（⚠ 最高値からのドローダウンではない）


def coverage(samples: np.ndarray, y: np.ndarray) -> dict:
    """中心区間の被覆率と平均幅。`samples` `[n, m]`・`y` `[n]`。⚠ **幅を併記する**（広げるだけで被覆率は上がる）。"""
    out = {}
    for p in INTERVALS:
        lo, hi = np.quantile(samples, [(1 - p) / 2, (1 + p) / 2], axis=1)
        out[f"cover_{int(p * 100)}"] = float(((y >= lo) & (y <= hi)).mean())
        out[f"width_{int(p * 100)}"] = float((hi - lo).mean())
    return out


def brier(prob: np.ndarray, event: np.ndarray) -> float:
    return float(((np.asarray(prob, dtype=float) - np.asarray(event, dtype=float)) ** 2).mean())


def reliability(prob: np.ndarray, event: np.ndarray, edges=(0.0, 0.4, 0.5, 0.6, 0.7, 1.0001)) -> list[dict]:
    """確率帯別の予想と実際の割合。⚠ **各帯の件数を必ず出す**（件数の少ない帯を読まないため）。"""
    prob, event = np.asarray(prob, dtype=float), np.asarray(event, dtype=float)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        k = (prob >= lo) & (prob < hi)
        rows.append({"帯": f"{lo:.1f}–{min(hi, 1.0):.1f}", "件数": int(k.sum()),
                     "予想": float(prob[k].mean()) if k.any() else None,
                     "実際": float(event[k].mean()) if k.any() else None})
    return rows


def drop_event(r: np.ndarray) -> np.ndarray:
    """1〜5 日目のどれかの**終値**が起点から 5% 以上下がったか。`r` `[..., horizon]`（日次対数）→ `[...]`。

    ⚠ **日中の安値ではない**（日足の終値しか生成していない。プラン §7）。
    """
    return cumulative_returns(r).min(axis=-1) <= DROP


def summarize_paths(paths: np.ndarray) -> dict:
    """1 起点ぶんのシナリオ `[m, horizon]`（日次対数）→ 予測出力の数値（プラン §7）。"""
    cum = cumulative_returns(paths)
    return {"probability_up_5d": float((cum[:, -1] > 0).mean()),
            "median_return_5d": float(np.median(cum[:, -1])),
            "return_quantiles_by_day": {f"{q * 100:g}%": [float(v) for v in np.quantile(cum, q, axis=0)]
                                        for q in QUANTILES},
            "probability_close_below_minus_5pct": float(drop_event(paths).mean())}


def _acf1(x: np.ndarray) -> float:
    a, b = x[..., :-1].ravel(), x[..., 1:].ravel()
    return float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else float("nan")


def generation_quality(r: np.ndarray) -> dict:
    """生成品質（⚠ **予測精度とは別の表に出す**）。`r` は日次対数リターン `[..., horizon]`（実測でも生成でも同じ式）。"""
    x = np.asarray(r, dtype=np.float64)
    flat = x.ravel()
    sd = flat.std()
    z = (flat - flat.mean()) / sd if sd > 0 else flat * 0
    return {"日次σ": float(sd), "5日σ": float(x.sum(axis=-1).std()), "歪度": float((z ** 3).mean()),
            "尖度": float((z ** 4).mean() - 3), "|r|>3σ の割合": float((np.abs(z) > 3).mean()),
            "自己相関(1) r": _acf1(x), "自己相関(1) |r|": _acf1(np.abs(x))}


def block_bootstrap_mean(d: np.ndarray, block: int, n_boot: int, seed: int) -> dict:
    """移動ブロック再標本化で平均の 95% 区間を出す。⚠ **重なった 5 日予測を独立と数えないため**（プラン §5）。"""
    d = np.asarray(d, dtype=np.float64)
    n = len(d)
    rng = np.random.default_rng(seed)
    k = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, k))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, -1)[:, :n]
    means = d[idx].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return {"mean": float(d.mean()), "ci_low": float(lo), "ci_high": float(hi), "n": int(n),
            "block": int(block), "n_boot": int(n_boot)}
