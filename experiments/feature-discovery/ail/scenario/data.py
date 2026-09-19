"""シナリオ予測の入力 — 特徴量・60 日の窓・5 日の答え・時間順の分割・標準化（プラン §3・§5）。

> 予測起点 t ＝ 足 t の終値が確定した後（日足は 16:00 ET の引けで確定。足の `ts` はその営業日の 00:00 UTC）。
> ⚠ **足 t の特徴量は足 t までしか見ない。** 答えは足 t+1 … t+5 の日次対数リターン。

⚠ **落としたい事故は 3 つ**（`tests/test_scenario_data.py`）:

1. 特徴量が未来を見る（`rolling` は後ろ向きだけ・`shift(-k)` は答えを作る 1 か所だけ）
2. 答えが未確定の起点で学習する（境目では **答えの確定日** で切る。起点の日付では切らない）
3. 標準化が検証・テストの行から学ぶ（`Scaler.fit` は学習の最後の起点までの行しか見ない）

⚠ **欠損は埋めない。** 助走（20 本）や市場の足の欠けで NaN が入った窓は、起点ごと落とす（未来方向の補間をしないため）。
⚠ **窓の並びは古い順**（`X[:, 0]` が 59 本前・`X[:, -1]` が起点の足）。`seq` 層（新しい順）とは逆なので混ぜない。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ail.data import store

# ⚠ **最小構成**（プラン §3 の 1〜4。大量に足す前にこれを評価する）。⚠ **順序は保存する scaler・重みと対になる**
BASE_FEATURES = ("ret_1", "vol_rel20", "rv_5", "rv_20", "cum_5", "cum_20")
MARKET_FEATURE = "mkt_ret_1"
WARMUP = 20                      # 最長の後ろ向きの窓（rv_20・cum_20・vol_rel20）


def dates_of(bars: pd.DataFrame) -> pd.Series:
    """足の営業日（tz なしの日付）。⚠ **`ts` は営業日の 00:00 UTC なので、日付はそのまま営業日**。"""
    return bars["ts"].dt.tz_localize(None).dt.normalize()


def feature_names(symbol: str, market: str | None) -> tuple[str, ...]:
    """⚠ **対象が市場代表そのものなら市場の列は足さない**（`ret_1` と同じ列が 2 本になるだけ）。"""
    return BASE_FEATURES + ((MARKET_FEATURE,) if market and market != symbol else ())


def build_features(bars: pd.DataFrame, market_bars: pd.DataFrame | None = None) -> pd.DataFrame:
    """1 銘柄の足から特徴量の表を作る。返り値は `bars` と同じ行数・同じ並び（`date` 列つき）。"""
    r = np.log(bars["close"]).diff()               # ⚠ 足 i のリターン = c[i]/c[i-1]。未来ではない
    v = bars["volume"].astype(float)
    out = pd.DataFrame({"date": dates_of(bars)}, index=bars.index)
    out["ret_1"] = r
    # ⚠ **平均は前日までの 20 本**（`shift(1)`）。今日の出来高が自分の基準に入らないようにする
    out["vol_rel20"] = np.log(v) - np.log(v.shift(1).rolling(WARMUP).mean())
    for k in (5, 20):
        out[f"rv_{k}"] = np.sqrt((r ** 2).rolling(k).mean())      # 実現ボラ（平均は引かない）
        out[f"cum_{k}"] = r.rolling(k).sum()
    if market_bars is not None:
        # ⚠ **同じ取引所・同じ引けで確定する足だけを日付で結ぶ**（公開時刻が同じ）。無い日は NaN のまま
        # ⚠ **先に対象の営業日へ並べてから差を取る。** 市場の足だけで差を取ると、欠けた日の翌日が 2 日ぶんのリターンになる
        m = pd.Series(market_bars["close"].to_numpy(dtype=float), index=dates_of(market_bars))
        out[MARKET_FEATURE] = np.log(out["date"].map(m)).diff()
    return out[["date", *[c for c in BASE_FEATURES + (MARKET_FEATURE,) if c in out]]]


@dataclass
class Samples:
    """起点ごとの窓と答え。⚠ **答えが未確定の起点も持つ**（`labeled` が False。予測には使える・学習と評価には使えない）。"""

    X: np.ndarray            # [n, window, features]・古い順
    Y: np.ndarray            # [n, horizon]・日次対数リターン（未確定は NaN）
    origin: np.ndarray       # [n] 予測起点の営業日（datetime64[D]）
    label_end: np.ndarray    # [n] 答えが確定する営業日（未確定は NaT）
    features: tuple[str, ...]

    @property
    def labeled(self) -> np.ndarray:
        return ~np.isnat(self.label_end)

    def __len__(self) -> int:
        return len(self.origin)


def build_samples(feats: pd.DataFrame, names: tuple[str, ...], window: int, horizon: int) -> Samples:
    """特徴量の表から (窓, 答え) を組む。⚠ **X と Y の時点の対応はここ 1 か所で決まる**（取り違えない）。"""
    f = feats[list(names)].to_numpy(dtype=np.float64)
    r = feats["ret_1"].to_numpy(dtype=np.float64)
    d = feats["date"].to_numpy().astype("datetime64[D]")
    n = len(feats)
    idx = [i for i in range(window - 1, n) if np.isfinite(f[i - window + 1:i + 1]).all()]
    X = np.stack([f[i - window + 1:i + 1] for i in idx]).astype(np.float32)
    Y = np.full((len(idx), horizon), np.nan, dtype=np.float32)
    end = np.full(len(idx), np.datetime64("NaT"), dtype="datetime64[D]")
    for j, i in enumerate(idx):
        if i + horizon < n:                        # ⚠ 足 i+1 … i+horizon が全部あるときだけ確定
            Y[j] = r[i + 1:i + horizon + 1]
            end[j] = d[i + horizon]
    return Samples(X, Y, d[idx], end, tuple(names))


@dataclass
class Fold:
    name: str
    train: np.ndarray        # Samples への添字
    val: np.ndarray
    test: np.ndarray
    val_start: np.datetime64
    test_start: np.datetime64
    test_stop: np.datetime64


def split(s: Samples, test_starts: list[str], test_end: str, val_years: int) -> list[Fold]:
    """時間順・増えていく窓。⚠ **学習と検証は「答えの確定日」で切る**（プラン §5 の図）。

    学習 ＝ 答えが検証の始まりより前に確定 ／ 検証 ＝ 起点が検証期間・答えがテストの始まりより前に確定 ／
    テスト ＝ 起点が塊の中（⚠ 答えが次の塊に入るのは構わない。評価であって学習ではない）。
    """
    starts = [np.datetime64(x, "D") for x in test_starts]
    stops = starts[1:] + [np.datetime64(test_end, "D")]
    if any(a >= b for a, b in zip(starts, stops)):
        raise SystemExit(f"⚠ test_starts は昇順で、test_end より前: {test_starts} / {test_end}")
    ok = s.labeled
    folds = []
    for k, (t0, t1) in enumerate(zip(starts, stops), 1):
        v0 = np.datetime64((pd.Timestamp(t0) - pd.DateOffset(years=val_years)).date(), "D")
        train = np.flatnonzero(ok & (s.label_end < v0))
        val = np.flatnonzero(ok & (s.origin >= v0) & (s.label_end < t0))
        test = np.flatnonzero(ok & (s.origin >= t0) & (s.origin < t1))
        if not (len(train) and len(val) and len(test)):
            raise SystemExit(f"⚠ fold {k} が空（学習 {len(train)}・検証 {len(val)}・テスト {len(test)}）")
        folds.append(Fold(f"f{k}", train, val, test, v0, t0, t1))
    return folds


@dataclass
class Scaler:
    """特徴量の平均・標準偏差と、答えの尺度。⚠ **学習区間の行だけで fit し、検証・テスト・予測には固定して使う。**

    ⚠ **答えは中心化しない**（尺度で割るだけ）。0 の位置を動かすと「上昇確率」の意味が学習区間の平均に依存する。
    """

    features: tuple[str, ...]
    mean: np.ndarray
    std: np.ndarray
    y_scale: float
    fit_until: str           # fit に使った最後の営業日（記録用）

    @classmethod
    def fit(cls, feats: pd.DataFrame, names: tuple[str, ...], until: np.datetime64) -> "Scaler":
        """`until` ＝ 学習の最後の起点。⚠ **それより後の行は見ない**（答えの 5 日ぶんも見ない）。"""
        rows = feats.loc[feats["date"].to_numpy().astype("datetime64[D]") <= until, list(names)].dropna()
        if len(rows) < 2:
            raise SystemExit(f"⚠ 標準化に使える行が無い（until={until}）")
        std = rows.std(ddof=0).to_numpy()
        std = np.where(std > 0, std, 1.0)          # 動かない列は割らない
        y = float(rows["ret_1"].std(ddof=0)) or 1.0
        return cls(tuple(names), rows.mean().to_numpy(), std, y, str(until))

    def x(self, X: np.ndarray) -> np.ndarray:
        return ((X - self.mean) / self.std).astype(np.float32)

    def y(self, Y: np.ndarray) -> np.ndarray:
        return (Y / self.y_scale).astype(np.float32)

    def y_inverse(self, Y: np.ndarray) -> np.ndarray:
        return Y * self.y_scale

    def to_dict(self) -> dict:
        return {"features": list(self.features), "mean": self.mean.tolist(), "std": self.std.tolist(),
                "y_scale": self.y_scale, "fit_until": self.fit_until}

    @classmethod
    def from_dict(cls, doc: dict, names: tuple[str, ...]) -> "Scaler":
        if tuple(doc["features"]) != tuple(names):
            raise SystemExit(f"⚠ 保存した scaler と特徴量の順序が違う: {doc['features']} / {list(names)}")
        return cls(tuple(names), np.asarray(doc["mean"]), np.asarray(doc["std"]),
                   float(doc["y_scale"]), doc["fit_until"])


def quality_flags(bars: pd.DataFrame, today: str | None = None) -> dict:
    """足の検査（プラン §3 の条件 3）。⚠ **数えるだけで直さない**（直すのは `cli.adjust`。rules.md 2 章）。"""
    d = dates_of(bars)
    r = np.log(bars["close"]).diff()
    gap = d.diff().dt.days
    out = {
        "rows": int(len(bars)),
        "first": str(d.iloc[0].date()), "last": str(d.iloc[-1].date()),
        "duplicated_dates": int(d.duplicated().sum()),
        "not_monotonic": int((d.diff().dt.days <= 0).sum()),
        "weekend_bars": int((d.dt.dayofweek >= 5).sum()),
        "nan_cells": int(bars[["open", "high", "low", "close", "volume"]].isna().sum().sum()),
        "nonpositive_price": int((bars[["open", "high", "low", "close"]] <= 0).sum().sum()),
        "zero_volume": int((bars["volume"] <= 0).sum()),
        # ⚠ **|r| > 20% は分割の取りこぼしを疑う**（SPY の実測の最大は 2008-10-13 の 13.6%）
        "abs_return_over_20pct": int((r.abs() > 0.20).sum()),
        "gaps_over_5_days": [str(x.date()) for x in d[gap > 5]],
    }
    if today:
        out["stale_days"] = int((pd.Timestamp(today) - d.iloc[-1]).days)
    return out


def fingerprint(period: str, symbol: str) -> dict:
    """入力の指紋（rules.md 10 章）。⚠ **manifest（`cli.adjust` が書く）の写し**で、ここでは計算し直さない。"""
    path = os.path.join(store.DATA, "manifests", f"adjusted_{period}.json")
    m = json.load(open(path, encoding="utf-8"))
    return {"layer": "adjusted", "period": period, "symbol": symbol, "written_at": m.get("written_at"),
            "source": m.get("source"), "dividend_adjusted": m.get("dividend_adjusted"),
            **{k: (m.get("series") or {}).get(symbol, {}).get(k)
               for k in ("rows", "oldest_ms", "newest_ms", "sha256")}}


def load(cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, tuple[str, ...]]:
    """(足, 特徴量の表, 特徴量の名前)。設定は `config/scenario/*.toml`。"""
    d = store.adjusted_dir(cfg["period"])
    bars = store.read_bars(d, cfg["symbol"])
    names = feature_names(cfg["symbol"], cfg.get("market"))
    market = store.read_bars(d, cfg["market"]) if MARKET_FEATURE in names else None
    return bars, build_features(bars, market), names
