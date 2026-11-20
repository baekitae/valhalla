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


def test_app_and_core_versions_match():
    import re
    from pathlib import Path
    app = Path(__file__).resolve().parents[1].joinpath("valhalla.py").read_text(encoding="utf-8")
    assert re.search(r'REQUIRED_CORE_VERSION = "([^"]+)"', app).group(1) == core.CORE_VERSION


def test_daily_report_helpers():
    h = core.build_signal_history(_daily(), _daily(seed=1), _macro(2))
    sig = core.action_signals(h)
    rows = []
    for day in ("2026-11-18", "2026-11-19"):
        for t in ("SOXL", "FAS", "NVDA"):
            r = core.report_row(sig, t, day)
            assert r and r["category"] == core.category_of(t) and isinstance(r["test_n"], int)
            rows.append(r)
    rep = pd.DataFrame(rows)
    cmp_df = core.report_to_compare(rep[rep["date"] == "2026-11-19"])
    assert len(cmp_df) == 3 and "검증 차이(%p)" in cmp_df
    assert core.compare_tables(cmp_df)["grid"].shape[0] >= 2
    trend = core.plus_share_trend(rep)
    assert len(trend) == 2 and "전체" in trend
    ledger = pd.DataFrame({"ticker": ["SOXL"] * 25, "p_up_4w": [60.0] * 25, "base_4w": [50.0] * 25,
                           "ret_4w": [1.0] * 20 + [-1.0] * 5})
    health = {h["유형"]: h["상태"] for h in core.live_health(ledger)}
    assert health["3배 ETF"] == "정상" and health["1배 ETF"] == "표본 부족"


def test_rule_review_picks_by_train_and_judges_on_test():
    hs = {f"T{i}": core.build_signal_history(_daily(seed=i), _daily(seed=i + 50), _macro(2)) for i in range(3)}
    cands = core.candidate_rules()[:12]
    assert len(core.candidate_rules()) == 4 * 3 * 4 * 3 * 3 * 2
    res = core.review_rules(hs, core.ACTION_RULES, cands, thresholds={"min_signals": 1})
    assert res["candidate"] is not None and res["candidate_test"] is not None
    assert isinstance(res["adopt"], bool) and res["reasons"]
    # 지금 규칙과 같은 후보는 고르지 않음
    assert core._rule_key(res["candidate"]) != core._rule_key(core.ACTION_RULES)
    txt = core.rule_text({"buy": {"chart_min": 4, "weeks": 1, "macro_min": 0},
                          "sell": {"chart_max": 2, "weeks": 3, "below_ma": False}})
    assert "4점 이상이 1주" in txt["매수"] and "거시" not in txt["매수"] and "20일선" not in txt["매도"]
