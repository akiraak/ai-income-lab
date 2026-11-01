"""開発ループの「新しい口の置き場」（rules.md 31 章・2026-10-25）: `ail/ideas/i*.py` を bootstrap が読む ／ `build.ORDER` に無い層は後ろに足す。

⚠ 既存の経路は 1 ビットも変わらない: 登録済みの層は全部 `ORDER` にある（並びが変わらない）・既存の登録名は残る。
"""

from __future__ import annotations

import glob
import os
import tomllib

import ail.bootstrap  # noqa: F401
from ail import config, ideas, registry
from cli import build


def test_loader_imports_idea_modules_and_registers_only_new_names():
    names = ideas.load_all()                       # 2 度読んでも同じ（import は 1 度だけ効く）
    assert "i20261025_h5_pick_centered" in names
    dets = registry.available("detector")
    assert "H5 先5〜40日から期間を選ぶゲート（全列・学習・土台差）" in dets
    assert "H5 先5〜40日から期間を選ぶゲート（全列・学習）" in dets          # 既存の名前は残る


def test_layer_order_keeps_existing_configs_bit_identical():
    assert build.layer_order(["own", "cs"]) == ["own", "cs"]
    assert build.layer_order(["cs", "own", "zzz", "trend"]) == ["own", "trend", "cs", "zzz"]   # ORDER の後ろに config の順
    assert build.layer_order(["ex", "own", "ex"]) == ["own", "ex"]
    # 登録済みの層は全部 ORDER にある ＝ 既存の config の並びは前と同じ式（[l for l in ORDER if l in layers]）
    # ⚠ 2026-11-01: `ail/ideas/` が足した層（`mkt`）は ORDER に無い ＝ 後ろに足される側。それ以外は全部 ORDER にある
    idea_layers = {"mkt", "gap"}      # gap ＝ 登録簿 20261101-gap-intraday-layer
    assert set(registry.available("feature")) - idea_layers <= set(build.ORDER)
    for p in glob.glob(os.path.join(config.CONFIG, "experiment", "*.toml")):
        with open(p, "rb") as f:
            layers = tomllib.load(f).get("feature_layers", ["own"])
        if set(layers) & idea_layers:
            continue                               # 新しい層を使う config（dev_mkt_*）は後ろに足す形
        assert build.layer_order(layers) == [l for l in build.ORDER if l in layers], p
