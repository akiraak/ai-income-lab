"""偽の実行の物差しを、選ぶ段の上位の行と「採る」の行に当てる（登録簿 `20261109-null-shuffle-top-rows`）。⚠ **読むだけ。**

    python3 -m cli.null_rows --target sel1995|mix|sel600 [--perms 200] [--state <途中の置き場.json>] [--json out.json] [--save]

`cli.null_shuffle`（登録簿 `20261107-date-shuffle-null`）は素の own × Ridge だけに当てた。ここでは **対象の行を作った道そのもの**
（`cli.run.evaluate_trading` ＝ 選別 ・ 合わせる口 ・ 訓練分割の成績で銘柄を選ぶ条件 ・ 整数株の器）を通す。
変えるのは fold の切り口だけ: `splits.folds_by_dates` が返す訓練分割 ／ 評価分割の `y` を、`cli.null_shuffle.shuffle_y` で
日付の塊ごとに入れ替える（⚠ 塊 20 営業日 ・ 200 回 ・ 断面を保つ ・ fold をまたがない ＝ 11/7 と同じ水準。振らない）。

対象（登録簿のとおり・θ 50 の 1 水準 ＝ 選ぶ段と検証結果一覧の行）:
  sel1995 … `sel_small4_1995` の F1-7 IC の安定性 ／ F2-2 RFE（対 B&H ＝ 基準 常に上）
  mix     … `trader_mix_t4` の M1 3本の平均（対 B&H）。⚠ 外のメンバー 2 本も同じ日付の入れ替えで学ぶ
            （fold ・ 分割ごとに最初に来た表の日付で入れ替えを 1 度作り、ほかの表にも同じ対応を当てる。持ち主だけエンバーゴで訓練が
             1 日短い fold は、対応に無い日の行を落として数を記録する）
  sel600  … `sel600_midcap_cs` の「選抜5・訓練リターン・1本$600」（対 同じ 5 本を持ち続ける）。⚠ 選抜は入れ替えた `y` でやり直す
            （`select.by_return` は訓練分割の `y` を読む ＝ 選抜も偽になる）
対象の config は、対象の行に要らない部品（ほかの選別 ・ ほかの θ ・ ほかのトレーダーの条件）を外した写し。
⚠ 元の行（入れ替えなし）を同じ関数で回し、検証結果一覧の値と一致することを先に確かめる（外した部品が効いていないことの確認）。

⚠ **検証結果一覧に行を作らない・`n_trials` に数えない・`runs/` を作らない**（記録の口は何もしない写しを渡す）。
⚠ 判定の式は変えない（並べて見せるだけ）。`--save` のときだけ DB の `outputs` の `diag/<時刻>_null_rows_<対象>/` に JSON を入れる。
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import time

import numpy as np
import pandas as pd

from ail import config, runs
from ail.contracts import META_COLUMNS
from ail.validation import splits
from cli import null_shuffle as ns

BH = "基準 常に上（ドリフト）"
THETA = 50.0
SEL_TAG = "選抜5・訓練リターン・1本$600"


def _targets() -> dict:
    from cli import run as run_mod
    return {
        "sel1995": {"experiment": "sel_small4_1995",
                    "rows": [("F1-7 IC の安定性", BH), ("F2-2 RFE", BH)]},
        "mix": {"experiment": "trader_mix_t4", "rows": [("M1 3本の平均", BH)]},
        "sel600": {"experiment": "sel600_midcap_cs",
                   "rows": [(run_mod.trader_name("全部使う（基準）", SEL_TAG), run_mod.trader_hold_name(SEL_TAG))]},
    }


def trimmed(target: str) -> dict:
    """対象の行に要る部品だけを残した config の写し（θ 50 ・ 基準線は B&H だけ）。"""
    exp = copy.deepcopy(config.resolve_experiment(_targets()[target]["experiment"]))
    ds = exp["_dataset"]
    exp.setdefault("horizon_min", 1440.0 if ds["period"] == "d" else float(exp["horizon"]))
    exp["baselines"] = ["常に上（ドリフト）"]
    t = exp["trading"]
    t["thresholds"] = [THETA]
    if target == "sel1995":
        exp["selectors"] = ["F2-2 RFE", "F1-7 IC の安定性"]
    elif target == "mix":
        t.pop("trader", None)
    elif target == "sel600":
        t["trader"] = [c for c in t["trader"] if c.get("tag") == SEL_TAG]
        assert len(t["trader"]) == 1
    return exp


class _NoRun:
    """記録の口の写し（何も書かない）。"""

    def log(self, text: str = "") -> None:
        pass

    def fitted(self, name: str, doc) -> None:
        pass

    def selected(self, picked) -> None:
        pass


def apply_src(df: pd.DataFrame, src: pd.Series) -> pd.DataFrame:
    """df の `y` を「日 → 借り先の日」の対応 src で差し替えた写し（`null_shuffle.shuffle_y` と同じ差し替え方。
    対応に無い日の行・借り先にその銘柄の行が無い行は落とす）。"""
    ts = pd.to_datetime(df["ts"])
    look = df.assign(_ts=ts).set_index(["symbol", "_ts"])["y"]
    look = look[~look.index.duplicated()]
    key = pd.MultiIndex.from_arrays([df["symbol"].to_numpy(), pd.DatetimeIndex(src.reindex(ts).array)])
    out = df.assign(y=look.reindex(key).to_numpy())
    out = out[np.isfinite(out["y"].to_numpy(dtype=float))].reset_index(drop=True)
    if "y_sign" in out:
        out["y_sign"] = np.sign(out["y"])
    return out


class shuffled_folds:
    """`splits.folds_by_dates` を、返す訓練 ／ 評価の `y` を入れ替える版に差し替える（with の間だけ）。

    入れ替え（日 → 借り先の日）は (種, fold, 分割) ごとに **最初に来た表の日付で 1 度だけ作り**、後から来る表にも同じ対応を当てる
    ＝ 持ち主の表と外のメンバーの表が同じ入れ替えで学ぶ。日付の集合が違う表（持ち主だけエンバーゴで訓練が 1 日短い、など）は
    `borrowed` に数え、対応に無い日の行を落とす。表が 1 枚だけなら `null_shuffle.shuffle_y` と同じ入れ替えになる。
    """

    def __init__(self, seed: int | None, block: int = ns.BLOCK):
        self.seed, self.block = seed, block
        self.src: dict[tuple[int, int], pd.Series] = {}
        self.borrowed = 0

    def _shuffle(self, df: pd.DataFrame, f: int, part: int) -> pd.DataFrame:
        df = df.reset_index(drop=True)
        days = pd.DatetimeIndex(pd.to_datetime(df["ts"]).unique()).sort_values()
        key = (int(f), part)
        if key not in self.src:
            rng = np.random.default_rng([int(self.seed), int(f), part, 20261109])
            self.src[key] = pd.Series(ns.date_permutation(days, self.block, rng), index=days)
        elif len(days) != len(self.src[key]) or not days.equals(self.src[key].index):
            self.borrowed += 1
        return apply_src(df, self.src[key])

    def __enter__(self):
        self.orig = splits.folds_by_dates
        if self.seed is None:
            return self

        def patched(*a, **k):
            for f, tr, te in self.orig(*a, **k):
                yield f, self._shuffle(tr, f, 0), self._shuffle(te, f, 1)
        splits.folds_by_dates = patched
        return self

    def __exit__(self, *exc):
        splits.folds_by_dates = self.orig
        return False


def one(target: str, exp: dict, panel: pd.DataFrame, feats: list[str], seed: int | None) -> dict:
    """1 回の（偽の）実行 → 行ごとの fold の上乗せと判定。seed が None なら入れ替えない（元の行）。"""
    from cli import run as run_mod
    from cli.planted_power import verdict

    with shuffled_folds(seed) as sf:
        res, _sym, _summary, _daily, _extra = run_mod.evaluate_trading(panel, feats, copy.deepcopy(exp), _NoRun())
    res = res[res["閾値"] == THETA]
    out = {"_borrowed": sf.borrowed}
    for name, base in _targets()[target]["rows"]:
        a = res[res["手法"] == name].set_index("fold")["純利bp"]
        b = res[res["手法"] == base].set_index("fold")["純利bp"]
        if a.empty or b.empty:
            raise SystemExit(f"⚠ 行が無い: {name!r} ／ {base!r}（ある手法: {sorted(res['手法'].unique())[:12]}）")
        e = [float(a.get(f, np.nan) - b.get(f, np.nan)) for f in sorted(b.index)]
        out[name] = {"edges": e, "verdict": verdict(e), "mean": float(np.nanmean(e))}
    return out


def summarize(null: list[dict], orig: dict, name: str) -> dict:
    m = np.array([r[name]["mean"] for r in null])
    allpos = np.array([np.all(np.asarray(r[name]["edges"]) > 0) for r in null])
    vs = [r[name]["verdict"] for r in null]
    o = orig[name]["mean"]
    return {"row": name, "perms": len(null), "null_p05": float(np.quantile(m, 0.05)), "null_p50": float(np.quantile(m, 0.5)),
            "null_p95": float(np.quantile(m, 0.95)), "全fold正の割合": float(allpos.mean()), "採る": vs.count("採る") / len(vs),
            "保留": vs.count("保留") / len(vs), "落とす": vs.count("落とす") / len(vs), "元の上乗せ": o,
            "元の判定": orig[name]["verdict"], "元のfold": orig[name]["edges"],
            "元の分位": float((m < o).mean() + 0.5 * (m == o).mean())}


def null_dist(target: str, perms: int = ns.PERMS, state: str | None = None, log=print) -> dict:
    from cli.run import load_panel

    exp = trimmed(target)
    panel, path = load_panel(exp["name"], exp["_dataset"]["period"], False, exp.get("features_from"))
    feats = [c for c in panel.columns if c not in META_COLUMNS]
    done: dict = {}
    if state and os.path.exists(state):
        with open(state, encoding="utf-8") as fh:
            done = json.load(fh)
    t0 = time.perf_counter()
    if "orig" not in done:
        done["orig"] = one(target, exp, panel, feats, None)
        done["orig_seconds"] = time.perf_counter() - t0
    orig = done["orig"]
    names = [n for n, _b in _targets()[target]["rows"]]
    log("元の行: " + " ／ ".join(f"{n} {orig[n]['mean']:+.1f}bp/fold {orig[n]['verdict']}" for n in names)
        + f"（{done['orig_seconds']:.0f} 秒）")
    null = done.setdefault("null", [])
    for s in range(len(null), perms):
        null.append(one(target, exp, panel, feats, s))
        if state:
            with open(state + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(done, fh, ensure_ascii=False)
            os.replace(state + ".tmp", state)
        if (s + 1) % 10 == 0:
            log(f"{s + 1}/{perms} 回・{time.perf_counter() - t0:.0f} 秒")
    out = {"target": target, "experiment": exp["name"], "table": path, "theta": THETA, "block_days": ns.BLOCK, "perms": len(null),
           "rows": names, "orig": orig, "null": null, "borrowed_parts": int(sum(r.get("_borrowed", 0) for r in null)),
           "summary": [summarize(null, orig, n) for n in names] if null else [], "seconds": time.perf_counter() - t0}
    for r in out["summary"]:
        log(f"{r['row']}: 偽の上乗せ 5/50/95% {r['null_p05']:+.0f} ／ {r['null_p50']:+.0f} ／ {r['null_p95']:+.0f}bp/fold・"
            f"全 fold 正 {r['全fold正の割合']:.1%}・採る {r['採る']:.1%} 保留 {r['保留']:.1%}・"
            f"元 {r['元の上乗せ']:+.0f}（{r['元の判定']}・分位 {r['元の分位']:.3f}）")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="偽の実行の物差しを上位の行と「採る」の行に当てる（読むだけ・n_trials に数えない）")
    p.add_argument("--target", required=True, choices=["sel1995", "mix", "sel600"])
    p.add_argument("--perms", type=int, default=ns.PERMS)
    p.add_argument("--state", help="途中の結果を置く JSON（あれば続きから）")
    p.add_argument("--json")
    p.add_argument("--save", action="store_true", help="DB の outputs の diag/<時刻>_null_rows_<対象>/ にも入れる")
    a = p.parse_args()
    doc = null_dist(a.target, a.perms, a.state)
    text = json.dumps(doc, ensure_ascii=False, indent=1, default=float)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            fh.write(text)
    if a.save:
        print("→ " + runs.put_output(f"diag/{time.strftime('%Y-%m-%dT%H-%M-%S')}_null_rows_{a.target}/null_rows.json", text))


if __name__ == "__main__":
    main()
