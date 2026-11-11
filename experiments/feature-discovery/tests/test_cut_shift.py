"""選ぶ段の併記「切れ目ずらしで残るか」（rules.md 33-6。2026-11-11）。

⚠ 物差し・順位・上位は併記の有無で変わらない ／ 併記は識別項目が同じで検証方式だけが切れ目ずらしの 2 行から読む ／
`cli.cut_shift` の config は手で書いた `cut13_*` ／ `cut23_*`（33-2）と同じ作り ／ 運転手は新顔の行にだけ 2 本足す（`cut_new`）。
"""

from __future__ import annotations

import tomllib

import pytest

from ail import catalog, config
from ail.validation import splits
from cli import cut_shift as cs
from cli import devloop
from cli import select_candidates as sc
from cli import trader_shape as ts
from tests.test_devloop_night import FakeEnv, world  # noqa: F401
from tests.test_select_candidates import _doc, _insert, conn  # noqa: F401

F1 = "own.f9-1.ridge.shared"


def _cut(conn, base: str, off: str, edge: float, verdict: str, method: str = "F9-1 試し", **kw):
    slug = {"1/3": "cut1of3", "2/3": "cut2of3"}[off]
    _insert(conn, f"{base}~{slug}@50", _doc(method, verdict=verdict, edge=edge, fold="3/5 ＋＋−−＋", style=sc.CUT_STYLES[off], **kw))


# --- 併記の列 ------------------------------------------------------------------------------------------

def test_styles_follow_the_catalog_and_the_two_fixed_levels():
    assert sc.CUT_STYLES == {"1/3": "閾値売買（切れ目ずらし1/3）", "2/3": "閾値売買（切れ目ずらし2/3）"}
    assert set(sc.CUT_STYLES) == set(splits.CUT_OFFSETS) == {o for o, _ in cs.OFFSETS}
    assert all(cs.style_of(o) == sc.CUT_STYLES[o] for o in sc.CUT_STYLES)


def test_ranking_is_unchanged_by_the_note(conn):
    before = [r.name for r in sc.select(conn, top=3, diag=False)["ranked"]]
    _cut(conn, F1, "1/3", 900, "保留")
    _cut(conn, F1, "2/3", -900, "落とす")
    res = sc.select(conn, top=3, diag=False)
    assert [r.name for r in res["ranked"]] == before                 # ⚠ 切れ目ずらしの行は上位に入らない・順位も動かさない
    assert res["rule1"] == sc.apply_rules(sc.load_rows(conn))["rule1"]


@pytest.mark.parametrize("a,b,label", [
    ((10.0, "保留"), (5.0, "採る"), "残る"),
    ((10.0, "保留"), (-5.0, "落とす"), "切れ目に依る"),
    ((-1.0, "落とす"), (-5.0, "落とす"), "切れ目に依る"),
    ((10.0, "保留"), (0.0, "保留"), "切れ目に依る"),                 # 上乗せ 0 は「正」ではない（33-4 の 2）
])
def test_stays_only_when_both_are_positive_and_held(conn, a, b, label):
    _cut(conn, F1, "1/3", *a)
    _cut(conn, F1, "2/3", *b)
    res = sc.select(conn, top=3, diag=False)
    row = next(r for r in res["ranked"] if r.name == f"{F1}@50")
    assert row.cut["label"] == label and set(row.cut["parts"]) == {"1/3", "2/3"}
    assert row.cut["parts"]["1/3"]["name"] == f"{F1}~cut1of3@50"
    other = next(r for r in res["ranked"] if r.name == "own.f9-2.ridge.shared@50")
    assert other.cut == {"label": None, "parts": {}} and sc.cut_text(other.cut) == "—"
    text = sc.render(res)
    assert "切れ目ずらし" in text and label in text and sc.to_json(res)["top"][0].get("cut") is not None


def test_one_row_only_or_another_key_is_not_a_reading(conn):
    _cut(conn, F1, "1/3", 10.0, "保留")
    _cut(conn, F1, "2/3", 10.0, "保留", symbols="48")                # 銘柄の数が違う ＝ 別の識別項目
    row = next(r for r in sc.select(conn, top=3, diag=False)["ranked"] if r.name == f"{F1}@50")
    assert row.cut["label"] is None and list(row.cut["parts"]) == ["1/3"] and "片方だけ" in sc.cut_text(row.cut)


def test_asof_also_hides_later_cut_rows(conn):
    _insert(conn, f"{F1}~cut1of3@50", _doc("F9-1 試し", edge=10, style=sc.CUT_STYLES["1/3"]), first_run="2026-11-11")
    _insert(conn, f"{F1}~cut2of3@50", _doc("F9-1 試し", edge=10, style=sc.CUT_STYLES["2/3"]), first_run="2026-11-11")
    row = next(r for r in sc.select(conn, asof="2026-10-10", top=3, diag=False)["ranked"] if r.name == f"{F1}@50")
    assert row.cut["parts"] == {}


# --- config を機械で書く -------------------------------------------------------------------------------

@pytest.mark.parametrize("src,method,hand", [("sel_small4_1995", "F1-7 IC の安定性", "sel_small4_1995"),
                                             ("sel_small4_1995", "F2-2 RFE", "sel_small4_1995"),
                                             ("trader_mix_t4", "M1 3本の平均", "mix_t4")])
def test_machine_config_matches_the_hand_written_ones(src, method, hand):
    """33-6 の 3 ＝ 33-2 の写し: 手で書いた `cut13_*` ／ `cut23_*` と、`name` と絞った手法以外は同じ。"""
    for offset, tag in cs.OFFSETS:
        name = cs.config_name(src, "50", tag)
        text = cs.cut_config(ts.source_text(src), src=src, method=method, theta="50", offset=offset, name=name)
        a, b = tomllib.loads(text), config.experiment(f"{tag}_{hand}")
        assert a.pop("name") == name == f"dev_{tag}_{src.replace('_', '-')}_50" and b.pop("name")
        if src == "sel_small4_1995":
            assert a.pop("selectors") == [method] and method in b.pop("selectors")
        assert a == b
        assert catalog.threshold_style(tomllib.loads(text)) == sc.CUT_STYLES[offset]
        assert cs.counted(text) == (1 if src == "sel_small4_1995" else 2)     # 合成 ＝ 1 ＋ この実行の中のメンバー（33-6 の 4）
        assert f"登録簿 `{cs.IDEA}`" in text[:2000]                            # 棚卸しの区分（`group_of`）は config の頭から読む


MULTI = '''# 頭の説明
name = "roles_x"
dataset = "daily"
detectors = ["H1 買い", "H2 売り", "H3 別", "X1 損切り"]
model = "Ridge"

[trading]
style = "threshold"
thresholds = [50, 55]
form = "shared"
top_k = [3]

[trading.position_exits]
lines = [50]

# 1 つ目
[[trading.mix]]
name = "R1 組"
combine = "roles"
members = [
  { method = "H1 買い", role = "buy" },
  { method = "H2 売り", role = "exit" },
]

# 2 つ目
[[trading.mix]]
name = "R2 組"
combine = "roles"
members = [
  { method = "H3 別", role = "buy" },
  { method = "X1 損切り", role = "stop" },
]

[[trading.trader]]
tag = "5本・$600"

# 検証
[validation]
split = "walk_forward_dates"
folds = 5
seed = 0

[features]
x = 1
'''


def test_mix_keeps_only_its_own_block_and_inner_members():
    text = cs.cut_config(MULTI, src="roles_x", method="R2 組", theta="50", offset="1/3", name="dev_cut13_roles-x_50")
    d = tomllib.loads(text)
    assert [m["name"] for m in d["trading"]["mix"]] == ["R2 組"] and d["detectors"] == ["H3 別", "X1 損切り"]
    assert d["trading"]["thresholds"] == [50] and "trader" not in d["trading"] and "top_k" not in d["trading"]
    assert "position_exits" not in d["trading"] and d["validation"] == {"split": "walk_forward_dates", "folds": 5, "seed": 0, "cut_offset": "1/3"}
    assert d["features_from"] == "roles_x" and d["features"] == {"x": 1}
    assert "# 1 つ目" not in text and "R1 組" not in text                    # ⚠ 見出しの間のコメントは落ちることがある（`ts._remove_table` と同じ・意味は変わらない）
    assert cs.counted(text) == 3                                              # 合成 1 ＋ 中のメンバー 2
    one = cs.cut_config(MULTI, src="roles_x", method="H1 買い", theta="55", offset="2/3", name="n")
    d1 = tomllib.loads(one)
    assert d1["detectors"] == ["H1 買い"] and d1["trading"]["thresholds"] == [55] and cs.counted(one) == 3   # ⚠ 合成 2 つは残る ＝ 数える


def test_baselines_are_not_counted():
    text = MULTI.replace('detectors = ["H1 買い", "H2 売り", "H3 別", "X1 損切り"]', 'selectors = ["F9 試し", "乱択（基準）", "全部使う（基準）"]')
    text = text[:text.index("# 1 つ目")] + text[text.index("# 検証"):]
    assert cs.counted(text) == 2 * 2                                          # F9 ・全部使う（閾値売買では数える）× θ 2


@pytest.mark.parametrize("bad", ["H1 買い〔売り線50〕", "H1 買い〔上位3・端数〕", "無い手法"])
def test_rows_with_brackets_or_unknown_methods_are_refused(bad):
    with pytest.raises(SystemExit, match="無い手法"):
        cs.cut_config(MULTI, src="roles_x", method=bad, theta="50", offset="1/3", name="n")


def test_variant_sources_and_other_offsets_are_refused():
    with pytest.raises(SystemExit, match="変種"):
        cs.cut_config(MULTI.replace("seed = 0", "seed = 0\nmax_train_days = 2520"), src="x", method="R1 組", theta="50", offset="1/3", name="n")
    with pytest.raises(SystemExit, match="変種"):
        cs.cut_config(MULTI.replace("seed = 0", 'seed = 0\ncut_offset = "1/3"'), src="x", method="R1 組", theta="50", offset="2/3", name="n")
    with pytest.raises(SystemExit, match="ずらし"):
        cs.cut_config(MULTI, src="x", method="R1 組", theta="50", offset="1/2", name="n")
    with pytest.raises(SystemExit, match="threshold"):
        cs.cut_config('name = "x"\n', src="x", method="R1 組", theta="50", offset="1/3", name="n")


def test_config_without_validation_table_gets_one():
    text = MULTI[:MULTI.index("# 検証")] + "[features]\nx = 1\n"
    d = tomllib.loads(cs.cut_config(text, src="x", method="R1 組", theta="50", offset="2/3", name="n"))
    assert d["validation"] == {"cut_offset": "2/3"} and d["features"] == {"x": 1}


# --- 運転手（段 4 ・段 3 の持ち越し） --------------------------------------------------------------------

SRC = '''name = "exp_x"
dataset = "daily"
selectors = ["F9-1 試し", "乱択（基準）"]
model = "Ridge"

[trading]
style = "threshold"
thresholds = [50, 55, 60]
form = "shared"

[validation]
folds = 5
seed = 0
'''


def _patch(monkeypatch, root):
    monkeypatch.setattr(ts, "resolve_trial", lambda conn, name: {"experiment": "exp_x", "method": "F9-1 試し", "theta": "50", "run": "r"})
    monkeypatch.setattr(ts, "source_text", lambda exp: SRC)
    monkeypatch.setattr(ts, "source_path", lambda name: str(root / "config" / "experiment" / f"{name}.toml"))
    monkeypatch.setattr(ts, "shape_config", lambda text, **kw: f'name = "{kw["name"]}"\n')


def test_without_cut_new_the_stage_is_as_before(world, monkeypatch):
    root, cfg, conn = world
    assert "cut_new" not in cfg["select"]
    _patch(monkeypatch, root)
    st = {"last_top": ["other@50"]}
    out = devloop.select_stage(cfg, FakeEnv(root, conn).env, st, "2026-10-27", lambda s: None)
    assert out["shaped"] == ["dev_tr_exp-x_50"] and st["pending_shapes"] == ["dev_tr_exp-x_50"]


def test_new_entry_gets_two_cut_configs_next_to_the_trader_shape(world, monkeypatch):
    root, cfg, conn = world
    cfg["select"]["cut_new"] = True
    _patch(monkeypatch, root)
    st = {"last_top": ["other@50"]}
    logs: list[str] = []
    out = devloop.select_stage(cfg, FakeEnv(root, conn).env, st, "2026-10-27", logs.append)
    want = ["dev_tr_exp-x_50", "dev_cut13_exp-x_50", "dev_cut23_exp-x_50"]
    assert out["new"] == ["own.f9-1.ridge.shared@50"] and out["shaped"] == want and st["pending_shapes"] == want
    for name, off in (("dev_cut13_exp-x_50", "1/3"), ("dev_cut23_exp-x_50", "2/3")):
        d = tomllib.loads((root / "config" / "experiment" / f"{name}.toml").read_text(encoding="utf-8"))
        assert d["name"] == name and d["validation"]["cut_offset"] == off and d["selectors"] == ["F9-1 試し"] and d["trading"]["thresholds"] == [50]
        assert devloop.pending_trials(name) == 1
    assert devloop.pending_trials("dev_tr_exp-x_50") == 2 and devloop.pending_trials("無い") == 2
    # 次の晩は新顔ではない ＝ 足さない。持ち越しは残る
    out2 = devloop.select_stage(cfg, FakeEnv(root, conn).env, st, "2026-10-28", lambda s: None)
    assert out2["new"] == [] and out2["shaped"] == [] and st["pending_shapes"] == want
    # 日誌の 1 行に併記が出る
    text = devloop.write_journal(cfg, "2026-10-27", {"select": out}, str(root / "log" / "2026-10-27.md"))
    assert "切れ目ずらし: —" in text and "`dev_cut13_exp-x_50`" in text


def test_existing_cut_rows_are_not_run_again(world, monkeypatch):
    root, cfg, conn = world
    cfg["select"]["cut_new"] = True
    _patch(monkeypatch, root)
    _cut(conn, F1, "1/3", 10.0, "保留")
    conn.commit()
    logs: list[str] = []
    out = devloop.select_stage(cfg, FakeEnv(root, conn).env, {"last_top": ["other@50"]}, "2026-10-27", logs.append)
    assert out["shaped"] == ["dev_tr_exp-x_50", "dev_cut23_exp-x_50"] and any("1/3 の行が既にある" in s for s in logs)


def test_unwritable_shapes_are_logged_not_fatal(world, monkeypatch):
    root, cfg, conn = world
    cfg["select"]["cut_new"] = True
    _patch(monkeypatch, root)
    monkeypatch.setattr(ts, "resolve_trial", lambda conn, name: {"experiment": "exp_x", "method": "F9-1 試し〔売り線50〕", "theta": "50", "run": "r"})
    logs: list[str] = []
    out = devloop.select_stage(cfg, FakeEnv(root, conn).env, {"last_top": ["other@50"]}, "2026-10-27", logs.append)
    assert out["shaped"] == ["dev_tr_exp-x_50"] and sum("切れ目ずらし" in s and "書けない" in s for s in logs) == 2


def test_pending_counts_and_overflow_goes_to_the_next_night(world):
    root, cfg, conn = world
    exp = root / "config" / "experiment"
    big = SRC.replace('selectors = ["F9-1 試し", "乱択（基準）"]', 'selectors = ["a", "b", "c", "d", "e"]').replace("[50, 55, 60]", "[50]")
    for n in ("dev_cut13_big_50", "dev_cut23_big_50", "dev_cut13_big2_50"):
        (exp / f"{n}.toml").write_text(big.replace("seed = 0", 'seed = 0\ncut_offset = "1/3"'), encoding="utf-8")
    assert devloop.pending_trials("dev_cut13_big_50") == 5
    state = {"pending_shapes": ["dev_tr_x_50", "dev_cut13_big_50", "dev_cut23_big_50", "dev_cut13_big2_50"]}
    exps, picked, count = devloop.plan_queue(cfg, devloop.load_ideas(cfg), state)
    assert exps == ["dev_tr_x_50", "dev_cut13_big_50", "dev_cut23_big_50"] and count == 12 and picked == []   # 2 ＋ 5 ＋ 5・4 本目は翌晩
    # 先頭の 1 本は上限を超えていても回す（詰まらない）
    (exp / "dev_cut13_huge_50.toml").write_text(big.replace('["a", "b", "c", "d", "e"]', str([f"m{i}" for i in range(20)]).replace("'", '"'))
                                                .replace("seed = 0", 'seed = 0\ncut_offset = "1/3"'), encoding="utf-8")
    exps, _, count = devloop.plan_queue(cfg, devloop.load_ideas(cfg), {"pending_shapes": ["dev_cut13_huge_50", "dev_tr_x_50"]})
    assert exps == ["dev_cut13_huge_50"] and count == 20
