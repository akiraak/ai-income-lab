"""夜の予測（2026-11-01。プラン docs/plans/nightly-predict.md §8）の部品。`run-live.sh` が呼ぶ。

    python3 nightly.py models --traders T4,T18            # その人たちの（実験, 手法）を 1 行ずつ（タブ区切り）
    python3 nightly.py models --all-real                  # config/traders/ 直下の本物の人全員（名簿に関係なく ＝ 夜に ▶ 開始した人も翌日に動ける）
    python3 nightly.py done --date D --file <道> --ok ok.tsv --failed failed.tsv   # out/D/predict.done.jsonl に「済み」の 1 行
    python3 nightly.py find --date D --traders @roster    # D の 15:50 に読む予測を探す（前の営業日 P の「済み」の印）→ JSON 1 行・無ければ rc 13

⚠ **読むだけ ／ 足すだけ**（予測は作らない・発注しない）。記録は livefs（道の近くの DB）。
⚠ 印が無い ／ 名簿の人のモデルが欠けている ／ 前の営業日でない ときは発注しない（その場で予測して埋めない ＝ 合図の時刻が混ざる）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as Date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(os.path.dirname(HERE), "tastytrade-api-sample")
sys.path.insert(0, HERE)
sys.path.insert(0, SAMPLE_DIR)

import livefs  # noqa: E402
import market_calendar  # noqa: E402
import roster  # noqa: E402
import trader as traders_mod  # noqa: E402
from mode import SIM_TRADER_PREFIX as SIM_PREFIX  # noqa: E402

DONE = "predict.done.jsonl"
RC_MISSING = 13


def experiment_models(names: list[str], traders_dir: str) -> list[tuple[str, str]]:
    """その人たちの experiment モデルの（実験, 手法）。並びは最初に出た順・重ねない（run-live.sh の今までの拾い方と同じ）。"""
    seen: list[tuple[str, str]] = []
    for name in names:
        t = traders_mod.load_trader(name.strip(), traders_dir)
        for m in t.models:
            if m.kind == "experiment" and (m.name, m.method or "") not in seen:
                seen.append((m.name, m.method or ""))
    return seen


def real_traders(traders_dir: str) -> list[str]:
    """直下の本物の人（試験用・sim の写しを除く）。"""
    out = []
    for f in sorted(os.listdir(traders_dir)):
        if not f.endswith(".toml") or f.startswith(SIM_PREFIX):
            continue
        t = traders_mod.load_trader(f[:-5], traders_dir)
        if not t.test:
            out.append(t.name)
    return out


def names_of(spec: str, halt_file: str | None = None) -> list[str]:
    if spec.strip() == roster.TOKEN:
        return roster.names(roster.roster_path(halt_file) if halt_file else roster.default_path())
    return [n.strip() for n in spec.split(",") if n.strip()]


def _read_tsv(path: str | None) -> list[list[str]]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [line.rstrip("\n").split("\t") for line in f if line.strip()]


def done_row(day: str, predict_file: str, ok: list[list[str]], failed: list[list[str]]) -> dict:
    return {"asof": day, "file": predict_file, "models": [list(m[:2]) + [""] * (2 - len(m[:2])) for m in ok],
            "failed": [list(m[:2]) + [""] * (2 - len(m[:2])) for m in failed],
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def find(out_dir: str, day: str, needed: list[tuple[str, str]], cal=None) -> tuple[dict | None, str]:
    """(使う予測, 理由)。使えなければ (None, 理由)。⚠ 印は前の営業日 P の最後の 1 行だけを見る。"""
    cal = cal or market_calendar.nyse()
    try:
        p = cal.previous_trading_day(Date.fromisoformat(day)).isoformat()
    except ValueError as exc:
        return None, str(exc)
    lines = livefs.read_lines(os.path.join(out_dir, p, DONE), missing_ok=True)
    if not lines:
        return None, f"前の営業日 {p} の夜の予測の「済み」の印が無い"
    row = json.loads(lines[-1])
    if row.get("asof") != p:
        return None, f"印の asof {row.get('asof')} が前の営業日 {p} と違う"
    have = {tuple(m) for m in row.get("models") or []}
    missing = [m for m in needed if tuple(m) not in have]
    if missing:
        return None, f"前の営業日 {p} の夜の予測に、名簿の人のモデルが欠けている: {missing}"
    return {"signal_date": p, "predict": row["file"], "finished_at": row.get("finished_at")}, ""


AFTER_CLOSE_MIN = 15      # 引けの 15 分後から（途中の足で予測しない）


def after_close(d: Date, cal=None, now: datetime | None = None) -> bool:
    """d の引けの 15 分後を過ぎたか（ET）。⚠ 半日立会は 13:00 ET の引け。"""
    from datetime import timedelta
    cal = cal or market_calendar.nyse()
    et = market_calendar.ET
    now = (now or datetime.now(timezone.utc)).astimezone(et)
    close = datetime.combine(d, cal.close_et(d), tzinfo=et) + timedelta(minutes=AFTER_CLOSE_MIN)
    return now >= close


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("models")
    a.add_argument("--traders", default=None)
    a.add_argument("--all-real", action="store_true")
    a.add_argument("--traders-dir", default=os.environ.get("LT_TRADERS_DIR") or os.path.join(HERE, "config", "traders"))
    a.add_argument("--halt-file", default=None)
    b = sub.add_parser("done")
    b.add_argument("--out-dir", default=os.environ.get("LT_OUT_DIR") or os.path.join(HERE, "out"))
    b.add_argument("--date", required=True)
    b.add_argument("--file", required=True)
    b.add_argument("--ok", required=True)
    b.add_argument("--failed", default=None)
    c = sub.add_parser("find")
    c.add_argument("--out-dir", default=os.environ.get("LT_OUT_DIR") or os.path.join(HERE, "out"))
    c.add_argument("--date", required=True)
    c.add_argument("--traders", required=True)
    c.add_argument("--traders-dir", default=os.environ.get("LT_TRADERS_DIR") or os.path.join(HERE, "config", "traders"))
    c.add_argument("--halt-file", default=None)
    sub.add_parser("real-traders").add_argument("--traders-dir", default=os.environ.get("LT_TRADERS_DIR") or os.path.join(HERE, "config", "traders"))
    for name in ("prev", "is-trading-day", "after-close"):
        sub.add_parser(name).add_argument("--date", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "real-traders":
        print(",".join(real_traders(args.traders_dir)))
        return 0
    if args.cmd in ("prev", "is-trading-day", "after-close"):
        cal, d = market_calendar.nyse(), Date.fromisoformat(args.date)
        if args.cmd == "prev":
            print(cal.previous_trading_day(d).isoformat())
            return 0
        if args.cmd == "is-trading-day":
            return 0 if cal.is_trading_day(d) else 1
        return 0 if after_close(d, cal) else 1

    if args.cmd == "models":
        names = real_traders(args.traders_dir) if args.all_real else names_of(args.traders or "", args.halt_file)
        for exp, meth in experiment_models(names, args.traders_dir):
            print(f"{exp}\t{meth}")
        return 0
    if args.cmd == "done":
        row = done_row(args.date, args.file, _read_tsv(args.ok), _read_tsv(args.failed))
        livefs.append(os.path.join(args.out_dir, args.date, DONE), json.dumps(row, ensure_ascii=False))
        print(json.dumps(row, ensure_ascii=False))
        return 0
    names = names_of(args.traders, args.halt_file)
    got, why = find(args.out_dir, args.date, experiment_models(names, args.traders_dir))
    if got is None:
        print(json.dumps({"stop": why}, ensure_ascii=False))
        return RC_MISSING
    print(json.dumps(got, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
