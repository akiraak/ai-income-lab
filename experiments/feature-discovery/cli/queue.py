"""実験を並べて回し続ける器。⚠ **`cli/run.py` は置き換えない。外から 1 本ずつ起動するだけ。**

    python3 -m cli.queue --config blank_f41              # 回す（⚠ 途中から再開する）
    python3 -m cli.queue --config blank_f41 --dry-run    # ⚠ 実行を 1 つも作らずに並びだけ見る
    python3 -m cli.queue --config blank_f41 --restart    # ⚠ 状態を捨てて最初から

⚠ **設計の要点は 4 つ**（プラン `plans/archive/evolutionary-search-runner.md` §4）。
  1. ⚠ **subprocess で起動する。** 同じプロセスで回すと、1 本が落ちたときに残り全部が道連れになる
  2. ⚠ **本番 1 本につき `--leak` 対照を 1 本、自動で並べる**（rules.md 14-10 規約 6）。⚠ **手で足すと必ず忘れる**
  3. ⚠ **状態を DB（`runs/research.sqlite` の `queue_state`。2026-09-21 までは `runs/queue/<名前>.json`）に残す。**
     途中で殺しても続きから回り、⚠ **済んだものは 2 度回さない**
     （同じ設定を 2 度回すと実行が 2 つでき、⚠ **台帳の鍵では 1 行のままなので気づけない**）
  4. ⚠ **1 本終わるごとに台帳を吐き直し、`n_trials` の推移を状態に残す**（数え落としに気づくため）

⚠ **打ち切りは 3 つとも config に書く**（14-9・プラン §3-2）。⚠ **回し続ける仕組みは、放っておくと
`n_trials` を無限に増やす。** 止める条件を後から緩めない。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import tomllib

ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
QUEUE_CONFIG = os.path.join(ROOT, "config", "queue")
LEDGER = os.path.abspath(os.path.join(
    ROOT, "..", "..", "docs", "specs", "experiments", "feature-discovery", "ledger.md"))

# ⚠ **既定は「安全側」**: leak 対照を並べる・台帳を吐き直す・3 連続で失敗したら止める
DEFAULTS = {"leak": True, "ledger": True, "max_failures": 3, "time_budget_s": 0.0}


# --- 設定と並び ---------------------------------------------------------

def load_config(name: str, path: str | None = None) -> dict:
    p = path or os.path.join(QUEUE_CONFIG, f"{name}.toml")
    if not os.path.exists(p):
        have = sorted(f[:-5] for f in os.listdir(QUEUE_CONFIG)) if os.path.isdir(QUEUE_CONFIG) else []
        raise SystemExit(f"config/queue/{name}.toml が無い。あるのは: {', '.join(have) or '（1 つも無い）'}")
    with open(p, "rb") as f:
        cfg = tomllib.load(f)
    if not cfg.get("experiments"):
        raise SystemExit(f"{os.path.relpath(p, ROOT)} に `experiments` が無い")
    # ⚠ **走り出す前に実験の名前を全部確かめる**（config.resolve_experiment と同じ考え。
    # ⚠ **3 本目で「名前が違う」で止まるのが一番もったいない**）
    missing = [e for e in cfg["experiments"]
               if not os.path.exists(os.path.join(ROOT, "config", "experiment", f"{e}.toml"))]
    if missing:
        raise SystemExit(f"config/experiment に無い実験: {', '.join(missing)}")
    return {**DEFAULTS, **cfg, "name": name}


def plan_items(cfg: dict) -> list[dict]:
    """回す順。⚠ **本番の直後に leak 対照を置く**（間に別の実験を挟まない）。

    ⚠ **leak は「配線が生きているか」の検査**なので、⚠ **本番と同じコード・同じ時刻の近くで回さないと
    意味が薄れる。**
    """
    items: list[dict] = []
    for exp in cfg["experiments"]:
        items.append(_item(exp, leak=False))
        if cfg.get("leak", True):
            items.append(_item(exp, leak=True))
    return items


def _item(experiment: str, leak: bool) -> dict:
    return {"id": f"{experiment}{'_leak' if leak else ''}", "experiment": experiment,
            "leak": leak, "status": "pending", "run_dir": None, "n_trials": None,
            "seconds": None, "attempts": 0, "error": None}


# --- 状態（再開の要） ---------------------------------------------------

def state_path(name: str) -> str:
    """状態の置き場の表示用（DB のどの行か）。"""
    from ail import runs
    return f"{os.path.relpath(runs.db_path(), ROOT)} の queue_state「{name}」"


def load_state(name: str) -> dict | None:
    from ail import rundb, runs
    path = runs.db_path()
    return rundb.load_queue(rundb.connect(path), name) if os.path.exists(path) else None


def save_state(state: dict) -> None:
    from ail import rundb, runs
    state["updated_at"] = time.strftime("%Y-%m-%dT%H-%M-%S")
    conn = rundb.connect(runs.db_path())
    try:
        rundb.save_queue(conn, state["queue"], state)
    finally:
        conn.close()


def merge(old: dict | None, items: list[dict], cfg: dict) -> dict:
    """前の状態に新しい並びを重ねる。⚠ **済んだ項目の結果は 1 つも捨てない。**

    ⚠ **`running` のまま残っている項目は「途中で殺された」** ので `pending` に戻す。
    ⚠ **その実行は `summary.csv` を書けていないので台帳に行が立たない** — 回し直しても二重にならない。
    """
    prev = {i["id"]: i for i in (old or {}).get("items", [])}
    out = []
    for it in items:
        was = prev.get(it["id"])
        if was and was.get("status") == "done":
            out.append(was)
        elif was:
            out.append({**it, "attempts": was.get("attempts", 0),
                        "error": was.get("error"),
                        "status": "pending"})
        else:
            out.append(it)
    return {"queue": cfg["name"], "config": {k: v for k, v in cfg.items() if k != "name"},
            "created_at": (old or {}).get("created_at", time.strftime("%Y-%m-%dT%H-%M-%S")),
            "items": out}


# --- 実行 ---------------------------------------------------------------

def _subprocess_runner(experiment: str, leak: bool) -> tuple[bool, str | None, str]:
    """`cli.run` を別プロセスで 1 本回す。戻り値は (成功したか, 実行の名前, 末尾のログ)。"""
    cmd = [sys.executable, "-m", "cli.run", "--experiment", experiment] + (["--leak"] if leak else [])
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    tail = (p.stdout or "")[-2000:] + (p.stderr or "")[-2000:]
    return p.returncode == 0, parse_run_dir(p.stdout or ""), tail


def parse_run_dir(stdout: str) -> str | None:
    """`cli.run` が最後に出す `→ <時刻>_<名前>`（実行の名前）を拾う。

    ⚠ **拾えなくても実行は失敗ではない**（None のまま進む）。⚠ **状態から実行を辿れなくなるだけ。**
    """
    for line in reversed(stdout.splitlines()):
        if line.startswith("→ "):
            return line[2:].split("#")[0].strip()
    return None


def _write_ledger() -> int | None:
    """台帳を吐き直して `n_trials` を返す。⚠ **数字は作らない**（`ail/catalog.py` が正本）。"""
    from cli import ledger
    from ail import catalog
    from ail.validation import checks

    d = catalog.ledger()
    text = ledger.build(d)
    with open(LEDGER, "w", encoding="utf-8") as f:
        f.write(text)
    ledger.store(d)                      # ⚠ 同じ行と判定を DB の ledger_rows にも（ledger.md と同じ生成物）
    return checks.n_trials_now()


def run_queue(name: str, *, dry_run: bool = False, restart: bool = False,
              runner=_subprocess_runner, ledger_writer=_write_ledger,
              clock=time.time, log=print, config_path: str | None = None) -> dict:
    cfg = load_config(name, config_path)
    state = merge(None if restart else load_state(name), plan_items(cfg), cfg)

    todo = [i for i in state["items"] if i["status"] != "done"]
    done = len(state["items"]) - len(todo)
    log(f"キュー {name}: 全 {len(state['items'])} 本（済み {done} ／ 残り {len(todo)}）")
    if dry_run:
        # ⚠ **何も実行せず、状態も書かない。** 並びと打ち切りだけを見るための口
        for i in state["items"]:
            log(f"  [{i['status']:>7}] {i['id']}")
        log(f"  打ち切り: 連続失敗 {cfg['max_failures']} ／ 時間 "
            f"{cfg['time_budget_s'] or '—'}s ／ 台帳 {'吐き直す' if cfg['ledger'] else '触らない'}")
        return state

    save_state(state)
    started, failures = clock(), 0
    for item in state["items"]:
        if item["status"] == "done":
            continue                                    # ⚠ 2 度回さない（要点 3）
        budget = float(cfg.get("time_budget_s") or 0.0)
        if budget and clock() - started > budget:
            log(f"⚠ 時間の上限 {budget:g}s を超えたので止める（残り {len([i for i in state['items'] if i['status'] != 'done'])} 本）")
            break
        item["status"], item["attempts"] = "running", item.get("attempts", 0) + 1
        save_state(state)
        log(f"→ {item['id']} を回す（{item['attempts']} 回目）")
        t0 = clock()
        ok, run_dir, tail = runner(item["experiment"], item["leak"])
        item["seconds"] = round(clock() - t0, 1)
        item["run_dir"], item["error"] = run_dir, None if ok else tail[-800:]
        item["status"] = "done" if ok else "failed"
        if ok:
            failures = 0
            # ⚠ **台帳は本番の実行の後だけ吐き直す**（leak 対照は台帳に行が立たない）
            if cfg.get("ledger", True) and not item["leak"]:
                item["n_trials"] = ledger_writer()
            log(f"  ✅ {item['seconds']}s / {run_dir or '—'}"
                + (f" / n_trials {item['n_trials']}" if item.get("n_trials") else ""))
        else:
            failures += 1
            log(f"  ⚠ 失敗（{item['seconds']}s・連続 {failures}）: {tail.strip().splitlines()[-1] if tail.strip() else '—'}")
        save_state(state)
        if failures >= int(cfg.get("max_failures", 3)):
            log(f"⚠ {failures} 本続けて失敗したので止める。⚠ **直してから同じコマンドで再開する**")
            break
    left = [i for i in state["items"] if i["status"] != "done"]
    log(f"キュー {name}: 済み {len(state['items']) - len(left)} ／ 残り {len(left)}"
        + (f" ／ 状態 {state_path(name)}" if not dry_run else ""))
    return state


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True,
                    help="config/queue/<名前>.toml の名前（⚠ パスを直に渡してもよい）")
    ap.add_argument("--dry-run", action="store_true",
                    help="⚠ 実行を 1 つも作らずに並びと打ち切りだけ出す")
    ap.add_argument("--restart", action="store_true",
                    help="⚠ 状態を捨てて最初から（済んだ実行も回し直す）")
    args = ap.parse_args()
    # ⚠ **パスを直に渡せる口**（器だけを確かめたいときに `config/queue/` を汚さないため）
    path = args.config if os.path.exists(args.config) else None
    name = os.path.basename(args.config)[:-5] if path and args.config.endswith(".toml") else args.config
    run_queue(name, dry_run=args.dry_run, restart=args.restart, config_path=path)


if __name__ == "__main__":
    main()
