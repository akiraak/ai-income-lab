"""実行記録。1 実行 1 ディレクトリ（`runs/<時刻>_<実験名>/`）。

    runs/2026-09-08T22-31-00_own_only_h1/
      config.json    使った設定の写し（TOML を解決したあとの姿）
      inputs.json    ⚠ 入力の指紋（層・行数・最古・最新・ハッシュ）
      env.json       種・コードの版（git の commit）・ライブラリの版
      result.csv     手法ごとの成績（fold ごとの生の行）
      summary.csv    手法ごとにまとめたもの
      checks.json    ⚠ fold の符号・上乗せ・実効標本数・デフレーテッド SR（管理画面が読む）
      selected.csv   ⚠ 手法が fold ごとに選んだ列（偽薬を選んだ割合の実測に使う）
      log.txt        画面に出したものと同じ
      fitted/        ⚠ 標本から学んだ係数（再現用。⚠ **次の実行では読み込まない**）

⚠ **これが無いと「昨日より良くなったか」が言えない。** 日々改善は、前回の数字が
⚠ **同じ入力・同じ種で再現できて初めて回る。**
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time

import pandas as pd

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
RUNS = os.path.join(ROOT, "runs")

# ⚠ **日付をずらした偽薬の実行は名前の末尾で見分ける**（`_leak` と同じ扱い。台帳の試行に数えない）
_SHIFT = re.compile(r"_shift(\d+)$")


def variant(experiment: str, leak: bool = False, shift_days: int = 0) -> str:
    """表（`data/features/<これ>/`）と実行（`runs/<時刻>_<これ>/`）の名前。

    ⚠ **対照（先読み・偽薬）は名前の末尾で本物と分ける。** ⚠ **本物と同じ名前にすると、台帳が
    「特徴量の層」で実行をまとめるときに偽薬が代表の行を乗っ取る**（鍵が同じ「own ex」になる）。
    """
    if shift_days < 0:
        raise SystemExit(f"⚠ --shift-days は 0 以上（負は未来の値を貼る）: {shift_days}")
    if leak and shift_days:
        raise SystemExit("⚠ --leak と --shift-days は同時に使わない（何の対照なのか分からなくなる）")
    return experiment + ("_leak" if leak else "") + (f"_shift{shift_days}" if shift_days else "")


def shift_days_of(name: str, config: dict | None = None) -> int:
    """⚠ **偽薬（日付を過去へずらした）の実行なら、そのずらし幅。本物なら 0。**

    名前の末尾（`_shift<S>`）か、config の `features.ex_shift_days` のどちらかで見分ける
    （⚠ **TOML に直接書いた偽薬も取りこぼさない**）。
    """
    m = _SHIFT.search(name)
    by_name = int(m.group(1)) if m else 0
    by_config = int(((config or {}).get("features") or {}).get("ex_shift_days") or 0)
    return by_name or by_config


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def _versions() -> dict:
    import numpy, sklearn, scipy          # noqa: E401
    out = {"python": sys.version.split()[0], "numpy": numpy.__version__,
           "pandas": pd.__version__, "scikit-learn": sklearn.__version__,
           "scipy": scipy.__version__, "platform": platform.platform()}
    # ⚠ **モデルの軸で使う版も残す**（plans/archive/gpu-models.md。入っていない環境では黙って省く）
    for mod in ("torch", "lightgbm"):
        try:
            out[mod] = __import__(mod).__version__
        except Exception:
            pass
    return out


class Run:
    """1 実行ぶんの記録。⚠ **画面に出すものは全部 `log` を通す**（後から同じものが読める）。"""

    def __init__(self, name: str, config: dict, seed: int):
        self.started = time.strftime("%Y-%m-%dT%H-%M-%S")
        self.dir = os.path.join(RUNS, f"{self.started}_{name}")
        os.makedirs(self.dir, exist_ok=True)
        self.lines: list[str] = []
        self._write("config.json", {k: v for k, v in config.items() if not k.startswith("__")})
        self._write("env.json", {"seed": seed, "git_commit": _git_commit(),
                                 "started_at": self.started, **_versions()})

    def _write(self, name: str, doc) -> None:
        with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2, default=str)

    def log(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)

    def inputs(self, doc: dict) -> None:
        """⚠ **入力の指紋。** 同じ指紋・同じ種なら同じ数字が出なければならない。"""
        self._write("inputs.json", doc)

    def fitted(self, name: str, doc) -> None:
        """⚠ **標本から学んだ係数を残す。** 再現のためだけで、⚠ **次の実行では読み込まない**。"""
        d = os.path.join(self.dir, "fitted")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, f"{name}.json"), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2, default=str)

    def checks(self, doc: dict) -> None:
        """⚠ **検査の結果。** fold の符号・上乗せ・実効標本数・デフレーテッド SR を記録の一部にする。"""
        self._write("checks.json", doc)

    def selected(self, picked: pd.DataFrame) -> None:
        """⚠ **選別手法が fold ごとに選んだ列。** ⚠ **偽薬を選んだ割合の実測に使う。**"""
        if len(picked):
            picked.to_csv(os.path.join(self.dir, "selected.csv"), index=False)

    def result(self, raw: pd.DataFrame, summary: pd.DataFrame) -> None:
        raw.to_csv(os.path.join(self.dir, "result.csv"), index=False)
        summary.to_csv(os.path.join(self.dir, "summary.csv"))

    def close(self) -> str:
        with open(os.path.join(self.dir, "log.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")
        return self.dir


def list_runs() -> list[str]:
    return sorted(os.listdir(RUNS)) if os.path.isdir(RUNS) else []
