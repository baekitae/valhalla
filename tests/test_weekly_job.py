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

    def order(self, *_ , **__):
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def limit(self, n):
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
        if getattr(self, "rng", None) and self.op == "select":
            match = match[self.rng[0]: self.rng[1] + 1]
        if self.op == "insert":
            for item in (self.payload if isinstance(self.payload, list) else [self.payload]):
                rows.append({"id": len(rows) + 1, **{"ret_1w": None, "ret_4w": None, "ret_12w": None}, **item}
                            if self.table in ("valhalla_positions",) else {"id": len(rows) + 1, **item})
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


def test_champion_vs_challenger_live_signals():
    sb = FakeSB()
    d = _daily(n=3000)
    weekly.us_asof = lambda: pd.Timestamp(d.index[-1])
    weekly.TICKERS = [("SOXL", "SOXX")]
    weekly.core.load_active_rule(sb)
    # 항상 신호가 나도록 느슨한 도전자 (차트 0점 이상 1주, 거시 0점)
    loose = {"buy": {"chart_min": 0, "weeks": 1, "macro_min": 0}, "sell": {"chart_max": -1, "weeks": 1, "below_ma": False}}
    sb.db["valhalla_signal_rules"].append({"id": 50, "version": 2, "status": "challenger", "params": loose})
    assert [v for v, _ in weekly.core.load_challengers(sb)] == [2]
    # 과거 신호 하나를 넣어 두면 채점됨
    past = pd.Timestamp(d.index[-1]).to_period("W-FRI").end_time.normalize() - pd.Timedelta(weeks=10)
    sb.db["valhalla_rule_signals"] = [{"id": 1, "rule_version": 1, "ticker": "SOXL", "week": past.strftime("%Y-%m-%d"),
                                       "action": "매수", "close": 1.0, "ret_4w": None, "ret_12w": None}]
    msg = weekly.run(sb, lambda t, adj: d)
    assert "경쟁 v1 vs v2" in msg
    live = pd.DataFrame(sb.db["valhalla_rule_signals"])
    assert live.loc[live["id"] == 1, "ret_4w"].notna().all()
    weekly.run(sb, lambda t, adj: d)                       # 같은 주 재실행 → 중복 없음
    assert len(sb.db["valhalla_rule_signals"]) == len(live)
    table = weekly.core.rule_competition(pd.DataFrame(sb.db["valhalla_rule_signals"]), {1: "챔피언", 2: "도전자"})
    assert set(table["버전"]) <= {1, 2}
    verdicts = weekly.core.promotion_verdicts(table, 1)
    assert all("표본 부족" in v or "대기" in v or "승격" in v or "열세" in v or "접전" in v for v in verdicts.values())



def test_positions_recorded_graded_and_ensemble():
    sb = FakeSB()
    d = _daily(n=3000)
    weekly.TICKERS = [("SOXL", "SOXX")]
    end = pd.Timestamp(d.index[-1])
    # 2주 전 시점으로 한 번, 지금 시점으로 한 번 → 지난 기록이 채점되어야 함
    weekly.us_asof = lambda: end - pd.Timedelta(days=14)
    weekly.run(sb, lambda t, adj: d[d.index <= end - pd.Timedelta(days=14)])
    first = pd.DataFrame(sb.db["valhalla_positions"])
    assert {"rule_v1", "trend200", "voltarget", "hold", "ensemble"} <= set(first["player"])
    weekly.us_asof = lambda: end
    msg = weekly.run(sb, lambda t, adj: d)
    allp = pd.DataFrame(sb.db["valhalla_positions"])
    old_week = first["week"].iloc[0]
    assert allp[allp["week"] == old_week]["ret_1w"].notna().all(), msg
    assert "앙상블" in msg
    # 같은 날 재실행해도 중복 기록 없음
    n = len(sb.db["valhalla_positions"])
    weekly.run(sb, lambda t, adj: d)
    assert len(sb.db["valhalla_positions"]) == n


def test_any_player_can_become_champion():
    core = weekly.core
    sb = FakeSB()
    sb.table(core.RULE_TABLE).insert({"version": 1, "status": "active", "params": core.ACTION_RULES}).execute()
    assert core.load_champion(sb)["player"] == "rule_v1"          # 첫 챔피언 = 활성 규칙
    weeks = pd.date_range("2026-01-02", periods=25, freq="W-FRI").strftime("%Y-%m-%d")
    rows = []
    for i, w in enumerate(weeks):
        r = 2.0 if i % 2 else -2.0                                  # 오르고 내리는 장
        for t in ("AAA", "BBB"):
            rows += [{"player": "rule_v1", "ticker": t, "week": w, "position": 1.0, "ret_1w": r},
                     {"player": "trend200", "ticker": t, "week": w, "position": 1.0 if r > 0 else 0.0, "ret_1w": r},
                     {"player": "hold", "ticker": t, "week": w, "position": 1.0, "ret_1w": r},
                     {"player": "ensemble", "ticker": t, "week": w, "position": 0.0, "ret_1w": r}]
    graded = pd.DataFrame(rows)
    v = core.arena_verdicts(graded, "rule_v1")
    assert v["trend200"][0] == "promote"                            # 기준 선수도 승격 추천
    assert v["hold"][0] == "close" and v["ensemble"][0] in ("close", "behind")
    short = core.arena_verdicts(graded[graded["week"] <= weeks[5]], "rule_v1")
    assert short["trend200"][0] == "short"
    core.crown_champion(sb, "trend200", "test", "2026-06-01")
    assert core.load_champion(sb)["player"] == "trend200"
    assert [h["status"] for h in sb.db[core.CHAMPION_TABLE]].count("champion") == 1


def test_rule_promotion_carries_crown():
    core = weekly.core
    sb = FakeSB()
    sb.table(core.RULE_TABLE).insert({"version": 1, "status": "active", "params": core.ACTION_RULES}).execute()
    sb.table(core.RULE_TABLE).insert({"version": 2, "status": "challenger", "params": core.ACTION_RULES}).execute()
    core.load_champion(sb)
    core.decide_proposal(sb, 2, "accept", "2026-06-01")
    assert core.load_champion(sb)["player"] == "rule_v2"
