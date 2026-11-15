"""count_strings.py <py>... : docstring でない文字列リテラルに、読み分けが要らない言葉がいくつあるか（ファイル別）。"""
import ast, io, sys, tokenize, re, collections
sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0])
from rules_code import rules_for
from apply_code import doc_spans
tot = collections.Counter(); words = collections.Counter()
for f in sys.argv[1:]:
    src = open(f, encoding="utf-8").read()
    try: docs = doc_spans(ast.parse(src))
    except SyntaxError: continue
    lines = src.split("\n")
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type not in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)): continue
        if t.type == tokenize.STRING and (t.start[0], len(lines[t.start[0] - 1][:t.start[1]].encode("utf-8"))) in docs: continue
        for pat, rep in rules_for(f):
            k = len(re.findall(pat, t.string))
            if k: tot[f] += k; words[rep] += k
print("合計", sum(tot.values()), "／ ファイル", len(tot))
for f, n in tot.most_common(45): print(f"{n:4d} {f}")
print(words.most_common(30))
