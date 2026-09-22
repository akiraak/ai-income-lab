"""テストが記録を読む・置くための道具（⚠ 記録は DB ＝ `livefs`。道はいままでと同じ）。"""

import json

import livefs


def jsonl(path) -> list[dict]:
    return [json.loads(line) for line in livefs.read_lines(path) if line.strip()]


def doc(path):
    text = livefs.read_doc(path)
    return None if text is None else json.loads(text)


def put_doc(path, obj) -> None:
    livefs.write_doc(path, json.dumps(obj, ensure_ascii=False, indent=1))


def put_jsonl(path, rows) -> None:
    livefs.append_many(path, [json.dumps(r, ensure_ascii=False) for r in rows])


def exists(path) -> bool:
    return livefs.exists(path)


def isdir(path) -> bool:
    return livefs.isdir(path)


def blob(root) -> str:
    """`root` の下の記録の全部の文字（秘密が出ていないかを見る）。"""
    _db, head, keys = livefs._keys_under(root)
    import os
    base = os.path.abspath(os.fspath(root))
    return "".join(livefs.read_text(os.path.join(base, *k[len(head):].split("/"))) or "" for k in keys)


def db_tree(path) -> list[tuple[str, int]]:
    """`path` の下の記録の一覧（道・文字数）。本物の木が動かないことを見る（読むだけ・DB を作らない）。"""
    import os
    _db, head, keys = livefs._keys_under(path)
    base = os.path.abspath(os.fspath(path))
    return [(k, len(livefs.read_text(os.path.join(base, *k[len(head):].split("/"))) or "")) for k in keys]
