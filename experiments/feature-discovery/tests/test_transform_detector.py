"""変換を検知器に通す口（rules.md 24 章。[プラン](../../../docs/plans/transform-into-detectors.md) §4）。

⚠ **ここで落としたいのは 6 つ。**
  1. 指紋: `transform` の無い検知器の config（`fwd`）の出力が、口を足す前（commit 00e4958）と 1 ビットも同じ
  2. 指紋: `transform` が無ければ `prep.augment` は**同じオブジェクト**を返し、検知器名も変わらない
  3. 口が開く: 検知器が受け取る `feats`・`tr`・`te` に変換の列が**足されて**いる（元の列も残る）・手法名は「変換名 ＋ 検知器名」・係数は `_transform`
  4. 先読み（14-11 規約 3 と同型）: 評価分割を差し替えても `_transform` の式が変わらない（検知器の経路で）
  5. leak 対照: `LEAK_` の列は変換の入力に入る → 上乗せが跳ねる
  6. 名前の衝突: 変換の列名が表の列と重なれば止まる
⚠ **成績のテストは書かない**（結果は落ちてよい。14-10 規約 4）。
"""

from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
import pytest

from ail import registry, runs
from ail.validation import checks, prep
from cli.run import evaluate_trading, fold_buy_pct
from tests import _fingerprint as fp
from tests import test_fwd as tf
import ail.bootstrap  # noqa: F401

GA = "F4-1 記号回帰（遺伝的プログラミング）"
MOCK_TF = "テスト用 変換（検知器の口）"
MOCK_DET = "テスト用 検知器（受け取った列を覚える）"
SEEN: list[dict] = []


@registry.register("transform", MOCK_TF)
def _mock_transform(Xtr, Xte, ctx):
    """決まった式 2 本（学ばない）。⚠ 列名は `ctx["tt_names"]` で差し替えられる（衝突のテスト用）。"""
    names = ctx.get("tt_names", ["tt_00", "tt_01"])
    c = list(Xtr.columns)

    def conv(X):
        return pd.DataFrame({names[0]: X[c[0]] * 2.0, names[1]: X[c[0]] + X[c[1]]}, index=X.index)
    return conv(Xtr), conv(Xte), {"式": [f"{c[0]}*2", f"{c[0]}+{c[1]}"], "列": names}


@registry.register("detector", MOCK_DET)
def _mock_detector(tr, te, feats, ctx):
    SEEN.append({"feats": list(feats), "tr": list(tr.columns), "te": list(te.columns), "rows": len(te)})
    return np.full(len(te), 60.0), {"columns": list(feats)}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runs, "RUNS", str(tmp_path))
    return runs.Run("test_transform_detector", {}, seed=0)


def _ctx(**kw):
    return {**tf._ctx(), **kw}


def _args(p, exp, tr, te, ctx):
    """`fold_buy_pct` を 1 fold ぶん直接呼ぶときの引数。"""
    te = te.reset_index(drop=True)
    return dict(tr=tr, te=te, feats=tf._feats(p), exp=exp, ctx=ctx, model=ctx["model"], selectors={},
                detectors=registry.resolve_all("detector", exp["detectors"]), baselines={},
                form="shared", k=8, groups=te.groupby("symbol").indices, f=1, picked=[], log=lambda *_: None)


# --- 1・2. 指紋（transform が無ければ 1 ビットも変わらない） ------------------

def test_fwd_fingerprint_is_unchanged_without_transform(run):
    """⚠ **この経路は 13500t の毎日の予測も通る。** 口を足す前のコード（2026-10-09・commit 00e4958）で取った指紋と比べる。"""
    want = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "fwd_fingerprint.json"), encoding="utf-8"))
    p = tf._panel()
    exp = tf._exp()
    res, per_sym, summary, daily, extra = evaluate_trading(p, tf._feats(p), exp, run)
    doc = checks.compute_trading(res, summary, per_sym, daily, exp, n_trials=70, extra=extra)
    ch = {th: {k: e.get(k) for k in fp.CHECK_KEYS if k in e} for th, e in doc["by_threshold"].items()}
    got = {"per_symbol": fp._digest(fp._frame_text(per_sym, fp.PER_SYMBOL_COLUMNS)),
           "result": fp._digest(fp._frame_text(res, fp.RESULT_COLUMNS)),
           "summary": fp._digest(fp._frame_text(summary.reset_index(), ["手法"] + fp.SUMMARY_COLUMNS)),
           "checks": fp._digest(json.dumps(ch, ensure_ascii=False, sort_keys=True))}
    assert got == {k: want[k] for k in got}, (got, want["sample"])


def test_without_transform_augment_returns_the_same_objects_and_names():
    p = tf._panel()
    tr, te = tf._split(p)
    feats = tf._feats(p)
    exp = {**tf._exp(), "detectors": [MOCK_DET]}
    a, b, c, d = prep.augment(exp, tr, te, feats, _ctx())
    assert a is tr and b is te and c is feats and d is None
    SEEN.clear()
    buy, exits, n_cols, fitted = fold_buy_pct(**_args(p, exp, tr, te, _ctx()))
    assert list(buy) == [MOCK_DET] and "_transform" not in fitted
    assert SEEN[-1]["feats"] == feats                        # ⚠ 検知器が受け取る列も変わらない


# --- 3. 口が開く ------------------------------------------------------------

def test_the_port_adds_the_transformed_columns_and_keeps_the_originals():
    p = tf._panel()
    tr, te = tf._split(p)
    feats = tf._feats(p)
    exp = {**tf._exp(), "transform": MOCK_TF, "detectors": [MOCK_DET]}
    SEEN.clear()
    buy, exits, n_cols, fitted = fold_buy_pct(**_args(p, exp, tr, te, _ctx()))
    seen = SEEN[-1]
    assert seen["feats"] == feats + ["tt_00", "tt_01"]             # 足す（置き換えない）
    assert set(feats) <= set(seen["tr"]) and set(feats) <= set(seen["te"])
    assert {"tt_00", "tt_01"} <= set(seen["tr"]) and {"tt_00", "tt_01"} <= set(seen["te"])
    assert seen["rows"] == len(te)
    assert list(buy) == [f"{MOCK_TF} ＋ {MOCK_DET}"]                # 手法名 ＝ 変換名 ＋ 検知器名
    assert fitted["_transform"]["列"] == ["tt_00", "tt_01"]       # 係数は _transform
    assert n_cols[f"{MOCK_TF} ＋ {MOCK_DET}"] == float(len(feats) + 2)
    # ⚠ 元の表は書き換えない（呼び元の tr・te に列が増えない）
    assert "tt_00" not in tr.columns and "tt_00" not in te.columns


def test_fwd_reads_the_added_columns_as_explanatory_variables():
    """本物の検知器 `fwd` は `feats` の全列を説明変数にする ＝ 足した列が `columns` に出る。"""
    p = tf._panel()
    tr, te = tf._split(p)
    exp = {**tf._exp(), "transform": MOCK_TF}
    buy, exits, n_cols, fitted = fold_buy_pct(**_args(p, exp, tr, te, _ctx()))
    lab = f"{MOCK_TF} ＋ {tf.NAME}"
    assert list(buy) == [lab] and len(buy[lab]) == len(te)
    assert fitted[lab]["columns"] == tf._feats(p) + ["tt_00", "tt_01"]


# --- 4. 先読み（評価分割を差し替えても式が変わらない） ---------------------------

def test_evaluation_split_does_not_change_the_transform():
    p = tf._panel()
    tr, te = tf._split(p)
    exp = {**tf._exp(), "transform": GA}
    ctx = _ctx(generations=3, pop=20, k=4)                     # ⚠ GA を小さく（答えの性質は同じ）
    _, _, _, f1 = fold_buy_pct(**_args(p, exp, tr, te, ctx))
    te2 = te.copy()
    for c in tf._feats(p):
        te2[c] = te2[c] * 7.5 + 3.0                            # ⚠ まったく別の値
    _, _, _, f2 = fold_buy_pct(**_args(p, exp, tr, te2, ctx))
    assert f1["_transform"]["式"] == f2["_transform"]["式"]
    assert f1["_transform"]["適合度"] == f2["_transform"]["適合度"]


# --- 5. leak 対照 --------------------------------------------------------------

def test_leak_makes_the_edge_jump_through_the_port(run):
    p = tf._panel(leak=True)
    exp = {**tf._exp(), "transform": GA, "model_args": {"generations": 3, "pop": 20, "k": 4}}
    res, per_sym, summary, daily, extra = evaluate_trading(p, tf._feats(p), exp, run)
    doc = checks.compute_trading(res, summary, per_sym, daily, exp, n_trials=70, leak=True, extra=extra)
    assert set(summary.index.get_level_values(0)) >= {f"{GA} ＋ {tf.NAME}"}
    for th, e in doc["by_threshold"].items():
        assert e["edge_vs_bh"]["mean_bp"] > 50.0, th
        assert e["edge_vs_bh"]["positive"] == e["edge_vs_bh"]["folds"], th
    # ⚠ 答えの列は変換の入力にも入っている（式の材料に出る）。同じ表の 1 分割で係数を直接見る
    tr, te = tf._split(p)
    _, _, _, fitted = fold_buy_pct(**_args(p, exp, tr, te, _ctx(generations=3, pop=20, k=4)))
    assert any("LEAK_" in s for s in fitted["_transform"]["式"])


# --- 5b. ⚠ 学習の対象（y_fwd_）は変換の入力に入らない（2026-10-09 に踏んだ穴） ----------

def test_the_label_never_reaches_the_transform():
    """⚠ **本物の表の `feats` は META_COLUMNS だけを外す**（`cli/run.py`）ので `y_fwd_10` が残っている。検知器は自分で
    接頭辞を外すが、変換に渡す前に外さないと式に答えが混ざる（1 回目の実行 `2026-10-09T11-27-40` で踏んだ）。"""
    from ail.contracts import META_COLUMNS
    p = tf._panel()
    feats = [c for c in p.columns if c not in META_COLUMNS]        # ⚠ cli/run.py と同じ作り方
    assert f"{tf.labels.SCALE_PREFIX}{tf.W}" in feats
    tr, te = tf._split(p)
    exp = {**tf._exp(), "transform": GA}
    args = _args(p, exp, tr, te, _ctx(generations=3, pop=20, k=4))
    args["feats"] = feats
    _, _, _, fitted = fold_buy_pct(**args)
    assert not any(tf.labels.SCALE_PREFIX in s for s in fitted["_transform"]["式"])
    lab = f"{GA} ＋ {tf.NAME}"
    assert not any(c.startswith(tf.labels.SCALE_PREFIX) for c in fitted[lab]["columns"])   # 検知器の側は今までどおり
    assert any(c.startswith("gp_") for c in fitted[lab]["columns"])


# --- 6. 名前の衝突 ------------------------------------------------------------

def test_column_name_clash_stops():
    p = tf._panel()
    tr, te = tf._split(p)
    exp = {**tf._exp(), "transform": MOCK_TF, "detectors": [MOCK_DET]}
    with pytest.raises(SystemExit, match="重なる"):
        fold_buy_pct(**_args(p, exp, tr, te, _ctx(tt_names=["own_ret_1", "tt_01"])))
