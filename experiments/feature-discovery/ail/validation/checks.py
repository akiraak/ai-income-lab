"""1 実行ぶんの検査をまとめて計算し、`runs/<実行>/checks.json` に残す。

⚠ **重い計算は実行のときに 1 度だけ行う。** 管理画面は読むだけにする
（[プラン §2](../../../../docs/plans/archive/dashboard-experiments.md)）。⚠ **同じ数字が 2 か所で計算されない。**

⚠ **良い数字が出たときにしか意味が無い道具ではない。** 悪い数字のときも同じ形で残すから、
⚠ **後から「どの検証がどこまで通ったか」を並べて比べられる。**

    from ail.validation import checks
    doc = checks.compute(result, summary, config, panel=panel, n_trials=36)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail.validation import stats

# ⚠ **基準線はスコアにしない。** 「常に上」が最良になっては比較にならない
DRIFT = "基準 常に上（ドリフト）"


def is_baseline(name: str) -> bool:
    """基準線の行か。⚠ **`基準 ` で始まるものと、選別しない 2 本。**"""
    return name.startswith("基準 ") or name in ("全部使う（基準）", "乱択（基準）")


def _sign_row(values) -> dict:
    v = list(values)
    return {"pattern": "".join("＋" if x > 0 else "−" if x < 0 else "0" for x in v),
            "positive": int(sum(1 for x in v if x > 0)), "folds": len(v),
            "values": [round(float(x), 4) for x in v]}


def _t(values) -> float | None:
    """⚠ **fold は 5 本しかない。** t 値は目安であって検定ではない。"""
    v = np.asarray(list(values), dtype=float)
    if len(v) < 2 or v.std(ddof=1) == 0:
        return None
    return float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))


def best_method(summary: pd.DataFrame) -> str | None:
    """⚠ **基準線を除いた**、純利 bp が最大の手法。"""
    rows = [m for m in summary.index if not is_baseline(str(m))]
    if not rows:
        return None
    return str(max(rows, key=lambda m: float(summary.loc[m, "純利bp"])))


def compute(result: pd.DataFrame, summary: pd.DataFrame, config: dict,
            panel: pd.DataFrame | None = None, full_panel: pd.DataFrame | None = None,
            n_trials: int | None = None, leak: bool = False) -> dict:
    """検査を 1 つの辞書にする。⚠ **計算できないものは鍵ごと省く**（0 や null で埋めない）。"""
    doc: dict = {"leak": bool(leak), "cost_bp": float(config.get("cost_bp", 5.0))}

    name = best_method(summary)
    if name is None:
        return doc
    s = summary.loc[name]
    doc["best"] = {"method": name, "純利bp": round(float(s["純利bp"]), 4),
                   "粗利bp": round(float(s["粗利bp"]), 4),
                   "的中率": round(float(s["的中率"]), 4), "IC": round(float(s["IC"]), 4),
                   "本数": round(float(s["本数"]), 2)}

    by = result.pivot(index="fold", columns="手法", values="純利bp")
    if name in by:
        doc["folds"] = _sign_row(by[name].sort_index())

    # ⚠ **ドリフトへの上乗せ。** 粗利で見る（「常に上」はほとんど回転せず本当はコストを払わない）
    g = result.pivot(index="fold", columns="手法", values="粗利bp")
    if name in g and DRIFT in g:
        d = (g[name] - g[DRIFT]).sort_index()
        doc["edge_vs_drift"] = {**_sign_row(d), "mean_bp": round(float(d.mean()), 4),
                                "t": (round(t, 4) if (t := _t(d)) is not None else None)}
        doc["drift_粗利bp"] = round(float(g[DRIFT].mean()), 4)

    if panel is not None:
        doc.update(_panel_checks(panel, full_panel, float(s["粗利bp"]), n_trials, config))
    return doc


def test_rows(panel: pd.DataFrame, config: dict) -> int:
    """⚠ **成績を測ったのは検証に回った行だけ。** パネル全体で数えると標本を水増しする。

    ⚠ **分割は fold を飛ばすことがある**（短すぎる fold）ので、⚠ **割り算で出さずに実際に回す。**
    """
    from ail import registry
    import ail.bootstrap  # noqa: F401

    v = config.get("validation", {})
    split = registry.resolve("split", v.get("split", "walk_forward"))
    return sum(len(te) for _f, _tr, te in split(
        panel, int(v.get("folds", 5)), float(config.get("horizon_min", 0.0)),
        int(v.get("embargo_bars", 0)), float(config.get("bar_minutes", 0.0))))


def _panel_checks(panel: pd.DataFrame, full_panel: pd.DataFrame | None, gross_bp: float,
                  n_trials: int | None, config: dict) -> dict:
    """⚠ **行数は標本数ではない**（rules.md 12 章 限界 2）。パネルが無いと出せない。

    ⚠ **系列どうしの相関は「間引く前」の表で測る。** 間引いた表では同じ時刻に全銘柄が
    揃わず、⚠ **相関行列が作れずに実効系列数が出せない**（2026-09-08 に踏んだ）。
    ⚠ **検証に回った行数のほうは、実際に回した「間引いた後」の表から数える。**
    """
    out: dict = {}
    if not {"symbol", "ts", "y"} <= set(panel.columns):
        return out
    wide = (full_panel if full_panel is not None else panel).pivot_table(
        index="ts", columns="symbol", values="y").dropna(how="any")
    if wide.shape[1] < 2 or len(wide) < 3:
        return out
    eb = stats.effective_breadth(wide)
    # ⚠ **検証に回った行を、実効系列数で割り引いて数える。**
    # ⚠ **パネル全体の行数を標本数と読むと、t 値も DSR も甘くなる。**
    n_test = test_rows(panel, config)
    n_obs_eff = (n_test / eb["系列数"]) * eb["実効系列数"] if eb["系列数"] else 0.0
    out["breadth"] = {"系列数": eb["系列数"], "実効系列数": round(eb["実効系列数"], 3),
                      "t値の割引": round(eb["t値の割引"], 4),
                      "パネルの時刻": len(wide), "検証の行": int(n_test),
                      "実効観測数": int(round(n_obs_eff))}

    sd = float(panel["y"].std())
    if sd > 0 and n_obs_eff >= 3 and n_trials and n_trials >= 2:
        sr = (gross_bp * 1e-4) / sd
        d = stats.deflated_sharpe(sr, int(n_obs_eff), int(n_trials))
        out["dsr"] = {"SR": round(sr, 6), "SR0": round(float(d["SR0"]), 6),
                      "DSR": round(float(d["DSR"]), 4),
                      # ⚠ **試行数は増えていく。** 正本は台帳で、ここは「そのときの値」
                      "n_trials": int(n_trials), "n_obs": int(round(n_obs_eff)),
                      "注記": "⚠ SR > 0 の検定であって、基準線を超えたかの検定ではない"}
    return out


# --- 閾値つき売買（rules.md 13 章） -------------------------------------

def _edge_bins(method_daily, bh_daily, per_fold: int = 2) -> dict | None:
    """fold 内で日数を等分した上乗せ符号（rules.md 14-3 の診断列）。⚠ **採否には使わない。**

    ⚠ **fold 境界（強制清算の位置）を bin がまたがない**ように、既存 fold の中だけで割る。
    偶然の全符号正は 5 fold の 3.1% から 10 bin の 0.098% に締まるが、
    ⚠ **全符号正を要求する検出限界はむしろ上がる**ので判定には使わない（validation-power.md §3-2）。
    B&H の系列と fold 数・日付が合わなければ None（計算できないものは省く）。
    """
    if not method_daily or not bh_daily or len(method_daily) != len(bh_daily):
        return None
    vals: list[float] = []
    for m, b in zip(method_daily, bh_daily):
        if len(m) != len(b) or not (m.index == b.index).all():
            return None
        e = (m.values - b.values)
        for part in np.array_split(np.arange(len(e)), per_fold):
            vals.append(float(e[part].sum()))
    d = _sign_row(vals)
    d["bins"] = d.pop("folds")
    t = _t(vals)
    return {"per_fold": per_fold, **d, "mean_bp": round(float(np.mean(vals)), 4),
            "t": (round(t, 4) if t is not None else None),
            "注記": "⚠ 診断列。採否は 5 fold の上乗せと DSR のまま（rules.md 14-3）"}


def _breadth_trading(panel) -> dict | None:
    """実効系列数の常時併記（rules.md 14-3）。⚠ **DSR の n_obs（検証日数）は変えない。**

    per_symbol の「勝ち銘柄 58/63」を独立な 58 勝と読み違えないための併記
    （63 系列の実効は 4.71 本【実測】。rules.md 12 章 限界 2）。
    """
    if panel is None or not {"symbol", "ts", "y"} <= set(panel.columns):
        return None
    wide = panel.pivot_table(index="ts", columns="symbol", values="y").dropna(how="any")
    if wide.shape[1] < 2 or len(wide) < 3:
        return None
    eb = stats.effective_breadth(wide)
    return {"系列数": eb["系列数"], "実効系列数": round(eb["実効系列数"], 3),
            "t値の割引": round(eb["t値の割引"], 4), "パネルの時刻": int(len(wide)),
            "注記": "⚠ per_symbol の読み違え防止の併記。n_obs は検証日数のまま（rules.md 14-3）"}


def _concat(parts) -> pd.Series | None:
    """fold ごとの日次系列を 1 本に繋ぐ。⚠ **fold の境目（強制清算）はそのまま残る。**"""
    if not parts:
        return None
    return pd.concat(parts).sort_index()


def _random_gate(method_parts, rand_parts) -> dict | None:
    """⚠ **同じ保有日率の乱択ゲートとの差**（rules.md 14-6 の b）。⚠ **基準線なので採否に使わない。**

    ⚠ **これが正で初めて「正しい日を休んだ」と言える。** 手法と乱択ゲートは保有日数が同じなので、
    ⚠ **差が 0 なら「ただ休んだだけ」**である（休む日をずらしても同じ成績になる）。
    """
    if not method_parts or not rand_parts or len(method_parts) != len(rand_parts):
        return None
    vals = [float(m.sum() - r.sum()) for m, r in zip(method_parts, rand_parts)]
    t = _t(vals)
    return {**_sign_row(vals), "mean_bp": round(float(np.mean(vals)), 4),
            "rand_純利bp": round(float(np.mean([r.sum() for r in rand_parts])), 4),
            "t": (round(t, 4) if t is not None else None),
            "注記": "⚠ 基準線（rules.md 14-6 b）。⚠ **採否には使わない**。"
                    "保有日数を保ったままポジションを巡回シフトしたもの"}


def _episodes(bh_parts, method_parts, hold_parts, min_drop_bp: float = 1000.0) -> dict | None:
    """⚠ **エピソード表**（rules.md 14-8）。⚠ **成果物であって採否には使わない。**

    B&H ポートフォリオの累積（bp・対数なので足せる）から、⚠ **山 → 谷が `min_drop_bp` 以上の
    下降エピソード**を拾い、同じ区間で手法が何 bp 取れたか・どれだけ休んだかを並べる。

    ⚠ **実効標本はエピソードの回数**（検証期間で 10 回台）なので、⚠ **検定には使えない。**
    ⚠ **fold の境目を跨ぐエピソードには強制清算のコストが入っている**（そのまま数える）。
    """
    bh, me = _concat(bh_parts), _concat(method_parts)
    if bh is None or me is None or len(bh) != len(me) or not (bh.index == me.index).all():
        return None
    hold = _concat(hold_parts)
    eq_bh, eq_me = bh.cumsum().to_numpy(), me.cumsum().to_numpy()
    days = bh.index
    rows: list[dict] = []
    peak, peak_i, trough_i, live = eq_bh[0], 0, None, False

    def close(recovery) -> None:
        lo, hi = peak_i, trough_i
        row = {"山": str(pd.Timestamp(days[lo]).date()),
               "谷": str(pd.Timestamp(days[hi]).date()),
               "回復": (None if recovery is None else str(pd.Timestamp(recovery).date())),
               "日数": int(hi - lo),
               "B&Hbp": round(float(eq_bh[hi] - eq_bh[lo]), 1),
               "手法bp": round(float(eq_me[hi] - eq_me[lo]), 1)}
        row["上乗せbp"] = round(row["手法bp"] - row["B&Hbp"], 1)
        if hold is not None:
            row["保有日率"] = round(float(hold.to_numpy()[lo + 1:hi + 1].mean()), 4) if hi > lo else None
        rows.append(row)

    for i in range(len(eq_bh)):
        if eq_bh[i] >= peak:
            if live:
                close(days[i])
                live = False
            peak, peak_i = eq_bh[i], i
        else:
            if not live and eq_bh[i] - peak <= -min_drop_bp:
                live, trough_i = True, i
            elif live and eq_bh[i] < eq_bh[trough_i]:
                trough_i = i
    if live:
        close(None)
    if not rows:
        return None
    edges = [r["上乗せbp"] for r in rows]
    return {"下げ幅の下限bp": min_drop_bp, "回数": len(rows),
            "上乗せが正のエピソード": int(sum(1 for e in edges if e > 0)),
            "上乗せの合計bp": round(float(sum(edges)), 1), "episodes": rows,
            "注記": "⚠ 成果物（rules.md 14-8）。⚠ **実効標本はエピソードの回数なので検定に使わない**"}


def best_method_trading(methods) -> str | None:
    """新方式の最良手法。⚠ **「基準 」と乱択だけを除く**（「全部使う」は検証方式が処置なので手法。13-9）。"""
    rows = [str(m) for m in methods
            if not str(m).startswith("基準 ") and str(m) != "乱択（基準）"]
    return rows[0] if rows else None


def compute_trading(result: pd.DataFrame, summary: pd.DataFrame, per_symbol: pd.DataFrame,
                    daily: dict, config: dict, n_trials: int | None = None,
                    leak: bool = False, panel: pd.DataFrame | None = None,
                    extra: dict | None = None) -> dict:
    """閾値つき売買の検査（rules.md 13 章）。⚠ **3 閾値とも残す**（良かった閾値だけ報告しない。13-3 の 3）。

    - fold の符号は **対 B&H の上乗せ**で見る（13-7。純利の符号では「買って持っただけ」と区別できない）
    - DSR の SR は **ポートフォリオ日次純利系列（fold 連結）** から。歪度・尖度も系列から実測して渡す。
      n_obs は検証日数（⚠ 行数 63 × 日数 を使わない）
    - 銘柄別 bp は要約だけ載せる（成果物は per_symbol.csv。⚠ **採否には使わない**）
    - 診断列（rules.md 14-3。⚠ **採否には使わない**）: `edge_bins`（fold 内 2 等分の上乗せ符号）と、
      `panel` があれば `breadth`（実効系列数）
    """
    t = config.get("trading", {})
    doc: dict = {"leak": bool(leak), "style": "threshold",
                 "form": str(t.get("form", "shared")),
                 "cost_bp": float(config.get("cost_bp", 5.0)),
                 "thresholds": [float(x) for x in t.get("thresholds", [])]}
    by: dict[str, dict] = {}
    for th in doc["thresholds"]:
        s_th = summary[summary["閾値"] == th]
        r_th = result[result["閾値"] == th]
        name = best_method_trading(
            s_th.sort_values("純利bp", ascending=False).index)
        if name is None:
            continue
        entry: dict = {}
        s = s_th.loc[name]
        entry["best"] = {"method": name, "純利bp": round(float(s["純利bp"]), 4),
                         "粗利bp": round(float(s["粗利bp"]), 4),
                         "的中率": round(float(s["的中率"]), 4),
                         "本数": round(float(s["本数"]), 2),
                         "取引回数": round(float(s["取引回数"]), 1),
                         "保有日率": round(float(s["保有日率"]), 4)}
        net = r_th.pivot(index="fold", columns="手法", values="純利bp")
        if name in net and DRIFT in net:
            e = (net[name] - net[DRIFT]).sort_index()
            entry["edge_vs_bh"] = {**_sign_row(e), "mean_bp": round(float(e.mean()), 4),
                                   "t": (round(t_, 4) if (t_ := _t(e)) is not None else None)}
            entry["bh_純利bp"] = round(float(net[DRIFT].mean()), 4)
        if (eb := _edge_bins(daily.get((name, th)), daily.get((DRIFT, th)))) is not None:
            entry["edge_bins"] = eb
        ex = extra or {}
        # ⚠ **どちらも診断・成果物であって採否には使わない**（rules.md 14-6 b・14-8）
        if (rg := _random_gate(daily.get((name, th)),
                               (ex.get("rand") or {}).get((name, th)))) is not None:
            entry["random_gate"] = rg
        if (ep := _episodes(daily.get((DRIFT, th)), daily.get((name, th)),
                            (ex.get("hold") or {}).get((name, th)))) is not None:
            entry["episodes"] = ep
        series = pd.concat(daily.get((name, th), [pd.Series(dtype=float)]))
        if len(series) >= 3 and float(series.std()) > 0 and n_trials and n_trials >= 2:
            sr = float(series.mean() / series.std())
            skew, kurt = float(series.skew()), float(series.kurt()) + 3.0  # pandas は超過尖度
            d = stats.deflated_sharpe(sr, len(series), int(n_trials), skew, kurt)
            entry["dsr"] = {"SR": round(sr, 6), "SR0": round(float(d["SR0"]), 6),
                            "DSR": round(float(d["DSR"]), 4),
                            "n_trials": int(n_trials), "n_obs": int(len(series)),
                            "歪度": round(skew, 4), "尖度": round(kurt, 4),
                            "注記": "⚠ SR > 0 の検定であって、B&H を超えたかの検定ではない"}
        ps = per_symbol[(per_symbol["手法"] == name) & (per_symbol["閾値"] == th)]
        if len(ps):
            tot = ps.groupby("銘柄")["純利bp"].sum()
            entry["per_symbol"] = {"銘柄数": int(len(tot)),
                                   "中央値bp": round(float(tot.median()), 4),
                                   "四分位bp": [round(float(tot.quantile(0.25)), 4),
                                                round(float(tot.quantile(0.75)), 4)],
                                   "勝ち銘柄": int((tot > 0).sum())}
        by[f"{th:g}"] = entry
    doc["by_threshold"] = by
    if (br := _breadth_trading(panel)) is not None:
        doc["breadth"] = br

    # 最良の閾値の写しを最上位にも置く（一覧の 1 数字）。⚠ **3 閾値とも by_threshold にある**
    if by:
        top = max(by, key=lambda k: by[k]["best"]["純利bp"])
        doc["best"] = {**by[top]["best"], "閾値": float(top)}
        for key in ("edge_vs_bh", "bh_純利bp", "dsr", "per_symbol", "edge_bins",
                    "random_gate", "episodes"):
            if key in by[top]:
                doc[key] = by[top][key]
        if "edge_vs_bh" in by[top]:
            # ⚠ fold の符号は上乗せで見る（13-7）。純利の符号は載せない
            doc["folds"] = {k: by[top]["edge_vs_bh"][k]
                            for k in ("pattern", "positive", "folds", "values")}
    return doc


def n_trials_now() -> int | None:
    """台帳が数えている試行数。⚠ **数え落とすと必ず甘くなる**（rules.md 11 章 規約 4）。

    ⚠ **数える規則は `catalog.is_trial` が正本**（カタログ ID の行 ＋ モデルが処置の行）。

    ⚠ **`summary.csv` を書いたあとに呼ぶ。** 台帳は `runs/*/summary.csv` を読むので、
    ⚠ **この時点で台帳はもうこの実行の行を数えている。** だから「この実行ぶん」を足さない
    （2026-09-12 まで足しており、各実行の `n_trials` がそのぶん多かった。
    ⚠ **足す方式は重複にも弱い** — 同じ設定を同じ表で回し直すと台帳の鍵では 1 試行のままだし、
    leak 対照は台帳に入らない。[validation-power.md §8-2-5](../../../../docs/specs/experiments/feature-discovery/validation-power.md)）。
    ⚠ **引数は受けない**（受けると同じ間違いがまた書ける）。
    """
    try:
        from ail import catalog
        rows, _leak, _runs = catalog.trials()
        return len([r for r in rows if catalog.is_trial(r)])
    except Exception:
        return None
