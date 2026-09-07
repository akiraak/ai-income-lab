#!/usr/bin/env python3
"""record.py のマスクと記録形式のテスト（ネットワーク不要）。

    python3 test_record.py

検証方針 §7-2「資格情報が漏れない」の担保。実行記録に秘密が出ないことを、
値そのものを混ぜたうえで確かめる。
"""

from __future__ import annotations

import json
import os
import tempfile

import record

FAILED = []


def check(label: str, condition: bool) -> None:
    print(f"  {'ok  ' if condition else 'NG  '} {label}")
    if not condition:
        FAILED.append(label)


def test_masker() -> None:
    print("Masker:")
    masker = record.Masker()
    masker.add("s3cr3t-client-secret", "<client_secret:masked>")
    masker.add("refresh-token-value", "<refresh_token:masked>")
    masker.add_account("5WT00042")

    text = masker.text("secret=s3cr3t-client-secret account=5WT00042 refresh=refresh-token-value")
    check("client secret が消える", "s3cr3t-client-secret" not in text)
    check("口座番号が消える", "5WT00042" not in text)
    check("refresh token が消える", "refresh-token-value" not in text)
    check("置き換え後の印が残る", "<account:masked>" in text and "<client_secret:masked>" in text)

    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjE3ODg2MjIxMTl9.signaturepart"
    check("登録していない JWT も消える", "eyJ" not in masker.text(f"token={jwt}"))

    nested = masker({"a": ["5WT00042", {"b": "s3cr3t-client-secret"}], "5WT00042": 1})
    check("入れ子の値もたどる", "5WT00042" not in json.dumps(nested))
    check("キー側も消える", "<account:masked>" in json.dumps(nested))

    short = record.Masker()
    short.add("ab", "<x>")
    check("短すぎる値は登録しない（誤爆防止）", short.text("abcabc") == "abcabc")


def test_recorder() -> None:
    print("Recorder:")
    with tempfile.TemporaryDirectory() as tmp:
        rec = record.Recorder(tmp, venue="tastytrade", env="cert")
        rec.mask.add("token-abcdef", "<access_token:masked>")
        with rec.step(1, "テスト手順") as row:
            row["detail"] = {"raw": "Authorization: Bearer token-abcdef"}
            row["result"] = "authenticated"
        try:
            with rec.step(2, "落ちる手順"):
                raise ValueError("わざと落とす")
        except ValueError:
            pass

        rows = [json.loads(line) for line in open(rec.path, encoding="utf-8")]
        check("2 手順が 2 行になる", len(rows) == 2)
        check("会場名を持つ", rows[0]["venue"] == "tastytrade")
        check("環境を持つ", rows[0]["env"] == "cert")
        check("ET と PT の時刻を持つ", {"utc", "et", "pt"} <= set(rows[0]["started_at"]))
        check("所要 ms を持つ", isinstance(rows[0]["elapsed_ms"], float))
        check("SDK の版を持つ", "python" in rows[0]["sdk"])
        check("記録にトークンが出ない", "token-abcdef" not in json.dumps(rows))
        check("例外も 1 行として残る", rows[1]["ok"] is False and rows[1]["result"] == "error")
        check("落ちた理由が残る", rows[1]["error"]["type"] == "ValueError")
        check("ファイル名に会場と環境が入る", os.path.basename(rec.path).startswith("tastytrade-cert-"))


def test_excerpt() -> None:
    print("excerpt:")
    long_list = list(range(100))
    check("配列は先頭 3 件に縮む", len(record.excerpt(long_list)) == 4)
    check("残数を書く", "+97 items" in record.excerpt(long_list)[-1])
    check("長い文字列を切る", record.excerpt("x" * 500).endswith("(+100 chars)"))
    check("辞書のキー数を絞る", "..." in record.excerpt({str(i): i for i in range(40)}))


if __name__ == "__main__":
    test_masker()
    test_recorder()
    test_excerpt()
    print()
    if FAILED:
        print(f"NG: {len(FAILED)} 件 — {FAILED}")
        raise SystemExit(1)
    print("すべて通った")
