"""「目的の系列 ＋ 外生系列」の深層学習 2 本を検知器として回す（[記録](../../../../docs/specs/experiments/exog-deep-models.md) §0）。

⚠ **入力の窓は時系列分類器・PatchTST と同じ**（過去 60 営業日の累積の道筋。`tsc.window_paths`）。
⚠ **違うのは「ほかの系列も見る」こと**: 同じ日の **ほかの銘柄の道筋**（表の同じ日付の行から組む）と、
**金利・為替の水準の道筋**（`raw/treasury`・`raw/ecb` の系列を 1 日ずらして as-of で貼る ＝ `ex` 層と同じ規約）。

    目的の道筋 60 点 ＋ 共変量（他銘柄 ＋ 金利・為替）× 60 点 → モデル → 先 10 日の値 → Platt 較正 → 買い%

| 検知器 | モデル（config の `model`） | 共変量 |
| --- | --- | --- |
| S2 Chronos-2（60日窓・先10日・共変量あり） | `Chronos-2`（zero-shot） | 他 62 銘柄の相対価格 ＋ 金利 7 ＋ 為替 7 の水準 |
| S3 Chronos-2（60日窓・先10日・共変量なし） | `Chronos-2` | ⚠ 無し（共変量の効きと、学習コーパスの先読みを切り分ける対照） |
| S4 TimeXer（60日窓・先10日・外生あり） | `TimeXer`（教師あり） | 全 63 銘柄の道筋（自分の枠は 0）＋ 金利 7 ＋ 為替 7 の水準の道筋 |

⚠ **学習の対象は `y_fwd_10`**（先 10 営業日の累積対数リターン。rules.md 15-3）。損益の対象は 1 日の `y` のまま。
⚠ **較正は訓練の日付の尻を 10 営業日ぶん（暦 15 日）パージして Platt**（15-5。`scale._fit_calibration` と同じ）。
⚠ **fit は訓練分割の内側だけ**（3 章 B）。⚠ **評価期間の行は transform にしか使わない。**

⚠ **共変量の履歴は「表の同じ日付の行」と「外部系列の as-of」から組む**ので、⚠ **未来は構造的に入らない**
（他銘柄の道筋は足 t までのリターン、外部系列は t − 1 日までに公表された値）。
⚠ **表の行が無い銘柄・日（上場前など）は道筋 0（値動き無し）で埋める**。外部系列の始まりより前も最初の値で埋める（平ら）。
⚠ **暦（取引日）は SPY の日足から取る**（外部系列を 60 取引日に並べるため。パージで抜けた日付を表から取れないので）。

⚠ **`LEAK_` の列は窓の後ろに並べてモデルに渡す**（rules.md 13-10。PatchTST と同じ配線）。
"""

from __future__ import annotations

import functools

import numpy as np
import pandas as pd

from ail.data import store
from ail.detectors import tsc
from ail.detectors.scale import _fit_calibration
from ail.features import exog, labels, seq
from ail.models import calibrate, chronos2, timexer
from ail.models.holdout import date_holdout
from ail.registry import register

HORIZON = 10                                   # ⚠ 先 10 営業日（K5。`fwd.WINDOW` と同じ値。回す前に固定）
# ⚠ **外部系列は金利と為替だけ**（記録 §0-2。気象・地震・災害は入れない）
EXT_SOURCES = ("treasury", "ecb")
EXT_LAG_DAYS = 1                               # ⚠ `ex` 層と同じ 1 日（発表の遅れ）
CALENDAR_SYMBOL = "SPY"                        # 取引日の暦の出どころ
NAME_S2 = "S2 Chronos-2（60日窓・先10日・共変量あり）"
NAME_S3 = "S3 Chronos-2（60日窓・先10日・共変量なし）"
NAME_S4 = "S4 TimeXer（60日窓・先10日・外生あり）"


# --- 暦と外部系列（プロセスの中で 1 度だけ読む。テストは差し替える） ------------------------------

@functools.lru_cache(maxsize=None)
def calendar() -> np.ndarray:
    """取引日（`datetime64[D]`・昇順）。SPY の日足から。"""
    bars = store.read_bars(store.adjusted_dir("d"), CALENDAR_SYMBOL)
    ts = pd.to_datetime(bars["time_ms"], unit="ms", utc=True).dt.tz_localize(None).dt.normalize()
    return np.array(sorted(set(ts.to_numpy().astype("datetime64[D]"))), dtype="datetime64[D]")


@functools.lru_cache(maxsize=None)
def external() -> tuple[list[str], np.ndarray]:
    """外部系列（金利 7 ＋ 為替 7）を暦の各日に as-of（1 日ずらし・前の値を引き継ぐ）で貼った (名前, 日 × 系列)。

    ⚠ **系列の始まりより前は最初の値で埋める**（treasury は 2018-01-02 から。2018 年の頭の窓は平らになる ＝ 記録に書く）。
    """
    series, _owner = exog.load_series(EXT_SOURCES, zero_fill=())
    names = sorted(series)
    days = pd.DatetimeIndex(calendar())
    asof = days - pd.Timedelta(days=EXT_LAG_DAYS)
    cols = []
    for nm in names:
        s = series[nm]
        union = s.index.union(asof)
        v = s.reindex(union).ffill().reindex(asof).bfill()
        cols.append(v.to_numpy(dtype=np.float32))
    return names, np.stack(cols, axis=1)


def _day_index(ts) -> np.ndarray:
    """表の日付 → 暦の位置。⚠ **暦に無い日付は止める**（表と暦が同じ足から来ているはず）。"""
    d = pd.to_datetime(pd.Series(np.asarray(ts))).dt.tz_localize(None).dt.normalize().to_numpy().astype("datetime64[D]")
    cal = calendar()
    k = np.searchsorted(cal, d)
    bad = (k >= len(cal)) | (cal[np.minimum(k, len(cal) - 1)] != d)
    if bad.any():
        raise SystemExit(f"⚠ 表の日付が暦（{CALENDAR_SYMBOL} の日足）に無い: {d[bad][:3]}")
    return k


def external_windows(ts, window: int = seq.WINDOW, levels: bool = False) -> tuple[list[str], np.ndarray]:
    """行ごとの外部系列の窓 (行, 系列, 窓)。`levels=False` なら道筋（最後の点 0）、True なら水準そのまま。"""
    names, M = external()
    k = _day_index(ts)
    if (k < window - 1).any():
        raise SystemExit("⚠ 暦の頭より前の窓を求めた（外部系列の窓が組めない）")
    idx = k[:, None] - np.arange(window - 1, -1, -1)[None, :]         # 古い → 新しい
    w = M[idx]                                                        # (行, 窓, 系列)
    w = np.transpose(w, (0, 2, 1))                                    # (行, 系列, 窓)
    if not levels:
        w = w - w[:, :, -1:]
    return names, np.ascontiguousarray(w, dtype=np.float32)


# --- 他銘柄の道筋 -------------------------------------------------------------------------------

def symbol_slots(*frames) -> list[str]:
    """枠の並び（銘柄名の昇順）。⚠ **訓練と検証で同じ並びにする**ため、両方の銘柄を合わせて決める。"""
    return sorted(set().union(*[set(f["symbol"].unique()) for f in frames]))


def symbol_panel(df: pd.DataFrame, feats, slots: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(行の日付の位置, 行の枠, 日付 × 枠 × 窓 の道筋)。行が無い枠は 0。"""
    paths = tsc.window_paths(df, feats)[:, 0, :]
    day_codes, days = pd.factorize(pd.to_datetime(df["ts"]), sort=True)
    slot_of = {s: i for i, s in enumerate(slots)}
    slot = df["symbol"].map(slot_of).to_numpy()
    if pd.isna(slot).any():
        raise SystemExit("⚠ 枠に無い銘柄がある")
    slot = slot.astype(int)
    P = np.zeros((len(days), len(slots), paths.shape[1]), dtype=np.float32)
    P[day_codes, slot] = paths
    return day_codes, slot, P


def build_inputs(df: pd.DataFrame, feats, leak: list[str], slots: list[str], kind: str) -> tuple[np.ndarray, list[str]]:
    """1 行 ＝ [目的 60 ｜ 共変量 K × 60 ｜ LEAK]（float32）と共変量の名前。

    kind: "timexer" ＝ 全枠の道筋（自分の枠は 0）＋ 外部系列の道筋 ／ "chronos" ＝ 他枠の相対価格 ＋ 外部系列の水準 ／
          "chronos_nocov" ＝ 目的だけ。
    """
    day_codes, slot, P = symbol_panel(df, feats, slots)
    target = P[day_codes, slot]                                       # (行, 窓) ＝ 自分の道筋
    n = len(df)
    parts, names = [], []
    if kind == "timexer":
        cov = P[day_codes].copy()                                     # (行, 枠, 窓)
        cov[np.arange(n), slot] = 0.0                                 # ⚠ 自分は内生の側にある
        parts.append(cov.reshape(n, -1))
        names += [f"sym:{s}" for s in slots]
        ext_names, ext = external_windows(df["ts"])
        parts.append(ext.reshape(n, -1))
        names += [f"ext:{s}" for s in ext_names]
        head = target
    elif kind == "chronos":
        others = np.array([[j for j in range(len(slots)) if j != i] for i in range(len(slots))], dtype=int)
        cov = np.exp(P[day_codes[:, None], others[slot]])             # (行, 枠 − 1, 窓) ＝ 相対価格（最後 1）
        parts.append(cov.reshape(n, -1))
        names += [f"sym:{i}" for i in range(len(slots) - 1)]          # ⚠ 枠の意味は行ごとに違う（自分を除いた順）
        ext_names, ext = external_windows(df["ts"], levels=True)
        parts.append(ext.reshape(n, -1))
        names += [f"ext:{s}" for s in ext_names]
        head = np.exp(target)
    elif kind == "chronos_nocov":
        head = np.exp(target)
    else:
        raise ValueError(kind)
    cols = [head] + parts
    if leak:
        cols.append(df[leak].to_numpy(dtype=np.float32))
    return np.ascontiguousarray(np.hstack(cols), dtype=np.float32), names


# --- 検知器 -------------------------------------------------------------------------------------

def _prepare(tr: pd.DataFrame, te: pd.DataFrame, feats, ctx: dict, kind: str, model_name: str):
    model = ctx["model"]
    if getattr(model, "__ail_name__", None) != model_name:
        raise SystemExit(f"⚠ 検知器（{kind}）は config の `model = \"{model_name}\"` と組にする"
                         f"（いまは {getattr(model, '__ail_name__', model)!r}）")
    target = f"{labels.SCALE_PREFIX}{HORIZON}"
    if target not in tr:
        raise SystemExit(f"⚠ {target} が表に無い。`label_scales = [{HORIZON}]` の表を `features_from` で読む")
    cols = seq.window_columns(feats)
    leak = [c for c in feats if c.startswith("LEAK_")]
    # ⚠ **末尾はラベルが取れない行**（表の尻）。訓練からだけ落とす（検証の行はラベルを使わない）
    tr_ok = tr.loc[tr[target].notna().to_numpy()]
    slots = symbol_slots(tr_ok, te)
    Xtr, names = build_inputs(tr_ok, feats, leak, slots, kind)
    Xte, _ = build_inputs(te, feats, leak, slots, kind)
    ytr = tr_ok[target].to_numpy(dtype=float)
    n_cov = len(names)
    doc = {"input": tsc.INPUT, "window": seq.WINDOW, "target": target, "columns": cols + leak,
           "slots": slots, "covariates": names, "n_cov": n_cov,
           "訓練の行": int(len(tr_ok)), "上がる割合": round(float((ytr > 0).mean()), 4)}
    return model, tr_ok, Xtr, Xte, ytr, leak, n_cov, names, doc


def _chronos(kind: str, name: str, tr, te, feats, ctx):
    model, tr_ok, Xtr, Xte, ytr, leak, n_cov, names, doc = _prepare(tr, te, feats, ctx, kind, "Chronos-2")
    # ⚠ **ctx は写しを渡す**（窓の長さと記録を書き込むので、呼び元の ctx を汚さない）
    c = {**ctx, "seq_window": seq.WINDOW, "n_cov": n_cov, "cov_names": names, "n_exog": len(leak)}
    cal = _fit_calibration(model, Xtr, ytr, tr_ok["ts"], HORIZON, c)     # ⚠ 10 営業日ぶんパージした尻で較正
    fit_cal = c.pop(chronos2.DOC_KEY, None)
    pred = model(Xtr, ytr, Xte, c)
    fit_main = c.pop(chronos2.DOC_KEY, None)
    doc.update({"model": "Chronos-2", "detector": name, "推論（較正用）": fit_cal, "推論（本番）": fit_main, **cal.doc})
    return cal.buy_pct(pred), doc


@register("detector", NAME_S2)
def chronos_cov(tr, te, feats, ctx):
    return _chronos("chronos", NAME_S2, tr, te, feats, ctx)


@register("detector", NAME_S3)
def chronos_nocov(tr, te, feats, ctx):
    return _chronos("chronos_nocov", NAME_S3, tr, te, feats, ctx)


@register("detector", NAME_S4)
def timexer_exo(tr, te, feats, ctx):
    model, tr_ok, Xtr, Xte, ytr, leak, n_cov, names, doc = _prepare(tr, te, feats, ctx, "timexer", "TimeXer")
    ts = tr_ok["ts"].to_numpy()
    c = {**ctx, "seq_window": seq.WINDOW, "n_exo": n_cov, "n_exog": len(leak)}
    # ⚠ **較正は訓練の日付の尻（10 営業日ぶんパージ）**。学習器にも同じ日付を渡し、早期打ち切りの検証も日付で切らせる
    split = date_holdout(ts, label_bars=HORIZON)
    if split is None:
        pred_c = np.asarray(model(Xtr, ytr, Xtr, {**c, "ts_tr": ts}), dtype=float)
        cal = calibrate.fit_from_predictions(pred_c, ytr, "train")
        fit_cal = None
    else:
        head, hold = split
        c1 = {**c, "ts_tr": ts[head]}
        pred_c = np.asarray(model(Xtr[head], ytr[head], Xtr[hold], c1), dtype=float)
        cal = calibrate.fit_from_predictions(pred_c, ytr[hold], "holdout")
        fit_cal = c1.get(timexer.DOC_KEY)
    c2 = {**c, "ts_tr": ts}
    pred = model(Xtr, ytr, Xte, c2)
    doc.update({"model": "TimeXer", "detector": NAME_S4, "学習（較正用）": fit_cal,
                "学習（本番）": c2.get(timexer.DOC_KEY), **cal.doc})
    return cal.buy_pct(pred), doc
