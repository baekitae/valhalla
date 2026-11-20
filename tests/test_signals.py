import numpy as np
import pandas as pd

import valhalla_core as core


def _daily(n=3000, seed=0, drift=0.0004, vol=0.03):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2012-01-02", periods=n)
    close = 50 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    high = close * (1 + rng.uniform(0, 0.02, n))
    low = close * (1 - rng.uniform(0, 0.02, n))
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close,
                         "Volume": rng.integers(1e6, 2e6, n)}, index=idx)


def _macro(seed):
    d = _daily(seed=seed, drift=0, vol=0.01)
    return {t: d for t in core.MACRO_TICKERS_SIGNAL.values()}


def test_history_has_scores_and_forward_returns():
    h = core.build_signal_history(_daily(), _daily(seed=1), _macro(2))
    assert h["chart_score"].between(0, len(core.CHART_SIGNALS)).all()
    assert h["macro_score"].between(0, len(core.MACRO_SIGNALS)).all()
    # 마지막 4주는 미래 수익률이 없어야 함 (누수 없음)
    assert h["ret_4w"].iloc[-4:].isna().all() and h["ret_4w"].iloc[-5:-4].notna().all()


def test_signals_do_not_use_future_data():
    d = _daily()
    full = core.chart_signal_table(d)
    cut = d.iloc[:2000]
    part = core.chart_signal_table(cut)
    keys = [k for k, _, _ in core.CHART_SIGNALS if k != "soxx_up"]
    common = part.index[:-1]          # 잘린 마지막 주는 미완성일 수 있음
    pd.testing.assert_frame_equal(full.loc[common, keys].astype(float), part.loc[common, keys].astype(float))


def test_backtest_split_and_probability():
    h = core.build_signal_history(_daily(), _daily(seed=1), _macro(2))
    bt = core.score_backtest(h)
    assert bt["train_4"]["표본(주)"].sum() > bt["test_4"]["표본(주)"].sum() > 0
    p, n = core.shrunk_probability(bt["train_4"], 3)
    assert 0 <= p <= 100


def test_prediction_and_grading_and_calibration():
    h = core.build_signal_history(_daily(), _daily(seed=1), _macro(2))
    preds = []
    for i in range(400, len(h) - 15):
        p = core.make_prediction(h.iloc[: i + 1])
        preds.append(p)
    ledger = pd.DataFrame(preds)
    graded = core.grade_predictions(ledger.assign(ret_4w=np.nan, ret_12w=np.nan), h["close"])
    assert graded["ret_4w"].notna().all()
    table, summary = core.calibration(graded, 4)
    assert summary["n"] == len(graded) and 0 <= summary["brier"] <= 1
    assert table["예측수"].sum() == len(graded)


def test_action_signals_edges_and_backtest():
    h = core.build_signal_history(_daily(), _daily(seed=1), _macro(2))
    sig = core.action_signals(h)
    acts = sig.loc[sig["action"] != "", "action"].tolist()
    # 같은 신호가 연달아 두 번 나오지 않음 (매수→매도→매수 …)
    assert all(a != b for a, b in zip(acts, acts[1:]))
    # 신호가 켜진 주는 반드시 조건을 만족
    assert sig.loc[sig["action"] == "매수", "buy_on"].all()
    assert sig.loc[sig["action"] == "매도", "sell_on"].all()
    bt = core.action_backtest(sig)
    assert {"train_buy", "test_sell", "equity", "summary"} <= set(bt)
    assert 0 <= bt["summary"]["exposure"] <= 100


def test_action_signals_no_future_leak():
    d = _daily()
    full = core.action_signals(core.build_signal_history(d, None, {}))
    part = core.action_signals(core.build_signal_history(d.iloc[:2000], None, {}))
    common = part.index[:-1]
    assert (full.loc[common, "action"] == part.loc[common, "action"]).all()


def test_universe_grid_and_compare_tables():
    assert set(core.CATEGORIES) == {v[1] for v in core.SIGNAL_UNIVERSE.values()}
    assert core.partner_of("SOXL") == "SOXX" and core.partner_of("SOXX") == "QQQ" and core.partner_of("JPM") == "XLF"
    assert core.sector_of("FAS") == "금융" and core.category_of("FAS") == "3배 ETF"
    h = core.build_signal_history(_daily(), _daily(seed=1), _macro(2))
    rows = [dict(core.ticker_summary(core.action_signals(h), t)) for t in ("SOXL", "USD", "SOXX", "NVDA", "FAS", "XLF")]
    t = core.compare_tables(pd.DataFrame(rows))
    assert set(t["grid"].index) == {"반도체", "금융"} and 0 <= t["plus_share"] <= 100
