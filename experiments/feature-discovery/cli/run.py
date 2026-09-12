"""実験 1 本を回す → `runs/`。⚠ **走り出す前に config の名前を全部 resolve する**。

    python3 -m cli.run --experiment own_only_h1
    python3 -m cli.run --experiment own_only_h1 --leak      # ⚠ 配線の検査（跳ね上がるはず）

⚠ **設計の要点は 3 つ**（rules.md 9 章）。
  1. ⚠ **特徴量の選別は訓練分割の内側だけで行う**（Ambroise-McLachlan 2002）
  2. ⚠ **パージとエンバーゴを入れる**（ラベルが未来 k 本を跨ぐので、訓練の末尾は捨てる）
  3. ⚠ **良い数字より先にコストを引く**（E8 §1-4。往復 5〜10bp）
"""

from __future__ import annotations

import argparse
import json
import os
import warnings

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from ail import config, registry, runs
from ail.contracts import META_COLUMNS
from ail.data import store
from ail.validation import checks, metrics
import ail.bootstrap  # noqa: F401

warnings.filterwarnings("ignore")


def load_panel(experiment: str, period: str, leak: bool,
               source: str | None = None) -> tuple[pd.DataFrame, str]:
    """`source`（config の `features_from`）があればその実験の表を読む。

    ⚠ **橋渡しの追試は「同じ表」で回して初めて差を検証方式だけの差として読める**（rules.md 13-8）。
    表を作り直すと（列の順・行の間引きが同じでも）比較に「表の差」が混ざりうる。
    """
    src = source or experiment
    path = os.path.join(store.DATA, "features",
                        src + ("_leak" if leak else ""), f"{period}.parquet")
    if not os.path.exists(path):
        raise SystemExit(f"{os.path.relpath(path, store.ROOT)} が無い。先に `python3 -m cli.build` を回す")
    return pd.read_parquet(path), path


def evaluate(panel: pd.DataFrame, feats: list[str], exp: dict, run: runs.Run) -> pd.DataFrame:
    v = exp.get("validation", {})
    seed = int(v.get("seed", 0))
    k = int(exp.get("k", 8))
    cost_bp = float(exp.get("cost_bp", 5.0))
    horizon_min = float(exp["horizon_min"])
    ctx = {"seed": seed, **exp.get("model_args", {})}

    split = registry.resolve("split", v.get("split", "walk_forward"))
    selectors = registry.resolve_all("selector", exp["selectors"])
    baselines = registry.resolve_all("model", exp.get("baselines", []))
    model = registry.resolve("model", exp.get("model", "Ridge"))

    out: list[dict] = []
    picked: list[dict] = []
    for f, tr, te in split(panel, int(v.get("folds", 5)), horizon_min,
                           int(v.get("embargo_bars", 0)), float(exp.get("bar_minutes", 0.0))):
        yte = te["y"].values
        # ⚠ モデルを使わない基準線を先に測る。**これを超えない予測は「何も学んでいない」。**
        for bname, bfn in baselines.items():
            p = bfn(tr, te, feats, ctx)
            g = float(np.mean(p * yte))
            out.append({"手法": f"基準 {bname}", "fold": f, "選んだ本数": 0,
                        "的中率": float(np.mean(np.sign(p) == np.sign(yte))), "IC": 0.0,
                        "粗利bp": g * 1e4, "純利bp": g * 1e4 - cost_bp})

        # ⚠ 標準化は**訓練分割の内側で fit**（rules.md 3 章の B。全期間で fit すると漏れる）
        sc = StandardScaler().fit(tr[feats])
        Xtr = pd.DataFrame(sc.transform(tr[feats]), columns=feats)
        Xte = pd.DataFrame(sc.transform(te[feats]), columns=feats)
        ytr = tr["y"].values
        for name, fn in selectors.items():
            cols = fn(Xtr, ytr, k, ctx)                 # ⚠ 選別は訓練の内側だけ
            p = model(Xtr[cols], ytr, Xte[cols], ctx)
            out.append({"手法": name, "fold": f, "選んだ本数": len(cols),
                        **metrics.score(p, yte, cost_bp)})
            # ⚠ **何を選んだかを残す。** ⚠ **偽薬を選んだ割合が、そのまま偽発見率の実測になる**
            picked.extend({"手法": name, "fold": f, "列": c} for c in cols)
        run.log(f"  fold {f}: 訓練 {len(tr):,} / 検証 {len(te):,}")
    run.selected(pd.DataFrame(picked))
    return pd.DataFrame(out)


def evaluate_trading(panel: pd.DataFrame, feats: list[str], exp: dict, run: runs.Run):
    """閾値つき売買（rules.md 13 章）: 較正 → 閾値 → 状態機械 → 銘柄別 bp ＋ ポートフォリオ。

    ⚠ **1 fold 1 fit を 3 閾値で使い回す**（13-3 の 5。閾値は予測の後ろにしか効かない）。
    ⚠ **fold の切れ目は日付で 1 回決める**（13-6 の 1。形式 (A)(B) で同じ fold にするため）。
    """
    from ail.models import calibrate
    from ail.validation import simulate as sim
    from ail.validation import splits

    t = exp["trading"]
    thresholds = [float(x) for x in t.get("thresholds", (50.0, 55.0, 60.0))]
    form = str(t.get("form", "shared"))
    v = exp.get("validation", {})
    k = int(exp.get("k", 8))
    cost_bp = float(exp.get("cost_bp", 5.0))
    horizon_min = float(exp["horizon_min"])
    ctx = {"seed": int(v.get("seed", 0)), **exp.get("model_args", {})}
    model = registry.resolve("model", exp.get("model", "Ridge"))
    selectors = registry.resolve_all("selector", exp["selectors"])
    baselines = registry.resolve_all("model", exp.get("baselines", []))

    edges = splits.date_edges(panel["ts"], int(v.get("folds", 5)))
    out: list[dict] = []
    sym_out: list[dict] = []
    picked: list[dict] = []
    daily: dict[tuple[str, float], list[pd.Series]] = {}

    for f, tr, te in splits.folds_by_dates(panel, edges, horizon_min,
                                           int(v.get("embargo_bars", 0)),
                                           float(exp.get("bar_minutes", 0.0))):
        te = te.reset_index(drop=True)
        y = te["y"].values
        ts_te = pd.to_datetime(te["ts"])
        groups = te.groupby("symbol").indices          # 銘柄 → 行位置（時刻順のまま）

        buy: dict[str, np.ndarray] = {}
        n_cols: dict[str, float] = {}
        fitted_doc: dict[str, dict] = {}
        # ⚠ 基準線は「買い% の定数指標」としてシミュレータを共有する（13-5。別実装を作らない）
        for bname, bfn in baselines.items():
            p = np.asarray(bfn(tr, te, feats, ctx), dtype=float)
            buy[f"基準 {bname}"] = np.where(p > 0, 100.0, 0.0)
            n_cols[f"基準 {bname}"] = 0.0

        if form == "per_symbol":
            # (B) 銘柄別: fit も較正も銘柄ごと（13-6 の 2）。fold の切れ目は上で決めた日付を共有
            sel_sum: dict[str, float] = {n: 0.0 for n in selectors}
            sel_cnt: dict[str, int] = {n: 0 for n in selectors}
            for name in selectors:
                buy[name] = np.full(len(te), np.nan)
            for s, idx in groups.items():
                tr_s = tr[tr["symbol"] == s]
                te_s = te.iloc[idx]
                if len(tr_s) < 30:
                    run.log(f"  ⚠ fold {f} {s}: 訓練 {len(tr_s)} 行しか無いので飛ばす")
                    continue
                sc = StandardScaler().fit(tr_s[feats])
                Xtr = pd.DataFrame(sc.transform(tr_s[feats]), columns=feats)
                Xte = pd.DataFrame(sc.transform(te_s[feats]), columns=feats)
                ytr = tr_s["y"].values
                for name, fn in selectors.items():
                    cols = fn(Xtr, ytr, k, ctx)
                    cal = calibrate.fit(model, Xtr[cols], ytr, ctx)
                    pred = model(Xtr[cols], ytr, Xte[cols], ctx)
                    buy[name][idx] = cal.buy_pct(pred)
                    sel_sum[name] += len(cols)
                    sel_cnt[name] += 1
                    fitted_doc.setdefault(name, {})[str(s)] = cal.doc
            for name in selectors:
                n_cols[name] = sel_sum[name] / sel_cnt[name] if sel_cnt[name] else 0.0
        else:
            # (A) 共通 1 本: 63 銘柄をプールして 1 モデル（現行の形）
            sc = StandardScaler().fit(tr[feats])
            Xtr = pd.DataFrame(sc.transform(tr[feats]), columns=feats)
            Xte = pd.DataFrame(sc.transform(te[feats]), columns=feats)
            ytr = tr["y"].values
            for name, fn in selectors.items():
                cols = fn(Xtr, ytr, k, ctx)
                cal = calibrate.fit(model, Xtr[cols], ytr, ctx)
                pred = model(Xtr[cols], ytr, Xte[cols], ctx)
                buy[name] = cal.buy_pct(pred)
                n_cols[name] = float(len(cols))
                fitted_doc[name] = cal.doc
                picked.extend({"手法": name, "fold": f, "列": c} for c in cols)
        run.fitted(f"calibration_f{f}", fitted_doc)     # ⚠ 再現用。次の実行では読み込まない（13-2 の 3）

        for mname, bp in buy.items():
            ok = ~np.isnan(bp)
            hit = float(np.mean((bp[ok] > 50.0) == (y[ok] > 0))) if ok.any() else 0.0
            ic = (float(np.corrcoef(bp[ok], y[ok])[0, 1])
                  if ok.any() and np.std(bp[ok]) > 0 else 0.0)
            for th in thresholds:
                nets: dict[str, pd.Series] = {}
                grosses: dict[str, pd.Series] = {}
                trades, pos_days, days = 0, 0, 0
                for s, idx in groups.items():
                    if np.isnan(bp[idx]).any():        # 飛ばした銘柄（(B) で訓練が無い）
                        continue
                    r = sim.simulate(bp[idx], y[idx], th, cost_bp)
                    key = str(s)
                    nets[key] = pd.Series(r["net_bp"], index=ts_te.iloc[idx].values)
                    grosses[key] = pd.Series(r["gross_bp"], index=ts_te.iloc[idx].values)
                    trades += r["trades"]
                    pos_days += int(r["pos"].sum())
                    days += len(idx)
                    sym_out.append({"手法": mname, "閾値": th, "fold": f, "銘柄": key,
                                    "純利bp": round(float(r["net_bp"].sum()), 4),
                                    "粗利bp": round(float(r["gross_bp"].sum()), 4),
                                    "取引回数": r["trades"],
                                    "保有日率": round(r["hold_ratio"], 4),
                                    "見送り日数": r["skip_days"]})
                if not nets:
                    continue
                port_net = sim.portfolio_daily(nets)
                port_gross = sim.portfolio_daily(grosses)
                daily.setdefault((mname, th), []).append(port_net)
                out.append({"手法": mname, "fold": f, "閾値": th,
                            "選んだ本数": n_cols.get(mname, 0.0), "的中率": hit, "IC": ic,
                            "粗利bp": float(port_gross.sum()), "純利bp": float(port_net.sum()),
                            "取引回数": trades,
                            "保有日率": pos_days / days if days else 0.0,
                            "検証日数": int(len(port_net))})
        run.log(f"  fold {f}: 訓練 {len(tr):,} / 検証 {len(te):,}（{len(groups)} 銘柄）")

    if picked:
        run.selected(pd.DataFrame(picked))
    res = pd.DataFrame(out)
    summary = (res.groupby(["手法", "閾値"])
                  .agg(本数=("選んだ本数", "mean"), 的中率=("的中率", "mean"), IC=("IC", "mean"),
                       粗利bp=("粗利bp", "mean"), 純利bp=("純利bp", "mean"),
                       取引回数=("取引回数", "mean"), 保有日率=("保有日率", "mean"),
                       fold数=("fold", "size"))
                  .reset_index().set_index("手法")
                  .sort_values("純利bp", ascending=False).round(4))
    return res, pd.DataFrame(sym_out), summary, daily


def apply_gate(exp: dict, gate_doc: dict, ignore: bool, run: runs.Run) -> dict | None:
    """前置きの門を実験に適用する（rules.md 14-5）。全手法が門前なら None（＝ 閾値売買を回さない）。

    ⚠ `--ignore-gate` は「門前の手法を後から回す」用（14-5 の規律 3）。回した手法は summary に
    載るので、台帳では門前の行が立たず**普通の試行として数えられる**。
    """
    blocked = list(gate_doc.get("blocked", []))
    if ignore and blocked:
        gate_doc["forced"] = True
        run.log("⚠ --ignore-gate: 門前の手法も回す（そのときは普通に試行として数える。rules.md 14-5 規律 3）")
        return exp
    if not gate_doc.get("passed"):
        run.log("⚠ **全手法が門前 ＝ 閾値売買を回さない**（台帳には「門前」で残す・"
                "n_trials に数えない。rules.md 14-5）")
        return None
    if blocked:
        run.log("⚠ 門前の手法は回さない: " + "、".join(blocked) + "（rules.md 14-5）")
        return {**exp, "selectors": [s for s in exp["selectors"] if s not in blocked]}
    return exp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--leak", action="store_true", help="⚠ 配線の検査（未来を混ぜた表を使う）")
    ap.add_argument("--sample", type=int, default=60000, help="行が多いとき間引く（0 で間引かない）")
    ap.add_argument("--layer", default="adjusted", help="記録に残すだけ（表は cli.build が作る）")
    ap.add_argument("--ignore-gate", action="store_true",
                    help="⚠ 門前の手法も回す（後から回す用。普通に試行として数える。rules.md 14-5）")
    args = ap.parse_args()

    exp = config.resolve_experiment(args.experiment)     # ⚠ ここで名前を全部解決する
    ds = exp["_dataset"]
    panel, path = load_panel(args.experiment, ds["period"], args.leak, exp.get("features_from"))
    feats = [c for c in panel.columns if c not in META_COLUMNS]
    exp.setdefault("horizon_min", 1440.0 if ds["period"] == "d" else float(exp["horizon"]))

    name = args.experiment + ("_leak" if args.leak else "")
    run = runs.Run(name, {k: v for k, v in exp.items() if not k.startswith("_")},
                   int(exp.get("validation", {}).get("seed", 0)))
    # ⚠ **層は表の隣の sidecar を正とする。** `--layer` は自己申告なので、
    # ⚠ **食い違ったらここで言う**（黙って通すと台帳の「データの層」が嘘になる）
    meta = _features_meta(path)
    if meta and meta.get("layer") and meta["layer"] != args.layer:
        run.log(f"⚠ **--layer {args.layer} だが、表は層 {meta['layer']} から作られている**"
                f"（{os.path.basename(path)}）。⚠ **sidecar のほうを記録に残す。**")
    # ⚠ **期間は「読んだ表」から取る**（sidecar の自己申告ではなく実物）。台帳の鍵の「期間」が
    # ⚠ **これを正として読む**（rules.md 14-4。⚠ **無い実行は「—」で、後から埋めない**）
    ts = pd.to_datetime(panel["ts"])
    run.inputs({"features_file": os.path.relpath(path, store.ROOT),
                "layer": (meta or {}).get("layer") or args.layer,
                "layer_declared": args.layer, "features_meta": meta,
                "panel_start": str(ts.min().date()), "panel_end": str(ts.max().date()),
                "rows_before_sample": int(len(panel)),
                "features": len(feats), "symbols": int(panel["symbol"].nunique()),
                "sample": args.sample,
                "data_manifest": {p: _digest(p, ds["period"]) for p in ("raw", "adjusted")}})

    trading = (exp.get("trading") or {}).get("style") == "threshold"
    full_panel = panel          # ⚠ **相関は間引く前で測る**（checks.py の注記）
    if args.sample and len(panel) > args.sample:
        if trading:
            # ⚠ 状態機械は日次の連続した系列が前提。間引くと保有日が飛び、コストの数え方が壊れる
            run.log("⚠ 閾値つき売買は間引かない（--sample は効かない。rules.md 13-4）")
        else:
            panel = panel.iloc[:: max(1, len(panel) // args.sample)]
    leaky = [c for c in feats if c.startswith("LEAK")]
    layer = (meta or {}).get("layer") or args.layer
    run.log(f"実験 {args.experiment} / 層 {layer} / {ds['period']} 足")
    run.log(f"行 {len(panel):,} / 特徴量 {len(feats)}"
            + (f"  ⚠ **わざとした先読みの列あり: {leaky}**" if leaky else ""))

    if trading:
        # ⚠ **門が先**（rules.md 14-5 規律 1）。通らない手法は閾値売買を回さず、門の値だけ checks に残す
        from ail.validation import gate
        gate_doc = gate.evaluate_gate(panel, feats, exp, run)
        gated_exp = apply_gate(exp, gate_doc, args.ignore_gate, run)
        if gated_exp is None:
            t = exp["trading"]
            run.checks({"leak": args.leak, "style": "threshold",
                        "form": str(t.get("form", "shared")),
                        "cost_bp": float(exp.get("cost_bp", 5.0)),
                        "thresholds": [float(x) for x in t.get("thresholds", (50.0, 55.0, 60.0))],
                        "gate": gate_doc})
            print(f"→ {os.path.relpath(run.close(), store.ROOT)}")
            return
        exp = gated_exp
        res, per_sym, g, daily = evaluate_trading(panel, feats, exp, run)
        run.log("")
        run.log(g.to_string())
        run.log(f"\n⚠ 純利 = 売買した日だけ片道 {float(exp.get('cost_bp', 5.0)) / 2:g}bp を引いた後"
                "（rules.md 13-4）。⚠ **採否は対 B&H の上乗せで測る**（13-7。純利の符号では"
                "「買って持っただけ」と区別できない）。")
        run.result(res, g)
        run.per_symbol(per_sym)
        # ⚠ **`summary.csv` を書いたあとに数える。** 台帳はそれを読むので、この実行の行
        # （検証方式が処置 ＝ 選別 × 閾値の数。門前の手法は selectors から外れている）は
        # ⚠ **もう台帳に入っている。この実行ぶんを足さない**（13-9・14-5。足すと二重になる）
        doc = checks.compute_trading(res, g, per_sym, daily, exp,
                                     n_trials=checks.n_trials_now(),
                                     leak=args.leak, panel=full_panel)
        doc["gate"] = gate_doc                       # ⚠ 記録するだけ。採否には使わない（14-5）
        run.checks(doc)
        run.log(_checks_line_trading(doc))
        print(f"→ {os.path.relpath(run.close(), store.ROOT)}")
        return

    res = evaluate(panel, feats, exp, run)
    g = (res.groupby("手法")
            .agg(本数=("選んだ本数", "mean"), 的中率=("的中率", "mean"), IC=("IC", "mean"),
                 粗利bp=("粗利bp", "mean"), 純利bp=("純利bp", "mean"), fold数=("fold", "size"))
            .sort_values("純利bp", ascending=False).round(4))
    run.log("")
    run.log(g.to_string())
    run.log(f"\n⚠ 純利 = 粗利 − コスト {exp.get('cost_bp', 5.0)}bp。⚠ **正でなければその手法は使えない。**")
    run.result(res, g)

    # ⚠ **検査はここで 1 度だけ計算して記録に残す**（管理画面は読むだけ。プラン §2）
    # ⚠ **`summary.csv`（上の `run.result`）を書いたあとに数える。** 台帳はそれを読むので、
    # ⚠ **この実行の行はもう台帳に入っている。この実行ぶんを足さない**（足すと二重になる）
    doc = checks.compute(res, g, exp, panel=panel, full_panel=full_panel,
                         n_trials=checks.n_trials_now(), leak=args.leak)
    run.checks(doc)
    run.log(_checks_line(doc))
    print(f"→ {os.path.relpath(run.close(), store.ROOT)}")


def _checks_line_trading(doc: dict) -> str:
    """⚠ **3 閾値とも 1 行に出す**（良かった閾値だけ報告しない。rules.md 13-3 の 3）。"""
    parts = []
    for th, e in (doc.get("by_threshold") or {}).items():
        b, ed = e.get("best") or {}, e.get("edge_vs_bh") or {}
        seg = f"θ={th}: 純利 {b.get('純利bp', 0.0):+.2f}bp"
        if ed:
            seg += f" 上乗せ {ed.get('mean_bp', 0.0):+.2f}bp"
            if ed.get("t") is not None:
                seg += f" t={ed['t']:.2f}"
        seg += f" 取引 {b.get('取引回数', 0.0):.0f} 回/fold"
        if d := e.get("dsr"):
            seg += f" DSR {d['DSR']:.3f}"
        parts.append(seg)
    return ("検査: " + " ／ ".join(parts)) if parts else ""


def _checks_line(doc: dict) -> str:
    b, f = doc.get("best"), doc.get("folds")
    if not b:
        return ""
    parts = [f"最良 {b['method']} 純利 {b['純利bp']:+.2f}bp"]
    if f:
        parts.append(f"fold {f['positive']}/{f['folds']} {f['pattern']}")
    if (e := doc.get("edge_vs_drift")) and e.get("t") is not None:
        parts.append(f"上乗せ {e['mean_bp']:+.2f}bp t={e['t']:.2f}")
    if d := doc.get("dsr"):
        parts.append(f"DSR {d['DSR']:.4f}（実効 n {d['n_obs']:,} / {d['n_trials']} 試行）")
    return "検査: " + " ／ ".join(parts)


def _features_meta(path: str) -> dict | None:
    """`cli.build` が表の隣に書いた層の記録。⚠ **無ければ古い表（自己申告のまま）。**"""
    p = os.path.splitext(path)[0] + ".meta.json"
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _digest(layer: str, period: str = "d") -> str | None:
    try:
        return store.manifest_digest(layer, period)
    except Exception:
        return None


if __name__ == "__main__":
    main()
