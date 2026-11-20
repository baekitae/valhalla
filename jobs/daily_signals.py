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
    reports, new_preds, graded, failed, live_new = [], 0, 0, [], 0
    for ticker, partner in TICKERS:
        try:
            res = _run_one(sb, ticker, partner, macro, partners, fetch, asof, rules, rule_version, contenders)
            live_new += res.get("live_new", 0)
            new_preds += res["new"]
            graded += res["graded"]
            if res["report"]:
                reports.append(res["report"])
        except Exception as exc:
            traceback.print_exc()
            failed.append(f"{ticker}({exc.__class__.__name__})")
    for i in range(0, len(reports), 100):
        sb.table(core.REPORT_TABLE).upsert(reports[i:i + 100], on_conflict="date,ticker").execute()
    rep = pd.DataFrame(reports)
    share = float((rep["test_diff"].dropna() > 0).mean() * 100) if not rep.empty and rep["test_diff"].notna().any() else None
    lines.append(f"규칙 v{rule_version} · {len(TICKERS)}개 중 {len(reports)}개 점검 · 새 예측 {new_preds}건 · 채점 {graded}건")
    lines.append("경쟁 " + " vs ".join(f"v{v}" for v, _ in contenders) + f" · 새 실전 신호 {live_new}건")
    if share is not None:
        lines.append(f"검증 구간에서 평소보다 나은 종목 {share:.0f}%")
    if failed:
        lines.append("실패: " + ", ".join(failed[:10]))
    return " / ".join(lines)


def _run_one(sb, ticker, partner, macro, partners, fetch, asof, rules=None, rule_version=None,
             contenders=None) -> dict:
    out = {"new": 0, "graded": 0, "report": None, "live_new": 0}
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
