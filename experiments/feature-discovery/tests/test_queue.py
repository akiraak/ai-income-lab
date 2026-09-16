"""⚠ **器そのものを検査する。** 中身（GA）ではなく「回し続ける仕組み」の側。

⚠ **この器で壊れると痛いのは 3 つ**（プラン `plans/archive/evolutionary-search-runner.md` §4）。
  1. ⚠ **済んだ実行を 2 度回す** — `runs/` にディレクトリが 2 つでき、⚠ **台帳の鍵では 1 行のままなので
     気づけない**（数え落としと同じ形で、しかも気づけない）
  2. ⚠ **leak 対照が並ばない** — 配線の穴に気づく唯一の手が消える（rules.md 14-10 規約 6）
  3. ⚠ **止まらない** — 回し続ける仕組みは、放っておくと `n_trials` を無限に増やす

⚠ **実験は 1 本も回さない。** runner を差し替えて、器の判断だけを見る。
"""

from __future__ import annotations

import json
import os

import pytest

from cli import queue

EXP = "trade_own_ridge_a"          # ⚠ 実在する config（名前の事前解決を通すため）


def _cfg(tmp_path, **kw) -> str:
    body = [f'experiments = ["{EXP}"]']
    for k, v in kw.items():
        body.append(f"{k} = {json.dumps(v)}")
    p = tmp_path / "q.toml"
    p.write_text("\n".join(body) + "\n", encoding="utf-8")
    return str(p)


class Runner:
    """呼ばれた回数と中身を覚えるだけの偽の runner。"""

    def __init__(self, ok=True):
        self.calls: list[tuple[str, bool]] = []
        self.ok = ok

    def __call__(self, experiment: str, leak: bool):
        self.calls.append((experiment, leak))
        ok = self.ok(len(self.calls)) if callable(self.ok) else self.ok
        return ok, (f"runs/x{len(self.calls)}" if ok else None), "ログの末尾"


@pytest.fixture
def q(tmp_path, monkeypatch):
    monkeypatch.setattr(queue, "STATE_DIR", str(tmp_path / "state"))
    return tmp_path


def test_本番の直後に_leak_対照が並ぶ(q):
    cfg = queue.load_config("q", _cfg(q))
    items = queue.plan_items(cfg)
    assert [(i["experiment"], i["leak"]) for i in items] == [(EXP, False), (EXP, True)]


def test_leak_を切れば本番だけ(q):
    cfg = queue.load_config("q", _cfg(q, leak=False))
    assert [i["leak"] for i in queue.plan_items(cfg)] == [False]


def test_存在しない実験名は走り出す前に落ちる(q):
    p = q / "bad.toml"
    p.write_text('experiments = ["そんな実験は無い"]\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        queue.load_config("bad", str(p))


def test_dry_run_は実行も状態の書き込みもしない(q):
    r = Runner()
    queue.run_queue("q", dry_run=True, runner=r, ledger_writer=lambda: 1,
                    log=lambda *a: None, config_path=_cfg(q))
    assert r.calls == []
    assert not os.path.exists(queue.state_path("q"))


def test_済んだ実行は_2_度回さない(q):
    """⚠ **これが再開の要。** 2 度回すと `runs/` に二重のディレクトリができる。"""
    path = _cfg(q)
    r1 = Runner()
    queue.run_queue("q", runner=r1, ledger_writer=lambda: 10, log=lambda *a: None, config_path=path)
    assert len(r1.calls) == 2                      # 本番 ＋ leak
    r2 = Runner()
    queue.run_queue("q", runner=r2, ledger_writer=lambda: 10, log=lambda *a: None, config_path=path)
    assert r2.calls == []                          # ⚠ 1 本も回さない
    state = queue.load_state("q")
    assert [i["status"] for i in state["items"]] == ["done", "done"]


def test_restart_なら済んだものも回し直す(q):
    path = _cfg(q)
    queue.run_queue("q", runner=Runner(), ledger_writer=lambda: 10, log=lambda *a: None, config_path=path)
    r = Runner()
    queue.run_queue("q", restart=True, runner=r, ledger_writer=lambda: 10,
                    log=lambda *a: None, config_path=path)
    assert len(r.calls) == 2


def test_失敗しても次へ進み_連続で止まる(q):
    """⚠ **1 本の失敗で全部落ちない**（要点 1）。⚠ **だが連続したら止まる**（要点 3）。"""
    path = _cfg(q, max_failures=2)
    r = Runner(ok=False)
    state = queue.run_queue("q", runner=r, ledger_writer=lambda: 10,
                            log=lambda *a: None, config_path=path)
    assert len(r.calls) == 2                       # 2 連続で失敗 → 打ち切り
    assert [i["status"] for i in state["items"]] == ["failed", "failed"]
    assert state["items"][0]["error"]              # ⚠ 理由を残す（残さないと直せない）


def test_時間の上限で止まる(q):
    path = _cfg(q, time_budget_s=10)
    clock = iter([0.0, 0.0, 5.0, 100.0, 100.0, 100.0])   # 2 本目に入る前に超える
    r = Runner()
    state = queue.run_queue("q", runner=r, ledger_writer=lambda: 10, log=lambda *a: None,
                            clock=lambda: next(clock), config_path=path)
    assert len(r.calls) == 1
    assert [i["status"] for i in state["items"]] == ["done", "pending"]


def test_台帳は本番の後だけ吐き直す(q):
    """⚠ **leak 対照は台帳に行が立たない**ので吐き直す意味が無い（毎回回すと遅くなるだけ）。"""
    seen = []
    queue.run_queue("q", runner=Runner(), ledger_writer=lambda: seen.append(1) or 42,
                    log=lambda *a: None, config_path=_cfg(q))
    assert len(seen) == 1
    state = queue.load_state("q")
    assert state["items"][0]["n_trials"] == 42     # 本番
    assert state["items"][1]["n_trials"] is None   # leak


def test_途中で殺された項目は_pending_に戻る(q):
    """⚠ **`running` のまま残った実行は `summary.csv` を書けていない** ので台帳に行が立たない。
    ⚠ **回し直しても二重にならない。**"""
    old = {"items": [{"id": EXP, "experiment": EXP, "leak": False, "status": "running",
                      "attempts": 1, "run_dir": None, "n_trials": None,
                      "seconds": None, "error": None}]}
    cfg = queue.load_config("q", _cfg(q))
    merged = queue.merge(old, queue.plan_items(cfg), cfg)
    assert merged["items"][0]["status"] == "pending"
    assert merged["items"][0]["attempts"] == 1     # ⚠ 何回目かは覚えている


def test_実行ディレクトリを最後の行から拾う():
    out = "fold 1: 訓練 100 / 検証 20\n→ runs/2026-09-16T10-00-00_x\n"
    assert queue.parse_run_dir(out) == "runs/2026-09-16T10-00-00_x"
    assert queue.parse_run_dir("何も出なかった") is None      # ⚠ 失敗にはしない
