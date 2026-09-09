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
    records_dir.mkdir(parents=True, exist_ok=True)
    head = rows[0]
    path = records_dir / f"{head['venue']}-{head['env']}-{head['run_id']}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


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
        sample_python=sys.executable,
        symbol="SPY",
        poll_seconds=30,
        tt={"TT_ENV": "cert", "TT_CLIENT_SECRET": FAKE_SECRET, "TT_REFRESH_TOKEN": FAKE_REFRESH, "TT_REST_BASE": "http://127.0.0.1:1"},
    )
    s.ensure_dirs()
    return s


def write_experiment(runs_dir: Path, run_id: str, *, config: dict, inputs: dict,
                     summary: list[tuple], checks: dict | None = None) -> Path:
    """検証の実行記録（runs/<実行>/）を 1 つ作る。summary は (手法, 本数, 的中率, IC, 粗利, 純利)。"""
    d = runs_dir / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    (d / "inputs.json").write_text(json.dumps(inputs, ensure_ascii=False), encoding="utf-8")
    (d / "env.json").write_text(json.dumps({"seed": 0, "git_commit": "abc1234",
                                            "started_at": run_id.split("_")[0]},
                                           ensure_ascii=False), encoding="utf-8")
    lines = ["手法,本数,的中率,IC,粗利bp,純利bp,fold数"]
    lines += [",".join(str(x) for x in row) + ",5" for row in summary]
    (d / "summary.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if checks is not None:
        (d / "checks.json").write_text(json.dumps(checks, ensure_ascii=False), encoding="utf-8")
    return d
