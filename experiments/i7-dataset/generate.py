#!/usr/bin/env python3
"""ソフトウェア開発の日本語評価セットを OpenAI 互換エンドポイントで生成する。

既定は Ollama (http://localhost:11434/v1) + qwen3:8b (Apache-2.0)。
本文は必ず許諾上問題のないモデルで生成する。Claude / OpenAI / Gemini は使わない（README 参照）。
"""
import argparse, hashlib, json, os, random, re, sys, time, datetime
import requests

PROMPT_VERSION = "v1"

SYSTEM = (
    "/no_think\n"
    "あなたはソフトウェア開発の評価問題を作る専門家です。"
    "指定されたカテゴリ・小トピック・難易度・言語で、日本語の評価問題を 1 問だけ作ります。"
    "必ず以下を守ってください。\n"
    "- 既存の書籍・記事・OSS のコードを引用・再現しない。コードは全てこの場で書き下ろす\n"
    "- 実在の製品名・企業名・人名を出さない（汎用的な架空の設定にする）\n"
    "- 問いは自己完結させる。必要な前提・コードは context に含める\n"
    "- reference_answer は根拠とトレードオフを示す。結論だけにしない\n"
    "- rubric は採点者が機械的に確認できる 3〜5 項目\n"
    "出力は JSON オブジェクトのみ。キー: question, context, reference_answer, rubric(配列), tags(配列)。"
)

USER_TMPL = (
    "カテゴリ: {category_label}\n小トピック: {subtopic}\n難易度: {difficulty} ({difficulty_desc})\n"
    "対象言語: {language}\n\n上の条件で評価問題を 1 問、JSON で出力してください。"
)


def extract_json(text: str):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no json object in output")
    return json.loads(text[start : end + 1])


def call(base_url, api_key, model, messages, timeout):
    r = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": messages,
            "temperature": 0.8,
            "response_format": {"type": "json_object"},
        },
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    usage = data.get("usage", {})
    return data["choices"][0]["message"]["content"], usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--out", default="experiments/i7-dataset/out/pilot.jsonl")
    ap.add_argument("--model", default=os.environ.get("GEN_MODEL", "qwen3:8b"))
    ap.add_argument("--license", default=os.environ.get("GEN_MODEL_LICENSE", "Apache-2.0"))
    ap.add_argument("--base-url", default=os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1"))
    ap.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY", "ollama"))
    ap.add_argument("--topics", default=os.path.join(os.path.dirname(__file__), "topics.json"))
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--prefix", default="swdev-ja")
    args = ap.parse_args()

    topics = json.load(open(args.topics, encoding="utf-8"))
    rng = random.Random(args.seed)
    combos = [
        (ck, cv["label"], st, dk, dv, lang)
        for ck, cv in topics["categories"].items()
        for st in cv["subtopics"]
        for dk, dv in topics["difficulties"].items()
        for lang in topics["languages"]
    ]
    rng.shuffle(combos)
    combos = combos[: args.n]

    existing = 0
    if os.path.exists(args.out):
        existing = sum(1 for _ in open(args.out, encoding="utf-8"))
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    ok = fail = 0
    tok_in = tok_out = 0
    t0 = time.time()
    with open(args.out, "a", encoding="utf-8") as f:
        for i, (ck, cl, st, dk, dv, lang) in enumerate(combos, start=existing + 1):
            user = USER_TMPL.format(category_label=cl, subtopic=st, difficulty=dk, difficulty_desc=dv, language=lang)
            messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]
            t1 = time.time()
            try:
                raw, usage = call(args.base_url, args.api_key, args.model, messages, args.timeout)
                obj = extract_json(raw)
                for k in ("question", "reference_answer", "rubric"):
                    if k not in obj:
                        raise ValueError(f"missing key {k}")
            except Exception as e:  # noqa: BLE001
                fail += 1
                print(f"[{i}] FAIL {ck}/{st}/{dk}/{lang}: {e}", file=sys.stderr)
                continue
            dt = time.time() - t1
            tok_in += usage.get("prompt_tokens", 0)
            tok_out += usage.get("completion_tokens", 0)
            item = {
                "id": f"{args.prefix}-{i:04d}",
                "category": ck,
                "subtopic": st,
                "difficulty": dk,
                "language": lang,
                "question": obj.get("question", ""),
                "context": obj.get("context", ""),
                "reference_answer": obj.get("reference_answer", ""),
                "rubric": obj.get("rubric", []),
                "tags": obj.get("tags", []),
                "provenance": {
                    "generator_model": args.model,
                    "model_license": args.license,
                    "endpoint": args.base_url,
                    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "prompt_version": PROMPT_VERSION,
                    "prompt_sha256": hashlib.sha256((SYSTEM + user).encode()).hexdigest()[:16],
                    "gen_seconds": round(dt, 1),
                    "reviewed_by_human": False,
                },
            }
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            f.flush()
            ok += 1
            print(f"[{i}] ok {ck}/{dk}/{lang} {dt:.0f}s", file=sys.stderr)

    total = time.time() - t0
    print(json.dumps({
        "summary": "generate",
        "model": args.model,
        "license": args.license,
        "ok": ok,
        "fail": fail,
        "seconds": round(total),
        "sec_per_item": round(total / max(ok, 1)),
        "prompt_tokens": tok_in,
        "completion_tokens": tok_out,
        "out": args.out,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
