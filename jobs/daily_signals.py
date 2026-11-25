"""신호 관제 매일 자동 점검 (GitHub Actions, KST 화~토 07:30 = 미국 장 마감 후).

  1) 마감된 주봉으로 반등 신호 점수를 계산해 valhalla_predictions 에 주당 예측 1건 기록
     (같은 주가 이미 있으면 덮어쓰지 않음 → 예측은 사후에 고치지 않는다. 매일 돌아도 주 1건)
  2) 지난 예측 중 4·12주가 지난 건 실제 수익률로 채점
  3) 53개 종목 섹터·유형별 비교 결과를 valhalla_signal_reports 에 날짜별로 저장 (매일 스스로 비교)
  4) valhalla_job_runs 에 실행 결과 기록

로컬 실행:  SUPABASE_URL=... SUPABASE_KEY=... python jobs/weekly_signals.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import valhalla_core as core  # noqa: E402

KST, ET = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")
TABLE = "valhalla_predictions"
_DEFAULT = ",".join(f"{t}:{p}" for t, (p, _, _) in core.SIGNAL_UNIVERSE.items())   # 섹터 × (3배·2배·1배 ETF·개별 종목)
TICKERS = [(t.strip().upper(), p.strip().upper()) for t, p in
           (pair.split(":") for pair in (os.environ.get("SIGNAL_TICKERS") or _DEFAULT).split(",") if ":" in pair)]


def get_client():
    from supabase import create_client
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_KEY 환경변수가 필요합니다.")
    return create_client(url, key)


def download(ticker: str, adjusted: bool) -> pd.DataFrame | None:
    raw = yf.download(ticker, period="max", interval="1d", auto_adjust=adjusted, progress=False, threads=False)
    return core.clean_ohlcv(raw)


def load_histories(fetch=download) -> dict[str, pd.DataFrame]:
    """규칙 점검용: 모든 대상 종목의 마감 주봉 신호 이력."""
    macro = {t: fetch(t, False) for t in core.MACRO_TICKERS_SIGNAL.values()}
    partners, out, asof = {}, {}, us_asof()
    for ticker, partner in TICKERS:
        try:
            daily = fetch(ticker, True)
            if daily is None:
                continue
            if partner and partner not in partners:
                partners[partner] = fetch(partner, True)
            out[ticker] = core.complete_weeks(core.build_signal_history(daily, partners.get(partner), macro), asof)
        except Exception:
            traceback.print_exc()
    return out


def us_asof() -> pd.Timestamp:
    now = dt.datetime.now(ET)
    day = pd.Timestamp(now.date())
    return day if now.hour >= 16 else day - pd.Timedelta(days=1)


def run(sb, fetch=download) -> str:
    macro = {t: fetch(t, False) for t in core.MACRO_TICKERS_SIGNAL.values()}
    lines = []
    partners: dict[str, pd.DataFrame | None] = {}
    asof = us_asof()
    rule_version, rules = core.load_active_rule(sb)
    contenders = [(rule_version, rules)] + core.load_challengers(sb)
    day = asof.strftime("%Y-%m-%d")
    reports, new_preds, graded, failed, live_new, daily_new = [], 0, 0, [], 0, 0
    for ticker, partner in TICKERS:
        try:
            res = _run_one(sb, ticker, partner, macro, partners, fetch, asof, rules, rule_version, contenders)
            live_new += res.get("live_new", 0)
            daily_new += res.get("daily", 0) or 0
            new_preds += res["new"]
            graded += res["graded"]
            if res["report"]:
                reports.append(res["report"])
        except Exception as exc:
            traceback.print_exc()
            failed.append(f"{ticker}({exc.__class__.__name__})")
    for i in range(0, len(reports), 100):
        sb.table(core.REPORT_TABLE).upsert(reports[i:i + 100], on_conflict="date,ticker").execute()
    try:
        n_ens, weights = update_ensemble(sb, asof)
        lines.append(f"앙상블 {n_ens}건 · 가중치 " + ", ".join(f"{core.player_label(p)} {w:.0%}" for p, w in
                                                         sorted(weights.items(), key=lambda x: -x[1])))
    except Exception as exc:
        traceback.print_exc()
        lines.append(f"앙상블 실패: {exc}")
    try:
        lines.append(update_rebounds(sb, fetch, asof))
    except Exception as exc:
        traceback.print_exc()
        lines.append(f"반등 판별전 실패: {exc}")
    rep = pd.DataFrame(reports)
    share = float((rep["test_diff"].dropna() > 0).mean() * 100) if not rep.empty and rep["test_diff"].notna().any() else None
    lines.append(f"규칙 v{rule_version} · {len(TICKERS)}개 중 {len(reports)}개 점검 · 새 예측 {new_preds}건 · 채점 {graded}건")
    lines.append("경쟁 " + " vs ".join(f"v{v}" for v, _ in contenders) + f" · 새 실전 신호 {live_new}건")
    lines.append(f"일봉 리그 새 기록 {daily_new}건")
    if share is not None:
        lines.append(f"검증 구간에서 평소보다 나은 종목 {share:.0f}%")
    if failed:
        lines.append("실패: " + ", ".join(failed[:10]))
    return " / ".join(lines)


def update_rebounds(sb, fetch, asof) -> str:
    """반등 판별전: SOX 반등 사건을 찾아 기록하고, 13주가 지났거나 저점을 깬 사건을 채점."""
    df = fetch("^SOX", False)
    if df is None or df.empty:
        df = fetch("SOXX", True)
    close = df["Close"].dropna()
    close = close[close.index <= asof]
    events = core.detect_rebounds(close)
    if events.empty:
        return "반등 판별전 · 사건 없음"
    lab = core.label_rebounds(events, close)
    lab["base_rate"] = core.rebound_base_rates(lab)
    lab["macro_prob"] = np.nan
    try:
        tnx, vix = fetch("^TNX", False), fetch("^VIX", False)
        credit = pd.read_csv(core.FRED_CSV.format(sid=core.FRED_SERIES["baa"]), na_values=".")
        credit = pd.to_numeric(credit.set_index(pd.to_datetime(credit.iloc[:, 0])).iloc[:, 1], errors="coerce").dropna()
        ck = core.check_conditions(tnx["Close"], vix["Close"], credit)
        lab["macro_prob"] = core.check_calls_at(ck, lab["week"]).to_numpy()
    except Exception:
        traceback.print_exc()
    last_done = core._friday(asof) if asof.weekday() == 4 else core._friday(asof) - pd.Timedelta(weeks=1)
    live = lab[(lab["week"] >= pd.Timestamp(core.REBOUND_LIVE_START)) & (lab["week"] <= last_done)]
    rows = []
    for _, r in live.iterrows():
        rows.append({"week": r["week"].strftime("%Y-%m-%d"), "event_date": r["event_date"].strftime("%Y-%m-%d"),
                     "trigger": r["trigger"], "jump": r["jump"], "prior_low": round(r["prior_low"], 2),
                     "depth": r["depth"], "judge_close": None if pd.isna(r["judge_close"]) else round(r["judge_close"], 2),
                     "base_rate": float(r["base_rate"]),
                     "macro_prob": None if pd.isna(r["macro_prob"]) else round(float(r["macro_prob"]), 3),
                     "outcome": None if pd.isna(r["outcome"]) else int(r["outcome"]),
                     "resolved_on": None if r["resolved_on"] is None or pd.isna(r["resolved_on"])
                     else pd.Timestamp(r["resolved_on"]).strftime("%Y-%m-%d")})
    if rows:
        sb.table(core.REBOUND_TABLE).upsert(rows, on_conflict="week").execute()
    hist = lab.dropna(subset=["outcome"])
    share = f" · 과거 {len(hist)}건 중 추세 전환 {hist['outcome'].mean() * 100:.0f}%" if len(hist) else ""
    if lab["macro_prob"].notna().any():
        calls = pd.DataFrame({core.MACRO_CHECK_PLAYER: lab["macro_prob"].to_numpy(),
                              core.REBOUND_BASE_PLAYER: lab["base_rate"].to_numpy()}, index=pd.DatetimeIndex(lab["week"]))
        board = core.rebound_scoreboard(lab, calls).set_index("id")
        if core.MACRO_CHECK_PLAYER in board.index:
            m = board.loc[core.MACRO_CHECK_PLAYER]
            share += (f" · 거시 체크리스트 {int(m['사건 수'])}건 브라이어 {m['브라이어']:.3f}(기준 대비 {m['기준 대비']:+.3f})"
                      f" 적중 {m['적중률(%)']:.0f}% · 최근 사건 확률 {lab['macro_prob'].iloc[-1] * 100:.0f}%")
    pending = live[live["outcome"].isna()]
    return f"반등 판별전 · 실전 사건 {len(live)}건(대기 {len(pending)}){share}"


def update_ensemble(sb, asof) -> tuple[int, dict]:
    """채점 전(다음 주 결과가 나오기 전)인 주에 대해서만 앙상블 비중을 계산·갱신 → 미래 정보 누수 없음.
    AI 위원회가 토요일에 늦게 기록해도, 다음 날 점검에서 앙상블에 반영됩니다."""
    allp = pd.DataFrame(core.sb_fetch_all(sb, core.POSITION_TABLE))
    if allp.empty:
        return 0, {}
    weights = core.ensemble_weights(allp, asof)
    open_rows = allp[allp["ret_1w"].isna() & (allp["player"] != "ensemble")]
    n = 0
    for (ticker, week), g in open_rows.groupby(["ticker", "week"]):
        members = {r["player"]: r["position"] for _, r in g.iterrows()}
        val = core.ensemble_position(members, weights)
        if val is None:
            continue
        ex = allp[(allp["player"] == "ensemble") & (allp["ticker"] == ticker) & (allp["week"] == week)]
        note = ", ".join(f"{p}:{weights[p]:.2f}" for p in sorted(members) if p in weights)
        if ex.empty:
            sb.table(core.POSITION_TABLE).insert({"player": "ensemble", "ticker": ticker, "week": week, "position": val,
                                                  "close": float(g["close"].dropna().iloc[0]) if g["close"].notna().any() else None,
                                                  "note": note}).execute()
        elif pd.isna(ex["ret_1w"].iloc[0]):
            sb.table(core.POSITION_TABLE).update({"position": val, "note": note}).eq("id", int(ex["id"].iloc[0])).execute()
        n += 1
    return n, weights


def _record_daily(sb, ticker, daily, partner_daily, macro, ref, contenders, asof) -> int:
    """일봉 리그: 실전 시작일 이후 장 마감된 날마다 선수별 비중을 기록(없는 날만), 다음 날 수익률로 채점."""
    d = daily[daily.index <= asof]
    start = pd.Timestamp(core.DAILY_LIVE_START)
    if d.empty or d.index[-1] < start:
        return 0
    dh = core.build_daily_signal_history(d, partner_daily, macro)
    pos = core.daily_player_positions(d, ref, dh, contenders, ticker)
    pos = pos[pos.index >= start].tail(10)
    have = {(r["player"], str(r["day"])[:10]) for r in core.sb_fetch_all(sb, core.DAILY_TABLE, {"ticker": ticker})}
    rows = [{"player": pid, "ticker": ticker, "day": day.strftime("%Y-%m-%d"), "position": float(v),
             "close": round(float(d["Close"].loc[day]), 4)}
            for day, r in pos.iterrows() for pid, v in r.items()
            if pd.notna(v) and (pid, day.strftime("%Y-%m-%d")) not in have]
    if rows:
        sb.table(core.DAILY_TABLE).insert(rows).execute()
    allrows = pd.DataFrame(core.sb_fetch_all(sb, core.DAILY_TABLE, {"ticker": ticker}))
    if not allrows.empty:
        todo = allrows[allrows["ret_1d"].isna()]
        graded = core.grade_daily(todo, d["Close"])
        for (_, old), (_, new) in zip(todo.iterrows(), graded.iterrows()):
            if pd.notna(new.get("ret_1d")):
                sb.table(core.DAILY_TABLE).update({"ret_1d": float(new["ret_1d"])}).eq("id", int(old["id"])).execute()
    return len(rows)


def _run_one(sb, ticker, partner, macro, partners, fetch, asof, rules=None, rule_version=None,
             contenders=None) -> dict:
    out = {"new": 0, "graded": 0, "report": None, "live_new": 0, "positions": 0}
    daily = fetch(ticker, True)
    if daily is None:
        raise RuntimeError("시세 없음")
    if partner and partner not in partners:
        partners[partner] = fetch(partner, True)
    hist = core.complete_weeks(core.build_signal_history(daily, partners.get(partner), macro), asof)
    pred = core.make_prediction(hist, ticker)
    existing = (sb.table(TABLE).select("id").eq("ticker", ticker).eq("week", pred["week"]).execute().data or [])
    if not existing:
        sb.table(TABLE).insert(pred).execute()
        out["new"] = 1
    ledger = pd.DataFrame(sb.table(TABLE).select("*").eq("ticker", ticker).execute().data or [])
    if not ledger.empty:
        graded = core.grade_predictions(ledger, hist["close"])
        for (_, old), (_, new) in zip(ledger.iterrows(), graded.iterrows()):
            patch = {c: float(new[c]) for c in ("ret_4w", "ret_12w")
                     if pd.notna(new.get(c)) and pd.isna(old.get(c))}
            if patch:
                sb.table(TABLE).update(patch).eq("id", int(old["id"])).execute()
                out["graded"] += 1
    # 챔피언·도전자 규칙의 실전 신호 기록 (마감 주에 신호가 난 경우만, 중복 없이)
    for version, rule in (contenders or []):
        row = core.live_rule_row(core.action_signals(hist, rule), ticker, version)
        if row:
            dup = (sb.table(core.RULE_SIGNAL_TABLE).select("id").eq("rule_version", version).eq("ticker", ticker)
                   .eq("week", row["week"]).eq("action", row["action"]).execute().data or [])
            if not dup:
                sb.table(core.RULE_SIGNAL_TABLE).insert(row).execute()
                out["live_new"] += 1
    live = pd.DataFrame(sb.table(core.RULE_SIGNAL_TABLE).select("*").eq("ticker", ticker).execute().data or [])
    if not live.empty:
        graded_live = core.grade_predictions(live, hist["close"])
        for (_, old), (_, new) in zip(live.iterrows(), graded_live.iterrows()):
            patch = {c: float(new[c]) for c in ("ret_4w", "ret_12w")
                     if c in new and pd.notna(new.get(c)) and pd.isna(old.get(c))}
            if patch:
                sb.table(core.RULE_SIGNAL_TABLE).update(patch).eq("id", int(old["id"])).execute()
    # 선수 경기장: 계산 가능한 선수들의 이번 주 비중 기록 + 지난 기록 채점
    ref = core.trend_reference(ticker, daily, partners.get(partner))
    pos = core.player_positions(hist, daily, ref, contenders or [], ticker)
    week = hist.index[-1].strftime("%Y-%m-%d")
    have = {r["player"] for r in core.sb_fetch_all(sb, core.POSITION_TABLE, {"ticker": ticker, "week": week})}
    new_rows = [{"player": pid, "ticker": ticker, "week": week, "position": round(float(v), 4),
                 "close": round(float(hist["close"].iloc[-1]), 4)}
                for pid, v in pos.iloc[-1].items() if pd.notna(v) and pid not in have]
    if new_rows:
        sb.table(core.POSITION_TABLE).insert(new_rows).execute()
        out["positions"] = len(new_rows)
    prow = pd.DataFrame(core.sb_fetch_all(sb, core.POSITION_TABLE, {"ticker": ticker}))
    if not prow.empty:
        todo = prow[prow["ret_1w"].isna()]
        graded_p = core.grade_positions(todo, hist["close"])
        for (_, old), (_, new) in zip(todo.iterrows(), graded_p.iterrows()):
            if pd.notna(new.get("ret_1w")):
                sb.table(core.POSITION_TABLE).update({"ret_1w": float(new["ret_1w"])}).eq("id", int(old["id"])).execute()
    try:
        out["daily"] = _record_daily(sb, ticker, daily, partners.get(partner), macro, ref, contenders or [], asof)
    except Exception:
        traceback.print_exc()
    out["report"] = core.report_row(core.action_signals(hist, rules), ticker, asof.strftime("%Y-%m-%d"))
    if out["report"] is not None:
        out["report"]["rule_version"] = rule_version
    return out


def main() -> int:
    sb = get_client()
    ran_at = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    try:
        summary, code = "신호 관제 점검: " + run(sb), 0
    except Exception as exc:
        traceback.print_exc()
        summary, code = f"신호 관제 실패: {exc}", 1
    print(summary)
    try:
        sb.table("valhalla_job_runs").insert({"ran_at": ran_at, "source": "signals", "summary": summary}).execute()
    except Exception:
        traceback.print_exc()
    return code


if __name__ == "__main__":
    np.seterr(all="ignore")
    sys.exit(main())
