"""ブラウザに送る前のマスク。

record.py の Masker（JSONL と同じ置換 ＋ JWT の正規表現）に、口座番号のラベル化を足したもの。
口座番号は `acct-<sha256 の先頭 4 桁>` に置き換える（複数口座を区別でき、元には戻せない）。
画面・JSON API の応答は必ずこれを通す（プラン §2-2）。
"""

from __future__ import annotations

import hashlib
import threading

import record  # experiments/tastytrade-api-sample（config.import_sample で path に入れる）


def account_label(number: str) -> str:
    digest = hashlib.sha256(number.encode("utf-8")).hexdigest()[:4]
    return f"acct-{digest}"


class Redactor:
    def __init__(self) -> None:
        self._masker = record.Masker()
        self._accounts: dict[str, str] = {}
        self._lock = threading.Lock()

    def secret(self, value: str | None, placeholder: str) -> None:
        with self._lock:
            self._masker.add(value, placeholder)

    def account(self, number: str | None) -> str | None:
        """口座番号を登録してラベルを返す。以後、応答中の番号はラベルに置き換わる。"""
        if not number:
            return None
        with self._lock:
            label = self._accounts.get(number)
            if label is None:
                label = account_label(number)
                self._accounts[number] = label
                self._masker.add(number, label)
            return label

    def label_for(self, number: str | None) -> str | None:
        if not number:
            return None
        return self._accounts.get(number) or self.account(number)

    def text(self, s: str) -> str:
        with self._lock:
            return self._masker.text(s)

    def __call__(self, obj):
        with self._lock:
            return self._masker(obj)
