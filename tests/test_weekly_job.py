import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd

from tests.test_signals import _daily

spec = importlib.util.spec_from_file_location("weekly", Path(__file__).resolve().parents[1] / "jobs/weekly_signals.py")
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

    def update(self, patch):
        self.op, self.payload = "update", patch
        return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        match = [r for r in rows if all(str(r.get(c)) == str(v) for c, v in self.filters)]
        if self.op == "insert":
            rows.append({"id": len(rows) + 1, **self.payload})
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
    msg1 = weekly.run(sb, fetch)
    msg2 = weekly.run(sb, fetch)
    rows = sb.db["valhalla_predictions"]
    assert len(rows) == 2, msg1
    assert "이미 기록됨" in msg2
    assert rows[0]["ret_4w"] is not None and rows[0]["ret_12w"] is not None
