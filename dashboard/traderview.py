"""vibeboard の「トレーダー」タブの画面（HTML の body と CSS）。仕様は docs/specs/dashboard.md §16。

**トレーダー 1 人 1 ページ**。ページには「この人が使うモデル」と、そのモデルの**特性**を、やさしい言葉で出す。

  - ⚠ **だれが居るかは実売買の設定から引く**（`experiments/live-trading/config/traders/*.toml` の試験用でない人。
    直下に 1 人も居なければ `candidates/notional/` ＝「まだ確定していない」の印つき）。⚠ **人数を数えない・見くらべるページを作らない**
    （利用者の指示 2026-09-20。トレーダーは 1 人のときも 10 人のときもある）
  - ⚠ **言葉の正本は 2 本**: モデルの解説 ＝ `dashboard/models.toml` の `[[model]]`（「予測モデル」タブ `modelview.py` と同じ 1 本。
    dashboard.md §17）／ 人の側の言葉 ＝ `dashboard/traders.toml`（呼び名 `[nicks]`・共通の文 `[common]`）。
    説明をこのコードに書かない。⚠ **数字は設定から写すだけ**
  - ⚠ **売買結果は出さない**。⚠ **`out/`・`state/`・`.env` を読まない**（このモジュールが開くのは TOML だけ）
  - ⚠ **標準ライブラリだけ・読むだけ**（vibeboard の sidecar が `python3` で起こす。tailnet の閲覧者にも見える）
"""

from __future__ import annotations

import html
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DASHBOARD_DIR = Path(__file__).resolve().parent
REPO_ROOT = DASHBOARD_DIR.parent

SPEC_URL = "/#specs/experiments/live-trading.md"
GLOSSARY_URL = "/#glossary/all"
MODEL_URL = "/#models/"          # 「予測モデル」タブのモデルのページ（後ろに `[[model]]` の id）
TRADER_URL = "/#traders/"        # このタブの人のページ（後ろに設定の鍵）
# 人ごとの色（モデルの札の線と、出力スコアのものさしの「買う」の帯）。白地の上で読める濃さ。足りなくなったら頭から使い回す
COLORS = ("#2a78d6", "#b0567a", "#2f8f6b", "#a8741a", "#6b5fc7")
BASES = ("build", "trial", "guess")


@dataclass(frozen=True)
class TraderPaths:
    words: Path            # 人の側の言葉の正本（traders.toml）
    models: Path           # モデルの解説の正本（models.toml）
    traders_dir: Path      # 実売買の設定（直下 ＝ 確定・candidates/notional ＝ 候補）
    universe_dir: Path     # 銘柄の集合（本数を数えるだけ）
    # 「システム説明」タブ（systemview.py）が読むもの: 説明の言葉の正本と、机上の検証の検証結果一覧（合計を写すだけ）
    system: Path = DASHBOARD_DIR / "system.toml"
    ledger: Path = REPO_ROOT / "docs" / "specs" / "experiments" / "feature-discovery" / "ledger.md"
    # 「予測モデル」タブ（modelview.py）が試した経緯の数を引く研究の DB（`ledger_rows` だけ・読み取り専用）。
    # ⚠ None ＝ 読まない（TOML の数を出す）。vibetab は実行タブと同じ置き場（`--runs-dir`）の DB を渡す
    research_db: Path | None = None

    @classmethod
    def default(cls) -> "TraderPaths":
        lt = REPO_ROOT / "experiments" / "live-trading" / "config" / "traders"
        return cls(
            words=Path(os.environ.get("AIL_TRADERS_WORDS") or DASHBOARD_DIR / "traders.toml"),
            models=Path(os.environ.get("AIL_MODELS_WORDS") or DASHBOARD_DIR / "models.toml"),
            system=Path(os.environ.get("AIL_SYSTEM_WORDS") or DASHBOARD_DIR / "system.toml"),
            traders_dir=Path(os.environ.get("AIL_TRADERS_DIR") or lt),
            universe_dir=Path(os.environ.get("AIL_UNIVERSE_DIR")
                              or REPO_ROOT / "experiments" / "feature-discovery" / "config" / "universe"),
        )


# ⚠ **白地・濃い字に固定する**（利用者の指示 2026-09-20「黒背景はやめる」。vibetab の page() は明暗を OS に合わせるので、ここで上書きする）
CSS = """
 :root { color-scheme: light; }
 html, body { background: #ffffff; color: #1f2328; }
 body { max-width: 920px; font-size: 14px; line-height: 1.75; margin: 20px 24px 48px; }
 a { color: #0969da; }
 h1 { font-size: 22px; margin: 0 0 6px; }
 h1 small { font-size: 13px; font-weight: 400; color: #656d76; margin-left: 8px; }
 h2 { font-size: 18px; margin: 34px 0 10px; padding: 0 0 6px; border-bottom: 2px solid #d0d7de; }
 h2 .no { display: inline-block; min-width: 26px; height: 26px; line-height: 26px; margin-right: 8px; border-radius: 13px;
          background: var(--who, #57606a); color: #fff; font-size: 14px; text-align: center; }
 h3 { font-size: 14px; margin: 14px 0 2px; color: #424a53; }
 p { margin: 6px 0; }
 ul { margin: 4px 0 4px 1.3em; padding: 0; }
 li { margin: 3px 0; }
 .lead { font-size: 15px; margin: 4px 0 10px; }
 .why { display: block; font-size: 12px; color: #656d76; }
 .sub { font-size: 12px; color: #656d76; }
 .note { border: 1px solid #d4a72c; background: #fff8c5; border-radius: 6px; padding: 8px 12px; margin: 12px 0; font-size: 13px; }
 .care { border: 1px solid #d0d7de; background: #f6f8fa; border-radius: 6px; padding: 6px 14px; }
 .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin: 12px 0; }
 .card { display: block; border: 1px solid #d0d7de; border-top: 4px solid var(--who, #57606a); border-radius: 6px;
         padding: 10px 14px 12px; color: inherit; text-decoration: none; background: #fff; }
 a.card:hover { border-color: var(--who, #57606a); }
 .card .nick { font-size: 18px; font-weight: 700; }
 .card .key { font-size: 12px; color: #656d76; margin-left: 6px; }
 .card .ttl { font-size: 13px; color: #424a53; margin: 0 0 8px; }
 .card dl { margin: 0; display: grid; grid-template-columns: 5em 1fr; gap: 4px 8px; font-size: 13px; }
 .card dt { color: #656d76; }
 .card dd { margin: 0; }
 .chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
 .chip { border: 1px solid #d0d7de; border-radius: 6px; padding: 4px 10px; font-size: 13px; background: #f6f8fa; }
 .chip b { display: block; font-size: 11px; font-weight: 400; color: #656d76; }
 .flow { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin: 12px 0; counter-reset: step; }
 .flow > div { border: 1px solid #d0d7de; border-radius: 6px; padding: 10px 12px; background: #f6f8fa; counter-increment: step; }
 .flow b { display: block; font-size: 14px; margin-bottom: 2px; }
 .flow b::before { content: counter(step) ". "; color: #656d76; }
 .steps { margin: 6px 0 6px 1.4em; padding: 0; }
 .steps li { margin: 8px 0; }
 .ruler { margin: 10px 0 4px; max-width: 640px; }
 .ruler .bar { display: flex; height: 26px; border-radius: 4px; overflow: hidden; border: 1px solid #d0d7de; font-size: 12px; }
 .ruler .bar span { display: flex; align-items: center; justify-content: center; white-space: nowrap; overflow: hidden; }
 .ruler .sell { background: #d8dee4; }
 .ruler .wait { background: repeating-linear-gradient(45deg, #ffffff, #ffffff 5px, #eaeef2 5px, #eaeef2 10px); }
 .ruler .buy { background: var(--who, #57606a); color: #fff; }
 .ruler .ticks { position: relative; height: 18px; font-size: 11px; color: #656d76; }
 .ruler .ticks span { position: absolute; transform: translateX(-50%); }
 .ruler .ticks span:first-child { transform: none; }
 .ruler .ticks span:last-child { transform: translateX(-100%); }
 .tag { display: inline-block; font-size: 11px; line-height: 16px; padding: 0 6px; margin-left: 6px; border-radius: 8px; border: 1px solid #d0d7de; color: #57606a; white-space: nowrap; vertical-align: 1px; }
 .tag.trial { border-color: #d4a72c; color: #7d4e00; background: #fff8c5; }
 .tag.guess { border-color: #cf222e; color: #a40e26; }
 .model { border: 1px solid #d0d7de; border-left: 4px solid var(--who, #57606a); border-radius: 6px; padding: 10px 14px; margin: 10px 0; }
 .model .lb { font-size: 16px; font-weight: 700; }
 .model code { font-size: 12px; color: #57606a; }
 details { margin: 28px 0 0; }
 summary { cursor: pointer; color: #656d76; font-size: 13px; }
 table { border-collapse: collapse; margin: 8px 0; font-size: 13px; }
 th, td { border: 1px solid #d0d7de; padding: 3px 10px; text-align: left; }
 th { background: #f6f8fa; font-weight: 600; }
"""


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _load(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def load_words(paths: TraderPaths) -> dict:
    """`common`・`nicks` ＝ 人の側（traders.toml）／ `mcommon`・`models` ＝ モデルの側（models.toml）。"""
    doc, mdoc = _load(paths.words), _load(paths.models)
    return {"common": doc.get("common") or {}, "nicks": doc.get("nicks") or {},
            "mcommon": mdoc.get("common") or {}, "models": list(mdoc.get("model", []))}


def _money(v: float) -> str:
    return f"${v:,.0f}" if float(v).is_integer() else f"${v:,.2f}"


def _num(v: float) -> str:
    return f"{v:g}"


def trader_files(paths: TraderPaths) -> list[tuple[str, Path, bool]]:
    """(鍵, 設定のファイル, 確定か)。直下の試験用でない人 ＋ 直下にまだ居ない候補。鍵の順。"""
    found: dict[str, tuple[Path, bool]] = {}
    for f in sorted(paths.traders_dir.glob("*.toml")):
        if _load(f).get("test") is not True:
            found[f.stem] = (f, True)
    for f in sorted((paths.traders_dir / "candidates" / "notional").glob("*.toml")):
        found.setdefault(f.stem, (f, False))
    return [(k, *found[k]) for k in sorted(found)]


def load_facts(paths: TraderPaths, name: str) -> dict | None:
    """設定から写す数字。⚠ 候補（金額指定の形）を読んだときは `settled` が False。"""
    hit = next(((f, ok) for k, f, ok in trader_files(paths) if k == name), None)
    if hit is None:
        return None
    src, settled = hit
    doc = _load(src)
    symbols = list(doc.get("symbols") or [])
    universe, group = doc.get("universe"), doc.get("universe_group")
    if not symbols and universe:
        groups = (_load(paths.universe_dir / f"{universe}.toml").get("groups") or {})
        symbols = list(groups.get(group) or []) if group else [s for g in groups.values() for s in g]
    budget = float(doc.get("budget_usd") or 0)
    line = float(doc.get("threshold") or 0)
    n = len(symbols)
    try:
        rel = src.relative_to(REPO_ROOT)
    except ValueError:
        rel = src
    models = [{"name": str(m.get("name") or ""), "method": str(m.get("method") or ""), "kind": str(m.get("kind") or "")}
              for m in doc.get("models", [])]
    return {
        "settled": settled, "source": str(rel), "budget": budget, "n": n, "k": len(models),
        "per": budget / n if n else 0.0, "line": line, "sell": 100.0 - line,
        "sizing": str(doc.get("sizing") or ""), "combine": str(doc.get("combine") or "asis"),
        "universe": (f"{universe}（{group}）" if group else str(universe or "")) or "、".join(symbols),
        "models": models,
    }


def find_model(words: dict, spec: dict) -> dict | None:
    """設定の [[models]] の 1 本に当たる説明。⚠ 手法（method）まで同じものだけ（違う手法は別のモデル）。"""
    return next((m for m in words["models"]
                 if m.get("name") == spec["name"] and str(m.get("method") or "") == spec["method"]), None)


def _fill(text: str, facts: dict) -> str:
    """文の中の {budget} などを設定の数字で埋める。⚠ 知らない札はそのまま残す（落とさない）。"""
    values = {"budget": _money(facts["budget"]), "n": str(facts["n"]), "per": _money(facts["per"]),
              "line": _num(facts["line"]), "sell": _num(facts["sell"]), "k": str(facts["k"])}
    out = str(text or "")
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out


def _line_text(common: dict, facts: dict) -> str:
    return _fill(common.get("line_same" if facts["line"] == facts["sell"] else "line_band"), facts)


def _buy_way(common: dict, facts: dict) -> str:
    return str(common.get("buy_way_shares" if facts["sizing"] == "shares" else "buy_way_notional") or "")


def _ul(items) -> str:
    return "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items or [] if i) + "</ul>"


def _who(words: dict, name: str) -> str:
    """呼び名（鍵）。呼び名が無ければ鍵だけ。"""
    nick = words["nicks"].get(name)
    return f"{nick}（{name}）" if nick else name


def ruler(common: dict, facts: dict) -> str:
    """出力スコアのものさし（0〜100）。売る ／ 何もしない ／ 買う の帯。⚠ 幅は設定の売買基準値（θ）から出す。"""
    sell, line = facts["sell"], facts["line"]
    segs = [("sell", sell, common.get("ruler_sell"))]
    if line > sell:
        wide = line - sell >= 18          # 「何もしない」の 5 文字が入る幅（640px の帯で約 115px）
        segs.append(("wait", line - sell, common.get("ruler_wait") if wide else common.get("ruler_wait_short") or ""))
    segs.append(("buy", 100.0 - line, common.get("ruler_buy")))
    bar = "".join(f"<span class='{c}' style='width:{w:g}%'>{esc(label)}</span>" for c, w, label in segs)
    ticks = "".join(f"<span style='left:{m:g}%'>{_num(m)}</span>" for m in sorted({0.0, sell, line, 100.0}))
    return f"<div class='ruler'><div class='bar'>{bar}</div><div class='ticks'>{ticks}</div></div>"


def _h2(no: int, title: str) -> str:
    return f"<h2><span class='no'>{no}</span>{esc(title)}</h2>"


def sidebar(paths: TraderPaths) -> dict:
    words = load_words(paths)
    return {"items": [{"id": name, "label": _who(words, name), "badge": "" if settled else "未確定"}
                      for name, _src, settled in trader_files(paths)]}


def _tag(common: dict, basis: str) -> str:
    basis = basis if basis in BASES else "guess"      # 印の無い文は、確かめていないものとして出す
    return f"<span class='tag {basis}'>{esc(common.get('basis_' + basis))}</span>"


def trader_body(paths: TraderPaths, name: str) -> str | None:
    words, facts = load_words(paths), load_facts(paths, name)
    if facts is None:
        return None
    common, mcommon = words["common"], words["mcommon"]
    index = [k for k, _f, _ok in trader_files(paths)].index(name)
    nick = words["nicks"].get(name)
    models = [(spec, find_model(words, spec)) for spec in facts["models"]]
    many = len(models) > 1
    out = [f"<div style='--who:{COLORS[index % len(COLORS)]}'>",
           f"<h1>{esc(nick or name)}" + (f"<small>{esc(name)}</small>" if nick else "") + "</h1>"]
    if not facts["settled"]:
        out.append(f"<p class='note'>⚠ {esc(common.get('unsettled'))}</p>")

    out.append(_h2(1, "使うモデル"))
    for spec, m in models:
        formal = f"<code>{esc(spec['name'])}</code>" + (f" <code>{esc(spec['method'])}</code>" if spec["method"] else "")
        if m is None:
            out.append(f"<div class='model'><div>{formal}</div><p>{esc(mcommon.get('no_words'))}</p></div>")
            continue
        card = m.get("card") or {}
        chips = [(mcommon.get("card_sees"), card.get("sees")), (mcommon.get("card_decides"), card.get("decides"))]
        # 「予測モデル」タブのこのモデルのページへ（⚠ iframe の中なので target=_top）
        more = (f"<p class='sub'><a href='{MODEL_URL}{esc(m['id'])}' target='_top'>{esc(mcommon.get('open_page'))}</a></p>"
                if m.get("id") else "")
        out.append(f"<div class='model'><div class='lb'>{esc(m.get('label'))}</div><div>{formal}</div>"
                   f"<p>{esc(m.get('summary'))}</p><div class='chips'>"
                   + "".join(f"<div class='chip'><b>{esc(k)}</b>{esc(v)}</div>" for k, v in chips if v)
                   + f"</div>{more}</div>")
    out.append(f"<p>{esc(_fill(common.get('combine_' + facts['combine']) or '', facts))}</p>")

    out.append(_h2(2, "モデルの特性"))
    for spec, m in models:
        if m is None:
            continue
        if many:
            out.append(f"<h3>{esc(m.get('label'))}</h3>")
        out.append("<ul>" + "".join(
            f"<li>{esc(t.get('text'))}{_tag(mcommon, str(t.get('basis') or ''))}"
            + (f"<span class='why'>理由: {esc(t['why'])}</span>" if t.get("why") else "") + "</li>"
            for t in m.get("traits") or []) + "</ul>")
    out.append(f"<p class='sub'>{esc(mcommon.get('basis_note'))}</p>")

    out.append(_h2(3, "何を見て、どう出力スコアを計算するか"))
    for spec, m in models:
        if m is None:
            continue
        if many:
            out.append(f"<h3>{esc(m.get('label'))}</h3>")
        for g in m.get("sees") or []:
            out.append(f"<h3>{esc(g.get('label'))}</h3>" + _ul(g.get("items")))
        if m.get("sees_note"):
            out.append(f"<p class='sub'>{esc(m['sees_note'])}</p>")
        out.append(f"<ol class='steps'><li><b>学ぶ</b>　{esc(common.get('step_learn'))}</li>"
                   f"<li><b>出力スコアをつける</b>　{esc(common.get('step_score'))}<br>{esc(m.get('how'))}</li></ol>")

    out.append(_h2(4, "この人の決まり"))
    out.append(f"<h3>売買基準値</h3><p>{esc(_line_text(common, facts))}</p>{ruler(common, facts)}")
    out.append(f"<h3>いつ</h3><p>{esc(common.get('when'))}</p>")
    out.append(f"<h3>いくら</h3><p>{esc(_fill(common.get('amount_one'), facts))}{esc(_buy_way(common, facts))}</p>"
               + _ul(common.get("money")))

    limits = [x for _spec, m in models if m for x in (m.get("limits") or [])]
    out.append(_h2(5, "気をつけること") + "<div class='care'>"
               + _ul([common.get("limit_common"), common.get("limit_purpose"), *limits, common.get("limit_survivor")]) + "</div>")

    rows = [("予測モデル", "、".join(f"<code>{esc(s['name'])}</code>（{esc(s['method'] or s['kind'])}）" for s, _m in models)),
            ("売買基準値（θ）", esc(_num(facts["line"]))), ("合成規則", f"<code>{esc(facts['combine'])}</code>"),
            ("銘柄集合", f"{esc(facts['universe'])}・{facts['n']} 本"),
            ("株数の決め方（sizing）", f"<code>{esc(facts['sizing'])}</code>"), ("予算", esc(_money(facts["budget"]))),
            ("設定のファイル", f"<code>{esc(facts['source'])}</code>" + ("" if facts["settled"] else "（候補）"))]
    table = "<table>" + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows) + "</table>"
    out.append("<details><summary>正式な名前（記録や設定に出てくる呼び名）</summary>"
               f"<p class='sub'>言葉の意味は <a href='{GLOSSARY_URL}' target='_top'>用語</a>、決めごとは "
               f"<a href='{SPEC_URL}' target='_top'>live-trading.md</a> §0-1。</p>{table}</details>")
    out.append("</div>")
    return "\n".join(out)


def body(paths: TraderPaths, item: str) -> str | None:
    return trader_body(paths, item)


def fingerprint(paths: TraderPaths) -> dict[str, float]:
    """見張り用。言葉の正本（人の側・モデルの側）と設定（確定・候補）の mtime。"""
    files = [paths.words, paths.models, *sorted(paths.traders_dir.glob("*.toml")),
             *sorted((paths.traders_dir / "candidates" / "notional").glob("*.toml"))]
    out = {}
    for f in files:
        try:
            out[str(f)] = f.stat().st_mtime
        except OSError:
            continue
    return out
