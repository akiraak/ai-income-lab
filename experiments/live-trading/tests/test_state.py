from state import TraderState, load_state, save_state


def test_buy_sell_realized_and_roundtrip(tmp_path):
    st = TraderState("A")
    st.apply_buy("SPY", 2, 100.0, "2026-10-01")
    st.apply_buy("SPY", 2, 110.0, "2026-10-02")   # 内部移転などで増える場合の加重平均
    assert st.holdings["SPY"].avg_price == 105.0 and st.cost_in_use() == 420.0
    st.apply_sell("SPY", 4, 120.0, "2026-10-03")
    assert st.realized_usd == 60.0 and "SPY" not in st.holdings
    assert st.unsettled("2026-10-03") == 480.0 and st.unsettled("2026-10-04") == 0.0
    save_state(str(tmp_path), st)
    back = load_state(str(tmp_path), "A")
    assert back.realized_usd == 60.0 and len(back.history) == 3


def test_sell_more_than_held_raises():
    import pytest
    st = TraderState("A")
    st.apply_buy("SPY", 1, 100.0, "2026-10-01")
    with pytest.raises(ValueError):
        st.apply_sell("SPY", 2, 100.0, "2026-10-02")


def test_selling_a_prorated_fractional_holding_rounds_to_zero():
    """合算注文の按分でできた端数の持ち分（6 桁）を全部売ると、約定の数量は注文の 4 桁で返る。丸めの差で落ちない・0 になる。"""
    from state import TraderState
    st = TraderState(name="x")
    st.apply_buy("T", 2.992573, 25.0, "2026-11-25")
    st.apply_sell("T", 2.9926, 26.0, "2026-11-30")
    assert "T" not in st.holdings and abs(st.realized_usd - 2.992573) < 1e-9
    st.apply_buy("T", 3.0, 25.0, "2026-12-01")
    import pytest
    with pytest.raises(ValueError):
        st.apply_sell("T", 3.001, 26.0, "2026-12-02")          # 丸めの差を超える売りは今までどおり拒否


def test_rounding_dust_is_not_kept_as_a_holding():
    from state import TraderState
    st = TraderState(name="x")
    st.apply_buy("BAC", 0.997728, 54.0, "2026-10-01")
    st.apply_sell("BAC", 0.9977, 54.3, "2026-10-02")          # 注文の数量は 4 桁 ＝ 0.000028 株が残る
    assert "BAC" not in st.holdings and st.position("BAC") == 0
    st.apply_buy("BAC", 2.0, 54.0, "2026-10-03")
    st.apply_sell("BAC", 1.0, 54.0, "2026-10-04")             # ふつうの一部売りは残る
    assert st.holdings["BAC"].shares == 1.0
