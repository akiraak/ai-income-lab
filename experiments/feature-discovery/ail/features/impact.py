"""`im_` ⚠ **災害を地域と業種に割り当てた層。** ⚠ **`ex_` との違いは 1 点だけ — 銘柄ごとに値が変わる。**

⚠ **なぜこの層を作ったのか。** [§9-4](../../../../docs/specs/experiments/daily-data-sources.md) の判定:

| 層 | 値 | ⚠ 何に効きうるか |
| --- | --- | --- |
| `ex_` | ⚠ **全銘柄で同じ** | ⚠ **市場全体の方向だけ。** 銘柄の選択には効きようがない |
| ⚠ **`im_`** | ⚠ **銘柄ごとに違う** | ⚠ **断面（どの銘柄が上がるか）が動きうる** |

⚠ **作りは 1 行で言える。** ⚠ **`im_<経路>(銘柄, 日) = Σ 重み[銘柄][地域] × 変換した災害の系列[地域](日)`。**

⚠ **重みを先に掛けてはいけない。** ⚠ **`z20` は定数倍で消える**（`z(k·x) = z(x)`）ので、
⚠ **先に重みを掛けると全銘柄で同じ値に戻る**（この層を作った意味が消える）。
⚠ **変換してから重みを掛ける。**

⚠ **曝露が 0 の銘柄は 0 になる。** ⚠ **0 は欠損ではない**（「その災害に反応する商売を持たない」という値である）。

> ⚠ **偽薬は「割り当ての入れ替え」である**（`im_scramble`）。⚠ **系列も重みの分布もそのままで、
> 銘柄への割り当てだけを混ぜる。** ⚠ **本物の割り当てが入れ替えを超えられないなら、
> 効いて見えたのは「曝露にばらつきがあること」であって、割り当ての中身ではない。**
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail import config
from ail.data import regions
from ail.features.exog import asof_join, load_series, transform
from ail.registry import register

PREFIX = "im_"
# ⚠ **偽薬（割り当ての入れ替え）を同じ表に並べるときの接頭辞。**
# ⚠ **1 回の実行で本物と偽薬を並べると、「選別手法がどちらを選んだか」がそのまま偽発見率になる**
# （[§9-3](../../../../docs/specs/experiments/daily-data-sources.md) と同じ読み方）
PLACEBO_PREFIX = "im_pb_"
DEFAULT_LAG_DAYS = 1
# ⚠ **取得元ごとの発表の遅れ**（`ex_` と同じ表を持つ）。⚠ **NCEI Storm Events は 101 日遅れる**【実測】
DEFAULT_SOURCE_LAG_DAYS = {"ncei_storm": 120}
# ⚠ **行が無い日 = その事象が 0 件だった日。** 災害も警報も地震と同じ形（前方埋め禁止）
DEFAULT_ZERO_FILL = ("ncei_storm", "iem", "usgs")
DEFAULT_TRANSFORMS = ("d1", "z20")
DEFAULT_MAX_STALE_DAYS = 7


def weights_of(conf: dict, table: str, symbol: str) -> dict[str, float]:
    """重みを引く。⚠ **書いていない銘柄は 0**（欠損ではない）。

    `kind = "scalar"` の表は数を 1 個持つので、`{"": 値}` の形に揃えて返す。
    """
    raw = (conf.get("weights") or {}).get(table) or {}
    w = raw.get(symbol)
    if w is None:
        return {}
    if isinstance(w, dict):
        return {k: float(v) for k, v in w.items() if float(v) != 0.0}
    return {"": float(w)} if float(w) != 0.0 else {}


def alias_map(symbols: list[str], scramble: bool, seed: int, tries: int = 100) -> dict[str, str]:
    """⚠ **偽薬の割り当て。** 銘柄 → 「重みを引くときに使う別の銘柄」。

    ⚠ **並べ替えなので、重みの分布（何本が曝露を持つか、その大きさ）は本物と完全に同じ。**
    ⚠ **違うのは「誰に付いているか」だけである。**

    ⚠ **自分に戻る銘柄を作らない**（撹乱。derangement）。⚠ **1 本でも本物の重みが残ると、
    そのぶん偽薬が本物に近づき、差が薄まる。**
    """
    order = sorted(symbols)
    if not scramble or len(order) < 2:
        return {s: s for s in order}
    rng = np.random.default_rng(seed)
    for _ in range(tries):
        perm = list(rng.permutation(len(order)))
        if all(i != j for i, j in enumerate(perm)):
            return {order[i]: order[j] for i, j in enumerate(perm)}
    # ⚠ **引けなかったら 1 つずらす**（撹乱であることは保証される）
    return {order[i]: order[(i + 1) % len(order)] for i in range(len(order))}


def _channel_frame(ch: dict, series: dict[str, pd.Series],
                   kinds: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    """1 経路の「地域 → 変換した表」。⚠ **ここまでは銘柄に依存しない**（重みは後で掛ける）。

    ⚠ **1 本でも欠けたら止める。** ⚠ **黙って足し忘れると、地域が 3 つの重み付き和になり、
    どの地域が抜けたか分からないまま数字だけが出る。**
    """
    if ch.get("kind") == "region":
        want = {r: ch["series"].format(region=r) for r in regions.REGIONS}
    else:
        want = {"": ch["series"]}
    missing = [sid for sid in want.values() if sid not in series]
    if missing:
        raise SystemExit(f"⚠ 経路 {ch['name']} の系列が無い: {', '.join(missing)}。先に取得する")
    return {key: transform(series[sid], kinds) for key, sid in want.items()}


@register("feature", "im")
def layer(panel: dict[str, pd.DataFrame], ctx: dict) -> dict[str, pd.DataFrame]:
    lag = int(ctx.get("im_lag_days", DEFAULT_LAG_DAYS))
    if lag < 1:
        # ⚠ **`ex_` と同じ理由。** 発表の遅れを無視すると、その日のうちに知り得ない値を使う
        raise ValueError(f"⚠ im_lag_days は 1 以上でなければならない: {lag}")
    name = ctx.get("im_exposure") or ctx.get("universe") or "us63"
    conf = config.exposure(name)
    only = set(ctx.get("im_channels") or ())
    channels = [c for c in conf.get("channel", []) if not only or c["name"] in only]
    if not channels:
        raise SystemExit(f"⚠ config/exposure/{name}.toml に経路が 1 つも無い")

    kinds = tuple(ctx.get("im_transforms", DEFAULT_TRANSFORMS))
    max_stale = int(ctx.get("im_max_stale_days", DEFAULT_MAX_STALE_DAYS))
    zero_fill = tuple(ctx.get("im_zero_fill", DEFAULT_ZERO_FILL))
    src_lag = {**DEFAULT_SOURCE_LAG_DAYS, **dict(ctx.get("im_source_lag_days", {}))}
    sources = tuple(sorted({c["source"] for c in channels}))

    series, _owner = load_series(sources, zero_fill)
    if not series:
        raise SystemExit("⚠ 外部系列が 1 本も無い。先に `python3 -m cli.fetch --exog impact_warnings` を回す")

    scramble = bool(ctx.get("im_scramble", False))
    seed = int(ctx.get("im_scramble_seed", 0))
    # ⚠ **同じ表に本物と偽薬を並べるか。** ⚠ **並べると偽発見率をその実行の中で測れる**
    both = bool(ctx.get("im_with_placebo", False))
    variants = [(PREFIX, alias_map(list(panel), scramble, seed))]
    if both:
        if scramble:
            raise SystemExit("⚠ im_with_placebo と im_scramble は同時に立てない"
                             "（本物の側まで入れ替わり、比べる相手が消える）")
        variants.append((PLACEBO_PREFIX, alias_map(list(panel), True, seed)))

    out: dict[str, list[pd.DataFrame]] = {sym: [] for sym in panel}
    for ch in channels:
        frames = _channel_frame(ch, series, kinds)
        width = max(int(src_lag.get(ch["source"], lag)), 1)
        for prefix, alias in variants:
            cols = [f"{prefix}{ch['name']}_{k}" for k in kinds]
            # ⚠ **重みごとに 1 回だけ as-of を貼る**（同じ重みの銘柄で計算を使い回す）
            cache: dict[tuple, pd.DataFrame] = {}
            for sym, bars in panel.items():
                w = weights_of(conf, ch["weights"], alias[sym])
                key = tuple(sorted(w.items()))
                wide = cache.get(key)
                if wide is None:
                    # ⚠ **変換してから重みを掛ける**（先に掛けると z20 で消える）
                    acc = sum(frames[k] * v for k, v in w.items() if k in frames)
                    if not isinstance(acc, pd.DataFrame):
                        # ⚠ **曝露なし = 0。** ⚠ **欠損にしない**（0 は「反応する商売を持たない」という値）
                        acc = pd.DataFrame(0.0, index=next(iter(frames.values())).index,
                                           columns=list(kinds))
                    wide = cache[key] = acc.sort_index()
                picked = asof_join(wide, bars["ts"], width, max_stale)
                picked.columns = cols
                picked.index = bars.index
                out[sym].append(picked)
    return {sym: pd.concat(parts, axis=1) for sym, parts in out.items()}
