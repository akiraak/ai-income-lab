"""検証結果一覧の行（検証名）から、評価期間の切れ目をずらした config（rules.md 33 章・33-6）を機械で書く。

⚠ **中身は 33-2 ／ 33-6 の 3 の写し ＝ 新しい規約ではない**。手で書いた `cut13_*` ／ `cut23_*`（2026-11-11）と同じ作り方:
   元の実験の config を写し・手法を行の 1 本に絞り・θ を行の 1 水準にし・`[validation] cut_offset` を足す。
   表・モデル・k・fold の数・パージ・エンバーゴ・種・費用は触らない。
⚠ **回すと決めるのは結果を見る前**（開発ループでは 33-6 の 2 ＝ 新顔の行だけ。手で当てるなら先に登録簿へ）。
   この CLI は config を書くだけで、回さない・DB に書かない。
⚠ 書かない形（33-6 の 5）: 手法名に〔…〕の付いた行（上位 K・出口の線・トレーダーの形）・元の config が変種（訓練の窓・翌日始値など）の行。

    .venv/bin/python -m cli.cut_shift --from own.f1-7.ridge.shared~p1995-02-10@50 --print    # 2 本（1/3 ・ 2/3）を標準出力へ
    .venv/bin/python -m cli.cut_shift --from <検証名>                                         # config/experiment/dev_cut13_<元>_<θ>.toml ／ dev_cut23_…
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib

import ail.bootstrap  # noqa: F401
from ail import catalog, rundb, runs
from ail.validation import splits
from cli import trader_shape as ts

OFFSETS = (("1/3", "cut13"), ("2/3", "cut23"))    # ⚠ rules.md 33-2 の 2 つだけ（`splits.CUT_OFFSETS` と同じ。足すなら新しい id で先に登録）
IDEA = "20261111-fold-cut-shift"                  # 棚卸しの区分（`select_candidates.group_of` が config の頭から読む）
PLAIN = "閾値売買"


def style_of(offset: str) -> str:
    """ずらしの量 → 検証方式の変種の名前（`catalog.threshold_style` と同じ綴り）。"""
    return catalog.threshold_style({"trading": {"style": "threshold"}, "validation": {"cut_offset": offset}})


def config_name(experiment: str, theta: str, tag: str) -> str:
    return f"dev_{tag}_{re.sub(r'[^a-z0-9]+', '-', experiment.lower()).strip('-')}_{theta}"


def _is_counted(method: str) -> bool:
    """閾値売買の行として数える手法か（`catalog.is_trial` の名前の条件の写し）。"""
    return not (method.startswith("基準 ") or method == "乱択（基準）")


def counted(text: str) -> int:
    """その config を回すと増える検証の数（手法 × θ）。⚠ 〔…〕の行を作る項目は `cut_config` が外してある前提。"""
    doc = tomllib.loads(text)
    t = doc.get("trading") or {}
    methods = [str(s) for s in doc.get("selectors") or []] + [str(s) for s in doc.get("detectors") or []]
    methods += [str(m.get("name")) for m in t.get("mix") or []]
    return sum(1 for m in methods if _is_counted(m)) * max(1, len(t.get("thresholds") or []))


def _keep_array_table(text: str, header: str, keep) -> str:
    """`[[header]]` の塊のうち `keep(その塊を読んだ dict)` が真のものだけ残す（直前のコメント行は塊と一緒に扱う）。"""
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != header:
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        while j < len(lines) and not ts._HEADER.match(lines[j]):
            j += 1
        end = j
        while end > i + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
            end -= 1                                  # 次の見出しに付いたコメントと空行は次の塊のもの
        block = lines[i:end]
        if keep(tomllib.loads("\n".join(block))):
            out += block
        else:
            while out and out[-1].lstrip().startswith("#"):
                out.pop()
            while out and not out[-1].strip():
                out.pop()
            out.append("")
        out += lines[end:j]
        i = j
    return "\n".join(out).rstrip("\n") + "\n"


def _add_cut_offset(text: str, offset: str) -> str:
    line = (f'cut_offset = "{offset}"            # ⚠ fold 1〜5 の初日を、その群の日付の数 × {offset}（切り捨て）個ぶん後ろへ'
            "（rules.md 33-1。回す前に固定）")
    lines = text.splitlines()
    at = next((i for i, ln in enumerate(lines) if ln.strip() == "[validation]"), None)
    if at is None:
        return text.rstrip("\n") + "\n\n[validation]\n" + line + "\n"
    end = at + 1
    while end < len(lines) and not ts._HEADER.match(lines[end]):
        end += 1
    while end > at + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")):
        end -= 1
    return "\n".join(lines[:end] + [line] + lines[end:]) + "\n"


def cut_config(text: str, *, src: str, method: str, theta: str, offset: str, name: str) -> str:
    """元の実験の TOML（文字列）→ 切れ目をずらした config（文字列）。⚠ 表・モデル・k・fold・種・費用は触らない。"""
    if offset not in splits.CUT_OFFSETS:
        raise SystemExit(f"⚠ ずらしは {' ／ '.join(splits.CUT_OFFSETS)} だけ（rules.md 33-2）: {offset!r}")
    doc = tomllib.loads(text)
    t = doc.get("trading") or {}
    if t.get("style") != "threshold":
        raise SystemExit(f"⚠ {src}: `[trading] style = \"threshold\"` の config だけ（rules.md 33 章）")
    if catalog.threshold_style(doc) != PLAIN:
        raise SystemExit(f"⚠ {src}: 元の config が変種（{catalog.threshold_style(doc)}）＝ 機械では書かない（rules.md 33-6 の 5）")
    sel = [str(s) for s in doc.get("selectors") or []]
    det = [str(s) for s in doc.get("detectors") or []]
    mixes = {str(m.get("name")): m for m in t.get("mix") or []}
    kind = "detector" if method in det else "selector" if method in sel else "mix" if method in mixes else None
    if kind is None:
        raise SystemExit(f"⚠ {src} の selectors ／ detectors ／ trading.mix に無い手法: {method!r}"
                         "（〔…〕の付いた行は機械では書かない ＝ rules.md 33-6 の 5）")
    out = ts._strip_header(text)
    out = ts._replace_statement(out, "name", f'name = "{name}"')
    if "features_from" not in doc:
        out = re.sub(r"(?m)^(name\s*=[^\n]*\n)", rf'\1features_from = "{src}"   # ⚠ 表は作り直さず、元の実験のものを読む（13-8）\n', out, count=1)
    note = "⚠ 当てる行の 1 本だけ（cli.cut_shift）"
    if kind == "selector":
        out = ts._replace_statement(out, "selectors", f"selectors = {ts._array([method])}   # {note}")
    elif kind == "detector":
        out = ts._replace_statement(out, "detectors", f"detectors = {ts._array([method])}   # {note}")
    else:
        # 合成: その合成 1 つと、この実行の中のメンバー（experiment の無いメンバー）だけ（33-6 の 3）
        inner = [str(m.get("method")) for m in mixes[method].get("members") or [] if not m.get("experiment")]
        note = "⚠ 合成のメンバーだけ（cli.cut_shift）"
        if sel and (keep := [s for s in sel if s in inner]) and keep != sel:
            out = ts._replace_statement(out, "selectors", f"selectors = {ts._array(keep)}   # {note}")
        if det and (keep := [s for s in det if s in inner]) and keep != det:
            out = ts._replace_statement(out, "detectors", f"detectors = {ts._array(keep)}   # {note}")
        if len(mixes) > 1:
            out = _keep_array_table(out, "[[trading.mix]]", lambda d: str(d["trading"]["mix"][0].get("name")) == method)
    th = f"{float(theta):g}"
    out = ts._replace_statement(out, "thresholds", f"thresholds = [{th}]              # ⚠ 検証結果一覧の行の 1 水準だけ（rules.md 33-6 の 2）")
    out = ts._remove_table(out, "[trading.position_exits]")
    out = ts._remove_statement(out, "top_k")
    out = ts._remove_statement(out, "top_k_budgets_usd")
    out = ts._remove_table(out, "[[trading.trader]]")
    out = _add_cut_offset(out, offset)
    header = (
        f"# ⚠ **評価期間の切れ目をずらして上位の行を測り直す**（rules.md 33 章・33-6 ＝ 選ぶ段の併記・登録簿 `{IDEA}`）。\n"
        f"#    `cli.cut_shift` が機械で書いた（元 `{src}.toml`・手法 {method}・θ {th}・ずらし {offset}）。\n"
        f"# ⚠ **`{src}.toml` との違い**: `[validation] cut_offset = \"{offset}\"`・θ を行の 1 水準に・手法を当てる行に絞った"
        f"（合成はその合成とこの実行の中のメンバーだけ）・\n"
        f"#    表は `features_from` で借りる（作り直さない ＝ 13-8）・`[[trading.trader]]` ／ 上位 K ／ 出口の線の項目は外した。"
        f"表・モデル・k・fold の数・種・費用は同じ。\n"
        f"# ⚠ **回すと決めたのは結果を見る前**（33-6 の 2 ＝ 新しく上位に入った行だけ）。⚠ 回した行は全部数える（33-6 の 4）。"
        f"⚠ 残っても採る根拠にしない（33-4 の 1）。\n"
        f"#\n"
        f"# ⚠ **TOML の平の key はテーブル見出しより上に書く**（rules.md 10 章 規約 1）\n")
    text_out = header + out
    got = tomllib.loads(text_out)                     # ⚠ 壊れた TOML を書かない
    if catalog.threshold_style(got) != style_of(offset):
        raise SystemExit(f"⚠ {src}: 書いた config の検証方式が {catalog.threshold_style(got)}（{style_of(offset)} のはず）")
    return text_out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="trial", required=True, metavar="検証名", help="検証結果一覧の行（θ つき）")
    ap.add_argument("--print", action="store_true", help="書かずに標準出力へ")
    ap.add_argument("--force", action="store_true", help="同じ道にあっても書き換える")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    conn = rundb.connect(args.db or runs.db_path(), readonly=True)
    try:
        t = ts.resolve_trial(conn, args.trial)
    finally:
        conn.close()
    print(f"  {t['method']!r} θ {t['theta']} ← {t['run']}（{t['experiment']}）", file=sys.stderr)
    for offset, tag in OFFSETS:
        name = config_name(t["experiment"], t["theta"], tag)
        text = cut_config(ts.source_text(t["experiment"]), src=t["experiment"], method=t["method"], theta=t["theta"],
                          offset=offset, name=name)
        if args.print:
            print(text)
            continue
        path = ts.source_path(name)
        if os.path.exists(path) and not args.force:
            raise SystemExit(f"⚠ もうある: {path}（書き換えるなら --force。⚠ 既に回した config を書き換えると記録とずれる）")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {path}（数える {counted(text)}）", file=sys.stderr)


if __name__ == "__main__":
    main()
