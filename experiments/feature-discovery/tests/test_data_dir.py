"""`AIL_DATA_DIR`（実売買用の置き場への差し替え）。⚠ **未指定なら今までどおり `data/`** であること。"""

import importlib
import os

from ail.data import store


def _reload(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("AIL_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("AIL_DATA_DIR", value)
    return importlib.reload(store)


def test_default_is_data(monkeypatch):
    try:
        s = _reload(monkeypatch, None)
        assert s.DATA == os.path.join(s.ROOT, "data")
        assert s.adjusted_dir("d") == os.path.join(s.ROOT, "data", "adjusted", "d")
        assert _reload(monkeypatch, "").DATA == os.path.join(s.ROOT, "data")    # 空文字も未指定と同じ
    finally:
        monkeypatch.undo()
        importlib.reload(store)


def test_override(monkeypatch, tmp_path):
    try:
        s = _reload(monkeypatch, str(tmp_path / "live"))
        assert s.DATA == str(tmp_path / "live")
        assert s.raw_dir("tastytrade", "d") == str(tmp_path / "live" / "raw" / "tastytrade" / "d")
        assert s.series_dir("ecb").startswith(s.DATA)
    finally:
        monkeypatch.undo()
        importlib.reload(store)
