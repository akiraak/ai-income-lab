"""条件付き GAN のシナリオ予測（[プラン](../../../../docs/plans/cgan-scenario-forecast.md)）。

⚠ **`cli.run` の配線（点予測 → 較正 → 閾値 → 純利 bp）には載せない。** 出力が分布（1,000 本のシナリオ）で、
物差しが CRPS なので、registry にも台帳（`ail/catalog.py`）にも登録しない（記録 `cgan-scenario.md` §0 決定 1・6）。
⚠ **だから `ail/bootstrap.py` に import を足さない。**
"""
