"""vibeboard の「予測モデル」タブの画面（HTML の body）。仕様は docs/specs/dashboard.md §17。

**一覧 ＋ モデル 1 本 1 ページ**。モデルの「型」ごとに、何を見て・どう答えを出し・過去のデータで試したらどうだったかを、やさしい言葉で出す。
2026-09-20 に手厚くした（利用者の裁定）: 図（入れるもの → 計算 → 出力スコア）／ このモデルの組み立て（軸の表）／ 小さな例で段を追う ／
出力スコアの出かたと読み方 ／ 試した経緯の表 ／ 大見出しごとの「詳しく（用語あり）」の囲み（⚠ **畳まない**）。⚠ **欄の無いモデルは、その部分を出さないだけ**。

  - ⚠ **言葉の正本は `dashboard/models.toml`**（トレーダーのタブと同じ 1 本。説明をこのコードに書かない）
  - ⚠ **どれが「いま使っている」かは実売買の設定から引く**（`traderview` の読み手。設定の `[[models]]` と
    `name`・`method` が合う `[[model]]`）。⚠ **本数を数えない・見くらべる表や順位を作らない**
  - ⚠ **用語を書いてよいのは `[model.detail.*]`（「詳しく」の囲み）と正式な名前の欄だけ**。ほかの欄（`axes`・`walk`・`score`・`history` も）は
    やさしい言葉の検査を受ける（`tests/test_traders_tab.py` の `FORBIDDEN`）。⚠ 新しい欄はトレーダーのタブには出ない（人のページを長くしない）
  - ⚠ **「過去のデータで試した結果」は人が記録から写した印と文**（`result`。検証結果一覧からは機械で引けない ＝ §17-3）。
    `[common] show_result = false` で段と印を出さない。⚠ **売買結果は出さない**
  - ⚠ **`out/`・`state/`・`.env`・`runs/` を読まない**（このモジュールが開くのは TOML だけ。「正式な名前」の段は実験の config から写す）
  - ⚠ **標準ライブラリだけ・読むだけ**（vibeboard の sidecar が `python3` で起こす。tailnet の閲覧者にも見える）
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import figures
import traderview
from traderview import TraderPaths, esc

LIST_ID = "all"
VERDICTS = ("adopt", "hold", "drop")
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
# 実験の config から「正式な名前」の段へ写す鍵（あるものだけ）
CONFIG_KEYS = ("model", "feature_layers", "detectors", "transform", "selectors", "symbol")
DOC_CATEGORIES = (("docs/plans/", "plans"), ("docs/specs/", "specs"))     # vibetab.DOC_CATEGORIES と同じ
COLOR_LIVE, COLOR_DESK = "#2a78d6", "#57606a"
SYSTEM_URL = "/#system/"
TAB_URLS = {"system": SYSTEM_URL, "models": traderview.MODEL_URL, "traders": traderview.TRADER_URL,
            "glossary": "/#glossary/", "experiments": "/#experiments/", "data": "/#data/"}
# このモデルの組み立て（軸）。行の名前は `[common]` の `axis_<鍵>`
AXES = ("data", "prep", "calc", "target", "scope")
# 「詳しく（用語あり）」の囲みを置ける大見出し（`[model.detail.<鍵>]`）
DETAIL_PARTS = ("about", "how", "score", "result")

CSS = traderview.CSS + figures.CSS + """
 .more { margin: 10px 0 0; border: 1px solid #d0d7de; border-radius: 6px; padding: 6px 12px; background: #f6f8fa; }
 .more .more-h { font-size: 12px; font-weight: 700; color: #656d76; margin: 2px 0 4px; }
 .more p, .more li { font-size: 13px; }
 .more table { background: #fff; }
 .nav { display: flex; flex-wrap: wrap; gap: 8px; margin: 10px 0; }
 .nav a { border: 1px solid #d0d7de; border-radius: 6px; padding: 4px 10px; font-size: 13px; text-decoration: none; background: #f6f8fa; }
 .walk { border-left: 4px solid #d0d7de; padding: 2px 0 2px 14px; margin: 8px 0; }
 .walk ol { margin: 6px 0 6px 1.4em; padding: 0; }
 .walk li { margin: 6px 0; }
 tr.old td { color: #656d76; }
 table.hist th, table.hist td:nth-child(1), table.hist td:nth-child(3), table.hist td:nth-child(4), table.axes th { white-space: nowrap; }
 .tag.adopt { border-color: #1a7f37; color: #116329; background: #dafbe1; }
 .tag.hold { border-color: #d4a72c; color: #7d4e00; background: #fff8c5; }
 .tag.drop { border-color: #afb8c1; color: #424a53; background: #eaeef2; }
 .card .ttl .tag { margin-left: 0; }
 .who { margin: 8px 0 0; font-size: 13px; }
 .who a { margin-right: 10px; }
 .result { border: 1px solid #d0d7de; border-radius: 6px; padding: 8px 14px; margin: 10px 0; }
 .result .tag { margin-left: 0; margin-right: 8px; font-size: 12px; line-height: 20px; padding: 0 8px; border-radius: 10px; }
 td ul { margin: 0 0 0 1.1em; }
"""


def config_dir(paths: TraderPaths) -> Path:
    """実験の config の置き場（`experiments/feature-discovery/config/`）。銘柄の集合の 1 つ上。"""
    return paths.universe_dir.parent


def doc_url(rel: str) -> str:
    """リポジトリ直下からの道を vibeboard の hash URL にする（vibetab.doc_url と同じ決まり）。"""
    def enc(p: str) -> str:
        return "/".join(quote(s, safe="") for s in p.split("/"))

    if rel.endswith((".md", ".html")):
        for prefix, name in DOC_CATEGORIES:
            if rel.startswith(prefix):
                return f"/#{name}/{enc(rel[len(prefix):])}"
    return f"/#files/{enc(rel)}"


def _link(link: dict) -> str:
    """`{ label, doc }`（文書 → Specs ／ Plans ／ Files タブ）か `{ label, tab, item }`（vibeboard のタブ。`item` を省くとタブの頭）。"""
    label = esc(link.get("label"))
    if link.get("doc"):
        return f"<a href='{esc(doc_url(str(link['doc'])))}' target='_top'>{label}</a>"
    base = TAB_URLS.get(str(link.get("tab") or ""))
    if not base:
        return ""
    url = base + str(link["item"]) if link.get("item") else base.rstrip("/")      # item が無ければタブの頭へ
    return f"<a href='{esc(url)}' target='_top'>{label}</a>"


def _links(links) -> str:
    html_ = "".join(_link(x) for x in links or [])
    return f"<div class='nav'>{html_}</div>" if html_ else ""


def _paras(items) -> str:
    return "".join(f"<p>{esc(t)}</p>" for t in items or [] if t)


def load(paths: TraderPaths) -> dict:
    """言葉の正本 ＋ だれがどのモデルを使うか。`models` は「いま使っている」→「机上で試した」の順（束の中は書いた順）。"""
    words = traderview.load_words(paths)
    models = [m for m in words["models"] if ID_PATTERN.match(str(m.get("id") or ""))]
    users: dict[str, list[dict]] = {}
    for key, _src, settled in traderview.trader_files(paths):
        facts = traderview.load_facts(paths, key) or {"models": []}
        for spec in facts["models"]:
            hit = traderview.find_model(words, spec)
            if hit is not None and hit.get("id") and key not in [u["key"] for u in users.get(hit["id"], [])]:
                users.setdefault(hit["id"], []).append({"key": key, "settled": settled})
    live = [m for m in models if m["id"] in users]
    desk = [m for m in models if m["id"] not in users]
    return {"words": words, "common": words["mcommon"], "models": live + desk, "users": users}


def _show_result(common: dict) -> bool:
    return common.get("show_result") is not False


def _verdict(m: dict) -> str:
    v = str((m.get("result") or {}).get("verdict") or "")
    return v if v in VERDICTS else ""


def _verdict_tag(common: dict, m: dict) -> str:
    v = _verdict(m)
    return f"<span class='tag {v}'>{esc(common.get('result_' + v))}</span>" if v else ""


def sidebar(paths: TraderPaths) -> dict:
    data = load(paths)
    common, show = data["common"], _show_result(data["common"])
    items = [{"id": LIST_ID, "label": str(common.get("list_title") or "一覧")}]
    for m in data["models"]:
        group = common.get("group_live") if m["id"] in data["users"] else common.get("group_desk")
        items.append({"id": m["id"], "label": str(m.get("label") or m["id"]), "group": str(group or ""),
                      "badge": str(common.get("result_" + _verdict(m)) or "") if show and _verdict(m) else ""})
    return {"items": items}


def _chips(common: dict, m: dict) -> str:
    card = m.get("card") or {}
    chips = [(common.get("card_sees"), card.get("sees")), (common.get("card_decides"), card.get("decides"))]
    return ("<div class='chips'>" + "".join(f"<div class='chip'><b>{esc(k)}</b>{esc(v)}</div>" for k, v in chips if v)
            + "</div>")


def list_body(paths: TraderPaths) -> str:
    """一覧 ＝ モデル 1 本 1 枚の札。⚠ 本数を数えない・見くらべる表や順位を作らない。"""
    data = load(paths)
    common, show = data["common"], _show_result(data["common"])
    out = [f"<h1>{esc(common.get('list_title'))}</h1>", f"<p class='lead'>{esc(common.get('list_lead'))}</p>"]
    for title, color, rows in (
            (common.get("group_live"), COLOR_LIVE, [m for m in data["models"] if m["id"] in data["users"]]),
            (common.get("group_desk"), COLOR_DESK, [m for m in data["models"] if m["id"] not in data["users"]])):
        if not rows:
            continue
        out.append(f"<h2>{esc(title)}</h2><div class='cards' style='--who:{color}'>")
        for m in rows:
            tag = f"<div class='ttl'>{_verdict_tag(common, m)}</div>" if show and _verdict(m) else ""
            out.append(f"<a class='card' href='{traderview.MODEL_URL}{esc(m['id'])}' target='_top'>"
                       f"<div class='nick'>{esc(m.get('label'))}</div>{tag}"
                       f"<p>{esc(m.get('summary'))}</p>{_chips(common, m)}</a>")
        out.append("</div>")
    if show:
        out.append(f"<p class='sub'>{esc(common.get('result_note'))}</p><p class='sub'>{esc(common.get('result_caution'))}</p>")
    return "\n".join(out)


def _configs(paths: TraderPaths, m: dict) -> list[tuple[str, dict]]:
    """(config/ からの道, そこから写した鍵)。⚠ 無いファイルは空の表で返す（落とさない）。"""
    rels = list(m.get("configs") or ([f"experiment/{m['name']}.toml"] if m.get("name") else []))
    out = []
    for rel in rels:
        rel = str(rel)
        path = (config_dir(paths) / rel).resolve()
        doc = traderview._load(path) if path.suffix == ".toml" and config_dir(paths).resolve() in path.parents else {}
        # ⚠ 写すのは平の値だけ（`[model]` のような表は写さない）
        out.append((rel, {k: doc[k] for k in CONFIG_KEYS if isinstance(doc.get(k), (str, list))}))
    return out


def _more(label, inner: str) -> str:
    # ⚠ 畳まない（<details> にしない）。開いた状態だけ ＝ 利用者の指示 2026-09-20（「システム説明」と同じ）
    return f"<div class='more'><div class='more-h'>{esc(label)}</div>{inner}</div>" if inner else ""


def _detail(common: dict, m: dict, part: str) -> str:
    """大見出しごとの「詳しく（用語あり）」の囲み（`[model.detail.<part>]` の `text`・`points`・`links`）。無ければ出さない。"""
    d = (m.get("detail") or {}).get(part) or {}
    inner = _paras(d.get("text")) + (traderview._ul(d["points"]) if d.get("points") else "") + _links(d.get("links"))
    return _more(common.get("detail_label") or "詳しく（用語あり）", inner)


def _axes(common: dict, m: dict) -> str:
    """このモデルの組み立て ＝ 軸ごとに「このモデルではどれか」。⚠ 1 本のモデルの中だけの表（モデルどうしを横に並べない）。"""
    axes = m.get("axes") or {}
    rows = [(common.get("axis_" + k) or k, axes[k]) for k in AXES if axes.get(k)]
    if not rows:
        return ""
    note = f"<p class='sub'>{esc(common['axes_note'])}</p>" if common.get("axes_note") else ""
    return (f"<h3>{esc(common.get('axes_title') or 'このモデルの組み立て')}</h3><table class='axes'>"
            + "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in rows) + f"</table>{note}")


def _walk(common: dict, m: dict) -> str:
    """計算を小さな例で、段を追って。⚠ 例の数字は作りもの（`walk_caution` が必ず付く）。"""
    walk = m.get("walk") or {}
    steps = [x for x in walk.get("steps") or [] if x.get("text")]
    if not steps:
        return ""
    items = "".join(f"<li>{'<b>' + esc(x['t']) + '</b>　' if x.get('t') else ''}{esc(x['text'])}</li>" for x in steps)
    return (f"<h3>{esc(common.get('walk_title') or '小さな例で、段を追って')}</h3><div class='walk'>"
            + (f"<p>{esc(walk['lead'])}</p>" if walk.get("lead") else "") + f"<ol>{items}</ol>"
            + (f"<p>{esc(walk['note'])}</p>" if walk.get("note") else "")
            + f"<p class='sub'>{esc(common.get('walk_caution'))}</p></div>")


def _history(common: dict, m: dict) -> str:
    """試した経緯の表（人が記録から写したもの）。いまの印に数えない行は薄く出し、理由を添える:
    `old = true` ＝ 計算を直す前の試し（理由は `[common] history_old`）／ `aside = "理由"` ＝ ちがう売り買いの決まりでの試しなど。"""
    rows = [r for r in m.get("history") or [] if r.get("what")]
    if not rows:
        return ""
    names = [(v, common.get("result_" + v) or v) for v in VERDICTS]
    out = [f"<h3>{esc(common.get('history_title') or '試した経緯')}</h3><table class='hist'><tr>"
           + "".join(f"<th>{esc(common.get(k) or d)}</th>" for k, d in (
               ("history_when", "いつ"), ("history_what", "何を試したか"), ("history_ways", "何通り"), ("history_split", "内訳"))) + "</tr>"]
    for r in rows:
        counts = [(name, int(r.get(v) or 0)) for v, name in names]
        split = " ／ ".join(f"{name} {n}" for name, n in counts if n)
        aside = _aside(common, r)
        old = f"<span class='sub'>（{esc(aside)}）</span>" if aside else ""
        note = f"<span class='why'>{esc(r['note'])}</span>" if r.get("note") else ""
        out.append(f"<tr{' class=' + chr(39) + 'old' + chr(39) if aside else ''}><td>{esc(r.get('when'))}</td>"
                   f"<td>{esc(r['what'])}{old}{note}</td><td>{sum(n for _k, n in counts) or ''}</td><td>{esc(split)}</td></tr>")
    out.append("</table>")
    if common.get("history_note"):
        out.append(f"<p class='sub'>{esc(common['history_note'])}</p>")
    return "".join(out)


def _aside(common: dict, r: dict) -> str:
    """いまの印に数えない行の理由（数える行なら ""）。"""
    return str(r.get("aside") or (common.get("history_old") if r.get("old") else "") or "")


def best_verdict(m: dict) -> str:
    """経緯の表（`old`・`aside` の行を除く）でいちばん良かった印。表が無ければ ""。⚠ `result.verdict` と食い違わないことをテストが見る。"""
    rows = [r for r in m.get("history") or [] if not (r.get("old") or r.get("aside"))]
    for v in VERDICTS:
        if any(int(r.get(v) or 0) > 0 for r in rows):
            return v
    return ""


def _score(common: dict, m: dict) -> str:
    """出力スコアの出かた（幅・癖。印つき）と読み方。"""
    score = m.get("score") or {}
    items = [t for t in score.get("items") or [] if t.get("text")]
    if not items and not score.get("read"):
        return ""
    out = ["<ul>" + "".join(
        f"<li>{esc(t['text'])}{traderview._tag(common, str(t.get('basis') or ''))}"
        + (f"<span class='why'>理由: {esc(t['why'])}</span>" if t.get("why") else "") + "</li>" for t in items) + "</ul>"]
    if score.get("read"):
        out.append(f"<h3>{esc(common.get('score_read') or '読み方')}</h3>{_paras(score['read'])}")
    if common.get("score_link"):
        out.append(_links([{"label": common["score_link"], "tab": "system", "item": "live"}]))
    return "".join(out)


def _formal_table(paths: TraderPaths, m: dict) -> str:
    def cell(v) -> str:
        return "、".join(f"<code>{esc(x)}</code>" for x in v) if isinstance(v, list) else f"<code>{esc(v)}</code>"

    rows = [("正式な名前", "<ul>" + "".join(f"<li><code>{esc(x)}</code></li>" for x in m.get("formal") or []) + "</ul>")]
    if m.get("name"):
        rows.append(("実売買の設定での綴り", f"<code>{esc(m['name'])}</code>"
                     + (f" <code>{esc(m['method'])}</code>" if m.get("method") else "")))
    for rel, keys in _configs(paths, m):
        copied = "".join(f"<br>{esc(k)} = {cell(v)}" for k, v in keys.items())
        rows.append(("実験の設定", f"<code>experiments/feature-discovery/config/{esc(rel)}</code>{copied}"))
    return "<table>" + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows) + "</table>"


def model_body(paths: TraderPaths, item: str) -> str | None:
    data = load(paths)
    m = next((x for x in data["models"] if x["id"] == item), None)
    if m is None:
        return None
    common, words = data["common"], data["words"]
    users = data["users"].get(item, [])
    out = [f"<div style='--who:{COLOR_LIVE if users else COLOR_DESK}'>", f"<h1>{esc(m.get('label'))}</h1>"]

    out.append(traderview._h2(1, "どんなモデルか"))
    out.append(f"<p class='lead'>{esc(m.get('summary'))}</p>{_chips(common, m)}")
    if users:
        links = "".join(
            f"<a href='{traderview.TRADER_URL}{esc(u['key'])}' target='_top'>{esc(traderview._who(words, u['key']))}</a>"
            + ("" if u["settled"] else f"<span class='sub'>（{esc(common.get('unsettled_mark'))}）</span> ")
            for u in users)
        out.append(f"<p class='who'><b>{esc(common.get('used_by'))}</b>　{links}</p>")
    else:
        out.append(f"<p class='who sub'>{esc(common.get('used_by_none'))}</p>")
    out.append(_axes(common, m) + _detail(common, m, "about"))

    out.append(traderview._h2(2, "モデルの特性"))
    out.append("<ul>" + "".join(
        f"<li>{esc(t.get('text'))}{traderview._tag(common, str(t.get('basis') or ''))}"
        + (f"<span class='why'>理由: {esc(t['why'])}</span>" if t.get("why") else "") + "</li>"
        for t in m.get("traits") or []) + "</ul>")
    out.append(f"<p class='sub'>{esc(common.get('basis_note'))}</p>")

    out.append(traderview._h2(3, "何を見て、どう答えを出すか"))
    out.append(figures.flow_html(m.get("flow"), m.get("flow_claim") or common.get("flow_claim"), per_row=5))
    for g in m.get("sees") or []:
        out.append(f"<h3>{esc(g.get('label'))}</h3>" + traderview._ul(g.get("items")))
    if m.get("sees_note"):
        out.append(f"<p class='sub'>{esc(m['sees_note'])}</p>")
    out.append(f"<h3>答えの出し方</h3><p>{esc(m.get('how'))}</p>")
    out.append(_walk(common, m))
    if common.get("flow_link"):                  # 全部のモデルに共通の流れは「システム説明」に書いてある（二重に書かない）
        out.append(_links([{"label": common["flow_link"], "tab": "system", "item": "build"}]))
    out.append(_detail(common, m, "how"))

    no = 4
    score = _score(common, m)
    if score:
        out.append(traderview._h2(no, "出力スコアの出かたと読み方") + score + _detail(common, m, "score"))
        no += 1
    if _show_result(common):
        out.append(traderview._h2(no, "過去のデータで試した結果"))
        no += 1
        result = m.get("result") or {}
        if _verdict(m):
            out.append(f"<div class='result'>{_verdict_tag(common, m)}{esc(result.get('text'))}"
                       + (f"<span class='why'>{esc(result['why'])}</span>" if result.get("why") else "") + "</div>")
        else:
            out.append(f"<p>{esc(common.get('result_none'))}</p>")
        out.append(_history(common, m))
        records = [str(r) for r in m.get("records") or []]
        if records:
            out.append(f"<p class='sub'>{esc(common.get('records_label'))}: " + "　".join(
                f"<a href='{esc(doc_url(r))}' target='_top'>{esc(r.rsplit('/', 1)[-1])}</a>" for r in records) + "</p>")
        out.append(f"<p class='sub'>{esc(common.get('result_note'))}</p>")
        out.append(_detail(common, m, "result"))

    survivor = common.get("limit_survivor") if m.get("stocks") is not False else None
    out.append(traderview._h2(no, "気をつけること") + "<div class='care'>" + traderview._ul(
        [*(m.get("limits") or []), common.get("result_caution") if _show_result(common) else None,
         survivor, common.get("limit_costs") if m.get("stocks") is not False else None]) + "</div>")

    # ⚠ 畳まない（利用者の裁定 2026-09-20）。最後の囲み ＝ 正式な名前と設定
    out.append(_more(common.get("formal_label") or "正式な名前と設定（用語あり）",
                     f"<p class='sub'>言葉の意味は <a href='{traderview.GLOSSARY_URL}' target='_top'>用語</a>。</p>"
                     f"{_formal_table(paths, m)}"))
    out.append("</div>")
    return "\n".join(out)


def body(paths: TraderPaths, item: str) -> str | None:
    return list_body(paths) if item == LIST_ID else model_body(paths, item)


def fingerprint(paths: TraderPaths) -> dict[str, float]:
    """見張り用。言葉の正本と実売買の設定（だれがどのモデルを使うか）の mtime。"""
    return traderview.fingerprint(paths)
