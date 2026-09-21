"""vibeboard の「トレーダー」タブ（`dashboard/traderview.py`・`dashboard/traders.toml`・`dashboard/models.toml`）の検査。仕様は dashboard.md §16。

⚠ モデルの解説は `models.toml`（「予測モデル」タブと同じ 1 本。§17）。ここの `FORBIDDEN`・`COMPARING`・`_texts` は `test_models_tab.py` も使う。

見るもの: やさしい言葉（本文に専門用語が無い）／ ほかの人とくらべる文が無い ／ どこまで確かかの印 ／ 数字は設定から写すだけ ／
だれが居るかは設定から（人数を数えない・見くらべるページが無い）／ 売買の記録を開かない ／ 経路 ／ HTML の escape。
"""

import builtins
import json
import re
import threading
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import traderview
import vibetab

# ⚠ **本文で使わない語**（dashboard.md §16 の表と同じ。足すときは表と一緒に直す）。
# 英字は大文字小文字を区別しない。画面の「正式な名前」の段は設定から写すので、この検査の外
FORBIDDEN = [
    "買い%", "買い％", "出口%", "出口％", "θ", "閾値", "特徴量", "own", "cs_", "rel_", "ex_", "seq",
    "Ridge", "線形", "LightGBM", "決定木", "勾配", "時系列分類器", "QUANT", "fit", "訓練", "較正", "fold", "walk",
    "universe", "us63", "company", "sizing", "notional", "端株", "成行", "執行", "B&H", "n_trials", "DSR", "bp",
    "シグナル", "合図", "ボラティリティ", "リターン", "パラメータ", "過学習", "バックテスト",
    # 2026-09-21 の利用者の指示「表・手法・形式・窓という単語が分かりにくい」。言い換え ＝ 手法 → 数字の選び方・作り方 ／
    # 形式 → 学習範囲 ／ 窓 → 観測期間・取引時間帯（⚠「表」は「表す」「表示」にも当たるので検査に入れず、目で直す）
    "手法", "形式", "窓",
]
REAL_WORDS = Path(traderview.DASHBOARD_DIR) / "traders.toml"
REAL_MODELS = Path(traderview.DASHBOARD_DIR) / "models.toml"
# モデルの `[[model]]` のうち、正式な名前・鍵・道を書く欄（本文の検査の外）
FORMAL_KEYS = ("id", "name", "method", "formal", "configs", "records")
# 用語を書いてよい欄 ＝ 画面の「詳しく（用語あり）」の囲み（`[model.detail.*]`。dashboard.md §17-2）
DETAIL_KEYS = ("detail",)


# ⚠ **ほかの人・ほかのモデルとくらべる文を書かない**（利用者の指示 2026-09-20。トレーダーは 1 人のときも 10 人のときもある）
COMPARING = [r"\d+\s*人", r"(人|トレーダー|モデル)の中で", "いちばん多い", "いちばん少ない", "いちばん高い", "いちばん低い",
             r"（T\d+）", r"T\d+\s*(より|と同じ)", "アキ", "アリス", "カエデ", "ほかの人", "ほかのトレーダー", "ほかのモデル"]


def _walk(where: str, v) -> list[tuple[str, str]]:
    if isinstance(v, dict):
        return [x for k, c in v.items() for x in _walk(f"{where}.{k}", c)]
    if isinstance(v, list):
        return [x for i, c in enumerate(v) for x in _walk(f"{where}[{i}]", c)]
    return [(where, str(v))]


def _texts(doc: dict) -> list[tuple[str, str]]:
    """本文の全部の文（入れ子の表・配列も）。⚠ モデルの正式な名前の欄（`FORMAL_KEYS`）・「詳しく」の欄（`DETAIL_KEYS`）・
    印の鍵（`basis`・`verdict`・経緯の表の数と `old`）と呼び名は除く。"""
    out = _walk("common", doc.get("common") or {})
    for m in doc.get("model", []):
        body = {k: v for k, v in m.items() if k not in FORMAL_KEYS + DETAIL_KEYS}
        body["traits"] = [{k: v for k, v in t.items() if k != "basis"} for t in m.get("traits") or []]
        score = dict(m.get("score") or {})
        score["items"] = [{k: v for k, v in t.items() if k != "basis"} for t in score.get("items") or []]
        body["score"] = score
        body["history"] = [{k: v for k, v in r.items() if k in ("when", "what", "note", "aside")} for r in m.get("history") or []]
        body["result"] = {k: v for k, v in (m.get("result") or {}).items() if k != "verdict"}
        out += _walk(str(m.get("id") or m.get("name")), body)
    return out


def _real() -> dict:
    """人の側（traders.toml）の `[common]`・`[nicks]` ＋ モデルの側（models.toml）の `[[model]]`。"""
    doc = tomllib.loads(REAL_WORDS.read_text(encoding="utf-8"))
    assert "model" not in doc, "モデルの解説は models.toml に書く（正本を 2 つにしない）"
    doc["model"] = tomllib.loads(REAL_MODELS.read_text(encoding="utf-8"))["model"]
    return doc


def test_words_use_plain_language():
    hits = [(where, w) for where, text in _texts(_real()) for w in FORBIDDEN if w.lower() in text.lower()]
    assert not hits, f"やさしい言葉に言い換える: {hits}"


def test_words_do_not_compare_people():
    hits = [(where, pat) for where, text in _texts(_real()) for pat in COMPARING if re.search(pat, text)]
    assert not hits, f"1 つのモデルだけを読んで分かる文にする（人数が変わると嘘になる）: {hits}"


def test_the_model_output_is_called_output_score():
    """モデルが最後に出す 0〜100 の数は「出力スコア」と呼ぶ（利用者の決定 2026-09-20。それまでは「点」）。

    ⚠ 「点」は別の意味（場所の 1 点・幅の単位）と紛れるので本文で使わない。⚠ 裸の「スコア」も使わない
    （検証タブの「スコア」＝ 最良手法の純利 bp と紛れる）。
    """
    texts = _texts(_real())
    assert not [(where, text) for where, text in texts if "点" in text]
    assert not [(where, text) for where, text in texts if re.search(r"(?<!出力)スコア", text)]
    assert any("出力スコア" in text for _where, text in texts)


def test_every_trait_says_how_sure_it_is():
    for m in _real()["model"]:
        assert m.get("traits"), m["id"]
        for t in m["traits"]:
            assert t.get("basis") in traderview.BASES, (m["id"], t.get("text"))
            # 確かめていない理由を言い切らない: 試し運転・見立ての理由は「見られる」か、見えたことの言い換え
            if t["basis"] == "guess":
                assert "見られる" in (t.get("why") or "") + t["text"], (m["id"], t["text"])


def test_words_hold_no_config_numbers():
    """予算・買う線・本数・1 本あたりの金額は設定から写す。言葉の正本に書くと設定とずれる。"""
    bad = [(where, m.group(0)) for where, text in _texts(_real())
           for m in re.finditer(r"\$\s?\d|(?<!\d)(?:63|48|55|45)(?!\d)", text)]
    assert not bad, f"設定の数字は {{budget}} {{n}} {{per}} {{line}} {{sell}} で書く: {bad}"


def test_real_traders_all_have_model_words_and_nicks():
    """いま設定に居る人の全部のモデルに説明がある。呼び名は意味の無い名前（⚠ 一度付けたら変えない・使い回さない）。"""
    paths = traderview.TraderPaths.default()
    words = traderview.load_words(paths)
    names = [k for k, _f, _ok in traderview.trader_files(paths)]
    assert names and not [n for n in names if n.startswith(("test_", "mock_", "sim_"))], names   # 試験用は出さない
    for n in names:
        facts = traderview.load_facts(paths, n)
        assert facts["n"] > 0 and facts["models"], n
        for spec in facts["models"]:
            assert traderview.find_model(words, spec) is not None, (n, spec)
    assert {k: words["nicks"].get(k) for k in ("T1", "T2", "T3")} == {"T1": "アキ", "T2": "アリス", "T3": "カエデ"}
    assert len(set(words["nicks"].values())) == len(words["nicks"])


# ---------------------------------------------------------------- 最小の置き場

WORDS = '''
[nicks]
TA = "ナマエ"

[common]
when = "いつの文"
money = ["お金 1"]
amount_one = "予算は {budget}。{n} 本で割って 1 本あたり {per}。"
buy_way_notional = "金額を決めて買う。"
buy_way_shares = "1 株単位で買う。"
step_learn = "学ぶ文"
step_score = "点の文"
line_same = "点が {line} を上回ったら買い、{line} を下回ったら売る。"
line_band = "点が {line} を上回ったら買い、{sell} を下回ったら売る。"
ruler_sell = "売る"
ruler_wait = "何もしない"
ruler_buy = "買う"
combine_asis = "モデルは 1 本だけ。"
combine_mean = "{k} 本のモデルの点を平均する。"
unsettled = "まだ確定していない"
limit_common = "良いとは言えていない"
'''

MODELS = '''
[common]
card_sees = "見るもの"
card_decides = "決め方"
open_page = "モデルのページ"
basis_build = "作りから"
basis_trial = "試し運転で見えた"
basis_guess = "見立て"
basis_note = "印の意味"
no_words = "説明はまだ"

[[model]]
id = "a-type"
name = "exp_a"
method = "手法 A"
label = "<b>形</b>を読む型"
summary = "モデル A のひとこと"
card = { sees = "札の見るもの", decides = "札の決め方" }
traits = [{ text = "特性 1", why = "理由 1", basis = "build" }, { text = "特性 2", why = "", basis = "trial" }, { text = "特性 3", why = "" }]
sees = [{ label = "まとまり 1", items = ["見るもの 1", "見るもの 2"] }]
how = "点の出し方 A"
limits = ["苦手 A"]

[[model]]
name = "exp_b"
method = "手法 B"
label = "B の型"
summary = "モデル B のひとこと"
traits = [{ text = "特性 B", why = "", basis = "build" }]
how = "点の出し方 B"
'''


def _trader(name: str, models: list[tuple[str, str]], extra: str = "") -> str:
    blocks = "".join(f'[[models]]\nkind = "experiment"\nname = "{n}"\nmethod = "{m}"\n' for n, m in models)
    return f'name = "{name}"\nbudget_usd = 300.0\nuniverse = "u4"\n{extra}\n{blocks}'


@pytest.fixture()
def paths(tmp_path: Path) -> traderview.TraderPaths:
    words = tmp_path / "traders.toml"
    words.write_text(WORDS, encoding="utf-8")
    models = tmp_path / "models.toml"
    models.write_text(MODELS, encoding="utf-8")
    tdir = tmp_path / "traders"
    (tdir / "candidates" / "notional").mkdir(parents=True)
    udir = tmp_path / "universe"
    udir.mkdir()
    (udir / "u4.toml").write_text('name = "u4"\n[groups]\netf = ["E1"]\ncompany = ["C1", "C2", "C3"]\n', encoding="utf-8")
    (tdir / "TA.toml").write_text(_trader("TA", [("exp_a", "手法 A")],
        'universe_group = "company"\nsizing = "shares"\ncombine = "asis"\nthreshold = 55.0'), encoding="utf-8")
    (tdir / "candidates" / "notional" / "TB.toml").write_text(_trader("TB", [("exp_a", "手法 A"), ("exp_b", "手法 B")],
        'sizing = "notional"\ncombine = "mean"\nthreshold = 50.0'), encoding="utf-8")
    (tdir / "test_x.toml").write_text(_trader("test_x", [("exp_a", "手法 A")], "test = true\nthreshold = 50.0"), encoding="utf-8")
    return traderview.TraderPaths(words=words, models=models, traders_dir=tdir, universe_dir=udir)


def test_who_is_listed_comes_from_config(paths):
    """だれが居るかは設定から。試験用は出さない・人数を数えない・見くらべるページは無い。"""
    items = traderview.sidebar(paths)["items"]
    assert [(i["id"], i["label"], i["badge"]) for i in items] == [("TA", "ナマエ（TA）", ""), ("TB", "TB", "未確定")]
    assert not any("人" in json.dumps(i, ensure_ascii=False) for i in items)
    for gone in ("overview", "compare", "flow", "test_x"):
        assert traderview.body(paths, gone) is None
    # 人を足せば、言葉の正本を触らなくても一覧に出る（モデルの説明が無ければ「まだ」と出る）
    (paths.traders_dir / "TC.toml").write_text(_trader("TC", [("exp_new", "")], "threshold = 50.0"), encoding="utf-8")
    assert [i["id"] for i in traderview.sidebar(paths)["items"]] == ["TA", "TB", "TC"]
    body = traderview.body(paths, "TC")
    assert "exp_new" in body and "説明はまだ" in body


def test_facts_come_from_config(paths):
    a = traderview.load_facts(paths, "TA")
    assert (a["settled"], a["n"], a["per"], a["line"], a["sell"], a["sizing"], a["k"]) == (True, 3, 100.0, 55.0, 45.0, "shares", 1)
    b = traderview.load_facts(paths, "TB")
    assert (b["settled"], b["n"], b["per"], b["line"], b["sell"], b["k"]) == (False, 4, 75.0, 50.0, 50.0, 2)
    assert traderview.load_facts(paths, "nobody") is None


def test_page_shows_the_model_and_its_traits(paths):
    body = traderview.body(paths, "TA")
    heads = re.findall(r"<h2><span class='no'>(\d)</span>([^<]+)</h2>", body)
    assert heads == [("1", "使うモデル"), ("2", "モデルの特性"), ("3", "何を見て、どう出力スコアを計算するか"),
                     ("4", "この人の決まり"), ("5", "気をつけること")]
    assert "<code>exp_a</code> <code>手法 A</code>" in body and "モデル A のひとこと" in body      # 使うモデルを表示する
    assert "モデルは 1 本だけ。" in body
    # 使うモデルの札から「予測モデル」タブのそのモデルのページへ（id のあるモデルだけ）
    assert "<a href='/#models/a-type' target='_top'>モデルのページ</a>" in body
    assert "/#models/" not in traderview.body(paths, "TB").split("モデル B のひとこと")[1].split("</div></div>")[0]
    # 特性には、どこまで確かかの印。印の無い文は「見立て」として出す
    assert "特性 1<span class='tag build'>作りから</span>" in body
    assert "特性 2<span class='tag trial'>試し運転で見えた</span>" in body
    assert "特性 3<span class='tag guess'>見立て</span>" in body
    assert "理由: 理由 1" in body and body.count("理由: ") == 1
    assert "予算は $300。3 本で割って 1 本あたり $100。" in body and "1 株単位で買う。" in body
    assert "点が 55 を上回ったら買い、45 を下回ったら売る。" in body and "苦手 A" in body
    assert "まだ確定していない" not in body and "<details>" in body and "ナマエ" in body
    # 設定を変えれば画面が変わる（言葉の正本は触らない）
    cfg = paths.traders_dir / "TA.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("threshold = 55.0", "threshold = 60.0"), encoding="utf-8")
    assert "点が 60 を上回ったら買い、40 を下回ったら売る。" in traderview.body(paths, "TA")


def test_trader_with_several_models_and_candidate_mark(paths):
    body = traderview.body(paths, "TB")
    assert "まだ確定していない" in body and "（候補）" in body
    assert "モデル A のひとこと" in body and "モデル B のひとこと" in body and "2 本のモデルの点を平均する。" in body
    assert "特性 1" in body and "特性 B" in body and "<h3>B の型</h3>" in body               # モデルごとに分けて出す
    assert "点が 50 を上回ったら買い、50 を下回ったら売る。" in body


def test_method_must_match(paths):
    """同じ実験でも手法が違えば別のモデル（説明を取り違えない）。"""
    (paths.traders_dir / "TD.toml").write_text(_trader("TD", [("exp_a", "別の手法")], "threshold = 50.0"), encoding="utf-8")
    body = traderview.body(paths, "TD")
    assert "説明はまだ" in body and "モデル A のひとこと" not in body


def test_ruler_widths_follow_the_lines(paths):
    """出力スコアのものさし: 幅のある線は 売る ／ 何もしない ／ 買う の 3 帯、幅の無い線は 2 帯。幅は設定から。"""
    common = traderview.load_words(paths)["common"]
    band = traderview.ruler(common, traderview.load_facts(paths, "TA"))       # 55 ／ 45
    assert [float(w) for w in re.findall(r"width:([\d.]+)%", band)] == [45.0, 10.0, 45.0]
    assert "class='wait'" in band and "何もしない" not in band               # 狭い帯に長い札を押し込まない（短い札が無ければ空）
    same = traderview.ruler(common, traderview.load_facts(paths, "TB"))       # 50
    assert [float(w) for w in re.findall(r"width:([\d.]+)%", same)] == [50.0, 50.0] and "class='wait'" not in same


def test_light_background_is_fixed():
    """黒背景にしない（利用者の指示 2026-09-20）。OS が暗くても白地・濃い字。"""
    assert "color-scheme: light;" in traderview.CSS and "background: #ffffff" in traderview.CSS
    assert "prefers-color-scheme" not in traderview.CSS


def test_html_is_escaped(paths):
    body = traderview.body(paths, "TA")
    assert "&lt;b&gt;形&lt;/b&gt;を読む型" in body and "<b>形</b>" not in body


def test_never_opens_trading_records(paths, monkeypatch):
    """売買結果は出さない ＝ 読むのは言葉の正本・設定・銘柄の集合の TOML だけ（out/・state/・.env を開かない）。"""
    opened: list[str] = []
    real_open = builtins.open

    def spy(file, *a, **kw):
        opened.append(str(file))
        return real_open(file, *a, **kw)

    monkeypatch.setattr(builtins, "open", spy)
    for item in ("TA", "TB"):
        traderview.body(paths, item)
    traderview.sidebar(paths)
    assert opened and all(p.endswith(".toml") for p in opened), opened
    assert not [p for p in opened if re.search(r"(^|/)(out|state|sim)(/|$)|\.env|jsonl", p)], opened


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


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, "", {}


def test_http_routes(server):
    status, body, _ = _get(f"{server}/traders/api/sidebar")
    assert status == 200 and [i["id"] for i in json.loads(body)["items"]] == ["TA", "TB"]
    status, body, headers = _get(f"{server}/traders/view?item=TA")
    assert status == 200 and "モデルの特性" in body
    assert headers.get("Content-Security-Policy") == "frame-ancestors 'self'"
    assert _get(f"{server}/traders/view?item=overview")[0] == 404
    assert _get(f"{server}/traders/view?item=nope")[0] == 404
    assert _get(f"{server}/traders")[0] == 200


def test_fingerprint_sees_words_and_config(paths):
    fp = traderview.fingerprint(paths)
    assert str(paths.words) in fp and str(paths.models) in fp and any(k.endswith("TA.toml") for k in fp) and any(k.endswith("TB.toml") for k in fp)
