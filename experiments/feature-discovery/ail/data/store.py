"""`raw/` `adjusted/` `derived/` の読み書きと manifest。⚠ **入力の指紋（行数・最古・最新・ハッシュ）をここで作る**。

置き場（rules.md 1 章）:

    data/raw/<source>/<period>/<SYMBOL>.csv      ⚠ 取ってきたまま。書き換えない
    data/adjusted/<period>/<SYMBOL>.csv          目盛りを直したもの
    data/derived/<変換名>__<引数>/<SYMBOL>.csv   推定しない変換だけ（rules.md 3 章）
    data/manifests/<layer>_<period>.json         層ごとの指紋

⚠ **ファイル名の `/` は `-` に置き換える**（`BRK/B` → `BRK-B.csv`）。
⚠ **列の意味は `ail/contracts.py` の `BAR_COLUMNS` が正本**（`time_ms` は足の開始時刻・UTC ミリ秒）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Iterable

import pandas as pd

from ail.contracts import BAR_COLUMNS, SERIES_COLUMNS

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DATA = os.path.join(ROOT, "data")


# ---------------------------------------------------------------- 名前

def to_filename(symbol: str) -> str:
    """`BRK/B` → `BRK-B`。⚠ **識別子は `/` 付きのまま持ち、ファイル名にするときだけ置き換える**。"""
    return symbol.replace("/", "-")


def from_filename(name: str) -> str:
    """`BRK-B` → `BRK/B`。⚠ ハイフンを含む本物のティッカーが増えたらここで例外表を持つ。"""
    return "BRK/B" if name == "BRK-B" else name


# ---------------------------------------------------------------- 置き場

def raw_dir(source: str, period: str) -> str:
    return os.path.join(DATA, "raw", source, period)


def series_dir(source: str) -> str:
    """外部系列の置き場。⚠ **足とはディレクトリを分ける**（形が違うので検査も違う）。"""
    return os.path.join(DATA, "raw", source, "series")


def adjusted_dir(period: str) -> str:
    return os.path.join(DATA, "adjusted", period)


def derived_dir(transform: str, args: str = "") -> str:
    """⚠ **引数までディレクトリ名に入れて、引数違いを別物として並存させる**（rules.md 3 章）。"""
    return os.path.join(DATA, "derived", f"{transform}__{args}" if args else transform)


def path_of(directory: str, symbol: str) -> str:
    return os.path.join(directory, f"{to_filename(symbol)}.csv")


def symbols_in(directory: str) -> list[str]:
    if not os.path.isdir(directory):
        return []
    return sorted(from_filename(f[:-4]) for f in os.listdir(directory) if f.endswith(".csv"))


# ---------------------------------------------------------------- 読み書き

def read_bars(directory: str, symbol: str) -> pd.DataFrame:
    """足を読む。⚠ **`ts`（UTC の datetime）をここで必ず足す**ので、各所で書き直さない。"""
    df = pd.read_csv(path_of(directory, symbol))
    df["ts"] = pd.to_datetime(df["time_ms"], unit="ms", utc=True)
    return df.sort_values("time_ms").reset_index(drop=True)


def write_bars(directory: str, symbol: str, df: pd.DataFrame) -> str:
    """足を書く。⚠ **`BAR_COLUMNS` 以外は落とす**（派生列を層に混ぜ込まない）。"""
    os.makedirs(directory, exist_ok=True)
    path = path_of(directory, symbol)
    df[list(BAR_COLUMNS)].to_csv(path, index=False)
    return path


def read_series(directory: str, series_id: str) -> pd.DataFrame:
    """外部系列を読む。⚠ **`ts` をここで足す**（足と同じ扱いにする）。"""
    df = pd.read_csv(path_of(directory, series_id))
    df["ts"] = pd.to_datetime(df["time_ms"], unit="ms", utc=True)
    return df.sort_values("time_ms").reset_index(drop=True)


def write_series(directory: str, series_id: str, df: pd.DataFrame) -> str:
    """外部系列を書く。⚠ **`SERIES_COLUMNS` 以外は落とす。**"""
    os.makedirs(directory, exist_ok=True)
    path = path_of(directory, series_id)
    df[list(SERIES_COLUMNS)].to_csv(path, index=False)
    return path


def series_fingerprint(df: pd.DataFrame) -> dict:
    body = df[list(SERIES_COLUMNS)].to_csv(index=False).encode()
    return {"rows": int(len(df)),
            "oldest_ms": int(df["time_ms"].iloc[0]) if len(df) else None,
            "newest_ms": int(df["time_ms"].iloc[-1]) if len(df) else None,
            "sha256": hashlib.sha256(body).hexdigest()[:16]}


# ---------------------------------------------------------------- 指紋と manifest

def fingerprint(df: pd.DataFrame) -> dict:
    """1 系列の指紋。⚠ **同じ指紋なら同じ入力**とみなす（`runs/` の再現性の根拠）。"""
    body = df[list(BAR_COLUMNS)].to_csv(index=False).encode()
    return {
        "rows": int(len(df)),
        "oldest_ms": int(df["time_ms"].iloc[0]) if len(df) else None,
        "newest_ms": int(df["time_ms"].iloc[-1]) if len(df) else None,
        "sha256": hashlib.sha256(body).hexdigest()[:16],
    }


def write_manifest(layer: str, period: str, entries: dict, extra: dict | None = None) -> str:
    """層の manifest。⚠ **調整の比率と出典もここに残す**（rules.md 2 章 規約 8）。"""
    d = os.path.join(DATA, "manifests")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{layer}_{period}.json")
    doc = {"layer": layer, "period": period,
           "written_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "symbols": len(entries), **(extra or {}), "series": entries}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    return path


def read_manifest(layer: str, period: str) -> dict:
    path = os.path.join(DATA, "manifests", f"{layer}_{period}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def manifest_digest(layer: str, period: str) -> str:
    """manifest 全体を 1 個の短いハッシュにする。⚠ **`runs/` はこれで入力を指す**。"""
    m = read_manifest(layer, period)
    body = json.dumps(m["series"], sort_keys=True).encode()
    return hashlib.sha256(body).hexdigest()[:16]


def load_panel(directory: str, symbols: Iterable[str] | None = None) -> dict[str, pd.DataFrame]:
    """複数銘柄をまとめて読む。⚠ **断面の特徴量はここから作る**。"""
    syms = list(symbols) if symbols is not None else symbols_in(directory)
    out = {}
    for s in syms:
        if os.path.exists(path_of(directory, s)):
            out[s] = read_bars(directory, s)
    return out
