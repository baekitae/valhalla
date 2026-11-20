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


def test_player_positions_backtest_and_ensemble():
    d, ref = _daily(), _daily(seed=1)
    h = core.build_signal_history(d, ref, _macro(2))
    pos = core.player_positions(h, d, ref, [(1, core.ACTION_RULES)])
    assert {"rule_v1", "trend200", "voltarget", "hold"} == set(pos.columns)
    assert pos["voltarget"].dropna().between(0, 1).all() and set(pos["trend200"].dropna().unique()) <= {0.0, 1.0}
    bt = core.backtest_players(pos, h["close"])
    assert set(bt["id"]) == set(pos.columns)
    hold_row = bt.set_index("id").loc["hold"]
    # 그냥 보유의 누적 = 주봉 단순 보유 (첫 매수 비용만 차이)
    raw = (h["close"].iloc[-1] / h["close"].iloc[0] - 1) * 100
    assert abs(hold_row["전체 누적(%)"] - raw) / max(abs(raw), 1) < 0.05
    # 실전 기록 형태로 바꿔 채점·경기표·앙상블
    rows = []
    for wk in h.index[-60:-1]:
        for pid in pos.columns:
            rows.append({"player": pid, "ticker": "SOXL", "week": wk.strftime("%Y-%m-%d"), "position": float(pos.loc[wk, pid]) if pd.notna(pos.loc[wk, pid]) else 0.0})
    g = core.grade_positions(pd.DataFrame(rows), h["close"])
    assert g["ret_1w"].notna().all()
    arena = core.arena_table(g)
    assert "그냥 보유" in set(arena["선수"]) and abs(arena.set_index("id").loc["hold", "보유 대비(%p)"]) < 1e-9
    w = core.ensemble_weights(g, h.index[-1])
    assert set(w) == {"rule_v1", "trend200", "voltarget"} and abs(sum(w.values()) - 1) < 1e-6 and max(w.values()) <= 0.5 + 1e-9
    assert 0 <= core.ensemble_position({"rule_v1": 1.0, "trend200": 0.0, "voltarget": 0.5}, w) <= 1
    # 표본 부족이면 균등
    w2 = core.ensemble_weights(g[g["week"] > g["week"].max()[:8] + "01"], h.index[-1])
    assert len(set(round(v, 4) for v in w2.values())) <= 1


def test_lab_techniques_only_on_leveraged_etfs():
    daily = _daily(seed=3, n=1500)
    hist = core.complete_weeks(core.build_signal_history(daily, daily, {}), daily.index[-1])
    lev = core.player_positions(hist, daily, daily, [(1, core.ACTION_RULES)], "SOXL")
    stock = core.player_positions(hist, daily, daily, [(1, core.ACTION_RULES)], "NVDA")
    for pid in core.TECHNIQUE_PLAYERS:
        assert pid in lev and pid not in stock
        v = lev[pid].dropna()
        assert len(v) > 50 and v.between(0, 1).all()
    assert abs(lev["rebal50"].dropna().median() - 0.5) < 0.1          # 50:50은 비중 절반 근처
    assert core.player_label("inf_v2_safe").startswith("④")
    ch = lev["charles"].dropna()
    assert ch.between(0, 1).all() and set(ch.round(2).unique()) <= {0.0, 0.15, 0.25, 0.35, 0.4, 0.7}
    bt = core.backtest_players(lev, hist["close"])
    assert set(core.TECHNIQUE_PLAYERS) <= set(bt["id"])


def test_charles_rules_follow_signal_index():
    n = 900
    idx = pd.bdate_range("2020-01-01", periods=n)
    up = pd.DataFrame({"Close": 100 * np.exp(np.linspace(0, 0.6, n))}, index=idx)      # 잔잔한 상승
    down = pd.DataFrame({"Close": 100 * np.exp(np.linspace(0, -0.6, n))}, index=idx)   # 꾸준한 하락
    soxl = pd.DataFrame({"Close": 30 * np.exp(np.linspace(0, 1.8, n))}, index=idx)
    assert abs(core.charles_exposure(soxl, up).iloc[-1] - 0.70) < 1e-9          # 큰 규칙: 70% 보유(과열 아님)
    assert core.charles_exposure(soxl, down).iloc[-1] == 0.0                     # 200일선 아래면 쉼
    # 과열: 평균선보다 25% 넘게 위면 절반(35%)
    hotc = pd.DataFrame({"Close": 100 * np.exp(np.linspace(0, 3.0, n))}, index=idx)
    assert abs(core.charles_exposure(soxl, hotc).iloc[-1] - 0.35) < 1e-9
