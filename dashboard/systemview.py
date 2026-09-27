"""vibeboard の「システム説明」タブの画面（HTML の body）。仕様は docs/specs/dashboard.md §18。

**全体像 ＋ パートごとの詳細**（2026-09-26 利用者決定「システム説明 ＝ 全体の説明と各パートの詳細説明。各モデル・各トレーダーの説明はしない」）。
ページ ＝ 全体像（`overview`）→ 予測モデルのしくみ（`model`）→ トレーダーのしくみ（`trader`。Step 4）→ 実際の売買（`live`）→ 名前と識別名（`names`）。
⚠ 2026-09-21〜26 は概要だけで、しくみのページは予測モデルのタブに居た（経緯は dashboard.md §17-7）。
モデル 1 本ずつは「予測モデル」タブ、トレーダー 1 人ずつは「トレーダー」タブ。

  - ⚠ **言葉の正本は `dashboard/system.toml`**（ページ → 段。説明をこのコードに書かない）
  - ⚠ **ページの描き方は `modelview.py` と同じもの**（段・図・表・「詳しく」の囲み ＝ 畳まない）。図の描き方は `figures.py`
  - ⚠ **売買結果は出さない・`out/`・`state/`・`.env`・`runs/` を開かない**（開くのは TOML と `ledger.md` だけ）
  - ⚠ **標準ライブラリだけ・読むだけ**（vibeboard の sidecar が `python3` で起こす。tailnet の閲覧者にも見える）
"""

from __future__ import annotations

import modelview
import traderview
from figures import MAX_NODES, _width, figure_html, flow_svg, folds_svg  # noqa: F401（図の描き方は figures.py。ここからも同じ名前で引ける）
from traderview import TraderPaths

ID_PATTERN = modelview.ID_PATTERN
SYSTEM_URL, TAB_URLS = modelview.SYSTEM_URL, modelview.TAB_URLS
CSS = modelview.CSS
ledger_totals = modelview.ledger_totals


def load(paths: TraderPaths) -> dict:
    doc = traderview._load(paths.system)
    pages = [p for p in doc.get("page", []) if ID_PATTERN.match(str(p.get("id") or ""))]
    return {"common": doc.get("common") or {}, "pages": pages}


def sidebar(paths: TraderPaths) -> dict:
    return {"items": [{"id": p["id"], "label": str(p.get("label") or p["id"]), "sub": str(p.get("sub") or "")}
                      for p in load(paths)["pages"]]}


def body(paths: TraderPaths, item: str) -> str | None:
    data = load(paths)
    page = next((p for p in data["pages"] if p["id"] == item), None)
    if page is None:
        return None
    return modelview.page_body(data["common"], paths, page, data["pages"], SYSTEM_URL)


def fingerprint(paths: TraderPaths) -> dict[str, float]:
    """見張り用。システム説明の言葉・検証結果一覧 ＋ モデルの言葉と実売買の設定（型ごとの段がそこから写す）。"""
    out = traderview.fingerprint(paths)
    for f in (paths.system, paths.ledger):
        try:
            out[str(f)] = f.stat().st_mtime
        except OSError:
            continue
    return out
