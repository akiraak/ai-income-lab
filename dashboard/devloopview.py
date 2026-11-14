"""「開発ループ」の画面（外部のタブ「実行」の 1 ページ ＝ `/experiments/view?item=devloop`。接頭辞 `/devloop` でも同じ中身）。
仕様は docs/specs/dashboard.md §20。rules.md 31 章・プラン docs/plans/daily-dev-loop.md（2026-10-25）。

  - ⚠ **読むだけ・標準ライブラリだけ**。開くのは 登録簿（`experiments/feature-discovery/config/ideas/*.toml`）・数と置き場（`config/devloop.toml`）・
    日誌（`docs/specs/experiments/dev-loop/log/*.md`）・研究の DB の `ledger_rows`（読み取り専用。登録簿の実行 → 検証結果一覧の行）。
    `out/`・`state/`・`.env` を読まない。操作の経路は無い（止める手は `DEVLOOP_HALT` ＝ CLI ／ 利用者）
  - ⚠ **良し悪し・意味の有無の印を付けない**（利用者の指示 2026-10-24）。印は 手間・測れるか・触る場所。判定は検証結果一覧のまま写す
  - ⚠ **並べたものであって採用ではない**（候補の人・本番は利用者。成績で人を選ばない）。「予測モデル」「トレーダー」のタブにはリンクしない
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import compareview
import traderview
from traderview import TraderPaths, esc

ITEM = "devloop"
SIDEBAR_LABEL = "開発ループ"
SIDEBAR_SUB = "登録簿 × 結果 × 日誌（読むだけ）"
STATUS_CLASS = {"登録": "hold", "作成中": "hold", "回した": "adopt", "既出": "drop", "失敗": "drop", "共有コード変更": "manual", "人の手": "manual", "案": ""}
CSS = compareview.CSS + """
 .chip.manual { background: color-mix(in srgb, #6f42c1 18%, transparent); border-color: #6f42c1; }
 pre.journal { font-size: 12px; white-space: pre-wrap; max-height: 36em; overflow: auto; padding: 8px; border: 1px solid color-mix(in srgb, currentColor 25%, transparent); }
 table.cmp td.q { max-width: 28em; }
"""


# ---------------------------------------------------------------- 読む


def load_ideas(paths: TraderPaths) -> list[dict]:
    out = []
    d = Path(paths.ideas_dir)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.toml")):
        try:
            with open(p, "rb") as f:
                doc = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            doc = {"id": p.stem, "title": "⚠ 読めない TOML", "status": "?"}
        out.append(doc)
    return out


def load_settings(paths: TraderPaths) -> dict:
    try:
        with open(paths.devloop_toml, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def journals(paths: TraderPaths) -> list[Path]:
    d = Path(paths.devloop_log)
    return sorted(d.glob("*.md"), reverse=True) if d.is_dir() else []


def rows_by_run(paths: TraderPaths) -> dict[str, list[compareview.Row]]:
    """実行名 → その実行を含む検証結果一覧の行（leak = 0・数える行）。"""
    rows = compareview.ledger(paths) or {}
    out: dict[str, list[compareview.Row]] = {}
    for r in rows.values():
        for run in r.doc.get("実行一覧") or []:
            out.setdefault(str(run), []).append(r)
    return out


# ---------------------------------------------------------------- 描く


def _chip(status: str) -> str:
    cls = STATUS_CLASS.get(status, "")
    return f"<span class='chip {cls}'>{esc(status)}</span>"


def _result_cells(idea: dict, by_run: dict[str, list[compareview.Row]]) -> str:
    runs = [str(r) for r in (idea.get("runs") or [])]
    if not runs:
        return "—"
    parts = []
    seen: set[str] = set()
    for run in runs:
        for r in by_run.get(run, []):
            if r.name in seen or compareview.SHAPED.search(r.method) is None and r.method.startswith("基準 "):
                continue
            seen.add(r.name)
            edge = "—" if r.edge is None else compareview._fmt_edge(r.edge)
            parts.append(f"<span class='sub'>θ{esc(r.theta)}</span> {esc(edge)} <span class='fold'>{esc(r.fold)}</span> "
                         f"<span class='chip {compareview.VERDICT_CLASS.get(r.verdict, '')}'>{esc(r.verdict)}</span>")
    return "<br>".join(parts) if parts else f"<span class='sub'>実行 {len(runs)}・行なし（検証結果一覧を吐き直す前）</span>"


def page_body(paths: TraderPaths) -> str:
    ideas = load_ideas(paths)
    st = load_settings(paths)
    by_run = rows_by_run(paths)
    ide = st.get("ideation") or {}
    out = [f"<h1>{esc(SIDEBAR_LABEL)}</h1>",
           "<p class='note'>毎晩「アイデア出し → 作る → 机上で検証 → 選ぶ」を 1 周回す仕組み（rules.md 31 章）。正本は登録簿の TOML と日誌で、ここは写すだけ。</p>",
           "<ul class='caution'>"
           "<li>⚠ 良し悪し・意味の有無の印は無い（利用者の指示 2026-10-24）。印は 手間・測れるか・触る場所 の 3 つ。判定は検証結果一覧の列をそのまま写す。</li>"
           "<li>⚠ 段 4 の「上位 3」は並べたものであって採用ではない。候補の人にするか・本番に入れるかは利用者（成績で人を選ばない）。</li>"
           "<li>操作の経路は無い。止める手は <code>experiments/feature-discovery/DEVLOOP_HALT</code>（利用者）。</li></ul>"]
    if st:
        sched, run = st.get("schedule") or {}, st.get("run") or {}
        out.append(f"<p class='meta'>設定: 参考の度合い r {esc(ide.get('reference_pct'))}%・1 晩 N {esc(ide.get('ideas_per_night'))} 本・"
                   f"段 1 ≤ ${esc(ide.get('max_budget_usd'))}・段 2 {esc((st.get('build') or {}).get('mode'))} ≤ ${esc((st.get('build') or {}).get('max_budget_usd'))}・"
                   f"1 晩 ≤ {esc(sched.get('total_hours'))} 時間・数える検証数 ≤ {esc(run.get('max_trials_per_night'))}・{esc(sched.get('time_local'))} {esc(sched.get('timezone'))}</p>")
    counts: dict[str, int] = {}
    for d in ideas:
        counts[str(d.get("status"))] = counts.get(str(d.get("status")), 0) + 1
    stamp = compareview.built_at(paths)
    out.append(f"<h2>登録簿 {len(ideas)} 本（" + "・".join(f"{esc(k)} {v}" for k, v in sorted(counts.items())) + "）"
               + (f" <small>検証結果一覧 {esc(stamp)}</small>" if stamp else "") + "</h2>")
    head = [("id", False), ("題", False), ("種類", False), ("出どころ", False), ("状態", False), ("登録時刻", False), ("数える", True),
            ("手間", False), ("触る場所", False), ("結果（検証結果一覧の行）", False)]
    rows = []
    for d in sorted(ideas, key=lambda x: str(x.get("id")), reverse=True):
        o = d.get("origin") or {}
        src = "参考あり" if o.get("source") == "reference" else "参考なし"
        if d.get("reference_mode"):
            src += f"（{d['reference_mode']}）"
        title = f"<b>{esc(d.get('title'))}</b>" + (f"<br><span class='sub'>{esc(str(d.get('question'))[:80])}</span>" if d.get("question") else "")
        rows.append(f"<tr><td><code>{esc(d.get('id'))}</code></td><td class='q'>{title}</td><td>{esc(d.get('kind'))}</td><td>{esc(src)}</td>"
                    f"<td>{_chip(str(d.get('status')))}</td><td class='v'>{esc(d.get('registered_at') or '—')}</td><td class='num'>{esc(d.get('n_trials_planned'))}</td>"
                    f"<td>{esc(d.get('effort'))}</td><td class='q'>{esc(str(d.get('touches') or '')[:60])}</td><td>{_result_cells(d, by_run)}</td></tr>")
    out.append(compareview._table(head, rows) if rows else "<p class='sub'>登録簿はまだ無い</p>")
    js = journals(paths)
    out.append(f"<h2>日誌 {len(js)} 晩</h2>")
    if js:
        latest = js[0]
        try:
            text = latest.read_text(encoding="utf-8")
        except OSError:
            text = "（読めない）"
        # ⚠ 原文（段 1 の出力）は長いので、日誌の頭から「段 1 の原文」の前までを出す
        cut = text.find("## 段 1 の原文")
        shown = text if cut < 0 else text[:cut] + "\n（段 1 の原文は日誌のファイルで）"
        out.append(f"<p class='note'>最新 <code>{esc(latest.name)}</code>" + (f"・ほか " + "、".join(f"<code>{esc(p.name)}</code>" for p in js[1:8]) if len(js) > 1 else "") + "</p>")
        out.append(f"<pre class='journal'>{esc(shown)}</pre>")
    else:
        out.append("<p class='sub'>まだ 1 晩も回っていない（試運転はプラン Phase 3-7）</p>")
    out.append(f"<script>{compareview.SCRIPT}</script>")
    return "\n".join(out)


def body(paths: TraderPaths, item: str) -> str | None:
    return page_body(paths) if item in ("all", "") else None


def sidebar(paths: TraderPaths) -> dict:
    return {"items": [{"id": "all", "label": SIDEBAR_LABEL, "sub": SIDEBAR_SUB, "group": "開発ループ"}]}


def fingerprint(paths: TraderPaths) -> dict[str, float]:
    """見張り用: 登録簿・日誌・設定の mtime ＋ 検証結果一覧を DB に入れ直した時刻（compareview の指紋）。"""
    out = compareview.fingerprint(paths)
    ideas = list(Path(paths.ideas_dir).glob("*.toml")) if Path(paths.ideas_dir).is_dir() else []
    for p in [Path(paths.devloop_toml), *ideas, *journals(paths)]:
        try:
            out[str(p)] = p.stat().st_mtime
        except OSError:
            pass
    return out
