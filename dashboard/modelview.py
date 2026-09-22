"""vibeboard の「予測モデル」タブの画面（HTML の body）。仕様は docs/specs/dashboard.md §17。

**一覧 ＋ モデル 1 本 1 ページ**。モデルの「型」ごとに、何を見て・どう答えを出し・過去のデータで試したらどうだったかを、やさしい言葉で出す。
2026-09-20 に手厚くした（利用者の裁定）: 図（入れるもの → 計算 → 出力スコア）／ このモデルの組み立て（軸の表）／ 小さな例で段を追う ／
出力スコアの出かたと読み方 ／ 試した経緯の表 ／ 大見出しごとの「詳しく（用語あり）」の囲み（⚠ **畳まない**）。⚠ **欄の無いモデルは、その部分を出さないだけ**。

  - ⚠ **言葉の正本は `dashboard/models.toml`**（トレーダーのタブと同じ 1 本。説明をこのコードに書かない）
  - ⚠ **どれが「いま使っている」かは実売買の設定から引く**（`traderview` の読み手。設定の `[[models]]` と
    `name`・`method` が合う `[[model]]`）。⚠ **本数を数えない・見くらべる表や順位を作らない**
  - ⚠ **用語を書いてよいのは `[model.detail.*]`（「詳しく」の囲み）と正式な名前の欄だけ**。ほかの欄（`axes`・`walk`・`score`・`history` も）は
    やさしい言葉の検査を受ける（`tests/test_traders_tab.py` の `FORBIDDEN`）。⚠ 新しい欄はトレーダーのタブには出ない（人のページを長くしない）
  - ⚠ **「過去のデータで試した結果」の印と文は人が書く**（`result`）。⚠ **試した経緯の数は研究の DB から数える**（2026-09-21。
    経緯の行の `names` ＝ 予測モデル名のパターン〔rules.md 10-2〕で `ledger_rows` を引き、θ はまとめて数える。DB が無い機械・
    `names` の無い行は TOML の数）。⚠ 印・TOML の数と DB の数が食い違えばテストが落ちる（`tests/test_models_tab.py`）。
    `[common] show_result = false` で段と印を出さない。⚠ **売買結果は出さない**
  - ⚠ **「詳しく」の中の差し込み**（`{{gate|…}}`・`{{calib|…}}` ＝ `PLUG`。2026-09-21）は、実行の記録（DB の `files`）の
    `checks.json` の門と `fitted/calibration_f*.json` の較正の係数から引く（出どころの実行の識別名を title に出す）。
    DB の無い機械では人が写した控え。⚠ 控えと DB の値が食い違えばテストが落ちる
  - ⚠ **`out/`・`state/`・`.env` を読まない**。開くのは TOML と、研究の DB の `ledger_rows`（検証結果一覧の生成物）・
    `files` の `checks.json` と `fitted/calibration_f*.json` だけで、DB は読み取り専用（無い DB は作らない）。
    「正式な名前」の段は実験の config から写す
  - ⚠ **標準ライブラリだけ・読むだけ**（vibeboard の sidecar が `python3` で起こす。tailnet の閲覧者にも見える）
"""

from __future__ import annotations

import fnmatch
import json
import re
import sqlite3
import zlib
from pathlib import Path
from urllib.parse import quote

import figures
import traderview
from traderview import TraderPaths, esc

LIST_ID = "all"
VERDICTS = ("adopt", "hold", "drop")
# 検証結果一覧の判定 → 印（`ledger_rows.verdict`）。⚠ 読むのは n_trials に数える行だけ（基準線の行は数えない）
LEDGER_VERDICTS = {"採る": "adopt", "保留": "hold", "落とす": "drop"}
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
 .dbv { border-bottom: 1px dotted #57606a; cursor: help; }
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


# 「詳しく」の中の差し込み（2026-09-21。プラン db-model-facts.md の Phase 4）:
#   {{gate|<実行>|<数字の選び方・作り方>|<項目>|<書き方>|<控え>}}  ＝ checks.json の gate.methods[…][項目]（auc ／ width_pt ／ auc_folds ／ width_folds）
#   {{calib|<実行>|<数字の選び方・作り方>|<項目>|<書き方>|<控え>}} ＝ fitted/calibration_f1..N.json の […][項目]（a ／ b。fold の順に並べる）
# 書き方 ＝ 小数の桁（`+` を付けると正の数に ＋）。控え ＝ DB の無い機械で出す、人が写した文字（⚠ DB の値と同じでないとテストが落ちる）
PLUG = re.compile(r"\{\{(gate|calib)\|([^|{}]+)\|([^|{}]+)\|([^|{}]+)\|(\+?\d)\|([^{}]*)\}\}")
PLUG_FIELDS = {"gate": ("auc", "width_pt", "auc_folds", "width_folds"), "calib": ("a", "b")}
_CALIB_FILE = re.compile(r"^fitted/calibration_f(\d+)\.json$")


class RunFacts:
    """実行の記録（DB の `files`）から「詳しく」に差し込む値を引く。⚠ 読み取り専用・引くのは `checks.json` と
    `fitted/calibration_f*.json` だけ・無い DB は作らない。DB が無い・開けないときは何も返さない（控えを出す）。"""

    def __init__(self, paths: TraderPaths):
        self.conn: sqlite3.Connection | None = None
        self._cache: dict[tuple[str, str], object] = {}
        db = paths.research_db
        if db is not None and Path(db).is_file():
            try:
                self.conn = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True, timeout=10)
                self.conn.execute("SELECT 1 FROM files LIMIT 1")
            except sqlite3.Error:
                self.close()

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _json(self, run: str, path: str):
        key = (run, path)
        if key not in self._cache:
            doc = None
            try:
                row = self.conn.execute("SELECT codec, body FROM files WHERE run = ? AND path = ?", (run, path)).fetchone()
                if row is not None:
                    codec, body = row
                    text = body if codec == "text" and isinstance(body, str) else zlib.decompress(body).decode("utf-8")
                    doc = json.loads(text)
            except (sqlite3.Error, ValueError, zlib.error):
                doc = None
            self._cache[key] = doc
        return self._cache[key]

    def value(self, kind: str, run: str, method: str, field: str):
        """数か、数の並び（fold の順）。引けなければ None。"""
        if self.conn is None or field not in PLUG_FIELDS.get(kind, ()):
            return None
        if kind == "gate":
            g = self._json(run, "checks.json") or {}
            v = (((g.get("gate") or {}).get("methods") or {}).get(method) or {}).get(field)
        else:
            try:
                files = [p for (p,) in self.conn.execute(
                    "SELECT path FROM files WHERE run = ? AND path LIKE 'fitted/calibration_f%.json'", (run,))]
            except sqlite3.Error:
                return None
            folds = sorted((int(m.group(1)), p) for p in files if (m := _CALIB_FILE.match(p)))
            v = [((self._json(run, p) or {}).get(method) or {}).get(field) for _n, p in folds] or None
        nums = v if isinstance(v, list) else [v]
        if v is None or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in nums):
            return None
        return v


def plug_text(v, fmt: str) -> str:
    """差し込む数の書き方（負は − ・ `+` なら正に ＋ ・ 並びは「 ／ 」でつなぐ）。"""
    def one(x: float) -> str:
        s = f"{x:.{int(fmt.lstrip('+'))}f}".replace("-", "−")
        return "＋" + s if fmt.startswith("+") and x > 0 else s
    return " ／ ".join(one(x) for x in v) if isinstance(v, list) else one(v)


def _plugged(text: str, facts: RunFacts | None, used: list) -> str:
    """文の中の差し込みを埋めた HTML（地の文はエスケープ）。DB から引けた値は点線の下線 ＋ 出どころの実行（title）。"""
    out, pos = [], 0
    for m in PLUG.finditer(text):
        out.append(esc(text[pos:m.start()]))
        kind, run, method, field, fmt, fallback = m.groups()
        v = facts.value(kind, run, method, field) if facts is not None else None
        if v is None:
            out.append(esc(fallback))
        else:
            used.append(run)
            out.append(f"<span class='dbv' title='{esc('試した記録から: ' + run)}'>{esc(plug_text(v, fmt))}</span>")
        pos = m.end()
    out.append(esc(text[pos:]))
    return "".join(out)


def _detail(common: dict, m: dict, part: str, facts: RunFacts | None = None) -> str:
    """大見出しごとの「詳しく（用語あり）」の囲み（`[model.detail.<part>]` の `text`・`points`・`links`）。無ければ出さない。
    文の中の差し込み（`PLUG`）は、DB がある機械では実行の記録から引いた値にする。"""
    d = (m.get("detail") or {}).get(part) or {}
    used: list[str] = []
    paras = "".join(f"<p>{_plugged(str(t), facts, used)}</p>" for t in d.get("text") or [] if t)
    points = ("<ul>" + "".join(f"<li>{_plugged(str(t), facts, used)}</li>" for t in d["points"] if t) + "</ul>"
              if d.get("points") else "")
    has_plug = any(PLUG.search(str(t)) for t in [*(d.get("text") or []), *(d.get("points") or [])])
    note = common.get("detail_db_note") if used else common.get("detail_hand_note") if has_plug else None
    inner = paras + points + _links(d.get("links")) + (f"<p class='sub'>{esc(note)}</p>" if note and (paras or points) else "")
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


Ledger = list[tuple[str, str, "str | None", "str | None"]]


def ledger_rows(paths: TraderPaths) -> Ledger | None:
    """研究の DB の検証結果一覧のうち n_trials に数える行 ＝ (予測モデル名, 判定, 最初の日, 最後の日)。

    ⚠ 読み取り専用で開く（無い DB は作らない・書かない）。DB が無い・表が無い・開けないときは None（TOML の数を出す）。
    """
    db = paths.research_db
    if db is None or not Path(db).is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True, timeout=10)
        try:
            return conn.execute("SELECT model_name, verdict, first_run, last_run FROM ledger_rows"
                                " WHERE leak = 0 AND is_trial = 1").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def counted(ledger: Ledger | None, patterns) -> dict | None:
    """経緯の 1 行の数を DB から数える。`patterns` ＝ 予測モデル名のパターン（`fnmatch`。θ はまとめて数える）。

    返すのは `{adopt, hold, drop, rows, first, last}`（rows ＝ 当たった行の数・first/last ＝ 実行の最初と最後の日）。
    DB が無い・`names` が無い・1 行も当たらないときは None（TOML の数を出す）。
    """
    if ledger is None or not patterns:
        return None
    pats = [str(p) for p in patterns]
    hit = [r for r in ledger if any(fnmatch.fnmatchcase(r[0], p) for p in pats)]
    if not hit:
        return None
    out: dict = {v: 0 for v in VERDICTS}
    for _name, verdict, _first, _last in hit:
        if verdict in LEDGER_VERDICTS:
            out[LEDGER_VERDICTS[verdict]] += 1
    days = [d for r in hit for d in r[2:] if d]
    return {**out, "rows": len(hit), "first": min(days, default=None), "last": max(days, default=None)}


def _history(common: dict, m: dict, ledger: Ledger | None = None) -> str:
    """試した経緯の表。数は研究の DB から数える（`names` の無い行・DB の無い機械は人が写した TOML の数）。
    いまの印に数えない行は薄く出し、理由を添える:
    `old = true` ＝ 計算を直す前の試し（理由は `[common] history_old`）／ `aside = "理由"` ＝ ちがう売り買いの決まりでの試しなど。"""
    rows = [r for r in m.get("history") or [] if r.get("what")]
    if not rows:
        return ""
    names = [(v, common.get("result_" + v) or v) for v in VERDICTS]
    out = [f"<h3>{esc(common.get('history_title') or '試した経緯')}</h3><table class='hist'><tr>"
           + "".join(f"<th>{esc(common.get(k) or d)}</th>" for k, d in (
               ("history_when", "いつ"), ("history_what", "何を試したか"), ("history_ways", "何通り"), ("history_split", "内訳"))) + "</tr>"]
    for r in rows:
        db = counted(ledger, r.get("names"))
        counts = [(name, int((db or r).get(v) or 0)) for v, name in names]
        split = " ／ ".join(f"{name} {n}" for name, n in counts if n)
        # ⚠ DB がある機械で、DB から数えられなかった行（`names` が無い）にだけ「人が写した数」の印
        hand = (f"<span class='sub'>（{esc(common['history_hand'])}）</span>"
                if ledger is not None and db is None and common.get("history_hand") else "")
        aside = _aside(common, r)
        old = f"<span class='sub'>（{esc(aside)}）</span>" if aside else ""
        note = f"<span class='why'>{esc(r['note'])}</span>" if r.get("note") else ""
        out.append(f"<tr{' class=' + chr(39) + 'old' + chr(39) if aside else ''}><td>{esc(r.get('when'))}</td>"
                   f"<td>{esc(r['what'])}{old}{note}</td><td>{sum(n for _k, n in counts) or ''}</td><td>{esc(split)}{hand}</td></tr>")
    out.append("</table>")
    source = common.get("history_source_db" if ledger is not None else "history_source_hand")
    notes = " ".join(str(x) for x in (common.get("history_note"), source) if x)
    if notes:
        out.append(f"<p class='sub'>{esc(notes)}</p>")
    return "".join(out)


def _aside(common: dict, r: dict) -> str:
    """いまの印に数えない行の理由（数える行なら ""）。"""
    return str(r.get("aside") or (common.get("history_old") if r.get("old") else "") or "")


def best_verdict(m: dict, ledger: Ledger | None = None) -> str:
    """経緯の表（`old`・`aside` の行を除く）でいちばん良かった印（DB があれば DB の数で）。表が無ければ ""。
    ⚠ `result.verdict` と食い違わないことをテストが見る。"""
    rows = [r for r in m.get("history") or [] if not (r.get("old") or r.get("aside"))]
    for v in VERDICTS:
        if any(int((counted(ledger, r.get("names")) or r).get(v) or 0) > 0 for r in rows):
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
    has_plug = any(PLUG.search(str(t)) for d in (m.get("detail") or {}).values()
                   for t in [*(d.get("text") or []), *(d.get("points") or [])])
    facts = RunFacts(paths) if has_plug else None       # 差し込みのあるページだけ DB を開く
    try:
        return _model_body(paths, data, m, facts)
    finally:
        if facts is not None:
            facts.close()


def _model_body(paths: TraderPaths, data: dict, m: dict, facts: "RunFacts | None") -> str:
    item = m["id"]
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
    out.append(_axes(common, m) + _detail(common, m, "about", facts))

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
    out.append(_detail(common, m, "how", facts))

    no = 4
    score = _score(common, m)
    if score:
        out.append(traderview._h2(no, "出力スコアの出かたと読み方") + score + _detail(common, m, "score", facts))
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
        out.append(_history(common, m, ledger_rows(paths) if m.get("history") else None))
        records = [str(r) for r in m.get("records") or []]
        if records:
            out.append(f"<p class='sub'>{esc(common.get('records_label'))}: " + "　".join(
                f"<a href='{esc(doc_url(r))}' target='_top'>{esc(r.rsplit('/', 1)[-1])}</a>" for r in records) + "</p>")
        out.append(f"<p class='sub'>{esc(common.get('result_note'))}</p>")
        out.append(_detail(common, m, "result", facts))

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
    """見張り用。言葉の正本と実売買の設定（だれがどのモデルを使うか）の mtime ＋ 検証結果一覧を DB に入れ直した時刻。"""
    out = traderview.fingerprint(paths)
    db = paths.research_db
    if db is not None and Path(db).is_file():
        try:
            conn = sqlite3.connect(f"file:{Path(db)}?mode=ro", uri=True, timeout=10)
            try:
                row = conn.execute("SELECT value FROM meta WHERE key = 'ledger_built_at'").fetchone()
            finally:
                conn.close()
            if row:
                out["ledger_rows"] = float(re.sub(r"\D", "", str(row[0])) or 0)
        except sqlite3.Error:
            pass
    return out
