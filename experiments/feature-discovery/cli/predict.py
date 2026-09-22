"""実売買の「今日の買い%」（[プラン §2-6](../../../docs/plans/live-trading-three-models.md)・Phase 1）。

    AIL_DATA_DIR=data-live python3 -m cli.predict --experiment trade_own_ridge_a --asof 2026-09-21 \
        --out ../live-trading/out/2026-09-21/predict.jsonl
    python3 -m cli.predict --experiment trade_ownseq_ridge_a --method "T3 QUANT（60日窓）" --asof 2026-09-04

⚠ **モデル・較正・変換のコードはここに 1 行も無い。** 足すのは配線だけ:
  表 ＝ `cli.build.assemble`（`build` の前半そのもの）／ 買い% ＝ `cli.run.fold_buy_pct`（バックテストの fold の中身そのもの）。
  違うのは塊の切り方だけ ＝ **訓練: ラベルが `asof` より前に確定している行 ／ 検証: `asof` の行**。

⚠ **先読みをしない**（rules.md 7 章）: `asof` より後の足は読んだ直後に捨てる。`asof` の終値（15:50 の代役）を
  含むラベルの行（＝ 前の営業日の行）は訓練に入れない。`asof` の行の `y` は見ない。
⚠ **`runs/` を作らない・台帳に出ない**（試行ではない。既存の構成をそのまま通すだけ）。
⚠ **研究用の表（`data/features/`）を読まない・書かない**（足から毎回メモリ上で組む）。実売買では `AIL_DATA_DIR` を
  実売買用の置き場（`live_update.sh` の `data-live/`）に向ける。
⚠ **`asof` の足**: 置き場にあればそれを使う（市場時間中に `live_update.sh` を流すと「今日の途中の足」が入る ＝ 終値の代役）。
  `--proxy` は足を上書き ／ 追加する口（検算と、足が取れなかったときの予備）。代役と公式終値の差は差 1 の一部として後で測る。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import warnings

import numpy as np
import pandas as pd

from ail import contracts, registry
from ail.data import store
from ail.models import calibrate
from ail.validation import prep
from cli import build as build_cli
from cli import run as run_cli
import ail.bootstrap  # noqa: F401

warnings.filterwarnings("ignore")


class PredictError(SystemExit):
    pass


def _asof_ts(asof: str) -> pd.Timestamp:
    return pd.Timestamp(asof, tz="UTC").normalize()


def make_panel_hook(asof: pd.Timestamp, proxy: dict | None, notes: dict):
    """足のパネルを「`asof` の時点で見えるもの」にする。⚠ **`asof` より後は捨てる**（先読みの入口を塞ぐ）。"""
    def hook(panel: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        out = {}
        for s, bars in panel.items():
            day = bars["ts"].dt.normalize()
            b = bars[day <= asof].reset_index(drop=True)
            if proxy and s in proxy:
                p = proxy[s]
                missing = [c for c in ("open", "high", "low", "close", "volume") if p.get(c) is None]
                if missing:
                    raise PredictError(f"--proxy の {s} に {missing} が無い（足 1 本ぶん open/high/low/close/volume が要る）")
                b = b[b["ts"].dt.normalize() < asof]
                # ⚠ 足の時刻（UTC の何時か）は直前の足に合わせる（置き場の流儀をそのまま継ぐ）
                offset = (b["ts"].iloc[-1] - b["ts"].iloc[-1].normalize()) if len(b) else pd.Timedelta(0)
                ts = asof + offset
                row = {"time_ms": int(ts.value // 1_000_000), "ts": ts,
                       **{c: float(p[c]) for c in ("open", "high", "low", "close", "volume")}}
                b = pd.concat([b, pd.DataFrame([row])], ignore_index=True)
                notes["proxy_symbols"].append(s)
            out[s] = b
        return out
    return hook


def split_asof(df: pd.DataFrame, feats: list[str], asof: pd.Timestamp, start: str | None,
               horizon: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """(訓練, 検証, 記録)。⚠ **訓練 ＝ ラベルの終わりの足が `asof` より前の行だけ**（`asof` の終値に触れない）。"""
    df = df.replace([np.inf, -np.inf], np.nan)
    if start:
        df = df[df["ts"] >= pd.Timestamp(start, tz="UTC")]
    day = df["ts"].dt.normalize()
    # ラベルの終わりの足の日付 ＝ 同じ銘柄の horizon 本先の ts（`y_elapsed_min` と同じ数え方）
    label_end = (df["ts"] + pd.to_timedelta(df["y_elapsed_min"], unit="m")).dt.normalize()
    tr = df[(day < asof) & (label_end < asof)].dropna()
    te = df[day == asof]
    bad = te[te[feats].isna().any(axis=1)]["symbol"].tolist()
    te = te[~te["symbol"].isin(bad)]
    # ⚠ **`asof` の行のラベルは未来**。モデルの関数は見ないが、念のため潰してから渡す（0 は「無い」の印ではなく詰め物）
    te = te.assign(y=0.0, y_sign=0.0, y_elapsed_min=0.0)
    doc = {"train_rows": int(len(tr)), "train_start": str(tr["ts"].min().date()) if len(tr) else None,
           "train_end": str(tr["ts"].max().date()) if len(tr) else None,
           "test_rows": int(len(te)), "dropped_symbols_nan_features": bad, "horizon": horizon}
    return tr, te, doc


def predict(experiment: str, asof: str, method: str | None = None, layer: str = "adjusted",
            proxy: dict | None = None, quiet: bool = True) -> tuple[list[dict], dict]:
    """1 実験 × 1 手法の `asof` の買い% ／ 出口%。戻り値は (predict.jsonl の行, 記録)。"""
    from ail import config

    t0 = time.time()
    a = _asof_ts(asof)
    exp = config.resolve_experiment(experiment)
    if (exp.get("trading") or {}).get("style") != "threshold":
        raise PredictError(f"{experiment} は閾値売買の実験ではない（[trading] style = threshold が要る）")
    table_exp = exp.get("features_from") or experiment      # ⚠ 表の持ち主（rules.md 13-8。cli.run と同じ）
    notes: dict = {"proxy_symbols": []}
    sink = io.StringIO() if quiet else sys.stdout
    with contextlib.redirect_stdout(sink):
        df, panel, texp = build_cli.assemble(table_exp, layer, keep_tail=True,
                                             panel_hook=make_panel_hook(a, proxy, notes))
    feats = contracts.feature_columns(df)
    horizon = int(texp["horizon"])
    tr, te, doc = split_asof(df, feats, a, texp.get("start_date"), horizon)
    if te.empty:
        last = max((b["ts"].max() for b in panel.values() if len(b)), default=None)
        raise PredictError(f"{asof} の行が無い（足の最終日 {None if last is None else last.date()}）。"
                           "先に live_update.sh を流すか --proxy を渡す")
    if len(tr) < 100:
        raise PredictError(f"訓練が {len(tr)} 行しか無い")
    t_table = time.time()

    # --- ここから `cli.run.evaluate_trading` と同じ解決。手法は 1 本に絞る（実売買で使う 1 本だけ fit する）
    t = exp["trading"]
    form = str(t.get("form", "shared"))
    v = exp.get("validation", {})
    k = int(exp.get("k", 8))
    seed = int(v.get("seed", 0))
    exp.setdefault("horizon_min", 1440.0 if exp["_dataset"]["period"] == "d" else float(exp["horizon"]))
    model = registry.resolve("model", exp.get("model", "Ridge"))
    ctx = {"seed": seed, "model": model, "k": k, **exp.get("model_args", {})}
    names_d = list(exp.get("detectors", []))
    names_s = [] if names_d else list(exp.get("selectors", []))
    labels = {n: (n if names_d else prep.label(exp, n)) for n in (names_d or names_s)}
    if method is None:
        if len(labels) != 1:
            raise PredictError(f"--method が要る。{experiment} の手法: {list(labels.values())}")
        method = next(iter(labels.values()))
    pick = [n for n, lab in labels.items() if method in (n, lab)]
    if len(pick) != 1:
        raise PredictError(f"手法 {method!r} は {experiment} に無い。ある手法: {list(labels.values())}")
    detectors = registry.resolve_all("detector", pick) if names_d else {}
    selectors = registry.resolve_all("selector", pick) if not names_d else {}
    label = labels[pick[0]]

    # ⚠ `folds_by_dates` と同じ並べ方（時刻順）で渡す
    tr = tr.sort_values("ts").reset_index(drop=True)
    te = te.sort_values("ts").reset_index(drop=True)
    groups = te.groupby("symbol").indices
    logs: list[str] = []
    buy, exits, _n, fitted = run_cli.fold_buy_pct(
        tr, te, feats, exp, ctx, model=model, selectors=selectors, detectors=detectors,
        baselines={}, form=form, k=k, groups=groups, f="asof", picked=[], log=logs.append)
    bp = np.asarray(buy[label], dtype=float)
    ex = exits.get(label)
    ex = 100.0 - bp if ex is None else np.asarray(ex, dtype=float)
    t_fit = time.time()

    fp = hashlib.sha256()
    fp.update(pd.util.hash_pandas_object(tr[["symbol", "ts"] + feats + ["y"]], index=False).values.tobytes())
    fp.update(pd.util.hash_pandas_object(te[["symbol", "ts"] + feats], index=False).values.tobytes())
    meta = {"experiment": experiment, "method": label, "asof": asof, "table": table_exp, "layer": layer,
            "form": form, "seed": seed, "calibration": calibrate.VERSION, "features": len(feats),
            "input_fingerprint": fp.hexdigest()[:16], "commit": _commit(), "data_dir": os.path.relpath(store.DATA, store.ROOT),
            **doc, "proxy_symbols": notes["proxy_symbols"], "fitted": fitted.get(label),
            "seconds": {"table": round(t_table - t0, 2), "fit_predict": round(t_fit - t_table, 2)},
            "log": logs}
    rows = []
    for i, s in enumerate(te["symbol"].tolist()):
        if np.isnan(bp[i]) or np.isnan(ex[i]):
            continue
        rows.append({"date": asof, "model": experiment, "method": label, "symbol": s,
                     "buy": round(float(bp[i]), 6), "exit": round(float(ex[i]), 6),
                     "proxy_close": float(te["close"].iloc[i]), "proxy": s in notes["proxy_symbols"],
                     "input_fingerprint": meta["input_fingerprint"], "commit": meta["commit"]})
    return rows, meta


def _commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=store.ROOT, capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def write_rows(path: str, rows: list[dict]) -> None:
    """`predict.jsonl` に足す。⚠ **同じ (日付, モデル) の古い行は置き換える**（同じ日に流し直しても二重にならない）。"""
    keep: list[str] = []
    keys = {(r["date"], r["model"]) for r in rows}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.strip() and (lambda o: (o.get("date"), o.get("model")))(json.loads(line)) not in keys:
                    keep.append(line.rstrip("\n"))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for line in keep + [json.dumps(r, ensure_ascii=False) for r in rows]:
            f.write(line + "\n")
    os.replace(tmp, path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--asof", required=True, help="YYYY-MM-DD（この日の行を当てる。これより後の足は読まない）")
    ap.add_argument("--method", default=None, help="実験の中の手法（選別 ／ 検知器の名前）。1 本しか無ければ省ける")
    ap.add_argument("--layer", default="adjusted", choices=("adjusted", "raw"))
    ap.add_argument("--proxy", default=None,
                    help="`asof` の足の代役（JSON: {銘柄: {open, high, low, close, volume}}）。置き場の足を上書き ／ 追加する")
    ap.add_argument("--out", default=None, help="predict.jsonl（無ければ標準出力へ）")
    ap.add_argument("--meta-out", default=None, help="記録（訓練の範囲・指紋・所要時間）の JSON。既定は <out>.meta.jsonl に 1 行足す")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    proxy = None
    if args.proxy:
        with open(args.proxy, encoding="utf-8") as f:
            proxy = json.load(f)
    rows, meta = predict(args.experiment, args.asof, args.method, args.layer, proxy, quiet=not args.verbose)
    if args.out:
        write_rows(args.out, rows)
        mpath = args.meta_out or os.path.splitext(args.out)[0] + ".meta.jsonl"
        with open(mpath, "a", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")
    else:
        for r in rows:
            print(json.dumps(r, ensure_ascii=False))
    s = meta["seconds"]
    print(f"{args.experiment} ／ {meta['method']} ／ {args.asof}: {len(rows)} 銘柄・訓練 {meta['train_rows']:,} 行"
          f"（〜{meta['train_end']}）・表 {s['table']} 秒 ＋ fit {s['fit_predict']} 秒・指紋 {meta['input_fingerprint']}"
          + (f"・⚠ 特徴量が欠けて外した銘柄 {meta['dropped_symbols_nan_features']}" if meta["dropped_symbols_nan_features"] else ""),
          file=sys.stderr)


if __name__ == "__main__":
    main()
