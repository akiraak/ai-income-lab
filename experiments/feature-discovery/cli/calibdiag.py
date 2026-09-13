"""買い% の幅が潰れる原因を切り分ける診断（[プラン](../../../docs/plans/archive/buy-pct-width-collapse.md)）。

    python3 -m cli.calibdiag --experiment sel_small4_1995
    python3 -m cli.calibdiag --experiment trend_scales_1995
    python3 -m cli.calibdiag --curve            # AUC → 幅 の対応だけ引く

⚠ **これは検証ではなく診断である。** ⚠ **`runs/` には 1 バイトも書かない** — 出力は
`out/diag/<時刻>/` に落とす。⚠ **`config.json` を持つディレクトリを作らないので
`catalog._read_run` が拾わず、`n_trials` も台帳も動かない**（rules.md 10 章・13-9）。

切り分ける容疑は 3 つ（プラン §2）。

| 容疑 | 見る列 | 効いていると言える形 |
| --- | --- | --- |
| 1 予測に散らばりが無い | `pred_std` | ⚠ **0 に近い** |
| 2 Platt が最尤解に届いていない | `dll = ll_std − ll_now` | ⚠ **正**（同じ目的関数なので、届いていれば 0） |
| 3 AUC 0.52 では幅 20 点が出ない | `width_std` ／ `--curve` | ⚠ **解き直しても 20 点に届かない** |
"""

from __future__ import annotations

import argparse
import json
import os
import time
import warnings

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail import config, registry, runs
from ail.contracts import META_COLUMNS
from ail.data import store
from ail.models import calibrate
from ail.models.holdout import date_holdout, tail_holdout
from ail.validation import prep, splits
from cli.run import load_panel
import ail.bootstrap  # noqa: F401

warnings.filterwarnings("ignore")

OUT = os.path.join(runs.ROOT, "out", "diag")
THRESHOLDS = (50.0, 55.0, 60.0)


# --------------------------------------------------------------------------- 較正の解き直し

def _platt_standardized(pred: np.ndarray, up: np.ndarray) -> tuple[float, float]:
    """⚠ **予測を標準化してから解き、元のスケールへ戻す。**

    ⚠ **変えたのは解き方だけ**で、モデル（1 変数ロジスティック）も標本も目的関数も同じ。
    だから ⚠ **対数尤度が上がったら、現行の解が最尤解でなかったことの証明になる**（プラン §2-1）。
    """
    from sklearn.linear_model import LogisticRegression

    mu, sd = float(np.mean(pred)), float(np.std(pred))
    if sd == 0.0:
        return 0.0, 0.0
    z = (pred - mu) / sd
    m = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000, tol=1e-10)
    m.fit(z.reshape(-1, 1), up.astype(int))
    a_z, b_z = float(m.coef_[0, 0]), float(m.intercept_[0])
    return a_z / sd, b_z - a_z * mu / sd


def _loglik(pred: np.ndarray, up: np.ndarray, a: float, b: float) -> float:
    """⚠ **平均対数尤度**（行数が違う fold を横に並べても読めるようにする）。"""
    z = np.clip(a * pred + b, -500, 500)
    p = 1.0 / (1.0 + np.exp(-z))
    eps = 1e-12
    return float(np.mean(np.where(up, np.log(p + eps), np.log1p(-p + eps))))


def _auc(target, pred) -> float | None:
    from sklearn.metrics import roc_auc_score

    up = np.asarray(target, dtype=float) > 0
    if up.all() or (~up).all():
        return None
    return float(roc_auc_score(up, np.asarray(pred, dtype=float)))


def _width(buy: np.ndarray) -> float:
    return float(np.percentile(buy, 95) - np.percentile(buy, 5))


def _crossings(buy: np.ndarray) -> dict:
    """⚠ **θ の上に居る日の割合。** 0 か 1 に張り付く ＝ 状態機械が「ずっと休む / ずっと持つ」に潰れる。

    ⚠ **最小値と最大値で「跨ぐか」を見ても足りない** — 1 日だけ超える系列は跨ぐが、
    ⚠ **売買はほとんど変わらない**（保有日率 0.998 の形）。⚠ **割合で見る。**
    """
    return {f"上θ{int(t)}": round(float(np.mean(buy > t)), 4) for t in THRESHOLDS}


def _row(pred_fit, target_fit, pred_te, y_te, source: str) -> dict:
    """1 fold × 1 手法。⚠ **現行の解と標準化した解を、同じ標本の上で並べる。**"""
    pred_fit = np.asarray(pred_fit, dtype=float)
    up = np.asarray(target_fit, dtype=float) > 0
    cal = calibrate.fit_from_predictions(pred_fit, target_fit, source)
    a1, b1 = _platt_standardized(pred_fit, up) if cal.source != "constant" else (0.0, cal.b)
    buy_fit_now, buy_te_now = cal.buy_pct(pred_fit), cal.buy_pct(pred_te)
    std_cal = calibrate.Calibration(a=a1, b=b1, source="std")
    buy_fit_std, buy_te_std = std_cal.buy_pct(pred_fit), std_cal.buy_pct(pred_te)
    ll0, ll1 = _loglik(pred_fit, up, cal.a, cal.b), _loglik(pred_fit, up, a1, b1)
    return {
        "較正元": cal.source, "較正の行": int(len(pred_fit)),
        "上がる割合": round(float(up.mean()), 4),
        # 容疑 1: 予測そのもの
        "pred_std": float(np.std(pred_fit)),
        "pred_p05": float(np.percentile(pred_fit, 5)),
        "pred_p95": float(np.percentile(pred_fit, 95)),
        "auc_fit": _auc(target_fit, pred_fit), "auc_te": _auc(y_te, pred_te),
        # 容疑 2: 解けているか
        "a_now": cal.a, "b_now": cal.b, "a_std": a1, "b_std": b1,
        "ll_now": ll0, "ll_std": ll1, "dll": ll1 - ll0,
        # 容疑 3: 解き直しても幅が出るか
        "width_fit_now": _width(buy_fit_now), "width_fit_std": _width(buy_fit_std),
        "width_te_now": _width(buy_te_now), "width_te_std": _width(buy_te_std),
        "buy_te_now_中央": float(np.median(buy_te_now)),
        "buy_te_std_中央": float(np.median(buy_te_std)),
        **{k + "(現行)": v for k, v in _crossings(buy_te_now).items()},
        **{k + "(解直)": v for k, v in _crossings(buy_te_std).items()},
    }


# --------------------------------------------------------------------------- 選別 × モデル

def _selector_rows(panel, feats, exp, edges, v, ctx, k, model) -> list[dict]:
    """⚠ **`cli/run.py` の (A) 共通の経路をそのままなぞる**（較正 → 買い%）。"""
    selectors = registry.resolve_all("selector", exp.get("selectors", []))
    rows: list[dict] = []
    for f, tr, te in splits.folds_by_dates(panel, edges, float(exp["horizon_min"]),
                                           int(v.get("embargo_bars", 0)),
                                           float(exp.get("bar_minutes", 0.0))):
        sc = StandardScaler().fit(tr[feats])
        Xtr = pd.DataFrame(sc.transform(tr[feats]), columns=feats)
        Xte = pd.DataFrame(sc.transform(te[feats]), columns=feats)
        ytr, yte = tr["y"].to_numpy(dtype=float), te["y"].to_numpy(dtype=float)
        Xtr, Xte, _ = prep.apply(exp, Xtr, Xte, ctx)
        for name, fn in selectors.items():
            cols = fn(Xtr, ytr, k, ctx)
            # ⚠ **`calibrate.fit` の中身をそのまま開く**（同じ tail holdout・同じ model 呼び）
            (Xh, yh), hold = tail_holdout(Xtr[cols], ytr)
            if hold is not None:
                Xv, yv = hold
                pred_fit, target, source = model(Xh, yh, Xv, ctx), yv, "holdout"
            else:
                pred_fit, target, source = model(Xtr[cols], ytr, Xtr[cols], ctx), ytr, "train"
            pred_te = model(Xtr[cols], ytr, Xte[cols], ctx)
            rows.append({"fold": f, "系統": "選別", "手法": prep.label(exp, name),
                         "列": len(cols),
                         **_row(np.asarray(pred_fit, float), np.asarray(target, float),
                                np.asarray(pred_te, float), yte, source)})
    return rows


# --------------------------------------------------------------------------- 検知器

def _detector_rows(panel, feats, exp, edges, v, ctx) -> list[dict]:
    """⚠ **`ail/detectors/scale.py` の `scale_gate` を開いてなぞる。**

    ⚠ **C 系（`classic_filter`）は学習しないので較正が無い。** 幅だけ並べて、
    ⚠ **「幅 100 点」が予測力の証明ではない**ことを同じ表で見えるようにする（プラン §1）。
    """
    from ail.detectors import scale
    from ail.features import labels, trend

    model = ctx["model"]
    rows: list[dict] = []
    for f, tr, te in splits.folds_by_dates(panel, edges, float(exp["horizon_min"]),
                                           int(v.get("embargo_bars", 0)),
                                           float(exp.get("bar_minutes", 0.0))):
        yte = te["y"].to_numpy(dtype=float)
        for label, w in scale.SCALES.items():
            cols = trend.scale_columns(feats, w)
            target_col = f"{labels.SCALE_PREFIX}{w}"
            tr_ok = tr.loc[tr[target_col].notna().to_numpy()]
            sc = StandardScaler().fit(tr_ok[cols])
            Xtr = pd.DataFrame(sc.transform(tr_ok[cols]), columns=cols)
            Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
            ytr = tr_ok[target_col].to_numpy(dtype=float)
            split = date_holdout(tr_ok["ts"], label_bars=w)
            if split is None:
                pred_fit, target, source = model(Xtr, ytr, Xtr, ctx), ytr, "train"
            else:
                head, hold = split
                pred_fit = model(Xtr[head], ytr[head], Xtr[hold], ctx)
                target, source = ytr[hold], "holdout"
            pred_te = model(Xtr, ytr, Xte, ctx)
            rows.append({"fold": f, "系統": "D 学習ゲート", "手法": f"{label}{w}日", "列": len(cols),
                         **_row(np.asarray(pred_fit, float), np.asarray(target, float),
                                np.asarray(pred_te, float), yte, source)})
            # ⚠ **同じ fold・同じスケールの C 系**（学習しない規則）
            buy_c = np.where(te[f"{trend.PREFIX}trend{w}_dist"].to_numpy(dtype=float) > 0,
                             100.0, 0.0)
            rows.append({"fold": f, "系統": "C 古典フィルタ", "手法": f"{label} SMA{w}", "列": 1,
                         "較正元": "none（学習しない）", "auc_te": _auc(yte, buy_c),
                         "width_te_now": _width(buy_c), "width_te_std": _width(buy_c),
                         "buy_te_now_中央": float(np.median(buy_c)),
                         **{k + "(現行)": v2 for k, v2 in _crossings(buy_c).items()}})
    return rows


# --------------------------------------------------------------------------- AUC → 幅

def curve(n: int = 3000, seed: int = 0) -> pd.DataFrame:
    """⚠ **正しく較正したときの「AUC → 買い% の幅」を実測で引く**（プラン §2-2）。

    ⚠ **手法の話ではない。** 正規の点数と 2 値の結果という素直な形で、⚠ **信号の強さだけを振る。**
    ⚠ **ここで出る幅は上限に近い**（実データの予測は正規より裾が重く、幅はもっと出にくい）。
    """
    rng = np.random.default_rng(seed)
    out = []
    for d in np.arange(0.0, 1.01, 0.05):          # 標準化した平均差
        z = rng.standard_normal(n)
        up = (d * z + rng.standard_normal(n)) > 0
        a, b = _platt_standardized(z, up)
        buy = calibrate.Calibration(a=a, b=b, source="std").buy_pct(z)
        out.append({"平均差": round(float(d), 3), "AUC": _auc(up.astype(float) - 0.5, z),
                    "幅_pt": _width(buy), "p05": float(np.percentile(buy, 5)),
                    "p95": float(np.percentile(buy, 95)),
                    **{f"上θ{int(t)}": round(float(np.mean(buy > t)), 4) for t in THRESHOLDS}})
    return pd.DataFrame(out)


# --------------------------------------------------------------------------- 実行

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment")
    ap.add_argument("--leak", action="store_true")
    ap.add_argument("--curve", action="store_true", help="AUC → 幅 の対応だけ引く")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
    tag = args.tag or (args.experiment or "curve") + ("_leak" if args.leak else "")
    d = os.path.join(OUT, f"{stamp}_{tag}")
    os.makedirs(d, exist_ok=True)

    if args.curve:
        c = curve()
        c.to_csv(os.path.join(d, "auc_width.csv"), index=False)
        print(c.to_string(index=False))
        print(f"→ {os.path.relpath(d, runs.ROOT)}")
        return

    exp = config.resolve_experiment(args.experiment)
    ds = exp["_dataset"]
    panel, path = load_panel(args.experiment, ds["period"], args.leak, exp.get("features_from"))
    feats = [c for c in panel.columns if c not in META_COLUMNS]
    exp.setdefault("horizon_min", 1440.0 if ds["period"] == "d" else float(exp["horizon"]))
    v = exp.get("validation", {})
    k = int(exp.get("k", 8))
    model = registry.resolve("model", exp.get("model", "Ridge"))
    ctx = {"seed": int(v.get("seed", 0)), "model": model, "k": k, **exp.get("model_args", {})}
    edges = splits.date_edges(panel["ts"], int(v.get("folds", 5)))

    print(f"診断 {args.experiment}{' (leak)' if args.leak else ''} / 行 {len(panel):,} / 列 {len(feats)}")
    rows = (_detector_rows(panel, feats, exp, edges, v, ctx) if exp.get("detectors")
            else _selector_rows(panel, feats, exp, edges, v, ctx, k, model))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(d, "folds.csv"), index=False)
    json.dump({"実験": args.experiment, "leak": args.leak,
               "表": os.path.relpath(path, store.ROOT), "行": int(len(panel)),
               "⚠ 注記": "診断であって検証ではない。runs/ に書かないので n_trials は動かない"},
              open(os.path.join(d, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    cols = ["fold", "手法", "較正元", "pred_std", "auc_fit", "a_now", "a_std",
            "ll_now", "ll_std", "dll", "width_fit_now", "width_fit_std",
            "width_te_now", "width_te_std", "buy_te_std_中央"]
    show = [c for c in cols if c in df.columns]
    with pd.option_context("display.width", 220, "display.max_columns", 40):
        print(df[show].to_string(index=False, float_format=lambda x: f"{x:.5g}"))
    print(f"→ {os.path.relpath(d, runs.ROOT)}")


if __name__ == "__main__":
    main()
