"""apply_strings.py [--write] <py>... : docstring でない文字列リテラルに rules_code の規則を当てる（⚠ 動きに出る。全部の置き換えを目で見てから --write）。
SKIP に書いた (ファイル名の末尾, 置き換え前の文字) は当てない。"""
import ast, io, sys, tokenize, re
sys.path.insert(0, sys.argv[0].rsplit("/", 1)[0])
from rules_code import rules_for
from apply_code import doc_spans
try: from strings_skip import SKIP
except ImportError: SKIP = set()
write = "--write" in sys.argv
total = 0
for f in [a for a in sys.argv[1:] if a.endswith(".py")]:
    src = open(f, encoding="utf-8").read()
    docs = doc_spans(ast.parse(src)); lines = src.split("\n")
    offs, pos = [], 0
    for l in lines: offs.append(pos); pos += len(l) + 1
    out, last = [], 0
    for t in tokenize.generate_tokens(io.StringIO(src).readline):
        if t.type not in (tokenize.STRING, getattr(tokenize, "FSTRING_MIDDLE", -1)): continue
        if t.type == tokenize.STRING and (t.start[0], len(lines[t.start[0] - 1][:t.start[1]].encode("utf-8"))) in docs: continue
        s, e = offs[t.start[0] - 1] + t.start[1], offs[t.end[0] - 1] + t.end[1]
        seg = src[s:e]; new = seg
        for pat, rep in rules_for(f):
            def sub(m, rep=rep):
                global total
                key = (f.rsplit("/", 1)[-1], m.group(0))
                ctx = new[max(0, m.start() - 16):m.end() + 14].replace("\n", "⏎")
                if key in SKIP or (f.rsplit("/", 1)[-1], ctx) in SKIP: return m.group(0)
                total += 1
                print(f"{f.rsplit('/', 1)[-1][:24]:24} L{t.start[0]:<5} {new[max(0, m.start() - 16):m.start()]}【{m.group(0)}→{rep}】{new[m.end():m.end() + 14]}".replace("\n", "⏎"))
                return rep
            new = re.sub(pat, sub, new)
        if new != seg: out.append(src[last:s]); out.append(new); last = e
    if out and write:
        out.append(src[last:]); new_src = "".join(out); ast.parse(new_src)
        open(f, "w", encoding="utf-8").write(new_src)
print(f"# {total} か所" + ("（書いた）" if write else "（試し）"))
