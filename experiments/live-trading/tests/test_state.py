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
