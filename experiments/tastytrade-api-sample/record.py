"""6 手順の実行を JSON Lines で 1 手順 1 行に落とす記録器。

プラン docs/plans/tastytrade-api-sample.md §2-1 の記録形式:
  手順番号・会場・環境・開始/終了時刻・所要 ms・結果・生レスポンスの抜粋・SDK の版。
口座番号とトークンは書き出す直前にマスクする（§7-2）。
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
PT = ZoneInfo("America/Los_Angeles")

# 値を登録しなくても消しておきたいもの（JWT と DXLink トークン）
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]+")


class Masker:
    """実行中に見つけた秘密（トークン・口座番号）を控え、書き出す前に置き換える。"""

    def __init__(self) -> None:
        self._secrets: list[tuple[str, str]] = []

    def add(self, value: str | None, placeholder: str) -> None:
        if not value or not isinstance(value, str) or len(value) < 4:
            return
        if any(v == value for v, _ in self._secrets):
            return
        self._secrets.append((value, placeholder))
        # 長いものから消さないと部分一致で崩れる
        self._secrets.sort(key=lambda pair: len(pair[0]), reverse=True)

    def add_account(self, account_number: str | None) -> None:
        self.add(account_number, "<account:masked>")

    def text(self, s: str) -> str:
        for value, placeholder in self._secrets:
            if value in s:
                s = s.replace(value, placeholder)
        return _JWT_RE.sub("<jwt:masked>", s)

    def __call__(self, obj):
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {self.text(str(k)): self(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self(v) for v in obj]
        return obj


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> dict:
    return {
        "utc": dt.isoformat(timespec="milliseconds"),
        "et": dt.astimezone(ET).isoformat(timespec="milliseconds"),
        "pt": dt.astimezone(PT).isoformat(timespec="milliseconds"),
    }


def sdk_versions() -> dict:
    """観点 F の材料。使ったライブラリの版を記録に残す。"""
    versions = {"python": platform.python_version()}
    for name in ("requests", "websockets"):
        try:
            module = __import__(name)
            versions[name] = getattr(module, "__version__", "unknown")
        except ImportError:
            versions[name] = "not installed"
    versions["sdk"] = "none (REST/websocket を直接叩く)"
    return versions


class Recorder:
    """1 実行 = 1 ファイル。手順ごとに 1 行を追記する。"""

    def __init__(self, out_dir: str, venue: str, env: str, run_id: str | None = None, mock: bool = False) -> None:
        os.makedirs(out_dir, exist_ok=True)
        self.venue = venue
        self.env = env
        # 接続先をモックに差し替えた実行は【実測】ではない。行に mock: true を残し、判定から外せるようにする（2026-09-05）
        self.mock = mock
        self.run_id = run_id or _now().strftime("%Y%m%dT%H%M%SZ")
        self.path = os.path.join(out_dir, f"{venue}-{env}-{self.run_id}.jsonl")
        self.mask = Masker()
        self._sdk = sdk_versions()

    def write(self, row: dict) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(self.mask(row), ensure_ascii=False) + "\n")

    @contextmanager
    def step(self, number: int, name: str, **extra):
        """`with rec.step(2, "口座照会") as r:` の中で r["detail"] などを埋める。

        例外は握りつぶさずに記録してから再送出する（動かなかった理由を残すため。§7-4）。
        """
        started = _now()
        started_perf = time.perf_counter()
        row: dict = {
            "venue": self.venue,
            "run_id": self.run_id,
            "step": number,
            "name": name,
            "env": self.env,
            "started_at": _stamp(started),
            "sdk": self._sdk,
            "result": None,
            "detail": {},
            **extra,
        }
        if self.mock:
            row["mock"] = True
        print(f"[step {number}] {name} ... ", end="", flush=True)
        try:
            yield row
        except Exception as exc:  # 記録してから落とす
            row["ok"] = False
            row["result"] = row.get("result") or "error"
            row["error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc().splitlines()[-6:],
            }
            # API のエラー封筒（error.errors[] に本当の理由が入る）を落とさない
            for attr in ("status", "code", "body"):
                if hasattr(exc, attr):
                    row["error"][attr] = excerpt(getattr(exc, attr), limit=12)
            print(f"NG ({type(exc).__name__}: {exc})", file=sys.stderr)
            raise
        else:
            row.setdefault("ok", True)
            row["result"] = row.get("result") or "ok"
            print(f"{'OK' if row['ok'] else '--'} ({row['result']})")
        finally:
            ended = _now()
            row["ended_at"] = _stamp(ended)
            row["elapsed_ms"] = round((time.perf_counter() - started_perf) * 1000, 1)
            self.write(row)


def excerpt(obj, limit: int = 24):
    """生レスポンスの抜粋。長い配列・辞書は落として記録を読める大きさに保つ。"""
    if isinstance(obj, dict):
        out = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= limit:
                out["..."] = f"(+{len(obj) - limit} keys)"
                break
            out[k] = excerpt(v, limit)
        return out
    if isinstance(obj, list):
        head = [excerpt(v, limit) for v in obj[:3]]
        if len(obj) > 3:
            head.append(f"(+{len(obj) - 3} items)")
        return head
    if isinstance(obj, str) and len(obj) > 400:
        return obj[:400] + f"...(+{len(obj) - 400} chars)"
    return obj
