"""vibeboard の「予測モデル」タブの画面（HTML の body）。仕様は docs/specs/dashboard.md §17。

**一覧 ＋ モデル 1 本 1 ページ**。モデルの「型」ごとに、何を見て・どう答えを出し・過去のデータで試したらどうだったかを、やさしい言葉で出す。

  - ⚠ **言葉の正本は `dashboard/models.toml`**（トレーダーのタブと同じ 1 本。説明をこのコードに書かない）
  - ⚠ **どれが「いま使っている」かは実売買の設定から引く**（`traderview` の読み手。設定の `[[models]]` と
    `name`・`method` が合う `[[model]]`）。⚠ **本数を数えない・見くらべる表や順位を作らない**
  - ⚠ **「過去のデータで試した結果」は人が記録から写した印と文**（`result`。台帳からは機械で引けない ＝ §17-3）。
    `[common] show_result = false` で段と印を出さない。⚠ **売買結果は出さない**
  - ⚠ **`out/`・`state/`・`.env`・`runs/` を読まない**（このモジュールが開くのは TOML だけ。「正式な名前」の段は実験の config から写す）
  - ⚠ **標準ライブラリだけ・読むだけ**（vibeboard の sidecar が `python3` で起こす。tailnet の閲覧者にも見える）
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import quote

import traderview
from traderview import TraderPaths, esc

LIST_ID = "all"
VERDICTS = ("adopt", "hold", "drop")
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*$")
# 実験の config から「正式な名前」の段へ写す鍵（あるものだけ）
CONFIG_KEYS = ("model", "feature_layers", "detectors", "transform", "selectors", "symbol")
DOC_CATEGORIES = (("docs/plans/", "plans"), ("docs/specs/", "specs"))     # vibetab.DOC_CATEGORIES と同じ
COLOR_LIVE, COLOR_DESK = "#2a78d6", "#57606a"

CSS = traderview.CSS + """
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

    out.append(traderview._h2(2, "モデルの特性"))
    out.append("<ul>" + "".join(
        f"<li>{esc(t.get('text'))}{traderview._tag(common, str(t.get('basis') or ''))}"
        + (f"<span class='why'>理由: {esc(t['why'])}</span>" if t.get("why") else "") + "</li>"
        for t in m.get("traits") or []) + "</ul>")
    out.append(f"<p class='sub'>{esc(common.get('basis_note'))}</p>")

    out.append(traderview._h2(3, "何を見て、どう答えを出すか"))
    for g in m.get("sees") or []:
        out.append(f"<h3>{esc(g.get('label'))}</h3>" + traderview._ul(g.get("items")))
    if m.get("sees_note"):
        out.append(f"<p class='sub'>{esc(m['sees_note'])}</p>")
    out.append(f"<h3>答えの出し方</h3><p>{esc(m.get('how'))}</p>")

    no = 4
    if _show_result(common):
        out.append(traderview._h2(no, "過去のデータで試した結果"))
        no += 1
        result = m.get("result") or {}
        if _verdict(m):
            out.append(f"<div class='result'>{_verdict_tag(common, m)}{esc(result.get('text'))}"
                       + (f"<span class='why'>{esc(result['why'])}</span>" if result.get("why") else "") + "</div>")
        else:
            out.append(f"<p>{esc(common.get('result_none'))}</p>")
        records = [str(r) for r in m.get("records") or []]
        if records:
            out.append(f"<p class='sub'>{esc(common.get('records_label'))}: " + "　".join(
                f"<a href='{esc(doc_url(r))}' target='_top'>{esc(r.rsplit('/', 1)[-1])}</a>" for r in records) + "</p>")
        out.append(f"<p class='sub'>{esc(common.get('result_note'))}</p>")

    survivor = common.get("limit_survivor") if m.get("stocks") is not False else None
    out.append(traderview._h2(no, "気をつけること") + "<div class='care'>" + traderview._ul(
        [*(m.get("limits") or []), common.get("result_caution") if _show_result(common) else None,
         survivor, common.get("limit_costs") if m.get("stocks") is not False else None]) + "</div>")

    out.append("<details><summary>正式な名前（記録や設定に出てくる呼び名）</summary>"
               f"<p class='sub'>言葉の意味は <a href='{traderview.GLOSSARY_URL}' target='_top'>用語</a>。</p>"
               f"{_formal_table(paths, m)}</details>")
    out.append("</div>")
    return "\n".join(out)


def body(paths: TraderPaths, item: str) -> str | None:
    return list_body(paths) if item == LIST_ID else model_body(paths, item)


def fingerprint(paths: TraderPaths) -> dict[str, float]:
    """見張り用。言葉の正本と実売買の設定（だれがどのモデルを使うか）の mtime。"""
    return traderview.fingerprint(paths)
