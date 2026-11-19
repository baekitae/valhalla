"""발할라 리그 자동 실행 (GitHub Actions, KST 화~토 07:00).

배치된 요원을 리그 설정(종목·시작일·자본·비용)대로 오늘까지 매매하고
  1) ai_ledger 의 해당 종목 장부를 최신 체결로 교체
  2) valhalla_league_daily 에 날짜별 요원 자산·수익률·MDD 기록 (실전 검증 이력)
  3) valhalla_job_runs 에 실행 결과 기록

로컬 실행:  SUPABASE_URL=... SUPABASE_KEY=... python jobs/daily_league.py
"""
from __future__ import annotations

import datetime as dt
import json
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

KST = ZoneInfo("Asia/Seoul")


def get_client():
    from supabase import create_client
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise SystemExit("SUPABASE_URL / SUPABASE_KEY 환경변수가 필요합니다.")
    return create_client(url, key)


def load_models(sb) -> list[dict]:
    models = []
    for order, row in enumerate(sb.table("valhalla_ai_models").select("*").execute().data or []):
        value = row["genes"]
        for _ in range(2):
            if isinstance(value, str):
                value = json.loads(value)
        meta = value if isinstance(value, dict) else {"genes": value}
        models.append({**meta, "name": row["agent_name"], "genes": core.pad_genes(meta.get("genes", [])),
                       "rank": int(meta.get("rank") or order + 1)})
    return sorted(models, key=lambda m: (m["rank"], m["name"]))


def download(ticker: str, period: str) -> pd.DataFrame | None:
    raw = yf.download(ticker, period=period, interval="1d", auto_adjust=False, progress=False, threads=False)
    return core.clean_ohlcv(raw)


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", force_ascii=False)) if not df.empty else []


def run(sb, models: list[dict], fetch=download) -> str:
    groups: dict[tuple, list[dict]] = {}
    for m in models:
        ticker = m.get("league_ticker") or m.get("ticker") or "SOXL"
        start = m.get("league_start") or m.get("trained_at") or (dt.date.today() - dt.timedelta(days=180)).isoformat()
        key = (ticker, start, float(m.get("league_capital", 10_000.0)), float(m.get("league_fee", 10)))
        groups.setdefault(key, []).append(m)

    lines = []
    for (ticker, start, capital, fee), ms in groups.items():
        df = fetch(ticker, core.league_period(start))
        if df is None:
            lines.append(f"{ticker}: 시세 없음")
            continue
        res = core.league_run(df, start, capital, fee, [m["genes"] for m in ms], [m["name"] for m in ms], ticker)
        if res is None:
            lines.append(f"{ticker}: 시작일({start}) 이후 데이터 없음")
            continue
        # 1) 장부 교체 (해당 종목만)
        trades = core.validate_table("ai_ledger", res["trades"]) if not res["trades"].empty else res["trades"]
        sb.table("ai_ledger").delete().eq("Ticker", ticker).execute()
        recs = _records(trades)
        for i in range(0, len(recs), 500):
            sb.table("ai_ledger").insert(recs[i:i + 500]).execute()
        # 2) 일별 성적 기록
        curve = res["curve"]
        peak = curve.cummax()
        mdd = ((1 - curve / peak) * 100)
        bh = (curve["Buy & Hold"] / capital - 1) * 100
        daily = []
        for name in [m["name"] for m in ms]:
            for d, eq in curve[name].items():
                daily.append({"date": f"{d:%Y-%m-%d}", "agent": name, "ticker": ticker, "equity": round(float(eq), 2),
                              "return_pct": round(float(eq / capital - 1) * 100, 3),
                              "mdd_pct": round(float(mdd[name].loc[:d].max()), 3),
                              "bh_return_pct": round(float(bh.loc[d]), 3)})
        for i in range(0, len(daily), 500):
            sb.table("valhalla_league_daily").upsert(daily[i:i + 500], on_conflict="date,agent").execute()
        best = res["summary"].sort_values("수익률(%)", ascending=False).iloc[0]
        lines.append(f"{ticker}: {res['start']}~{res['end']} {res['days']}일 · 체결 {len(trades)}건 · "
                     f"1위 {best['요원']} {best['수익률(%)']:+.2f}% (보유 {res['bh_ret']:+.2f}%)")
    return " / ".join(lines) if lines else "배치된 요원 없음"


def main() -> int:
    sb = get_client()
    ran_at = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    try:
        summary = run(sb, load_models(sb))
        code = 0
    except Exception as exc:
        traceback.print_exc()
        summary, code = f"실패: {exc}", 1
    print(summary)
    try:
        sb.table("valhalla_job_runs").insert({"ran_at": ran_at, "source": "auto", "summary": summary}).execute()
    except Exception:
        traceback.print_exc()
    return code


if __name__ == "__main__":
    np.seterr(all="ignore")
    sys.exit(main())
