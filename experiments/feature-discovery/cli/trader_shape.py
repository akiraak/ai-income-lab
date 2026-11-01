"""検証結果一覧の行（検証名）から、トレーダーと同じ形で机上にかける config（rules.md 20 章・howto-model-trader.md §3-2）を機械で書く。

⚠ **中身は 20 章の写し ＝ 新しい規約ではない**。手で写していた作業（元の実験の config を写し・手法を候補に絞り・θ を行の 1 水準にし・
   `[[trading.trader]]` を 2 つ足す）をそのまま機械でやる。⚠ 20 章の売買の規則・条件・判定は 1 行も変えない。
⚠ **回すと決めるのは結果を見る前**（事前固定 ＝ 20 章の節 ／ 登録簿を先に書く）。この CLI は config を書くだけで、回さない・DB に書かない。
⚠ 2a の条件は **5 本・$600**（rules.md 25-2 の 7 ＝ 2026-10-04 から 1 人 $600。`--budget 300` で 2026-10-02 までの形）。綴りは `config/names.toml` の `[trader]` に無ければ止まる。

    .venv/bin/python -m cli.trader_shape --from own.f1-7.ridge.shared~p1995-02-10@50            # DB で検証名 → 元の実験・手法・θ を引く
    .venv/bin/python -m cli.trader_shape --from <検証名> --from <同じ実験・同じ θ の検証名>     # 1 つの config に 2 本
    .venv/bin/python -m cli.trader_shape --experiment trade_own_ridge_a --method "全部使う（基準）" --theta 50 --budget 300 --print
    （既定の置き場 `config/experiment/trader_<元の実験名>.toml`。あれば `--force` が無い限り書かない）
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import tomllib

import ail.bootstrap  # noqa: F401
from ail import config as cfg_mod
from ail import names, rundb, runs

SYMBOLS = ["T", "PFE", "NKE", "VZ", "BAC"]     # 2a の 5 本（2026-09-21 に実際の 3 人で確定。rules.md 20-2。⚠ 結果を見て選び直さない）
TAG_ALL = "全銘柄・全部を買える予算"              # 2b
DEFAULT_BUDGET = 600                           # rules.md 25-2 の 7（2026-10-04 から 1 人 $600）
_HEADER = re.compile(r"^\s*\[")
_TRADING_HEADER = re.compile(r"^\s*\[\[?trading(\.|\]|\]\])")


# ---------------------------------------------------------------- 検証名 → 元の実験


def resolve_trial(conn: sqlite3.Connection, trial_name: str) -> dict:
    """検証結果一覧の行 → {experiment, method, theta, run}。⚠ 元の実験 ＝ 実行一覧のうち `[[trading.trader]]` の無い最初の実行の config。"""
    row = conn.execute("SELECT doc FROM ledger_rows WHERE leak = 0 AND is_trial = 1 AND trial_name = ?", (trial_name,)).fetchone()
    if row is None:
        raise SystemExit(f"⚠ 検証結果一覧に {trial_name!r} が無い（leak = 0・数える行だけ。綴りは ledger.md の行の名前）")
    import json
    doc = json.loads(row[0])
    th = str(doc.get("閾値") or "")
    if doc.get("検証方式") != "閾値売買" or th in ("", "—"):
        raise SystemExit(f"⚠ {trial_name!r} は閾値売買の行ではない（検証方式 {doc.get('検証方式')}）＝ 20 章の器にかけられない")
    for run in doc.get("実行一覧") or []:
        c = runs.read_json(str(run), "config.json")
        if not c or not c.get("name"):
            continue
        if (c.get("trading") or {}).get("trader"):
            continue
        return {"experiment": str(c["name"]), "method": str(doc.get("手法名")), "theta": th, "run": str(run)}
    raise SystemExit(f"⚠ {trial_name!r} の実行一覧に、トレーダーの形でない実行の config が無い（手で `--experiment` を指定する）")


def source_path(experiment: str) -> str:
    return os.path.join(cfg_mod.CONFIG, "experiment", f"{experiment}.toml")


def source_text(experiment: str) -> str:
    path = source_path(experiment)
    if not os.path.exists(path):
        raise SystemExit(f"⚠ 元の実験の config が無い: {path}")
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- 文字列の加工（コメントは残す）


def _strip_header(text: str) -> str:
    """先頭のコメントと空行（元の実験の説明）を落とす。"""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith("#")):
        i += 1
    return "\n".join(lines[i:]) + "\n"


def _replace_statement(text: str, key: str, new: str) -> str:
    """`key = …`（配列は複数行でも）を 1 文ごと `new` に替える。無ければ止まる。"""
    pat = re.compile(rf"(?ms)^{re.escape(key)}\s*=\s*(\[.*?\][^\n]*|[^\n]*)$")
    m = pat.search(text)
    if m is None:
        raise SystemExit(f"⚠ 元の config に `{key} =` が無い")
    return text[:m.start()] + new + text[m.end():]


def _remove_statement(text: str, key: str) -> str:
    pat = re.compile(rf"(?ms)^{re.escape(key)}\s*=\s*(\[.*?\][^\n]*|\{{[^\n]*\}}[^\n]*|[^\n]*)\n?")
    return pat.sub("", text)


def _remove_table(text: str, header: str) -> str:
    """`[header]` から次の見出しの前までを落とす（直前に続くコメント行も）。"""
    lines = text.splitlines()
    out, i = [], 0
    while i < len(lines):
        if lines[i].strip() == header:
            # 直前に付いているコメント行を落とす
            while out and out[-1].lstrip().startswith("#"):
                out.pop()
            i += 1
            while i < len(lines) and not _HEADER.match(lines[i]):
                i += 1
            while out and not out[-1].strip():
                out.pop()
            out.append("")
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).rstrip("\n") + "\n"


def _array(values: list[str]) -> str:
    return "[" + ", ".join(f'"{v}"' for v in values) + "]"


def _trader_blocks(tag_a: str, budget: int, methods: list[str] | None) -> str:
    m = f"\nmethods = {_array(methods)}   # ⚠ 合成の行にだけ当てる（rules.md 20-6 の 4 の 5）" if methods else ""
    return ("\n# ⚠ 2a いまの人の条件（rules.md 20-2・25-2 の 7）。5 本は 2026-09-21 に実際の 3 人で確定したもの（⚠ 結果を見て選び直さない）\n"
            "[[trading.trader]]\n"
            f'tag = "{tag_a}"\n'
            f"symbols = {_array(SYMBOLS)}\n"
            f"budget_usd = {budget}{m}\n"
            "\n# ⚠ 2b 全銘柄を買える予算（rules.md 20-2）: 予算は fold ごとに「評価期間の close の最高値 × 銘柄数」\n"
            "[[trading.trader]]\n"
            f'tag = "{TAG_ALL}"{m}\n')


def _insert_after_trading(text: str, block: str) -> str:
    """`[trading]` と、それに続く `[trading.*]` ／ `[[trading.*]]` の後ろに差し込む。"""
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == "[trading]"), None)
    if start is None:
        raise SystemExit("⚠ 元の config に `[trading]` が無い（閾値売買の config だけ ＝ rules.md 20 章）")
    at = len(lines)
    for i in range(start + 1, len(lines)):
        if _HEADER.match(lines[i]) and not _TRADING_HEADER.match(lines[i]):
            at = i
            break
    # 見出しの直前に付いているコメント行の前に入れる
    while at > 0 and lines[at - 1].lstrip().startswith("#"):
        at -= 1
    head = "\n".join(lines[:at]).rstrip("\n")
    tail = "\n".join(lines[at:]).rstrip("\n")
    return head + "\n" + block + ("\n" + tail + "\n" if tail else "")


def shape_config(text: str, *, src: str, methods: list[str], theta: str, budget: int = DEFAULT_BUDGET,
                 name: str | None = None) -> str:
    """元の実験の TOML（文字列）→ トレーダーの形の config（文字列）。⚠ 表・モデル・k・fold・種・コストは触らない。"""
    doc = tomllib.loads(text)
    if (doc.get("trading") or {}).get("style") != "threshold":
        raise SystemExit(f"⚠ {src}: `[trading] style = \"threshold\"` の config だけ（rules.md 20 章）")
    tag_a = f"5本・${budget}"
    if tag_a not in names.table().get("trader", {}):
        raise SystemExit(f"⚠ 条件 {tag_a!r} の綴りが config/names.toml の [trader] に無い（先に綴りを登録する ＝ rules.md 20-4 の 4）")
    sel = [str(s) for s in doc.get("selectors") or []]
    det = [str(s) for s in doc.get("detectors") or []]
    mix = [str(m.get("name")) for m in (doc.get("trading") or {}).get("mix") or []]
    kind = {m: ("detector" if m in det else "selector" if m in sel else "mix" if m in mix else None) for m in methods}
    if any(v is None for v in kind.values()):
        bad = [m for m, v in kind.items() if v is None]
        raise SystemExit(f"⚠ {src} の selectors ／ detectors ／ trading.mix に無い手法: {bad}")
    kinds = set(kind.values())
    if len(kinds) != 1:
        raise SystemExit(f"⚠ 手法の種類が混ざっている（{kind}）＝ 別々の config にする")
    k = kinds.pop()
    out = _strip_header(text)
    new_name = name or f"trader_{src}"
    out = _replace_statement(out, "name", f'name = "{new_name}"')
    if "features_from" not in doc:
        out = re.sub(r"(?m)^(name\s*=[^\n]*\n)", rf'\1features_from = "{src}"   # ⚠ 表は作り直さず、元の実験のものを読む（13-8）\n', out, count=1)
    if k == "selector":
        keep = [s for s in sel if s in methods]
        out = _replace_statement(out, "selectors", f"selectors = {_array(keep)}   # ⚠ 候補の {len(keep)} 本だけ（cli.trader_shape）")
    elif k == "detector":
        keep = [s for s in det if s in methods]
        out = _replace_statement(out, "detectors", f"detectors = {_array(keep)}   # ⚠ 候補の {len(keep)} 本だけ（cli.trader_shape）")
    th = f"{float(theta):g}"
    out = _replace_statement(out, "thresholds", f"thresholds = [{th}]              # ⚠ 検証結果一覧の行の 1 水準だけ（rules.md 20-4 の 1）")
    out = _remove_table(out, "[trading.position_exits]")
    out = _remove_statement(out, "top_k")
    out = _remove_statement(out, "top_k_budgets_usd")
    out = _remove_table(out, "[[trading.trader]]")
    out = _insert_after_trading(out, _trader_blocks(tag_a, budget, methods if k == "mix" else None))
    n = len(methods)
    header = (
        f"# ⚠ **検証結果一覧の行をトレーダーと同じ形で机上にかける**（rules.md 20 章・howto-model-trader.md §3-2）。\n"
        f"#    `cli.trader_shape` が機械で書いた（元 `{src}.toml`・手法 {' ／ '.join(methods)}・θ {th}）。\n"
        f"# ⚠ **`{src}.toml` との違い**: 手法を候補の {n} 本に絞った・θ を行の 1 水準にした・表は `features_from` で借りる（作り直さない ＝ 13-8）・\n"
        f"#    `[[trading.trader]]` を 2 つ足した（2a {tag_a} ／ 2b {TAG_ALL}）。表・モデル・k・fold・種・コストは同じ\n"
        f"#    （素の行は既存と同じ識別項目にまとまる ＝ 再現の検算）。\n"
        f"# ⚠ **回すと決めるのは結果を見る前**（事前固定 ＝ 20 章の節 ／ 登録簿を先に書く）。⚠ {n} 本 × 条件 2 ＝ {2 * n} 検証として数える（leak 対照は queue が足す）。\n"
        f"#\n"
        f"# ⚠ **TOML の平の key はテーブル見出しより上に書く**（rules.md 10 章 規約 1）\n")
    text_out = header + out
    tomllib.loads(text_out)   # ⚠ 壊れた TOML を書かない
    return text_out


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="trials", action="append", default=[], metavar="検証名",
                    help="検証結果一覧の行（θ つき）。複数なら同じ実験・同じ θ だけ 1 つの config にまとめる")
    ap.add_argument("--experiment", default=None, help="元の実験名（DB を引かずに直接）")
    ap.add_argument("--method", dest="methods", action="append", default=[], help="手法名（--experiment と一緒に。複数可）")
    ap.add_argument("--theta", default=None, help="θ（--experiment と一緒に）")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, help=f"2a の予算（既定 {DEFAULT_BUDGET} ＝ rules.md 25-2 の 7）")
    ap.add_argument("--name", default=None, help="config の name（既定 trader_<元の実験名>）")
    ap.add_argument("--out", default=None, help="書く道（既定 config/experiment/<name>.toml）")
    ap.add_argument("--print", action="store_true", help="書かずに標準出力へ")
    ap.add_argument("--force", action="store_true", help="同じ道にあっても書き換える")
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)

    if args.trials and (args.experiment or args.methods or args.theta):
        raise SystemExit("⚠ --from と --experiment ／ --method ／ --theta は同時に使えない")
    if args.trials:
        conn = rundb.connect(args.db or runs.db_path(), readonly=True)
        try:
            found = [resolve_trial(conn, t) for t in args.trials]
        finally:
            conn.close()
        exps, ths = {f["experiment"] for f in found}, {f["theta"] for f in found}
        if len(exps) != 1 or len(ths) != 1:
            raise SystemExit(f"⚠ 元の実験か θ が違う行は別々の config にする: {[(f['experiment'], f['theta']) for f in found]}")
        experiment, theta = exps.pop(), ths.pop()
        methods = [f["method"] for f in found]
        for f in found:
            print(f"  {f['method']!r} θ {f['theta']} ← {f['run']}（{f['experiment']}）", file=sys.stderr)
    else:
        if not (args.experiment and args.methods and args.theta):
            raise SystemExit("⚠ --from <検証名> か、--experiment と --method と --theta をそろえて指定する")
        experiment, methods, theta = args.experiment, args.methods, args.theta
    text = shape_config(source_text(experiment), src=experiment, methods=methods, theta=theta,
                        budget=args.budget, name=args.name)
    if args.print:
        print(text, end="")
        return
    name = args.name or f"trader_{experiment}"
    out = args.out or source_path(name)
    if os.path.exists(out) and not args.force:
        raise SystemExit(f"⚠ もうある: {out}（書き換えるなら --force。⚠ 既に回した config を書き換えると記録とずれる）")
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {out}", file=sys.stderr)
    print(f"  次: rules.md 20 章の節（事前固定・数 {2 * len(methods)}）→ config/queue/<名前>.toml（leak = true）→ cli.queue → cli.report --catalog → 記録",
          file=sys.stderr)


if __name__ == "__main__":
    main()
