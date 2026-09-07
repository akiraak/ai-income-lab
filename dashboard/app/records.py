"""実行記録（out/*.jsonl）の読み取り。一覧・1 実行の詳細・実行間の差分。

記録形式は record.py（1 手順 1 行。venue / run_id / step / name / env / started_at / ended_at / elapsed_ms /
ok / result / detail / sdk、モックなら mock: true）。ファイル名は `<venue>-<env>-<run_id>.jsonl` だが、
中身の列を正として読む（他社を足したときにファイル名の規則が変わっても壊れない）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

SKIP_LISTS = {"messages", "events_head", "events_tail", "warnings", "errors", "cases", "response_keys"}


@dataclass
class Run:
    run_id: str
    venue: str
    env: str
    mock: bool
    path: Path
    rows: list[dict] = field(default_factory=list)

    @property
    def started(self) -> dict:
        first = self.rows[0] if self.rows else {}
        return first.get("started_at") or {}

    @property
    def envs(self) -> list[str]:
        seen: list[str] = []
        for r in self.rows:
            e = r.get("env")
            if e and e not in seen:
                seen.append(e)
        return seen

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.rows if r.get("ok"))

    @property
    def step_count(self) -> int:
        return len(self.rows)

    @property
    def elapsed_ms(self) -> float:
        return round(sum(float(r.get("elapsed_ms") or 0) for r in self.rows), 1)

    @property
    def sdk(self) -> dict:
        return (self.rows[0].get("sdk") if self.rows else None) or {}

    def step(self, number: int, env: str | None = None) -> dict | None:
        for r in self.rows:
            if r.get("step") == number and (env is None or r.get("env") == env):
                return r
        return None

    def summary(self) -> dict:
        return {
            "run_id": self.run_id,
            "venue": self.venue,
            "env": self.env,
            "envs": self.envs,
            "mock": self.mock,
            "file": self.path.name,
            "started_at": self.started,
            "step_count": self.step_count,
            "ok_count": self.ok_count,
            "elapsed_ms": self.elapsed_ms,
            "steps": [step_brief(r) for r in self.rows],
        }


def step_brief(row: dict) -> dict:
    return {
        "step": row.get("step"),
        "name": row.get("name"),
        "env": row.get("env"),
        "ok": row.get("ok"),
        "result": row.get("result"),
        "elapsed_ms": row.get("elapsed_ms"),
    }


@lru_cache(maxsize=256)
def _read_file(path_str: str, mtime_ns: int, size: int) -> tuple:
    rows = []
    with open(path_str, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"step": None, "name": "(壊れた行)", "ok": False, "result": "unparsable", "raw": line[:200]})
    return tuple(rows)


def load_runs(records_dir: Path) -> list[Run]:
    """新しい順。ファイルの mtime・サイズが変わらなければキャッシュを使う。"""
    runs: list[Run] = []
    if not records_dir.is_dir():
        return runs
    for path in records_dir.glob("*.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        rows = list(_read_file(str(path), st.st_mtime_ns, st.st_size))
        if not rows:
            continue
        head = next((r for r in rows if r.get("run_id")), rows[0])
        stem_parts = path.stem.split("-")
        run_id = head.get("run_id") or (stem_parts[-1] if stem_parts else path.stem)
        venue = head.get("venue") or (stem_parts[0] if stem_parts else "unknown")
        env = head.get("env") or (stem_parts[1] if len(stem_parts) > 2 else "unknown")
        mock = any(r.get("mock") is True for r in rows)
        runs.append(Run(run_id=run_id, venue=venue, env=env, mock=mock, path=path, rows=rows))
    runs.sort(key=lambda r: (r.started.get("utc") or "", r.run_id), reverse=True)
    return runs


def find_run(runs: list[Run], run_id: str) -> Run | None:
    for r in runs:
        if r.run_id == run_id or r.path.name == run_id:
            return r
    return None


# ---------------------------------------------------------------- 差分


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def flatten_metrics(row: dict) -> dict[str, float]:
    """detail の数値を `dotted.path` で平らにする。状態遷移の列は `path.<status>` = at_ms にする。"""
    out: dict[str, float] = {}
    if _is_number(row.get("elapsed_ms")):
        out["elapsed_ms"] = row["elapsed_ms"]

    def walk(obj, prefix: str, depth: int) -> None:
        if depth > 6:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                key = f"{prefix}.{k}" if prefix else str(k)
                if _is_number(v):
                    out[key] = v
                elif isinstance(v, (dict, list)):
                    walk(v, key, depth + 1)
        elif isinstance(obj, list):
            last = prefix.rsplit(".", 1)[-1]
            if last in SKIP_LISTS:
                out[f"{prefix}.count"] = len(obj)
                return
            labelled = [x for x in obj if isinstance(x, dict) and _is_number(x.get("at_ms")) and (x.get("status") or x.get("type"))]
            if labelled and len(labelled) == len(obj):
                for x in obj:
                    label = x.get("status") or x.get("type")
                    out.setdefault(f"{prefix}.{label}", x["at_ms"])
            elif all(isinstance(x, dict) for x in obj) and obj:
                out[f"{prefix}.count"] = len(obj)
                for i, x in enumerate(obj[:5]):
                    walk(x, f"{prefix}[{i}]", depth + 1)
            else:
                out[f"{prefix}.count"] = len(obj)

    walk(row.get("detail") or {}, "", 0)
    return out


def _step_key(row: dict) -> tuple:
    return (row.get("step") if row.get("step") is not None else 999, row.get("env") or "")


def diff_runs(a: Run, b: Run) -> list[dict]:
    """手順ごとに A と B を並べる。同じ手順が同じ環境で複数回あるときは最初の行を使う。"""
    def index(run: Run) -> dict:
        idx: dict = {}
        for r in run.rows:
            idx.setdefault(_step_key(r), r)
        return idx

    ia, ib = index(a), index(b)
    keys = sorted(set(ia) | set(ib))
    out = []
    for key in keys:
        ra, rb = ia.get(key), ib.get(key)
        ma = flatten_metrics(ra) if ra else {}
        mb = flatten_metrics(rb) if rb else {}
        metrics = []
        for m in sorted(set(ma) | set(mb)):
            va, vb = ma.get(m), mb.get(m)
            delta = round(vb - va, 3) if _is_number(va) and _is_number(vb) else None
            metrics.append({"key": m, "a": va, "b": vb, "delta": delta})
        out.append(
            {
                "step": key[0] if key[0] != 999 else None,
                "env": key[1],
                "name": (ra or rb or {}).get("name"),
                "a": step_brief(ra) if ra else None,
                "b": step_brief(rb) if rb else None,
                "metrics": metrics,
            }
        )
    return out
