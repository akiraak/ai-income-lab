"""夜の予測（2026-11-01。プラン docs/plans/nightly-predict.md §8）。

固定するもの: 前の営業日（週末・祝日・暦の外は止める）／「済み」の印を探す（無い・asof 違い・名簿の人のモデルが欠けている ＝ 使わない・最後の 1 行だけ見る）／
直下の本物の人全員（試験用・sim の写しを除く）／ run_day.py の歯止め（submit で前の営業日でない signal-date は認証の前に rc 13・
無ければ今までと同じ）／ 引けの 15 分後。⚠ ネットワークは使わない。MODE・run.lock・記録は tmp。
"""
import json
import os
from datetime import date, datetime, timezone

import pytest

import livefs
import market_calendar
import nightly
from tests.test_not_production import CERT, NOWHERE, events, run

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = os.path.join(HERE, "config", "traders")


def test_previous_trading_day_skips_weekends_and_holidays():
    cal = market_calendar.nyse()
    assert cal.previous_trading_day(date(2026, 11, 2)) == date(2026, 10, 30)     # 月 → 金
    assert cal.previous_trading_day(date(2026, 11, 3)) == date(2026, 11, 2)
    assert cal.previous_trading_day(date(2026, 11, 27)) == date(2026, 11, 25)    # 感謝祭の翌日 → 水
    first = date(min(cal.years), 1, 2)
    with pytest.raises(ValueError, match="暦"):
        cal.previous_trading_day(first - __import__("datetime").timedelta(days=10))   # 暦の外へ戻る


def _mark(out, day, models, file="/x/predict.jsonl"):
    livefs.append(os.path.join(out, day, nightly.DONE), json.dumps(nightly.done_row(day, file, [list(m) for m in models], [])))


@pytest.fixture
def out(tmp_path):
    livefs.init(str(tmp_path), "demo")                 # ⚠ 作業用の DB（本物の live.sqlite には入らない）
    d = tmp_path / "out"
    d.mkdir()
    return str(d)


NEED = [("trade_own_fwdmulti_c", "H4"), ("trade_own_ridge_a", "全部使う（基準）")]


def test_find_needs_the_previous_trading_days_mark_with_every_model(out):
    got, why = nightly.find(out, "2026-11-03", NEED)
    assert got is None and "印が無い" in why
    _mark(out, "2026-11-02", NEED[:1])
    got, why = nightly.find(out, "2026-11-03", NEED)
    assert got is None and "欠けている" in why
    _mark(out, "2026-11-02", NEED + [("other", "x")], file="/y/predict-2.jsonl")    # 最後の 1 行が勝つ
    got, why = nightly.find(out, "2026-11-03", NEED)
    assert got == {"signal_date": "2026-11-02", "predict": "/y/predict-2.jsonl", "finished_at": got["finished_at"]} and why == ""
    # 2 日前の印は使わない（前の営業日だけ）
    got, why = nightly.find(out, "2026-11-04", NEED)
    assert got is None and "2026-11-03" in why


def test_find_rejects_a_mark_whose_asof_differs(out):
    livefs.append(os.path.join(out, "2026-11-02", nightly.DONE),
                  json.dumps(nightly.done_row("2026-10-30", "/x", [list(m) for m in NEED], [])))
    got, why = nightly.find(out, "2026-11-03", NEED)
    assert got is None and "asof" in why


def test_real_traders_are_the_non_test_ones_at_the_top():
    names = nightly.real_traders(CONF)
    assert {"T4", "T18", "T19"} <= set(names)
    assert not any(n.startswith("sim_") or n in ("test_a", "mock_a", "mock_b") for n in names)
    models = nightly.experiment_models(["T4", "T18", "T19"], CONF)
    assert len(models) == len(set(models)) == 7


def test_after_close_is_15_minutes_after_the_close():
    cal = market_calendar.nyse()
    et = market_calendar.ET
    d = date(2026, 11, 2)
    assert not nightly.after_close(d, cal, datetime(2026, 11, 2, 16, 14, tzinfo=et))
    assert nightly.after_close(d, cal, datetime(2026, 11, 2, 16, 15, tzinfo=et))
    early = sorted(cal.early_closes)[0]                                            # 半日立会は 13:00 ET の引け
    assert nightly.after_close(early, cal, datetime.combine(early, datetime.min.time(), tzinfo=et).replace(hour=13, minute=15))


# ---------- run_day.py の歯止め（認証の前で止まる ＝ ネットワークは使わない）

SUBMIT = ["--traders", "test_a", "--mode", "submit", "--ignore-window", "--date", "2026-11-03"]


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_MODE_DIR", str(tmp_path))
    return tmp_path


def test_run_day_refuses_a_stale_signal_date_before_auth(base):
    r = run("run_day.py", base, SUBMIT + ["--signal-date", "2026-10-30"], {**CERT, **NOWHERE})
    assert r.returncode == 13 and "前の営業日 2026-11-02" in r.stderr
    ev = [e for e in events(base) if e["kind"] == "refused_stale_predict"]
    assert ev and ev[0]["signal_date"] == "2026-10-30" and ev[0]["expected"] == "2026-11-02"


def test_run_day_with_the_right_signal_date_goes_past_the_guard(base):
    r = run("run_day.py", base, SUBMIT + ["--signal-date", "2026-11-02"], {**CERT, **NOWHERE})
    assert r.returncode != 13 and not [e for e in events(base) if e["kind"] == "refused_stale_predict"]
    start = [e for e in events(base) if e["kind"] == "start"]
    assert start and start[0]["signal_date"] == "2026-11-02"


def test_run_day_without_signal_date_is_unchanged(base):
    r = run("run_day.py", base, SUBMIT, {**CERT, **NOWHERE})
    assert r.returncode != 13
    start = [e for e in events(base) if e["kind"] == "start"]
    assert start and "signal_date" not in start[0]


# ---------- run-live.sh（発注の回）: 前の営業日の印を確かめて執行器に渡す ／ 無ければ rc 13 で執行器を起こさない

import subprocess  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(HERE))


def _live(base, args):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_", "AIL_"))}
    env.update({"TT_ENV_FILE": "/nonexistent", "LT_MODE_DIR": str(base), "LT_OUT_DIR": str(base / "out"),
                "LT_STATE_DIR": str(base / "state" / "x"), "TT_OUT_DIR": str(base / "sample-out"), **NOWHERE})
    return subprocess.run([os.path.join(ROOT, "run-live.sh"), *args], capture_output=True, text=True, env=env, timeout=120)


@pytest.fixture
def live_base(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_MODE_DIR", str(tmp_path))
    livefs.init(str(tmp_path), "demo")
    (tmp_path / "out").mkdir()
    return tmp_path


def test_run_live_refuses_without_last_nights_mark(live_base):
    r = _live(live_base, ["--traders", "T18", "--date", "2026-11-03", "--mode", "plan"])
    assert r.returncode == 13, r.stdout + r.stderr
    assert "夜の予測が使えない" in r.stdout and "執行器（" not in r.stdout


def test_run_live_passes_last_nights_predictions_and_signal_date(live_base):
    out = str(live_base / "out")
    pred = os.path.join(out, "2026-11-02", "predict-x.jsonl")
    need = nightly.experiment_models(["T18"], CONF)
    rows = [{"model": e, "method": m, "symbol": s, "date": "2026-11-02", "buy": 60.0, "exit": 40.0}
            for e, m in need for s in ("T", "PFE", "NKE", "VZ", "BAC")]
    livefs.append_many(pred, [json.dumps(r, ensure_ascii=False) for r in rows])
    _mark(out, "2026-11-02", need, file=pred)
    r = _live(live_base, ["--traders", "T18", "--date", "2026-11-03", "--mode", "plan"])
    assert "夜の予測（asof 2026-11-02）を読む" in r.stdout and "執行器（--mode plan）" in r.stdout, r.stdout + r.stderr
    start = [e for e in events(live_base) if e["kind"] == "start"]
    assert start and start[0]["signal_date"] == "2026-11-02"
    # ⚠ plan でも認証が要るので、合図を読むところは下の signals.collect で確かめる


def test_signals_are_read_by_the_signal_date(live_base):
    """夜の予測の行（date ＝ 前の営業日）は signal_date で引けば読め、今日の日付で引けば読まない（古い予測で売買しない仕組みはそのまま）。"""
    import signals
    from trader import load_traders
    pred = os.path.join(str(live_base / "out"), "2026-11-02", "predict-x.jsonl")
    need = nightly.experiment_models(["T18"], CONF)
    livefs.append_many(pred, [json.dumps({"model": e, "method": m, "symbol": s, "date": "2026-11-02", "buy": 60.0, "exit": 40.0}, ensure_ascii=False)
                              for e, m in need for s in ("T", "PFE", "NKE", "VZ", "BAC")])
    t18 = load_traders(["T18"], CONF)
    sigs, ev = signals.collect(t18, "2026-11-02", pred)
    assert len(sigs) == 5 and not [e for e in ev if e["kind"] == "no_signal"]
    sigs, ev = signals.collect(t18, "2026-11-03", pred)
    assert not sigs and len([e for e in ev if e["kind"] == "no_signal"]) == 5


def test_run_live_same_day_refuses_submit(live_base):
    r = _live(live_base, ["--traders", "T18", "--same-day", "--mode", "submit", "--date", "2026-11-03"])
    assert r.returncode == 64 and "plan ／ dry-run だけ" in r.stdout


# ---------- 夜の段の終値の控え（docs/plans/closes-snapshot-night.md・2026-11-09）
# run-live.sh の --predict-only の塊だけを切り出し、python ・ live_update.sh ・ run_predict を偽物にして流す（時刻 ・ ネットワークに頼らない）

FAKE_PY = r"""#!/usr/bin/env bash
echo "PY $*" >> "$CALLS"
case "$*" in
  *"paper.py --snapshot-only"*) exit "${PAPER_RC:-0}" ;;
  *"models --all-real"*) printf 'exp_a\tm\n' ;;
esac
exit 0
"""


def _night_block(tmp_path, update_rc=0, paper_rc=0):
    text = open(os.path.join(ROOT, "run-live.sh"), encoding="utf-8").read()
    start = text.index('if [ "$PREDICT_ONLY" = 1 ]; then')
    end = text.index('[ -n "$TRADERS" ] || {', start)
    fd, lt = tmp_path / "fd", tmp_path / "lt"
    fd.mkdir(); lt.mkdir()
    (fd / "live_update.sh").write_text(f'#!/usr/bin/env bash\necho "UPDATE" >> "$CALLS"\nexit {update_rc}\n')
    (fd / "live_update.sh").chmod(0o755)
    py = tmp_path / "py"
    py.write_text(FAKE_PY); py.chmod(0o755)
    script = tmp_path / "night.sh"
    script.write_text("\n".join([
        "#!/usr/bin/env bash", "T0=$(date +%s)", 'say() { echo "SAY $*"; }',
        'run_predict() { echo "PREDICT $2" >> "$CALLS"; return 0; }',
        f'PREDICT_ONLY=1; SKIP_UPDATE=0; DATE=""; STEPS=""; FD="{fd}"; LT="{lt}"; PY_LT="{py}"; NIGHTLY=nightly.py; ROOT="{tmp_path}"',
        text[start:end], ""]))
    calls = tmp_path / "calls.txt"
    env = {**{k: v for k, v in os.environ.items() if not k.startswith(("LT_", "AIL_"))}, "CALLS": str(calls), "PAPER_RC": str(paper_rc)}
    p = subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=60)
    return p, calls.read_text(encoding="utf-8").splitlines()


def test_night_snapshots_closes_after_the_update_and_before_predicting(tmp_path):
    p, calls = _night_block(tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    i_up = calls.index("UPDATE")
    i_paper = next(i for i, c in enumerate(calls) if "paper.py --snapshot-only" in c)
    i_pred = next(i for i, c in enumerate(calls) if c.startswith("PREDICT "))
    assert i_up < i_paper < i_pred
    assert "--bars-dir" in calls[i_paper] and calls[i_paper].endswith("/adjusted/d")
    assert "終値の控え → closes.json" in p.stdout


def test_a_failed_snapshot_does_not_stop_the_prediction(tmp_path):
    p, calls = _night_block(tmp_path, paper_rc=1)
    assert p.returncode == 0, p.stdout + p.stderr
    assert any(c.startswith("PREDICT ") for c in calls)
    assert "終値の控えに失敗" in p.stdout


def test_no_snapshot_when_the_update_failed(tmp_path):
    p, calls = _night_block(tmp_path, update_rc=1)
    assert p.returncode == 10
    assert not any("paper.py" in c for c in calls) and not any(c.startswith("PREDICT ") for c in calls)
