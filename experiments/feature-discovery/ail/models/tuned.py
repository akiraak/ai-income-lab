"""ハイパーパラメータを**訓練分割の内側**で選ぶモデル（rules.md 14-12。2026-10-09 利用者決定 B）。

`<モデル>（内側選抜）` ＝ 事前固定した候補（`CANDIDATES`。既定が先頭）を訓練分割の尻 10%（`tail_holdout`。
門・較正・GA と同じ置き場）で Spearman ρ で測り、champion を訓練全体で学び直して評価分割を当てる。

⚠ **評価分割 `Xte` には champion が決まった後にしか触れない**（14-11 規約 3 と同型の検査 ＝ `tests/test_tuned.py`）。
⚠ **champion が既定なら、基底のモデルと 1 ビットも違わない**（14-12 規約 4）。
⚠ **モデルが呼ばれるたびにその訓練の尻で選ぶ**（門・較正・本番で champion が違ってよい。規約 5）。
⚠ **候補は結果を見て足さない・動かさない**（規約 1。写しを `tests/test_tuned.py` が固定する）。
⚠ **config にこのモデル名が無ければ 1 ビットも変えない**（基底の `trees.py`・`deep.py` は ctx に項目が無ければ今までの定数）。

記録: 1 回の呼び出しごとに候補・ρ・champion・尻の行数を `ctx[DOC_KEY]` のリストに積む。
`cli/run.py` の `fold_buy_pct` が本番の呼び出しの後に `drain` で取り出して `fitted_doc[手法]["内側選抜"]` に写し、
門（`ail/validation/gate.py`）は取り出して捨てる（門は診断）。⚠ 取り残すと fold をまたいで混ざるので、
モデルを呼んだ側が必ず `drain` する。
"""

from __future__ import annotations

import itertools

import numpy as np

from ail.models import deep, linear, trees
from ail.models.holdout import tail_holdout
from ail.registry import register

# ctx に積む記録の項目名。⚠ **モデルを呼んだ側が `drain` で空にする**
DOC_KEY = "__tuned_docs"


def _front(default: dict, grid: list[dict]) -> list[dict]:
    """既定を先頭に、残りは格子の順（⚠ 順序は固定。同点は先頭 ＝ 既定が選ばれる）。"""
    return [default] + [c for c in grid if c != default]


# ⚠ **水準は rules.md 14-12-1 の写し**（回す前に固定。2026-10-09）。既定は基底のモデルの定数と同じ値
RIDGE_DEFAULT = {"alpha": 1.0}
RIDGE = _front(RIDGE_DEFAULT, [{"alpha": a} for a in (1.0, 10.0, 100.0, 1e3, 1e4, 1e5, 1e6)])

LGBM_DEFAULT = {"lgbm_leaves": 31, "lgbm_lr": 0.05, "lgbm_min_child": 100}
LGBM = _front(LGBM_DEFAULT, [{"lgbm_leaves": nl, "lgbm_lr": lr, "lgbm_min_child": mc}
                             for nl, lr, mc in itertools.product((7, 31, 127), (0.02, 0.05, 0.1), (20, 100, 500))])

MLP_DEFAULT = {"mlp_hidden": (64, 32), "mlp_dropout": 0.2, "mlp_lr": 1e-3}
MLP = _front(MLP_DEFAULT, [{"mlp_hidden": h, "mlp_dropout": d, "mlp_lr": lr}
                           for h, d, lr in itertools.product(((64,), (32, 16), (64, 32), (128, 64), (128, 64, 32)),
                                                             (0.0, 0.2, 0.5), (3e-4, 1e-3, 3e-3))])

CANDIDATES: dict[str, list[dict]] = {"Ridge": RIDGE, "LightGBM": LGBM, "MLP": MLP}


def spearman(pred, y) -> float | None:
    """尻での予測と y の Spearman ρ（符号つき）。NaN・定数の予測は None（＝ 最低点）。"""
    from scipy.stats import rankdata

    p = np.asarray(pred, dtype=float)
    t = np.asarray(y, dtype=float)
    if len(p) < 3 or not np.all(np.isfinite(p)) or np.ptp(p) == 0 or np.ptp(t) == 0:
        return None
    c = float(np.corrcoef(rankdata(p), rankdata(t))[0, 1])
    return c if np.isfinite(c) else None


def _plain(c: dict) -> dict:
    """記録用（JSON に落ちる形）。tuple は list に。"""
    return {k: (list(v) if isinstance(v, tuple) else v) for k, v in c.items()}


def inner_select(base, Xtr, ytr, Xte, ctx: dict, candidates: list[dict]):
    """候補を訓練分割の尻で測って champion を選び、訓練全体で学び直して `Xte` を当てる。

    ⚠ **`Xte` に触るのは最後の 1 行だけ**（評価分割を差し替えても champion も ρ も変わらない）。
    ⚠ **候補の学びは `base(頭, y頭, 尻, …)`** ＝ 基底のモデルの早期打ち切りは頭の中でさらに尻を切る（二重にならない）。
    学び直しの早期打ち切りは選抜と同じ尻を使う（14-12 規約 9。評価分割には漏れない）。
    """
    ytr = np.asarray(ytr, dtype=float)
    default = candidates[0]
    doc: dict = {"候補": [_plain(c) for c in candidates], "既定": _plain(default)}
    (Xh, yh), holdout = tail_holdout(Xtr, ytr)
    if holdout is None:
        # ⚠ 尻が切れない（小さい標本）。既定で動き、記録に残す（14-12 規約 2）
        doc.update({"切れなかった": True, "尻の行数": 0, "ρ": [None] * len(candidates),
                    "champion": _plain(default), "champion_index": 0, "既定のρ": None})
        champion = default
    else:
        Xv, yv = holdout
        rhos = [spearman(base(Xh, yh, Xv, {**ctx, **c}), yv) for c in candidates]
        scores = np.array([-np.inf if r is None else r for r in rhos], dtype=float)
        best = int(np.argmax(scores))             # ⚠ 同点は先頭（既定）。argmax は最初の最大を返す
        champion = candidates[best]
        doc.update({"切れなかった": False, "尻の行数": int(len(yv)),
                    "ρ": [None if r is None else round(r, 6) for r in rhos],
                    "champion": _plain(champion), "champion_index": best, "既定のρ": doc_rho(rhos[0])})
    pred = base(Xtr, ytr, Xte, {**ctx, **champion})
    ctx.setdefault(DOC_KEY, []).append(doc)
    return pred


def doc_rho(r: float | None) -> float | None:
    return None if r is None else round(r, 6)


def drain(ctx: dict) -> list[dict]:
    """積んだ記録を取り出して空にする。⚠ **内側選抜のモデルを使っていなければ空のリスト**（ctx は 1 バイトも変わらない）。"""
    return list(ctx.pop(DOC_KEY, []))


def fold_doc(docs: list[dict]) -> dict:
    """`fold_buy_pct` の 1 手法ぶん（較正 → 本番の順に 2 回呼ばれる）を `fitted_doc` の形に。"""
    if len(docs) == 2:
        return {"較正": docs[0], "本番": docs[1]}
    return {"呼び出し": docs}


@register("model", "Ridge（内側選抜）")
def ridge_tuned(Xtr, ytr, Xte, ctx):
    return inner_select(linear.ridge, Xtr, ytr, Xte, ctx, RIDGE)


@register("model", "LightGBM（内側選抜）")
def lightgbm_tuned(Xtr, ytr, Xte, ctx):
    return inner_select(trees.lightgbm_gbdt, Xtr, ytr, Xte, ctx, LGBM)


@register("model", "MLP（内側選抜）")
def mlp_tuned(Xtr, ytr, Xte, ctx):
    return inner_select(deep.mlp, Xtr, ytr, Xte, ctx, MLP)
