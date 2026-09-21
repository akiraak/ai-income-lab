"""vibeboard の「予測モデル」タブ（`dashboard/modelview.py`・`dashboard/models.toml`）の検査。仕様は dashboard.md §17。

見るもの: やさしい言葉・くらべる文が無い・設定の数字が無い（`[common]` も）／ 鍵（`id`）／ 試した結果の印と記録のファイル ／
「いま使っている」は実売買の設定から（本数を数えない）／ `show_result = false` で結果を出さない ／ TOML しか開かない ／ 経路 ／ 白地。
⚠ 特性の印（`basis`）とモデルの本文の検査は `test_traders_tab.py` が同じ `models.toml` に対して行う。
"""

import builtins
import json
import re
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
axis_data = "入れるデータ"
axis_prep = "下ごしらえ"
axis_calc = "計算の仕方"
axis_target = "当てにいく対象"
axis_scope = "学ぶ範囲"
walk_title = "小さな例"
walk_caution = "例の数字は作りもの"
score_read = "読み方"
score_link = "出力スコアの使われ方"
history_title = "試した経緯"
history_old = "計算を直す前"
history_note = "経緯の注"

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
  { when = "2026-09-12", what = "直した試し", hold = 1, drop = 2, note = "経緯の理由" },
  { when = "2026-09-19", what = "別の決まり", adopt = 1, aside = "ちがう決まり" },
]

[model.detail.about]
text = ["用語 Ridge の説明"]

[model.detail.how]
points = ["alpha = 1.0"]
links = [{ label = "記録へ", doc = "docs/specs/experiments/x.md" }]

[model.detail.score]
text = ["Platt の a"]

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
    assert "<tr><th>入れるデータ</th><td>軸のデータ</td></tr><tr><th>計算の仕方</th><td>軸の計算</td></tr>" in part["どんなモデルか"]
    assert "知らない軸" not in body and "用語 Ridge の説明" in part["どんなモデルか"]
    # 3: 図（主張が直前・箱の文字はエスケープ）→ 見るもの → 答えの出し方 → 小さな例 → 共通の流れへ → 詳しく
    how = part["何を見て、どう答えを出すか"]
    assert how.index("<p class='claim'>共通の図の主張</p><div class='fig'><svg") < how.index("まとまり 1") < how.index("答えの出し方 A")
    assert how.count("<g class='node'>") == 3 and "&lt;計算&gt;" in how and "<計算>" not in how
    assert how.index("答えの出し方 A") < how.index("<h3>小さな例</h3>") < how.index("<a href='/#system/build' target='_top'>共通の流れ</a>")
    assert "<li><b>段 1</b>　一段目</li><li>二段目</li></ol>" in how and "空" not in how and "例の数字は作りもの" in how
    assert "alpha = 1.0" in how and "<a href='/#specs/experiments/x.md' target='_top'>記録へ</a>" in how
    # 4: 癖（印つき。印が無ければ見立て）・読み方・使われ方へのリンク・詳しく
    score = part["出力スコアの出かたと読み方"]
    assert "癖 1<span class='tag trial'>試し運転で見えた</span><span class='why'>理由: 癖の理由</span>" in score
    assert "癖 2<span class='tag guess'>見立て</span>" in score and "読み方 1" in score and "Platt の a" in score
    assert "<a href='/#system/live' target='_top'>出力スコアの使われ方</a>" in score
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
