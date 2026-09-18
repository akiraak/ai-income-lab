#!/usr/bin/env python3
"""機械の利用状況（GPU・CPU・メモリ・ディスク）を読む。vibeboard の「ハード」タブの読み手。

仕様は docs/specs/dashboard.md §14。⚠ **読むだけ・写すだけ**（閾値の判定は持たない）。

  - GPU は `nvidia-smi` を**固定の引数**で起こして CSV を読む（shell を通さない）
  - CPU・メモリは `/proc`、ディスクは `shutil.disk_usage`
  - ⚠ **WSL2 では GPU のプロセス名とプロセス別メモリが取れない**（`[Not Found]`・`[N/A]`）ので、
    PID だけ受け取って `/proc/<pid>` から引く

⚠ **標準ライブラリだけで書く**（`vibetab.py` と同じ。psutil / pynvml は入れない）。
⚠ **`dashboard/app/` の下に置かない**（`app/` は g3plus に載る。これは開発機の話）。
⚠ **import しただけでは何も走らない**。見張り（`Sampler`）は `start()` を呼んだときだけ回る。
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

DEFAULT_INTERVAL_S = 5.0
MIN_INTERVAL_S = 1.0
HISTORY_POINTS = 720              # 5 秒 × 720 ＝ 1 時間
NVIDIA_SMI_TIMEOUT_S = 3.0
CMDLINE_LIMIT = 160
DISK_MOUNTS = ("/", "/mnt/c")     # 無いものは飛ばす

# `--query-gpu` の列（名前, 型）。⚠ 並びは parse_gpu_csv と共有する
GPU_FIELDS = (
    ("index", int),
    ("name", str),
    ("utilization.gpu", float),
    ("memory.used", float),
    ("memory.total", float),
    ("temperature.gpu", float),
    ("power.draw", float),
    ("power.limit", float),
    ("fan.speed", float),
    ("pstate", str),
)
# 絞りの理由。⚠ **旧名 `clocks_throttle_reasons.*` で引く**（610 系のドライバは新名
# `clocks_event_reasons.*` と両方を受ける【実測 2026-09-18】。古いドライバは旧名しか知らない）
THROTTLE_FIELDS = (
    ("clocks_throttle_reasons.hw_slowdown", "ハードの減速"),
    ("clocks_throttle_reasons.hw_thermal_slowdown", "熱（ハード）"),
    ("clocks_throttle_reasons.sw_thermal_slowdown", "熱（ドライバ）"),
    ("clocks_throttle_reasons.hw_power_brake_slowdown", "電力ブレーキ"),
    ("clocks_throttle_reasons.sw_power_cap", "電力の上限"),
)
GPU_QUERY = ",".join([n for n, _ in GPU_FIELDS] + [n for n, _ in THROTTLE_FIELDS])
GPU_ARGV = ("nvidia-smi", f"--query-gpu={GPU_QUERY}", "--format=csv,noheader,nounits")
APPS_ARGV = ("nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits")

# 履歴に持つ系列（キー, 表示名, 単位, 縦軸の上端。None は値から決める）。⚠ スレッド別は持たない
HISTORY_SERIES = (
    ("gpu_util", "GPU 使用率", "%", 100),
    ("gpu_mem", "GPU メモリ", "%", 100),
    ("gpu_temp", "GPU 温度", "℃", 100),
    ("gpu_power", "GPU 電力", "W", None),
    ("cpu", "CPU 使用率", "%", 100),
    ("mem", "メモリ", "%", 100),
)

_SENSITIVE = re.compile(r"token|secret|passw|credential|api[-_]?key|apikey|(^|[-_])key($|[-_])", re.I)


# ---------------------------------------------------------------- parse（純関数）


def _cell(raw: str, typ):
    """⚠ `[N/A]`・`[Not Supported]`・`[Not Found]` は None（0 にしない）。"""
    s = raw.strip()
    if not s or s.startswith("["):
        return None
    if typ is str:
        return s
    try:
        return typ(float(s)) if typ is int else typ(s)
    except ValueError:
        return None


def parse_gpu_csv(text: str) -> list[dict]:
    """`GPU_ARGV` の出力（GPU 1 枚 1 行）→ dict の一覧。列が足りない行は捨てる。"""
    out = []
    n = len(GPU_FIELDS) + len(THROTTLE_FIELDS)
    for row in csv.reader(text.splitlines(), skipinitialspace=True):
        if len(row) < n:
            continue
        gpu = {name: _cell(row[i], typ) for i, (name, typ) in enumerate(GPU_FIELDS)}
        gpu["throttle"] = [label for j, (_, label) in enumerate(THROTTLE_FIELDS)
                           if row[len(GPU_FIELDS) + j].strip() == "Active"]
        used, total = gpu["memory.used"], gpu["memory.total"]
        gpu["memory.pct"] = round(100.0 * used / total, 1) if used is not None and total else None
        out.append(gpu)
    return out


def parse_compute_apps(text: str) -> list[int]:
    """`APPS_ARGV` の出力 → PID の一覧（重複は除く・順は保つ）。0 件は空。"""
    pids: list[int] = []
    for line in text.splitlines():
        pid = _cell(line.split(",")[0], int)
        if pid is not None and pid not in pids:
            pids.append(pid)
    return pids


def parse_proc_stat(text: str) -> dict[str, tuple[int, int]]:
    """`/proc/stat` → {"cpu": (busy, total), "cpu0": …}。busy ＝ total − idle − iowait。"""
    out: dict[str, tuple[int, int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or not parts[0].startswith("cpu"):
            continue
        try:
            vals = [int(v) for v in parts[1:9]]   # user … steal（guest は user に含まれる）
        except ValueError:
            continue
        if len(vals) < 5:
            continue
        total = sum(vals)
        out[parts[0]] = (total - vals[3] - vals[4], total)
    return out


def _pct(prev: tuple[int, int] | None, now: tuple[int, int] | None) -> float | None:
    if prev is None or now is None:
        return None
    d_total = now[1] - prev[1]
    if d_total <= 0:                       # 同じ時点・巻き戻り。⚠ 0 除算しない
        return None
    return round(max(0.0, min(100.0, 100.0 * (now[0] - prev[0]) / d_total)), 1)


def cpu_usage(prev: dict | None, now: dict) -> dict:
    """2 時点の差から使用率 %。1 時点しか無ければ None。"""
    names = sorted((k for k in now if k != "cpu"), key=lambda k: int(k[3:]))
    if not prev:
        return {"total_pct": None, "per_thread_pct": None, "threads": len(names)}
    return {"total_pct": _pct(prev.get("cpu"), now.get("cpu")),
            "per_thread_pct": [_pct(prev.get(k), now.get(k)) for k in names],
            "threads": len(names)}


def parse_meminfo(text: str) -> dict:
    """`/proc/meminfo` → kB。⚠ 空きは `MemAvailable`（`MemFree` はキャッシュを空きに数えない）。"""
    kv: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        num = rest.split()
        if num and num[0].isdigit():
            kv[key.strip()] = int(num[0])
    total, avail = kv.get("MemTotal"), kv.get("MemAvailable")
    used = total - avail if total is not None and avail is not None else None
    s_total, s_free = kv.get("SwapTotal"), kv.get("SwapFree")
    return {"total_kb": total, "available_kb": avail, "used_kb": used,
            "used_pct": round(100.0 * used / total, 1) if used is not None and total else None,
            "swap_total_kb": s_total,
            "swap_used_kb": s_total - s_free if s_total is not None and s_free is not None else None}


def mask_cmdline(raw: bytes | str, limit: int = CMDLINE_LIMIT) -> str:
    """`/proc/<pid>/cmdline`（NUL 区切り）→ 表示用の 1 行。

    ⚠ **このタブは tailnet の閲覧者にも見える。** 秘密らしい名前の引数は値を伏せる
    （`--token=x`・`API_KEY=x`・`--password x`）。長いものは `limit` 字で切る。
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    args = [a for a in raw.replace("\n", " ").split("\0") if a]
    out: list[str] = []
    hide_next = False
    for a in args:
        if hide_next and not a.startswith("-"):
            out.append("***")
            hide_next = False
            continue
        hide_next = False
        name, eq, _ = a.partition("=")
        if eq and _SENSITIVE.search(name):
            out.append(f"{name}=***")
        else:
            out.append(a)
            hide_next = bool(not eq and a.startswith("-") and _SENSITIVE.search(a))
    s = " ".join(out)
    return s if len(s) <= limit else s[: limit - 1] + "…"


# ---------------------------------------------------------------- 読み出し


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def read_process(pid: int, proc: Path, clk_tck: int | None = None) -> dict:
    """PID → cmdline・経過・RSS。⚠ 消えた PID でも落ちない（値が None になるだけ）。"""
    base = proc / str(pid)
    info = {"pid": pid, "cmdline": None, "elapsed_s": None, "rss_kb": None}
    try:
        raw = (base / "cmdline").read_bytes()
        info["cmdline"] = mask_cmdline(raw) or None
    except OSError:
        return info
    status = _read(base / "status") or ""
    m = re.search(r"^VmRSS:\s+(\d+)", status, re.M)
    if m:
        info["rss_kb"] = int(m.group(1))
    stat, uptime = _read(base / "stat"), _read(proc / "uptime")
    if stat and uptime:
        try:
            # comm は空白や括弧を含み得るので、最後の ')' の後ろから数える（starttime は 22 番目）
            start_ticks = int(stat.rsplit(")", 1)[1].split()[19])
            tck = clk_tck or os.sysconf("SC_CLK_TCK")
            info["elapsed_s"] = max(0, int(float(uptime.split()[0]) - start_ticks / tck))
        except (IndexError, ValueError, OSError):
            pass
    return info


def _run_smi(run, argv) -> tuple[str | None, str | None]:
    """（標準出力, 失敗の理由）。⚠ 失敗しても例外を上げない。"""
    try:
        r = run(list(argv), capture_output=True, text=True, timeout=NVIDIA_SMI_TIMEOUT_S)
    except FileNotFoundError:
        return None, "nvidia-smi が見つからない"
    except subprocess.TimeoutExpired:
        return None, f"nvidia-smi が {NVIDIA_SMI_TIMEOUT_S:g} 秒で返らなかった"
    except OSError as e:
        return None, f"nvidia-smi を起こせない（{e.__class__.__name__}）"
    if r.returncode != 0:
        first = ((r.stderr or r.stdout or "").strip().splitlines() or [""])[0]
        return None, f"nvidia-smi が終了コード {r.returncode}: {first[:120]}"
    return r.stdout, None


def read_gpu(run=subprocess.run, proc: Path = Path("/proc")) -> dict:
    text, err = _run_smi(run, GPU_ARGV)
    if err:
        return {"ok": False, "error": err, "gpus": [], "procs": []}
    gpus = parse_gpu_csv(text)
    if not gpus:
        return {"ok": False, "error": "nvidia-smi の出力を読めない", "gpus": [], "procs": []}
    apps, apps_err = _run_smi(run, APPS_ARGV)
    procs = [read_process(pid, proc) for pid in parse_compute_apps(apps or "")]
    return {"ok": True, "error": None, "gpus": gpus, "procs": procs, "procs_error": apps_err}


def read_disks(mounts=DISK_MOUNTS, disk_usage=shutil.disk_usage) -> list[dict]:
    out = []
    for m in mounts:
        try:
            u = disk_usage(m)
        except OSError:
            continue
        out.append({"mount": m, "total": u.total, "used": u.used, "free": u.free,
                    "used_pct": round(100.0 * u.used / u.total, 1) if u.total else None})
    return out


def read_snapshot(prev_stat: dict | None = None, *, run=subprocess.run, proc: Path = Path("/proc"),
                  mounts=DISK_MOUNTS, disk_usage=shutil.disk_usage,
                  now=time.time) -> tuple[dict, dict]:
    """いまの状態を 1 件。戻り値は（写し, 次回に渡す `/proc/stat` の生の値）。

    ⚠ **GPU が読めなくても CPU・メモリ・ディスクは入れて返す。**
    """
    stat = parse_proc_stat(_read(proc / "stat") or "")
    cpu = cpu_usage(prev_stat, stat)
    load = (_read(proc / "loadavg") or "").split()[:3]
    try:
        cpu["loadavg"] = [float(v) for v in load] if len(load) == 3 else None
    except ValueError:
        cpu["loadavg"] = None
    snap = {"ts": now(), "gpu": read_gpu(run, proc), "cpu": cpu,
            "mem": parse_meminfo(_read(proc / "meminfo") or ""),
            "disks": read_disks(mounts, disk_usage)}
    return snap, stat


def history_point(snap: dict) -> dict:
    g = (snap["gpu"]["gpus"] or [{}])[0]
    return {"ts": snap["ts"], "gpu_util": g.get("utilization.gpu"), "gpu_mem": g.get("memory.pct"),
            "gpu_temp": g.get("temperature.gpu"), "gpu_power": g.get("power.draw"),
            "cpu": snap["cpu"].get("total_pct"), "mem": snap["mem"].get("used_pct")}


# ---------------------------------------------------------------- 見張り


def interval_from_env() -> float:
    try:
        return max(MIN_INTERVAL_S, float(os.environ.get("AIL_HW_INTERVAL_S") or DEFAULT_INTERVAL_S))
    except ValueError:
        return DEFAULT_INTERVAL_S


class Sampler:
    """値を読むのはここ 1 か所。画面は `latest()` の写しを取りに来るだけ。

    ⚠ **`start()` を呼ぶまでスレッドは無い。** 止まっている間の `latest()` は、その場で 1 回読む
    （テストと、見張りの最初の 1 回が終わる前の要求のため）。
    ⚠ **履歴はメモリ上の輪だけ**（ディスクに書かない。プロセスを入れ直すと消える）。
    """

    def __init__(self, read=read_snapshot, interval_s: float | None = None,
                 points: int = HISTORY_POINTS):
        self._read = read
        self.interval_s = interval_s if interval_s is not None else interval_from_env()
        self._lock = threading.Lock()
        self._prev_stat: dict | None = None
        self._latest: dict | None = None
        self._history: deque[dict] = deque(maxlen=points)
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def sample(self) -> dict:
        with self._lock:
            snap, self._prev_stat = self._read(self._prev_stat)
            snap["interval_s"] = self.interval_s
            self._latest = snap
            self._history.append(history_point(snap))
            return snap

    def latest(self) -> dict:
        if self.running and self._latest is not None:
            return self._latest
        return self.sample()

    def history(self) -> dict:
        with self._lock:
            pts = list(self._history)
        return {"interval_s": self.interval_s, "capacity": self._history.maxlen,
                "series": [{"key": k, "label": lb, "unit": u, "max": mx}
                           for k, lb, u, mx in HISTORY_SERIES],
                "points": pts}

    def start(self) -> None:
        if self.running:
            return
        self._thread = threading.Thread(target=self._loop, name="hwstat-sampler", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                self.sample()
            except Exception as e:  # noqa: BLE001（見張りを死なせない。次の回でまた読む）
                print(f"[hwstat] 読み出しに失敗: {e.__class__.__name__}: {e}", flush=True)
            time.sleep(self.interval_s)


if __name__ == "__main__":
    s = Sampler()
    s.sample()
    time.sleep(1.0)                       # CPU 使用率は 2 時点の差
    print(json.dumps(s.sample(), ensure_ascii=False, indent=1))
