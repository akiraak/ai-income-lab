"""ハードの読み手（dashboard/hwstat.py）。⚠ **GPU・nvidia-smi・本物の /proc に頼らない。**

実行（`run`）と `/proc` の場所を差し替えて、WSL2 の【実測 2026-09-18】の出力をそのまま通す。
"""

from __future__ import annotations

import subprocess
import threading
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

import hwstat

GPU_LINE = ("0, NVIDIA GeForce RTX 3090 Ti, 34, 1294, 24564, 49, 63.71, 480.00, 31, P3, "
            "Not Active, Not Active, Not Active, Not Active, Not Active\n")
APPS_LINE = "203676, [Not Found], [N/A]\n"

STAT_1 = """cpu  1000 0 500 8000 500 0 0 0 0 0
cpu0 600 0 300 3800 300 0 0 0 0 0
cpu1 400 0 200 4200 200 0 0 0 0 0
intr 12345
"""
STAT_2 = """cpu  1300 0 600 8500 600 0 0 0 0 0
cpu0 800 0 350 4050 300 0 0 0 0 0
cpu1 500 0 250 4450 300 0 0 0 0 0
intr 12399
"""
MEMINFO = """MemTotal:       49325752 kB
MemFree:        34000000 kB
MemAvailable:   45606440 kB
SwapTotal:      12582912 kB
SwapFree:       12582912 kB
"""

Usage = namedtuple("Usage", "total used free")


def fake_run(gpu_out: str = GPU_LINE, apps_out: str = APPS_LINE):
    def run(argv, **kw):
        assert argv[0] == "nvidia-smi" and "shell" not in kw
        out = apps_out if any("compute-apps" in a for a in argv) else gpu_out
        return SimpleNamespace(returncode=0, stdout=out, stderr="")
    return run


@pytest.fixture()
def proc(tmp_path: Path) -> Path:
    p = tmp_path / "proc"
    p.mkdir()
    (p / "stat").write_text(STAT_1)
    (p / "loadavg").write_text("1.04 1.00 1.00 2/714 205958\n")
    (p / "meminfo").write_text(MEMINFO)
    (p / "uptime").write_text("161425.34 5000000.00\n")
    d = p / "203676"
    d.mkdir()
    (d / "cmdline").write_bytes(b"/venv/bin/python\0-m\0cli.run\0--experiment\0trade_x\0")
    (d / "status").write_text("Name:\tpython\nVmRSS:\t 2380300 kB\n")
    # comm に空白と括弧があっても starttime（22 番目）を取り違えない
    (d / "stat").write_text("203676 (py thon) x) S " + " ".join(["0"] * 18) + " 15905594 0 0\n")
    return p


def snapshot(proc, prev=None, run=None, **kw):
    return hwstat.read_snapshot(prev, run=run or fake_run(), proc=proc, mounts=("/",),
                                disk_usage=lambda m: Usage(1000, 160, 840), now=lambda: 1.0, **kw)


# ---------------------------------------------------------------- parse


def test_parse_gpu_csv_measured_line():
    (g,) = hwstat.parse_gpu_csv(GPU_LINE)
    assert g["index"] == 0 and g["name"] == "NVIDIA GeForce RTX 3090 Ti"
    assert g["utilization.gpu"] == 34.0 and g["memory.used"] == 1294.0
    assert g["power.draw"] == 63.71 and g["pstate"] == "P3"
    assert g["memory.pct"] == 5.3 and g["throttle"] == []


def test_parse_gpu_csv_na_is_none_not_zero():
    line = GPU_LINE.replace(", 31, P3", ", [N/A], P3").replace("63.71", "[Not Supported]")
    (g,) = hwstat.parse_gpu_csv(line)
    assert g["fan.speed"] is None and g["power.draw"] is None


def test_parse_gpu_csv_throttle_and_garbage():
    line = GPU_LINE.replace("Not Active, Not Active, Not Active, Not Active, Not Active",
                            "Not Active, Active, Not Active, Not Active, Active")
    (g,) = hwstat.parse_gpu_csv(line)
    assert g["throttle"] == ["熱（ハード）", "電力の上限"]
    assert hwstat.parse_gpu_csv("") == [] and hwstat.parse_gpu_csv("No devices were found\n") == []


def test_parse_compute_apps():
    assert hwstat.parse_compute_apps(APPS_LINE) == [203676]
    assert hwstat.parse_compute_apps("") == []
    assert hwstat.parse_compute_apps("12, a, 1\n12, a, 1\n[N/A]\n7\n") == [12, 7]


def test_cpu_usage_two_points():
    u = hwstat.cpu_usage(hwstat.parse_proc_stat(STAT_1), hwstat.parse_proc_stat(STAT_2))
    # 全体: busy 1500→1900、total 10000→11000 ＝ 40%
    assert u["total_pct"] == 40.0 and u["threads"] == 2
    assert u["per_thread_pct"] == [50.0, 30.0]


def test_cpu_usage_one_point_and_no_delta():
    now = hwstat.parse_proc_stat(STAT_1)
    assert hwstat.cpu_usage(None, now) == {"total_pct": None, "per_thread_pct": None, "threads": 2}
    same = hwstat.cpu_usage(now, now)                       # ⚠ 0 除算しない
    assert same["total_pct"] is None and same["per_thread_pct"] == [None, None]


def test_parse_meminfo_uses_available():
    m = hwstat.parse_meminfo(MEMINFO)
    assert m["used_kb"] == 49325752 - 45606440 and m["used_pct"] == 7.5
    assert m["swap_used_kb"] == 0
    no_swap = hwstat.parse_meminfo("MemTotal: 100 kB\nMemAvailable: 40 kB\n")
    assert no_swap["used_pct"] == 60.0 and no_swap["swap_total_kb"] is None
    assert hwstat.parse_meminfo("")["used_pct"] is None


@pytest.mark.parametrize("raw, shown, hidden", [
    (b"app\0--token=abc123\0run", "--token=***", "abc123"),
    (b"env\0API_KEY=abc123\0app", "API_KEY=***", "abc123"),
    (b"app\0--password\0hunter2\0--port\x003015", "--password *** --port 3015", "hunter2"),
    (b"app\0--client-secret=zzz", "--client-secret=***", "zzz"),
])
def test_mask_cmdline_hides_secrets(raw, shown, hidden):
    s = hwstat.mask_cmdline(raw)
    assert shown in s and hidden not in s


def test_mask_cmdline_keeps_ordinary_args_and_truncates():
    assert hwstat.mask_cmdline(b"python\0-m\0cli.run\0--keyboard=us\0") == "python -m cli.run --keyboard=us"
    long = hwstat.mask_cmdline(b"x" * 500)
    assert len(long) == hwstat.CMDLINE_LIMIT and long.endswith("…")


# ---------------------------------------------------------------- 読み出し


def test_read_snapshot_ok(proc):
    snap, stat = snapshot(proc)
    assert snap["gpu"]["ok"] and snap["gpu"]["gpus"][0]["temperature.gpu"] == 49.0
    (p,) = snap["gpu"]["procs"]
    assert p["pid"] == 203676 and p["rss_kb"] == 2380300
    assert p["cmdline"] == "/venv/bin/python -m cli.run --experiment trade_x"
    assert p["elapsed_s"] is not None                       # 値は clk_tck を固定した下のテストで見る
    assert snap["cpu"]["loadavg"] == [1.04, 1.0, 1.0] and snap["cpu"]["total_pct"] is None
    assert snap["disks"] == [{"mount": "/", "total": 1000, "used": 160, "free": 840, "used_pct": 16.0}]
    (proc / "stat").write_text(STAT_2)
    snap2, _ = snapshot(proc, prev=stat)
    assert snap2["cpu"]["total_pct"] == 40.0


def test_process_elapsed_uses_clk_tck(proc):
    info = hwstat.read_process(203676, proc, clk_tck=100)
    assert info["elapsed_s"] == int(161425.34 - 159055.94)


def _raise(exc):
    def run(argv, **kw):
        raise exc
    return run


@pytest.mark.parametrize("run, needle", [
    (_raise(FileNotFoundError()), "見つからない"),
    (_raise(subprocess.TimeoutExpired("nvidia-smi", 3)), "返らなかった"),
    (lambda argv, **kw: SimpleNamespace(returncode=9, stdout="", stderr="NVIDIA-SMI has failed\nmore"),
     "終了コード 9: NVIDIA-SMI has failed"),
])
def test_gpu_failure_keeps_the_rest(proc, run, needle):
    snap, _ = snapshot(proc, run=run)
    assert snap["gpu"]["ok"] is False and needle in snap["gpu"]["error"]
    assert snap["gpu"]["gpus"] == [] and snap["gpu"]["procs"] == []
    # ⚠ GPU が読めなくても他の節は入っている
    assert snap["mem"]["total_kb"] == 49325752 and snap["disks"] and snap["cpu"]["threads"] == 2


def test_vanished_pid_does_not_crash(proc):
    snap, _ = snapshot(proc, run=fake_run(apps_out="999999, [Not Found], [N/A]\n"))
    assert snap["gpu"]["procs"] == [{"pid": 999999, "cmdline": None, "elapsed_s": None, "rss_kb": None}]


def test_missing_mount_is_skipped():
    def du(m):
        if m == "/mnt/c":
            raise FileNotFoundError(m)
        return Usage(10, 9, 1)
    assert [d["mount"] for d in hwstat.read_disks(("/", "/mnt/c"), du)] == ["/"]


# ---------------------------------------------------------------- 見張り


def make_sampler(proc, **kw) -> hwstat.Sampler:
    return hwstat.Sampler(read=lambda prev: snapshot(proc, prev=prev), interval_s=5.0, **kw)


def test_import_and_construct_start_no_thread(proc):
    before = {t.name for t in threading.enumerate()}
    s = make_sampler(proc)
    assert not s.running and "hwstat-sampler" not in before
    assert "hwstat-sampler" not in {t.name for t in threading.enumerate()}
    # 止まっていても latest() はその場で読んで返す（2 回目で CPU の差が出る）
    assert s.latest()["cpu"]["total_pct"] is None
    (proc / "stat").write_text(STAT_2)
    assert s.latest()["cpu"]["total_pct"] == 40.0 and s.latest()["interval_s"] == 5.0


def test_history_is_a_bounded_ring(proc):
    s = make_sampler(proc, points=3)
    assert s.history()["points"] == []
    for _ in range(5):
        s.sample()
    h = s.history()
    assert len(h["points"]) == 3 and h["capacity"] == 3
    assert h["points"][-1]["gpu_util"] == 34.0 and h["points"][-1]["mem"] == 7.5
    assert [x["key"] for x in h["series"]] == ["gpu_util", "gpu_mem", "gpu_temp", "gpu_power", "cpu", "mem"]


def test_history_point_without_gpu(proc):
    snap, _ = snapshot(proc, run=_raise(FileNotFoundError()))
    pt = hwstat.history_point(snap)
    assert pt["gpu_util"] is None and pt["mem"] == 7.5


def test_interval_from_env(monkeypatch):
    monkeypatch.setenv("AIL_HW_INTERVAL_S", "0.1")
    assert hwstat.interval_from_env() == hwstat.MIN_INTERVAL_S
    monkeypatch.setenv("AIL_HW_INTERVAL_S", "nazo")
    assert hwstat.interval_from_env() == hwstat.DEFAULT_INTERVAL_S
