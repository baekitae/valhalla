import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from tests.test_signals import _daily

spec = importlib.util.spec_from_file_location("daily", Path(__file__).resolve().parents[1] / "jobs/daily_signals.py")
weekly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(weekly)


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table, self.filters, self.op, self.payload = db, table, [], "select", None

    def select(self, *_):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def insert(self, row):
        self.op, self.payload = "insert", row
        return self

    def upsert(self, rows, on_conflict=None):
        self.op, self.payload = "upsert", rows
        return self

    def update(self, patch):
        self.op, self.payload = "update", patch
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        match = [r for r in rows if all(str(r.get(c)) == str(v) for c, v in self.filters)]
        if self.op == "insert":
            rows.append({"id": len(rows) + 1, **self.payload})
        elif self.op == "upsert":
            keys = (on_keys := ("date", "ticker"))
            for r in self.payload:
                rows[:] = [x for x in rows if tuple(x.get(k) for k in keys) != tuple(r.get(k) for k in keys)]
                rows.append(r)
        elif self.op == "update":
            for r in match:
                r.update(self.payload)
        return type("R", (), {"data": match})()


class FakeSB:
    def __init__(self):
        self.db = {}

    def table(self, name):
        return FakeQuery(self.db, name)


def test_job_records_once_and_grades():
    d = _daily(n=3000)
    end = d.index[-1]
    fetch = lambda t, adj: d  # noqa: E731
    sb = FakeSB()
    # 과거 예측 하나를 넣어 두면 채점되어야 함
    week = pd.Timestamp(end).to_period("W-FRI").end_time.normalize() - pd.Timedelta(weeks=20)
    sb.db["valhalla_predictions"] = [{"id": 1, "week": week.strftime("%Y-%m-%d"), "ticker": "SOXL",
                                       "p_up_4w": 55.0, "ret_4w": None, "ret_12w": None}]
    weekly.us_asof = lambda: pd.Timestamp(end)
    weekly.TICKERS = [("SOXL", "SOXX")]
    msg1 = weekly.run(sb, fetch)
    msg2 = weekly.run(sb, fetch)
    rows = sb.db["valhalla_predictions"]
    assert len(rows) == 2, msg1                     # 매일 돌아도 주 1건
    assert "새 예측 0건" in msg2
    reports = sb.db["valhalla_signal_reports"]
    assert len(reports) == 1 and reports[0]["ticker"] == "SOXL"   # 같은 날 재실행 → 갱신
    assert rows[0]["ret_4w"] is not None and rows[0]["ret_12w"] is not None


def test_default_universe_covers_all_categories():
    cats = {c for _, c, _ in weekly.core.SIGNAL_UNIVERSE.values()}
    secs = {s for _, _, s in weekly.core.SIGNAL_UNIVERSE.values()}
    assert len(secs) >= 10
    assert set(weekly.core.CATEGORIES) <= cats
    assert len(weekly._DEFAULT.split(",")) == len(weekly.core.SIGNAL_UNIVERSE)


def test_one_failing_ticker_does_not_stop_others():
    sb = FakeSB()
    d = _daily(n=3000)
    weekly.us_asof = lambda: pd.Timestamp(d.index[-1])
    weekly.TICKERS = [("BAD", "SOXX"), ("SOXL", "SOXX")]

    def fetch(t, adj):
        if t == "BAD":
            raise RuntimeError("boom")
        return d
    msg = weekly.run(sb, fetch)
    assert "BAD(" in msg and len(sb.db["valhalla_predictions"]) == 1


def test_rule_review_job_proposes_or_keeps():
    import importlib.util as u
    spec2 = u.spec_from_file_location("rule_review", Path(__file__).resolve().parents[1] / "jobs/rule_review.py")
    rr = u.module_from_spec(spec2)
    spec2.loader.exec_module(rr)
    sb = FakeSB()
    d = _daily(n=3000)
    rr.daily.us_asof = lambda: pd.Timestamp(d.index[-1])
    rr.daily.TICKERS = [("SOXL", "SOXX"), ("TQQQ", "QQQ")]
    msg = rr.run(sb, lambda t, adj: d, candidates=weekly.core.candidate_rules()[:6])
    rules = sb.db["valhalla_signal_rules"]
    assert rules[0]["status"] == "active" and rules[0]["version"] == 1
    assert ("제안" in msg) or ("교체 안 함" in msg)
    # 승인 대기 중이면 새 점검을 건너뜀
    sb.db["valhalla_signal_rules"].append({"id": 99, "version": 2, "status": "proposed", "params": {}})
    assert "건너뜀" in rr.run(sb, lambda t, adj: d, candidates=[])
    # 승인하면 이전 활성 규칙은 retired
    weekly.core.decide_proposal(sb, 99, True, "2026-11-20T00:00:00")
    status = {r["version"]: r["status"] for r in sb.db["valhalla_signal_rules"]}
    assert status[1] == "retired" and status[2] == "active"
