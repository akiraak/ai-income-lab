"""実行記録。⚠ **1 実行 1 記録・上書きしない**（rules.md 10 章）。正本は `runs/research.sqlite`（`ail/rundb.py`）。

⚠ **2026-09-21 にディレクトリから DB へ移した**（利用者の裁定「2 か所には置かない。DB に入れたらファイルは削除」。
[プラン](../../../docs/plans/db-model-facts.md)）。実行の名前（`<時刻>_<実験名>`）と、中のファイルの道・中身は
いままでのディレクトリと同じで、置き場だけが DB の行になった:

    実行 2026-09-08T22-31-00_own_only_h1 の中のファイル
      config.json    使った設定の写し（TOML を解決したあとの姿）
      inputs.json    ⚠ 入力の指紋（層・行数・最古・最新・ハッシュ）
      env.json       種・コードの版（git の commit）・ライブラリの版
      result.csv     手法ごとの成績（fold ごとの生の行）
      summary.csv    手法ごとにまとめたもの
      checks.json    ⚠ fold の符号・上乗せ・実効標本数・デフレーテッド SR（管理画面が読む）
      selected.csv   ⚠ 手法が fold ごとに選んだ列（偽薬を選んだ割合の実測に使う）
      per_symbol.csv ⚠ 銘柄別の純利 bp（閾値つき売買だけ。成果物であって採否には使わない）
      holds.csv      ⚠ 1 取引 1 行の保有日数（閾値つき売買だけ。rules.md 13-4 の 6・15-7 の 4。採否には使わない）
      daily.csv      ⚠ 日次のポートフォリオ系列（純利・保有日率・乱択ゲート。エピソード表の素）
      log.txt        画面に出したものと同じ
      fitted/        ⚠ 標本から学んだ係数（再現用。⚠ **次の実行では読み込まない**）

⚠ **これが無いと「昨日より良くなったか」が言えない。** 日々改善は、前回の数字が
⚠ **同じ入力・同じ種で再現できて初めて回る。**

  - ⚠ **書くたびにその場で DB に入れる**（`cli.run` は自分の `summary.csv` が台帳に入った状態で `n_trials` を数える）
  - `Run.dir` は作業の置き場（`runs/work/<実行>/`）。道でしか書けないもの（`torch.save` など）はここに置けば、
    `close()` が DB に入れて置き場を消す。⚠ 途中で落ちた実行は、次の `Run` か `python3 -m cli.db sweep` が閉じる
  - 読むのは `list_runs()`・`read_json()`・`read_csv()`・`read_bytes()`・`materialized()`（道が要る読み手のため）
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

import pandas as pd

from ail import rundb

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
# ⚠ DB（`research.sqlite`）と作業の置き場（`work/`）の親。テストはここを一時ディレクトリに差し替える
RUNS = os.path.join(ROOT, "runs")


def db_path() -> str:
    return os.environ.get("AIL_RESEARCH_DB") or os.path.join(RUNS, rundb.FILE_NAME)


def work_root() -> str:
    return os.path.join(RUNS, "work")


def _reader():
    return rundb.shared(db_path())

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
        self.name = f"{self.started}_{name}"
        self.conn = rundb.connect(db_path())
        rundb.sweep(self.conn, work_root())          # ⚠ 前に落ちた実行を閉じる（中身は足さない）
        rundb.open_run(self.conn, self.name)
        self.dir = os.path.join(work_root(), self.name)   # ⚠ 作業の置き場。close() で DB に入れて消す
        os.makedirs(self.dir, exist_ok=True)
        self.lines: list[str] = []
        self._write("config.json", {k: v for k, v in config.items() if not k.startswith("__")})
        self._write("env.json", {"seed": seed, "git_commit": _git_commit(),
                                 "started_at": self.started, **_versions()})

    def _put(self, path: str, data: bytes) -> None:
        rundb.put(self.conn, self.name, path, data)

    def _write(self, name: str, doc) -> None:
        self._put(name, json.dumps(doc, ensure_ascii=False, indent=2, default=str).encode("utf-8"))

    def _csv(self, name: str, df: pd.DataFrame, **kw) -> None:
        # ⚠ `to_csv(道)` と同じバイト列（UTF-8・改行 \n）
        self._put(name, df.to_csv(**kw).encode("utf-8"))

    def log(self, text: str = "") -> None:
        print(text, flush=True)
        self.lines.append(text)

    def inputs(self, doc: dict) -> None:
        """⚠ **入力の指紋。** 同じ指紋・同じ種なら同じ数字が出なければならない。"""
        self._write("inputs.json", doc)

    def fitted(self, name: str, doc) -> None:
        """⚠ **標本から学んだ係数を残す。** 再現のためだけで、⚠ **次の実行では読み込まない**。"""
        self._write(f"fitted/{name}.json", doc)

    def checks(self, doc: dict) -> None:
        """⚠ **検査の結果。** fold の符号・上乗せ・実効標本数・デフレーテッド SR を記録の一部にする。"""
        self._write("checks.json", doc)

    def selected(self, picked: pd.DataFrame) -> None:
        """⚠ **選別手法が fold ごとに選んだ列。** ⚠ **偽薬を選んだ割合の実測に使う。**"""
        if len(picked):
            self._csv("selected.csv", picked, index=False)

    def result(self, raw: pd.DataFrame, summary: pd.DataFrame) -> None:
        self._csv("result.csv", raw, index=False)
        self._csv("summary.csv", summary)

    def daily(self, net: dict, extra: dict | None = None) -> None:
        """⚠ **日次のポートフォリオ系列**（手法 × 閾値 × 日）。⚠ **fold を跨いで連結して 1 本にする。**

        ⚠ **これが無いと、後から系列を見たいときに同じ config を回し直すしかない**
        （2026-09-11 の検出限界の検討がそうなった。validation-power.md §1）。
        エピソード表（rules.md 14-8）も検出限界の引き直しも、ここを読めば済む。
        """
        rows = []
        for kind, table in (("純利bp", net), *(extra or {}).items()):
            if not isinstance(table, dict):
                continue                          # ⚠ `extra["holds"]` は表（DataFrame）で、日次系列ではない
            for (method, th), parts in (table or {}).items():
                if not parts:
                    continue
                s = pd.concat(parts).sort_index()
                rows.append(pd.DataFrame({"手法": method, "閾値": th, "系列": kind,
                                          "ts": s.index, "値": s.to_numpy()}))
        if rows:
            self._csv("daily.csv", pd.concat(rows, ignore_index=True), index=False)

    def holds(self, df) -> None:
        """⚠ **1 取引 1 行の保有日数**（rules.md 13-4 の 6）。⚠ **成果物であって採否には使わない**（`per_symbol.csv` と同じ扱い）。"""
        if df is not None and len(df):
            self._csv("holds.csv", df, index=False)

    def topk(self, df) -> None:
        """⚠ **上位 K の診断**（rules.md 17-5 の 4: 買いの合図・買えた数・見送り・投下率）。⚠ **採否には使わない。**"""
        if df is not None and len(df):
            self._csv("topk.csv", df, index=False)

    def per_symbol(self, df: pd.DataFrame) -> None:
        """⚠ **銘柄別 bp は成果物**（rules.md 13-7。利用者の求める出力）。⚠ **採否には使わない。**"""
        if len(df):
            self._csv("per_symbol.csv", df, index=False)

    def close(self) -> str:
        """ログと作業の置き場を DB に入れ、実行を閉じる（⚠ 以後は書き換えない）。戻り値は実行の名前。"""
        self._put("log.txt", ("\n".join(self.lines) + "\n").encode("utf-8"))
        if os.path.isdir(self.dir):
            rundb.put_dir(self.conn, self.name, self.dir)
            shutil.rmtree(self.dir)               # ⚠ 入れたら消す（2 か所に置かない）
        rundb.close_run(self.conn, self.name)
        self.conn.close()
        return self.name


def list_runs() -> list[str]:
    """DB にある実行の名前（名前の順 ＝ 時刻の順）。⚠ 実行中・落ちた実行も入る（いままでのディレクトリと同じ）。"""
    return rundb.list_runs(_reader())


def read_bytes(run: str, path: str) -> bytes | None:
    return rundb.get(_reader(), run, path)


def read_json(run: str, path: str):
    """⚠ 無ければ None（`{}` で埋めない。呼び手が決める）。"""
    return rundb.get_json(_reader(), run, path)


def read_csv(run: str, path: str, **kw) -> pd.DataFrame | None:
    data = read_bytes(run, path)
    return None if data is None else pd.read_csv(io.BytesIO(data), **kw)


def exists(run: str, path: str) -> bool:
    return read_bytes(run, path) is not None


def files(run: str, prefix: str = "") -> list[str]:
    return rundb.list_files(_reader(), run, prefix)


@contextlib.contextmanager
def materialized(run: str):
    """実行のファイルを一時ディレクトリに書き戻して、その道を渡す（`torch.load` のように道が要る読み手のため）。

    ⚠ 読むためだけの写し。出たら消す（2 か所に置かない）。
    """
    run = resolve(run)
    with tempfile.TemporaryDirectory(prefix="ail-run-") as d:
        rundb.export_run(_reader(), run, d)
        yield d


def resolve(arg: str) -> str:
    """`runs/<実行>`・`<実行>`・末尾の `/` を許して実行の名前にする。⚠ DB に無ければ止める。"""
    name = os.path.basename(os.path.normpath(arg))
    if name not in set(list_runs()):
        raise SystemExit(f"⚠ 実行 {name} は DB に無い（{db_path()}）")
    return name
