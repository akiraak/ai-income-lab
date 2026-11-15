"""文字列リテラルの一文字の言葉。rules_code2 と同じ読み分けから、記録の項目名に当たるものを外す。"""
import re, rules_code2
def rules_for(path):
    name = path.rsplit("/", 1)[-1]
    if name in ("test_models_tab.py", "test_traders_tab.py"): return []          # テストの中の作り物の TOML（中で閉じている）
    if name in ("gate.py", "rundb.py", "paper.py"): return []                     # 記録に残る文 ／ SQL のスキーマ ／ シェルと対のログの文
    drop = ["土台"]                                                               # fitted の項目名「土台」（実行の記録）
    if re.search(r"ail/ideas/|ail/models/", path) or name in ("catalog.py", "ledger.py", "report.py"): drop.append("種")   # 項目名「種」
    if name in ("prep.py", "test_transform_detector.py"): drop.append("入出窓")   # 項目名「口」
    if name == "select_candidates.py": drop.append("目矢刻")                      # 決まりの名前の列「印」
    if name == "test_nightly.py": drop.append("控え")                              # run-live.sh の表示を見る assert（シェルは替えていない）
    return [(p, r) for p, r in rules_code2.rules_for(path) if not any(d in p for d in drop)]
