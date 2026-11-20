"""신호 규칙 월간 점검 (GitHub Actions, 매달 1일 KST 09:10).

후보 규칙 432개를 53개 종목에 적용해서
  1) 학습 구간(최근 2년 제외)으로만 가장 좋은 후보 1개를 고르고
  2) 고를 때 쓰지 않은 최근 2년에서 지금 규칙보다 '확실히' 나을 때만
  3) valhalla_signal_rules 에 '제안'으로 기록합니다. 적용은 발할라 화면에서 사람이 승인합니다.

로컬 실행:  SUPABASE_URL=... SUPABASE_KEY=... python jobs/rule_review.py
"""
from __future__ import annotations

import datetime as dt
import sys
import traceback
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import valhalla_core as core  # noqa: E402
import daily_signals as daily  # noqa: E402

KST = ZoneInfo("Asia/Seoul")


def run(sb, fetch=daily.download, candidates=None) -> str:
    version, current = core.load_active_rule(sb)
    pending = core.pending_proposal(sb)
    if pending:
        return f"규칙 v{version} 유지 · 승인 대기 중인 제안 v{pending['version']}이 있어 새 점검을 건너뜀"
    histories = daily.load_histories(fetch)
    res = core.review_rules(histories, current, candidates)
    cur, cand = res["current_test"], res["candidate_test"] or {}
    head = (f"규칙 v{version} 점검 ({len(histories)}개 종목) · 지금 규칙 검증 구간: 평소보다 나은 종목 "
            f"{cur.get('plus_share')}%, 평균 차이 {cur.get('avg_diff')}%p")
    if res["adopt"]:
        new_v = core.save_proposal(sb, res)
        return f"{head} / 새 규칙 v{new_v} 제안: {core.rule_text(res['candidate'])['매수']} · " + " · ".join(res["reasons"])
    tail = f"최고 후보 검증: {cand.get('plus_share')}%, {cand.get('avg_diff')}%p" if cand else "후보 없음"
    return f"{head} / 교체 안 함 ({tail}): " + " · ".join(res["reasons"])


def main() -> int:
    sb = daily.get_client()
    ran_at = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    try:
        summary, code = run(sb), 0
    except Exception as exc:
        traceback.print_exc()
        summary, code = f"규칙 점검 실패: {exc}", 1
    print(summary)
    try:
        sb.table("valhalla_job_runs").insert({"ran_at": ran_at, "source": "rules", "summary": summary}).execute()
    except Exception:
        traceback.print_exc()
    return code


if __name__ == "__main__":
    np.seterr(all="ignore")
    sys.exit(main())
