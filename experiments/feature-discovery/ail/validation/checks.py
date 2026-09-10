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


def n_trials_now(extra: int = 0) -> int | None:
    """台帳が数えている試行数 ＋ この実行ぶん。⚠ **数え落とすと必ず甘くなる**（rules.md 11 章 規約 4）。

    ⚠ **数える規則は `catalog.is_trial` が正本**（カタログ ID の行 ＋ モデルが処置の行）。
    """
    try:
        from ail import catalog
        rows, _leak, _runs = catalog.trials()
        return len([r for r in rows if catalog.is_trial(r)]) + int(extra)
    except Exception:
        return None
