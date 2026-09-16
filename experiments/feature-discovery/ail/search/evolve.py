"""F4-1 記号回帰（遺伝的プログラミング）。⚠ **選抜は訓練分割の内側だけで行う。**

⚠ **世代を回すループは全部「訓練分割の内側」に入っている**（プラン `plans/archive/evolutionary-search-runner.md` §2）。
⚠ **検証分割 `Xte` に触れるのは、決まった式を当てるときだけ**である。だから

  - ⚠ **`n_trials` に数えるのは champion × θ の 3 試行**で、⚠ **世代 × 個体は数えない**
    （rules.md 14-5 の門と同型。訓練内 holdout しか見ていないものは偶然の最大値を作る母集団に入らない）
  - ⚠ **既存 589 試行と同じ土俵で読める**

⚠ **構造は事前固定**（14-9・プラン §3-2。⚠ **回す前に書いた。結果を見て動かさない**）。
⚠ **式の中の数値（定数）は固定しない** — 訓練の内側で決まる。それが 14-9 の要件そのものである。

⚠ **時間方向の演算子（lag・rolling）は入れない。** ⚠ **先読みが入りやすく**（7 章）、
既存の列が既に窓を持っている（`own_` は 5/20/60 営業日の量）。
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from ail.models.holdout import tail_holdout
from ail.registry import register

# ⚠ **事前固定**（プラン §3-2）。⚠ **config で上書きできるが、上書きしたら n_trials の数え直しが要る**
DEFAULTS: dict = {
    "pop": 100,              # 個体数
    "generations": 20,       # ⚠ 打ち切り 1（主）
    "seconds": 600.0,        # ⚠ 打ち切り 2（保険。1 fold あたり）
    "patience": 5,           # ⚠ 打ち切り 3（最良適合度がこの世代数改善しなければ止める）
    "tournament": 3,
    "elite": 2,
    "p_cross": 0.7,
    "p_mut": 0.2,
    "max_depth": 6,
    "max_nodes": 30,
    "corr_max": 0.9,         # ⚠ champion を選ぶときの間引き
    "k": 16,                 # ⚠ 出す列の数（選別の k と同じ。比べる相手と列数を揃える）
    "holdout_frac": 0.1,     # ⚠ 門・較正と同じ場所
}

OPS2 = ("add", "sub", "mul", "div")
OPS1 = ("log", "sqrt", "neg", "abs")
_SYM = {"add": "＋", "sub": "−", "mul": "×", "div": "÷"}
_EPS = 1e-6


# --- 式（木）------------------------------------------------------------

def _eval(node, X: np.ndarray) -> np.ndarray:
    """式を表に当てる。⚠ **`div` と `log` は保護する**（0 割りで NaN を撒かない）。"""
    t = node[0]
    if t == "x":
        return X[:, node[1]]
    if t == "c":
        return np.full(X.shape[0], float(node[1]))
    if t in OPS1:
        a = _eval(node[1], X)
        if t == "log":
            return np.log(np.abs(a) + 1e-9)
        if t == "sqrt":
            return np.sqrt(np.abs(a))
        return -a if t == "neg" else np.abs(a)
    a, b = _eval(node[1], X), _eval(node[2], X)
    if t == "add":
        return a + b
    if t == "sub":
        return a - b
    if t == "mul":
        return a * b
    safe = np.where(np.abs(b) < _EPS, 1.0, b)          # ⚠ 保護つき除算
    return np.where(np.abs(b) < _EPS, 1.0, a / safe)


def _text(node, names: list[str]) -> str:
    t = node[0]
    if t == "x":
        return names[node[1]]
    if t == "c":
        return f"{node[1]:.3f}"
    if t in OPS1:
        return f"{t}({_text(node[1], names)})"
    return f"({_text(node[1], names)} {_SYM[t]} {_text(node[2], names)})"


def _size(node) -> int:
    if node[0] in ("x", "c"):
        return 1
    return 1 + sum(_size(c) for c in node[1:])


def _depth(node) -> int:
    if node[0] in ("x", "c"):
        return 1
    return 1 + max(_depth(c) for c in node[1:])


def _nodes(node, path=()):
    """(道筋, 節) を全部返す。交叉・突然変異で部分木を差し替えるため。"""
    out = [(path, node)]
    if node[0] not in ("x", "c"):
        for i, c in enumerate(node[1:], start=1):
            out += _nodes(c, path + (i,))
    return out


def _replace(node, path, sub):
    if not path:
        return sub
    i = path[0]
    kids = list(node[1:])
    kids[i - 1] = _replace(kids[i - 1], path[1:], sub)
    return (node[0], *kids)


def _random_tree(rng, n_cols: int, depth: int, full: bool):
    """ramped half-and-half。⚠ **深さの上限は事前固定**（膨張を止める）。"""
    if depth <= 1 or (not full and rng.random() < 0.3):
        if rng.random() < 0.15:
            return ("c", float(rng.uniform(-1.0, 1.0)))     # ⚠ 定数は訓練の内側で決まる
        return ("x", int(rng.integers(n_cols)))
    if rng.random() < 0.3:
        return (OPS1[int(rng.integers(len(OPS1)))], _random_tree(rng, n_cols, depth - 1, full))
    op = OPS2[int(rng.integers(len(OPS2)))]
    return (op, _random_tree(rng, n_cols, depth - 1, full),
            _random_tree(rng, n_cols, depth - 1, full))


# --- 適合度（⚠ 訓練分割の内側だけで測る）--------------------------------

def _ranks(a: np.ndarray) -> np.ndarray:
    order = np.argsort(a, kind="mergesort")
    r = np.empty(len(a), dtype=float)
    r[order] = np.arange(len(a), dtype=float)
    return r


def fitness(vals: np.ndarray, y_rank: np.ndarray) -> float:
    """その式 1 列と y の **Spearman 順位相関の絶対値**（符号は後段の Ridge が学ぶ）。

    ⚠ **使えない式は 0**（定数・NaN 過多）。⚠ **0 で埋めるのはここだけ** — 「測れない」ではなく
    ⚠ **「使えない」**の意味である。
    """
    finite = np.isfinite(vals)
    if finite.mean() < 0.5:
        return 0.0
    v = np.where(finite, vals, 0.0)
    if float(np.std(v)) < 1e-12:
        return 0.0
    c = np.corrcoef(_ranks(v), y_rank)[0, 1]
    return 0.0 if not np.isfinite(c) else float(abs(c))


def search(X: np.ndarray, y: np.ndarray, names: list[str], params: dict, seed: int,
           clock=time.time) -> dict:
    """GA を 1 本回す。⚠ **渡すのは訓練分割の内側の表だけ。**

    戻り値は `{"式": [...], "適合度": [...], "世代": g, "止めた理由": ..., "秒": ...}`。
    """
    rng = np.random.default_rng(seed)
    p, n_cols = params, X.shape[1]
    y_rank = _ranks(np.asarray(y, dtype=float))
    started = clock()

    pop = [_random_tree(rng, n_cols, int(rng.integers(2, p["max_depth"] + 1)), i % 2 == 0)
           for i in range(p["pop"])]
    fits = [fitness(_eval(t, X), y_rank) for t in pop]
    best, since, gen, why = max(fits), 0, 0, "世代の上限"

    for gen in range(1, int(p["generations"]) + 1):
        if clock() - started > float(p["seconds"]):
            why = "時間の上限"                                  # ⚠ 打ち切り 2
            break
        order = np.argsort(fits)[::-1]
        nxt = [pop[i] for i in order[:int(p["elite"])]]         # ⚠ エリートは無条件で残す
        while len(nxt) < p["pop"]:
            a = _tournament(pop, fits, rng, int(p["tournament"]))
            if rng.random() < p["p_cross"]:
                b = _tournament(pop, fits, rng, int(p["tournament"]))
                child = _cross(a, b, rng, p)
            elif rng.random() < p["p_mut"] / max(1e-9, 1.0 - p["p_cross"]):
                child = _mutate(a, rng, n_cols, p)
            else:
                child = a
            nxt.append(child)
        pop = nxt
        fits = [fitness(_eval(t, X), y_rank) for t in pop]
        if max(fits) > best + 1e-9:
            best, since = max(fits), 0
        else:
            since += 1
            if since >= int(p["patience"]):
                why = "改善が止まった"                          # ⚠ 打ち切り 3
                break

    champs = _champions(pop, fits, X, p)
    return {"式": [_text(t, names) for t, _ in champs],
            "適合度": [round(f, 4) for _, f in champs],
            "木": [t for t, _ in champs],
            "世代": gen, "止めた理由": why, "秒": round(clock() - started, 1),
            "個体数": int(p["pop"]), "holdout 行数": int(X.shape[0])}


def _tournament(pop, fits, rng, size: int):
    idx = rng.integers(len(pop), size=size)
    return pop[max(idx, key=lambda i: fits[i])]


def _cross(a, b, rng, p):
    """部分木の交換。⚠ **上限を超えたら親をそのまま返す**（膨張を止める）。"""
    pa = _nodes(a)[int(rng.integers(len(_nodes(a))))][0]
    sub = _nodes(b)[int(rng.integers(len(_nodes(b))))][1]
    child = _replace(a, pa, sub)
    return child if _depth(child) <= p["max_depth"] and _size(child) <= p["max_nodes"] else a


def _mutate(a, rng, n_cols: int, p):
    pa = _nodes(a)[int(rng.integers(len(_nodes(a))))][0]
    sub = _random_tree(rng, n_cols, 2, False)
    child = _replace(a, pa, sub)
    return child if _depth(child) <= p["max_depth"] and _size(child) <= p["max_nodes"] else a


def _champions(pop, fits, X: np.ndarray, p) -> list[tuple]:
    """⚠ **上位から、既に選んだ式と `|ρ| > corr_max` のものを落としながら k 本**。

    ⚠ **間引かないと、同じ情報の式が 16 本並ぶ**（GA は良い式のまわりに集まるので必ず起きる）。
    """
    out: list[tuple] = []
    cols: list[np.ndarray] = []
    for i in np.argsort(fits)[::-1]:
        if fits[i] <= 0.0:
            continue
        v = _eval(pop[i], X)
        v = np.where(np.isfinite(v), v, 0.0)
        if float(np.std(v)) < 1e-12:
            continue
        if any(abs(np.corrcoef(v, c)[0, 1]) > p["corr_max"]
               for c in cols if float(np.std(c)) > 1e-12):
            continue
        out.append((pop[i], float(fits[i])))
        cols.append(v)
        if len(out) >= int(p["k"]):
            break
    return out


# --- 変換の口（registry）------------------------------------------------

@register("transform", "F4-1 記号回帰（遺伝的プログラミング）")
def tf_symbolic(Xtr: pd.DataFrame, Xte: pd.DataFrame, ctx: dict):
    """⚠ **訓練分割の内側で式を進化させ、champion k 本を列にして返す。**

    ⚠ **`Xte` は探索に 1 度も渡らない**（決まった式を当てるだけ）。
    ⚠ **これが守れているかはテストで固定してある**（`tests/test_evolve.py` の
    「検証分割を替えても選ばれる式が変わらない」）。
    """
    y = ctx.get("ytr")
    if y is None:
        raise SystemExit("⚠ F4-1: ctx に `ytr` が無い。"
                         "`prep.apply(..., ytr=ytr)` で渡す（教師つきの変換だから要る）")
    p = {**DEFAULTS, **{k: ctx[k] for k in DEFAULTS if k in ctx}}
    names = list(Xtr.columns)
    y = np.asarray(y, dtype=float)

    # ⚠ **測る場所は訓練分割の尻**（門・較正と同じ。13-2 の 2）。⚠ **切れなければ訓練全体で測る**
    (_Xh, _yh), hold = tail_holdout(Xtr, y, frac=float(p["holdout_frac"]))
    if hold is None:
        Xs, ys, where = Xtr.to_numpy(dtype=float), y, "切れなかった（訓練全体で測った）"
    else:
        Xs, ys, where = hold[0].to_numpy(dtype=float), hold[1], "訓練分割の尻"

    doc = search(Xs, ys, names, p, int(ctx.get("seed", 0)))
    trees = doc.pop("木")
    if not trees:
        raise SystemExit("⚠ F4-1: 使える式が 1 本も残らなかった（適合度が全部 0）")

    def conv(X: pd.DataFrame, stats=None):
        raw = np.column_stack([_eval(t, X.to_numpy(dtype=float)) for t in trees])
        raw = np.where(np.isfinite(raw), raw, 0.0)
        if stats is None:                                   # ⚠ 標準化も訓練分割だけで fit（3 章 B）
            mu, sd = raw.mean(axis=0), raw.std(axis=0)
            sd = np.where(sd < 1e-12, 1.0, sd)
            stats = (mu, sd)
        z = (raw - stats[0]) / stats[1]
        cols = [f"gp_{i:02d}" for i in range(z.shape[1])]
        return pd.DataFrame(z, columns=cols, index=X.index), stats

    tr, stats = conv(Xtr)
    te, _ = conv(Xte, stats)
    doc["列"] = list(tr.columns)
    doc["holdout"] = where
    return tr, te, doc
