"""i マークのヘルプ（dashboard.md §15-10）。

⚠ **説明をこのコードにも templates にも持たない。** 正本は `dashboard/glossary.toml`（vibeboard の用語タブと同じ 1 本。§12）で、
templates は `{{ info("語") }}` と名前で指すだけ。ここは TOML を読んで `<details class="help">` を組む。

- スクリプトもインラインの style も使わない（CSP の内。開く・閉じるは `<details>` の素の動き。見た目は `app.css`、閉じ方は `app.js`）
- ⚠ **語が無いときは 500 にしない**（停止ボタンのある画面をヘルプの不備で落とさない）。「i?」の印 ＋ 警告ログを出し、
  テスト（`tests/test_help.py`）が templates の語を実物の TOML と突き合わせて落とす ＝ 静かに欠けない
- ⚠ **TOML が読めないときは i マークを出さない**（空の吹き出しを出さない）。`available()` が偽になり、フッタに注意が出る
- 「詳しく」は文書のパスと節を文字で出すだけ（管理画面から vibeboard の hash URL へは飛べない。面が別）
"""

from __future__ import annotations

import logging
import threading
import tomllib
from pathlib import Path
from typing import Callable

from markupsafe import Markup, escape

log = logging.getLogger("ail.help")
GLOSSARY_FILE = Path(__file__).resolve().parents[1] / "glossary.toml"


class HelpBook:
    def __init__(self, path: Path | None = None, clean: Callable[[str], str] | None = None) -> None:
        self.path = Path(path) if path else GLOSSARY_FILE
        self._clean = clean or (lambda s: s)  # 応答に出す文面は Redactor を通す（main.py が渡す）
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._terms: dict[str, dict] = {}
        self.missing_seen: set[str] = set()

    def terms(self) -> dict[str, dict]:
        """語 → {short, doc, where}。⚠ 読めないときは空（仮の説明で埋めない）。mtime が動いたら読み直す。"""
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            mtime = None
        with self._lock:
            if mtime is None:
                self._mtime, self._terms = None, {}
            elif mtime != self._mtime:
                try:
                    with open(self.path, "rb") as f:
                        doc = tomllib.load(f)
                    self._terms = {t["name"]: t for s in doc.get("section", []) for t in s.get("term", []) if t.get("name") and t.get("short")}
                except (OSError, tomllib.TOMLDecodeError, ValueError, TypeError, KeyError):
                    self._terms = {}
                self._mtime = mtime
            return self._terms

    def available(self) -> bool:
        return bool(self.terms())

    def mark(self, name: str) -> Markup:
        """見出しの横に置く i マーク。⚠ `<p>` の中では使わない（`<details>` の開始タグは `<p>` を閉じる）。"""
        terms = self.terms()
        if not terms:
            return Markup("")
        t = terms.get(name)
        if t is None:
            if name not in self.missing_seen:
                self.missing_seen.add(name)
                log.warning("i マーク: 用語が無い: %s（%s に足す）", name, self.path.name)
            return Markup('<span class="help-missing" title="用語が無い: {}">i?</span>').format(name)
        where = " ".join(x for x in (t.get("doc"), t.get("where")) if x)
        more = Markup('<span class="help-d">詳しく: {}</span>').format(self._clean(where)) if where else Markup("")
        return Markup(
            '<details class="help"><summary aria-label="「{name}」の説明"><span class="help-i" aria-hidden="true">i</span></summary>'
            '<span class="help-pop" role="note"><b class="help-t">{name}</b><span class="help-b">{short}</span>{more}</span></details>'
        ).format(name=escape(self._clean(name)), short=escape(self._clean(str(t["short"]))), more=more)
