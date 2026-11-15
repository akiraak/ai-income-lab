"""apply_code.py [--write] [--show N] <py>... : Python のコメントと docstring だけに規則を当てる。
⚠ 文字列リテラル（メッセージ・値・項目名）は触らない。当てた後に「docstring を除いた AST が同じ」「行数が同じ」を確かめ、違えばそのファイルは書かない。"""
import ast, io, sys, tokenize, re, random
sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0])
import trlib
from rules_code import rules_for

def doc_spans(tree):
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
            e = n.body[0]
            if isinstance(e, ast.Expr) and isinstance(e.value, ast.Constant) and isinstance(e.value.value, str):
                out.add((e.value.lineno, e.value.col_offset))
    return out

def strip_docs(tree):
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body:
            e = n.body[0]
            if isinstance(e, ast.Expr) and isinstance(e.value, ast.Constant) and isinstance(e.value.value, str):
                e.value.value = ""
    return ast.dump(tree)

def process(path, write, shown):
    src = open(path, encoding="utf-8").read()
    try: tree = ast.parse(src)
    except SyntaxError as e: return None, f"構文エラー {e}"
    docs = doc_spans(tree)
    lines = src.split("\n")
    # 行頭からのオフセット
    offs, pos = [], 0
    for l in src.split("\n"): offs.append(pos); pos += len(l) + 1
    def off(rc): return offs[rc[0] - 1] + len(lines[rc[0] - 1].encode("utf-8")[:rc[1]].decode("utf-8", "ignore")) if False else offs[rc[0] - 1] + rc[1]
    spans = []
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type == tokenize.COMMENT: spans.append((off(t.start), off(t.end)))
        elif t.type == tokenize.STRING:
            # ast の col_offset は UTF-8 のバイト。tokenize は文字。行の頭からのバイト数に直して比べる
            b = len(lines[t.start[0] - 1][:t.start[1]].encode("utf-8"))
            if (t.start[0], b) in docs: spans.append((off(t.start), off(t.end)))
    rules = rules_for(path)
    out, last, n = [], 0, 0
    for s, e in spans:
        seg = src[s:e]
        new, counts, sh = trlib.apply(seg, rules, show={p for p, _ in rules})
        k = sum(c for _, c in counts)
        if k: n += k; shown += [f"{path.rsplit('/', 1)[-1][:22]:22} {x.split(' ', 1)[1].strip()[:70]}" for x in sh]
        out.append(src[last:s]); out.append(new); last = e
    out.append(src[last:]); new_src = "".join(out)
    if n == 0: return 0, None
    if new_src.count("\n") != src.count("\n"): return None, "行数が変わった"
    try:
        if strip_docs(ast.parse(new_src)) != strip_docs(ast.parse(src)): return None, "AST が変わった"
    except SyntaxError as e: return None, f"当てた後に構文エラー {e}"
    if write: open(path, "w", encoding="utf-8").write(new_src)
    return n, None

if __name__ == "__main__":
    write = "--write" in sys.argv
    show = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 0
    files = [a for a in sys.argv[1:] if a.endswith(".py")]
    total, shown, bad = 0, [], []
    for f in files:
        n, err = process(f, write, shown)
        if err: bad.append((f, err))
        else: total += n
    print(f"# {len(files)} ファイル・{total} か所" + ("（書いた）" if write else "（試し）"))
    for f, e in bad: print(f"⚠ 書かなかった: {f}: {e}")
    if show:
        random.seed(0)
        for x in random.sample(shown, min(show, len(shown))): print(x)
