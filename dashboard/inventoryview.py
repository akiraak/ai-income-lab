"""「棚卸し」の画面（外部のタブ「実行」の 1 ページ ＝ `/experiments/view?item=inventory`。接頭辞 `/inventory` でも同じ中身）。
仕様は docs/specs/dashboard.md §21。プラン docs/plans/archive/inventory-tab.md（2026-11-07）。

  - ⚠ **読むだけ・標準ライブラリだけ**。開くのは 研究の DB の `ledger_rows`（読み取り専用）・実験の config の頭（開発ループの行の区分）・
    棚卸しの文書と閉じる案の下書き（`docs/specs/experiments/ledger-hold-inventory*.md`・`assets/ledger-hold-inventory*closed-draft.toml`）。
    `out/`・`state/`・`.env` を読まない。操作の経路は無い（閉じるのは利用者の「閉じて」→ `config/catalog_notes.toml` の `[[closed]]`）
  - ⚠ **判定・`n_trials` は変えない**。「閉じる」は「効かないと確かめた」ではない（rules.md 14 章・2026-10-22 の棚卸しの規則）
  - ⚠ 区分は研究側の `cli.select_candidates.group_of` の写し（同じ結果になることをテストで見る）
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import compareview
from traderview import TraderPaths, esc

ITEM = "inventory"
SIDEBAR_LABEL = "棚卸し"
SIDEBAR_SUB = "保留のうち閉じる注記の無い行（読むだけ）"
HOLD = "保留"
CSS = compareview.CSS + """
 .chip.close { background: color-mix(in srgb, #8a8a8a 22%, transparent); border-color: #8a8a8a; }
 .chip.keep { background: color-mix(in srgb, #2a78d6 15%, transparent); border-color: #2a78d6; }
 table.cmp td.q { max-width: 30em; }
 .big { display: flex; gap: 18px; flex-wrap: wrap; margin: 6px 0 10px; }
 .big div { display: flex; flex-direction: column; } .big b { font-size: 22px; } .big span { font-size: 12px; opacity: .75; }
"""
# ⚠ `ail/catalog.py` の `_CLOSED_FIELDS` の写し（照合キー → 検証結果一覧の列）。足されたらここも足す（テストが研究側と突き合わせる）
CLOSED_FIELDS = {"key": "鍵", "model": "モデル", "layers": "特徴量の層", "style": "検証方式", "threshold": "閾値", "layer": "層",
                 "form": "形式", "granularity": "粒度", "period": "期間", "calibration": "較正", "symbols": "銘柄", "dataset": "集合"}
_RUN_EXP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_(.+)$")
_IDEA_IN_CONFIG = re.compile(r"登録簿 `(\d{8}-[a-z0-9-]+)`")
_KIND = re.compile(r"閉じる（[^）・]*・([^）]+)）")


# ---------------------------------------------------------------- 読む


def experiment_of(runs: list) -> str:
    m = _RUN_EXP.match(str((runs or [""])[0]))
    return m.group(1).removesuffix("_leak") if m else "?"


def group_of(experiment: str, experiment_dir: Path) -> str:
    """⚠ 研究側 `cli.select_candidates.group_of` の写し。"""
    if experiment.startswith("dev_tr_"):
        return "机上の持ち越し（31-3 の 3）"
    if experiment.startswith("dev_"):
        try:
            with open(Path(experiment_dir) / f"{experiment}.toml", encoding="utf-8") as f:
                m = _IDEA_IN_CONFIG.search(f.read(2000))
        except OSError:
            m = None
        return f"登録簿 {m.group(1)}" if m else f"開発ループ（id 不明）{experiment}"
    return re.sub(r"_t\d+b?$", "", experiment)


def drafts(paths: TraderPaths) -> list[tuple[Path, list[dict]]]:
    d = Path(paths.inventory_dir) / "assets"
    out = []
    for p in sorted(d.glob("ledger-hold-inventory*closed-draft.toml")) if d.is_dir() else []:
        try:
            with open(p, "rb") as f:
                out.append((p, list(tomllib.load(f).get("closed", []))))
        except (OSError, tomllib.TOMLDecodeError):
            out.append((p, []))
    return out


def documents(paths: TraderPaths) -> list[Path]:
    d = Path(paths.inventory_dir)
    return sorted(d.glob("ledger-hold-inventory*.md"), reverse=True) if d.is_dir() else []


def _matches(entry: dict, doc: dict) -> bool:
    cond = {CLOSED_FIELDS[k]: v for k, v in entry.items() if k in CLOSED_FIELDS}
    return bool(cond) and all(str(doc.get(col)) == str(v) for col, v in cond.items())


def note_kind(note: str) -> str:
    m = _KIND.search(str(note or ""))
    return m.group(1) if m else "閉じる"


def open_rows(paths: TraderPaths) -> tuple[list[dict], dict] | None:
    """(注記なしの保留の行, 数)。行 ＝ {row, group, experiment, proposal, kind, draft, note}。DB が無ければ None。"""
    led = compareview.ledger(paths)
    if led is None:
        return None
    ds = drafts(paths)
    held = [r for r in led.values() if r.verdict == HOLD]
    out = []
    for r in held:
        if r.closed:
            continue
        exp = experiment_of(r.doc.get("実行一覧") or [])
        hit = next(((p, e) for p, es in ds for e in es if _matches(e, r.doc)), None)
        out.append({"row": r, "group": group_of(exp, paths.experiment_dir), "experiment": exp,
                    "proposal": "閉じる" if hit else "残す", "kind": note_kind(hit[1].get("note")) if hit else "",
                    "draft": hit[0].name if hit else "", "note": str(hit[1].get("note") or "") if hit else ""})
    counts = {"held": len(held), "closed": sum(1 for r in held if r.closed), "open": len(out),
              "close": sum(1 for x in out if x["proposal"] == "閉じる"), "drafts": sum(len(es) for _p, es in ds)}
    return out, counts


# ---------------------------------------------------------------- 描く


def _plain_note(s: str) -> str:
    return re.sub(r"\*\*|`|\[([^\]]+)\]\([^)]*\)", lambda m: m.group(1) or "", s)


def _prop_chip(x: dict) -> str:
    if x["proposal"] == "閉じる":
        return f"<span class='chip close' title='{esc(_plain_note(x['note']))}'>閉じる（{esc(x['kind'])}）</span>"
    return "<span class='chip keep'>残す ／ 案なし</span>"


def page_body(paths: TraderPaths) -> str:
    out = [f"<h1>{esc(SIDEBAR_LABEL)}</h1>",
           "<p class='note'>検証結果一覧の「保留」のうち、閉じる注記の無い行を区分ごとに並べる（月 1 回の棚卸しの材料。rules.md 31 章・"
           "プラン daily-dev-loop.md 4-4）。研究の DB をいつ開いても最新で読む。</p>",
           "<ul class='caution'>"
           "<li>⚠ 判定・n_trials は変えない。「閉じる」は「効かないと確かめた」ではない（注記を足すだけで行は分母に残る）。</li>"
           "<li>⚠ 案は下書き（<code>docs/specs/experiments/assets/ledger-hold-inventory-*closed-draft.toml</code>）から写す。"
           "閉じるのは利用者の「閉じて」の後（Claude が <code>config/catalog_notes.toml</code> に貼って吐き直す）。この画面に操作は無い。</li>"
           "<li>⚠ 区分の処遇は記録で決める（行の数字の大小で選ばない）。根拠は棚卸しの文書の §2。</li></ul>"]
    got = open_rows(paths)
    if got is None:
        out.append("<p class='sub'>研究の DB（ledger_rows）が読めない</p>")
        return "\n".join(out)
    rows, c = got
    stamp = compareview.built_at(paths)
    keep = c["open"] - c["close"]
    out.append("<div class='big'>" + "".join(f"<div><b>{v}</b><span>{esc(k)}</span></div>" for v, k in (
        (c["held"], "保留"), (c["closed"], "閉じる注記つき"), (c["open"], "注記なし"), (c["close"], "案: 閉じる"), (keep, "案: 残す ／ 案なし")))
               + "</div>" + (f"<p class='meta'>検証結果一覧を DB に入れ直した時刻 {esc(stamp)}</p>" if stamp else ""))
    groups: dict[str, list[dict]] = {}
    for x in rows:
        groups.setdefault(x["group"], []).append(x)
    out.append(f"<h2>区分 {len(groups)}（注記なし {c['open']} 行）</h2>")
    head = [("区分", False), ("行", True), ("上乗せ bp/fold（最小〜最大）", False), ("fold の正の数", False), ("案", False), ("最初の実行", False)]
    body = []
    for g, xs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        edges = [x["row"].edge for x in xs if x["row"].edge is not None]
        rng = "—" if not edges else f"{compareview._fmt_edge(min(edges))} 〜 {compareview._fmt_edge(max(edges))}"
        pos = sorted(x["row"].fold.split(" ")[0].split("/")[0] for x in xs if x["row"].fold)
        n_close = sum(1 for x in xs if x["proposal"] == "閉じる")
        kinds = sorted({x["kind"] for x in xs if x["kind"]})
        prop = (f"閉じる {n_close}" + (f"（{esc('・'.join(kinds))}）" if kinds else "")) if n_close else ""
        prop += ("" if n_close == len(xs) else (" ／ " if n_close else "") + f"残す {len(xs) - n_close}")
        first = min(str((x["row"].doc.get("実行一覧") or ["—"])[0])[:10] for x in xs)
        body.append(f"<tr><td class='q'>{esc(g)}</td><td class='num'>{len(xs)}</td><td class='v'>{esc(rng)}</td>"
                    f"<td class='v'>{esc('・'.join(pos))}</td><td>{prop}</td><td class='v'>{esc(first)}</td></tr>")
    out.append(compareview._table(head, body) if body else "<p class='sub'>注記なしの保留は無い</p>")
    out.append("<h2>行の一覧</h2>")
    head = [("区分", False), ("検証名", False), ("手法", False), ("θ", True), ("上乗せ bp/fold", True), ("fold", False),
            ("並べ替え分位", False), ("並べ替えp", True), ("案", False), ("最初の実行", False)]
    body = []
    for x in sorted(rows, key=lambda x: (x["group"], x["row"].name)):
        r = x["row"]
        p = r.doc.get("並べ替えp")
        body.append(f"<tr><td class='q'>{esc(x['group'])}</td><td><code>{esc(r.name)}</code></td><td class='m'>{esc(r.method)}</td>"
                    f"<td class='num'>{esc(r.theta)}</td><td class='num edge'>{esc('—' if r.edge is None else compareview._fmt_edge(r.edge))}</td>"
                    f"<td><span class='fold'>{esc(r.fold)}</span></td><td class='v'>{esc(r.doc.get('並べ替え分位') or '—')}</td>"
                    f"<td class='num'>{esc('—' if p is None else ('<0.001' if p < 0.001 else f'{p:.3f}'))}</td>"
                    f"<td>{_prop_chip(x)}</td><td class='v'>{esc(str((r.doc.get('実行一覧') or ['—'])[0])[:10])}</td></tr>")
    if body:
        out.append(compareview._table(head, body))
    docs, ds = documents(paths), drafts(paths)
    out.append("<h2>文書と下書き</h2><ul>"
               + "".join(f"<li><code>docs/specs/experiments/{esc(p.name)}</code></li>" for p in docs)
               + "".join(f"<li><code>docs/specs/experiments/assets/{esc(p.name)}</code>（{len(es)} 件）</li>" for p, es in ds)
               + "</ul>")
    out.append(f"<script>{compareview.SCRIPT}</script>")
    return "\n".join(out)


def body(paths: TraderPaths, item: str) -> str | None:
    return page_body(paths) if item in ("all", "") else None


def sidebar(paths: TraderPaths) -> dict:
    return {"items": [{"id": "all", "label": SIDEBAR_LABEL, "sub": SIDEBAR_SUB, "group": "棚卸し"}]}


def fingerprint(paths: TraderPaths) -> dict[str, float]:
    """見張り用: 検証結果一覧を DB に入れ直した時刻（compareview の指紋）＋ 下書きと文書の mtime。"""
    out = compareview.fingerprint(paths)
    for p in [*documents(paths), *(p for p, _ in drafts(paths))]:
        try:
            out[str(p)] = p.stat().st_mtime
        except OSError:
            pass
    return out
