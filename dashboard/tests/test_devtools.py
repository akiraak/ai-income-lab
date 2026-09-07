"""モックの起動・停止と手順の実行（本物の資格情報は読まない）。"""

import time

from app.devtools import DevError, DevTools


def test_mock_start_and_step_run(settings):
    dev = DevTools(settings, __import__("app.masking", fromlist=["Redactor"]).Redactor())
    dev.mock.ports = (18765, 18766, 18767)
    dev.mock.start(market_data=True)
    try:
        assert dev.mock.status()["running"] and dev.mock.status()["responding"]
        job = dev.run_step("cert", "1,2,4", 3, use_mock=True)
        for _ in range(100):
            if job.status != "running":
                break
            time.sleep(0.2)
        assert job.status == "done", "\n".join(job.lines)
        assert any("final_Cancelled" in l for l in job.lines)
        assert "MOCK-CLIENT-SECRET" not in "\n".join(job.lines)
        assert list(settings.records_dir.glob("*.jsonl")), "TT_OUT_DIR に記録が落ちる"
        try:
            dev.run_step("prod", "4", 3, use_mock=False)
        except DevError as exc:
            assert "probe" in str(exc)
        else:
            raise AssertionError("prod で手順 4 が通ってはいけない")
    finally:
        dev.mock.stop()
    assert not dev.mock.status()["running"]
