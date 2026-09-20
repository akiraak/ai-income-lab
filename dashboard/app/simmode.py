"""機械のモード（実売買 ／ シミュレーション）を読む。⚠ **読むだけ・表示だけ**（切り替え・速さ・停止は CLI の `simctl.py`。dashboard.md §13）。

正本は執行器（`experiments/live-trading/`）が持つ:
  - `MODE`                          … `{"mode": "real" ／ "sim", "name": …, "since": …}`。⚠ **無ければ real**
  - `sim/<名前>/sim/control.json`   … 仮の時計（仮のいま ＝ sim_epoch ＋ 実時間の経過 × speed。`simclock.py` と同じ式）
  - `sim/<名前>/sim/status.json`    … 運転手の状態（何日目・窓の中 ／ 外）

⚠ **1 つの画面に本物とシミュレーションを混ぜない**: 読む記録はモードから決まる（real ＝ 本物の記録 ／ sim ＝ `sim/<名前>/`）。
   `AIL_LIVE_DIR` を手で指定していて、木の種類がモードと食い違うときは、数字を出さずに「食い違っている」とだけ出す。
⚠ 標準ライブラリだけ。どのファイルが無くても・壊れていても落とさない。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
DRIVER_STALE_S = 10.0


def _json(path: Path) -> dict | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def read_mode(mode_dir: Path | None) -> dict:
    """{"mode": "real"} ／ {"mode": "sim", "name", "since"} ／ {"mode": "unknown", "error"}（MODE が壊れている）。"""
    if mode_dir is None or not (mode_dir / "MODE").exists():
        return {"mode": "real"}
    doc = _json(mode_dir / "MODE")
    if not doc or doc.get("mode") not in ("real", "sim"):
        return {"mode": "unknown", "error": "MODE が読めない（執行器も運転手も起動を拒否している。直すか消す）"}
    if doc["mode"] == "real":
        return {"mode": "real", "since": doc.get("since")}
    if not NAME_RE.match(str(doc.get("name") or "")):
        return {"mode": "unknown", "error": "MODE のシミュレーションの名前が不正"}
    return {"mode": "sim", "name": doc["name"], "since": doc.get("since")}


def is_sim_tree(path: Path) -> bool:
    return (path / "sim" / "control.json").exists() or (path / "sim" / "status.json").exists()


def clock(root: Path, real_now: float | None = None) -> dict | None:
    """仮の時計のいま。control.json が無ければ None（運転手をまだ 1 度も起こしていない）。"""
    ctl = _json(root / "sim" / "control.json")
    if not ctl:
        return None
    try:
        moving = not ctl["paused"] and ctl["speed"] != "max"
        now = ctl["sim_epoch"] + (((real_now or time.time()) - ctl["real_epoch"]) * ctl["speed"] if moving else 0)
        now_et = datetime.fromtimestamp(now, ET)
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return None
    return {"now_et": now_et.isoformat(timespec="seconds"), "today": now_et.date().isoformat(), "speed": ctl["speed"], "paused": bool(ctl["paused"]),
            "speed_label": "最速" if ctl["speed"] == "max" else f"×{ctl['speed']}"}


def resolve(mode_dir: Path | None, live_dir: Path, live_dir_explicit: bool) -> dict:
    """いまのモードと、画面が読む記録の木。`mismatch` があるとき、画面は数字を出さない。"""
    m = read_mode(mode_dir)
    out = {"mode": m["mode"], "name": m.get("name"), "since": m.get("since"), "live_dir": live_dir, "mismatch": m.get("error"), "sim": None}
    if m["mode"] == "sim":
        root = mode_dir / "sim" / m["name"]
        out["halt_file"] = root / "HALT"
        if not live_dir_explicit:
            out["live_dir"] = root
        elif not is_sim_tree(live_dir):
            out["mismatch"] = "機械はシミュレーションモードだが、AIL_LIVE_DIR はシミュレーションの記録ではない"
        st = _json(out["live_dir"] / "sim" / "status.json") or {}
        c = clock(out["live_dir"]) or {}
        age = time.time() - float(st.get("updated_at") or 0)
        out["sim"] = {"name": m["name"], **c, "state": st.get("state"), "day_index": st.get("day_index"), "days_total": st.get("days_total"),
                      "sim_date": st.get("sim_date"), "source_date": st.get("source_date"), "last_rc": st.get("last_rc"),
                      "driver_alive": bool(st) and st.get("state") != "終了" and age < DRIVER_STALE_S, "started": bool(c)}
    elif m["mode"] == "real" and live_dir_explicit and is_sim_tree(live_dir):
        out["mismatch"] = "機械は実売買モードだが、AIL_LIVE_DIR はシミュレーションの記録を指している"
    return out


def public(info: dict) -> dict:
    """`/api/*` に足す形（パスは出さない）。"""
    out = {"mode": info["mode"]}
    if info["mode"] == "sim":
        out["sim"] = {k: v for k, v in (info["sim"] or {}).items()}
    if info["mismatch"]:
        out["mode_mismatch"] = info["mismatch"]
    return out
