"""신호 관제 주간 자동 실행 (GitHub Actions, KST 토 07:20 = 미국 금요일 장 마감 후).

  1) 마감된 주봉으로 반등 신호 점수를 계산해 valhalla_predictions 에 예측 1건 기록
     (같은 주가 이미 있으면 덮어쓰지 않음 → 예측은 사후에 고치지 않는다)
  2) 지난 예측 중 4·12주가 지난 건 실제 수익률로 채점
  3) valhalla_job_runs 에 실행 결과 기록

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
TICKERS = [(t.strip().upper(), p.strip().upper()) for t, p in
           (pair.split(":") for pair in os.environ.get("SIGNAL_TICKERS", "SOXL:SOXX").split(",") if ":" in pair)]


def get_client():
    from supabase import create_client
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_KEY 환경변수가 필요합니다.")
    return create_client(url, key)


def download(ticker: str, adjusted: bool) -> pd.DataFrame | None:
    raw = yf.download(ticker, period="max", interval="1d", auto_adjust=adjusted, progress=False, threads=False)
    return core.clean_ohlcv(raw)


def us_asof() -> pd.Timestamp:
    now = dt.datetime.now(ET)
    day = pd.Timestamp(now.date())
    return day if now.hour >= 16 else day - pd.Timedelta(days=1)


def run(sb, fetch=download) -> str:
    macro = {t: fetch(t, False) for t in core.MACRO_TICKERS_SIGNAL.values()}
    lines = []
    for ticker, partner in TICKERS:
        daily = fetch(ticker, True)
        if daily is None:
            lines.append(f"{ticker}: 시세 없음")
            continue
        hist = core.complete_weeks(core.build_signal_history(daily, fetch(partner, True) if partner else None, macro),
                                   us_asof())
        pred = core.make_prediction(hist, ticker)
        existing = (sb.table(TABLE).select("id").eq("ticker", ticker).eq("week", pred["week"]).execute().data or [])
        if existing:
            lines.append(f"{ticker} {pred['week']}: 이미 기록됨")
        else:
            sb.table(TABLE).insert(pred).execute()
            lines.append(f"{ticker} {pred['week']}: 차트 {pred['chart_score']}점·거시 {pred['macro_score']}점 → "
                         f"4주 상승확률 {pred['p_up_4w']}%")
        ledger = pd.DataFrame(sb.table(TABLE).select("*").eq("ticker", ticker).execute().data or [])
        if ledger.empty:
            continue
        graded = core.grade_predictions(ledger, hist["close"])
        n = 0
        for (_, old), (_, new) in zip(ledger.iterrows(), graded.iterrows()):
            patch = {c: float(new[c]) for c in ("ret_4w", "ret_12w")
                     if pd.notna(new.get(c)) and pd.isna(old.get(c))}
            if patch:
                sb.table(TABLE).update(patch).eq("id", int(old["id"])).execute()
                n += 1
        if n:
            lines.append(f"{ticker}: {n}건 채점")
    return " / ".join(lines) or "대상 종목 없음"


def main() -> int:
    sb = get_client()
    ran_at = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    try:
        summary, code = "신호 관제: " + run(sb), 0
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
