"""Valhalla Trading Dashboard.

Educational portfolio journal and market-monitoring dashboard.  This app does
not place orders and its analytics are not investment advice.

Run: streamlit run valhalla_dashboard_pro.py
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf


st.set_page_config(page_title="Valhalla | Trading Journal", page_icon="📈", layout="wide")

APP_TITLE: Final = "Valhalla Trading Journal"
DEFAULT_TICKER: Final = "SOXL"
DATA_DIR: Final = Path(os.getenv("VALHALLA_DATA_DIR", "data"))
JOURNAL_FILE: Final = DATA_DIR / "trade_journal.csv"
JOURNAL_COLUMNS: Final = ["id", "date", "time", "account", "ticker", "side", "quantity", "price", "source"]
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionSummary:
    quantity: float
    average_cost: float
    realized_pnl: float


def normalize_ticker(value: str) -> str:
    """Return a validated US-style ticker symbol."""
    ticker = value.strip().upper()
    if not re.fullmatch(r"[A-Z.\-]{1,15}", ticker):
        raise ValueError("티커는 영문 대문자, 마침표, 하이픈만 사용할 수 있습니다.")
    return ticker


def empty_journal() -> pd.DataFrame:
    return pd.DataFrame(columns=JOURNAL_COLUMNS)


def ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not JOURNAL_FILE.exists():
        empty_journal().to_csv(JOURNAL_FILE, index=False, encoding="utf-8-sig")


def validate_journal(frame: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize journal data before it is saved or analyzed."""
    missing = set(JOURNAL_COLUMNS) - set(frame.columns)
    if missing:
