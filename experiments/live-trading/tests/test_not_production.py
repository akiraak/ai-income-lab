"""「本番の機械ではない」印 `NOT_PRODUCTION`（live-trading.md §0-14。3 台の役割分け Phase 3）。

⚠ ネットワークは使わない（拒否は全部、認証の前で起きる）。印・MODE・run.lock は `LT_MODE_DIR` で tmp に向ける（本物を触らない）。
固定するもの: 印なしで何も変わらない ／ set が hostname を書く・status・clear・mode.log ／ 印のある機械の本番 submit は許可があっても rc=7 ／
prod の plan・dry-run と cert の submit は印で拒まれない ／ 他の機械の印・壊れた印でも拒む ／ sample.py も同じ印で本番の発注系を拒む。
"""
import json
import os
import subprocess
import sys

import pytest

import mode as modes
from tests._records import jsonl
import livefs

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE = os.path.normpath(os.path.join(HERE, "..", "tastytrade-api-sample"))


def _closed_port() -> int:
    """いま誰も聞いていないループバックのポート（つなぐと即 refused）。"""
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ⚠ 接続先はループバックの閉じたポート ＝ 印で止まらない経路も外（tastytrade）へは 1 度も出ない（偽の資格情報で本番に認証を試みない）
NOWHERE = {"TT_PROD_REST_BASE": f"http://127.0.0.1:{_closed_port()}", "TT_REST_BASE": f"http://127.0.0.1:{_closed_port()}"}
PROD = {"TT_PROD_CLIENT_SECRET": "s", "TT_PROD_REFRESH_TOKEN": "r", "TT_ALLOW_PROD_ORDERS": "1", **NOWHERE}
PROD_NOKEY = {"TT_PROD_CLIENT_SECRET": "s", "TT_PROD_REFRESH_TOKEN": "r", **NOWHERE}
CERT = {"TT_CLIENT_SECRET": "s", "TT_REFRESH_TOKEN": "r"}


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_MODE_DIR", str(tmp_path))
    monkeypatch.setenv("LT_HOSTNAME", "titan-test")
    return tmp_path


def run(script, base, args, env_extra, cwd=HERE):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TT_", "LT_"))}
    env.update({"TT_ENV_FILE": "/nonexistent", "LT_MODE_DIR": str(base), "LT_HOSTNAME": "titan-test",
                "LT_OUT_DIR": str(base / "out"), "LT_STATE_DIR": str(base / "state" / "x"), "TT_OUT_DIR": str(base / "sample-out"), **env_extra})
    return subprocess.run([sys.executable, os.path.join(cwd, script), *args], capture_output=True, text=True, env=env)


def events(base):
    return [r for p in livefs.find(base / "out", "*/events.jsonl") for r in jsonl(p)]


PROD_SUBMIT = ["--traders", "test_a", "--env", "prod", "--mode", "submit", "--ignore-window", "--i-know-this-is-real-money"]


# ---------- 印の読み書き

def test_no_mark_means_nothing_changes(base):
    assert modes.read_not_production() is None
    r = run("notprod.py", base, ["status"], {})
    assert r.returncode == 0 and "印なし" in r.stdout
    # 印が無ければ、本番 submit は今までどおり許可の 3 段で止まる（rc=2。印の rc=7 ではない）
    r = run("run_day.py", base, PROD_SUBMIT, PROD_NOKEY)
    assert r.returncode == 2 and "TT_ALLOW_PROD_ORDERS" in r.stderr and "本番の機械ではない" not in r.stderr


def test_set_writes_hostname_status_clear_and_log(base):
    r = run("notprod.py", base, ["set", "--reason", "本番は 13500t"], {})
    assert r.returncode == 0 and "印を置いた" in r.stdout
    doc = json.loads((base / "NOT_PRODUCTION").read_text())
    assert doc["machine"] == "titan-test" and doc["reason"] == "本番は 13500t" and doc["since"] and doc["by"]
    mark = modes.read_not_production()
    assert mark.matches_host and mark.error is None
    r = run("notprod.py", base, ["status"], {})
    assert r.returncode == 0 and "印あり" in r.stdout and "titan-test" in r.stdout
    r = run("notprod.py", base, ["set"], {})                       # 置き直さない
    assert r.returncode == 0 and "既に印がある" in r.stdout and json.loads((base / "NOT_PRODUCTION").read_text())["since"] == doc["since"]
    r = run("notprod.py", base, ["clear"], {})
    assert r.returncode == 0 and "印を外した" in r.stdout and not (base / "NOT_PRODUCTION").exists()
    r = run("notprod.py", base, ["clear"], {})
    assert r.returncode == 0 and "印は無かった" in r.stdout
    log = jsonl(base / "mode.log")
    assert [x["kind"] for x in log] == ["not_production_set", "not_production_cleared"]
    assert all(x["machine"] == "titan-test" and x["matches_host"] for x in log)


# ---------- 執行器

def test_marked_machine_refuses_prod_submit_before_permissions_and_network(base):
    modes.set_not_production(by="test", reason="稽古")
    r = run("run_day.py", base, PROD_SUBMIT, PROD)
    assert r.returncode == 7 and "本番の機械ではない" in r.stderr and "notprod.py clear" in r.stderr
    ev = events(base)
    assert [e["kind"] for e in ev] == ["refused_not_production"]
    assert ev[0]["machine"] == "titan-test" and ev[0]["matches_host"] and ev[0]["mode"] == "submit"
    # 許可が無くても同じ拒否（印が許可より先）
    r = run("run_day.py", base, PROD_SUBMIT[:-1], PROD_NOKEY)
    assert r.returncode == 7 and "TT_ALLOW_PROD_ORDERS" not in r.stderr


def test_mark_does_not_refuse_plan_dry_run_or_cert(base):
    modes.set_not_production(by="test")
    # prod の plan ／ dry-run: 印では止まらず、（資格情報が偽なので）認証まで進んで rc=1
    for args in (["--traders", "test_a", "--env", "prod", "--mode", "plan"],
                 ["--traders", "test_a", "--env", "prod", "--mode", "dry-run", "--allow-prod-dry-run", "--ignore-window"]):
        r = run("run_day.py", base, args, PROD_NOKEY)
        # 印では止まらず認証（ループバックの閉じたポート）まで進む ＝ 接続の失敗で終わる
        assert r.returncode != 7 and "本番の機械ではない" not in r.stderr and "127.0.0.1" in r.stderr, (args, r.stderr)
    # cert の submit: 印では止まらない（LT_MODE_DIR ＋ モック以外の submit の既存の拒否 rc=2 に落ちる ＝ 外へは出ない）
    r = run("run_day.py", base, ["--traders", "test_a", "--mode", "submit", "--ignore-window"], CERT)
    assert r.returncode == 2 and "LT_MODE_DIR" in r.stderr and "本番の機械ではない" not in r.stderr
    assert not [e for e in events(base) if e["kind"] == "refused_not_production"]


def test_foreign_or_broken_mark_still_refuses(base):
    (base / "NOT_PRODUCTION").write_text(json.dumps({"machine": "titan", "since": "2026-09-27T00:00:00+00:00", "by": "u"}))
    mark = modes.read_not_production()
    assert mark.machine == "titan" and not mark.matches_host and "この機械（titan-test）の印ではない" in mark.describe()
    r = run("run_day.py", base, PROD_SUBMIT, PROD)
    assert r.returncode == 7 and "この機械（titan-test）の印ではない" in r.stderr
    assert events(base)[-1]["matches_host"] is False
    r = run("notprod.py", base, ["status"], {})
    assert r.returncode == 1 and "印ではない" in r.stdout
    for broken in ("{", json.dumps({"since": "x"}), json.dumps({"machine": ""})):
        (base / "NOT_PRODUCTION").write_text(broken)
        assert modes.read_not_production().error
        r = run("run_day.py", base, PROD_SUBMIT, PROD)
        assert r.returncode == 7 and "印が読めない" in r.stderr, broken


# ---------- sample.py（発注の経路のもう 1 本）

def test_sample_cli_refuses_prod_order_steps_with_mark(base):
    modes.set_not_production(by="test")
    r = run("sample.py", base, ["--env", "prod", "--step", "4", "--i-know-this-is-real-money"], PROD, cwd=SAMPLE)
    assert r.returncode == 7 and "本番の機械ではない" in r.stderr
    # 読み取りだけの手順は印で止まらない
    r = run("sample.py", base, ["--env", "prod", "--step", "probe"], PROD_NOKEY, cwd=SAMPLE)
    assert r.returncode != 7 and "本番の機械ではない" not in r.stderr
    modes.clear_not_production(by="test")
    r = run("sample.py", base, ["--env", "prod", "--step", "4", "--i-know-this-is-real-money"], PROD, cwd=SAMPLE)
    assert r.returncode != 7
