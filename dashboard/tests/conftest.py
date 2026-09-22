import json
import os
import sys
from pathlib import Path

import pytest

DASH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DASH))

from app.config import Settings, import_sample  # noqa: E402

SAMPLE_DIR = DASH.parent / "experiments" / "tastytrade-api-sample"
import_sample(SAMPLE_DIR)

# 検証の記録の書き手（実験側 `ail/rundb.py`。標準ライブラリだけ）。⚠ テストで偽の実行を DB に書くためだけに使う
sys.path.insert(0, str(DASH.parent / "experiments" / "feature-discovery"))
from ail import rundb  # noqa: E402


class RunDir:
    """⚠ **テスト用: 偽の実行を DB（`<runs_dir>/research.sqlite`）に書く。** 記録は 2026-09-21 から DB。

    いままでのディレクトリと同じ書き方（`d.mkdir()`・`(d / "summary.csv").write_text(...)`）がそのまま使える。
    ⚠ 実行は閉じない（テストが後から書き足す・書き換える）。
    """

    def __init__(self, runs_dir: Path, name: str):
        self.runs_dir, self.name = Path(runs_dir), name
        conn = self._conn()
        if not conn.execute("SELECT 1 FROM runs WHERE name = ?", (name,)).fetchone():
            rundb.open_run(conn, name)
        conn.close()

    def _conn(self):
        return rundb.connect(str(self.runs_dir / rundb.FILE_NAME))

    def mkdir(self, *a, **k) -> None:
        pass

    def __truediv__(self, rel: str) -> "_RunFile":
        return _RunFile(self, rel)


class _RunFile:
    def __init__(self, run: RunDir, rel: str):
        self.run, self.rel = run, rel

    def write_text(self, text: str, encoding: str = "utf-8") -> None:
        conn = self.run._conn()
        rundb.put(conn, self.run.name, self.rel, text.encode(encoding))
        conn.close()

    def read_text(self, encoding: str = "utf-8") -> str:
        conn = self.run._conn()
        data = rundb.get(conn, self.run.name, self.rel)
        conn.close()
        return data.decode(encoding)

FAKE_SECRET = "FAKE-CLIENT-SECRET-0001"
FAKE_REFRESH = "FAKE-REFRESH-TOKEN-0001"
FAKE_ACCOUNT = "5WT99999"


def make_row(step, name, env="cert", ok=True, result="ok", detail=None, at="2026-09-08T14:00:00.000+00:00", mock=False, run_id="20260908T140000Z"):
    row = {
        "venue": "tastytrade",
        "run_id": run_id,
        "step": step,
        "name": name,
        "env": env,
        "started_at": {"utc": at, "et": at, "pt": at},
        "sdk": {"python": "3.12.3", "requests": "2.32.5", "websockets": "15.0.1", "sdk": "none (REST/websocket を直接叩く)"},
        "result": result,
        "detail": detail or {},
        "ok": ok,
        "ended_at": {"utc": at},
        "elapsed_ms": 100.0,
    }
    if mock:
        row["mock"] = True
    return row


def good_run(run_id="20260908T140000Z", at="2026-09-08T14:00:00.000+00:00", mock=False):
    """市場時間内（月曜 10:00 ET = 14:00 UTC）の成功記録。"""
    k = dict(at=at, mock=mock, run_id=run_id)
    return [
        make_row(1, "認証", result="authenticated", detail={"expires_in_s": 900, "jwt": {"lifetime_s": 954}, "refresh_token_rotated": False}, **k),
        make_row(2, "口座照会", detail={"account_count": 1}, **k),
        make_row(3, "現在値", env="cert", ok=True, result="unavailable_502", detail={"status": 502}, **k),
        make_row(3, "現在値", env="prod", detail={"delay_s": 0.4, "bid": 1.0, "ask": 1.1}, **k),
        make_row(4, "指値と取消", result="final_Cancelled", detail={"dry_run": {"elapsed_ms": 120.5}, "submit": {"elapsed_ms": 80.0, "transitions_after_submit": [{"at_ms": 5.0, "status": "Received"}, {"at_ms": 900.0, "status": "Live"}]}, "cancel": {"elapsed_ms": 70.0}, "roundtrip_ms": 1000.0}, **k),
        make_row(5, "約定", result="buy_Filled/sell_Filled", detail={"buy": {"elapsed_ms": 300.0}}, **k),
        make_row(6, "口座ストリーマ", result="messages_6/order通知_6", detail={"connect_ack_ms": 100.0, "message_count": 6, "messages": [{"at_ms": 1}] * 6}, **k),
        make_row(61, "DXLink", env="prod", result="events_39", detail={"event_count": 39}, **k),
        make_row(7, "レート制限", result="no_429", detail={"60_per_minute": {"requests": 60, "statuses": {"200": 60}, "first_429_at_request": None, "latency_ms": {"median": 130.0}}, "10_per_second": {"requests": 30, "first_429_at_request": None}}, **k),
    ]


def write_run(records_dir: Path, rows):
    """API 検証の記録を 1 本置く（⚠ 2026-09-21 から DB ＝ `livefs`。道はいままでと同じ）。"""
    import livefs

    head = rows[0]
    path = records_dir / f"{head['venue']}-{head['env']}-{head['run_id']}.jsonl"
    livefs.append_many(path, [json.dumps(r, ensure_ascii=False) for r in rows])
    return path


@pytest.fixture(autouse=True)
def _no_monitors_in_tests(monkeypatch):
    """テストで監視を起こさせない番人。`create_app(settings)` のまま `TestClient` を開くと落ちる。

    ⚠ **監視は `TT_REST_BASE=http://127.0.0.1:1` へ認証しに行く**。ふつうの Linux は即 `ConnectionRefused` だが、
    WSL2（mirrored）は閉じたループバックのポートが無応答で、`ttclient` の 30 秒を 1 本ずつ待ち切る
    （2026-09-18 に 16 本 × 30 秒 ＝ 487 秒【実測】）。監視の状態を見たいテストは `start_monitors=False` のまま
    `app.state.monitors.get(env)` に直に入れる（`test_app.py` の形）。
    """
    from app.monitor import Monitors

    async def _refuse(self):
        raise AssertionError("テストで監視を起こさない: create_app(settings, start_monitors=False) にする（conftest.py の番人）")

    monkeypatch.setattr(Monitors, "start", _refuse)


@pytest.fixture
def settings(tmp_path):
    data = tmp_path / "data"
    records = data / "records"
    s = Settings(
        auth_mode="loopback",
        cf_team=None,
        cf_aud=None,
        cf_email=None,
        bind="127.0.0.1",
        port=3012,
        data_dir=data,
        records_dir=records,
        sample_dir=SAMPLE_DIR,
        runs_dir=tmp_path / "runs",
        exp_dir=tmp_path / "feature-discovery",
        live_dir=tmp_path / "live-trading",
        sample_python=sys.executable,
        symbol="SPY",
        poll_seconds=30,
        tt={"TT_ENV": "cert", "TT_CLIENT_SECRET": FAKE_SECRET, "TT_REFRESH_TOKEN": FAKE_REFRESH, "TT_REST_BASE": "http://127.0.0.1:1"},
    )
    s.ensure_dirs()
    return s


def write_experiment(runs_dir: Path, run_id: str, *, config: dict, inputs: dict,
                     summary: list[tuple] | None, checks: dict | None = None) -> "RunDir":
    """検証の実行記録（DB の実行 1 つ）を作る。summary は (手法, 本数, 的中率, IC, 粗利, 純利)。

    ⚠ **`summary=None` は `summary.csv` を書かない**（前置きの門で閾値売買を回していない実行。
    rules.md 14-5。`cli/run.py` は門前のとき summary も result も書かない）。
    """
    d = RunDir(runs_dir, run_id)
    (d / "config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    (d / "inputs.json").write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
    (d / "env.json").write_text(json.dumps({"seed": 0, "git_commit": "abc1234",
                                            "started_at": run_id.split("_")[0]},
                                           ensure_ascii=False), encoding="utf-8")
    if summary is not None:
        lines = ["手法,本数,的中率,IC,粗利bp,純利bp,fold数"]
        lines += [",".join(str(x) for x in row) + ",5" for row in summary]
        (d / "summary.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if checks is not None:
        (d / "checks.json").write_text(json.dumps(checks, ensure_ascii=False), encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def _record_db(tmp_path):
    """⚠ 記録は DB（2026-09-21。プラン db-model-facts.md §11）。テストの一時置き場に自分の DB を作る ＝ 本物の live.sqlite に書かない。"""
    import livefs

    livefs.init(tmp_path, "live")
    yield
    livefs.forget()
