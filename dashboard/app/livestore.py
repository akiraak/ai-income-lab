"""記録の置き場 `livefs`（experiments/tastytrade-api-sample/livefs.py）を管理画面から使う 1 か所。

    from app.livestore import livefs

⚠ 2026-09-21 から、執行器・API 検証・管理画面の記録は道はそのままで中身は DB（本物はリポジトリ直下の live.sqlite・
シミュレーションは木の sim.sqlite・デモはデモの置き場の demo.sqlite）。プラン db-model-facts.md §11。
⚠ `HALT` はファイルのまま（停止ボタンはファイルの有無で止める）。
"""

import sys
from pathlib import Path

_SAMPLE = Path(__file__).resolve().parents[2] / "experiments" / "tastytrade-api-sample"
if str(_SAMPLE) not in sys.path:
    sys.path.insert(0, str(_SAMPLE))

import livefs  # noqa: E402,F401


def ensure_db(dirpath: Path, kind: str = "demo") -> None:
    """`dirpath` の記録が入る DB が無ければ、そこに作る（テスト・デモ・Docker の /data）。⚠ 本物の置き場は live.sqlite に入る。"""
    try:
        livefs.locate(Path(dirpath) / "_")
    except livefs.LiveFsError:
        livefs.init(dirpath, kind)


def load_fixture(dirpath: Path) -> int:
    """git に入っているデモの記録（`dashboard/demo/live/` のファイル）を、その置き場の demo.sqlite に入れる（入っていればとばす）。
    ⚠ ファイルがデモの正本（テストの部品）で、demo.sqlite は起動のたびに作り直せる生成物。入れた数を返す。"""
    dirpath = Path(dirpath)
    if not (dirpath / "out").is_dir() and not (dirpath / "state").is_dir():
        return 0
    livefs.init(dirpath, "demo")
    n = 0
    for sub in ("out", "state"):
        if (dirpath / sub).is_dir():
            for path in livefs._files(str(dirpath / sub)):
                n += livefs.import_file(path) == "added"
    return n
