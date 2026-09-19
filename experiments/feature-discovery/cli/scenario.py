"""条件付き GAN のシナリオ予測の入口（記録 `docs/specs/experiments/cgan-scenario.md`）。

    python3 -m cli.scenario run --config cgan_spy                 # 5 fold × 種 3 を学習 → テストの塊を評価 → 判定
    python3 -m cli.scenario run --config cgan_spy --folds f1 --seeds 0 --max-epochs 10   # 配線の確認（⚠ 判定は出さない）
    python3 -m cli.scenario predict --run runs/<実行> --latest    # 保存した重みから最新日の予測（JSON）
    python3 -m cli.scenario predict --run runs/<実行> --asof 2025-03-14

⚠ **`cli.run` とは別の物差し（分布 → CRPS）。** 実行は `runs/<時刻>_scn_<名前>/` に残すが、`summary.csv` を書かないので
台帳・検証タブには出ない（記録 §0 決定 6）。成績は `scores.csv`、起点ごとの生の行は `origins.csv`、判定は `verdict.json`。
⚠ **評価の実行は毎回学習し直す**（前の実行の `fitted/` を読まない）。`fitted/` を読むのは `predict` だけ。
"""

from __future__ import annotations

import argparse
import json
import os
import time

import numpy as np
import pandas as pd

from ail import config, runs
from ail.scenario import baselines, data, evaluate, metrics, model

RETURN_BASIS = "price_return_split_adjusted"      # 分割は調整済み・配当は未調整（記録 §0 決定 10）
TEST_Z_SEED = 20_000                               # テストの z の種（＋ 種）。検証の z（＋10,000）とは別の乱数列


def _code_fingerprint() -> dict:
    """使ったコードの sha256（先頭 16 桁）。⚠ **未コミットのまま回しても、どのコードの数字かを後から確かめられるように**。"""
    import glob
    import hashlib

    root = runs.ROOT
    files = sorted(glob.glob(os.path.join(root, "ail", "scenario", "*.py"))) + [os.path.join(root, "cli", "scenario.py")]
    return {os.path.relpath(f, root): hashlib.sha256(open(f, "rb").read()).hexdigest()[:16] for f in files}


def _prepare(cfg: dict):
    bars, feats, names = data.load(cfg)
    s = data.build_samples(feats, names, int(cfg["window"]), int(cfg["horizon"]))
    return bars, feats, names, s


def run(args) -> None:
    cfg = config.scenario(args.config)
    if args.max_epochs:
        cfg["model"]["max_epochs"] = int(args.max_epochs)
    seeds = [int(x) for x in args.seeds.split(",")] if args.seeds else [int(x) for x in cfg["seeds"]]
    bars, feats, names, s = _prepare(cfg)
    folds = data.split(s, **cfg["split"])
    if args.folds:
        folds = [f for f in folds if f.name in set(args.folds.split(","))]
    full = (not args.folds and not args.seeds and not args.max_epochs)
    n = int(cfg["n_scenarios"])
    r = runs.Run(f"scn_{cfg['name']}" + ("" if full else "_partial"), cfg, seeds[0])
    r.inputs({**data.fingerprint(cfg["period"], cfg["symbol"]), "features": list(names), "samples": len(s),
              "labeled": int(s.labeled.sum()), "code": _code_fingerprint(), "quality": data.quality_flags(bars, time.strftime("%Y-%m-%d")),
              "folds": {f.name: {"train": len(f.train), "val": len(f.val), "test": len(f.test),
                                 "val_start": str(f.val_start), "test_start": str(f.test_start),
                                 "test_stop": str(f.test_stop)} for f in folds}, "seeds": seeds})
    os.makedirs(os.path.join(r.dir, "fitted"), exist_ok=True)
    rows, history, quality, flagged = [], [], [], []
    for f in folds:
        sc = data.Scaler.fit(feats, names, s.origin[f.train].max())
        Xtr, Ytr, Xva, Yva, Xte = sc.x(s.X[f.train]), sc.y(s.Y[f.train]), sc.x(s.X[f.val]), sc.y(s.Y[f.val]), sc.x(s.X[f.test])
        Yte = s.Y[f.test].astype(np.float64)
        y5, drop = metrics.cumulative_returns(Yte)[:, -1], metrics.drop_event(Yte)
        base = {"fold": f.name, "origin": s.origin[f.test].astype(str), "y5": y5, "drop_real": drop}
        r.log(f"== {f.name}: 学習 {len(f.train)}・検証 {len(f.val)}・テスト {len(f.test)}（{f.test_start}〜）")

        # 比較対象（学習 ∪ 検証で fit。記録 §3-1）
        pool = np.concatenate([f.train, f.val])
        hist = baselines.historical(s.Y[pool], len(f.test), n, seed=0)
        rows.append(pd.DataFrame({**base, "model": "hist", "seed": np.nan, **evaluate.per_origin_from_paths(hist, Yte)}))
        lt = baselines.light(sc.x(s.X[pool]), s.Y[pool], Xte, n)
        cols = evaluate.per_origin_from_samples(lt["samples_5d"], y5)
        cols["prob_up"] = lt["prob_up"]                             # 上昇確率はロジスティック回帰のもの
        rows.append(pd.DataFrame({**base, "model": "light", "seed": np.nan, **cols, "prob_drop": np.nan}))
        point = baselines.ridge_point(sc.x(s.X[pool]), s.Y[pool], Xte)
        rows.append(pd.DataFrame({**base, "model": "ridge", "seed": np.nan, "median": point}))
        quality.append({"fold": f.name, "model": "実測（テスト）", "seed": None, **metrics.generation_quality(Yte)})
        quality.append({"fold": f.name, "model": "hist", "seed": None, **metrics.generation_quality(hist[0])})

        for seed in seeds:
            t0 = time.time()
            r.log(f"-- {f.name} 種 {seed}")
            gen, hist_rows = model.fit(Xtr, Ytr, Xva, Yva, cfg["model"], seed, sc.y_scale, log=r.log)
            picked = next(h for h in hist_rows if h["selected"])
            if picked["collapsed"] or picked["extreme"]:
                flagged.append(f"{f.name}_seed{seed}")
            history += [{"fold": f.name, "seed": seed, **h} for h in hist_rows]
            model.save(os.path.join(r.dir, "fitted", f"{f.name}_seed{seed}.pt"), gen, sc.to_dict(), cfg["model"],
                       len(names), int(cfg["horizon"]),
                       {"fold": f.name, "seed": seed, "epoch": picked["epoch"], "val_crps": picked["val_crps"],
                        "training_cutoff": str(s.label_end[f.train].max()), "val_cutoff": str(s.label_end[f.val].max())})
            paths = sc.y_inverse(model.generate(gen, Xte, n, seed=TEST_Z_SEED + seed).astype(np.float64))
            rows.append(pd.DataFrame({**base, "model": "cgan", "seed": seed, **evaluate.per_origin_from_paths(paths, Yte)}))
            quality.append({"fold": f.name, "model": "cgan", "seed": seed, **metrics.generation_quality(paths[:, :50])})
            r.log(f"   採った epoch {picked['epoch']}・検証 CRPS {picked['val_crps']:.6f}・{time.time() - t0:.0f} 秒")

    df = pd.concat(rows, ignore_index=True)
    df.to_csv(os.path.join(r.dir, "origins.csv"), index=False)
    pd.DataFrame(history).to_csv(os.path.join(r.dir, "history.csv"), index=False)
    pd.DataFrame(quality).to_csv(os.path.join(r.dir, "quality.csv"), index=False)
    scores = evaluate.summary(df)
    scores.to_csv(os.path.join(r.dir, "scores.csv"), index=False)
    r.log("")
    r.log("== 5 塊を合わせた成績（⚠ CRPS は低いほどよい。NaN ＝ 対象外）")
    r.log(scores[scores["fold"] == "all"].drop(columns=["fold"]).round(5).to_string(index=False))
    if full:
        verdict = evaluate.judge(df, cfg["eval"], flagged)
        r._write("verdict.json", verdict)
        r.log("")
        r.log(f"== 判定: {verdict['verdict']}  " + " ".join(f"({k}) {'○' if v else '×'}" for k, v in verdict["ok"].items()))
        a = verdict["a_diff_vs_hist"]
        r.log(f"   (a) CRPS の差 cGAN − 履歴ベース {a['mean']:+.6f}（95% 区間 {a['ci_low']:+.6f} 〜 {a['ci_high']:+.6f}）")
        r.log(f"   (b) 塊ごと {verdict['b_per_fold']}  (c) 種ごと {verdict['c_per_seed']}")
        r.log(f"   (d) 被覆率 {verdict['d_coverage']}・印 {verdict['d_flagged']}  (e) {verdict['e_crps']}")
    else:
        r.log("⚠ 一部だけの実行（--folds / --seeds / --max-epochs）なので判定は出さない")
    print(r.close())


def predict(args) -> None:
    cfg = json.load(open(os.path.join(args.run, "config.json"), encoding="utf-8"))
    bars, feats, names, s = _prepare(cfg)
    asof = s.origin[-1] if args.latest else np.datetime64(args.asof, "D")
    hit = np.flatnonzero(s.origin == asof)
    if not len(hit):
        raise SystemExit(f"⚠ {asof} を起点にした窓が無い（休場日か、足がまだ無い。最後の足は {s.origin[-1]}）")
    i = int(hit[0])
    # ⚠ **起点より前に学習を終えた重みのうち、一番新しい塊のもの**を使う（未来の答えで学習した重みを使わない）
    starts = [np.datetime64(x, "D") for x in cfg["split"]["test_starts"]]
    usable = [k for k, t0 in enumerate(starts, 1) if t0 <= asof]
    if not usable:
        raise SystemExit(f"⚠ {asof} より前に学習を終えた重みが無い（最初のテストの始まりは {starts[0]}）")
    path = os.path.join(args.run, "fitted", f"f{usable[-1]}_seed{args.seed}.pt")
    gen, doc = model.load(path)
    sc = data.Scaler.from_dict(doc["scaler"], names)
    n = int(cfg["n_scenarios"])
    paths = sc.y_inverse(model.generate(gen, sc.x(s.X[i:i + 1]), n, seed=TEST_Z_SEED + args.seed)[0].astype(np.float64))
    day = data.dates_of(bars)
    after = day[day > pd.Timestamp(str(asof))].dt.strftime("%Y-%m-%d").tolist()[:int(cfg["horizon"])]
    out = {"symbol": cfg["symbol"], "as_of": str(asof), "data_cutoff": str(asof),
           "data_available_at": "起点の営業日の引け（16:00 ET。半日立会は 13:00 ET）の後",
           "horizon_dates": after or "未確定（起点の翌営業日から 5 営業日）", "n_scenarios": n,
           "model_version": f"{os.path.basename(os.path.normpath(args.run))}/{os.path.basename(path)}",
           "training_cutoff": doc["meta"]["training_cutoff"], "checkpoint_selected_until": doc["meta"]["val_cutoff"],
           "return_basis": RETURN_BASIS, **metrics.summarize_paths(paths),
           "calibration_status": "確率の補正はしていない（生成した割合そのまま ＝ モデル推定の確率）。検証は記録 cgan-scenario.md",
           "data_quality_flags": data.quality_flags(bars, time.strftime("%Y-%m-%d")),
           "notes": ["予測帯は各日の周辺分布の区間で、経路全体が帯に入る確率ではない",
                     "下落確率は日足の終値ベース（日中の安値・ストップ注文の約定確率ではない）"]}
    print(json.dumps(out, ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("run", help="学習 → テストの塊を評価 → 判定")
    a.add_argument("--config", required=True, help="config/scenario/<名前>.toml の名前")
    a.add_argument("--folds", help="f1,f2 のように絞る（⚠ 絞ると判定は出さない）")
    a.add_argument("--seeds", help="0,1 のように絞る（⚠ 同上）")
    a.add_argument("--max-epochs", type=int, help="学習の上限を縮める（配線の確認用。⚠ 同上）")
    a.set_defaults(fn=run)
    b = sub.add_parser("predict", help="保存した重みから予測（JSON）")
    b.add_argument("--run", required=True, help="runs/<実行> のパス")
    g = b.add_mutually_exclusive_group(required=True)
    g.add_argument("--asof", help="予測起点の営業日（YYYY-MM-DD）")
    g.add_argument("--latest", action="store_true", help="足のある最後の営業日")
    b.add_argument("--seed", type=int, default=0)
    b.set_defaults(fn=predict)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
