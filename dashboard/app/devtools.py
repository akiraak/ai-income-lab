"""開発時の検証（プラン §2-5。ローカル面だけ）。

- モックサーバ（mock_server.py）の起動・停止・ログ
- selftest.sh の実行と結果表示
- 手順を選んで sample.py を実行し、標準出力をその場で流す。記録は TT_OUT_DIR で管理画面の記録先に落ちる

⚠ prod で通せるのは probe と dryrun だけ（TT_PROD_* を使う読み取り系）。`--i-know-this-is-real-money` は
この画面からは絶対に付けない（本番発注は利用者が CLI で行う）。
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path

import requests

from .config import MOCK_PORTS, Settings
from .masking import Redactor
from .monitor import utcnow_iso

STEP_RE = re.compile(r"^(all|1|2|3|4|5|5limit|6|cleanup|rate|probe|dryrun)(,(all|1|2|3|4|5|5limit|6|cleanup|rate|probe|dryrun))*$")
PROD_STEPS = {"probe", "dryrun"}
STEP_MENU = [
    ("all", "1〜6 を順に（既定 60 秒のストリーミング込み）"),
    ("1", "認証（OAuth2 refresh → access）"),
    ("2", "口座照会"),
    ("3", "現在値（cert は 502 が正常。TT_PROD_* があれば本番でも読む）"),
    ("4", "dry-run → 指値 $10 → 取消"),
    ("5", "成行 1 株 → 建玉 → 反対売買（⚠ 市場時間のみ約定）"),
    ("5limit", "指値 $2 の迂回路（時間外の実験）"),
    ("6", "口座ストリーマ ＋ DXLink"),
    ("cleanup", "働いている注文の取消"),
    ("rate", "レート制限（照会 60 回/分 → 10 回/秒）"),
    ("probe", "本番の読み取りプローブ（TT_PROD_* のみ使用）"),
    ("dryrun", "本番の dry-run（TT_ALLOW_PROD_DRY_RUN=1 が要る）"),
]
SELFTEST_PORTS = (8775, 8776, 8777)


class DevError(Exception):
    pass


class Job:
    def __init__(self, kind: str, argv: list[str], cwd: Path, note: str = "") -> None:
        self.id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.kind = kind
        self.argv = argv
        self.cwd = str(cwd)
        self.note = note
        self.started_at = utcnow_iso()
        self.ended_at: str | None = None
        self.returncode: int | None = None
        self.lines: list[str] = []
        self.proc: subprocess.Popen | None = None
        self.error: str | None = None

    @property
    def status(self) -> str:
        if self.error:
            return "failed"
        if self.returncode is None:
            return "running"
        return "done" if self.returncode == 0 else "failed"

    def brief(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "note": self.note,
            "argv": " ".join(self.argv),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "returncode": self.returncode,
            "status": self.status,
            "line_count": len(self.lines),
            "error": self.error,
        }


class JobRunner:
    def __init__(self, jobs_dir: Path, redactor: Redactor, keep: int = 30) -> None:
        self.jobs_dir = jobs_dir
        self.redactor = redactor
        self.keep = keep
        self.jobs: OrderedDict[str, Job] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def current(self) -> Job | None:
        for job in reversed(self.jobs.values()):
            if job.status == "running":
                return job
        return None

    def start(self, kind: str, argv: list[str], cwd: Path, env: dict, note: str = "") -> Job:
        with self._lock:
            if self.current is not None:
                raise DevError(f"別のジョブが実行中: {self.current.kind} ({self.current.id})")
            job = Job(kind, argv, cwd, note)
            try:
                job.proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            except OSError as exc:
                job.error = f"起動に失敗: {exc}"
                job.ended_at = utcnow_iso()
            self.jobs[job.id] = job
            while len(self.jobs) > self.keep:
                self.jobs.popitem(last=False)
        if job.proc is not None:
            threading.Thread(target=self._pump, args=(job,), daemon=True).start()
        return job

    def _pump(self, job: Job) -> None:
        assert job.proc and job.proc.stdout
        for line in job.proc.stdout:
            job.lines.append(self.redactor.text(line.rstrip("\n")))
            if len(job.lines) > 5000:
                del job.lines[:1000]
        job.proc.wait()
        job.returncode = job.proc.returncode
        job.ended_at = utcnow_iso()
        self._append_history(job)

    def _append_history(self, job: Job) -> None:
        import json

        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        with open(self.jobs_dir / "history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(job.brief(), ensure_ascii=False) + "\n")

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list(self) -> list[dict]:
        return [j.brief() for j in reversed(self.jobs.values())]

    def stop(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if job and job.proc and job.status == "running":
            job.proc.terminate()
            return True
        return False


class MockServer:
    def __init__(self, sample_dir: Path, python: str, log_dir: Path, ports=MOCK_PORTS) -> None:
        self.sample_dir = sample_dir
        self.python = python
        self.ports = ports
        self.log_path = log_dir / "mock.log"
        self.proc: subprocess.Popen | None = None
        self.started_at: str | None = None

    @property
    def rest_base(self) -> str:
        return f"http://127.0.0.1:{self.ports[0]}"

    @property
    def account_streamer(self) -> str:
        return f"ws://127.0.0.1:{self.ports[1]}"

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def probe(self) -> bool:
        """HTTP で何か返れば起動している（認証なしの GET は 401 が正常。selftest.sh と同じ見方）。"""
        try:
            r = requests.get(f"{self.rest_base}/customers/me/accounts", headers={"User-Agent": "ail-dashboard/0.1"}, timeout=1)
            return r.status_code < 500
        except requests.RequestException:
            return False

    def start(self, market_data: bool = True) -> None:
        if self.running():
            raise DevError("モックはもう動いている")
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        argv = [self.python, "mock_server.py", "--port", str(self.ports[0]), "--ws-port", str(self.ports[1]), "--dxlink-port", str(self.ports[2])]
        if market_data:
            argv.append("--market-data")
        log = open(self.log_path, "ab")
        self.proc = subprocess.Popen(argv, cwd=str(self.sample_dir), stdout=log, stderr=subprocess.STDOUT)
        self.started_at = utcnow_iso()
        for _ in range(30):
            if self.probe():
                return
            if not self.running():
                raise DevError("モックが起動直後に落ちた（ログを見る）")
            time.sleep(0.2)
        raise DevError("モックが応答しない")

    def stop(self) -> None:
        if self.proc and self.running():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def status(self) -> dict:
        tail: list[str] = []
        if self.log_path.exists():
            try:
                with open(self.log_path, encoding="utf-8", errors="replace") as f:
                    tail = f.readlines()[-20:]
            except OSError:
                pass
        return {
            "running": self.running(),
            "responding": self.probe() if self.running() else False,
            "pid": self.proc.pid if self.running() else None,
            "ports": {"rest": self.ports[0], "account_streamer": self.ports[1], "dxlink": self.ports[2]},
            "started_at": self.started_at if self.running() else None,
            "log_tail": [l.rstrip("\n") for l in tail],
        }


class DevTools:
    def __init__(self, settings: Settings, redactor: Redactor) -> None:
        self.settings = settings
        self.redactor = redactor
        self.jobs = JobRunner(settings.jobs_dir, redactor)
        self.mock = MockServer(settings.sample_dir, settings.sample_python, settings.jobs_dir)
        self._empty_env_file = Path(tempfile.gettempdir()) / "ail-dashboard-empty.env"
        self._empty_env_file.write_text("", encoding="utf-8")

    def _base_env(self) -> dict:
        env = dict(os.environ)
        env["TT_OUT_DIR"] = str(self.settings.records_dir)
        env["TT_HALT_FILE"] = str(self.settings.halt_file)
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def _mock_env(self, env: dict) -> dict:
        # 手元の .env（本物の資格情報）を読ませない。selftest.sh と同じ
        env["TT_ENV_FILE"] = str(self._empty_env_file)
        env["TT_REST_BASE"] = self.mock.rest_base
        env["TT_ACCOUNT_STREAMER"] = self.mock.account_streamer
        env["TT_PROD_REST_BASE"] = self.mock.rest_base
        env["TT_CLIENT_SECRET"] = "MOCK-CLIENT-SECRET"
        env["TT_REFRESH_TOKEN"] = "MOCK-REFRESH-TOKEN"
        env["TT_PROD_CLIENT_SECRET"] = "MOCK-PROD-SECRET"
        env["TT_PROD_REFRESH_TOKEN"] = "MOCK-PROD-REFRESH"
        for k in ("TT_ENV", "TT_ALLOW_PROD_ORDERS", "TT_ALLOW_PROD_DRY_RUN"):
            env.pop(k, None)
        return env

    def run_selftest(self) -> Job:
        env = self._base_env()
        env.pop("TT_OUT_DIR", None)  # selftest は sample/out の増分を見る
        env.update(PORT=str(SELFTEST_PORTS[0]), WS_PORT=str(SELFTEST_PORTS[1]), DX_PORT=str(SELFTEST_PORTS[2]))
        return self.jobs.start("selftest", ["bash", "./selftest.sh"], self.settings.sample_dir, env, note="モックに 6 手順 ＋ ガード ＋ HALT")

    def run_step(self, env_name: str, step: str, seconds: float, use_mock: bool, verify_expiry: bool = False) -> Job:
        step = step.strip()
        if not STEP_RE.match(step):
            raise DevError("手順の指定が不正")
        steps = set(step.split(","))
        if env_name not in ("cert", "prod"):
            raise DevError("env は cert か prod")
        argv = [self.settings.sample_python, "sample.py", "--step", step, "--seconds", str(max(1.0, min(seconds, 600.0)))]
        env = self._base_env()
        if use_mock:
            if not self.mock.running():
                raise DevError("モックが動いていない。先に起動する")
            env = self._mock_env(env)
            if env_name == "prod":
                raise DevError("モックに対する prod 実行は selftest.sh と同じ手順で cert として通す")
        else:
            if env_name == "prod":
                if not steps <= PROD_STEPS:
                    raise DevError("本番で通せるのは probe と dryrun だけ（読み取り系）。発注は CLI で利用者が行う")
                if "dryrun" in steps:
                    if not self.settings.allow_prod_dry_run:
                        raise DevError("本番 dry-run は TT_ALLOW_PROD_DRY_RUN=1 が要る")
                    argv.append("--allow-prod-dry-run")
            else:
                argv += ["--env", "cert"]
                if steps & PROD_STEPS:
                    pass  # probe / dryrun は TT_PROD_* で動く（sample.py 側の分岐）
        if verify_expiry and "1" in steps:
            argv.append("--verify-expiry")
        note = f"{'mock' if use_mock else env_name} / --step {step}"
        return self.jobs.start("step", argv, self.settings.sample_dir, env, note=note)

    def status(self) -> dict:
        return {"mock": self.mock.status(), "jobs": self.jobs.list(), "current": self.jobs.current.brief() if self.jobs.current else None, "steps": STEP_MENU}
