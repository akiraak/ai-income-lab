"""DXLink の取得の終わり方（2026-09-21）: 市場が開いている間も、今日の足の更新に引きずられず、履歴が届き終われば終わる。ネットワークは使わない。"""
import asyncio
import json
import sys
import time
import types

from ail.data.sources import tastytrade as tt

DAY = 86_400_000


class FakeWS:
    """認証 → 購読 → 履歴 2 本 → 今日の足（同じ時刻）を 0.1 秒おきに送り続ける（市場が開いている間の代役）。"""

    def __init__(self, sym):
        self.sym, self.out, self.subscribed = sym, [], False
        self.out += [{"type": "AUTH_STATE", "state": "UNAUTHORIZED"}]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, text):
        m = json.loads(text)
        if m["type"] == "AUTH":
            self.out.append({"type": "AUTH_STATE", "state": "AUTHORIZED"})
        elif m["type"] == "CHANNEL_REQUEST":
            self.out.append({"type": "CHANNEL_OPENED"})
        elif m["type"] == "FEED_SETUP":
            self.out.append({"type": "FEED_CONFIG"})
        elif m["type"] == "FEED_SUBSCRIPTION":
            self.subscribed = True
            hist = ["Candle", self.sym, 1 * DAY, 1, 2, 0.5, 1.5, 10, "Candle", self.sym, 2 * DAY, 1.5, 2, 1, 1.8, 12]
            self.out.append({"type": "FEED_DATA", "data": ["Candle", hist]})

    async def recv(self):
        if self.out:
            return json.dumps(self.out.pop(0))
        await asyncio.sleep(0.1)
        if self.subscribed:   # 今日の足（時刻 3 日目）が値を変えて届き続ける
            return json.dumps({"type": "FEED_DATA", "data": ["Candle", ["Candle", self.sym, 3 * DAY, 1.8, 2.2, 1.7, 1.8 + time.time() % 1 / 10, 5]]})
        return json.dumps({"type": "KEEPALIVE"})


def test_live_updates_of_todays_bar_do_not_keep_the_batch_open(monkeypatch):
    sym = "T{=d}"
    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=lambda *a, **k: FakeWS(sym)))
    t0 = time.perf_counter()
    got = asyncio.run(tt._fetch_batch("wss://x", "tok", ["T"], "d", 0, idle_s=0.5, max_s=10.0))
    took = time.perf_counter() - t0
    assert took < 3.0, took                                          # 修正前は max_s（10 秒）まで待った
    assert sorted(got[sym]) == [1 * DAY, 2 * DAY, 3 * DAY]            # 今日の足も（最後に来た値で）入る
