from app.judge import in_market_hours, judge
from app.records import diff_runs, flatten_metrics, load_runs
from tests.conftest import good_run, make_row, write_run


def test_load_runs_and_mock_flag(tmp_path):
    write_run(tmp_path, good_run())
    write_run(tmp_path, good_run(run_id="20260908T150000Z", at="2026-09-08T15:00:00.000+00:00", mock=True))
    runs = load_runs(tmp_path)
    assert [r.run_id for r in runs] == ["20260908T150000Z", "20260908T140000Z"]  # 新しい順
    assert runs[0].mock and not runs[1].mock
    assert runs[1].ok_count == runs[1].step_count == 9
    assert runs[1].envs == ["cert", "prod"]


def test_flatten_and_diff(tmp_path):
    a = good_run()
    b = good_run(run_id="20260909T140000Z", at="2026-09-09T14:00:00.000+00:00")
    b[4]["detail"]["submit"]["elapsed_ms"] = 95.0
    write_run(tmp_path, a)
    write_run(tmp_path, b)
    runs = {r.run_id: r for r in load_runs(tmp_path)}
    m = flatten_metrics(a[4])
    assert m["dry_run.elapsed_ms"] == 120.5 and m["submit.transitions_after_submit.Live"] == 900.0
    rows = diff_runs(runs["20260908T140000Z"], runs["20260909T140000Z"])
    step4 = next(r for r in rows if r["step"] == 4)
    sub = next(x for x in step4["metrics"] if x["key"] == "submit.elapsed_ms")
    assert sub["a"] == 80.0 and sub["b"] == 95.0 and sub["delta"] == 15.0
    assert next(r for r in rows if r["step"] == 6)["metrics"][-1]["key"] == "messages.count"


def test_market_hours():
    assert in_market_hours({"utc": "2026-09-08T14:00:00+00:00"})  # 火曜 10:00 ET
    assert not in_market_hours({"utc": "2026-09-05T18:00:00+00:00"})  # 土曜
    assert not in_market_hours({"utc": "2026-09-08T21:00:00+00:00"})  # 17:00 ET


def test_judge_all_ok_and_mock_excluded(tmp_path):
    write_run(tmp_path, good_run())
    write_run(tmp_path, good_run(run_id="20260909T140000Z", at="2026-09-09T14:00:00.000+00:00"))
    write_run(tmp_path, good_run(run_id="20260910T140000Z", at="2026-09-10T14:00:00.000+00:00", mock=True))
    result = judge(load_runs(tmp_path), [])
    assert result["venues"] == ["tastytrade"] and result["excluded_mock_runs"] == 1
    cells = result["cells"]["tastytrade"]
    assert {k: cells[k]["mark"] for k in "ABCDEF"} == {"A": "ok", "B": "ok", "C": "ok", "D": "ok", "E": "ok", "F": "ok"}
    assert "2 営業日" in cells["A"]["reason"]
    assert cells["_verdict"].startswith("成立")
    assert all(e["run_id"] for c in cells.values() if isinstance(c, dict) for e in c.get("evidence", []))


def test_judge_partial(tmp_path):
    rows = good_run()
    rows = [r for r in rows if r["step"] not in (5, 7)]
    rows[3]["started_at"]["utc"] = "2026-09-05T18:00:00.000+00:00"  # prod の手順 3 を土曜に
    write_run(tmp_path, rows)
    cells = judge(load_runs(tmp_path), [])["cells"]["tastytrade"]
    assert cells["A"]["mark"] == "na"  # 営業日を跨いでいない
    assert cells["C"]["mark"] == "na" and "参考値" in cells["C"]["reason"]
    assert cells["D"]["mark"] == "warn" and "未実行" in cells["D"]["reason"]
    assert cells["E"]["mark"] == "na"


def test_judge_monitor_events_span_days(tmp_path):
    write_run(tmp_path, good_run())
    events = [{"at": "2026-09-09T13:00:00+00:00", "venue": "tastytrade", "env": "cert", "kind": "refresh_ok", "detail": {}}]
    cells = judge(load_runs(tmp_path), events)["cells"]["tastytrade"]
    assert cells["A"]["mark"] == "ok" and "監視ループ" in cells["A"]["reason"]
    events.append({"at": "2026-09-09T14:00:00+00:00", "venue": "tastytrade", "env": "cert", "kind": "refresh_fail", "detail": {}})
    assert judge(load_runs(tmp_path), events)["cells"]["tastytrade"]["A"]["mark"] == "warn"


def test_judge_failed_step4(tmp_path):
    rows = [make_row(1, "認証", result="authenticated"), make_row(2, "口座"), make_row(4, "指値", ok=False, result="error", detail={}), ]
    rows[2]["error"] = {"type": "ApiError", "code": "preflight_check_failure"}
    write_run(tmp_path, rows)
    cells = judge(load_runs(tmp_path), [])["cells"]["tastytrade"]
    assert cells["D"]["mark"] == "ng" and "preflight_check_failure" in cells["D"]["reason"]
    assert cells["B"]["mark"] == "warn"


def test_judge_ignores_mock_events_and_pseudo_venues(tmp_path):
    events = [
        {"at": "2026-09-09T13:00:00+00:00", "venue": "tastytrade", "env": "cert", "kind": "refresh_ok", "detail": {}, "mock": True},
        {"at": "2026-09-09T13:00:00+00:00", "venue": "dashboard", "env": "-", "kind": "halt", "detail": {}},
    ]
    result = judge(load_runs(tmp_path), events)
    assert result["venues"] == [] and result["monitor_events"] == 1
