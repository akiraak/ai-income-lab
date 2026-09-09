"""特徴量 → `data/features/<実験>/<粒度>.parquet`。⚠ **入口は薄く保つ**（引数を読んで ail を呼ぶだけ）。

    python3 -m cli.build --experiment own_only_h1
    python3 -m cli.build --experiment cross_section_h1
    python3 -m cli.build --experiment own_only_h1 --leak     # ⚠ 配線の検査用（未来を混ぜる）

⚠ **入力は既定で `adjusted/`。** `--layer raw` は「ルール以前の数字」を再現するときだけ使う。
"""

from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

from ail import config, registry
from ail.contracts import META_COLUMNS
from ail.data import store
from ail.features import labels
import ail.bootstrap  # noqa: F401

ORDER = ("own", "cs", "rel", "ll")     # ⚠ 列の並びを実行ごとに変えない（cs は own に依存する）


def build(experiment: str, layer: str = "adjusted", leak: bool = False,
          max_elapsed: float | None = None, out: str | None = None) -> pd.DataFrame:
    exp = config.resolve_experiment(experiment)
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
    ctx = {"market": market, "sector_of": u.get("sector_of", {}),
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
    df = df.replace([np.inf, -np.inf], np.nan).dropna()
    if max_elapsed is not None:
        df = df[df["y_elapsed_min"] <= max_elapsed]
    feats = [c for c in df.columns if c not in META_COLUMNS]
    print(f"銘柄 {len(panel)} / 行 {before:,} → 欠損と条件で {len(df):,}")
    print(f"特徴量 {len(feats)} 本" + ("  ⚠ **わざとした先読みの列あり**" if leak else ""))
    print(f"ラベル y の平均 {df['y'].mean():.3e} / 標準偏差 {df['y'].std():.3e} / "
          f"上がる割合 {df['y_sign'].mean():.4f}")

    if out is None:
        out = os.path.join(store.DATA, "features", experiment + ("_leak" if leak else ""),
                           f"{period}.parquet")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"→ {os.path.relpath(out, store.ROOT)}")
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--layer", default="adjusted", choices=("adjusted", "raw"),
                    help="⚠ raw は「ルール以前の数字」を再現するときだけ")
    ap.add_argument("--leak", action="store_true", help="⚠ わざと未来を混ぜる（対照実験）")
    ap.add_argument("--max-elapsed", type=float, default=None,
                    help="ラベルが跨いだ実時間の上限（分）。夜跨ぎを落とすのに使う")
    ap.add_argument("--out")
    args = ap.parse_args()
    build(args.experiment, args.layer, args.leak, args.max_elapsed, args.out)


if __name__ == "__main__":
    main()
