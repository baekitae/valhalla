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


def test_breakeven_needed_index():
    # 첨부 예시: SOX 9,419.72 · SOXL $45.30 · 평단 $163.41 → 즉시 기준 약 14,447 (+53.4%)
    assert abs(core.needed_index(9419.72, 45.30, 163.41, 3) - 14447) < 2
    later = core.needed_index(9419.72, 45.30, 163.41, 3, years=1, vol=0.30)
    assert 16000 < later < 16600                          # 1년 뒤면 변동성·비용 때문에 더 올라야 함
    t = core.breakeven_table(9419.72, 45.30, {"평단": 163.41, "최종": 200.0}, 3, 0.35)
    assert list(t["지금 즉시 상승률(%)"].round(0)) == [53.0, 64.0]
    assert (t["1년 뒤"] > t["지금 즉시"]).all() and (t["2년 뒤"] > t["1년 뒤"]).all()
    idx = pd.bdate_range("2026-01-01", periods=3)
    h = core.needed_gain_history(pd.Series([100, 100, 100.0], idx), pd.Series([50, 40, 60.0], idx), 100)
    assert h.iloc[1] > h.iloc[0] > h.iloc[2]


def test_position_moves_marks_only_meaningful_changes():
    idx = pd.date_range("2026-01-02", periods=8, freq="W-FRI")
    rule = pd.Series([1, 1, 0, 0, 1, 1, 1, 0], idx, dtype=float)
    m = core.position_moves(rule)
    assert list(m["action"]) == ["매도", "매수", "매도"]
    wobble = pd.Series([0.5, 0.55, 0.45, 0.6, 0.8, 0.79, 0.5, 0.52], idx)   # 잔떨림은 무시
    m2 = core.position_moves(wobble)
    assert list(m2["action"]) == ["매수", "매도"] and m2["to"].iloc[0] == 0.8


def _crash_then(path_after):
    """300일 상승 → 30% 급락 → +6% 반등일 → 이후 경로."""
    up = list(np.linspace(100, 130, 300))
    down = list(np.linspace(130, 91, 15))
    pre = up + down + [91 * 1.06]
    vals = pre + [pre[-1] * x for x in path_after]
    idx = pd.bdate_range("2020-01-01", periods=len(vals))
    return pd.Series(vals, idx)


def test_rebound_reversal_vs_temporary():
    rise = _crash_then(list(np.linspace(1.0, 1.25, 80)))          # 저점 안 깨고 계속 상승 → 추세 전환
    ev = core.detect_rebounds(rise)
    assert len(ev) == 1 and ev["trigger"].iloc[0].startswith("하루")
    lab = core.label_rebounds(ev, rise)
    assert lab["outcome"].iloc[0] == 1.0
    fall = _crash_then(list(np.linspace(1.0, 0.8, 80)))           # 저점(91) 다시 깸 → 일시 반등
    lab2 = core.label_rebounds(core.detect_rebounds(fall), fall)
    assert lab2["outcome"].iloc[0] == 0.0
    short = _crash_then([1.01, 1.02])                               # 아직 13주 안 지남 → 대기
    lab3 = core.label_rebounds(core.detect_rebounds(short), short)
    assert np.isnan(lab3["outcome"].iloc[0])
    calm = pd.Series(np.linspace(100, 200, 400), pd.bdate_range("2020-01-01", periods=400))
    assert core.detect_rebounds(calm).empty                         # 급락 없는 장에서는 사건 없음


def test_rebound_scoreboard_and_base_rate():
    weeks = pd.date_range("2020-01-03", periods=4, freq="W-FRI")
    lab = pd.DataFrame({"week": weeks, "outcome": [1.0, 0.0, 1.0, np.nan],
                        "resolved_on": [weeks[1], weeks[2], weeks[3], None]})
    base = core.rebound_base_rates(lab)
    assert base.iloc[0] == 0.5 and base.iloc[2] > 0.5 - 0.2
    calls = pd.DataFrame({"good": [0.9, 0.1, 0.8, 0.5], "bad": [0.0, 1.0, 0.0, 0.5],
                          core.REBOUND_BASE_PLAYER: base.to_numpy()}, index=weeks)
    t = core.rebound_scoreboard(lab, calls)
    assert t.iloc[0]["id"] == "good" and t.set_index("id").loc["bad", "적중률(%)"] == 0.0
    assert t.set_index("id").loc["good", "사건 수"] == 3


def test_rebound_new_event_after_failed_one():
    up = list(np.linspace(100, 130, 300)) + list(np.linspace(130, 91, 15))
    vals = up + [96.5, 95, 93, 88, 85, 84] + [84 * 1.08] + list(np.linspace(90.7, 100, 40))
    c = pd.Series(vals, pd.bdate_range("2020-01-01", periods=len(vals)))
    ev = core.detect_rebounds(c)
    assert len(ev) == 2                       # 첫 반등이 저점을 깨며 실패 → 1~2주 뒤 반등도 새 사건
    lab = core.label_rebounds(ev, c)
    assert lab["outcome"].iloc[0] == 0.0


def test_backtest_metrics_and_experiment_rule():
    daily = _daily(seed=5, n=1500)
    hist = core.complete_weeks(core.build_signal_history(daily, daily, {}), daily.index[-1])
    v2 = {"buy": {"chart_min": 2, "weeks": 3, "macro_min": 3}, "sell": {"chart_max": 0, "weeks": 3, "below_ma": True}}
    exps = core.experiment_rules(core.ACTION_RULES, [(2, v2)])
    assert exps["exp_v3"]["buy"] == v2["buy"] and exps["exp_v3"]["sell"] == core.ACTION_RULES["sell"]
    assert core.experiment_rules(core.ACTION_RULES, []) == {}
    pos = core.player_positions(hist, daily, daily, [(1, core.ACTION_RULES)])
    pos["exp_v3"] = core.rule_positions(core.action_signals(hist, exps["exp_v3"]))
    bt = core.backtest_players(pos, hist["close"]).set_index("id")
    for col in ("연평균(%)", "칼마", "최장 회복(주)", "연 매매(회)", "최근 2년 칼마"):
        assert col in bt.columns
    assert bt.loc["hold", "연 매매(회)"] == 0 and bt.loc["hold", "최장 회복(주)"] > 0
    assert "실험" in core.player_label("exp_v3")


def test_macro_checklist_player():
    idx = pd.bdate_range("2020-01-01", periods=200)
    tnx = pd.Series(np.linspace(5.2, 4.0, 200), idx)          # 금리 하락 → 4.5 아래로
    vix = pd.Series(np.r_[np.full(100, 35.0), np.full(100, 18.0)], idx)
    credit = pd.Series(np.linspace(2.5, 2.0, 200), idx)        # 스프레드 축소
    ck = core.check_conditions(tnx, vix, credit)
    assert np.isnan(ck["prob"].iloc[10])                       # 비교할 과거가 없으면 판단 보류
    assert ck["met"].iloc[-1] == 4 and abs(ck["prob"].iloc[-1] - 0.8) < 1e-9
    assert ck["met"].iloc[80] == 2                             # 금리 높고 VIX 35 → 하락·스프레드만 충족
    wk = pd.Series(pd.to_datetime(["2020-06-05", "2020-10-02"]))
    calls = core.check_calls_at(ck, wk)
    assert calls.iloc[-1] == ck["prob"].dropna().iloc[-1]
    snap = core.checklist_snapshot(tnx, vix, credit, pd.Series([3.2], [idx[-1]]),
                                   pd.Series(np.linspace(100, 104, 20), pd.date_range("2019-01-01", periods=20, freq="MS")))
    names = [r["지표"] for r in snap]
    assert "하이일드 스프레드" in names and "근원 PCE 물가 (전년 대비)" in names
    assert {r["상태"] for r in snap} <= {"ok", "warn", "stop"}
    ev = core.upcoming_events(pd.Timestamp("2026-11-25 01:00"))
    assert ev["일정"].iloc[0].startswith("미국 10월 PCE") and (~ev["확정"]).any()


def test_fill_missing_day_from_intraday():
    daily = pd.DataFrame({"Open": [1.0, 2.0], "High": [1.0, 2.0], "Low": [1.0, 2.0], "Close": [1.0, 43.21],
                          "Volume": [1, 1]}, index=pd.to_datetime(["2026-11-20", "2026-11-23"]))
    idx = pd.to_datetime(["2026-11-23 15:30", "2026-11-24 09:30", "2026-11-24 12:00", "2026-11-24 15:30"])
    intr = pd.DataFrame({"Open": [43, 39, 37, 35.5], "High": [44, 40, 38, 36], "Low": [42, 38, 34, 35],
                         "Close": [43.21, 38, 35, 35.33], "Volume": [5, 1, 2, 3]}, index=idx)
    out = core.fill_missing_days(daily, core.intraday_to_daily(intr))
    assert out.index[-1] == pd.Timestamp("2026-11-24") and out["Close"].iloc[-1] == 35.33
    assert out["Low"].iloc[-1] == 34 and out["Volume"].iloc[-1] == 6 and len(out) == 3
    assert core.fill_missing_days(daily, None) is daily


def test_daily_version_of_rules():
    daily = _daily(seed=7, n=900)
    dh = core.build_daily_signal_history(daily, daily, {})
    assert len(dh) > 600 and dh.index.is_monotonic_increasing and not dh.index.duplicated().any()
    # 마지막 날 행 = '마지막 날로 끝나는 5거래일 묶음'으로 만든 주봉의 마지막 행과 같아야 함
    idx = daily.index
    ends = idx[[(len(idx) - 1 - i) % 5 == 0 for i in range(len(idx))]]
    ref = core.build_signal_history(daily, daily, {}, ends)
    assert dh.loc[idx[-1], "chart_score"] == ref["chart_score"].iloc[-1]
    assert dh.loc[idx[-1], "close"] == daily["Close"].iloc[-1]
    act = core.action_signals(dh, core.ACTION_RULES, step=5)
    assert set(act["action"].unique()) <= {"", "매수", "매도"}
    # 주봉 버전 결과는 그대로(ends 없음 = 금요일 주봉)
    wk = core.build_signal_history(daily, daily, {})
    assert wk.index.dayofweek.unique().tolist() == [4]
    pos_d = core.rule_positions(act)
    pos_w = core.weekly_to_daily_positions(core.rule_positions(core.action_signals(wk)), daily.index)
    bt = core.backtest_daily(pd.DataFrame({"daily": pos_d, "weekly": pos_w, "hold": 1.0}, index=daily.index),
                             daily["Close"]).set_index("전략")
    assert {"daily", "weekly", "hold"} <= set(bt.index) and bt.loc["hold", "연 매매(회)"] == 0


def test_daily_league_players_and_grading():
    daily = _daily(seed=9, n=1500)
    dh = core.build_daily_signal_history(daily, daily, {})
    lev = core.daily_player_positions(daily, daily, dh, [(1, core.ACTION_RULES)], "SOXL")
    stk = core.daily_player_positions(daily, daily, dh, [(1, core.ACTION_RULES)], "NVDA")
    for pid in ("rule_v1", "trend200", "voltarget", "hold", "inf_mine", "charles"):
        assert pid in lev.columns and lev[pid].dropna().between(0, 1).all()
    assert "inf_mine" not in stk.columns
    today = core.daily_today(lev).set_index("id")
    assert today.loc["hold", "오늘 비중"] == 1.0 and today.loc["hold", "오늘 신호"] == ""
    days = daily.index[-3:]
    rows = pd.DataFrame([{"player": p, "ticker": "SOXL", "day": d.strftime("%Y-%m-%d"), "position": pos}
                         for d in days for p, pos in (("hold", 1.0), ("cash", 0.0))])
    g = core.grade_daily(rows, daily["Close"])
    assert g["ret_1d"].notna().sum() == 4                       # 마지막 날은 다음 날이 없어 대기
    t = core.daily_league_table(g).set_index("id")
    assert t.loc["cash", "누적(%)"] == 0 and "보유 대비(%p)" in t.columns


def test_expected_session():
    import datetime as _dt
    assert core.expected_session(_dt.datetime(2026, 11, 24, 20, 0)) == pd.Timestamp("2026-11-24")   # 화 저녁
    assert core.expected_session(_dt.datetime(2026, 11, 24, 10, 0)) == pd.Timestamp("2026-11-23")   # 화 장중
    assert core.expected_session(_dt.datetime(2026, 11, 23, 9, 0)) == pd.Timestamp("2026-11-20")    # 월 개장 전 → 금
    assert core.expected_session(_dt.datetime(2026, 11, 22, 12, 0)) == pd.Timestamp("2026-11-20")   # 일요일


def test_portfolio_model_pieces():
    idx = pd.bdate_range("2019-01-01", periods=400)
    rng = np.random.default_rng(4)
    closes = {t: pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, 400))), idx) for t in core.THEME_ETFS}
    closes["소비재"] = pd.Series(100 * np.exp(0.006 * np.arange(400)), idx)    # 꾸준히 1등
    late = np.r_[np.linspace(100, 60, 370), np.linspace(60, 72, 30)]           # 꼴찌권에서 최근 반등 → 떠오르는 테마
    closes["사이버보안"] = pd.Series(late, idx)
    weeks = pd.date_range("2019-07-05", idx[-1], freq="W-FRI")
    rk = core.theme_rank_history(closes, weeks)
    lead, rising = core.pick_themes(rk["rank"].iloc[-1], rk["rank_prev"].iloc[-1], rk["above50"].iloc[-1])
    assert lead == "소비재"
    chg = (rk["rank_prev"].iloc[-1] - rk["rank"].iloc[-1]).drop("반도체")
    assert rising != lead and (chg[rising] >= 3 or chg.max() < 3)
    prices = {"WANT": pd.Series(1.0, idx), "XLY": closes["소비재"]}
    tg = core.portfolio_targets(weeks[-1], rk, prices, core_on=0.0)
    assert tg["CASH"] >= 0.5 - 1e-9 and tg.get("WANT") == 0.25          # 규칙 v1이 쉬면 핵심은 현금, 소비재는 3배
    assert abs(sum(tg.values()) - 1) < 1e-9
    wk = pd.DataFrame({"SOXL": np.linspace(10, 20, 30), "WANT": np.linspace(10, 12, 30)},
                      index=pd.date_range("2020-01-03", periods=30, freq="W-FRI"))
    tgts = {d: {"SOXL": 0.5, "WANT": 0.5} for d in wk.index}
    for pol in ("week", "month", "band", "none"):
        sim = core.simulate_portfolio(wk, tgts, pol)
        assert sim["value"].iloc[-1] > 1.3
    none = core.simulate_portfolio(wk, tgts, "none")
    week = core.simulate_portfolio(wk, tgts, "week")
    assert none["turnover"].sum() < week["turnover"].sum()
    st_ = core.portfolio_stats(week["value"], "2020-05-01")
    assert {"연평균(%)", "칼마", "실전(%)"} <= set(st_)
    hold = pd.DataFrame({"ticker": ["SOXL"], "shares": [10.0]})
    o = core.rebalance_orders(hold, {"SOXL": 30.0, "WANT": 50.0}, {"SOXL": 0.5, "WANT": 0.5}, new_cash=100,
                              buy_only=True).set_index("종목")
    assert o.loc["WANT", "주문"] == "매수" and o.loc["SOXL", "주문"] != "매도"
    o2 = core.rebalance_orders(hold, {"SOXL": 30.0, "WANT": 50.0}, {"SOXL": 0.5, "WANT": 0.5}).set_index("종목")
    assert o2.loc["SOXL", "주문"] == "매도" and abs(o2.loc["WANT", "금액($)"] - 150) < 1e-6


def test_sleeve_swap_without_full_rebalance():
    wk = pd.DataFrame({"A": [10, 20, 20, 20.0], "B": [10, 10, 10, 10.0], "C": [10, 10, 10, 10.0]},
                      index=pd.date_range("2020-01-03", periods=4, freq="W-FRI"))
    t = {wk.index[0]: {"A": 0.5, "B": 0.5}, wk.index[1]: {"A": 0.5, "B": 0.5},
         wk.index[2]: {"A": 0.5, "C": 0.5}, wk.index[3]: {"A": 0.5, "C": 0.5}}
    sim = core.simulate_portfolio(wk, t, "none")
    w = sim["weights"].iloc[2]
    assert "B" not in w and abs(w["A"] - 2 / 3) < 1e-9 and abs(w["C"] - 1 / 3) < 1e-9   # A는 불어난 비중 그대로
    full = core.simulate_portfolio(wk, t, "week")["weights"].iloc[2]
    assert abs(full["A"] - 0.5) < 1e-9


def test_theme_schedule_holds():
    weeks = pd.date_range("2020-01-03", periods=6, freq="W-FRI")
    themes = list(core.THEME_ETFS)
    rk = pd.DataFrame([[i + 1 for i in range(len(themes))]] * 6, index=weeks, columns=themes, dtype=float)
    rk.loc[weeks[2], ["금융", "리츠"]] = rk.loc[weeks[2], ["리츠", "금융"]].to_numpy()   # 순위가 잠깐 뒤바뀜
    ranks = {"rank": rk, "rank_prev": rk.shift(1).fillna(rk), "above50": rk.notna()}
    sch = core.theme_schedule(weeks, ranks)
    assert sch["lead"].nunique() == 1                      # 5위 안이면 주도 테마 유지


def test_harvest_rebalancing():
    wk = pd.DataFrame({"A": [10, 15, 15, 9, 9.0], "B": [10, 10, 10, 10, 10.0]},
                      index=pd.date_range("2020-01-03", periods=5, freq="W-FRI"))
    t = {d: core.with_cash_buffer({"A": 0.5, "B": 0.5}, 0.2) for d in wk.index}
    assert abs(t[wk.index[0]]["CASH"] - 0.2) < 1e-9 and abs(t[wk.index[0]]["A"] - 0.4) < 1e-9
    sim = core.simulate_portfolio(wk, t, "harvest", band=0.25)
    w1 = sim["weights"].iloc[1]
    assert abs(w1["A"] - 0.4) < 1e-9 and w1["CASH"] > 0.2          # A가 50% 올라 초과분을 현금화
    w3 = sim["weights"].iloc[3]
    assert w3["A"] >= 0.4 - 1e-9                                       # 40% 떨어진 뒤 현금으로 다시 채움
    hold = pd.DataFrame({"ticker": ["A", "OLD"], "shares": [100.0, 1.0]})
    o = core.harvest_orders(hold, {"A": 10.0, "B": 10.0, "OLD": 50.0}, {"A": 0.4, "B": 0.4, "CASH": 0.2},
                            cash=0, new_cash=200).set_index("종목")
    assert o.loc["A", "주문"] == "매도" and o.loc["B", "주문"] == "매수" and o.loc["OLD", "주문"] == "유지"


def test_portfolio_method_champion():
    idx = pd.date_range("2026-11-27", periods=60, freq="W-FRI")
    curves = pd.DataFrame({"pf_harvest": np.linspace(1, 1.1, 60), "pf_month": np.linspace(1, 1.3, 60),
                           "bm_soxl": np.linspace(1, 2, 60)}, index=idx)
    v = core.portfolio_verdicts(curves, "pf_harvest")
    assert v["pf_month"][0] == "promote" and "bm_soxl" not in v
    short = core.portfolio_verdicts(curves.iloc[:10], "pf_harvest")
    assert short["pf_month"][0] == "short"
    assert core.PORTFOLIO_MODE[core.PORTFOLIO_CHAMPION] == "수익 실현형"


def test_principles_risk_tax_cycle():
    j = pd.DataFrame({"Date": ["2026-03-02", "2026-03-02", "2026-03-03", "2026-03-04", "2025-12-01"],
                      "Time": ["10:00:00"] * 5, "Account": ["A", "A", "A", "A", "A"], "Ticker": ["SOXL"] * 5,
                      "Action": ["Buy", "Buy", "Buy", "Sell", "Buy"], "Qty": [1.0, 1.0, 2.0, 2.0, 1.0],
                      "Price": [100.0, 100.0, 100.0, 120.0, 90.0], "Format": ["x"] * 5})
    amt = core.daily_buy_amounts(j, 1000)
    chk = core.check_buy_principle(amt, 250_000, 300_000)
    assert len(chk["over_hard"]) == 0 and chk["days"] == 3
    chk2 = core.check_buy_principle(amt, 150_000, 180_000)
    assert len(chk2["over_hard"]) == 2
    assert core.buy_qty_for_budget(35.0, 1350, 250_000, 300_000)["qty"] == 5
    real = core.realized_by_year(j, 2026).set_index("Account")
    assert real.loc["A", "realized_usd"] > 0 and real.loc["A", "sells"] == 1
    exp = core.exposure_table([{"ticker": "SOXL", "qty": 10, "price": 30}, {"ticker": "SPY", "qty": 1, "price": 600}])
    st_ = core.stress_test(exp, shocks=(-0.30,)).set_index("시나리오")
    assert st_.loc["반도체만 -30%", "예상 손실($)"] == -270 and st_.loc["시장 전체 -30%", "예상 손실($)"] == -450
    acc = pd.DataFrame({"Account": ["X", "Y"], "Qty": [38, 38], "Avg": [164.0, 160.0]})
    hp = core.harvest_plan(27_790_000, acc, 35.0, 1340)
    assert hp["taxable_krw"] == 25_290_000 and hp["shares"] == 76          # 두 계좌로는 모자람 → 전부
    hp2 = core.harvest_plan(5_000_000, acc, 35.0, 1340)
    assert hp2["covered_krw"] >= hp2["taxable_krw"] and hp2["shares"] == 15
    cs = core.cycle_status(38, 164.23, 26, 35.0, "①", 10_000_000, 250_000, 1340)
    assert cs["목표가($)"] == round(164.23 * 1.1, 2) and cs["남은 예산(원)"] >= 0


def test_scenario_engine():
    import numpy as np
    import pandas as pd
    import valhalla_core as core
    idx = pd.bdate_range("2025-06-02", periods=300)
    close = pd.Series(np.r_[np.linspace(60, 48, 100), np.full(100, 48.0), np.linspace(48, 33, 100)], index=idx)
    vol = pd.Series(np.r_[np.full(100, 1e6), np.full(100, 9e6), np.full(100, 1e6)], index=idx)
    df = pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99, "Close": close, "Volume": vol})
    vr = core.volume_resistance(df, 33.0)
    assert 47 < vr["resistance"] < 49            # 거래가 몰린 48 근처가 매물대 중심
    assert vr["res_low"] < 48 < vr["res_high"] and vr["res_high"] - vr["res_low"] < 4   # 좁은 띠
    s = core.scenario_settings({})
    assert core.pick_scenario(33, 30, 8500, 8409, 48, 34, 70)[0] == "B"
    assert core.pick_scenario(29, 30, 8500, 8409, 48, 34, 70)[0] == "A"
    assert core.pick_scenario(33, 30, 8300, 8409, 48, 34, 70)[0] == "A"
    assert core.pick_scenario(50, 30, 9000, 8409, 48, 49, 70)[0] == "C"
    assert core.pick_scenario(50, 30, 9000, 8409, 48, 46, 70)[0] == "B"   # 주간 종가 확인 전
    assert "띠" in core.pick_scenario(47, 30, 9000, 8409, 48, 46, 70, 46.5)[1]
    assert core.pick_scenario(75, 30, 9900, 8409, 48, 74, 70)[0] == "D"
    cal = [("2026-12-10 04:00", "FOMC 금리 결정", "", True), ("2026-12-10 15:00", "TSMC 매출", "", False)]
    g = core.event_gate(pd.Timestamp("2026-12-09 10:00"), 24, 12, cal)
    assert g["state"] == "wait" and "FOMC" in g["event"]
    assert core.event_gate(pd.Timestamp("2026-12-10 08:00"), 24, 12, cal)["state"] == "settle"
    assert core.event_gate(pd.Timestamp("2026-12-11 09:00"), 24, 12, cal)["state"] == "after"
    assert core.event_gate(pd.Timestamp("2026-12-20 09:00"), 24, 12, cal)["state"] == "clear"
    acts = core.scenario_actions("B", "wait", s, 33.0, 48.0, 250_000, 300_000, 1350)
    assert any("발표" in a for a in acts)
    cyc = pd.DataFrame([{"계좌": "a", "보유 수량": 38, "평단($)": 164.23}])
    acc = core.scenario_accounts(cyc, s, 33.0, 1350, 250_000)
    assert set(acc["다시 사기"]) == {"전부", "절반", "70%"}
    whole, half = acc.set_index("다시 사기").loc["전부"], acc.set_index("다시 사기").loc["절반"]
    accts = pd.DataFrame({"Account": list("abcdefgh"), "Qty": [38] * 8, "Avg": [164.23] * 8})
    sl = core.shares_for_loss(27_750_000, accts, 32.60, 1344)
    assert sl["shares"] == 157 and sl["covered_krw"] >= 27_750_000              # 사용자 예시: 2,775만 원 → 157주
    assert acc.set_index("다시 사기").loc["70%"]["보유(주)"] == 26
    assert whole["남은 총알(발)"] == 2 and half["남은 총알(발)"] > 2
    snap = core.scenario_snapshot(df, 8500.0, 8409.0, s, False, None, pd.Timestamp("2026-11-25 13:00"))
    assert snap["code"] in "ABCD" and len(snap["checks"]) >= 6
    gr = core.grade_scenarios(pd.Series({idx[100]: "B", idx[200]: "A"}), close)
    assert gr["맞음"].tolist() == [True, True]


def test_agent_players():
    import numpy as np
    import pandas as pd
    import valhalla_core as core
    rng = np.random.default_rng(1)
    idx = pd.bdate_range("2016-01-01", periods=900)
    c = pd.Series(30 * np.exp(np.cumsum(rng.normal(0, 0.04, len(idx)))), index=idx)
    d = pd.DataFrame({"Open": c, "High": c * 1.03, "Low": c * 0.97, "Close": c, "Volume": 1e6}, index=idx)
    core.set_agents([{"rank": 1, "genes": core.DEFAULT_GENES, "ticker": "SOXL", "trained_at": "2018-06-01", "years": 1}])
    assert core.player_label("agent_1") == "🤖 AI 요원 1위"
    e = core.agent_exposure(d, core.DEFAULT_GENES)
    assert e.dropna().between(0, 1).all() and e.dropna().max() > 0
    hist = pd.DataFrame({"close": c.resample("W-FRI").last()})
    wk = core.agent_positions(hist.index, d, "SOXL")
    assert "agent_1" in wk and wk["agent_1"].notna().sum() > 50
    assert core.agent_positions(hist.index, d, "TQQQ").empty
    pos = pd.DataFrame({"agent_1": wk["agent_1"], "hold": 1.0}, index=hist.index)
    segs = core.agent_segments(core.AGENT_PLAYERS["agent_1"], pd.Timestamp("2019-06-01"))
    assert [s_[0][:2] for s_ in segs] == ["훈련", "훈련", "훈련"]
    cmp_ = core.segment_compare(pos, hist["close"], ["agent_1", "hold"], segs)
    assert set(cmp_["id"]) == {"agent_1", "hold"} and cmp_["구간"].nunique() >= 2
    core.set_agents([])
