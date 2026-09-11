"""⚠ **試した結果の台帳を「生成する」ための突き合わせ層。**

台帳は 3 つの正本を突き合わせた**生成物**である。⚠ **どれも手で書き写さない。**

| 正本 | 何の正本か | ここでの読み方 |
| --- | --- | --- |
| `docs/.../feature-discovery.md` §2 | ⚠ **手法のカタログ 25 件** | 表から `F1-1` などの ID を読む |
| `ail/registry.py` | ⚠ **実装の有無** | 選別手法の名前の先頭から ID を読む |
| `runs/<実行>/summary.csv` | ⚠ **結果** | 実行ごとに読む（fold の符号は `result.csv`） |

⚠ **旧配線（`evaluate.py`）の結果は `runs/` に無い。** spec の表をその場で読む
（どこを読むかと、その表を出した条件は `config/legacy.toml` に 1 か所だけ宣言する）。

    from ail import catalog
    catalog.entries()      # カタログ 25 件
    catalog.trials()       # 1 行 = 手法 × 粒度 × 地平 × 特徴量の層 × データの層
"""

from __future__ import annotations

import json
import os
import re
import tomllib

import pandas as pd

from ail import runs

ROOT = runs.ROOT
DOCS = os.path.abspath(os.path.join(ROOT, "..", "..", "docs"))
SPEC = os.path.join(DOCS, "specs", "experiments", "feature-discovery.md")
CONFIG = os.path.join(ROOT, "config")

# 系統の見出し（spec §2 の `### F1. フィルタ型 — …（7）`）から読む
_FAMILY_HEAD = re.compile(r"^###\s+(F\d)\.\s*([^—\-]+?)\s*[—\-]\s*.*?（(\d+)）\s*$")
_ID = re.compile(r"^(F\d-\d+)\b")

# ⚠ **spec の表は全角の記号を使う。** 取り違えると符号が反転する
_SIGNS = {"＋": "+", "−": "-", "－": "-", "▲": "-", ",": ""}


def _clean(cell: str) -> str:
    """`**強調**`・`⚠`・前後の空白を落とす。"""
    return re.sub(r"\*\*|⚠", "", cell).strip()


def _num(cell: str) -> float | None:
    """全角の符号を直して数にする。⚠ **`—` は「測っていない」で、0 ではない。**"""
    s = _clean(cell)
    for a, b in _SIGNS.items():
        s = s.replace(a, b)
    if s in ("", "—", "-", "–"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _rows_of_table(lines: list[str], start: int) -> list[list[str]]:
    """`start` 行目以降の最初の markdown 表を、セルの一覧にして返す。"""
    i = start
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        i += 1
    out = []
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        cells = [c for c in lines[i].strip().strip("|").split("|")]
        if not set("".join(cells).strip()) <= set("-: "):        # 区切りの行は捨てる
            out.append(cells)
        i += 1
    return out


# --- カタログ（spec §2）--------------------------------------------------

def entries(spec: str = SPEC) -> list[dict]:
    """spec §2 の 5 系統 25 件。⚠ **写しは作らない。表をその場で読む。**"""
    lines = open(spec, encoding="utf-8").read().splitlines()
    out: list[dict] = []
    family = family_name = None
    for line in lines:
        m = _FAMILY_HEAD.match(line)
        if m:
            family, family_name = m.group(1), m.group(2).strip()
            continue
        # ⚠ **系統の見出しから次の見出しまでが表の範囲。** ここで閉じないと、
        # ⚠ **§3-3 や §4-4 の結果の表に混ざった `F1-5` の行まで拾う**（2026-09-08 に踏んだ）
        if line.startswith("#"):
            family = family_name = None
            continue
        if not line.lstrip().startswith("|") or family is None:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = _ID.match(_clean(cells[0]))
        if not m or len(cells) < 7:
            continue
        out.append({"ID": m.group(1), "手法": _clean(cells[1]), "系統": family,
                    "系統名": family_name, "量": _clean(cells[2]), "相関": _clean(cells[3]),
                    "検定": _clean(cells[4]), "拠": _clean(cells[5])})

    # ⚠ **書式が変われば黙って減る。** 見出しが宣言している件数と突き合わせて、合わなければ止める
    declared = family_counts(spec)
    got: dict[str, int] = {}
    for e in out:
        got[e["系統"]] = got.get(e["系統"], 0) + 1
    if got != declared:
        raise SystemExit(f"⚠ カタログの件数が見出しと合わない: 読めた {got} / 見出し {declared}"
                         f"（{os.path.relpath(spec, ROOT)} §2 の表の書式を確かめる）")
    if len({e["ID"] for e in out}) != len(out):
        raise SystemExit(f"⚠ カタログの ID が重複している（{len(out)} 行）")
    return out


def family_counts(spec: str = SPEC) -> dict[str, int]:
    """見出しが宣言している件数（`### F1. … （7）`）。⚠ **実際に読めた件数と突き合わせる。**"""
    out = {}
    for line in open(spec, encoding="utf-8").read().splitlines():
        m = _FAMILY_HEAD.match(line)
        if m:
            out[m.group(1)] = int(m.group(3))
    return out


# --- 実装（registry）----------------------------------------------------

def implemented() -> dict[str, str]:
    """カタログの ID → registry の名前。⚠ **名前の先頭の ID で突き合わせる。**"""
    from ail import registry
    import ail.bootstrap  # noqa: F401  （@register は import されて初めて効く）

    out = {}
    for name in registry.available("selector"):
        m = _ID.match(name)
        if m:
            out[m.group(1)] = name
    return out


def baseline_names() -> set[str]:
    """ID を持たない＝ カタログの手法ではない行（基準線）。"""
    from ail import registry
    import ail.bootstrap  # noqa: F401

    names = registry.available("selector") + registry.available("model")
    return {n for n in names if not _ID.match(n)}


# --- 試行（runs/ と 旧配線）---------------------------------------------

def _granularity(config: dict) -> tuple[str, float]:
    """粒度の表示名と 1 本の分数。⚠ **`bar_minutes` を正とし、無ければ dataset 名から引く。**"""
    m = config.get("bar_minutes")
    if not m:
        m = {"daily": 1440.0, "min1": 1.0}.get(config.get("dataset", ""), 0.0)
    return ({1440.0: "日足", 1.0: "1 分足"}.get(float(m), f"{m:g} 分足"), float(m))


def _horizon(horizon: float, bar_minutes: float) -> str:
    """⚠ **「390 本先」は 1 分足では 1 取引日**（立会は 390 分）。本数だけだと粒度と混ざる。"""
    total = horizon * bar_minutes
    if not total:
        return f"{horizon:g} 本"
    if total >= 1440:
        unit = f"{total / 1440:g} 日"
    elif total >= 390 and total % 390 == 0:
        unit = f"{total / 390:g} 取引日"
    else:
        unit = f"{total:g} 分"
    return f"{horizon:g} 本（{unit}）"


def _sign_pattern(result: pd.DataFrame | None, method: str) -> str | None:
    """⚠ **平均が正でも「5 fold 中 2 回は符号が逆」なら実力ではない**（rules.md 11 章 規約 5）。"""
    if result is None or "手法" not in result:
        return None
    r = result[result["手法"] == method].sort_values("fold")
    if r.empty:
        return None
    pat = "".join("＋" if v > 0 else "−" if v < 0 else "0" for v in r["純利bp"])
    return f"{int((r['純利bp'] > 0).sum())}/{len(r)} {pat}"


def _read_run(name: str) -> dict | None:
    d = os.path.join(runs.RUNS, name)
    s = os.path.join(d, "summary.csv")
    c = os.path.join(d, "config.json")
    if not (os.path.exists(s) and os.path.exists(c)):
        return None
    doc = {"実行": name, "leak": name.endswith("_leak")}
    for f in ("config", "inputs", "env"):
        p = os.path.join(d, f + ".json")
        doc[f] = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    doc["summary"] = pd.read_csv(s, index_col=0)
    r = os.path.join(d, "result.csv")
    doc["result"] = pd.read_csv(r) if os.path.exists(r) else None
    return doc


def _layer_of(run: dict) -> str:
    """データの層。⚠ **sidecar（`cli.build` が書く）を正とし、無ければ自己申告を使う。**"""
    inputs = run.get("inputs", {})
    meta = inputs.get("features_meta") or {}
    return meta.get("layer") or inputs.get("layer") or "?"


def _trading_of(cfg: dict) -> tuple[str, str]:
    """(検証方式, 形式)。⚠ **旧実行は（毎日往復・共通）として読む**（rules.md 13-9 の 1）。"""
    t = cfg.get("trading") or {}
    if t.get("style") == "threshold":
        return "閾値売買", ("銘柄別" if t.get("form") == "per_symbol" else "共通")
    return "毎日往復", "共通"


def _edge_vs_bh(result: pd.DataFrame | None, method: str, th) -> tuple[float | None, str | None]:
    """(対 B&H 上乗せの平均 bp, fold の符号)。⚠ **fold の符号は上乗せで見る**（rules.md 13-7）。"""
    if result is None or "閾値" not in result or th is None:
        return None, None
    r = result[(result["手法"] == method) & (result["閾値"] == float(th))].sort_values("fold")
    b = result[(result["手法"] == "基準 常に上（ドリフト）")
               & (result["閾値"] == float(th))].sort_values("fold")
    if r.empty or b.empty or len(r) != len(b):
        return None, None
    e = r["純利bp"].values - b["純利bp"].values
    pat = "".join("＋" if v > 0 else "−" if v < 0 else "0" for v in e)
    return float(e.mean()), f"{int((e > 0).sum())}/{len(e)} {pat}"


def _run_trials(run: dict) -> list[dict]:
    cfg, inputs = run["config"], run.get("inputs", {})
    gran, bar_min = _granularity(cfg)
    layer = _layer_of(run)
    model = cfg.get("model", "Ridge")
    style, form = _trading_of(cfg)
    rows = []
    for method, s in run["summary"].iterrows():
        th = s.get("閾値") if style == "閾値売買" else None
        row = {
            "手法名": str(method), "粒度": gran, "地平": _horizon(cfg.get("horizon", 0), bar_min),
            # ⚠ **「基準 」の行はモデルを使わない**（常に上・直前符号）。モデル別に割れないよう「—」
            "モデル": "—" if str(method).startswith("基準 ") else model,
            "層": layer, "特徴量の層": " ".join(cfg.get("feature_layers", [])),
            "対象": cfg.get("targets") or "all", "k": cfg.get("k"),
            "コストbp": cfg.get("cost_bp"), "本数": s.get("本数"), "的中率": s.get("的中率"),
            "IC": s.get("IC"), "粗利bp": s.get("粗利bp"), "純利bp": s.get("純利bp"),
            "fold": None if style == "閾値売買" else _sign_pattern(run.get("result"), str(method)),
            "検証方式": style, "形式": form,
            "閾値": f"{float(th):g}" if th is not None else "—",
            "実行": run["実行"], "leak": run["leak"], "行": inputs.get("rows_before_sample"),
            "出所": "runs",
        }
        if style == "閾値売買":
            # ⚠ 閾値ごとに行が割れるので、fold の符号も閾値ごとの上乗せで引き直す
            edge, pat = _edge_vs_bh(run.get("result"), str(method), th)
            row["上乗せbp"], row["上乗せfold"], row["fold"] = edge, pat, pat
        rows.append(row)
    return rows


def legacy_tables(path: str | None = None) -> list[dict]:
    p = path or os.path.join(CONFIG, "legacy.toml")
    if not os.path.exists(p):
        return []
    with open(p, "rb") as f:
        return tomllib.load(f).get("table", [])


def legacy_trials(decl: dict) -> list[dict]:
    """⚠ **spec の表をその場で読む。** 数字を `config/` に写さない。"""
    spec = decl.get("spec")
    spec = SPEC if not spec else spec if os.path.isabs(spec) else os.path.join(ROOT, "..", "..", spec)
    lines = open(os.path.abspath(spec), encoding="utf-8").read().splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith(decl["heading"])), None)
    if start is None:
        raise SystemExit(f"{decl['id']}: 見出し {decl['heading']!r} が {spec} に無い")
    table = _rows_of_table(lines, start)
    if not table:
        raise SystemExit(f"{decl['id']}: {decl['heading']!r} の後ろに表が無い")
    head = [_clean(c) for c in table[0]]

    def col(*keys) -> int | None:
        for i, h in enumerate(head):
            if any(k in h for k in keys):
                return i
        return None

    ix = {k: col(*v) for k, v in {"手法": ("手法",), "本数": ("本数",), "的中率": ("的中率",),
                                  "IC": ("IC",), "粗利": ("粗利",), "純利": ("純利",)}.items()}
    if ix["手法"] is None or ix["純利"] is None:
        raise SystemExit(f"{decl['id']}: 表の見出しを読めない: {head}")
    bar_min = float(decl.get("bar_minutes", 0.0))
    rows = []
    for cells in table[1:]:
        name = _clean(cells[ix["手法"]])
        if not name:
            continue
        rows.append({
            "手法名": name, "粒度": decl.get("granularity", "?"),
            "地平": _horizon(float(decl.get("horizon", 0)), bar_min),
            "モデル": "—" if name.startswith("基準") else decl.get("model", "Ridge"),
            "層": decl.get("layer", "?"), "特徴量の層": " ".join(decl.get("feature_layers", [])),
            "対象": decl.get("targets", "all"), "k": decl.get("k"),
            "コストbp": decl.get("cost_bp"),
            **{k: (_num(cells[ix[k]]) if ix[k] is not None and ix[k] < len(cells) else None)
               for k in ("本数", "的中率", "IC")},
            "粗利bp": _num(cells[ix["粗利"]]) if ix["粗利"] is not None else None,
            "純利bp": _num(cells[ix["純利"]]),
            "fold": None, "検証方式": "毎日往復", "形式": "共通", "閾値": "—",
            "実行": decl["id"], "leak": False, "行": decl.get("rows"),
            "出所": "legacy",
        })
    return rows


# --- 判定 ---------------------------------------------------------------

# ⚠ **判定は数字から機械的に決める**（プラン §2-4）。落とした理由は E8 の失敗の型で書く
# ⚠ **閾値売買の行は対 B&H の上乗せで判定する**（rules.md 13-7。純利の符号では
# 「買って持っただけ」と区別できない）
JUDGE_RULES = [
    ("純利 > 0 かつ fold の符号が全部正", "採る", "—"),
    ("純利 > 0 だが fold の符号が割れる", "保留", "⚠ 平均だけ正（rules.md 11 章 規約 5）"),
    ("純利 ≤ 0 かつ 粗利 > 0", "落とす", "**X2** コストで消える"),
    ("純利 ≤ 0 かつ 粗利 ≤ 0", "落とす", "**X2 ＋ X9** コスト以前に優位性が無い"),
    ("⚠ 日足 × データの層が raw", "保留", "⚠ **無効・要再測**（分割調整の誤り。§6-3）"),
    ("基準線の行", "基準", "⚠ 採否の対象ではない。手法はこれを超えて初めて意味がある"),
    ("閾値売買: 上乗せ > 0 かつ fold の上乗せ符号が全部正", "採る",
     "⚠ DSR を通すまでは根拠「中」が上限（rules.md 13-7）"),
    ("閾値売買: 上乗せ > 0 だが符号が割れる", "保留", "⚠ 平均だけ正（rules.md 13-7）"),
    ("閾値売買: 上乗せ ≤ 0", "落とす", "⚠ **基準線以下 ＝ 何も学んでいない**（rules.md 13-7）"),
]


def judge(row: dict, baselines: set[str]) -> tuple[str, str]:
    """(判定, 理由)。⚠ **3 値（採る / 落とす / 保留）＋ 基準線。**

    ⚠ **「全部使う × Ridge 以外のモデル」は基準線ではなく手法として判定する**
    （モデルが処置。plans/archive/gpu-models.md §3-4。乱択・「基準 」の行は従来どおり基準線）。
    ⚠ **閾値売買の行は別の表**（rules.md 13-7）: 判定の量が「対 B&H の上乗せ」に変わり、
    「全部使う」も手法として判定する（検証方式が処置。13-9 の 4）。
    """
    invalid = row.get("粒度") == "日足" and row.get("層") == "raw"
    note = "⚠ **無効・要再測**（分割調整の誤り。§6-3）" if invalid else ""
    if row.get("検証方式") == "閾値売買":
        return _judge_trading(row, note)
    model_treated = (row["手法名"] == "全部使う（基準）"
                     and (row.get("モデル") or "Ridge") not in ("Ridge", "—"))
    if (canonical(row["手法名"])[1] in baselines or row["手法名"].startswith("基準 ")) \
            and not model_treated:
        if "常に上" in row["手法名"]:
            note = (note + " ／ " if note else "") + "⚠ **ほとんど回転しない＝ 実際はコストを払わない**"
        return "基準", note or "⚠ 採否の対象ではない"
    if invalid:
        return "保留", note
    net, gross = row.get("純利bp"), row.get("粗利bp")
    if net is None:
        return "保留", "純利が読めない"
    if net > 0:
        fold = row.get("fold")
        if fold and fold.split("/")[0] == fold.split("/")[1].split(" ")[0]:
            return "採る", "⚠ デフレーテッド SR を通すまでは根拠「中」が上限（rules.md 11 章 規約 3）"
        return "保留", "⚠ 平均だけ正。fold の符号が割れる（rules.md 11 章 規約 5）"
    if gross is not None and gross > 0:
        return "落とす", "**X2** コストで消える"
    return "落とす", "**X2 ＋ X9** コスト以前に優位性が無い"


def _judge_trading(row: dict, note: str) -> tuple[str, str]:
    """閾値売買の判定（rules.md 13-7 の表）。⚠ **量は対 B&H の上乗せ**。"""
    name = row.get("手法名") or ""
    if name.startswith("基準 ") or name == "乱択（基準）":
        if "常に上" in name:
            note = (note + " ／ " if note else "") + "⚠ 新方式では期初買い・期末売り（B&H）。上乗せの基準"
        return "基準", note or "⚠ 採否の対象ではない"
    if row.get("粒度") == "日足" and row.get("層") == "raw":
        return "保留", note
    edge = row.get("上乗せbp")
    if edge is None:
        return "保留", "対 B&H の上乗せが読めない"
    fold = row.get("上乗せfold") or ""
    if edge > 0:
        if fold and fold.split("/")[0] == fold.split("/")[1].split(" ")[0]:
            return "採る", (f"対 B&H 上乗せ {edge:+.2f}bp。"
                            "⚠ DSR を通すまでは根拠「中」が上限（rules.md 13-7）")
        return "保留", f"⚠ 上乗せの平均だけ正（{edge:+.2f}bp）。fold の符号が割れる（rules.md 13-7）"
    return "落とす", (f"⚠ **基準線以下 ＝ 何も学んでいない**"
                      f"（対 B&H 上乗せ {edge:+.2f}bp ≤ 0。rules.md 13-7）")


# --- 台帳の行 -----------------------------------------------------------

KEY = ("鍵", "モデル", "粒度", "地平", "特徴量の層", "層",
       "検証方式", "形式", "閾値")   # ⚠ 利用者が決めた 1 行の粒度
# ⚠ **モデルは 2026-09-09 に鍵へ足した**（plans/archive/gpu-models.md §3-2）。それまでは Ridge 1 本だったので
# ⚠ **既存の行はどれも割れない**（旧実行はモデル未指定 = Ridge として読む）
# ⚠ **検証方式・形式・閾値は 2026-09-10 に足した**（rules.md 13-9 の 1）。旧実行・旧配線は
# ⚠ **（毎日往復・共通・—）として読む**ので、既存の行はどれも割れない


def is_trial(row: dict) -> bool:
    """台帳の 1 行を n_trials に数えるか。⚠ **数え落とすと DSR が必ず甘くなる**（rules.md 11 章 規約 4）。

    カタログ ID を持つ行（従来どおり）に加え、⚠ **モデルが処置の行**
    （「全部使う × Ridge 以外のモデル」）も数える（plans/archive/gpu-models.md §3-4）。
    乱択・「基準 」の行は従来どおり基準線として数えない。
    ⚠ **閾値売買の行は閾値 1 水準ごとに 1 試行**（rules.md 13-9 の 3・4）。検証方式が処置なので
    「全部使う × Ridge」も数える。基準線（B&H・直前符号・乱択）は数えない。
    """
    if row.get("検証方式") == "閾値売買":
        name = row.get("手法名") or ""
        return not (name.startswith("基準 ") or name == "乱択（基準）")
    if row.get("ID"):
        return True
    return (row.get("鍵") == "全部使う（基準）"
            and (row.get("モデル") or "Ridge") not in ("Ridge", "—"))


def canonical(name: str) -> tuple[str | None, str]:
    """(カタログの ID, 照合用の名前)。⚠ **名前で照合してはいけない。**

    ⚠ **旧配線の表と registry で表記が違う**（「F1-5 単変量検定 ＋ FDR」と「F1-5 検定+FDR」）。
    ⚠ **ID で寄せないと、同じ試行が 2 行に割れる**（2026-09-08 に踏んだ）。
    """
    m = _ID.match(name)
    if m:
        return m.group(1), m.group(1)
    return None, re.sub(r"^基準\s+", "", name).strip()


def trials() -> tuple[list[dict], list[dict], list[dict]]:
    """(台帳の行, ⚠ 先読みの検査の行, 実行の一覧) を返す。

    ⚠ **同じ鍵（手法 × 粒度 × 地平 × 特徴量の層 × データの層）の実行はまとめる。**
    まとめた本数と、⚠ **まとまりの中で数字が食い違っていないか**を出す。
    """
    all_rows: list[dict] = []
    run_list: list[dict] = []
    for name in runs.list_runs():
        run = _read_run(name)
        if run is None:
            continue
        cfg, inputs, env = run["config"], run.get("inputs", {}), run.get("env", {})
        gran, _ = _granularity(cfg)
        run_list.append({"実行": name, "粒度": gran, "モデル": cfg.get("model", "Ridge"),
                         "層": _layer_of(run),
                         "行": inputs.get("rows_before_sample"), "特徴量": inputs.get("features"),
                         "銘柄": inputs.get("symbols"), "種": env.get("seed"),
                         "commit": env.get("git_commit"),
                         "指紋": (inputs.get("data_manifest") or {}).get("raw"),
                         "先読み": "⚠ **あり**" if run["leak"] else "—", "出所": "runs/"})
        all_rows += _run_trials(run)
    for decl in legacy_tables():
        all_rows += legacy_trials(decl)
        run_list.append({"実行": decl["id"], "粒度": decl.get("granularity"),
                         "層": decl.get("layer"), "行": decl.get("rows"),
                         "特徴量": decl.get("features"), "銘柄": decl.get("symbols"),
                         "種": decl.get("seed"), "commit": None, "指紋": None, "先読み": "—",
                         "出所": f"⚠ 旧配線 §{decl['heading'].strip('# ').rstrip('.')}"})

    for r in all_rows:
        r["ID"], r["鍵"] = canonical(r["手法名"])
    leak = [r for r in all_rows if r["leak"]]
    return _collapse([r for r in all_rows if not r["leak"]]), _collapse(leak), run_list


def _collapse(rows: list[dict], tol: float = 0.1) -> list[dict]:
    """同じ鍵の行をまとめる。⚠ **食い違ったら黙って隠さず印を付ける**（rules.md 11 章 規約 7）。"""
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        # ⚠ 鍵の列が無い行（手書きの検査用など）は None で寄せる。実運用の行は必ず全列を持つ
        groups.setdefault(tuple(r.get(k) for k in KEY), []).append(r)
    out = []
    for key, g in groups.items():
        # ⚠ **代表は新配線の一番新しい実行。** 旧配線の表しか無いときだけそれを使う
        latest = next((x for x in reversed(g) if x["出所"] == "runs"), g[-1])
        nets = [x["純利bp"] for x in g if x["純利bp"] is not None]
        spread = (max(nets) - min(nets)) if len(nets) > 1 else 0.0
        merged = dict(latest)
        merged["実行数"] = len(g)
        merged["実行一覧"] = [x["実行"] for x in g]
        merged["再現"] = ("—" if len(g) == 1 else
                          f"{len(g)} 実行・一致" if spread <= tol else
                          f"⚠ **{len(g)} 実行・幅 {spread:.2f}bp**")
        # fold の符号は、持っている実行があればそれを使う
        merged["fold"] = next((x["fold"] for x in reversed(g) if x["fold"]), None)
        out.append(merged)
    return out


def ledger() -> dict:
    """台帳に要るものを全部そろえて返す。表示は `cli/report.py` の仕事。"""
    cat = entries()
    impl = implemented()
    bases = baseline_names()
    rows, leak, run_list = trials()

    by_id = {c["ID"]: c for c in cat}
    for r in rows + leak:
        c = by_id.get(r["ID"])
        r["手法"] = c["手法"] if c else r["鍵"]
        r["系統"] = f"{c['系統']} {c['系統名']}" if c else "基準線"
        r["実装"] = "✅" if (r["ID"] in impl or r["鍵"] in bases) else "⚠ 無"
        r["判定"], r["理由"] = judge(r, bases)

    tried = {r["ID"] for r in rows if r["ID"]}
    note = notes()
    not_tried = []
    for c in cat:
        if c["ID"] in tried:
            continue
        n = note.get(c["ID"], {})
        cost = n.get("cost", "？")
        not_tried.append({**c, "系統": f"{c['系統']} {c['系統名']}",
                          "実装": "✅" if c["ID"] in impl else "⚠ 無",
                          # ⚠ **「まだ試していない」と「試さないと決めた」を分ける。**
                          # ⚠ **どちらも「効かない」ではないが、読み手にとっては別の情報である**
                          "判定": "⚠ 見送り" if cost == "見送り" else "未実施",
                          "手間": cost, "次の一手": n.get("next", "")})
    # ⚠ **手間の小さい順。** 同じ手間なら根拠の強い順（強 → 中 → 弱）
    rank = {"強": 0, "中": 1, "弱": 2}
    not_tried.sort(key=lambda c: (COST_ORDER.index(c["手間"]) if c["手間"] in COST_ORDER else 9,
                                  rank.get(c["拠"], 9), c["ID"]))
    return {"catalog": cat, "implemented": impl, "rows": rows, "leak": leak,
            "runs": run_list, "not_tried": not_tried, "notes": note}


# ⚠ **未実施を並べる順。** 手間の小さいものから読めないと、台帳を見ても次の一手が決まらない
COST_ORDER = ("小", "中", "大", "見送り")


def notes(path: str | None = None) -> dict[str, dict]:
    """⚠ **「次に何が要るか」だけは機械では出せない。** 1 か所（`config/catalog_notes.toml`）に閉じる。"""
    p = path or os.path.join(CONFIG, "catalog_notes.toml")
    if not os.path.exists(p):
        return {}
    with open(p, "rb") as f:
        doc = tomllib.load(f)
    return {k: (v if isinstance(v, dict) else {"next": str(v)})
            for k, v in doc.get("method", {}).items()}
