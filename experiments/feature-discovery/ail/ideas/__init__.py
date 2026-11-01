"""開発ループ（rules.md 31 章・2026-10-25）が足す**新しい口の置き場**。`ail.bootstrap` が `load_all()` で `i*.py` を全部 import する。

⚠ ここに置くのは registry への登録（検知器・層・学習器）だけ。既存のモジュールを書き換えない・既存の登録名を上書きしない
（`registry.register` は同じ名前を 2 度登録できない）。⚠ 共有の経路（`cli/run.py`・`ail/validation`・`ail/features`・`ail/detectors`）に
触る口はここに置けない ＝ 「人の手」（K7）。
⚠ ファイル名は `i<YYYYMMDD>_<slug>.py`（import できる名前。登録簿の id と対応させる）。ファイルを足すだけで既存の経路は 1 ビットも変わらない
（指紋テスト `tests/test_predict.py`・`tests/test_trading_run.py`・`tests/test_fwd.py`）。
"""

from __future__ import annotations

import importlib
import pkgutil


def load_all() -> list[str]:
    """`ail/ideas/i*.py` を全部 import する（登録だけ）。返すのは読んだモジュール名。"""
    names: list[str] = []
    for m in sorted(pkgutil.iter_modules(__path__), key=lambda x: x.name):
        if m.name.startswith("i") and not m.ispkg:
            importlib.import_module(f"{__name__}.{m.name}")
            names.append(m.name)
    return names
