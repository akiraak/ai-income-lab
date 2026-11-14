"""標準化のあとに挟む「標本から学ぶ変換」の 1 段（rules.md 3 章 B）。

⚠ **config に `transform` が無ければ 1 行も通らない。** 既存の実行はここを素通りする
（プラン `plans/selectors-small-four.md` §1-1 の (c)）。
⚠ **この前提が崩れると、配線を足したことが静かに全実行に影響する。** テストで固定してある
（`tests/test_selectors_small4.py` の「変換が無ければ表が同一オブジェクトのまま」）。

⚠ **fit は訓練分割だけ・検証分割には transform だけ。** 規律を守る責任は変換の実体が負う
（`ail/data/transforms/fitted.py` の `FittedTransform` が fit と transform を分けている）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ail import registry

# ⚠ **変換を検知器に通す口**（rules.md 24 章。2026-10-09 利用者決定 A「汎用の口」）。
# 変換が返した列はこの接頭辞に限らない（GA ＝ `gp_NN`・PCA ＝ `pc_…`）。⚠ 既存の列名と重なれば止める（`augment`）


def apply(exp: dict, Xtr: pd.DataFrame, Xte: pd.DataFrame, ctx: dict, ytr=None, ts_tr=None):
    """(Xtr, Xte, 係数) を返す。⚠ **変換が無ければ入力をそのまま返す**（同じオブジェクト）。

    ⚠ **`ytr` は教師つきの変換だけが読む**（F4-1 記号回帰の適合度。2026-09-16 に足した）。
    ⚠ **渡すのは訓練分割の y だけ**で、⚠ **検証分割の y はこの口に来ない**（3 章 B）。
    ⚠ **ctx は複製して渡す** — 呼び元の ctx に混ぜると、選別やモデルにも見えてしまう。
    """
    name = exp.get("transform")
    if not name:
        return Xtr, Xte, None
    if ytr is not None:
        ctx = {**ctx, "ytr": ytr}
    # ⚠ **GA の適合度の測り方**（rules.md 23 章。2026-10-08）: `fitness` は config の平の key（無ければ渡さない ＝ pooled）。
    # `ts_tr` は訓練分割の行の日付（断面・時期で測る適合度だけが読む。⚠ 検証分割の日付は来ない）
    if ts_tr is not None:
        ctx = {**ctx, "ts_tr": ts_tr}
    if exp.get("fitness", "pooled") != "pooled":
        ctx = {**ctx, "fitness": exp["fitness"]}
    return registry.resolve("transform", name)(Xtr, Xte, ctx)


XS_LABEL = "xs_excess"     # 登録簿 20261025-xs-excess-label（2026-10-25）
VOL_LABEL = "vol_scaled"   # 登録簿 20261101-vol-scaled-label（2026-11-01）
VOL_COLUMN = "own_vol_60"  # その銘柄の直近 60 日の日次 σ（足 t の終値まで ＝ 未来を見ない）


def train_label(exp: dict, tr: pd.DataFrame):
    """訓練のラベル。⚠ **`label` の無い config は `tr["y"].values` そのまま**（1 ビットも変わらない ＝ 既定経路の指紋）。

    `label = "xs_excess"`（開発ループの登録簿 `20261025-xs-excess-label`・rules.md 31 章）: 訓練のラベルを **y − 同じ日の表の全銘柄の y の平均**
    （断面の超過リターン）にする。訓練の行だけで作る（検証の行の y は使わない）。評価の損益・売買の器・θ・較正の式は変えない
    （較正は符号で行うので「平均を上回ったか」の確率になる）。⚠ 学習範囲 shared だけ（銘柄別は呼ぶ側が拒む）。
    """
    kind = exp.get("label")
    if not kind:
        return tr["y"].values
    if kind == VOL_LABEL:
        # 訓練のラベルを y ÷ σ（`own_vol_60`・標準化の前の値）にする。⚠ σ が欠けた ／ 0 以下の行は訓練の σ の中央値で割る（行を落とさない）
        if VOL_COLUMN not in tr:
            raise SystemExit(f"⚠ label = {VOL_LABEL!r} には表に {VOL_COLUMN} が要る（own 層）")
        sig = tr[VOL_COLUMN].astype(float)
        sig = sig.where(sig > 0).fillna(float(sig[sig > 0].median()))
        return (tr["y"].astype(float) / sig).to_numpy(dtype=float)
    if kind != XS_LABEL:
        raise SystemExit(f"⚠ label = {kind!r} は受けない（{XS_LABEL!r} ／ {VOL_LABEL!r} だけ。rules.md 31 章の登録簿）")
    y = tr["y"].astype(float)
    return (y - y.groupby(tr["ts"]).transform("mean")).to_numpy(dtype=float)


# 登録簿 20261101-deadzone-label（2026-11-13 サンドボックスの 1 本目・rules.md 31-7）: 訓練の行のうち、翌日の動きが小さい行を外す
DEADZONE = {"a": 0.25, "b": 0.5}   # |y| < 倍率 × own_vol_60 の行を訓練から外す（事前固定 ＝ レジストリの structure）


def deadzone_model(exp: dict, model, tr: pd.DataFrame):
    """`label_deadzone = "a" | "b"` のとき、学習に渡す行から「小さな値動きの行」を外すモデルを返す。

    ⚠ **`label_deadzone` の無い config は `model` そのもの**（同じオブジェクト ＝ 1 ビットも変わらない）。
    外すのは **学習の行だけ**: 較正の holdout の尻（予測する側）・評価の行は外さない。行の対応は `Xtr` の RangeIndex（0..n-1 ＝ `tr` の並び）で引く
    ＝ 較正が `tail_holdout` で頭を切っても同じ行が外れる。σ が欠けた ／ 0 以下の行は外さない。
    """
    kind = exp.get("label_deadzone")
    if not kind:
        return model
    if kind not in DEADZONE:
        raise SystemExit(f"⚠ label_deadzone = {kind!r} は受けない（{' ／ '.join(DEADZONE)} だけ。登録簿 20261101-deadzone-label）")
    if VOL_COLUMN not in tr:
        raise SystemExit(f"⚠ label_deadzone には表に {VOL_COLUMN} が要る（own 層）")
    sig = tr[VOL_COLUMN].astype(float).to_numpy()
    y = tr["y"].astype(float).to_numpy()
    with np.errstate(invalid="ignore"):
        small = (sig > 0) & (np.abs(y) < DEADZONE[kind] * sig)
    keep = ~small

    def wrapped(Xa, ya, Xb, ctx):
        idx = np.asarray(Xa.index)
        if len(idx) and (idx.min() < 0 or idx.max() >= len(keep)):
            raise SystemExit("⚠ label_deadzone: 学習の行が訓練分割の並びと対応しない（index が 0..n-1 でない）")
        k = keep[idx]
        return model(Xa[k], np.asarray(ya)[k], Xb, ctx)

    wrapped.deadzone_dropped = float(small.mean())      # 外した行の割合（記録用）
    return wrapped


def label(exp: dict, method: str) -> str:
    """手法名に変換を混ぜる。⚠ **変換名を先頭に置く。**

    ⚠ **台帳は手法名の先頭から `F5-1` を ID として読む**（`ail/catalog.py` の `_ID`）。
    ⚠ **混ぜないと「全部使う（基準）」の行に化けて、その試行が数えられない**（rules.md 11 章 規約 4）。
    """
    name = exp.get("transform")
    if not name:
        return method
    # ⚠ **GA の適合度を替えた行は〔適合度・…〕を後ろに付ける**（rules.md 23-2。既定 pooled は付けない ＝ 既存の名前のまま）
    fit = exp.get("fitness", "pooled")
    if fit == "pooled":
        return f"{name} ＋ {method}"
    from ail.search.evolve import FITNESS_LABELS
    if fit not in FITNESS_LABELS:
        raise SystemExit(f"⚠ fitness は pooled ／ {' ／ '.join(FITNESS_LABELS)}: {fit!r}（rules.md 23-1）")
    return f"{name} ＋ {method}{FITNESS_LABELS[fit]}"


def augment(exp: dict, tr: pd.DataFrame, te: pd.DataFrame, feats: list[str], ctx: dict):
    """検知器に渡す表へ、変換の列を**足す**口（rules.md 24 章）。(tr, te, feats, 係数) を返す。

    ⚠ **config に `transform` が無ければ入力をそのまま返す**（同じオブジェクト。1 ビットも変えない ＝
    この経路は 13500t の毎日の予測 `cli/predict.py` も通る）。
    ⚠ **fit は訓練分割の内側だけ**: 標準化を訓練で fit → `apply`（y と日付は訓練のもの）→ 返った列を
    `tr`・`te` に同じ名前で足す。⚠ **置き換えない**（検知器は自分の列を接頭辞で選ぶので、元の列が無いと動かない）。
    ⚠ **返った列の名前が表の既存の列と重なれば止める**（黙って上書きすると元の列が消える）。
    ⚠ 変換の係数は呼び元が `fitted_doc["_transform"]` に残す（次の実行では読み込まない。3 章 B）。
    """
    if not exp.get("transform"):
        return tr, te, feats, None
    from sklearn.preprocessing import StandardScaler

    from ail import contracts

    # ⚠ **変換の入力は説明変数だけ**（`contracts.is_meta` で外す。`y_fwd_…` ＝ 検知器の学習の対象はここで外す）。
    # ⚠ **2026-10-09 に踏んだ穴**: 本物の表では `feats` に `y_fwd_10` が残っている（検知器が自分で接頭辞を外す前提）。
    # それを変換にそのまま渡すと GA の式に答えが混ざり、leak 対照と同じ値になった（記録 §1-0）
    cols = [c for c in feats if not contracts.is_meta(c)]
    sc = StandardScaler().fit(tr[cols])
    Xtr = pd.DataFrame(sc.transform(tr[cols]), columns=cols)
    Xte = pd.DataFrame(sc.transform(te[cols]), columns=cols)
    ytr = tr["y"].values
    Ztr, Zte, tdoc = apply(exp, Xtr, Xte, ctx, ytr, ts_tr=tr["ts"].to_numpy())
    if list(Zte.columns) != list(Ztr.columns):
        raise SystemExit(f"⚠ 変換 {exp['transform']!r} が訓練と評価で違う列を返した（rules.md 24-1）")
    # ⚠ **入力の列をそのまま通したもの（名前が入力と同じ。F4-3 ／ F4-4 ／ F5-3 ／ F5-4 は `own` の列を残して返す）は足さない**
    # （検知器は元の列を持っている ＝ 足すと同じ名前が 2 本になる）。⚠ **入力に無い名前が表の列と重なれば止める**
    inputs = set(cols)
    passed = [str(c) for c in Ztr.columns if c in inputs]
    new = [str(c) for c in Ztr.columns if c not in inputs]
    clash = [c for c in new if c in tr.columns or c in te.columns]
    if clash:
        raise SystemExit(f"⚠ 変換 {exp['transform']!r} の列名が表の列と重なる: {clash[:5]}（rules.md 24-1 の 3）")
    if not new:
        raise SystemExit(f"⚠ 変換 {exp['transform']!r} は新しい列を 1 本も返さなかった（入力をそのまま通しただけ）")
    # ⚠ 位置で足す（`tr` の index は fold の切り出しのままなので、index で合わせない）
    tr = tr.assign(**{c: Ztr[c].to_numpy() for c in new})
    te = te.assign(**{c: Zte[c].to_numpy() for c in new})
    tdoc = {**(tdoc or {}), "口": {"足した列": len(new), "通したままの列（足さない）": len(passed), "入力の列": len(cols)}}
    return tr, te, list(feats) + new, tdoc
