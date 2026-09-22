"""vibeboard の「予測モデル」タブ（`dashboard/modelview.py`・`dashboard/models.toml`）の検査。仕様は dashboard.md §17。

見るもの: やさしい言葉・くらべる文が無い・設定の数字が無い（`[common]` も）／ 鍵（`id`）／ 試した結果の印と記録のファイル ／
「いま使っている」は実売買の設定から（本数を数えない）／ `show_result = false` で結果を出さない ／ TOML しか開かない ／ 経路 ／ 白地。
⚠ 特性の印（`basis`）とモデルの本文の検査は `test_traders_tab.py` が同じ `models.toml` に対して行う。
"""

import builtins
import dataclasses
import json
import re
import sqlite3
import threading
import tomllib

import pytest

import modelview
import traderview
import vibetab
import figures
from tests.test_traders_tab import COMPARING, FORBIDDEN, REAL_MODELS, _get, _texts, _trader
from tests.test_traders_tab import paths as _trader_paths  # noqa: F401（最小の置き場の fixture を借りる）

REPO_ROOT = traderview.REPO_ROOT


def _real() -> dict:
    return tomllib.loads(REAL_MODELS.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 本物の言葉の正本


def test_common_words_are_plain_and_do_not_compare():
    """`[common]` とモデルの本文（`result` も）。⚠ 正式な名前の欄は検査の外。"""
    texts = _texts(_real())
    hits = [(where, w) for where, text in texts for w in FORBIDDEN if w.lower() in text.lower()]
    assert not hits, f"やさしい言葉に言い換える: {hits}"
    hits = [(where, pat) for where, text in texts for pat in COMPARING if re.search(pat, text)]
    assert not hits, f"1 つのモデルだけを読んで分かる文にする: {hits}"
    bad = [(where, m.group(0)) for where, text in texts
           for m in re.finditer(r"\$\s?\d|(?<!\d)(?:63|48|55|45)(?!\d)|\d\s*bp", text)]
    assert not bad, f"実売買の設定の数字・細かい成績の数字を書かない: {bad}"


def test_ids_are_unique_and_url_safe():
    ids = [m.get("id") for m in _real()["model"]]
    assert all(i and modelview.ID_PATTERN.match(i) for i in ids), ids
    assert len(set(ids)) == len(ids) and modelview.LIST_ID not in ids


def test_every_model_has_words_result_and_files():
    """説明の欄がそろっている ／ 試した結果の印は 3 つのどれか ／ 記録と実験の設定のファイルが在る。"""
    paths = traderview.TraderPaths.default()
    for m in _real()["model"]:
        for key in ("label", "summary", "how", "formal", "traits", "sees"):
            assert m.get(key), (m["id"], key)
        result = m.get("result") or {}
        assert result.get("verdict") in modelview.VERDICTS and result.get("text"), m["id"]
        assert m.get("records"), m["id"]
        for rel in m["records"]:
            assert rel.startswith("docs/specs/") and (REPO_ROOT / rel).is_file(), (m["id"], rel)
        configs = modelview._configs(paths, m)
        assert configs, m["id"]
        for rel, keys in configs:
            assert (modelview.config_dir(paths) / rel).is_file() and keys, (m["id"], rel)


def test_deep_parts_are_well_formed():
    """図は箱 2〜12 個 ／ 組み立ては知っている軸だけ ／ 出力スコアの癖には印 ／ 経緯のいちばん良い印 ＝ 試した結果の印 ／
    「詳しく」は決まった大見出しにだけ ／ 小さな例があるなら「作りもの」の注が出る。"""
    doc = _real()
    common = doc["common"]
    for m in doc["model"]:
        assert 2 <= len(m.get("flow") or []) <= figures.MAX_NODES and all(x.get("t") for x in m["flow"]), m["id"]
        assert set(m.get("axes") or {}) <= set(modelview.AXES), m["id"]
        for t in (m.get("score") or {}).get("items") or []:
            assert t.get("basis") in traderview.BASES, (m["id"], t.get("text"))
            if t["basis"] == "guess":
                assert "見られる" in (t.get("why") or "") + t["text"], (m["id"], t["text"])
        if m.get("history"):
            assert modelview.best_verdict(m) == m["result"]["verdict"], m["id"]
        assert set(m.get("detail") or {}) <= set(modelview.DETAIL_PARTS), m["id"]
        if m.get("walk"):
            assert common.get("walk_caution") and all(x.get("text") for x in m["walk"]["steps"]), m["id"]
    for k in modelview.AXES:
        assert common.get("axis_" + k), k


def test_models_in_real_trader_configs_are_listed_as_live():
    """いま設定に居る人のモデルは全部「いま使っている」の束に出る（束は設定から。人が書かない）。"""
    paths = traderview.TraderPaths.default()
    data = modelview.load(paths)
    for key, _f, _ok in traderview.trader_files(paths):
        for spec in traderview.load_facts(paths, key)["models"]:
            hit = traderview.find_model(data["words"], spec)
            assert hit is not None and key in [u["key"] for u in data["users"][hit["id"]]], (key, spec)
    live = data["common"]["group_live"]
    groups = {i["id"]: i.get("group") for i in modelview.sidebar(paths)["items"]}
    assert all(groups[i] == live for i in data["users"]) and groups[modelview.LIST_ID] is None


# ---------------------------------------------------------------- 最小の置き場

MODELS = '''
[common]
show_result = true
list_title = "モデルの一覧"
list_lead = "一覧の前書き"
group_live = "いま使っている"
group_desk = "机上で試した"
card_sees = "見るもの"
card_decides = "決め方"
open_page = "モデルのページ"
used_by = "このモデルを使う人"
used_by_none = "使う人は居ない"
unsettled_mark = "未確定"
basis_build = "作りから"
basis_trial = "試し運転で見えた"
basis_guess = "見立て"
basis_note = "印の意味"
no_words = "説明はまだ"
result_adopt = "採る"
result_hold = "保留"
result_drop = "落とす"
result_none = "結果はまだ"
result_note = "結果の印の意味"
result_caution = "実際の売買の結果ではない"
records_label = "くわしい記録"
limit_survivor = "消えた会社が入っていない"
limit_costs = "そのとおりの値段で買えるとは限らない"
detail_label = "詳しく（用語あり）"
formal_label = "正式な名前と設定（用語あり）"
flow_claim = "共通の図の主張"
flow_link = "共通の流れ"
axes_title = "組み立て"
axis_data = "入力データ"
axis_prep = "下ごしらえ"
axis_calc = "計算の仕方"
axis_target = "当てにいく対象"
axis_scope = "学習範囲"
walk_title = "小さな例"
walk_caution = "例の数字は作りもの"
score_read = "読み方"
score_link = "出力スコアの使われ方"
history_title = "試した経緯"
history_old = "計算を直す前"
history_note = "経緯の注"
history_source_db = "記録から数えた"
history_source_hand = "人が写した"
history_hand = "写した数"
detail_db_note = "点線は記録から"
detail_hand_note = "囲みは控え"

[[model]]
id = "desk-one"
formal = ["Formal <One>"]
configs = ["experiment/exp_desk.toml", "../../etc/passwd.toml"]
label = "机上の型"
summary = "机上のひとこと"
traits = [{ text = "特性 D", why = "", basis = "build" }]
how = "答えの出し方 D"
stocks = false
result = { verdict = "drop", text = "どれも悪かった", why = "結果の理由" }
records = ["docs/specs/experiments/x.md", "experiments/readme.txt"]

[[model]]
id = "a-type"
name = "exp_a"
method = "手法 A"
formal = ["Formal A"]
label = "<b>形</b>を読む型"
summary = "モデル A のひとこと"
card = { sees = "札の見るもの", decides = "札の決め方" }
traits = [{ text = "特性 1", why = "理由 1", basis = "build" }, { text = "特性 3", why = "" }]
sees = [{ label = "まとまり 1", items = ["見るもの 1"] }]
how = "答えの出し方 A"
limits = ["苦手 A"]
result = { verdict = "hold", text = "割れた" }
flow = [{ t = "入れる" }, { t = "<計算>", s = "小さい字" }, { t = "出す", kind = "out" }]
axes = { data = "軸のデータ", calc = "軸の計算", other = "知らない軸" }
walk = { lead = "例の前書き", steps = [{ t = "段 1", text = "一段目" }, { text = "二段目" }, { t = "空" }], note = "例の後書き" }
score = { items = [{ text = "癖 1", why = "癖の理由", basis = "trial" }, { text = "癖 2" }], read = ["読み方 1"] }
history = [
  { when = "2026-09-01", what = "古い試し", hold = 1, drop = 2, old = true },
  { when = "2026-09-12", what = "直した試し", hold = 1, drop = 2, note = "経緯の理由", names = ["own.fixed.*"] },
  { when = "2026-09-19", what = "別の決まり", adopt = 1, aside = "ちがう決まり" },
]

[model.detail.about]
text = ["用語 Ridge の説明"]

[model.detail.how]
points = ["alpha = 1.0"]
links = [{ label = "記録へ", doc = "docs/specs/experiments/x.md" }]

[model.detail.score]
text = ["Platt の a {{calib|2026-01-01T00-00-00_x|方式 <A>|a|+2|＋1.00 ／ −2.00}}・門 {{gate|2026-01-01T00-00-00_x|方式 <A>|auc|3|0.500}}"]

[model.detail.result]
text = ["台帳の鍵"]

[[model]]
id = "bad id"
label = "鍵がおかしいモデル"

[[model]]
id = "no-result"
label = "結果の無い型"
result = { verdict = "great", text = "知らない印" }
'''


@pytest.fixture()
def paths(_trader_paths, tmp_path):
    _trader_paths.models.write_text(MODELS, encoding="utf-8")
    exp = tmp_path / "experiment"
    exp.mkdir()
    (exp / "exp_desk.toml").write_text('name = "exp_desk"\nmodel = "Formal"\nfeature_layers = ["own"]\ncost_bp = 5.0\n', encoding="utf-8")
    (exp / "exp_a.toml").write_text('name = "exp_a"\nmodel = "ModelOfA"\n', encoding="utf-8")
    return _trader_paths


def test_sidebar_groups_come_from_trader_config(paths):
    """「いま使っている」が先・束は設定から・本数を数えない・鍵のおかしいモデルは出さない。"""
    items = modelview.sidebar(paths)["items"]
    assert [(i["id"], i.get("group"), i.get("badge")) for i in items] == [
        ("all", None, None), ("a-type", "いま使っている", "保留"),
        ("desk-one", "机上で試した", "落とす"), ("no-result", "机上で試した", "")]
    assert not re.search(r"\d", json.dumps([i["label"] for i in items], ensure_ascii=False))
    # 使う人が居なくなれば、言葉の正本を触らなくても「机上で試した」へ移る
    (paths.traders_dir / "TA.toml").unlink()
    (paths.traders_dir / "candidates" / "notional" / "TB.toml").unlink()
    assert [i.get("group") for i in modelview.sidebar(paths)["items"][1:]] == ["机上で試した"] * 3


def test_list_page_is_cards_without_counts_or_ranking(paths):
    body = modelview.body(paths, "all")
    assert body.index("いま使っている") < body.index("&lt;b&gt;形&lt;/b&gt;を読む型") < body.index("机上で試した") < body.index("机上の型")
    assert "<a class='card' href='/#models/desk-one' target='_top'>" in body and "机上のひとこと" in body
    assert "<span class='tag drop'>落とす</span>" in body and "<span class='tag hold'>保留</span>" in body
    assert "<table" not in body and "位" not in body and not re.search(r"\d+\s*(本|個|種類)のモデル", body)


def test_model_page_sections_and_users(paths):
    body = modelview.body(paths, "a-type")
    heads = re.findall(r"<h2><span class='no'>(\d)</span>([^<]+)</h2>", body)
    assert heads == [("1", "どんなモデルか"), ("2", "モデルの特性"), ("3", "何を見て、どう答えを出すか"),
                     ("4", "出力スコアの出かたと読み方"), ("5", "過去のデータで試した結果"), ("6", "気をつけること")]
    # 使う人は設定から（呼び名つき・候補には「未確定」）。人のページへ飛べる
    assert "<a href='/#traders/TA' target='_top'>ナマエ（TA）</a>" in body
    assert "<a href='/#traders/TB' target='_top'>TB</a><span class='sub'>（未確定）</span>" in body
    assert "特性 1<span class='tag build'>作りから</span>" in body and "特性 3<span class='tag guess'>見立て</span>" in body
    assert "<span class='tag hold'>保留</span>割れた" in body and "結果の印の意味" in body
    assert "苦手 A" in body and "実際の売買の結果ではない" in body and "消えた会社が入っていない" in body
    # 正式な名前の段: 設定の綴り ＋ 実験の config から写した鍵（省けば experiment/<name>.toml）
    assert "<code>exp_a</code> <code>手法 A</code>" in body and "experiment/exp_a.toml" in body and "<code>ModelOfA</code>" in body
    assert "&lt;b&gt;形&lt;/b&gt;を読む型" in body and "<b>形</b>" not in body


def test_model_page_deep_parts(paths):
    """図・組み立て・小さな例・出力スコアの段・経緯の表・「詳しく」の囲み（⚠ 畳まない）。"""
    body = modelview.body(paths, "a-type")
    part = {h: body.split(f"</span>{h}</h2>")[1].split("<h2>")[0] for h in (
        "どんなモデルか", "何を見て、どう答えを出すか", "出力スコアの出かたと読み方", "過去のデータで試した結果")}
    # 1: 組み立ての表（知らない軸は出さない）＋ 詳しく
    assert "<tr><th>入力データ</th><td>軸のデータ</td></tr><tr><th>計算の仕方</th><td>軸の計算</td></tr>" in part["どんなモデルか"]
    assert "知らない軸" not in body and "用語 Ridge の説明" in part["どんなモデルか"]
    # 3: 図（主張が直前・箱の文字はエスケープ）→ 見るもの → 答えの出し方 → 小さな例 → 共通の流れへ → 詳しく
    how = part["何を見て、どう答えを出すか"]
    assert how.index("<p class='claim'>共通の図の主張</p><div class='fig'><svg") < how.index("まとまり 1") < how.index("答えの出し方 A")
    assert how.count("<g class='node'>") == 3 and "&lt;計算&gt;" in how and "<計算>" not in how
    assert how.index("答えの出し方 A") < how.index("<h3>小さな例</h3>") < how.index("<a href='/#models/build' target='_top'>共通の流れ</a>")
    assert "<li><b>段 1</b>　一段目</li><li>二段目</li></ol>" in how and "空" not in how and "例の数字は作りもの" in how
    assert "alpha = 1.0" in how and "<a href='/#specs/experiments/x.md' target='_top'>記録へ</a>" in how
    # 4: 癖（印つき。印が無ければ見立て）・読み方・使われ方へのリンク・詳しく
    score = part["出力スコアの出かたと読み方"]
    assert "癖 1<span class='tag trial'>試し運転で見えた</span><span class='why'>理由: 癖の理由</span>" in score
    assert "癖 2<span class='tag guess'>見立て</span>" in score and "読み方 1" in score and "Platt の a" in score
    assert "<a href='/#models/live' target='_top'>出力スコアの使われ方</a>" in score
    # 5: 経緯の表（古い試しは薄く・印ごとの内訳・合計）＋ 詳しく
    res = part["過去のデータで試した結果"]
    assert "<tr class='old'><td>2026-09-01</td><td>古い試し<span class='sub'>（計算を直す前）</span></td><td>3</td><td>保留 1 ／ 落とす 2</td></tr>" in res
    assert "<td>直した試し<span class='why'>経緯の理由</span></td><td>3</td>" in res and "台帳の鍵" in res
    assert "<tr class='old'><td>2026-09-19</td><td>別の決まり<span class='sub'>（ちがう決まり）</span></td><td>1</td><td>採る 1</td></tr>" in res
    assert modelview.best_verdict(modelview.load(paths)["models"][0]) == "hold"          # 薄い行（採る 1）は数えない
    # 「詳しく」も正式な名前も畳まない（<details> にしない）
    assert "<details" not in body and body.count("<div class='more-h'>詳しく（用語あり）</div>") == 4
    assert "<div class='more-h'>正式な名前と設定（用語あり）</div>" in body and "<code>ModelOfA</code>" in body


def test_deep_parts_are_absent_when_not_written(paths):
    """欄の無いモデルは、その部分を出さないだけ（見出しも番号も詰める）。"""
    body = modelview.body(paths, "desk-one")
    heads = [h for _n, h in re.findall(r"<h2><span class='no'>(\d)</span>([^<]+)</h2>", body)]
    assert "出力スコアの出かたと読み方" not in heads and heads[-1] == "気をつけること"
    assert "<svg" not in body and "小さな例" not in body and "試した経緯" not in body and "詳しく（用語あり）</div>" not in body
    assert "正式な名前と設定（用語あり）" in body


def test_trader_page_does_not_grow(paths):
    """新しい欄（図・組み立て・小さな例・出力スコアの段・経緯・詳しく）はトレーダーのページに出ない。"""
    body = traderview.body(paths, "TA")
    assert "モデル A のひとこと" in body
    for word in ("軸のデータ", "一段目", "癖 1", "直した試し", "用語 Ridge の説明", "alpha = 1.0", "<svg"):
        assert word not in body, word


def test_desk_model_page(paths):
    body = modelview.body(paths, "desk-one")
    assert "使う人は居ない" in body and "/#traders/" not in body
    assert "<span class='tag drop'>落とす</span>どれも悪かった<span class='why'>結果の理由</span>" in body
    assert "<a href='/#specs/experiments/x.md' target='_top'>x.md</a>" in body          # 文書は Specs タブへ
    assert "<a href='/#files/experiments/readme.txt' target='_top'>readme.txt</a>" in body
    assert "消えた会社が入っていない" not in body                                      # stocks = false
    assert "Formal &lt;One&gt;" in body and "feature_layers = <code>own</code>" in body and "cost_bp" not in body
    # config/ の外は開かない（道は出すが中身は写さない）
    assert "etc/passwd.toml" in body and body.count("model = ") == 1
    assert "結果はまだ" in modelview.body(paths, "no-result")                            # 知らない印は出さない
    for gone in ("bad id", "nope", "overview", "TA"):
        assert modelview.body(paths, gone) is None


def test_show_result_false_hides_results(paths):
    paths.models.write_text(MODELS.replace("show_result = true", "show_result = false"), encoding="utf-8")
    body = modelview.body(paths, "a-type")
    heads = re.findall(r"<h2><span class='no'>(\d)</span>([^<]+)</h2>", body)
    assert heads[-1] == ("5", "気をつけること") and "過去のデータで試した結果" not in body
    assert "割れた" not in body and "保留" not in body and "実際の売買の結果ではない" not in body
    assert "直した試し" not in body and "台帳の鍵" not in body                 # 経緯の表と、その「詳しく」も出さない
    assert "保留" not in modelview.body(paths, "all") and "落とす" not in modelview.body(paths, "all")
    assert all(not i.get("badge") for i in modelview.sidebar(paths)["items"])


def test_light_background_is_fixed():
    assert "color-scheme: light;" in modelview.CSS and "background: #ffffff" in modelview.CSS
    assert "prefers-color-scheme" not in modelview.CSS


def test_only_opens_toml(paths, monkeypatch):
    """売買の記録も、机上の実行（runs/）も開かない ＝ 読むのは言葉の正本・設定・実験の config の TOML だけ。"""
    opened: list[str] = []
    real_open = builtins.open

    def spy(file, *a, **kw):
        opened.append(str(file))
        return real_open(file, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    for item in ("all", "a-type", "desk-one"):
        modelview.body(paths, item)
    modelview.sidebar(paths)
    assert opened and all(p.endswith(".toml") for p in opened), opened
    assert not [p for p in opened if re.search(r"(^|/)(out|state|sim|runs)(/|$)|\.env|jsonl|passwd", p)], opened


# ---------------------------------------------------------------- 経路


@pytest.fixture()
def server(tmp_path, paths):
    runs = tmp_path / "runs"
    runs.mkdir()
    srv = vibetab.ThreadingHTTPServer(
        ("127.0.0.1", 0), vibetab.make_handler(runs, vibetab.ExpPaths(tmp_path / "exp"), trader_paths=paths))
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_http_routes(server):
    status, body, _ = _get(f"{server}/models/api/sidebar")
    assert status == 200 and [i["id"] for i in json.loads(body)["items"]] == ["all", "a-type", "desk-one", "no-result"]
    status, body, headers = _get(f"{server}/models/view?item=desk-one")
    assert status == 200 and "机上のひとこと" in body and "color-scheme: light;" in body
    assert headers.get("Content-Security-Policy") == "frame-ancestors 'self'"
    assert _get(f"{server}/models/view?item=all")[0] == 200
    assert _get(f"{server}/models/view?item=nope")[0] == 404
    assert _get(f"{server}/models")[0] == 200


def test_fingerprint_sees_words_and_trader_config(paths):
    fp = modelview.fingerprint(paths)
    assert str(paths.models) in fp and any(k.endswith("TA.toml") for k in fp)


# ---------------------------------------------------------------- 試した経緯の数を研究の DB から（2026-09-21）

REAL_DB = REPO_ROOT / "experiments" / "feature-discovery" / "runs" / "research.sqlite"


def _ledger_db(path, rows, built_at="2026-09-21T10-00-00"):
    """実験側 `ail/rundb.py` の `ledger_rows` と同じ形の小さな DB（⚠ 列の順も同じ）。"""
    conn = sqlite3.connect(path)
    conn.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                       "CREATE TABLE ledger_rows (leak INTEGER, trial_name TEXT, model_name TEXT, is_trial INTEGER,"
                       " verdict TEXT, closed INTEGER, first_run TEXT, last_run TEXT, runs TEXT, doc TEXT);")
    conn.executemany("INSERT INTO ledger_rows VALUES (?, ?, ?, ?, ?, 0, ?, ?, '[]', '{}')", rows)
    conn.execute("INSERT INTO meta VALUES ('ledger_built_at', ?)", (built_at,))
    conn.commit()
    conn.close()


def test_history_counts_come_from_the_db(paths, tmp_path):
    """`names` のある行は DB の数（θ はまとめて・基準線と leak 対照は数えない）。無い行には「人が写した数」の印。
    ⚠ DB は読み取り専用（中身も時刻も変わらない）・無い DB は作らない。"""
    db = tmp_path / "research.sqlite"
    _ledger_db(db, [
        (0, "own.fixed.ridge.shared@50", "own.fixed.ridge.shared", 1, "採る", "2026-09-12", "2026-09-17"),
        (0, "own.fixed.ridge.shared@55", "own.fixed.ridge.shared", 1, "落とす", "2026-09-12", "2026-09-12"),
        (0, "own.fixed.lgbm.shared@50", "own.fixed.lgbm.shared", 1, "落とす", "2026-09-13", "2026-09-13"),
        (0, "own.fixed.none.shared@50", "own.fixed.none.shared", 0, "基準", "2026-09-12", "2026-09-12"),   # 基準線
        (1, "own.fixed.ridge.shared@50", "own.fixed.ridge.shared", 1, "保留", "2026-09-12", "2026-09-12"),  # leak 対照
    ])
    before = (db.read_bytes(), db.stat().st_mtime_ns)
    with_db = dataclasses.replace(paths, research_db=db)
    res = modelview.body(with_db, "a-type").split("</span>過去のデータで試した結果</h2>")[1].split("<h2>")[0]
    assert "<td>直した試し<span class='why'>経緯の理由</span></td><td>3</td><td>採る 1 ／ 落とす 2</td></tr>" in res
    assert "<td>3</td><td>保留 1 ／ 落とす 2<span class='sub'>（写した数）</span></td></tr>" in res        # names の無い古い試し
    assert "<p class='sub'>経緯の注 記録から数えた</p>" in res
    m = modelview.load(with_db)["models"][0]
    assert modelview.best_verdict(m, modelview.ledger_rows(with_db)) == "adopt" and modelview.best_verdict(m) == "hold"
    assert modelview.fingerprint(with_db)["ledger_rows"] == 20260921100000.0
    assert (db.read_bytes(), db.stat().st_mtime_ns) == before
    # DB が無い機械・表の無い DB では TOML の数（印なし）と「人が写した」の注。無い DB は作らない
    for gone in (tmp_path / "none.sqlite", tmp_path / "empty.sqlite"):
        if gone.name == "empty.sqlite":
            sqlite3.connect(gone).close()
        body = modelview.body(dataclasses.replace(paths, research_db=gone), "a-type")
        assert "<td>直した試し<span class='why'>経緯の理由</span></td><td>3</td><td>保留 1 ／ 落とす 2</td></tr>" in body
        assert "写した数" not in body and "<p class='sub'>経緯の注 人が写した</p>" in body
    assert not (tmp_path / "none.sqlite").exists()


def test_real_history_matches_the_db():
    """⚠ **本物の経緯の表の数（人が写した控え）が、研究の DB から数えた数と同じ**。`names` はどれも 1 行以上に当たり、
    `when` の日は DB の実行の日の範囲に入り、試した結果の印は DB の数でいちばん良い印と同じ。
    ⚠ 食い違ったら、記録で確かめてから `models.toml` の数・印・文を直す（DB を正とする）。DB の無い機械では飛ばす。"""
    paths = dataclasses.replace(traderview.TraderPaths.default(), research_db=REAL_DB)
    ledger = modelview.ledger_rows(paths)
    if not ledger:
        pytest.skip("研究の DB（ledger_rows）が無い機械")
    bad = []
    for m in _real()["model"]:
        for r in m.get("history") or []:
            if not r.get("names"):
                continue
            db = modelview.counted(ledger, r["names"])
            where = (m["id"], r["when"], r["what"][:16])
            if db is None:
                bad.append((*where, "names がどの行にも当たらない"))
                continue
            got = {v: db[v] for v in modelview.VERDICTS}
            want = {v: int(r.get(v) or 0) for v in modelview.VERDICTS}
            if got != want or sum(got.values()) != db["rows"]:
                bad.append((*where, f"DB {got}（{db['rows']} 行）／ TOML {want}"))
            day = str(r["when"])[:10]
            if not (db["first"] <= day <= db["last"]):
                bad.append((*where, f"when {day} が DB の日 {db['first']}〜{db['last']} の外"))
        if m.get("history"):
            if modelview.best_verdict(m, ledger) != m["result"]["verdict"]:
                bad.append((m["id"], "試した結果の印", modelview.best_verdict(m, ledger), m["result"]["verdict"]))
    assert not bad, bad


# ---------------------------------------------------------------- 「詳しく」の差し込み（2026-09-21・Phase 4）

def _files_db(path, files: dict[tuple[str, str], object]):
    """実験側 `ail/rundb.py` の `files` と同じ形（JSON は文字列のまま ＝ codec 'text'）。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE files (run TEXT, path TEXT, size INTEGER, sha256 TEXT, codec TEXT, body BLOB)")
    conn.executemany("INSERT INTO files VALUES (?, ?, 0, '', 'text', ?)",
                     [(r, p, json.dumps(doc, ensure_ascii=False)) for (r, p), doc in files.items()])
    conn.commit()
    conn.close()


def test_detail_plugs_come_from_run_records(paths, tmp_path):
    """差し込みは DB の値（点線の下線・title に実行の識別名・fold の順・負は −・`+` で ＋）。DB が無ければ控え。地の文はエスケープ。"""
    run, method = "2026-01-01T00-00-00_x", "方式 <A>"
    db = tmp_path / "research.sqlite"
    _files_db(db, {(run, "fitted/calibration_f2.json"): {method: {"a": -2.5, "b": 0.1}},
                   (run, "fitted/calibration_f1.json"): {method: {"a": 12.345, "b": 0.2}},
                   (run, "checks.json"): {"gate": {"methods": {method: {"auc": 0.51234, "auc_folds": [0.5, 0.52]}}}}})
    before = db.read_bytes()
    score = modelview.body(dataclasses.replace(paths, research_db=db), "a-type").split(
        "</span>出力スコアの出かたと読み方</h2>")[1].split("<h2>")[0]
    title = "試した記録から: 2026-01-01T00-00-00_x"
    assert f"Platt の a <span class='dbv' title='{title}'>＋12.35 ／ −2.50</span>・門 <span class='dbv' title='{title}'>0.512</span>" in score
    assert "<p class='sub'>点線は記録から</p>" in score and "囲みは控え" not in score
    assert db.read_bytes() == before
    # DB が無い機械 ・ 実行が無い ・ 項目が無い ＝ 控え（印なし）
    for gone in (tmp_path / "none.sqlite", db):
        body = modelview.body(dataclasses.replace(paths, research_db=gone if gone.name == "none.sqlite" else None), "a-type")
        assert "Platt の a ＋1.00 ／ −2.00・門 0.500" in body and "class='dbv'" not in body
        assert "<p class='sub'>囲みは控え</p>" in body
    assert not (tmp_path / "none.sqlite").exists()
    facts = modelview.RunFacts(dataclasses.replace(paths, research_db=db))
    assert facts.value("gate", run, method, "width_pt") is None and facts.value("calib", "no-run", method, "a") is None
    assert facts.value("gate", run, method, "passed") is None        # 決まった項目だけ
    facts.close()
    assert modelview.plug_text([0.0, -0.004, 3.0], "+2") == "0.00 ／ −0.00 ／ ＋3.00"


def test_real_detail_plugs_match_the_db():
    """⚠ **本物の「詳しく」の差し込みはどれも形が正しく、控え（人が写した文字）が DB から引いた値と同じ**。
    ⚠ 食い違ったら、記録で確かめてから控えを直す（DB を正とする）。`{{` の書き損じも見つける。DB の無い機械では形だけ見る。"""
    doc = _real()
    plugs, broken = [], []
    for m in doc["model"]:
        for part, d in (m.get("detail") or {}).items():
            for t in [*(d.get("text") or []), *(d.get("points") or [])]:
                found = list(modelview.PLUG.finditer(t))
                if t.count("{{") != len(found) or t.count("}}") != len(found):
                    broken.append((m["id"], part, t[:40]))
                plugs += [(m["id"], part, x) for x in found]
    assert not broken, broken
    assert plugs
    for _id, _part, x in plugs:
        assert x.group(4) in modelview.PLUG_FIELDS[x.group(1)], x.group(0)
    facts = modelview.RunFacts(dataclasses.replace(traderview.TraderPaths.default(), research_db=REAL_DB))
    if facts.conn is None:
        pytest.skip("研究の DB が無い機械")
    bad = []
    for mid, part, x in plugs:
        kind, run, method, field, fmt, fallback = x.groups()
        v = facts.value(kind, run, method, field)
        if v is None:
            bad.append((mid, part, x.group(0)[:60], "DB から引けない"))
        elif modelview.plug_text(v, fmt) != fallback:
            bad.append((mid, part, field, f"DB {modelview.plug_text(v, fmt)} ／ 控え {fallback}"))
    facts.close()
    assert not bad, bad


# ---------------------------------------------------------------- しくみのページ（2026-09-21 にシステム説明から移した）

from tests.test_system_tab import DETAIL_KEYS as PAGE_DETAIL_KEYS, GUIDE_PAGES  # noqa: E402
from tests.test_traders_tab import _walk  # noqa: E402

SYSTEM_TOML = REPO_ROOT / "dashboard" / "system.toml"


def _page_plain(doc: dict) -> list[tuple[str, str]]:
    """しくみのページの本文（⚠ `detail*` ＝ 「詳しく（用語あり）」の囲みとリンク先は検査の外。リンクの見出しは本文）。"""
    out = []
    for p in doc.get("page", []):
        out += _walk(p["id"], {k: v for k, v in p.items() if k not in ("id", "section")})
        for i, sec in enumerate(p.get("section") or []):
            body = {k: v for k, v in sec.items() if k not in PAGE_DETAIL_KEYS}
            body["link_labels"] = [x.get("label") for x in sec.get("links") or []]
            out += _walk(f"{p['id']}[{i}]", body)
    return [(where, text) for where, text in out if not where.endswith(".kind")]


def test_guide_pages_are_plain_and_deep():
    """モデルを作る ／ 過去のデータで確かめる ／ 実際の売買で使う は手厚く（どの段にも「詳しく」か型ごとのしくみ・図が 1 つ以上）＋ 名前と識別名。
    本文はやさしい言葉・設定の数字と細かい成績の数字は「詳しく」へ・「点」と裸の「スコア」を使わない。"""
    doc = _real()
    pages = {p["id"]: p for p in doc["page"]}
    assert list(pages) == list(GUIDE_PAGES)
    for i in ("build", "verify", "live"):
        assert pages[i].get("lead") and sum(1 for sec in pages[i]["section"] if sec.get("figure")) >= 1, i
        for sec in pages[i]["section"]:
            assert sec.get("title") and (sec.get("detail") or sec.get("models")), (i, sec.get("title"))
    assert pages["names"].get("lead") and all(sec.get("detail") for sec in pages["names"]["section"])
    texts = _page_plain(doc)
    assert not [(w, x) for w, t in texts for x in FORBIDDEN if x.lower() in t.lower()], "本文はやさしい言葉で。用語は detail（詳しく）へ"
    assert not [(w, m.group(0)) for w, t in texts for m in re.finditer(r"\$\s?\d|(?<!\d)(?:63|48|55|45)(?!\d)|\d\s*bp", t)]
    assert not [(w, t) for w, t in texts if "点" in t or re.search(r"(?<!出力)スコア", t)]
    # 実際の売買で使う には、モデルの話の段だけ（帳面と口座・安全の仕掛け・何を見て判定するか は システム説明の概要へ）
    titles = [sec["title"] for sec in pages["live"]["section"]]
    assert titles == ["トレーダー ＝ モデル ＋ 売買基準値 ＋ 予算", "1 日の流れ", "出力スコアから注文へ", "出力スコアの読み方"]


def test_guide_page_ids_and_links():
    """しくみのページの id は [[model]] の id と重ならない。リンク先（このタブ・システム説明・文書）が在る。"""
    doc = _real()
    page_ids = [p["id"] for p in doc["page"]]
    model_ids = [m["id"] for m in doc["model"]]
    assert not set(page_ids) & set(model_ids) and modelview.LIST_ID not in page_ids
    items = {i["id"] for i in modelview.sidebar(traderview.TraderPaths.default())["items"]}
    system_ids = {p["id"] for p in tomllib.loads(SYSTEM_TOML.read_text(encoding="utf-8"))["page"]}
    for p in doc["page"]:
        for sec in p.get("section") or []:
            for link in [*(sec.get("links") or []), *(sec.get("detail_links") or [])]:
                assert link.get("label"), (p["id"], link)
                if link.get("doc"):
                    assert (REPO_ROOT / link["doc"]).is_file(), link
                elif link.get("tab") == "models" and link.get("item"):
                    assert link["item"] in items, link
                elif link.get("tab") == "system":
                    assert link.get("item") in system_ids, link
                else:
                    assert link.get("tab") in modelview.TAB_URLS, link


def test_real_guide_pages_render():
    """左の一覧 ＝ 一覧 → しくみ（束）→ いま使っている → 机上で試した。作る ＝ 型ごとのしくみ ／ 確かめる ＝ 検証結果一覧の合計 ／ 使う ＝ システム説明へのリンク。"""
    paths = traderview.TraderPaths.default()
    data = modelview.load(paths)
    items = modelview.sidebar(paths)["items"]
    assert [i["id"] for i in items[:5]] == [modelview.LIST_ID, *GUIDE_PAGES]
    assert {i["group"] for i in items[1:5]} == {data["common"]["group_guide"]}
    build = modelview.body(paths, "build")
    for m in data["models"]:
        if m["id"] in data["users"]:
            assert m["label"] in build and f"/#models/{m['id']}" in build                   # 型ごとのしくみは [[model]] から
    totals = modelview.ledger_totals(paths)
    assert totals and f"<b>{totals['rows']}</b>" in modelview.body(paths, "verify")         # 検証結果一覧の合計は ledger.md から
    live = modelview.body(paths, "live")
    assert "<a href='/#system/overview' target='_top'>" in live and "安全の仕掛け</h2>" not in live and "帳面と口座を合わせる</h2>" not in live
    assert "own-seq.t3-quant60.ridge.shared" in modelview.body(paths, "names")               # 予測モデル名（rules.md 10-2）
    assert "しくみのほかのページ" in build and "<a href='/#models/verify' target='_top'>" in build
    assert "<a href='/#models/build' target='_top'>" in modelview.body(paths, data["models"][0]["id"])   # モデルのページから共通の流れへ
