"""実売買用の足の継ぎ足し: 「種（研究用 `data/` の写し）の歴史」＋「取り直した足のうち、種の最終日以降」。

    python3 -m cli.live_merge --seed        # 初回: data/ → AIL_DATA_DIR（us63 の raw・adjusted と外部系列）＋ seed.json
    python3 -m cli.live_merge --staging <取り直した足の置き場（AIL_DATA_DIR の形）>

⚠ **なぜ丸ごと取り直したものを使わないか**【実測 2026-09-19】: DXLink の日足は、取り直すと過去が変わる。
  - 値が小数 2 桁に丸まって返る（種は 6 桁）
  - ⚠ **分割の権利落ち日の前日の終値だけが、調整前の値で返る**（AAPL 2020-08-28 の終値 499.23・前後は 125。始値は調整済み）。
    `cli.adjust` はこれを継ぎ目と見て、それより前の全期間を 1/4 にする ＝ **2020 年に偽の +300% が入る**
    （63 銘柄中 41 銘柄に 2% 超の食い違い。2018 年以降でも AAPL・AMZN・AVGO・GOOGL・NVDA・TSLA）
  → 歴史は種（バックテストと 1 ビットも同じ足）のまま凍らせ、種の最終日以降だけを取り直した足から採る。
⚠ **継ぎ目の検算**: 種の最後の 20 本で、種と取り直しの終値の比の中央値が 1 ± 0.5% に入らない銘柄は継がない（rc=3）。
  外れるのは、種の最終日より後に分割などがあって配信側が過去を付け替えたとき ＝ 人が見て種を作り直す。
⚠ **種の最終日以降に 1 日で ±40% を超える足があれば警告**（上の「前日の終値だけ調整前」の形を疑う）。
⚠ **`data/` は読むだけ**。書くのは `AIL_DATA_DIR` の下だけ（`data/` を指していたら拒否）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time

import pandas as pd

from ail import config
from ail.contracts import BAR_COLUMNS
from ail.data import store

RESEARCH = os.path.join(store.ROOT, "data")
EXOG_SOURCES = ("ecb", "treasury", "noaa", "usgs")
OVERLAP_BARS = 20
OVERLAP_TOL = 0.005
JUMP_WARN = 0.40


def _guard() -> None:
    if os.path.realpath(store.DATA) == os.path.realpath(RESEARCH):
        raise SystemExit("⚠ AIL_DATA_DIR が無い（か data/ を指している）。研究用の data/ には書かない")


def _read(directory: str, symbol: str) -> pd.DataFrame:
    return pd.read_csv(store.path_of(directory, symbol))[list(BAR_COLUMNS)].sort_values("time_ms")


def seed(dataset: str) -> None:
    _guard()
    ds = config.dataset(dataset)
    symbols = config.symbols_of(ds["universe"])
    freeze = {}
    for layer_dir in (("raw", ds["source"], ds["period"]), ("adjusted", ds["period"])):
        src, dst = os.path.join(RESEARCH, *layer_dir), os.path.join(store.DATA, *layer_dir)
        os.makedirs(dst, exist_ok=True)
        for s in symbols:
            shutil.copy2(store.path_of(src, s), store.path_of(dst, s))
    raw = store.raw_dir(ds["source"], ds["period"])
    for s in symbols:
        freeze[s] = int(_read(raw, s)["time_ms"].iloc[-1])
    for srcname in EXOG_SOURCES:
        a = os.path.join(RESEARCH, "raw", srcname, "series")
        if os.path.isdir(a):
            shutil.copytree(a, store.series_dir(srcname), dirs_exist_ok=True)
    doc = {"seeded_from": RESEARCH, "seeded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "research_adjusted_digest": _digest(), "dataset": dataset, "freeze_ms": freeze}
    with open(os.path.join(store.DATA, "seed.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    ends = sorted({str(pd.to_datetime(v, unit="ms").date()) for v in freeze.values()})
    print(f"種: {len(symbols)} 銘柄 ／ 種の最終日 {' '.join(ends)} → {store.DATA}")


def _digest() -> str | None:
    try:
        with open(os.path.join(RESEARCH, "manifests", "adjusted_d.json"), encoding="utf-8") as f:
            import hashlib
            return hashlib.sha256(json.dumps(json.load(f)["series"], sort_keys=True).encode()).hexdigest()[:16]
    except OSError:
        return None


def merge(staging: str, dataset: str) -> int:
    _guard()
    ds = config.dataset(dataset)
    with open(os.path.join(store.DATA, "seed.json"), encoding="utf-8") as f:
        freeze = json.load(f)["freeze_ms"]
    live = store.raw_dir(ds["source"], ds["period"])
    fetched = os.path.join(staging, "raw", ds["source"], ds["period"])
    rc, added = 0, {}
    for s in config.symbols_of(ds["universe"]):
        if not os.path.exists(store.path_of(fetched, s)):
            print(f"  ⚠ {s}: 取り直した足が無い（前のまま）")
            rc = max(rc, 2)
            continue
        base, new = _read(live, s), _read(fetched, s)
        fz = int(freeze[s])
        old = base[base["time_ms"] < fz]
        m = base[base["time_ms"] <= fz].tail(OVERLAP_BARS).merge(new, on="time_ms", suffixes=("_o", "_n"))
        ratio = float((m["close_n"] / m["close_o"]).median()) if len(m) else float("nan")
        if not (abs(ratio - 1.0) <= OVERLAP_TOL):
            print(f"  ⚠ {s}: 継ぎ目が合わない（種の最後の {len(m)} 本で 取り直し ÷ 種 ＝ {ratio:.4f}）→ 継がない。"
                  "種の最終日より後に分割などが無いか見て、種を作り直す")
            rc = 3
            continue
        tail = new[new["time_ms"] >= fz]
        out = pd.concat([old, tail], ignore_index=True)
        jump = out["close"].pct_change().iloc[len(old):].abs()
        if (jump > JUMP_WARN).any():
            when = pd.to_datetime(out["time_ms"].iloc[len(old):][jump > JUMP_WARN], unit="ms").dt.date
            print(f"  ⚠ {s}: 種の最終日以降に 1 日で ±{JUMP_WARN:.0%} 超の足: {', '.join(map(str, when))}")
            rc = max(rc, 2)
        store.write_bars(live, s, out)
        added[s] = len(tail)
    n = sorted(set(added.values()))
    print(f"継いだ: {len(added)} 銘柄 ／ 種の最終日以降の足 {n[0] if n else 0}〜{n[-1] if n else 0} 本")
    from cli.fetch import _record          # ⚠ 書いたら必ず検査して manifest に残す（rules.md 5 章）
    _record(live, "raw", ds["period"], {"source": ds["source"], "dataset": ds["name"],
                                        "live_merge": {"staging": staging, "rc": rc}})
    return rc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="daily")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--staging")
    args = ap.parse_args()
    if args.seed:
        seed(args.dataset)
        return 0
    if not args.staging:
        raise SystemExit("--seed か --staging のどちらかが要る")
    return merge(os.path.abspath(args.staging), args.dataset)


if __name__ == "__main__":
    raise SystemExit(main())
