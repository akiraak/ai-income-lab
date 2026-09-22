"""控え（発注の前に書く注文の覚え書き）。口座の建玉と台帳の帳尻を合わせる 3 段のうちの段 1（live-trading.md §0-8）。

    intent     発注の直前: 注文の ID（external-identifier）・日付・誰の・銘柄・向き・数量・手数料の見積り
    submitted  発注の後: 相手側の注文番号
    done       その人の台帳を保存した後（約定なし・エラー・人が閉じた、も done）

`state/<env>/journal.jsonl` に 1 行ずつ足すだけ（書き換えない。⚠ 2026-09-21 から DB の lines ＝ `livefs`。道はそのまま鍵）。⚠ `--mode submit` のときだけ書く。
起動時に done の無い intent があれば、その注文は「口座では約定したかもしれないのに台帳に入っていない」＝ `recovery.py` が照会して戻す。
1 注文 1 トレーダー（2026-09-19）なので、誰の台帳に入れるかは控えで確実に分かる。⚠ 差を推測で割り振らない。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from _livefs import livefs

NAME = "journal.jsonl"


class Journal:
    def __init__(self, state_dir: str, date: str | None = None, now=lambda: datetime.now(timezone.utc)):
        self.path = os.path.join(state_dir, NAME)
        self.date = date
        self.now = now
        self._open: set[str] = set()

    def _append(self, row: dict) -> None:
        # ⚠ 発注より先にディスクへ（落ちた後に読むための記録）＝ DB の 1 トランザクション（synchronous=FULL で書き終えてから戻る）
        livefs.append(self.path, json.dumps({**row, "at": self.now().isoformat(timespec="seconds")}, ensure_ascii=False))

    def intent(self, ext: str, order, fee_usd: float = 0.0) -> None:
        part = order.parts[0]
        self._open.add(ext)
        self._append({"op": "intent", "ext": ext, "date": self.date, "trader": part["trader"], "symbol": order.symbol, "side": order.side,
                      "sizing": order.sizing, "shares": order.shares, "value_usd": order.value_usd, "fee_usd": fee_usd})

    def submitted(self, ext: str, order_id) -> None:
        self._append({"op": "submitted", "ext": ext, "order_id": order_id})

    def done(self, ext: str, outcome: str, **extra) -> None:
        if ext in self._open:   # 発注の手前で終わった注文（HALT・dry-run のエラー）は控えに無い
            self._open.discard(ext)
            self._append({"op": "done", "ext": ext, "outcome": outcome, **extra})

    def close(self, ext: str, outcome: str, **extra) -> None:
        """起動時の復元・人の手（reconcile.py）が未完の控えを閉じる。"""
        self._append({"op": "done", "ext": ext, "outcome": outcome, **extra})

    def unfinished(self) -> list[dict]:
        """done の無い intent（古い順）。submitted があれば order_id が付く。"""
        entries: dict[str, dict] = {}
        for line in livefs.read_lines(self.path):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue                # 書いている途中で落ちた最後の行（ファイルの頃に取り込んだもの）
            ext = row.get("ext")
            if row.get("op") == "intent":
                entries[ext] = dict(row)
            elif row.get("op") == "submitted" and ext in entries:
                entries[ext]["order_id"] = row.get("order_id")
            elif row.get("op") == "done":
                entries.pop(ext, None)
        return list(entries.values())
