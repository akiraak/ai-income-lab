"""台帳の markdown を組み立てる。⚠ **中身は `ail/catalog.py` が作る。ここは並べるだけ。**

呼び出しは `python3 -m cli.report --catalog`。
"""

from __future__ import annotations

import time

from ail import catalog

DOC = "docs/specs/experiments/feature-discovery.md"
RULES = "rules.md"
GRAN_ORDER = ("日足", "1 分足")


def _bp(v: float | None, digits: int = 2) -> str:
    """⚠ **符号は spec と同じ全角で書く。** 取り違えると結論が反転する。"""
    if v is None:
        return "—"
    s = f"{abs(v):.{digits}f}"
    return f"＋{s}" if v > 0 else f"−{s}" if v < 0 else s


def _f(v: float | None, digits: int = 4) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def _table(head: list[str], align: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "| " + " | ".join(align) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out


def _sort_key(r: dict):
    g = GRAN_ORDER.index(r["粒度"]) if r["粒度"] in GRAN_ORDER else len(GRAN_ORDER)
    net = r["純利bp"] if r["純利bp"] is not None else -1e9
    return (g, -net)


def build() -> str:
    d = catalog.ledger()
    rows = sorted(d["rows"], key=_sort_key)
    # ⚠ **試行の行 = カタログ ID の行 ＋ モデルが処置の行**（catalog.is_trial が正本）
    methods = [r for r in rows if catalog.is_trial(r)]
    counts = {k: sum(1 for r in methods if r["判定"] == k) for k in ("採る", "落とす", "保留")}
    fam = {}
    for c in d["catalog"]:
        fam.setdefault(c["系統"], []).append(c)
    tried_ids = sorted({r["ID"] for r in methods if r["ID"]})

    L: list[str] = []
    a = L.append

    a("# 試した分析手法の台帳")
    a("")
    a("⚠ **このファイルは生成物である。手で書き換えない。**")
    a("")
    a("```bash")
    a("cd experiments/feature-discovery")
    a("./.venv/bin/python -m cli.report --catalog \\")
    a("  > ../../docs/specs/experiments/feature-discovery/ledger.md")
    a("```")
    a("")
    a(f"生成日 {time.strftime('%Y-%m-%d')} ／ 入口: [feature-discovery.md](../feature-discovery.md) "
      f"／ 規約: [rules.md]({RULES}) ／ 手法のカタログ: "
      f"[feature-discovery.md §2](../feature-discovery.md#2-特徴量を見つけ出す手法のカタログphase-25-系統-25-件)")
    a("")
    a("> ⚠ **これは投資助言ではなく調査資料である。** 特定の銘柄・売買を推奨しない。")
    a("> ⚠ **数字はすべて【実測】**（自前の検証で出したもの）。⚠ **良い数字は根拠「中」が上限**で、"
      "デフレーテッド SR とパージ CV を通していないものは報告しない（[rules.md 11 章](rules.md)）。")
    a("")
    a("> この図の主張: ⚠ **台帳は 3 つの正本を突き合わせた生成物である。** 手で書き写す欄はひとつも無い。")
    a("")
    a("```mermaid")
    a("flowchart LR")
    a('  C["spec §2 の表<br/>カタログ 25 件"] --> J["cli/report.py --catalog"]')
    a('  G["ail/registry.py<br/>実装の正本"] --> J')
    a('  R["runs/*/summary.csv<br/>結果の正本"] --> J')
    a('  N["config/catalog_notes.toml<br/>⚠ 人が書く唯一の列"] --> J')
    a('  J --> L["ledger.md<br/>⚠ この文書"]')
    a("```")
    a("")

    # --- 0. 一言 ---
    a("## 0. ここまでの一言")
    a("")
    a(f"⚠ **カタログ {len(d['catalog'])} 件のうち、実装したのは {len(d['implemented'])} 件、"
      f"実際に回したのは {len(tried_ids)} 件。** 残り {len(d['not_tried'])} 件は**未実施**である（§3）。")
    a("")
    held = [r for r in methods if r["判定"] == "保留"]
    invalid = [r for r in held if "無効" in (r["理由"] or "")]
    a(f"⚠ **試行は {len(methods)} 行（手法）＋ {len(rows) - len(methods)} 行（基準線）。**"
      f" ⚠ **「採る」は {counts['採る']} 件。** 落とす {counts['落とす']} 行 ／ "
      f"保留 {counts['保留']} 行。")
    if held:
        a("")
        a(f"⚠ **保留の内訳**: 無効・要再測（日足 × `raw`）が {len(invalid)} 行 ／ "
          f"⚠ **純利は正だが fold の符号が割れる**ものが {len(held) - len(invalid)} 行。"
          + ("⚠ **後者は「採る」の一歩手前ではない。** ⚠ **多重検定を通していない良い数字である**"
             "（[rules.md 11 章](rules.md) 規約 2・3）。" if len(held) - len(invalid) else ""))
    a("")
    a("| 何を聞かれたら | この台帳のどこで答えるか |")
    a("| --- | --- |")
    a("| その手法は試したか | §2 に行があるか。無ければ §3 に「未実施」で載っている |")
    a("| なぜ落としたか | §2 の「判定」と「理由」（⚠ **落ちた理由は E8 の失敗の型 X1〜X12 で書く**） |")
    a("| その数字はどの実行のものか | §2 の「実行」→ §4 の一覧（層・行数・種・commit・入力の指紋） |")
    a("| ⚠ **次に何を試すか** | §3。⚠ **手間の小さい順に並べてあり、上から読めば決まる** |")
    a("")
    a(f"⚠ **多重検定に使う `n_trials` は {len(methods)}**（= 試行の行数。基準線 "
      f"{len(rows) - len(methods)} 行は数えない。⚠ **「全部使う × Ridge 以外のモデル」は"
      "モデルが処置なので試行として数える** — [plans/archive/gpu-models.md §3-4](../../../plans/archive/gpu-models.md)）。"
      "⚠ **[rules.md 11 章](rules.md) の規約 4 は"
      "「手法 × 粒度 × 地平 × 銘柄集合」を全部数えることを求める。この数を数え落とすと必ず甘くなる。**")
    a("")

    # --- 1. 読み方 ---
    a("## 1. 台帳の読み方")
    a("")
    a("単位（実行 → 手法 → 試行 → fold → セル）の関係は [units.md](units.md) に図解がある（初見はそちらを先に読む）。")
    a("")
    a("⚠ **1 行 = 1 回の試行。** 鍵は **手法 × モデル × 粒度 × 地平 × 特徴量の層 × データの層 × "
      "検証方式 × 形式 × 閾値** である"
      "（[利用者の指示](../../../plans/archive/feature-discovery-ledger.md)。"
      "⚠ **モデルは 2026-09-09 に鍵へ足した** — それまでは Ridge 1 本。"
      "⚠ **検証方式・形式・閾値は 2026-09-10 に足した** — 旧実行は（毎日往復・共通・—）として読むので"
      "既存の行は割れない。[rules.md 13-9](rules.md)）。")
    a("⚠ **手法ごとに 1 行にすると潰れる。** 同じ手法を条件違いで何度も試すためである。")
    a("")
    a("| 列 | 中身 | ⚠ 読むときの注意 |")
    a("| --- | --- | --- |")
    a("| 実装 | `ail/registry.py` に在るか | ⚠ **無いものは「効かない」ではなく「試していない」** |")
    a("| 層 | データの層（`raw` / `adjusted`） | ⚠ **日足の `raw` は分割調整の誤りを含む＝ 無効**（[§6-3](../feature-discovery.md)） |")
    a("| 本数 | 選んだ特徴量の本数（fold の平均） | 絞るほど良いとは限らない |")
    a("| 的中率 | 符号が当たった割合 | ⚠ **株は上がる日が多い。** 基準「常に上」と必ず比べる |")
    a("| IC | 予測とラベルの相関 | ⚠ **IC が正でも粗利が最下位のことがある**（[§3-3](../feature-discovery.md)） |")
    a("| 粗利 bp | コストを引く前の 1 往復 | ⚠ **これだけを見てはいけない** |")
    a("| ⚠ **純利 bp** | ⚠ **往復コストを引いた後** | ⚠ **正でなければその手法は使えない**（[rules.md 9 章](rules.md) 規約 6） |")
    a("| fold | ⚠ **純利が正だった fold / 全 fold と符号の並び** | ⚠ **平均が正でも符号が割れるなら実力ではない**（[rules.md 11 章](rules.md) 規約 5） |")
    a("| 検証方式 | 毎日往復（旧）／ 閾値売買（[rules.md 13 章](rules.md)） | ⚠ **新旧の純利 bp は別の物差しで、直接比べない**（13-8）。閾値売買の純利は fold のポートフォリオ累計 |")
    a("| 形式 | 共通（63 銘柄で 1 モデル）／ 銘柄別（銘柄ごとに 1 本） | 閾値売買だけ。違うのは fit の範囲だけ（13-6） |")
    a("| 閾値 | θ（%）。買い% > θ で建て、売り% > θ で手仕舞う | ⚠ **1 水準 = 1 試行**（13-9）。3 水準とも載せる（良かった閾値だけ報告しない） |")
    a("| 再現 | 同じ鍵の実行が何本あり、一致したか | ⚠ **数字が動いたら、まず配線を疑う**（規約 7） |")
    a("| 実行 | 出所の実行 ID | §4 で層・種・commit・入力の指紋が引ける |")
    a("")
    a("> この図の主張: ⚠ **判定は数字から機械的に決める。** 手で「良さそう」とは書かない。")
    a("")
    a("```mermaid")
    a("flowchart TB")
    a('  S["1 行"] --> B{"基準線か"}')
    a('  B -->|"はい"| BS["基準<br/>⚠ 採否の対象ではない"]')
    a('  B -->|"いいえ"| I{"日足 × raw か"}')
    a('  I -->|"はい"| H1["保留<br/>⚠ 無効・要再測"]')
    a('  I -->|"いいえ"| N{"純利 > 0 か"}')
    a('  N -->|"いいえ・粗利 > 0"| D1["落とす<br/>X2 コストで消える"]')
    a('  N -->|"いいえ・粗利 ≤ 0"| D2["落とす<br/>X2 ＋ X9"]')
    a('  N -->|"はい"| F{"fold の符号が<br/>全部正か"}')
    a('  F -->|"はい"| A["採る<br/>⚠ 根拠は中が上限"]')
    a('  F -->|"いいえ"| H2["保留<br/>平均だけ正"]')
    a("```")
    a("")
    a("| 条件 | 判定 | 型・注記 |")
    a("| --- | --- | --- |")
    for cond, verdict, why in catalog.JUDGE_RULES:
        a(f"| {cond} | **{verdict}** | {why} |")
    a("| registry に無い | **未実施** | §3 |")
    a("")
    a("⚠ **失敗の型は E8 の台帳と同じもの**（[E8 §6](../e8-signal-methods/failures.md)）。"
      "**X2** コストで消える ／ **X9** 標本不足。")
    a("⚠ **1 分足の `raw` は保留にしない。** 目盛りの誤りは日足にしか無い"
      "（[§3 の注記](../feature-discovery.md)）。")
    a("")

    # --- 2. 台帳 ---
    a("## 2. 台帳（試した結果）")
    a("")
    a(f"⚠ **{len(rows)} 行。** うち手法 {len(methods)} 行・基準線 {len(rows) - len(methods)} 行。")
    a("")
    head = ["ID", "手法", "系統", "実装", "モデル", "層", "粒度", "地平", "特徴量の層",
            "検証方式", "形式", "閾値", "本数",
            "的中率", "IC", "粗利bp", "純利bp", "fold", "再現", "判定", "理由", "実行"]
    align = ["---", "---", "---", ":-:", ":-:", ":-:", "---", "---", ":-:", ":-:", ":-:", "---:",
             "---:", "---:", "---:", "---:", "---:", "---", "---", ":-:", "---", "---"]
    body = []
    for r in rows:
        verdict = f"**{r['判定']}**" if r["判定"] != "基準" else r["判定"]
        body.append([r["ID"] or "—", r["手法"], r["系統"], r["実装"],
                     r.get("モデル") or "—", r["層"], r["粒度"],
                     r["地平"], r["特徴量の層"] or "—",
                     r.get("検証方式") or "毎日往復", r.get("形式") or "共通", r.get("閾値") or "—",
                     _f(r["本数"], 1), _f(r["的中率"]),
                     _bp(r["IC"], 4) if r["IC"] is not None else "—",
                     _bp(r["粗利bp"]), f"**{_bp(r['純利bp'])}**", r["fold"] or "—",
                     r["再現"], verdict, r["理由"] or "—", f"`{r['実行']}`"])
    L += _table(head, align, body)
    a("")
    a("⚠ **「常に上（ドリフト）」の純利は割り引いて読む。** "
      "⚠ **ほとんど回転しないので、実際には往復コストを毎回払わない**（[rules.md 9 章](rules.md)）。")
    a("⚠ **fold が「—」の行は、fold ごとの生データが残っていない**（旧配線の `evaluate.py` は"
      "構造化のときに消えた）。⚠ **再測するまで埋まらない。**")
    a("⚠ **検証方式が「閾値売買」の行の fold は、対 B&H 上乗せの符号**（rules.md 13-7。"
      "純利の符号では「買って持っただけ」と区別できない）。"
      "⚠ **新旧の純利 bp は別の物差しで、直接比べない**（rules.md 13-8）。")
    a("")

    # --- 3. 未実施 ---
    a(f"## 3. まだ試していない手法（{len(d['not_tried'])} 件）")
    a("")
    skipped = [c for c in d["not_tried"] if c["判定"].endswith("見送り")]
    a("⚠ **空白にしない。** ⚠ **「効かなかった」のではなく「まだ回していない」ことを明示する行である。**")
    a("")
    a(f"⚠ **手間の小さい順に並べてある**（小 → 中 → 大 → 見送り。同じ手間なら根拠の強い順）。"
      f"⚠ **{len(d['not_tried']) - len(skipped)} 件は「まだ試していない」、"
      f"{len(skipped)} 件は「試さないと決めた」**（⚠ **理由は「次の一手」に書いてある**）。")
    a("")
    nt_head = ["ID", "手法", "系統", "実装", "拠", "⚠ 手間", "状態", "⚠ 次の一手（何が要るか）"]
    nt_align = ["---", "---", "---", ":-:", ":-:", ":-:", ":-:", "---"]
    L += _table(nt_head, nt_align,
                [[c["ID"], c["手法"], c["系統"], c["実装"], c["拠"], f"**{c['手間']}**",
                  f"**{c['判定']}**", c["次の一手"] or "—"] for c in d["not_tried"]])
    a("")
    a("⚠ **「次の一手」だけは人が書く**（`config/catalog_notes.toml`）。⚠ **1 か所に閉じてある。**")
    a("")
    a("**系統ごとの進み具合**")
    a("")
    prog = []
    for f, cs in sorted(fam.items()):
        impl = sum(1 for c in cs if c["ID"] in d["implemented"])
        tried = sum(1 for c in cs if c["ID"] in tried_ids)
        prog.append([f"{f} {cs[0]['系統名']}", str(len(cs)), str(impl), str(tried),
                     str(len(cs) - tried)])
    L += _table(["系統", "カタログ", "実装", "回した", "⚠ 未実施"],
                ["---", "---:", "---:", "---:", "---:"], prog)
    a("")
    a("⚠ **F4（生成型）と F5（表現学習型）は 1 件も触っていない。** "
      "⚠ **「特徴量を選ぶ」手法しか試しておらず、「特徴量を作る」側は空である。**")
    a("")

    # --- 4. 実行の一覧 ---
    a("## 4. 実行の一覧（出所）")
    a("")
    a("⚠ **数字が動いたら、まず入力の指紋と種と commit を見る**（[rules.md 11 章](rules.md) 規約 7）。")
    a("")
    L += _table(["実行", "出所", "粒度", "モデル", "層", "行", "特徴量", "銘柄", "種", "commit",
                 "入力の指紋", "先読み"],
                ["---", "---", "---", ":-:", ":-:", "---:", "---:", "---:", ":-:", "---", "---", ":-:"],
                [[f"`{r['実行']}`", r["出所"], r["粒度"] or "—", r.get("モデル") or "—",
                  r["層"] or "—",
                  f"{r['行']:,}" if r.get("行") else "—", str(r.get("特徴量") or "—"),
                  str(r.get("銘柄") or "—"), str(r.get("種") if r.get("種") is not None else "—"),
                  f"`{r['commit']}`" if r.get("commit") else "—",
                  f"`{r['指紋']}`" if r.get("指紋") else "—", r["先読み"]]
                 for r in d["runs"]])
    a("")
    a("⚠ **`runs/` は git 管理外である。** ⚠ **この台帳を同じ内容で再生成できるのは、"
      "その実行を持っている手元だけ**である（clone しただけの環境では旧配線の行しか出ない）。")
    a("")

    # --- 5. 先読みの検査 ---
    a("## 5. ⚠ 配線の検査（台帳の対象外）")
    a("")
    a("⚠ **わざと未来の値を混ぜた実行は、台帳の本体に入れない。** "
      "⚠ **的中率 99% の行が混ざると、台帳全体が嘘になる。**")
    a("⚠ **ここが跳ね上がらなければ、§2 の「効かない」は「配線が壊れていて測れていない」と読むべきである**"
      "（[rules.md 7 章](rules.md)）。")
    a("")
    if d["leak"]:
        L += _table(["ID", "手法", "モデル", "層", "粒度", "的中率", "IC", "粗利bp", "純利bp", "実行"],
                    ["---", "---", ":-:", ":-:", "---", "---:", "---:", "---:", "---:", "---"],
                    [[r["ID"] or "—", r["手法"], r.get("モデル") or "—", r["層"], r["粒度"],
                      _f(r["的中率"]),
                      _bp(r["IC"], 4), _bp(r["粗利bp"]), _bp(r["純利bp"]), f"`{r['実行']}`"]
                     for r in sorted(d["leak"], key=_sort_key)])
        # ⚠ **拾えなかった手法があること自体が結果である**（spec §3-2 の副産物）
        missed = [r for r in d["leak"] if (r["的中率"] or 0) < 0.9]
        a("")
        if missed:
            a("⚠ **先読みの列を拾わなかった手法がある**: "
              + " ／ ".join(f"**{r['ID'] or '—'} {r['手法']}**（的中率 {r['的中率']:.4f}）"
                            for r in sorted(missed, key=lambda x: -(x["的中率"] or 0))
                            if r["ID"])
              + "。⚠ **単独で圧倒的に効く 1 本を落とす設計だということである**"
                "（[§3-2](../feature-discovery.md)）。")
    else:
        a("⚠ **先読みの検査の実行が `runs/` に無い。** `cli.run --leak` を回す。")
    a("")

    # --- 6. 限界 ---
    a("## 6. ⚠ この台帳で埋まらないもの")
    a("")
    a("| # | 限界 | ⚠ 効き方 |")
    a("| ---: | --- | --- |")
    a("| 1 | ⚠ **1 分足（旧配線）の fold ごとの符号は出せない** | 生データが残っていない。"
      "⚠ **「平均は負」しか言えず、「毎回負け」とは言えない** |")
    a("| 2 | ⚠ **判定は数字だけで決める** | ⚠ **標本不足（X9）と「本当に効かない」を分けられない**"
      "（[§3-4 の限界 1](../feature-discovery.md)） |")
    a(f"| 3 | ⚠ **「未実施」は「効かない」ではない** | ⚠ **{len(d['not_tried'])} 件は情報が無いだけである。**"
      f"⚠ **落とした {counts['落とす']} 行と混同しない** |")
    a("| 4 | ⚠ **デフレーテッド SR を 1 件も通していない** | 今は「採る」が 0 件なので不要。"
      "⚠ **正の純利が 1 行でも出たら、その時点で必須になる**（`ail/validation/stats.py`） |")
    a("| 5 | 古い実行の「層」は自己申告 | `cli/build.py` の sidecar は**それ以降の実行にしか効かない** |")
    a("")
    return "\n".join(L) + "\n"
