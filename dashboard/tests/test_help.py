"""i マークのヘルプ（dashboard.md §15-10）。文面の正本は dashboard/glossary.toml。

固定するもの: templates が指す語が実物の TOML にある（⚠ 静かに欠けない）／ 各画面に出る ／ `<p>` の中に置かない ／
インラインを書かない（CSP）／ 公開面でも出て秘密が出ない ／ 語なし・ファイルなしのときの振る舞い。
⚠ ブラウザでの動き（押すと開く・Escape で閉じる）は pytest に入れない（playwright を依存に足さない。§15-9 と同じ）。
"""

import os
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.helptext import GLOSSARY_FILE, HelpBook
from app.main import create_app
from tests.conftest import good_run, write_run
from tests.test_app import INLINE, assert_clean
from tests.test_live import build_live_dir

DASH = Path(__file__).resolve().parents[1]
TEMPLATES = DASH / "app" / "templates"
INFO_CALL = re.compile(r"""info\(\s*(["'])(.+?)\1\s*\)""")
MARK = '<details class="help">'
# <p> の中の <details> は、開始タグが <p> を閉じる（HTML の構文規則）＝ 画面が崩れる
IN_P = re.compile(r"<p\b[^>]*>(?:(?!</p>).)*?<details class=\"help\"", re.S)

PAGES = ["/", "/?partial=strip", "/overall", "/overall?partial=monitor", "/traders/test_a", "/records", "/records/20260908T140000Z",
         "/records/diff?a=20260908T140000Z&b=20260909T140000Z", "/judge", "/ops"]


def used_terms() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted(TEMPLATES.glob("*.html")):
        for m in INFO_CALL.finditer(path.read_text(encoding="utf-8")):
            out.setdefault(m.group(2), []).append(path.name)
    return out


@pytest.fixture
def client(settings):
    write_run(settings.records_dir, good_run())
    write_run(settings.records_dir, good_run(run_id="20260909T140000Z", at="2026-09-09T14:00:00.000+00:00"))
    build_live_dir(settings.live_dir)
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        yield c


# ---------------------------------------------------------------- 静的（templates と実物の TOML）


def test_every_term_used_in_templates_exists_in_the_real_glossary():
    """⚠ 語を消したり名前を変えたりすると、ここが赤くなる（画面では「i?」になるだけなので、テストで気づく）。"""
    known = HelpBook().terms()
    assert known, f"{GLOSSARY_FILE} が読めない"
    used = used_terms()
    missing = {name: files for name, files in used.items() if name not in known}
    assert not missing, f"templates が指す語が glossary.toml に無い: {missing}"
    assert len(used) >= 20


def test_each_screen_template_has_a_mark():
    used_in = {f for files in used_terms().values() for f in files}
    for name in ("base.html", "overview.html", "overall.html", "trader.html", "orders_history.html", "monitor_panel.html",
                 "records.html", "record.html", "diff.html", "judge.html", "ops.html"):
        assert name in used_in, f"{name} に i マークが無い"


def test_help_texts_carry_no_account_or_secret_like_words():
    """公開面でも見える文面。⚠ 口座番号・トークンの形をしたものを書かない（正本の側で固定する）。"""
    known = HelpBook().terms()
    for name in used_terms():
        text = f"{name} {known[name]['short']} {known[name].get('where', '')}"
        assert not re.search(r"\b5W[A-Z0-9]{6}\b|eyJ[A-Za-z0-9_-]{8,}|acct-[0-9a-f]{4}", text), name


# ---------------------------------------------------------------- 描画


def test_pages_show_marks_without_breaking_csp_or_paragraphs(client):
    csp_before = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'"
    for path in PAGES:
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-security-policy"] == csp_before, path   # ⚠ CSP は緩めない
        assert MARK in r.text, f"{path} に i マークが無い"
        assert "help-missing" not in r.text, f"{path} に語の無い i マークがある"
        assert not INLINE.search(r.text), path
        assert not IN_P.search(r.text), f"{path}: <p> の中に details.help がある（<p> が閉じて崩れる）"
        assert_clean(r.text, path)
    body = client.get("/").text
    assert 'aria-label="「差 1（執行価格）」の説明"' in body and 'role="note"' in body
    assert "正 ＝ 不利" in body                       # 文面は TOML の写し
    assert "用語の正本（glossary.toml）が読めない" not in body


def test_static_files_carry_the_behaviour(client):
    js = client.get("/static/app.js").text
    assert "details.help" in js and "Escape" in js and "help-left" in js
    assert ".style" not in js and "setAttribute(\"style\"" not in js   # 置き場所はクラスで（style を書かない）
    css = client.get("/static/app.css").text
    assert "details.help" in css and ".help-pop" in css and "focus-visible" in css


def test_public_face_shows_help_too(settings):
    """公開面（cloudflare。ループバックは JWT 免除）でも i マークは出る（読むだけの部品）。操作の画面は無いまま。"""
    build_live_dir(settings.live_dir)
    settings.auth_mode = "cloudflare"
    settings.cf_team, settings.cf_aud, settings.cf_email = "team", "aud", "a@example.com"
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        for path in ("/", "/overall", "/traders/test_a", "/judge"):
            r = c.get(path)
            assert r.status_code == 200 and MARK in r.text and "help-missing" not in r.text, path
            assert_clean(r.text, path)
        assert c.get("/ops").status_code == 404


# ---------------------------------------------------------------- HelpBook（単体）

TOML = '''
[[section]]
id = "s"
label = "節"
  [[section.term]]
  name = "差 1"
  short = "ずれ <b>bp</b>"
  doc = "docs/x.md"
  where = "§1"
'''


def test_helpbook_escapes_and_names_the_doc(tmp_path):
    p = tmp_path / "g.toml"
    p.write_text(TOML, encoding="utf-8")
    html = str(HelpBook(p).mark("差 1"))
    assert html.startswith(MARK) and "&lt;b&gt;bp&lt;/b&gt;" in html and "<b>bp</b>" not in html
    assert "詳しく: docs/x.md §1" in html and "<a " not in html   # リンクにしない（面が別）
    assert not INLINE.search(html)


def test_helpbook_unknown_term_is_visible_not_fatal(tmp_path, caplog):
    p = tmp_path / "g.toml"
    p.write_text(TOML, encoding="utf-8")
    book = HelpBook(p)
    with caplog.at_level("WARNING", logger="ail.help"):
        html = str(book.mark("無い語<x>"))
    assert 'class="help-missing"' in html and "i?" in html and "<x>" not in html
    assert book.missing_seen == {"無い語<x>"} and "用語が無い" in caplog.text


def test_helpbook_missing_or_broken_file_shows_nothing(tmp_path):
    book = HelpBook(tmp_path / "nai.toml")
    assert not book.available() and str(book.mark("差 1")) == ""
    bad = tmp_path / "bad.toml"
    bad.write_text("[[section", encoding="utf-8")
    assert not HelpBook(bad).available()


def test_footer_warns_when_glossary_is_unreadable(settings, monkeypatch, tmp_path):
    """g3plus で TOML が COPY されていないとき（§7 の追従前）。⚠ 空の吹き出しを出さず、黙って消しもしない。"""
    monkeypatch.setenv("AIL_GLOSSARY_FILE", str(tmp_path / "nai.toml"))
    with TestClient(create_app(settings, start_monitors=False), client=("127.0.0.1", 50000)) as c:
        body = c.get("/").text
    assert MARK not in body and "用語の正本（glossary.toml）が読めない" in body


def test_helpbook_reloads_when_the_file_changes(tmp_path):
    p = tmp_path / "g.toml"
    p.write_text(TOML, encoding="utf-8")
    book = HelpBook(p)
    assert "ずれ" in str(book.mark("差 1"))
    p.write_text(TOML.replace("ずれ", "変えた"), encoding="utf-8")
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 10))
    assert "変えた" in str(book.mark("差 1"))


def test_helpbook_passes_text_through_the_redactor(tmp_path):
    p = tmp_path / "g.toml"
    p.write_text(TOML, encoding="utf-8")
    html = str(HelpBook(p, clean=lambda s: s.replace("ずれ", "<masked>")).mark("差 1"))
    assert "&lt;masked&gt;" in html and "ずれ" not in html
