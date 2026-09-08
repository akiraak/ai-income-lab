"""デモ（鍵なし）: 設定の判定、モックを相手にする監視 2 本、帯の表示。モックの起動と監視ループは動かさない。"""

import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from tests.conftest import FAKE_REFRESH, FAKE_SECRET, SAMPLE_DIR, good_run, write_run


def _environ(tmp_path: Path, **extra) -> dict:
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    env = {
        "AIL_ENV_FILE": str(empty),        # dashboard/.env を読ませない
        "AIL_TT_ENV_FILE": str(empty),     # サンプルの .env（本物の資格情報）を読ませない
        "AIL_DATA_DIR": str(tmp_path / "data"),
        "AIL_SAMPLE_DIR": str(SAMPLE_DIR),
        "AIL_SAMPLE_PYTHON": sys.executable,
    }
    env.update(extra)
    return env


def test_demo_is_automatic_without_credentials(tmp_path):
    s = load_settings(_environ(tmp_path))
    assert s.demo is True
    # デモのデータは本物と混ぜない
    assert s.data_dir == (tmp_path / "data" / "demo").resolve()
    assert s.records_dir == (tmp_path / "data" / "demo" / "records").resolve()


def test_demo_off_with_credentials_or_flag(tmp_path):
    with_creds = load_settings(_environ(tmp_path, TT_CLIENT_SECRET=FAKE_SECRET, TT_REFRESH_TOKEN=FAKE_REFRESH))
    assert with_creds.demo is False
    assert with_creds.records_dir == (tmp_path / "data" / "records").resolve()
    forced_off = load_settings(_environ(tmp_path, AIL_DEMO="0"))
    assert forced_off.demo is False


def test_demo_forced_even_with_credentials(tmp_path):
    s = load_settings(_environ(tmp_path, AIL_DEMO="1", TT_CLIENT_SECRET=FAKE_SECRET, TT_REFRESH_TOKEN=FAKE_REFRESH, TT_REST_BASE="http://127.0.0.1:1"))
    assert s.demo is True
    app = create_app(s, start_monitors=False)
    mons = app.state.monitors.items
    # 本物の資格情報があっても、監視は cert / prod ともモック相手（本物には繋がない）
    assert set(mons) == {"cert", "prod"}
    for mon in mons.values():
        assert mon.state["mock"] is True
        assert mon.creds["client_secret"] != FAKE_SECRET
        assert mon.client.base == s.mock_rest_base


def test_demo_pages_show_banner_and_include_mock_records(tmp_path):
    s = load_settings(_environ(tmp_path))
    s.ensure_dirs()
    write_run(s.records_dir, good_run(mock=True))
    app = create_app(s, start_monitors=False)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        home = c.get("/")
        assert home.status_code == 200
        assert "デモ" in home.text and "MOCK" in home.text
        assert "監視する環境が無い" not in home.text
        judge = c.get("/judge")
        assert judge.status_code == 200
        assert "DEMO" in judge.text and "tastytrade" in judge.text  # モックの記録から表が組み立つ
        api = c.get("/api/judge").json()
        assert api["demo"] is True and api["included_mock_runs"] == 1 and api["real_runs"] == 1


def test_no_demo_excludes_mock_records(tmp_path):
    s = load_settings(_environ(tmp_path, TT_CLIENT_SECRET=FAKE_SECRET, TT_REFRESH_TOKEN=FAKE_REFRESH, TT_REST_BASE="http://127.0.0.1:1"))
    s.ensure_dirs()
    write_run(s.records_dir, good_run(mock=True))
    app = create_app(s, start_monitors=False)
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        assert "デモ" not in c.get("/").text
        api = c.get("/api/judge").json()
        assert api["demo"] is False and api["excluded_mock_runs"] == 1 and api["real_runs"] == 0
