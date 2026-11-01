"""検証結果一覧に howto-model-trader.md §3-1 の物差し（2026-09-30 固定）を機械で当て、候補の上位を出す。

⚠ **読むだけ**（研究の DB `runs/research.sqlite` の `ledger_rows` と実行の記録を読み取り専用で読む。`runs/` を作らない・DB に書かない）。
⚠ **物差しは 1 つも変えない**（howto §3-1 の 6 つを写す）。ここで出るのは「並べたもの」であって採用ではない ＝
   候補の人にするかは利用者（§3-4）・本番に入れるかも利用者（CLAUDE.md）。成績で人を選ばない。
⚠ **併記（DSR・日次 t・最大の fold を外した平均・取引/fold・保有日率）は診断**で、順位には使わない（rules.md 11 章 規約 2・3 ＝ 良い数字は根拠「中」が上限）。

決まり（§3-1）と、この CLI の読み方:

  1. 対象 ＝ 検証方式「閾値売買」（⚠ 変種〔訓練直近N日・翌日始値・費用20bp〕は執行器の形ではないので対象外）・判定「保留」
  2. 除く ＝ 基準線（手法名が `基準 ` で始まる・乱択（基準）・全部使う（基準）＝ `checks.is_baseline`。2026-09-30 の当て方の写し）／
     上位 K の行（〔上位N・…〕）／ 無効・要再測（日足 × raw）／ 較正「旧」／ 〔トレーダー・…〕の行（2026-10-08）。合成の行は含める
  3. 第 1 キー ＝ fold の上乗せの符号が正の数・第 2 キー ＝ 上乗せの平均（bp/fold）
  4. 同じ予測モデル名（θ を除いた名前）が θ 違いで並んだら 1 位の 1 行だけ
  5. 上位 3 本（`--top`）。いまの人と同じモデルも除かない
  6. 本番の道に載らない形は印を付ける（1995 年からの表・本番の 63 銘柄の集合に無い・合成・執行器に無い形）

    .venv/bin/python -m cli.select_candidates                       # いまの上位 3（併記つき）
    .venv/bin/python -m cli.select_candidates --all --no-diag       # 決まり 4 まで通した全部（並べ順）
    .venv/bin/python -m cli.select_candidates --asof 2026-09-30     # 再現の検算（howto §3-1 の実測: 17 行 → F1-7・F2-2・D2）
    .venv/bin/python -m cli.select_candidates --json out.json
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import ail.bootstrap  # noqa: F401
from ail import names, rundb, runs
from ail.validation import checks, stats

STYLE = "閾値売買"                 # 決まり 1（変種は対象外）
VERDICT = "保留"
BH = "基準 常に上（ドリフト）"       # 上乗せの相手（rules.md 13-7）
LIVE_SYMBOLS = {63, 48}            # 本番の集合（63 本・会社の 48 本）
LIVE_SETS = {"daily", "—"}
LIVE_START = "2018-01-01"          # 本番の日足（data-live/）は 2018 年から
_VARIANT = re.compile(r"〔([^〕]*)〕$")
RULES = ("閾値売買・保留", "基準線 ／ 上位 K ／ 無効 ／ 較正旧 ／ トレーダーの形 を除く",
         "fold の正の数 → 上乗せの平均", "同じ予測モデル名は 1 行", "上位 N", "印")


@dataclass
class Row:
    name: str                      # 検証名（θ つき）
    model: str                     # 予測モデル名（θ なし）
    verdict: str
    closed: bool
    first_run: str | None
    doc: dict
    marks: list[str] = field(default_factory=list)
    diag: dict | None = None

    @property
    def method(self) -> str:
        return str(self.doc.get("手法名") or "")

    @property
    def edge(self) -> float | None:
        v = self.doc.get("上乗せbp")
        return float(v) if isinstance(v, (int, float)) else None

    @property
    def fold(self) -> str:
        return str(self.doc.get("上乗せfold") or self.doc.get("fold") or "")

    @property
    def positive(self) -> int:
        f = self.fold
        return int(f.split("/")[0]) if f and f.split("/")[0].isdigit() else -1

    @property
    def theta(self) -> str:
        return str(self.doc.get("閾値") or "")

    @property
    def runs(self) -> list[str]:
        return [str(r) for r in (self.doc.get("実行一覧") or [])]


# ---------------------------------------------------------------- 読む


def open_db(path: str | None = None) -> sqlite3.Connection:
    """研究の DB を読み取り専用で開く（無ければ止まる）。"""
    return rundb.connect(path or runs.db_path(), readonly=True)


def load_rows(conn: sqlite3.Connection) -> list[Row]:
    """`ledger_rows` の本体（leak = 0・n_trials に数える行）。"""
    out = []
    for name, model, verdict, closed, first_run, doc in conn.execute(
            "SELECT trial_name, model_name, verdict, closed, first_run, doc FROM ledger_rows"
            " WHERE leak = 0 AND is_trial = 1 ORDER BY trial_name"):
        d = json.loads(doc) if doc else {}
        out.append(Row(str(name), str(model), str(verdict), bool(closed), first_run, d if isinstance(d, dict) else {}))
    return out


def meta(conn: sqlite3.Connection) -> dict:
    m = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    n = m.get("ledger_n_trials")
    return {"n_trials": int(n) if n and str(n).isdigit() else None, "built_at": m.get("ledger_built_at") or ""}


# ---------------------------------------------------------------- 物差し


def is_topk(method: str) -> bool:
    """上位 K の行（rules.md 17-5）。`〔上位3・端数〕`・`ボラ上位〔…・上位3・…〕`。"""
    return bool(names._TOPK.search(method) or names._TOPK_BASE.match(method))


def is_trader_shape(method: str) -> bool:
    return bool(names._TRADER.search(method))


def passes_rule_1(r: Row) -> bool:
    return r.doc.get("検証方式") == STYLE and r.verdict == VERDICT


def excluded_by_rule_2(r: Row) -> str | None:
    """除く理由（None ＝ 残す）。"""
    m = r.method
    if checks.is_baseline(m):
        return "基準線"
    if is_topk(m):
        return "上位 K"
    if r.doc.get("粒度") == "日足" and r.doc.get("層") == "raw":
        return "無効・要再測"
    if r.doc.get("較正") == "旧":
        return "較正「旧」"
    if is_trader_shape(m):
        return "トレーダーの形"
    return None


def sort_key(r: Row):
    """決まり 3: fold の正の数（多い順）→ 上乗せの平均（大きい順）。同点は名前。"""
    return (-r.positive, -(r.edge if r.edge is not None else float("-inf")), r.name)


def marks_of(r: Row) -> list[str]:
    """決まり 6 の印（本番の道に載らない形）＋ 閉じる注記。⚠ 除く理由ではない。"""
    out = []
    period = str(r.doc.get("期間") or "")
    if period and period != "—" and period < LIVE_START:
        out.append(f"{period[:4]} 年からの表（本番の日足は 2018 年から）")
    syms, sset = r.doc.get("銘柄"), str(r.doc.get("集合") or "—")
    try:
        n_sym = int(str(syms))
    except (TypeError, ValueError):
        n_sym = None
    if (n_sym is not None and n_sym not in LIVE_SYMBOLS) or sset not in LIVE_SETS:
        out.append(f"本番の 63 銘柄の集合に無い（{syms} 本・{sset}）")
    if r.method in names.table().get("mix", {}):
        out.append("合成の行（人は members を執行器の合成規則で持つ）")
    if (m := _VARIANT.search(r.method)) and not is_trader_shape(r.method) and not is_topk(r.method):
        out.append(f"執行器に無い形〔{m.group(1)}〕")
    if r.closed:
        out.append("閉じる注記あり")
    return out


def apply_rules(rows: list[Row], asof: str | None = None, top: int = 3) -> dict:
    """決まり 1〜6 を順に当てる。返すのは段ごとの数と並べた行（全部）・上位 N。"""
    if asof:
        rows = [r for r in rows if not r.first_run or r.first_run <= asof]
    r1 = [r for r in rows if passes_rule_1(r)]
    excluded: dict[str, int] = {}
    r2 = []
    for r in r1:
        why = excluded_by_rule_2(r)
        if why:
            excluded[why] = excluded.get(why, 0) + 1
        else:
            r2.append(r)
    ranked = sorted(r2, key=sort_key)
    seen: set[str] = set()
    r4 = []
    for r in ranked:
        if r.model in seen:
            continue
        seen.add(r.model)
        r4.append(r)
    for r in r4:
        r.marks = marks_of(r)
    return {"asof": asof, "counted": len(rows), "rule1": len(r1), "excluded": excluded, "rule2": len(r2),
            "rule4": len(r4), "ranked": r4, "top": r4[:top]}


# ---------------------------------------------------------------- 併記（診断。順位に使わない）


def _series(daily: pd.DataFrame, method: str, th: float) -> pd.Series:
    d = daily[(daily["手法"] == method) & (daily["閾値"] == th) & (daily["系列"] == "純利bp")]
    return d.set_index("ts")["値"].astype(float).sort_index()


def diagnostics(r: Row, n_trials: int | None) -> dict | None:
    """実行の記録から: DSR（いまの n_trials で引き直す）・日次 t（対 B&H）・最大の fold を外した上乗せの平均・取引/fold・保有日率。
    ⚠ 読むのは検証名の実行一覧の先頭から、記録のそろう最初の 1 本。無ければ None。"""
    try:
        th = float(r.theta)
    except ValueError:
        return None
    for run in r.runs:
        res = runs.read_csv(run, "result.csv")
        if res is None or "閾値" not in res:
            continue
        m = res[(res["手法"] == r.method) & (res["閾値"] == th)].sort_values("fold")
        b = res[(res["手法"] == BH) & (res["閾値"] == th)].sort_values("fold")
        if m.empty or len(m) != len(b):
            continue
        edge = m["純利bp"].values - b["純利bp"].values
        out: dict = {"run": run, "folds": int(len(edge)),
                     "edge_mean": float(edge.mean()),
                     "edge_drop_max": float(np.delete(edge, int(np.argmax(edge))).mean()) if len(edge) >= 2 else None,
                     "trades_per_fold": float(m["取引回数"].mean()) if "取引回数" in m else None,
                     "hold_rate": float(m["保有日率"].mean()) if "保有日率" in m else None}
        daily = runs.read_csv(run, "daily.csv")
        if daily is not None and {"手法", "閾値", "系列", "ts", "値"} <= set(daily.columns):
            s, sb = _series(daily, r.method, th), _series(daily, BH, th)
            if len(s) >= 3 and float(s.std()) > 0:
                sr = float(s.mean() / s.std())
                skew, kurt = float(s.skew()), float(s.kurt()) + 3.0
                out["sr_daily"] = sr
                out["n_obs"] = int(len(s))
                if n_trials and n_trials >= 2:
                    d = stats.deflated_sharpe(sr, len(s), int(n_trials), skew, kurt)
                    out["dsr"] = None if np.isnan(d["DSR"]) else float(d["DSR"])
                    out["n_trials"] = int(n_trials)
            j = pd.concat([s.rename("m"), sb.rename("b")], axis=1, join="inner").dropna()
            if len(j) >= 3:
                diff = j["m"] - j["b"]
                sd = float(diff.std(ddof=1))
                out["daily_t"] = float(diff.mean() / (sd / np.sqrt(len(diff)))) if sd > 0 else None
        return out
    return None


# ---------------------------------------------------------------- まとめる・描く


def select(conn: sqlite3.Connection, asof: str | None = None, top: int = 3, diag: bool = True,
           all_rows: bool = False) -> dict:
    m = meta(conn)
    res = apply_rules(load_rows(conn), asof=asof, top=top)
    if diag:
        for r in (res["ranked"] if all_rows else res["top"]):
            r.diag = diagnostics(r, m["n_trials"])
    res.update(m)
    return res


def _f(v, digits: int = 1, sign: bool = True) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "—"
    return f"{v:+,.{digits}f}" if sign else f"{v:,.{digits}f}"


def _row_cells(i: int, r: Row) -> list[str]:
    who = f"{r.method}（{r.doc.get('モデル')}・{r.doc.get('特徴量の層')}・{r.doc.get('期間')}・{r.doc.get('銘柄')} 本）"
    run = r.runs[0] if r.runs else "—"
    d = r.diag or {}
    return [str(i), f"`{r.name}`", who, r.fold, _f(r.edge), r.theta, f"`{run}`", "<br>".join(r.marks) or "—",
            _f(d.get("dsr"), 2, sign=False), _f(d.get("daily_t"), 2), _f(d.get("edge_drop_max")),
            _f(d.get("trades_per_fold"), 0, sign=False), _f(d.get("hold_rate"), 2, sign=False)]


def render(res: dict, all_rows: bool = False) -> str:
    head = ["順", "検証名", "手法（モデル・層・期間・銘柄）", "fold（上乗せの符号）", "上乗せ bp/fold", "θ", "実行", "印（決まり 6）",
            "DSR", "日次 t", "最大 fold 外し", "取引/fold", "保有日率"]
    lines = [f"## 候補の上位（howto §3-1 の物差し・読むだけ）— n_trials {res.get('n_trials')}・検証結果一覧 {res.get('built_at') or '—'}"
             + (f"・`--asof {res['asof']}`（{res['asof']} までに最初の実行があった行だけ）" if res.get("asof") else ""), ""]
    ex = " ／ ".join(f"{k} {v}" for k, v in sorted(res["excluded"].items())) or "—"
    lines.append(f"- 数える行 {res['counted']:,} → 決まり 1（{STYLE}・{VERDICT}）{res['rule1']} → 決まり 2 で除く {ex} → 残り {res['rule2']}"
                 f" → 決まり 4（同じ予測モデル名は 1 行）{res['rule4']} → 上位 {len(res['top'])}")
    lines.append("- ⚠ これは並べたものであって採用ではない。候補の人にするかは利用者（howto §3-4）・本番に入れるかも利用者。成績で人を選ばない")
    lines.append("- ⚠ 併記の 5 列は診断（順位に使わない）: DSR ＝ 日次純利系列の SR をいまの n_trials で割り引いた確率（SR > 0 の検定であって B&H を超えたかの検定ではない）／"
                 " 日次 t ＝ 対 B&H の日次差の t ／ 最大 fold 外し ＝ 上乗せが最大の fold を外した平均（1 fold が作った上乗せに気づくため）／ 取引/fold・保有日率 ＝ 実行の記録の平均")
    lines.append("")
    lines.append("| " + " | ".join(head) + " |")
    lines.append("| ---: | --- | --- | --- | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: | ---: |")
    rows = res["ranked"] if all_rows else res["top"]
    for i, r in enumerate(rows, 1):
        lines.append("| " + " | ".join(_row_cells(i, r)) + " |")
    lines.append("")
    return "\n".join(lines)


def to_json(res: dict) -> dict:
    def one(r: Row) -> dict:
        return {"name": r.name, "model": r.model, "method": r.method, "theta": r.theta, "verdict": r.verdict, "closed": r.closed,
                "fold": r.fold, "positive": r.positive, "edge_bp": r.edge, "first_run": r.first_run, "runs": r.runs,
                "marks": r.marks, "diag": r.diag}
    return {k: v for k, v in res.items() if k not in ("ranked", "top")} | {"ranked": [one(r) for r in res["ranked"]],
                                                                            "top": [one(r) for r in res["top"]]}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="研究の DB（既定 runs/research.sqlite）。読み取り専用")
    ap.add_argument("--asof", default=None, help="YYYY-MM-DD。この日までに最初の実行があった行だけ（再現の検算）")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--all", action="store_true", help="決まり 4 まで通した全部を並べ順で出す")
    ap.add_argument("--no-diag", action="store_true", help="併記（実行の記録を読む）を省く")
    ap.add_argument("--json", default=None, help="結果を JSON で書く道")
    args = ap.parse_args(argv)
    conn = open_db(args.db)
    try:
        res = select(conn, asof=args.asof, top=args.top, diag=not args.no_diag, all_rows=args.all)
    finally:
        conn.close()
    print(render(res, all_rows=args.all))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(to_json(res), f, ensure_ascii=False, indent=1)
        print(f"wrote {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
