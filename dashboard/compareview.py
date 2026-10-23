"""「モデルとトレーダーの比較」の画面（外部のタブ「実行」の 1 ページ ＝ `/experiments/view?item=compare`。接頭辞 `/compare` でも同じ中身）。
仕様は docs/specs/dashboard.md §19。利用者の指示（2026-10-22）「既存のモデルとトレーダーの検証結果を比較できるように表示して」。

  - ⚠ **数字は全部 研究の DB の `ledger_rows`**（検証結果一覧の生成物。leak = 0・n_trials に数える行）から引く。手で写す欄は無い
  - ⚠ **人 ／ モデル → 検証名の対応と画面の言葉は `dashboard/compare.toml`**（説明をこのコードに書かない）。
    トレーダーの形の行の名前（5 本 ／ 全銘柄）は、素の形の検証名の 2 つ目の部品に `-<shape>` ／ `-trall` を差し込んで機械で作る
    （綴りは `experiments/feature-discovery/config/names.toml` の `[trader]`。rules.md 20-4 の 2）
  - ⚠ **「予測モデル」「トレーダー」のタブの決まり（見くらべる表・順位・くらべる文を置かない）は変えない**。このページは研究の側の表で、
    用語（θ・bp・fold・B&H）をそのまま使い、あちらからここへのリンクは置かない
  - ⚠ **成績で人を選ばない**（CLAUDE.md）。ページの頭に注意を出す。⚠ 売買結果（損益・注文）は出さない（3012 の管理画面の仕事）
  - ⚠ **読むだけ・標準ライブラリだけ**。開くのは TOML（compare ／ traders ／ models ／ names）・実売買の設定・研究の DB（読み取り専用）。
    `out/`・`state/`・`.env` を読まない
"""

from __future__ import annotations

import fnmatch
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import modelview
import traderview
from traderview import TraderPaths, esc

THETAS = ("50", "55", "60")
SHAPED = re.compile(r"〔トレーダー・[^〕]*〕")   # トレーダーの形の行の印（手法名の末尾。rules.md 20-4）
VERDICT_CLASS = {"採る": "adopt", "保留": "hold", "落とす": "drop"}
TRALL = "trall"                      # 全銘柄・全部を買える予算の綴り（names.toml [trader]）
ITEM = "compare"                     # 実行タブの目次の id
SIDEBAR_LABEL = "モデルとトレーダーの比較"
SIDEBAR_SUB = "検証結果一覧から（研究の DB を読むだけ）"
# 2026-10-21 までの設計のまま: 外部リソースなし（JS は並べ替えだけ・同梱）
CSS = """
 table.cmp { font-size: 12px; }
 table.cmp th { cursor: pointer; white-space: nowrap; user-select: none; }
 table.cmp th[data-dir='asc']::after { content: ' ▲'; opacity: .6; }
 table.cmp th[data-dir='desc']::after { content: ' ▼'; opacity: .6; }
 table.cmp td.v { white-space: nowrap; }
 table.cmp td.none { opacity: .45; text-align: center; }
 table.cmp td.who { min-width: 9em; }
 table.cmp td.m { min-width: 12em; }
 table.cmp td.u { min-width: 8em; }
 table.cmp td .sub.role { white-space: nowrap; }
 .edge { font-weight: 600; font-variant-numeric: tabular-nums; }
 .fold { font-family: ui-monospace, monospace; font-size: 11px; opacity: .85; }
 .chip { display: inline-block; padding: 0 6px; border-radius: 9px; font-size: 11px; line-height: 16px; border: 1px solid transparent; }
 .chip.adopt { background: color-mix(in srgb, #2f8f6b 22%, transparent); border-color: #2f8f6b; }
 .chip.hold { background: color-mix(in srgb, #d9a21b 24%, transparent); border-color: #b8860b; }
 .chip.drop { background: color-mix(in srgb, currentColor 10%, transparent); opacity: .8; }
 sup.closed { font-size: 10px; opacity: .7; margin-left: 2px; cursor: help; }
 .sub { opacity: .65; font-size: 11px; }
 .note { font-size: 12px; margin: 2px 0; }
 ul.caution { margin: 4px 0 10px 18px; padding: 0; }
 ul.caution li { margin: 2px 0; }
 .formal { font-size: 11px; opacity: .75; }
 a.t { text-decoration: none; }
"""
SCRIPT = """
(function () {
  document.querySelectorAll('table.cmp').forEach(function (t) {
    var ths = t.querySelectorAll('thead th');
    var tb = t.querySelector('tbody');
    ths.forEach(function (th, i) {
      th.addEventListener('click', function () {
        var num = th.dataset.num === '1';
        var dir = th.dataset.dir ? (th.dataset.dir === 'asc' ? 'desc' : 'asc') : (num ? 'desc' : 'asc');
        ths.forEach(function (h) { delete h.dataset.dir; });
        th.dataset.dir = dir;
        var key = function (r) {
          var c = r.children[i];
          if (!c) { return [1, '']; }
          var s = c.dataset.s;
          if (s !== undefined && s !== '') { return [0, parseFloat(s)]; }
          var txt = c.textContent.trim();
          return [(txt === '' || txt === '—') ? 1 : 0, txt];
        };
        var rows = Array.prototype.slice.call(tb.querySelectorAll('tr'));
        rows.sort(function (a, b) {
          var ka = key(a), kb = key(b);
          if (ka[0] !== kb[0]) { return ka[0] - kb[0]; }          // 空欄はいつも最後
          if (typeof ka[1] === 'number' && typeof kb[1] === 'number') {
            return dir === 'asc' ? ka[1] - kb[1] : kb[1] - ka[1];
          }
          var c = String(ka[1]).localeCompare(String(kb[1]), 'ja', { numeric: true });
          return dir === 'asc' ? c : -c;
        });
        rows.forEach(function (r) { tb.appendChild(r); });
      });
    });
  });
})();
"""


# ---------------------------------------------------------------- 読む


@dataclass(frozen=True)
class Row:
    """検証結果一覧の 1 行（`ledger_rows` の写し。`doc` は行まるごとの JSON）。"""
    name: str            # 検証名（trial_name。θ つき）
    model: str           # 予測モデル名（θ なし）
    verdict: str
    closed: bool
    doc: dict

    @property
    def edge(self) -> float | None:
        v = self.doc.get("上乗せbp")
        return float(v) if isinstance(v, (int, float)) else None

    @property
    def fold(self) -> str:
        return str(self.doc.get("上乗せfold") or self.doc.get("fold") or "")

    @property
    def method(self) -> str:
        return str(self.doc.get("手法名") or "")

    @property
    def theta(self) -> str:
        return str(self.doc.get("閾値") or "")

    @property
    def run_day(self) -> str:
        return str(self.doc.get("実行") or "")[:10]

    @property
    def reason(self) -> str:
        """「理由」の列を印の上の文にする（Markdown の飾りを落とす）。"""
        s = str(self.doc.get("理由") or "")
        s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
        return s.replace("**", "").replace("`", "")


def load_words(paths: TraderPaths) -> dict:
    """`compare.toml` ＝ `common`（言葉）・`trader`（人 → 検証名）・`model`（モデル → 素の形）。無ければ空。"""
    doc = traderview._load(paths.compare)
    return {"common": doc.get("common") or {}, "trader": list(doc.get("trader") or []), "model": list(doc.get("model") or [])}


def shapes(paths: TraderPaths, words: dict) -> list[str]:
    """トレーダーの形の綴り（`names.toml` の `[trader]` の値 ＋ compare.toml に書いた `shape`）。長いものから（`tr5-600` を `tr5` より先に当てる）。"""
    found = {str(v) for v in (traderview._load(paths.names).get("trader") or {}).values()}
    for t in words.get("trader") or []:
        for f in t.get("forms") or []:
            if f.get("shape"):
                found.add(str(f["shape"]))
    found.add(TRALL)
    return sorted(found, key=lambda s: (-len(s), s))


def with_shape(name: str, shape: str) -> str:
    """素の形の検証名 → トレーダーの形の検証名（2 つ目の部品に `-<shape>` を差し込む。rules.md 20-4 の 2・names.toml [trader]）。"""
    parts = name.split(".", 2)
    if len(parts) < 3:
        return name
    return f"{parts[0]}.{parts[1]}-{shape}.{parts[2]}"


def strip_shape(name: str, shape_list: list[str]) -> tuple[str, str | None]:
    """トレーダーの形の検証名 → (素の形の検証名, 綴り)。形でなければ (name, None)。"""
    parts = name.split(".", 2)
    if len(parts) < 3:
        return name, None
    for sh in shape_list:
        if parts[1].endswith("-" + sh):
            return f"{parts[0]}.{parts[1][:-len(sh) - 1]}.{parts[2]}", sh
    return name, None


def ledger(paths: TraderPaths) -> dict[str, Row] | None:
    """研究の DB の検証結果一覧（leak = 0・n_trials に数える行）を検証名で引ける形に。⚠ 読み取り専用。DB が無い・開けない・表が無いときは None。"""
    db = paths.research_db
    if db is None or not Path(db).is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True, timeout=10)
        try:
            rows = conn.execute("SELECT trial_name, model_name, verdict, closed, doc FROM ledger_rows"
                                " WHERE leak = 0 AND is_trial = 1").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    out: dict[str, Row] = {}
    for name, model, verdict, closed, doc in rows:
        try:
            d = json.loads(doc) if doc else {}
        except (TypeError, ValueError):
            d = {}
        out[str(name)] = Row(str(name), str(model), str(verdict), bool(closed), d if isinstance(d, dict) else {})
    return out


def built_at(paths: TraderPaths) -> str:
    """検証結果一覧を DB に入れ直した時刻（`meta.ledger_built_at`）。無ければ ""。"""
    db = paths.research_db
    if db is None or not Path(db).is_file():
        return ""
    try:
        conn = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True, timeout=10)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'ledger_built_at'").fetchone()
        finally:
            conn.close()
        return str(row[0]) if row else ""
    except sqlite3.Error:
        return ""


# ---------------------------------------------------------------- 描く部品


def _fmt_edge(v: float) -> str:
    s = f"{abs(v):,.1f}"
    return ("+" if v >= 0 else "−") + s


def _chip(common: dict, row: Row) -> str:
    cls = VERDICT_CLASS.get(row.verdict, "")
    closed = (f"<sup class='closed' title='{esc(row.reason)}'>{esc(common.get('closed_mark') or '閉')}</sup>"
              if row.closed else "")
    return f"<span class='chip {cls}'>{esc(row.verdict)}</span>{closed}"


def cell(common: dict, row: Row | None, name: str, *, label: str = "") -> str:
    """1 つの条件の欄: 上乗せ bp/fold ／ fold の符号 ／ 判定（＋ 閉じる注記の印）。行が無ければ「—」（印の上に検証名）。"""
    if row is None:
        return f"<td class='none' title='{esc(common.get('no_rows_model') or '行なし')}: {esc(name)}'>{esc(common.get('missing') or '—')}</td>"
    head = f"<span class='sub'>{esc(label)}</span><br>" if label else ""
    edge = row.edge
    if edge is None:
        # 毎日往復など上乗せの無い行は判定だけ
        return f"<td class='v' data-s='' title='{esc(row.name)} ／ {esc(row.run_day)}'>{head}{_chip(common, row)}</td>"
    return (f"<td class='v' data-s='{edge:.4f}' title='{esc(row.name)} ／ {esc(row.run_day)}'>{head}"
            f"<span class='edge'>{esc(_fmt_edge(edge))}</span> <span class='fold'>{esc(row.fold)}</span><br>{_chip(common, row)}</td>")


def _table(headers: list[tuple[str, bool]], rows: list[str]) -> str:
    th = "".join(f"<th{' data-num=1' if num else ''}>{esc(h)}</th>" for h, num in headers)
    return f"<table class='cmp'><thead><tr>{th}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", str(s or ""))


def _model_link(m: dict | None, spec: dict) -> str:
    if m is None:
        return f"<code>{esc(spec.get('name'))}</code> {esc(spec.get('method'))}"
    label = _strip_tags(m.get("label") or m.get("id") or "")
    return f"<a class='t' href='{traderview.MODEL_URL}{esc(m.get('id') or '')}'>{esc(label)}</a>"


# ---------------------------------------------------------------- 1. トレーダー


def _trader_rows(paths: TraderPaths, words: dict, twords: dict, rows: dict[str, Row] | None) -> tuple[list[str], set[str]]:
    """人の表の行と、使った素の形の検証名の集合（3 の表で除くため）。"""
    common = words["common"]
    declared = {str(t.get("id")): t for t in words["trader"] if t.get("id")}
    role_words = {r: twords["common"].get(f"role_{r}") or r for r in ("both", "buy", "exit", "stop", "watch")}
    out: list[str] = []
    claimed: set[str] = set()
    listed = [k for k, _f, _ok in traderview.trader_files(paths)]
    for name in listed + [k for k in sorted(declared, key=traderview._natural_key) if k not in listed]:
        facts = traderview.load_facts(paths, name)
        nick = twords["nicks"].get(name)
        who = f"{esc(nick)}（{esc(name)}）" if nick else esc(name)
        who = f"<a class='t' href='{traderview.TRADER_URL}{esc(name)}'>{who}</a>"
        t = declared.get(name) or {}
        if facts is None:
            status = f"<span class='warn'>{esc(common.get('not_in_config') or '設定に居ない')}</span>"
            models_html, theta, budget = "", "", ""
        else:
            status = esc(common.get("status_settled" if facts["settled"] else "status_candidate") or "")
            parts = []
            for spec in facts["models"]:
                m = traderview.find_model(twords, spec)
                role = str(spec.get("role") or "both")
                suffix = f" <span class='sub role'>{esc(role_words[role])}</span>" if role != "both" else ""
                parts.append(_model_link(m, spec) + suffix)
            combine = facts.get("combine") or "asis"
            comb = f"<br><span class='sub'>{esc(combine)}</span>" if facts["k"] > 1 else ""
            models_html = "<br>".join(parts) + comb
            theta = f"{facts['line']:g}"
            budget = f"{traderview._money(facts['budget'])} ／ {facts['n']}"
        if t.get("note"):
            status += f"<br><span class='sub'>{esc(t['note'])}</span>"
        record = (f"<a href='{modelview.doc_url(str(t['record']))}'>{esc(Path(str(t['record'])).stem)}</a>"
                  if t.get("record") else "")
        forms = [f for f in (t.get("forms") or []) if f.get("base")]
        if not forms:
            none = esc(common.get("no_mapping") or "対応なし")
            out.append(f"<tr><td class='who'>{who}</td><td>{status}</td><td>{models_html}</td><td class='num'>{theta}</td><td>{budget}</td>"
                       f"<td class='none'>{none}</td><td class='none'>{none}</td><td class='none'>{none}</td><td>{record}</td></tr>")
            continue
        for f in forms:
            base = str(f["base"])
            shape = str(f.get("shape") or "tr5")
            label = str(f.get("label") or "")
            claimed.update({base, with_shape(base, shape), with_shape(base, TRALL)})
            who_cell = who + (f"<br><span class='sub'>{esc(label)}</span>" if len(forms) > 1 and label else "")
            get = (rows or {}).get
            out.append(f"<tr><td class='who'>{who_cell}</td><td>{status}</td><td>{models_html}</td><td class='num'>{theta}</td><td>{budget}</td>"
                       + cell(common, get(base), base)
                       + cell(common, get(with_shape(base, shape)), with_shape(base, shape),
                              label=_shape_label(get(with_shape(base, shape))))
                       + cell(common, get(with_shape(base, TRALL)), with_shape(base, TRALL))
                       + f"<td>{record}</td></tr>")
    return out, claimed


def _shape_label(row: Row | None) -> str:
    """5 本の欄の頭に、検証した条件（`〔トレーダー・5本・$300〕` の中身）を出す。"""
    if row is None:
        return ""
    m = SHAPED.search(row.method)
    return m.group(0)[len("〔トレーダー・"):-1] if m else ""


# ---------------------------------------------------------------- 2. 予測モデル


def _current_patterns(m: dict) -> list[str]:
    """経緯の表のうち、いまの印に数える行（`old`・`aside` を除く）の予測モデル名のパターン（modelview.best_verdict と同じ選び方）。"""
    return [str(n) for r in m.get("history") or [] if r.get("what") and not (r.get("old") or r.get("aside"))
            for n in (r.get("names") or [])]


def _matching(rows: dict[str, Row], patterns: list[str]) -> list[Row]:
    return [r for r in rows.values() if any(fnmatch.fnmatchcase(r.model, p) for p in patterns)]


def _counts_html(common: dict, hit: list[Row]) -> str:
    n = {v: 0 for v in VERDICT_CLASS}
    for r in hit:
        if r.verdict in n:
            n[r.verdict] += 1
    unit = esc(common.get("rows_unit") or "行")
    return (f"<span class='edge'>{len(hit)}</span> {unit}<br><span class='sub'>"
            + " ／ ".join(f"{esc(v)} {n[v]}" for v in VERDICT_CLASS) + "</span>")


def _model_rows(paths: TraderPaths, words: dict, twords: dict, rows: dict[str, Row] | None) -> list[str]:
    common = words["common"]
    overrides = {str(x.get("id")): x for x in words["model"] if x.get("id")}
    # 使う人: 設定の [[models]] がどの解説に当たるか（traderview.find_model と同じ当て方）
    users: dict[str, list[str]] = {}
    for name, _f, _ok in traderview.trader_files(paths):
        facts = traderview.load_facts(paths, name)
        for spec in (facts or {}).get("models") or []:
            m = traderview.find_model(twords, spec)
            if m is not None and m.get("id"):
                nick = twords["nicks"].get(name)
                users.setdefault(str(m["id"]), []).append(f"{nick}（{name}）" if nick else name)
    out: list[str] = []
    for m in twords["models"]:
        mid = str(m.get("id") or m.get("name") or "")
        ov = overrides.get(mid) or {}
        label = _strip_tags(m.get("label") or mid)
        formal = " ／ ".join(str(x) for x in (m.get("formal") or []) if x)
        ident = " ".join(x for x in (str(m.get("name") or ""), str(m.get("method") or "")) if x)
        head = (f"<a class='t' href='{traderview.MODEL_URL}{esc(mid)}'>{esc(label)}</a>")
        formal_html = f"{esc(formal)}<br><span class='formal'>{esc(ident)}</span>" if (formal or ident) else ""
        who = "、".join(dict.fromkeys(users.get(mid, []))) or ""
        pats = _current_patterns(m)
        hit = _matching(rows, pats) if rows is not None and pats else []
        counts = _counts_html(common, hit) if hit else esc(common.get("missing") or "—")
        # ⚠ 上乗せが最大の形は素の形（対 B&H）の中から。トレーダーの形の行（対 持ち続ける）は相手が違うので混ぜない
        with_edge = [r for r in hit if r.edge is not None and not SHAPED.search(r.method)]
        if with_edge:
            best = max(with_edge, key=lambda r: r.edge or 0.0)
            best_html = (f"<span class='sub'>{esc(best.method)} θ{esc(best.theta)}</span><br>"
                         f"<span class='edge'>{esc(_fmt_edge(best.edge or 0.0))}</span> <span class='fold'>{esc(best.fold)}</span> {_chip(common, best)}")
            best_cell = f"<td class='v' data-s='{best.edge:.4f}' title='{esc(best.name)} ／ {esc(best.run_day)}'>{best_html}</td>"
        else:
            best_cell = f"<td class='none'>{esc(common.get('missing') or '—')}</td>"
        bases = [str(b) for b in (ov.get("base") or [])]
        if not bases:
            note = ov.get("none") or ""
            form = f"<span class='sub'>{esc(note)}</span>" if note else ""
            out.append(f"<tr><td class='m'>{head}</td><td>{formal_html}</td><td class='u'>{esc(who)}</td><td>{form}</td>"
                       + "".join(f"<td class='none'>{esc(common.get('missing') or '—')}</td>" for _ in THETAS)
                       + f"<td class='num'>{counts}</td>{best_cell}</tr>")
            continue
        for b in bases:
            get = (rows or {}).get
            cells = "".join(cell(common, get(f"{b}@{th}"), f"{b}@{th}") for th in THETAS)
            out.append(f"<tr><td class='m'>{head}</td><td>{formal_html}</td><td class='u'>{esc(who)}</td><td><code>{esc(b)}</code></td>{cells}"
                       f"<td class='num'>{counts}</td>{best_cell}</tr>")
    return out


# ---------------------------------------------------------------- 3. 人のいない形


def _orphan_rows(common: dict, rows: dict[str, Row] | None, shape_list: list[str], claimed: set[str]) -> list[str]:
    """DB にある `〔トレーダー・…〕` の行のうち、人の表で使わなかったもの。1 行 ＝ 1 条件（素の形と、その条件の行を横に）。

    ⚠ 2026-10-22: 条件の綴りは tr5 ／ trall だけでなく選抜（`sel5-…`）も増えたので、同じ素の形に条件が何本あっても 1 本 1 行で出す。
    """
    if not rows:
        return []
    items = []
    for r in rows.values():
        base, sh = strip_shape(r.name, shape_list)
        if sh is None or base in claimed:
            continue
        items.append((base, sh, r))
    out: list[str] = []
    for base, sh, r in sorted(items, key=lambda x: (SHAPED.sub("", x[2].method), x[2].theta, x[1])):
        title = SHAPED.sub("", r.method).strip()
        cond = _shape_label(r) or sh
        out.append(f"<tr><td>{esc(title)} <span class='sub'>θ{esc(r.theta)}</span><br><code>{esc(base)}</code></td>"
                   f"<td>{esc(cond)}</td>" + cell(common, rows.get(base), base) + cell(common, r, r.name) + "</tr>")
    return out


# ---------------------------------------------------------------- ページ


def page_body(paths: TraderPaths) -> str:
    words = load_words(paths)
    common = words["common"]
    twords = traderview.load_words(paths)
    rows = ledger(paths)
    shape_list = shapes(paths, words)
    out = [f"<h1>{esc(common.get('title') or SIDEBAR_LABEL)}</h1>"]
    if common.get("lead"):
        out.append(f"<p class='note'>{esc(common['lead'])}</p>")
    stamp = built_at(paths)
    if stamp:
        out.append(f"<p class='meta'>{esc(common.get('db_built') or 'DB')}: {esc(stamp)} ・ {esc(paths.research_db)}</p>")
    if rows is None:
        out.append(f"<p class='warn'>{esc(common.get('no_db') or '⚠ 研究の DB が無い')}</p>")
    if common.get("caution"):
        out.append("<ul class='caution'>" + "".join(f"<li>{esc(c)}</li>" for c in common["caution"]) + "</ul>")

    trader_rows, claimed = _trader_rows(paths, words, twords, rows)
    out.append(f"<h2>{esc(common.get('section_traders') or 'トレーダー')}</h2>")
    out.append(_table([(common.get("col_trader") or "トレーダー", False), (common.get("col_status") or "状態", False),
                       (common.get("col_models") or "使うモデル", False), (common.get("col_theta") or "θ", True),
                       (common.get("col_budget") or "予算 ／ 銘柄", False), (common.get("col_base") or "素の形", True),
                       (common.get("col_tr5") or "5 本", True), (common.get("col_trall") or "全銘柄", True),
                       (common.get("col_record") or "記録", False)], trader_rows))

    out.append(f"<h2>{esc(common.get('section_models') or '予測モデル')}</h2>")
    out.append(_table([(common.get("col_model") or "モデル", False), (common.get("col_formal") or "正式な名前", False),
                       (common.get("col_users") or "使う人", False), (common.get("col_form") or "形", False),
                       *[(f"θ {th}", True) for th in THETAS],
                       (common.get("col_counts") or "試した数", True), (common.get("col_best") or "上乗せが最大の形", True)],
                      _model_rows(paths, words, twords, rows)))

    orphans = _orphan_rows(common, rows, shape_list, claimed)
    if orphans:
        out.append(f"<h2>{esc(common.get('section_orphans') or '人のいない形')}</h2>")
        out.append(_table([(common.get("col_orphan_name") or "形", False), (common.get("col_cond") or "条件", False),
                           (common.get("col_base") or "素の形", True), (common.get("col_cond_row") or "その条件（対 持ち続ける）", True)], orphans))

    if common.get("howto"):
        out.append(f"<h2>{esc(common.get('section_howto') or '読み方')}</h2>")
        out.append("<ul class='caution'>" + "".join(f"<li>{esc(c)}</li>" for c in common["howto"]) + "</ul>")
    out.append(f"<script>{SCRIPT}</script>")
    return "\n".join(out)


def body(paths: TraderPaths, item: str) -> str | None:
    """タブ `compare` の view（item は `all` だけ）。知らない item は None。"""
    return page_body(paths) if item in ("all", "") else None


def sidebar(paths: TraderPaths) -> dict:
    words = load_words(paths)
    return {"items": [{"id": "all", "label": words["common"].get("title") or SIDEBAR_LABEL, "sub": SIDEBAR_SUB, "group": "比較"}]}


def fingerprint(paths: TraderPaths) -> dict[str, float]:
    """見張り用。言葉と設定（predict モデルのタブと同じ）＋ 検証結果一覧を DB に入れ直した時刻 ＋ compare.toml。"""
    out = modelview.fingerprint(paths)
    try:
        out[str(paths.compare)] = paths.compare.stat().st_mtime
    except OSError:
        pass
    return out
