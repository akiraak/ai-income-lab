#!/usr/bin/env python3
"""シミュレーションの操作の口（⚠ **CLI だけ**。管理画面は表示だけ ＝ live-trading.md §0-7 (b)）。

    python simctl.py status                          # いまのモード・仮の時刻・速さ・何日目
    python simctl.py mode sim sim1                   # 機械をシミュレーションモードへ（何も動いていないときだけ）
    python simctl.py mode real                       # 実売買モードへ戻す（運転手が止まっているときだけ）
    python simctl.py speed 60                        # ×1 ／ ×10 ／ ×60 ／ ×300 ／ ×1440 ／ max
    python simctl.py pause ／ resume ／ step ／ stop     # step ＝ 1 営業日進めて止まる。stop ＝ 運転手を終わらせる（その日の執行器が済んでから）
    python simctl.py check                           # 記録の検査（全行 sim ／ mock ／ test・秘密が出ていない）

`control.json` と `MODE` を書くだけで、運転手（simrun.py）がそれを読み直す。⚠ プロセスを起こさない・殺さない。
⚠ 実売買モードでは、速さ・停止の操作を拒否する（操作する時計が無い）。
"""

from __future__ import annotations

import argparse
import getpass
import glob
import json
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mode as modes  # noqa: E402
import simclock  # noqa: E402

ET = ZoneInfo("America/New_York")
SECRETS = ("MOCK-SECRET", "MOCK-REFRESH", "5WT00042", "eyJ")


def show_status(machine: modes.Mode) -> int:
    lock = modes.RunLock()
    free = lock.acquire(machine.mode, "simctl status")
    holder = None if free else lock.holder()
    lock.release()
    print(f"動いているもの: {'なし' if free else holder}")
    if not machine.is_sim:
        held = modes.open_real_positions()
        print(f"本物の建玉（state/prod）: {', '.join(held) if held else 'なし'}")
        return 0
    control = modes.control_file(machine.name)
    if not os.path.exists(control):
        print(f"仮の時計: まだ無い（simrun.py {machine.name} で始まる）")
        return 0
    ctl = simclock.read_control(control)
    now = datetime.fromtimestamp(simclock.sim_now(ctl, time.time()), ET)
    print(f"仮の時刻: {now:%Y-%m-%d %H:%M:%S} ET   速さ: {'最速' if ctl['speed'] == 'max' else '×' + str(ctl['speed'])}"
          f"{'   ⏸ 停止中' if ctl['paused'] else ''}{'   step ' + str(ctl['step']) if ctl.get('step') else ''}{'   stop 済み' if ctl.get('stop') else ''}")
    status_path = os.path.join(modes.sim_root(machine.name), "sim", "status.json")
    if os.path.exists(status_path):
        with open(status_path, encoding="utf-8") as f:
            st = json.load(f)
        age = time.time() - st.get("updated_at", 0)
        alive = "" if st.get("state") == "終了" or age < 10 else f"   ⚠ 運転手の更新が {age:.0f} 秒前で止まっている"
        print(f"運転手: {st.get('state')}   {st.get('day_index', 0)}/{st.get('days_total', '?')} 日目（仮の {st.get('sim_date')}・出どころ {st.get('source_date')}）"
              f"   直前の run_day rc={st.get('last_rc')}{alive}")
    return 0


def check(machine: modes.Mode) -> int:
    root = modes.sim_root(machine.name)
    rows, bad, leaks = 0, 0, 0
    from _livefs import livefs

    for path in livefs.find(os.path.join(root, "out"), "*/*.jsonl"):     # ⚠ 木の記録は木の sim.sqlite（道はそのまま）
        for line in livefs.read_lines(path):
            rows += 1
            row = json.loads(line)
            bad += not (row.get("sim") is True and row.get("mock") is True and row.get("test") is True)
            leaks += any(s in line for s in SECRETS)
    names = [os.path.basename(p)[:-5] for p in livefs.find(os.path.join(root, "state"), "*/*.json")]
    misnamed = [n for n in names if not n.startswith(modes.SIM_TRADER_PREFIX)]
    print(f"記録 {rows} 行: 印（sim ／ mock ／ test）の無い行 {bad}・秘密の出ている行 {leaks}・sim_ で始まらない状態 {misnamed or 0}")
    return 0 if rows and not bad and not leaks and not misnamed else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="シミュレーションの操作（CLI だけ）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("check")
    m = sub.add_parser("mode")
    m.add_argument("to", choices=["real", "sim"])
    m.add_argument("name", nargs="?")
    m.add_argument("--real-trading-will-stop", action="store_true", help="本物の建玉が残っていてもシミュレーションへ入る（⚠ その間、実売買の執行器は動かない ＝ 手仕舞いも出ない）")
    sp = sub.add_parser("speed")
    sp.add_argument("speed")
    for name in ("pause", "resume", "step", "stop"):
        sub.add_parser(name)
    args = ap.parse_args()

    try:
        if args.cmd == "mode":
            import simdata
            if args.to == "sim" and not os.path.exists(os.path.join(simdata.SIM_CONFIG_DIR, f"{args.name}.toml")):
                print(f"拒否: config/sim/{args.name}.toml が無い", file=sys.stderr)
                return 2
            after = modes.switch(args.to, args.name, by=getpass.getuser(), real_trading_will_stop=args.real_trading_will_stop)
            print(modes.banner(after))
            if after.is_sim:
                print("⚠ この機械では、実売買モードへ戻すまで本物の執行器（run_day.py）は起動しない。戻すのは simctl.py mode real")
            return 0
        machine = modes.read_mode()
    except modes.ModeError as exc:
        print(f"拒否: {exc}", file=sys.stderr)
        return 5
    print(modes.banner(machine))
    if args.cmd == "status":
        return show_status(machine)
    if not machine.is_sim:
        print(f"拒否: 実売買モードでは {args.cmd} は使えない（操作する仮の時計が無い）", file=sys.stderr)
        return 5
    if args.cmd == "check":
        return check(machine)
    control = modes.control_file(machine.name)
    if not os.path.exists(control):
        print(f"拒否: 仮の時計がまだ無い（simrun.py {machine.name} で始まる）", file=sys.stderr)
        return 2
    try:
        if args.cmd == "speed":
            simclock.set_speed(control, args.speed)
        elif args.cmd == "pause":
            simclock.pause(control)
        elif args.cmd == "resume":
            simclock.update_control(control, lambda c: c.update(paused=False, step=0))
        elif args.cmd == "step":
            simclock.update_control(control, lambda c: c.update(paused=False, step=c.get("step", 0) + 1))
        elif args.cmd == "stop":
            simclock.update_control(control, lambda c: c.update(stop=True))
    except ValueError as exc:
        print(f"拒否: {exc}", file=sys.stderr)
        return 2
    return show_status(machine)


if __name__ == "__main__":
    sys.exit(main())
