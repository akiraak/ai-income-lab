"""特徴量 → `data/features/<実験>/<粒度>.parquet`。⚠ **入口は薄く保つ**（引数を読んで ail を呼ぶだけ）。

    python3 -m cli.build --experiment own_only_h1
    python3 -m cli.build --experiment cross_section_h1
    python3 -m cli.build --experiment own_only_h1 --leak     # ⚠ 配線の検査用（未来を混ぜる）
    python3 -m cli.build --experiment impact_ex_2018 --shift-days 365   # ⚠ 偽薬（ex_ の日付を過去へずらす）

⚠ **入力は既定で `adjusted/`。** `--layer raw` は「ルール以前の数字」を再現するときだけ使う。
"""

from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd

from ail import config, registry, runs
from ail.contracts import META_COLUMNS
from ail.data import store
from ail.features import labels
import ail.bootstrap  # noqa: F401

ORDER = ("own", "cs", "rel", "ll", "ex", "im")   # ⚠ 列の並びを実行ごとに変えない（cs は own に依存する）
# ⚠ **`ex` と `im` は価格に依存しない**（外部系列を貼るだけ）が、並びは固定する


def build(experiment: str, layer: str = "adjusted", leak: bool = False,
          max_elapsed: float | None = None, out: str | None = None,
          shift_days: int = 0) -> pd.DataFrame:
    exp = config.resolve_experiment(experiment)
    table = runs.variant(experiment, leak, shift_days)      # ⚠ 表の名前（下のループの name と別）
    if shift_days:
        # ⚠ **偽薬: `ex_` の日付だけを過去へずらす。** ⚠ **`ex_` の無い実験では何もずれないので止める**
        if "ex" not in exp.get("feature_layers", []):
            raise SystemExit(f"⚠ --shift-days は ex_ 層を使う実験だけ（{experiment} は ex を持たない）")
        exp.setdefault("features", {})["ex_shift_days"] = int(shift_days)
    ds = exp["_dataset"]
    period, horizon = ds["period"], int(exp["horizon"])
    directory = (store.adjusted_dir(period) if layer == "adjusted"
                 else store.raw_dir(ds["source"], period))
    # ⚠ **行の並びを実行ごとに変えない。** 並びが変わると `--sample` の間引きと
    # ⚠ **ウォークフォワードの分割の境目が変わり、同じ設定でも数字が動く。**
    order = sorted(exp["_symbols"], key=lambda s: f"{store.to_filename(s)}_{period}.csv")
    panel = store.load_panel(directory, order)
    if not panel:
        raise SystemExit(f"{directory} が空。先に cli.fetch / cli.adjust を回す")

    u = config.universe(ds["universe"])
    market, _sectors = config.market_proxy(ds["universe"])
    # ⚠ **`universe` を渡すのは `im_` 層のため**（config/exposure/<universe>.toml を引く）
    ctx = {"market": market, "sector_of": u.get("sector_of", {}), "universe": ds["universe"],
           "etf": u.get("groups", {}).get("etf", []), **exp.get("features", {})}

    wanted = [l for l in ORDER if l in exp.get("feature_layers", ["own"])]
    if "own" not in wanted:
        wanted.insert(0, "own")        # ⚠ cs / rel / ll は own を土台にするので必ず作る
    parts: dict[str, dict[str, pd.DataFrame]] = {}
    for name in wanted:
        parts[name] = registry.resolve("feature", name)(panel, ctx)
        if name == "own":
            ctx["own"] = parts["own"]
        n = len(next(iter(parts[name].values())).columns)
        print(f"  {name:<4} {n:>5} 列", flush=True)

    keep = [l for l in wanted if l in exp.get("feature_layers", ["own"])]

    # ⚠ **予測の対象と、説明変数に使うだけの銘柄を分ける。**
    # ⚠ **ETF はセクター相対（`rel_sec_*`）を持てないので、混ぜたまま dropna すると黙って全部消える**
    #    （2026-09-08 に踏んだ。15 本の ETF が行から消えていた）。⚠ **消すなら明示的に消す。**
    targets = exp.get("targets", "all")
    if targets == "all":
        wanted_symbols = list(panel)
    else:
        wanted_symbols = [x for x in u.get("groups", {}).get(targets, []) if x in panel]
        if not wanted_symbols:
            raise SystemExit(f"targets = {targets!r} に当たる銘柄が universe に無い")
    print(f"  対象 {len(wanted_symbols)} 銘柄 / 説明変数に使う {len(panel)} 銘柄"
          + (f"（⚠ **{targets} だけを予測する**）" if targets != "all" else ""))

    targeted = set(wanted_symbols)
    frames, order_used = [], []
    for s, bars in panel.items():
        if s not in targeted:
            continue
        order_used.append(s)
        cols = [bars[["ts", "close"]]] + [parts[l][s] for l in keep]
        cols.append(labels.build_one(bars, horizon, leak=leak))
        x = pd.concat(labels.trim(cols, horizon), axis=1)
        x.insert(0, "symbol", s)
        frames.append(x)
    df = pd.concat(frames, ignore_index=True)

    # ⚠ **列の集合が銘柄で違うと、dropna がその銘柄を丸ごと消す。** 消える前にここで気づく
    shapes = {s: tuple(f.columns) for s, f in zip(order_used, frames)}
    if len(set(shapes.values())) > 1:
        ref = next(iter(shapes.values()))
        odd = [s for s, c in shapes.items() if c != ref]
        raise SystemExit(f"⚠ 列の集合が銘柄で違う: {odd[:5]}（先に config か features 側を直す）")

    before = len(df)
    # ⚠ **期間を揃える。** ⚠ **層ごとに始まりが違うと、比べているのが層の差か期間の差か分からなくなる**
    # （外部系列は 2018 年から、価格は 1994 年から。§8-4）
    start = exp.get("start_date")
    if start:
        df = df[df["ts"] >= pd.Timestamp(start, tz="UTC")]
        print(f"  期間を {start} 以降に揃える → {len(df):,} 行")
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    if max_elapsed is not None:
        df = df[df["y_elapsed_min"] <= max_elapsed]
    feats = [c for c in df.columns if c not in META_COLUMNS]
    print(f"銘柄 {len(panel)} / 行 {before:,} → 欠損と条件で {len(df):,}")
    print(f"特徴量 {len(feats)} 本" + ("  ⚠ **わざとした先読みの列あり**" if leak else "")
          + (f"  ⚠ **偽薬: ex_ の日付を過去へ {shift_days} 日ずらした**" if shift_days else ""))
    print(f"ラベル y の平均 {df['y'].mean():.3e} / 標準偏差 {df['y'].std():.3e} / "
          f"上がる割合 {df['y_sign'].mean():.4f}")

    if out is None:
        out = os.path.join(store.DATA, "features", table, f"{period}.parquet")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_parquet(out, index=False)

    # ⚠ **どの層から作った表かを、表の隣に書く。** ⚠ `cli.run --layer` は自己申告なので、
    # ⚠ **食い違うと台帳の「データの層」の列がそのまま嘘になる**（`ail/catalog.py`）。
    meta = {"layer": layer, "experiment": experiment, "period": period, "leak": leak,
            "shift_days": int(shift_days),
            "rows": int(len(df)), "features": len(feats),
            "built_at": time.strftime("%Y-%m-%dT%H-%M-%S")}
    with open(os.path.splitext(out)[0] + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"→ {os.path.relpath(out, store.ROOT)}（層 {layer}）")
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--layer", default="adjusted", choices=("adjusted", "raw"),
                    help="⚠ raw は「ルール以前の数字」を再現するときだけ")
    ap.add_argument("--leak", action="store_true", help="⚠ わざと未来を混ぜる（対照実験）")
    ap.add_argument("--shift-days", type=int, default=0,
                    help="⚠ 偽薬: ex_ の系列の日付を過去へ N 日ずらす（表の名前に _shift<N>）")
    ap.add_argument("--max-elapsed", type=float, default=None,
                    help="ラベルが跨いだ実時間の上限（分）。夜跨ぎを落とすのに使う")
    ap.add_argument("--out")
    args = ap.parse_args()
    build(args.experiment, args.layer, args.leak, args.max_elapsed, args.out, args.shift_days)


if __name__ == "__main__":
    main()
