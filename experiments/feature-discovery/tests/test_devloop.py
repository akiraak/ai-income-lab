"""`cli.devloop register` ＝ 開発ループの登録簿（rules.md 31-1）を検査し、`register.md` を吐く。

⚠ 項目がそろわない TOML は 登録 にできない ／ status は一方向（戻さない）／ registered_at は --promote が打つ ／
既出の疑いは key_shape が予測モデル名に当たった数（印は付けない）／ 生成物は手で書かない。
"""

from __future__ import annotations

import os
import tomllib

import pytest

from cli import devloop

IDEA = {"id": "20261025-x", "date": "2026-10-25", "title": "試し", "kind": "データ",
        "origin": {"source": "independent", "refs": [], "r_pct": 30, "n_per_night": 5, "prompt": "ideate_free"},
        "question": "問い", "structure": "口", "n_trials_planned": 3, "key_shape": "own-mkt.all.ridge.shared",
        "failure_modes": ["型 1"], "prediction": "予想", "effort": "小", "measurable": "測れる: 理由",
        "touches": "新しいモジュールだけ", "status": "案"}


@pytest.fixture
def loop(tmp_path, monkeypatch):
    ideas = tmp_path / "ideas"
    ideas.mkdir()
    cfg = {"ideation": {"reference_pct": 30, "ideas_per_night": 5},
           "paths": {"ideas": str(ideas), "register": str(tmp_path / "register.md")}}
    monkeypatch.setattr(devloop, "load_config", lambda: cfg)
    monkeypatch.setattr(devloop, "ledger_models", lambda db: ["own-mkt.all.ridge.shared", "own-mkt.all.ridge.shared~p1995", "own.all.ridge.shared"])
    return ideas, tmp_path / "register.md"


def _write(ideas, doc):
    devloop.write_idea(str(ideas / f"{doc['id']}.toml"), doc)


def test_dump_roundtrip_and_validate(loop):
    ideas, _ = loop
    _write(ideas, IDEA)
    text = (ideas / "20261025-x.toml").read_text(encoding="utf-8")
    assert tomllib.loads(text) == IDEA and text.startswith("# ")
    assert devloop.validate(IDEA, str(ideas / "20261025-x.toml")) == []
    bad = devloop.validate({**IDEA, "effort": "特大", "measurable": "たぶん", "status": "登録"}, str(ideas / "20261025-x.toml"))
    assert any("effort" in b for b in bad) and any("measurable" in b for b in bad) and any("registered_at" in b for b in bad)
    assert devloop.validate(IDEA, str(ideas / "other.toml"))[0].startswith("id ")
    assert devloop.validate({**IDEA, "n_trials_planned": 0, "key_shape": []}, str(ideas / "20261025-x.toml")) == []   # 物差しは識別項目なし
    assert any("key_shape" in b for b in devloop.validate({**IDEA, "key_shape": []}, str(ideas / "20261025-x.toml")))


def test_duplicates_count_pattern_hits_including_variants(loop):
    assert devloop.duplicates("own-mkt.all.ridge.shared", ["own-mkt.all.ridge.shared", "own-mkt.all.ridge.shared~p1995", "own.all.ridge.shared"]) == (2, ["own-mkt.all.ridge.shared", "own-mkt.all.ridge.shared~p1995"])
    assert devloop.duplicates(["own-mkt.*.lgbm.shared"], ["own-mkt.all.ridge.shared"]) == (0, [])
    assert devloop.duplicates("x", None) == (-1, [])


def test_register_promote_and_transitions(loop, monkeypatch):
    ideas, out = loop
    _write(ideas, IDEA)
    _write(ideas, {**IDEA, "id": "20261025-y", "key_shape": "own-zzz.*", "touches": "共有の経路: cli/run.py"})
    monkeypatch.setattr(devloop, "ledger_models", lambda db: ["own-mkt.all.ridge.shared"])

    class A:
        promote, set, run, out, db, strict = None, None, None, None, None, True
    a = A()
    assert devloop.cmd_register(a) == 0
    text = out.read_text(encoding="utf-8")
    assert "`20261025-x`" in text and "1 行（例 `own-mkt.all.ridge.shared`）" in text and "| 0 |" in text and "案" in text

    a.promote = ["20261025-x"]
    devloop.cmd_register(a)
    d = tomllib.loads((ideas / "20261025-x.toml").read_text(encoding="utf-8"))
    assert d["status"] == "登録" and d["registered_at"].endswith(("PDT", "PST"))
    with pytest.raises(SystemExit):           # 登録 は 2 度できない
        devloop.cmd_register(a)
    a.promote, a.set = None, ["20261025-y=人の手"]
    devloop.cmd_register(a)
    assert tomllib.loads((ideas / "20261025-y.toml").read_text(encoding="utf-8"))["status"] == "人の手"
    a.set = ["20261025-y=案"]                  # 戻さない
    with pytest.raises(SystemExit):
        devloop.cmd_register(a)
    a.set = ["20261025-x=作成中"]
    devloop.cmd_register(a)
    a.set, a.run = None, ["20261025-x=2026-10-25T13-00-00_dev_x"]
    devloop.cmd_register(a)
    assert tomllib.loads((ideas / "20261025-x.toml").read_text(encoding="utf-8"))["runs"] == ["2026-10-25T13-00-00_dev_x"]
    a.run, a.set = None, ["20261025-x=回した"]
    devloop.cmd_register(a)
    assert "回した" in out.read_text(encoding="utf-8")


def test_promote_refuses_incomplete(loop):
    ideas, _ = loop
    _write(ideas, {k: v for k, v in IDEA.items() if k != "prediction"})

    class A:
        promote, set, run, out, db, strict = ["20261025-x"], None, None, None, None, False
    with pytest.raises(SystemExit):
        devloop.cmd_register(A())


def test_real_config_and_prompts_exist():
    cfg = devloop.load_config()
    assert cfg["ideation"]["reference_pct"] == 30 and cfg["ideation"]["ideas_per_night"] == 5     # K1
    assert cfg["run"]["leak"] is True and cfg["run"]["max_trials_per_night"] == 12                 # K4
    assert cfg["schedule"]["time_local"] == "01:00"                                                 # K8
    p = os.path.join(devloop.ROOT, cfg["ideation"]["prompts"])
    for name in ("contract.md", "ideate_ref.md", "ideate_free.md"):
        assert os.path.exists(os.path.join(p, name))
    for f in ("contract.md",):
        text = open(os.path.join(p, f), encoding="utf-8").read()
        assert "良し悪し" in text and "[[idea]]" in text
        assert "3b. 未知への挑戦" in text and "p_hold" in text and "learn_if_null" in text      # 31-6（2026-11-01）
        assert "needed_effect" in text and "検出力の実測" in text                                # 31-6 の 5（2026-11-13）
    assert cfg["ideation"]["contract"] == 3 == devloop.CONTRACT_VERSION and cfg["ideation"]["outside_frame_min"] == 2
