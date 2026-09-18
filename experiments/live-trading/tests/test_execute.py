"""執行器の安全: 鍵・HALT・再送・按分（プラン §6 の「鍵」「窓」）。ネットワークは使わない。"""
import os

from ttclient import ApiError, Client

from execute import ExecResult, Executor, Fill, allocate_fills, is_session_offline
from plan import NetOrder


class FakeClient:
    """submit を数えるだけの偽物。Session offline を n 回返してから通す。"""

    def __init__(self, offline_times=0):
        self.offline_times = offline_times
        self.submits = 0
        self.dry_runs = 0
        self.cancels = 0
        self.env = "cert"
        self.allow_prod_orders = True
        self.build_equity_order = Client.build_equity_order

    def dry_run_order(self, acct, order):
        self.dry_runs += 1
        return {"buying-power-effect": {"change-in-buying-power": "1.0"}, "fee-calculation": {"total-fees": "0.0"}, "order": {"status": "Received"}}

    def submit_order(self, acct, order):
        self.submits += 1
        if self.offline_times > 0:
            self.offline_times -= 1
            raise ApiError(422, "preflight_check_failure", "Session offline", {"error": {"errors": [{"code": "session_offline", "message": "Session offline"}]}})
        return {"order": {"id": 7, "status": "Received"}, "warnings": []}

    def find_order_by_external_id(self, acct, ext):
        return None

    def wait_for_status(self, acct, oid, targets, timeout, interval):
        return [{"at_ms": 1, "status": "Filled"}]

    def get_order(self, acct, oid):
        return {"status": "Filled", "legs": [{"fills": [{"quantity": "1", "fill-price": "25.60", "filled-at": "t"}]}]}

    def cancel_order(self, acct, oid):
        self.cancels += 1
        return {}


def _order():
    return NetOrder("T", "buy", 1, 25.6, "shares", [{"trader": "test_a", "shares": 1, "usd": 25.6}])


def test_dry_run_mode_never_submits(tmp_path):
    c = FakeClient()
    ex = Executor(c, "ACCT", None, str(tmp_path / "HALT"), mode="dry-run")
    r = ex.run_one(_order())
    assert r.final_status == "dry-run" and c.dry_runs == 1 and c.submits == 0


def test_plan_mode_touches_nothing(tmp_path):
    c = FakeClient()
    r = Executor(c, "ACCT", None, str(tmp_path / "HALT"), mode="plan").run_one(_order())
    assert r.final_status == "planned" and c.dry_runs == 0 and c.submits == 0


def test_halt_blocks_submit(tmp_path):
    halt = tmp_path / "HALT"
    halt.write_text("stop")
    c = FakeClient()
    r = Executor(c, "ACCT", None, str(halt), mode="submit").run_one(_order())
    assert r.final_status == "halted" and c.submits == 0 and c.dry_runs == 0


def test_session_offline_retries_then_fills(tmp_path):
    c = FakeClient(offline_times=2)
    slept = []
    ex = Executor(c, "ACCT", None, str(tmp_path / "HALT"), mode="submit", retries=3, retry_interval=60, sleep=slept.append)
    r = ex.run_one(_order())
    assert r.final_status == "Filled" and c.submits == 3 and slept == [60, 60] and r.attempts == 3
    assert r.fills[0].price == 25.6 and r.fills[0].shares == 1


def test_session_offline_gives_up_after_retries(tmp_path):
    c = FakeClient(offline_times=5)
    ex = Executor(c, "ACCT", None, str(tmp_path / "HALT"), mode="submit", retries=3, retry_interval=1, sleep=lambda s: None)
    r = ex.run_one(_order())
    assert r.final_status == "error" and c.submits == 3 and r.error["code"] == "preflight_check_failure"


def test_prod_without_keys_is_guarded(tmp_path):
    # 本物の Client を prod・鍵なしで作る → dry-run すら ProductionGuard で止まる（ネットワークに出ない）
    c = Client(env="prod")
    r = Executor(c, "ACCT", None, str(tmp_path / "HALT"), mode="dry-run").run_one(_order())
    assert r.final_status == "guarded"
    # 取消の鍵だけでは発注が開かない
    c = Client(env="prod", allow_prod_cancel=True, allow_prod_dry_run=True)
    c.token = type("T", (), {"access_token": "x"})()
    r = Executor(c, "ACCT", None, str(tmp_path / "HALT"), mode="submit").run_one(_order())
    # dry-run は request() でネットワークに出ようとして失敗する → ApiError ではなく requests の例外になるので、
    # ここでは _guard_orders だけを直接確かめる
    import pytest
    from ttclient import ProductionGuard
    with pytest.raises(ProductionGuard):
        c._guard_orders()


def test_notional_order_shape():
    o = NetOrder("SPY", "buy", 0.0, 5.0, "notional", [{"trader": "a", "shares": 0.0089, "usd": 5.0}])
    body = Executor(FakeClient(), "ACCT", None, "/nonexistent/HALT", mode="plan").build(o, "lt-x")
    assert body["order-type"] == "Notional Market" and body["value"] == "5.00" and "quantity" not in body["legs"][0]
    o = NetOrder("SPY", "sell", 0.0089, 5.0, "notional", [])
    body = Executor(FakeClient(), "ACCT", None, "/nonexistent/HALT", mode="plan").build(o, "lt-x")
    assert body["order-type"] == "Market" and body["legs"][0]["quantity"] == "0.0089"


def test_allocate_fills_prorata():
    o = NetOrder("SPY", "buy", 3, 300, "shares", [{"trader": "A", "shares": 1, "usd": 100}, {"trader": "B", "shares": 2, "usd": 200}])
    r = ExecResult(o, "x", fills=[Fill("SPY", "buy", 2, 100.0, None, 1), Fill("SPY", "buy", 1, 103.0, None, 1)])
    parts = allocate_fills(r)
    assert [p["shares"] for p in parts] == [1, 2] and parts[0]["price"] == 101.0


def test_is_session_offline():
    assert is_session_offline(ApiError(422, "x", "Session offline", None))
    assert not is_session_offline(ApiError(422, "x", "insufficient funds", None))


def test_dry_run_502_is_retried(tmp_path):
    class Flaky(FakeClient):
        def __init__(self):
            super().__init__()
            self.fail = 1

        def dry_run_order(self, acct, order):
            if self.fail:
                self.fail -= 1
                raise ApiError(502, "non_json_response", "<html>502</html>", "<html>")
            return super().dry_run_order(acct, order)

    c = Flaky()
    slept = []
    r = Executor(c, "ACCT", None, str(tmp_path / "HALT"), mode="dry-run", retries=3, retry_interval=5, sleep=slept.append).run_one(_order())
    assert r.final_status == "dry-run" and slept == [5] and r.transitions[0]["status"].startswith("dry-run retry:502")


def test_4xx_is_not_retried(tmp_path):
    class Bad(FakeClient):
        def submit_order(self, acct, order):
            self.submits += 1
            raise ApiError(422, "invalid_order", "quantity too small", None)

    c = Bad()
    r = Executor(c, "ACCT", None, str(tmp_path / "HALT"), mode="submit", retries=3, retry_interval=1, sleep=lambda s: None).run_one(_order())
    assert r.final_status == "error" and c.submits == 1
