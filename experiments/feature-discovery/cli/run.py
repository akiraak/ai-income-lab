"""実験 1 本を回す → `runs/`。⚠ **走り出す前に config の名前を全部 resolve する**。

    python3 -m cli.run --experiment own_only_h1
    python3 -m cli.run --experiment own_only_h1 --leak      # ⚠ 配線の検査（跳ね上がるはず）
    python3 -m cli.run --experiment impact_ex_2018 --shift-days 365   # ⚠ 偽薬（台帳の試行に数えない）

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
from ail.validation import checks, metrics, prep
import ail.bootstrap  # noqa: F401

warnings.filterwarnings("ignore")


def load_panel(experiment: str, period: str, leak: bool,
               source: str | None = None, shift_days: int = 0) -> tuple[pd.DataFrame, str]:
    """`source`（config の `features_from`）があればその実験の表を読む。

    ⚠ **橋渡しの追試は「同じ表」で回して初めて差を検証方式だけの差として読める**（rules.md 13-8）。
    表を作り直すと（列の順・行の間引きが同じでも）比較に「表の差」が混ざりうる。
    ⚠ **`shift_days` は日付をずらした偽薬の表**（`_shift<S>`。`cli.build --shift-days` が作る）。
    """
    src = source or experiment
    path = os.path.join(store.DATA, "features",
                        runs.variant(src, leak, shift_days), f"{period}.parquet")
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
        # ⚠ **標本から学ぶ変換の 1 段**（3 章 B）。⚠ **config に `transform` が無ければ素通り**
        Xtr, Xte, tdoc = prep.apply(exp, Xtr, Xte, ctx, ytr)
        if tdoc is not None:
            run.fitted(f"transform_f{f}", tdoc)         # ⚠ 再現用。次の実行では読み込まない
        for name, fn in selectors.items():
            cols = fn(Xtr, ytr, k, ctx)                 # ⚠ 選別は訓練の内側だけ
            p = model(Xtr[cols], ytr, Xte[cols], ctx)
            lab = prep.label(exp, name)                 # ⚠ 変換名を手法名に混ぜる（台帳の ID）
            out.append({"手法": lab, "fold": f, "選んだ本数": len(cols),
                        **metrics.score(p, yte, cost_bp)})
            # ⚠ **何を選んだかを残す。** ⚠ **偽薬を選んだ割合が、そのまま偽発見率の実測になる**
            picked.extend({"手法": lab, "fold": f, "列": c} for c in cols)
        run.log(f"  fold {f}: 訓練 {len(tr):,} / 検証 {len(te):,}")
    run.selected(pd.DataFrame(picked))
    return pd.DataFrame(out)


def fold_buy_pct(tr: pd.DataFrame, te: pd.DataFrame, feats: list[str], exp: dict, ctx: dict, *,
                 model, selectors: dict, detectors: dict, baselines: dict, form: str, k: int,
                 groups: dict, f, picked: list[dict], log=print):
    """訓練 1 塊 → 検証 1 塊の **買い% ／ 出口%**（手法ごと）。⚠ **`evaluate_trading` の fold の中身をそのまま切り出したもの**。

    ⚠ **バックテストの fold と、実売買の「今日の買い%」（`cli/predict.py`）が同じ 1 本を通る**ための口。
    ⚠ **ここでモデル・較正・変換の式を変えない**（変えると既定経路の指紋が動く。`tests/test_trading_run.py`）。
    戻り値は (buy, exits, n_cols, fitted_doc)。`picked` には選んだ列を足す（呼び出し側の一覧）。
    """
    from ail.models import calibrate

    buy: dict[str, np.ndarray] = {}
    # ⚠ **出口%**（rules.md 16-1）。⚠ **None の手法は 100 − 入口% で回る ＝ 既存と完全一致**
    exits: dict[str, np.ndarray | None] = {}
    n_cols: dict[str, float] = {}
    fitted_doc: dict[str, dict] = {}
    # ⚠ 基準線は「買い% の定数指標」としてシミュレータを共有する（13-5。別実装を作らない）
    for bname, bfn in baselines.items():
        p = np.asarray(bfn(tr, te, feats, ctx), dtype=float)
        buy[f"基準 {bname}"] = np.where(p > 0, 100.0, 0.0)
        n_cols[f"基準 {bname}"] = 0.0

    if detectors:
        # ⚠ **検知器は買い% を直接返す**（rules.md 14-1 の出力の契約）。選別もモデルも中に隠れる。
        # ⚠ **シミュレータから先は選別 × モデルの経路とまったく同じものを使う**（物差しを揃える）
        for name, fn in detectors.items():
            res = fn(tr, te, feats, ctx)
            # ⚠ **3 つ返すのは出口% を別に持つ検知器**（rules.md 16-1。`ail/detectors/pair.py`）
            bp, ep, doc = res if len(res) == 3 else (res[0], None, res[1])
            buy[name] = np.asarray(bp, dtype=float)
            exits[name] = None if ep is None else np.asarray(ep, dtype=float)
            n_cols[name] = float(len(doc.get("columns", [])))
            fitted_doc[name] = doc
    elif form == "per_symbol":
        # (B) 銘柄別: fit も較正も銘柄ごと（13-6 の 2）。fold の切れ目は上で決めた日付を共有
        labels = {n: prep.label(exp, n) for n in selectors}   # ⚠ 変換名を混ぜた手法名
        sel_sum: dict[str, float] = {l: 0.0 for l in labels.values()}
        sel_cnt: dict[str, int] = {l: 0 for l in labels.values()}
        for lab in labels.values():
            buy[lab] = np.full(len(te), np.nan)
        for s, idx in groups.items():
            tr_s = tr[tr["symbol"] == s]
            te_s = te.iloc[idx]
            if len(tr_s) < 30:
                log(f"  ⚠ fold {f} {s}: 訓練 {len(tr_s)} 行しか無いので飛ばす")
                continue
            sc = StandardScaler().fit(tr_s[feats])
            Xtr = pd.DataFrame(sc.transform(tr_s[feats]), columns=feats)
            Xte = pd.DataFrame(sc.transform(te_s[feats]), columns=feats)
            ytr = tr_s["y"].values
            # ⚠ **(B) は銘柄ごとに fit する**ので、変換も銘柄ごとに fit し直す（3 章 B）
            Xtr, Xte, tdoc = prep.apply(exp, Xtr, Xte, ctx, ytr)
            if tdoc is not None:
                fitted_doc.setdefault("_transform", {})[str(s)] = tdoc
            for name, fn in selectors.items():
                lab = labels[name]
                cols = fn(Xtr, ytr, k, ctx)
                cal = calibrate.fit(model, Xtr[cols], ytr, ctx)
                pred = model(Xtr[cols], ytr, Xte[cols], ctx)
                buy[lab][idx] = cal.buy_pct(pred)
                sel_sum[lab] += len(cols)
                sel_cnt[lab] += 1
                fitted_doc.setdefault(lab, {})[str(s)] = cal.doc
        for lab in labels.values():
            n_cols[lab] = sel_sum[lab] / sel_cnt[lab] if sel_cnt[lab] else 0.0
    else:
        # (A) 共通 1 本: 63 銘柄をプールして 1 モデル（現行の形）
        sc = StandardScaler().fit(tr[feats])
        Xtr = pd.DataFrame(sc.transform(tr[feats]), columns=feats)
        Xte = pd.DataFrame(sc.transform(te[feats]), columns=feats)
        ytr = tr["y"].values
        # ⚠ **標本から学ぶ変換の 1 段**（3 章 B）。⚠ **config に `transform` が無ければ素通り**
        Xtr, Xte, tdoc = prep.apply(exp, Xtr, Xte, ctx, ytr)
        if tdoc is not None:
            fitted_doc["_transform"] = tdoc
        for name, fn in selectors.items():
            lab = prep.label(exp, name)             # ⚠ 変換名を手法名に混ぜる（台帳の ID）
            cols = fn(Xtr, ytr, k, ctx)
            cal = calibrate.fit(model, Xtr[cols], ytr, ctx)
            pred = model(Xtr[cols], ytr, Xte[cols], ctx)
            buy[lab] = cal.buy_pct(pred)
            n_cols[lab] = float(len(cols))
            fitted_doc[lab] = cal.doc
            picked.extend({"手法": lab, "fold": f, "列": c} for c in cols)
    return buy, exits, n_cols, fitted_doc


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
    seed = int(v.get("seed", 0))
    model = registry.resolve("model", exp.get("model", "Ridge"))
    # ⚠ **検知器はモデルを自分で呼ぶ**（買い% まで自前で作る。rules.md 14-1）ので ctx に入れて渡す
    ctx = {"seed": seed, "model": model, "k": k, **exp.get("model_args", {})}
    selectors = registry.resolve_all("selector", exp.get("selectors", []))
    detectors = registry.resolve_all("detector", exp.get("detectors", []))
    baselines = registry.resolve_all("model", exp.get("baselines", []))

    edges = splits.date_edges(panel["ts"], int(v.get("folds", 5)))
    out: list[dict] = []
    sym_out: list[dict] = []
    picked: list[dict] = []
    daily: dict[tuple[str, float], list[pd.Series]] = {}
    # ⚠ **14-6 (b) の乱択ゲートと、エピソード表（14-8）が要る保有日率**。どちらも診断で、採否に使わない
    extra: dict = {"hold": {}, "rand": {}}
    # ⚠ **1 取引 1 行の保有日数**（13-4 の 6）。⚠ **成果物であって採否には使わない**。`extra["holds"]` で持ち出す
    holds_out: list[dict] = []
    topk_out: list[dict] = []
    if t.get("top_k") and form == "per_symbol":
        raise SystemExit("⚠ 上位 K（rules.md 17 章）は形式 (A) と検知器だけ（(B) は銘柄ごとに較正が違い、買い% を銘柄間で比べられない）")
    if t.get("top_k") and "close" in panel:
        # ⚠ **特徴量には入らない**（`feats` は呼び出し側が先に決めている）。fold を切る前に作るのは、窓を過去へ伸ばすため
        panel = panel.assign(**{TOPK_VOL_COLUMN: topk_vol(panel)})

    for f, tr, te in splits.folds_by_dates(panel, edges, horizon_min,
                                           int(v.get("embargo_bars", 0)),
                                           float(exp.get("bar_minutes", 0.0))):
        te = te.reset_index(drop=True)
        y = te["y"].values
        ts_te = pd.to_datetime(te["ts"])
        groups = te.groupby("symbol").indices          # 銘柄 → 行位置（時刻順のまま）

        buy, exits, n_cols, fitted_doc = fold_buy_pct(
            tr, te, feats, exp, ctx, model=model, selectors=selectors, detectors=detectors,
            baselines=baselines, form=form, k=k, groups=groups, f=f, picked=picked, log=run.log)
        run.fitted(f"calibration_f{f}", fitted_doc)     # ⚠ 再現用。次の実行では読み込まない（13-2 の 3）

        for mname, bp in buy.items():
            ex = exits.get(mname)                  # ⚠ None なら 100 − 入口%（rules.md 16-1 の 4）
            ok = ~np.isnan(bp)
            hit = float(np.mean((bp[ok] > 50.0) == (y[ok] > 0))) if ok.any() else 0.0
            ic = (float(np.corrcoef(bp[ok], y[ok])[0, 1])
                  if ok.any() and np.std(bp[ok]) > 0 else 0.0)
            for th in thresholds:
                nets: dict[str, pd.Series] = {}
                grosses: dict[str, pd.Series] = {}
                rands: dict[str, pd.Series] = {}
                poss: dict[str, pd.Series] = {}
                revs: dict[str, pd.Series] = {}
                trades, pos_days, days, rand_trades, rev_trades = 0, 0, 0, 0, 0
                # ⚠ 乱択ゲートの種は config の種。⚠ **引く順は `groups` の並びで決まる**（再現する）
                rng = np.random.default_rng(seed)
                for s, idx in groups.items():
                    if np.isnan(bp[idx]).any():        # 飛ばした銘柄（(B) で訓練が無い）
                        continue
                    if ex is not None and np.isnan(ex[idx]).any():
                        continue                       # ⚠ 出口% が欠けた銘柄も同じく飛ばす
                    r = sim.simulate(bp[idx], y[idx], th, cost_bp,
                                     exit_pct=None if ex is None else ex[idx])
                    rg = sim.shifted_gate(r["pos"], y[idx], cost_bp, rng)   # 14-6 の b
                    # ⚠ **逆売買**（rules.md 14-3 の (3)。診断列・採否に使わない・試行に数えない）:
                    # ⚠ **入口% と出口% を入れ替えて同じ状態機械を回すだけ**（`simulate` 本体は触らない）。
                    # θ ≥ 50 では元の売買の補集合になる（reverse-trading.md §1）
                    rv = sim.simulate(ex[idx] if ex is not None else 100.0 - bp[idx], y[idx], th, cost_bp,
                                      exit_pct=bp[idx])
                    key, stamp = str(s), ts_te.iloc[idx].values
                    nets[key] = pd.Series(r["net_bp"], index=stamp)
                    grosses[key] = pd.Series(r["gross_bp"], index=stamp)
                    rands[key] = pd.Series(rg["net_bp"], index=stamp)
                    poss[key] = pd.Series(r["pos"].astype(float), index=stamp)
                    revs[key] = pd.Series(rv["net_bp"], index=stamp)
                    trades += r["trades"]
                    rand_trades += rg["trades"]
                    rev_trades += rv["trades"]
                    pos_days += int(r["pos"].sum())
                    days += len(idx)
                    hd = r["hold_days"]
                    sym_out.append({"手法": mname, "閾値": th, "fold": f, "銘柄": key,
                                    "純利bp": round(float(r["net_bp"].sum()), 4),
                                    "粗利bp": round(float(r["gross_bp"].sum()), 4),
                                    "取引回数": r["trades"],
                                    "保有日率": round(r["hold_ratio"], 4),
                                    "見送り日数": r["skip_days"],
                                    # ⚠ 以下は 2026-09-17 に末尾へ足した列（既存列の値は変えない）
                                    "保有日数中央値": float(np.median(hd)) if hd else np.nan,
                                    "保有日数最短": int(min(hd)) if hd else np.nan,
                                    "保有日数最長": int(max(hd)) if hd else np.nan,
                                    "逆売買純利bp": round(float(rv["net_bp"].sum()), 4)})
                    # ⚠ **1 取引 1 行**（holds.csv）。強制清算は最後の 1 取引だけ（13-4 の 4）
                    for i, (k_days, e_idx) in enumerate(zip(hd, r["entry_idx"])):
                        holds_out.append({"手法": mname, "閾値": th, "fold": f, "銘柄": key,
                                          "建てた日": str(pd.Timestamp(stamp[e_idx]).date()),
                                          "保有日数": int(k_days),
                                          "強制清算": bool(r["forced_close"] and i == len(hd) - 1)})
                if not nets:
                    continue
                port_net = sim.portfolio_daily(nets)
                port_gross = sim.portfolio_daily(grosses)
                port_rand = sim.portfolio_daily(rands)
                port_rev = sim.portfolio_daily(revs)
                daily.setdefault((mname, th), []).append(port_net)
                extra["hold"].setdefault((mname, th), []).append(sim.portfolio_daily(poss))
                extra["rand"].setdefault((mname, th), []).append(port_rand)
                extra.setdefault("rev", {}).setdefault((mname, th), []).append(port_rev)
                out.append({"手法": mname, "fold": f, "閾値": th,
                            "選んだ本数": n_cols.get(mname, 0.0), "的中率": hit, "IC": ic,
                            "粗利bp": float(port_gross.sum()), "純利bp": float(port_net.sum()),
                            "取引回数": trades,
                            "保有日率": pos_days / days if days else 0.0,
                            "検証日数": int(len(port_net)),
                            # ⚠ **基準線の診断**（14-6 b）。⚠ **採否には使わない**
                            "乱択ゲート純利bp": float(port_rand.sum()),
                            "乱択ゲート取引回数": rand_trades,
                            # ⚠ **逆売買の診断列**（14-3 の (3)）。⚠ **採否に使わない・試行に数えない**
                            "逆売買純利bp": float(port_rev.sum()),
                            "逆売買取引回数": rev_trades})
        if t.get("top_k"):
            # ⚠ **上位 K**（rules.md 17 章）。⚠ **既存の行を作り終えた後に足すだけ**（`top_k` の無い config は
            # ここを通らないので、既存の経路は 1 行も変わらない）
            _topk_fold(t, te, y, ts_te, buy, exits, n_cols, thresholds, cost_bp, seed, f,
                       out, daily, extra, topk_out)
        run.log(f"  fold {f}: 訓練 {len(tr):,} / 検証 {len(te):,}（{len(groups)} 銘柄）")

    if picked:
        run.selected(pd.DataFrame(picked))
    extra["holds"] = pd.DataFrame(holds_out)        # ⚠ 成果物。`run.holds` と `checks` が読む
    if topk_out:
        extra["topk"] = pd.DataFrame(topk_out)      # ⚠ 診断（17-5 の 4）。採否には使わない
    res = pd.DataFrame(out)
    summary = (res.groupby(["手法", "閾値"])
                  .agg(本数=("選んだ本数", "mean"), 的中率=("的中率", "mean"), IC=("IC", "mean"),
                       粗利bp=("粗利bp", "mean"), 純利bp=("純利bp", "mean"),
                       取引回数=("取引回数", "mean"), 保有日率=("保有日率", "mean"),
                       乱択ゲートbp=("乱択ゲート純利bp", "mean"),
                       fold数=("fold", "size"))
                  .reset_index().set_index("手法")
                  .sort_values("純利bp", ascending=False).round(4))
    return res, pd.DataFrame(sym_out), summary, daily, extra


TOPK_RANDOM_SEEDS = 5        # ⚠ 基準線「乱択上位 K」は種 5 つの平均（rules.md 17-4。事前固定）
TOPK_VOL_WINDOW = 60         # ⚠ 基準線「ボラ上位 K」の窓（営業日）。1 水準だけ・結果を見て動かさない（17-7 の 3）
TOPK_VOL_MIN = 20            # 窓に最低これだけ無い日は最下位（17-7 の 2）
TOPK_VOL_COLUMN = "_topk_vol"


def topk_vol(panel: pd.DataFrame) -> pd.Series:
    """銘柄ごとの直近 60 営業日の日次対数リターンの標準偏差（rules.md 17-7）。⚠ **足 t までの `close` しか見ない。**

    ⚠ y（t → t+1）は使わない。足 t の終値は執行の時点で分かっている値である。
    ⚠ 窓は fold を跨いで過去へ伸ばす（標本から学ばない ＝ 3 章 A。fit なし）。
    """
    p = panel.sort_values(["symbol", "ts"])
    r = np.log(p["close"]).groupby(p["symbol"]).diff()
    vol = r.groupby(p["symbol"]).rolling(TOPK_VOL_WINDOW, min_periods=TOPK_VOL_MIN).std()
    return vol.reset_index(level=0, drop=True).reindex(panel.index)


def topk_name(method: str, k: int, kind: str) -> str:
    """⚠ **構成は手法名に入れる**（rules.md 17-5 の 1・16-6 規約 1）。鍵に列は足さない。"""
    return f"{method}〔上位{k}・{kind}〕"


def topk_random_name(method: str, k: int, kind: str) -> str:
    """⚠ **`基準 ` で始める** ＝ 台帳が基準線として読み、試行に数えない（13-9 の 4）。"""
    return f"基準 乱択上位〔{method}・上位{k}・{kind}〕"


def topk_vol_name(method: str, k: int, kind: str) -> str:
    """基準線「ボラ上位 K」（rules.md 17-7）。⚠ **`基準 ` で始める** ＝ 試行に数えない。"""
    return f"基準 ボラ上位〔{method}・上位{k}・{kind}〕"


def _topk_fold(t: dict, te: pd.DataFrame, y, ts_te, buy: dict, exits: dict, n_cols: dict,
               thresholds: list[float], cost_bp: float, seed: int, f: int,
               out: list[dict], daily: dict, extra: dict, topk_out: list[dict]) -> None:
    """1 fold ぶんの上位 K の行（手法 × θ × K × 行の種類）と、その基準線「乱択上位 K」を足す（rules.md 17 章）。"""
    from ail.validation import simulate as sim

    ks = [int(x) for x in t["top_k"]]
    kinds: list[tuple[str, float | None]] = [("端数", None)]
    budgets = t.get("top_k_budgets_usd") or {}
    if budgets and "close" not in te:
        raise SystemExit("⚠ 整数株の版（rules.md 17-3）は表に close 列が要る")
    kinds += [(f"整数株{tag}", float(usd)) for tag, usd in budgets.items()]
    dates, syms = ts_te.values, te["symbol"].values
    for mname, bp in buy.items():
        if mname.startswith("基準 ") or mname == "乱択（基準）":
            continue                                   # ⚠ 基準線は上位 K に通さない（定数の買い% は全部同点）
        ex = exits.get(mname)
        ok = ~np.isnan(bp)
        hit = float(np.mean((bp[ok] > 50.0) == (y[ok] > 0))) if ok.any() else 0.0
        ic = (float(np.corrcoef(bp[ok], y[ok])[0, 1]) if ok.any() and np.std(bp[ok]) > 0 else 0.0)
        for th in thresholds:
            for k in ks:
                for kind, usd in kinds:
                    kw = {} if usd is None else {"price": te["close"].values, "budget_usd": usd}
                    r = sim.simulate_topk(bp, y, dates, syms, th, k, cost_bp, exit_pct=ex, **kw)
                    rs = [sim.simulate_topk(bp, y, dates, syms, th, k, cost_bp, exit_pct=ex,
                                            rng=np.random.default_rng(seed + q), **kw)
                          for q in range(TOPK_RANDOM_SEEDS)]
                    rand = {"port_net_bp": sum(x["port_net_bp"] for x in rs) / len(rs),
                            "port_gross_bp": sum(x["port_gross_bp"] for x in rs) / len(rs),
                            "invested": sum(x["invested"] for x in rs) / len(rs),
                            **{c: float(np.mean([x[c] for x in rs]))
                               for c in ("trades", "signals", "skipped_full", "skipped_price", "symbols_bought")}}
                    rows = [(topk_name(mname, k, kind), r, n_cols.get(mname, 0.0)),
                            (topk_random_name(mname, k, kind), rand, 0.0)]
                    if TOPK_VOL_COLUMN in te:
                        # ⚠ **基準線「ボラ上位 K」**（17-7）: 候補は同じ。並べる値だけを直近 60 日の値動きの大きさに替える
                        rows.append((topk_vol_name(mname, k, kind),
                                     sim.simulate_topk(bp, y, dates, syms, th, k, cost_bp, exit_pct=ex,
                                                       rank=te[TOPK_VOL_COLUMN].values, **kw), 0.0))
                    for name, res, cols in rows:
                        daily.setdefault((name, th), []).append(res["port_net_bp"])
                        extra["hold"].setdefault((name, th), []).append(res["invested"])
                        out.append({"手法": name, "fold": f, "閾値": th, "選んだ本数": cols,
                                    "的中率": hit, "IC": ic,
                                    "粗利bp": float(res["port_gross_bp"].sum()),
                                    "純利bp": float(res["port_net_bp"].sum()),
                                    "取引回数": res["trades"],
                                    "保有日率": float(res["invested"].mean()),     # ⚠ 上位 K では「平均の投下率」
                                    "検証日数": int(len(res["port_net_bp"])),
                                    "乱択ゲート純利bp": np.nan, "乱択ゲート取引回数": np.nan,
                                    "逆売買純利bp": np.nan, "逆売買取引回数": np.nan})
                        topk_out.append({"手法": name, "閾値": th, "fold": f, "K": k, "種類": kind,
                                         "買いの合図": res["signals"], "買えた数": res["trades"],
                                         "枠が無くて見送り": res["skipped_full"],
                                         "株価で見送り": res["skipped_price"],
                                         "平均の投下率": round(float(res["invested"].mean()), 4),
                                         "買った銘柄の種類数": res["symbols_bought"]})


def apply_gate(exp: dict, gate_doc: dict, enforce: bool, run: runs.Run) -> dict | None:
    """前置きの門を実験に適用する。

    ⚠ **既定（`enforce=False`）は診断**（rules.md 14-10 規約 2）: 門の値は記録するだけで、
    ⚠ **門前の手法も全部回す**。そのとき `forced` を付けるので、台帳では門前の行が立たず
    **普通の試行として数えられる**（`--ignore-gate` はこの既定の別名）。

    `enforce=True`（`--gate`）だけが従来の足切り（14-5 の経緯）: 全手法が門前なら None
    （＝ 閾値売買を回さない）、一部だけなら門前の手法を外す。⚠ **14-10 規約 2 に反する使い方。**
    """
    blocked = list(gate_doc.get("blocked", []))
    gate_doc["mode"] = "足切り" if enforce else "診断"
    if not enforce:
        if blocked:
            gate_doc["forced"] = True
            run.log("⚠ 門前の手法も回す: " + "、".join(blocked)
                    + "（門は診断。回したものは全部数える。rules.md 14-10 規約 2）")
        return exp
    run.log("⚠ --gate: 門で足切りする（14-10 規約 2 に反する使い方。rules.md 14-5 の経緯）")
    if not gate_doc.get("passed"):
        run.log("⚠ **全手法が門前 ＝ 閾値売買を回さない**（台帳には「門前」で残す・"
                "n_trials に数えない。rules.md 14-5）")
        return None
    if blocked:
        run.log("⚠ 門前の手法は回さない: " + "、".join(blocked) + "（rules.md 14-5）")
        key = "detectors" if exp.get("detectors") else "selectors"
        return {**exp, key: [s for s in exp.get(key, []) if s not in blocked]}
    return exp


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--leak", action="store_true", help="⚠ 配線の検査（未来を混ぜた表を使う）")
    ap.add_argument("--shift-days", type=int, default=0,
                    help="⚠ 偽薬: ex_ の日付を過去へ N 日ずらした表を使う（先に cli.build に同じ値）")
    ap.add_argument("--sample", type=int, default=60000, help="行が多いとき間引く（0 で間引かない）")
    ap.add_argument("--layer", default="adjusted", help="記録に残すだけ（表は cli.build が作る）")
    g = ap.add_mutually_exclusive_group()
    # ⚠ **既定は門で止めない**（rules.md 14-10 規約 2）。付け忘れで黙って 1 行も回らない罠を塞いだ（2026-09-14）
    g.add_argument("--gate", action="store_true",
                   help="⚠ 門で足切りする（全部門前なら回さない・一部なら外す）。14-10 規約 2 に反する使い方")
    g.add_argument("--ignore-gate", action="store_true",
                   help="既定と同じ（門は診断で全部回す）。過去の記録のコマンドのために残す別名")
    return ap


def main() -> None:
    args = _parser().parse_args()

    exp = config.resolve_experiment(args.experiment)     # ⚠ ここで名前を全部解決する
    ds = exp["_dataset"]
    panel, path = load_panel(args.experiment, ds["period"], args.leak, exp.get("features_from"),
                             args.shift_days)
    feats = [c for c in panel.columns if c not in META_COLUMNS]
    exp.setdefault("horizon_min", 1440.0 if ds["period"] == "d" else float(exp["horizon"]))

    name = runs.variant(args.experiment, args.leak, args.shift_days)
    meta = _features_meta(path)
    if args.shift_days:
        # ⚠ **表が本当にずらして作られたかを sidecar で確かめる。** ⚠ **食い違ったまま回すと、
        # 本物の表が偽薬の名前で記録に残る**（逆も同じ）
        if int((meta or {}).get("shift_days") or 0) != args.shift_days:
            raise SystemExit(f"⚠ {os.path.basename(os.path.dirname(path))} は --shift-days "
                             f"{args.shift_days} で作られていない（sidecar: {(meta or {}).get('shift_days')}）")
        exp.setdefault("features", {})["ex_shift_days"] = int(args.shift_days)
    run = runs.Run(name, {k: v for k, v in exp.items() if not k.startswith("_")},
                   int(exp.get("validation", {}).get("seed", 0)))
    # ⚠ **層は表の隣の sidecar を正とする。** `--layer` は自己申告なので、
    # ⚠ **食い違ったらここで言う**（黙って通すと台帳の「データの層」が嘘になる）
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
            + (f"  ⚠ **わざとした先読みの列あり: {leaky}**" if leaky else "")
            + (f"  ⚠ **偽薬: ex_ の日付を過去へ {args.shift_days} 日ずらした表（台帳の試行に数えない）**"
               if args.shift_days else ""))

    if trading:
        # ⚠ **門は先に測って checks に残す**（値を見てから回すかを決めない）。⚠ **既定では止めない**
        # （rules.md 14-10 規約 2）。止めるのは `--gate` のときだけで、そのときは門の値だけ checks に残す
        from ail.validation import gate
        gate_doc = gate.evaluate_gate(panel, feats, exp, run)
        gated_exp = apply_gate(exp, gate_doc, args.gate, run)
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
        res, per_sym, g, daily, extra = evaluate_trading(panel, feats, exp, run)
        run.log("")
        run.log(g.to_string())
        run.log(f"\n⚠ 純利 = 売買した日だけ片道 {float(exp.get('cost_bp', 5.0)) / 2:g}bp を引いた後"
                "（rules.md 13-4）。⚠ **採否は対 B&H の上乗せで測る**（13-7。純利の符号では"
                "「買って持っただけ」と区別できない）。")
        run.result(res, g)
        run.per_symbol(per_sym)
        run.holds(extra.get("holds"))              # ⚠ 1 取引 1 行の保有日数（13-4 の 6。採否には使わない）
        run.topk(extra.get("topk"))                # ⚠ 上位 K の診断（rules.md 17-5 の 4。採否には使わない）
        # ⚠ **日次のポートフォリオ系列を残す。** これが無かったので、検出限界の検討は同じ config を
        # ⚠ **回し直して系列を作り直すしかなかった**（validation-power.md §1）。エピソード表もここを読む
        run.daily(daily, extra)
        # ⚠ **`summary.csv` を書いたあとに数える。** 台帳はそれを読むので、この実行の行
        # （検証方式が処置 ＝ 選別 × 閾値の数。門前の手法は selectors から外れている）は
        # ⚠ **もう台帳に入っている。この実行ぶんを足さない**（13-9・14-5。足すと二重になる）
        doc = checks.compute_trading(res, g, per_sym, daily, exp,
                                     n_trials=checks.n_trials_now(),
                                     leak=args.leak, panel=full_panel, extra=extra)
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
