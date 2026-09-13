"""買い% の幅が潰れる原因を切り分ける診断（[プラン](../../../docs/plans/archive/buy-pct-width-collapse.md)）。

    python3 -m cli.calibdiag --experiment sel_small4_1995
    python3 -m cli.calibdiag --experiment trend_scales_1995
    python3 -m cli.calibdiag --curve            # AUC → 幅 の対応だけ引く
    python3 -m cli.calibdiag --experiment trend_scales_1995 --theta   # θ の置き方 3 案を比べる

⚠ **これは検証ではなく診断である。** ⚠ **`runs/` には 1 バイトも書かない** — 出力は
`out/diag/<時刻>/` に落とす。⚠ **`config.json` を持つディレクトリを作らないので
`catalog._read_run` が拾わず、`n_trials` も台帳も動かない**（rules.md 10 章・13-9）。

切り分ける容疑は 3 つ（プラン §2）。

| 容疑 | 見る列 | 効いていると言える形 |
| --- | --- | --- |
| 1 予測に散らばりが無い | `pred_std` | ⚠ **0 に近い** |
| 2 Platt が最尤解に届いていない | `dll = ll_std − ll_旧` | ⚠ **正**（同じ目的関数なので、届いていれば 0） |
| 3 AUC 0.52 では幅 20 点が出ない | `width_std` ／ `--curve` | ⚠ **解き直しても 20 点に届かない** |

⚠ **2026-09-13 に比較の相手を直した。** ⚠ **本文は 2026-09-12 の処置より前に書かれており、
「現行」＝ `ail/models/calibrate.py` が生のまま解いていた頃の解を指していた。** ⚠ **処置後は
`calibrate` 自身が標準化して解くので、そのままでは「直した解 vs 直した解」を比べることになり、
`dll` が必ず 0 に潰れて「元から解けていた」と読めてしまう**（実測: LightGBM で 1e-10〜1e-12）。
⚠ **旧の解き方（生の予測・`tol` 既定）を `_platt_legacy` で再現し、比較の相手をそちらへ移した。**
⚠ **列の名前も `*_now` → `*_旧` に改めた**（`dll`・`pred_std`・`width_*_std` の意味は変えていない）。

⚠ **`--theta` は θ の置き方の検討用**（[記録](../../../docs/specs/experiments/theta-placement.md)）。
⚠ **3 案（絶対 / 中央値±δ / 分位点）の帯と、入口・出口が立つ日の割合を訓練分割の内側だけで出す。**
⚠ **どれも採用していない**（2026-09-13 の裁定。rules.md 13-3 の 6）ので、⚠ **これは検討の道具であって本番の経路ではない。**
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
    だから ⚠ **対数尤度が上がったら、旧の解が最尤解でなかったことの証明になる**（プラン §2-1）。

    ⚠ **2026-09-12 以降は `ail/models/calibrate._platt` がこれと同じことをしている。**
    ⚠ **`tol` だけがここは 1e-10 で、本番は既定の 1e-4 である** — ⚠ **標準化した後は勾配が
    ⚠ **スケールに引きずられないので、どちらでも同じ解に着く**（実測: a の差は 5 桁目以下）。
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


def _platt_legacy(pred: np.ndarray, up: np.ndarray) -> tuple[float, float, int]:
    """⚠ **2026-09-12 より前の解き方を再現する**（生の予測をそのまま渡し、`tol` は既定 1e-4）。

    ⚠ **これが「旧」の正体である。** ⚠ **本番コードはもう標準化して解いている**ので、
    ⚠ **旧を測るにはここで再現するしかない。** ⚠ **`runs/` の旧行を作った経路そのものではなく、
    ⚠ **同じ解き方の再現である**（旧行は再計算しない — rules.md 13-2 の 6）。

    返り値の 3 つ目は **L-BFGS の反復回数**。⚠ **2 で止まっていたら動かぬ証拠**（プラン §2-2。
    ⚠ **scikit-learn は開始点を 1 回目に数えるので、止まった解は 1 ではなく 2 と出る**）。
    """
    from sklearn.linear_model import LogisticRegression

    m = LogisticRegression(C=1e6, solver="lbfgs", max_iter=1000)
    m.fit(np.asarray(pred, dtype=float).reshape(-1, 1), up.astype(int))
    return float(m.coef_[0, 0]), float(m.intercept_[0]), int(np.max(m.n_iter_))


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
    """⚠ **入口と出口のそれぞれが立つ日の割合。** どちらかが 0 に張り付くと状態機械が潰れる。

    ⚠ **最小値と最大値で「跨ぐか」を見ても足りない** — 1 日だけ超える系列は跨ぐが、
    ⚠ **売買はほとんど変わらない**（保有日率 0.998 の形）。⚠ **割合で見る。**

    ⚠ **出口は `出口% > θ` ＝ `買い% < 100 − θ`**（rules.md 13-4・16-1）。
    ⚠ **θ ≥ 50 なので、出口は必ず「買い% < 50」以下を要求する**（[プラン §2](
    ../../../docs/plans/archive/theta-placement.md)）。
    """
    out = {f"上θ{int(t)}": round(float(np.mean(buy > t)), 4) for t in THRESHOLDS}
    out.update({f"下θ{int(t)}": round(float(np.mean(buy < 100.0 - t)), 4) for t in THRESHOLDS})
    return out


# --------------------------------------------------------------------------- θ の置き方（案の比較）

def _bands(buy_tr: np.ndarray) -> list[dict]:
    """θ ∈ {50,55,60} を 3 通りの置き方で「帯 [下, 上]」に直す（[プラン §3](
    ../../../docs/plans/archive/theta-placement.md)）。⚠ **材料は訓練分割の内側の買い% だけ。**

    ⚠ **いまの契約は帯 [100 − θ, θ] と厳密に同じ**（入口 `買い% > θ`・出口 `買い% < 100 − θ`）。
    ⚠ **だから 3 案は「帯の置き方」の違いとして 1 つの表に並べられる。**

    | 案 | 帯 | ⚠ 現状に戻る条件 |
    | --- | --- | --- |
    | 1 現行（絶対） | [100 − θ, θ] | — |
    | 2 中央値 ± δ（δ = θ − 50） | [中央値 − δ, 中央値 ＋ δ] | ⚠ **中央値が 50 なら現行と完全一致** |
    | 3 分位点 | [Q(100 − θ), Q(θ)] | ⚠ **買い% が 0〜100 に一様なら現行と一致** |
    """
    med = float(np.median(buy_tr))
    out = []
    for t in THRESHOLDS:
        d = t - 50.0
        out.append({"θ": t, "案": "1 現行（絶対）", "下": 100.0 - t, "上": t})
        out.append({"θ": t, "案": "2 中央値±δ", "下": med - d, "上": med + d})
        out.append({"θ": t, "案": "3 分位点",
                    "下": float(np.percentile(buy_tr, 100.0 - t)),
                    "上": float(np.percentile(buy_tr, t))})
    return out


def _theta_rows(bank: list[dict]) -> pd.DataFrame:
    """案ごとに「入口が立つ日 / 出口が立つ日」の割合を訓練分割の上で出す。

    ⚠ **保有日率そのものは出さない** — 状態機械にはヒステリシスがあり、⚠ **保有日率は
    ⚠ **[入口が立つ割合, 1 − 出口が立つ割合] の間に入る。** ⚠ **両端を出して挟む**のが正しく、
    ⚠ **検証 fold を読まずに言えるのはここまでである**（プラン §3 規律 1）。
    """
    rows = []
    for e in bank:
        tr = e["buy_tr"]
        for b in _bands(tr):
            lo, hi = b["下"], b["上"]
            rows.append({"fold": e["fold"], "系統": e["系統"], "手法": e["手法"],
                         "θ": b["θ"], "案": b["案"],
                         "帯下": round(lo, 2), "帯上": round(hi, 2),
                         "買い%中央": round(float(np.median(tr)), 2),
                         "入口が立つ": round(float(np.mean(tr > hi)), 4),
                         "出口が立つ": round(float(np.mean(tr < lo)), 4),
                         "保有日率の下限": round(float(np.mean(tr > hi)), 4),
                         "保有日率の上限": round(1.0 - float(np.mean(tr < lo)), 4)})
    return pd.DataFrame(rows)


def _row(pred_fit, target_fit, pred_te, y_te, source: str) -> dict:
    """1 fold × 1 手法。⚠ **旧の解（`_platt_legacy`）と現行の解を、同じ標本の上で並べる。**

    ⚠ **現行 ＝ `ail/models/calibrate`（2026-09-12 の処置後は標準化して解く）。**
    ⚠ **旧を `calibrate` から取れなくなったので、ここで再現している**（上の注記）。
    """
    pred_fit = np.asarray(pred_fit, dtype=float)
    up = np.asarray(target_fit, dtype=float) > 0
    cal = calibrate.fit_from_predictions(pred_fit, target_fit, source)
    if cal.source == "constant":                     # 片側ラベル・定数予測。旧も同じ定数に落ちる
        a0, b0, it0 = 0.0, cal.b, 0
    else:
        a0, b0, it0 = _platt_legacy(pred_fit, up)
    old = calibrate.Calibration(a=a0, b=b0, source="旧")
    buy_fit_old, buy_te_old = old.buy_pct(pred_fit), old.buy_pct(pred_te)
    buy_fit_std, buy_te_std = cal.buy_pct(pred_fit), cal.buy_pct(pred_te)
    ll0, ll1 = _loglik(pred_fit, up, a0, b0), _loglik(pred_fit, up, cal.a, cal.b)
    return {
        "較正元": cal.source, "較正の行": int(len(pred_fit)),
        "上がる割合": round(float(up.mean()), 4),
        # 容疑 1: 予測そのもの
        "pred_std": float(np.std(pred_fit)),
        "pred_p05": float(np.percentile(pred_fit, 5)),
        "pred_p95": float(np.percentile(pred_fit, 95)),
        "auc_fit": _auc(target_fit, pred_fit), "auc_te": _auc(y_te, pred_te),
        # 容疑 2: 解けているか。⚠ **反復_旧 が 2 なら最適化が始まってすらいない**
        "a_旧": a0, "b_旧": b0, "反復_旧": it0, "a_std": cal.a, "b_std": cal.b,
        "ll_旧": ll0, "ll_std": ll1, "dll": ll1 - ll0,
        # 容疑 3: 解き直しても幅が出るか
        "width_fit_旧": _width(buy_fit_old), "width_fit_std": _width(buy_fit_std),
        "width_te_旧": _width(buy_te_old), "width_te_std": _width(buy_te_std),
        "buy_te_旧_中央": float(np.median(buy_te_old)),
        "buy_te_std_中央": float(np.median(buy_te_std)),
        **{k + "(旧)": v for k, v in _crossings(buy_te_old).items()},
        **{k + "(解直)": v for k, v in _crossings(buy_te_std).items()},
    }


# --------------------------------------------------------------------------- 選別 × モデル

def _selector_rows(panel, feats, exp, edges, v, ctx, k, model, bank=None) -> list[dict]:
    """⚠ **`cli/run.py` の (A) 共通の経路をそのままなぞる**（較正 → 買い%）。

    `bank` を渡すと ⚠ **訓練分割の内側の買い%**（較正を fit した標本の上の値）を貯める。
    ⚠ **θ の置き方の検討はこれだけを材料にする**（プラン §3 規律 1）。
    """
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
            cal = calibrate.fit_from_predictions(np.asarray(pred_fit, float), target, source)
            if bank is not None:
                bank.append({"fold": f, "系統": "選別", "手法": prep.label(exp, name),
                             "buy_tr": cal.buy_pct(pred_fit)})
            rows.append({"fold": f, "系統": "選別", "手法": prep.label(exp, name),
                         "列": len(cols),
                         **_row(np.asarray(pred_fit, float), np.asarray(target, float),
                                np.asarray(pred_te, float), yte, source)})
    return rows


# --------------------------------------------------------------------------- 検知器

def _detector_rows(panel, feats, exp, edges, v, ctx, bank=None) -> list[dict]:
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
            if bank is not None:
                cal = calibrate.fit_from_predictions(np.asarray(pred_fit, float), target, source)
                bank.append({"fold": f, "系統": "D 学習ゲート", "手法": f"{label}{w}日",
                             "buy_tr": cal.buy_pct(pred_fit)})
            rows.append({"fold": f, "系統": "D 学習ゲート", "手法": f"{label}{w}日", "列": len(cols),
                         **_row(np.asarray(pred_fit, float), np.asarray(target, float),
                                np.asarray(pred_te, float), yte, source)})
            # ⚠ **同じ fold・同じスケールの C 系**（学習しない規則）
            buy_c = np.where(te[f"{trend.PREFIX}trend{w}_dist"].to_numpy(dtype=float) > 0,
                             100.0, 0.0)
            if bank is not None:
                # ⚠ **C 系は学習しないので「訓練の内側」が無い。** ⚠ **訓練分割の同じ規則の出力を使う**
                buy_c_tr = np.where(
                    tr[f"{trend.PREFIX}trend{w}_dist"].to_numpy(dtype=float) > 0, 100.0, 0.0)
                bank.append({"fold": f, "系統": "C 古典フィルタ", "手法": f"{label} SMA{w}",
                             "buy_tr": buy_c_tr})
            rows.append({"fold": f, "系統": "C 古典フィルタ", "手法": f"{label} SMA{w}", "列": 1,
                         "較正元": "none（学習しない）", "auc_te": _auc(yte, buy_c),
                         "width_te_旧": _width(buy_c), "width_te_std": _width(buy_c),
                         "buy_te_旧_中央": float(np.median(buy_c)),
                         **{k + "(旧)": v2 for k, v2 in _crossings(buy_c).items()}})
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
    ap.add_argument("--theta", action="store_true",
                    help="⚠ θ の置き方 3 案を訓練分割の内側だけで比べる（プラン §3）")
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
    bank: list[dict] | None = [] if args.theta else None
    rows = (_detector_rows(panel, feats, exp, edges, v, ctx, bank) if exp.get("detectors")
            else _selector_rows(panel, feats, exp, edges, v, ctx, k, model, bank))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(d, "folds.csv"), index=False)
    json.dump({"実験": args.experiment, "leak": args.leak,
               "表": os.path.relpath(path, store.ROOT), "行": int(len(panel)),
               "⚠ 注記": "診断であって検証ではない。runs/ に書かないので n_trials は動かない"},
              open(os.path.join(d, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    if bank:
        th = _theta_rows(bank)
        th.to_csv(os.path.join(d, "theta.csv"), index=False)
        g = (th.groupby(["系統", "手法", "案", "θ"])
               [["帯下", "帯上", "買い%中央", "入口が立つ", "出口が立つ"]].mean().round(3))
        with pd.option_context("display.width", 200, "display.max_rows", 400):
            print(g.to_string())
        print("⚠ **fold 平均。** ⚠ **保有日率は「入口が立つ」以上・「1 − 出口が立つ」以下に入る**")

    cols = ["fold", "手法", "較正元", "pred_std", "auc_fit", "a_旧", "反復_旧", "a_std",
            "ll_旧", "ll_std", "dll", "width_fit_旧", "width_fit_std",
            "width_te_旧", "width_te_std", "buy_te_std_中央"]
    show = [c for c in cols if c in df.columns]
    with pd.option_context("display.width", 220, "display.max_columns", 40):
        print(df[show].to_string(index=False, float_format=lambda x: f"{x:.5g}"))
    print(f"→ {os.path.relpath(d, runs.ROOT)}")


if __name__ == "__main__":
    main()
