#!/usr/bin/env python3
"""生成した JSONL を検査する: 必須キー・長さ・rubric 件数・近似重複・禁止語。"""
import json, re, sys
from collections import Counter

REQUIRED = ["id", "category", "difficulty", "question", "reference_answer", "rubric", "provenance"]
MIN_Q, MIN_A = 40, 120
# 実在名の混入チェック（生成プロンプトで禁止している。検収時の目安であり網羅ではない）
BANNED = re.compile(r"(Google|Amazon|Microsoft|Meta|OpenAI|Anthropic|楽天|Yahoo|LINE|メルカリ|Netflix|Uber)", re.I)


def ngrams(s, n=3):
    s = re.sub(r"\s+", "", s)
    return {s[i : i + n] for i in range(max(len(s) - n + 1, 0))}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def main(path):
    items = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    problems = []
    grams = []
    for it in items:
        pid = it.get("id", "?")
        for k in REQUIRED:
            if k not in it:
                problems.append((pid, f"missing {k}"))
        if len(it.get("question", "")) < MIN_Q:
            problems.append((pid, f"question too short (<{MIN_Q})"))
        if len(it.get("reference_answer", "")) < MIN_A:
            problems.append((pid, f"answer too short (<{MIN_A})"))
        rb = it.get("rubric", [])
        if not isinstance(rb, list) or not (3 <= len(rb) <= 5):
            problems.append((pid, f"rubric count {len(rb) if isinstance(rb, list) else 'n/a'} (want 3-5)"))
        text = " ".join([it.get("question", ""), it.get("context", ""), it.get("reference_answer", "")])
        m = BANNED.search(text)
        if m:
            problems.append((pid, f"real-world name: {m.group(0)}"))
        if not it.get("provenance", {}).get("generator_model"):
            problems.append((pid, "no provenance.generator_model"))
        grams.append((pid, ngrams(it.get("question", ""))))
    dups = []
    for i in range(len(grams)):
        for j in range(i + 1, len(grams)):
            s = jaccard(grams[i][1], grams[j][1])
            if s >= 0.6:
                dups.append((grams[i][0], grams[j][0], round(s, 2)))
    cats = Counter(it.get("category") for it in items)
    diffs = Counter(it.get("difficulty") for it in items)
    reviewed = sum(1 for it in items if it.get("provenance", {}).get("reviewed_by_human"))
    print(json.dumps({
        "summary": "validate",
        "items": len(items),
        "problems": len(problems),
        "near_duplicates": len(dups),
        "reviewed_by_human": reviewed,
        "categories": dict(cats),
        "difficulties": dict(diffs),
    }, ensure_ascii=False))
    for pid, msg in problems:
        print(f"  {pid}: {msg}")
    for a, b, s in dups:
        print(f"  near-dup {a} ~ {b} (jaccard {s})")
    return 1 if problems or dups else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "experiments/i7-dataset/out/pilot.jsonl"))
