"""用語の置き換えの道具（使い捨て）。保護する範囲を除いて、文脈の表示と置き換えをする。"""
import re, sys

QUOTE = re.compile(r"利用者[^「」\n]{0,40}「[^」\n]*」(?:\s*[→／・]\s*「[^」\n]*」)*")
TICK = re.compile(r"`[^`\n]+`")
LINK = re.compile(r"\]\([^)\n]*\)")
PROTECT = []
FENCE = re.compile(r"^[ \t]*```.*?^[ \t]*```", re.S | re.M)

def protected(text, extra=(), fence=False):
    spans = [m.span() for rx in PROTECT for m in re.finditer(rx, text, re.M)]
    for rx in ((FENCE,) if fence else ()) + (TICK, LINK, QUOTE):
        spans += [m.span() for m in rx.finditer(text)]
    for a, b in extra:
        i = text.find(a)
        if i < 0: raise SystemExit(f"保護の開始が無い: {a[:30]}")
        j = text.find(b, i)
        if j < 0: raise SystemExit(f"保護の終了が無い: {b[:30]}")
        spans.append((i, j + len(b)))
    spans.sort()
    out = []
    for s, e in spans:
        if out and s <= out[-1][1]: out[-1] = (out[-1][0], max(out[-1][1], e))
        else: out.append((s, e))
    return out

def split(text, extra=(), fence=False):
    out, pos = [], 0
    for s, e in protected(text, extra, fence):
        if s > pos: out.append((False, text[pos:s]))
        out.append((True, text[s:e])); pos = e
    if pos < len(text): out.append((False, text[pos:]))
    return out

def contexts(text, pat, extra=(), w=14, fence=False):
    rx = re.compile(pat); res = []
    for prot, seg in split(text, extra, fence):
        if prot: continue
        for m in rx.finditer(seg):
            a, b = max(0, m.start() - w), min(len(seg), m.end() + w)
            res.append(seg[a:m.start()].replace("\n", "⏎") + "【" + m.group(0) + "】" + seg[m.end():b].replace("\n", "⏎"))
    return res

def apply(text, rules, extra=(), fence=False, show=()):
    """rules = [(正規表現, 置き換え)]。保護の外だけに順に当てる。(新しい文, [(規則, 数)], 見せる文脈) を返す。"""
    counts, shown = [], []
    parts = split(text, extra, fence)
    for pat, rep in rules:
        rx = re.compile(pat); n = 0
        for i, (prot, seg) in enumerate(parts):
            if prot: continue
            if pat in show:
                for m in rx.finditer(seg):
                    a, b = max(0, m.start() - 10), min(len(seg), m.end() + 10)
                    shown.append(f"{pat[:18]:18} {seg[a:m.start()]}【{m.group(0)}→{m.expand(rep)}】{seg[m.end():b]}".replace("\n", "⏎"))
            seg2, k = rx.subn(rep, seg)
            if k: parts[i] = (False, seg2); n += k
        counts.append((pat, n))
    return "".join(s for _, s in parts), counts, shown
