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
        raise ValueError(f"필수 열이 없습니다: {', '.join(sorted(missing))}")

    result = frame[JOURNAL_COLUMNS].copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.date.astype("string")
    result["time"] = result["time"].fillna("00:00:00").astype(str)
    result["ticker"] = result["ticker"].astype(str).str.strip().str.upper()
    result["side"] = result["side"].astype(str).str.title()
    result["quantity"] = pd.to_numeric(result["quantity"], errors="coerce")
    result["price"] = pd.to_numeric(result["price"], errors="coerce")

    valid = (
        result["date"].notna()
        & result["ticker"].str.fullmatch(r"[A-Z.\-]{1,15}", na=False)
        & result["side"].isin(["Buy", "Sell"])
        & result["quantity"].gt(0)
        & result["price"].gt(0)
    )
    invalid_rows = int((~valid).sum())
    if invalid_rows:
        raise ValueError(f"유효하지 않은 거래 {invalid_rows}건이 있습니다. 날짜, 티커, 구분, 수량, 단가를 확인하세요.")
    return result.sort_values(["date", "time", "id"], kind="stable").reset_index(drop=True)


def load_journal() -> pd.DataFrame:
    ensure_storage()
    try:
        frame = pd.read_csv(JOURNAL_FILE, dtype={"id": "string", "ticker": "string"})
        return validate_journal(frame) if not frame.empty else empty_journal()
    except (OSError, pd.errors.ParserError, ValueError) as exc:
        LOGGER.exception("Could not load journal")
        st.error(f"매매 일지를 불러오지 못했습니다: {exc}")
        return empty_journal()


def save_journal(frame: pd.DataFrame) -> None:
    validated = validate_journal(frame)
    temporary_path = JOURNAL_FILE.with_suffix(".tmp")
    validated.to_csv(temporary_path, index=False, encoding="utf-8-sig")
    temporary_path.replace(JOURNAL_FILE)  # atomic replacement on the same volume
    st.cache_data.clear()


@st.cache_data(ttl=300, show_spinner=False)
def fetch_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Fetch normalized OHLCV data with a short cache to reduce API calls."""
    raw = yf.download(ticker, period=period, auto_adjust=True, progress=False, threads=False)
    if raw.empty:
        raise ValueError(f"{ticker}의 가격 데이터를 찾지 못했습니다.")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(raw.columns):
        raise ValueError("가격 데이터에 필요한 OHLCV 열이 없습니다.")
    return raw.dropna(subset=["Close"]).copy()


def calculate_indicators(history: pd.DataFrame) -> dict[str, float]:
    close = history["Close"]
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(14).mean()
    losses = -delta.clip(upper=0).rolling(14).mean()
    relative_strength = gains / losses.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + relative_strength))
    ma20 = close.rolling(20).mean()
    return {
        "price": float(close.iloc[-1]),
        "ma20": float(ma20.iloc[-1]) if pd.notna(ma20.iloc[-1]) else float("nan"),
        "rsi14": float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else float("nan"),
        "volatility": float(close.pct_change().rolling(20).std().iloc[-1] * 100),
    }


def summarize_position(trades: pd.DataFrame) -> PositionSummary:
    """Calculate moving-average cost basis and realized P&L for a ticker."""
    quantity = average_cost = realized_pnl = 0.0
    for trade in trades.sort_values(["date", "time", "id"], kind="stable").itertuples():
        if trade.side == "Buy":
            total_cost = quantity * average_cost + trade.quantity * trade.price
            quantity += trade.quantity
            average_cost = total_cost / quantity
        else:
            sell_quantity = min(quantity, trade.quantity)
            realized_pnl += (trade.price - average_cost) * sell_quantity
            quantity -= sell_quantity
            if quantity <= 1e-9:
                quantity = average_cost = 0.0
    return PositionSummary(quantity, average_cost, realized_pnl)


def build_chart(history: pd.DataFrame, trades: pd.DataFrame, ticker: str, position: PositionSummary) -> go.Figure:
    chart = go.Figure()
    chart.add_trace(go.Candlestick(
        x=history.index, open=history["Open"], high=history["High"], low=history["Low"], close=history["Close"], name=ticker,
    ))
    if position.quantity:
        chart.add_hline(y=position.average_cost, line_dash="dot", line_color="#F6C945", annotation_text="평균 매수단가")
    for side, symbol, color, label in [("Buy", "triangle-up", "#22C55E", "매수"), ("Sell", "triangle-down", "#EF4444", "매도")]:
        subset = trades[trades["side"] == side]
        if not subset.empty:
            chart.add_trace(go.Scatter(
                x=pd.to_datetime(subset["date"]), y=subset["price"], mode="markers", name=label,
                marker={"symbol": symbol, "size": 11, "color": color},
                text=[f"{q:g}주 @ ${p:,.2f}" for q, p in zip(subset["quantity"], subset["price"])], hovertemplate="%{text}<extra></extra>",
            ))
    chart.update_layout(template="plotly_dark", height=560, margin={"l": 10, "r": 10, "t": 48, "b": 10}, xaxis_rangeslider_visible=False)
    chart.update_xaxes(rangebreaks=[{"bounds": ["sat", "mon"]}])
    return chart


def parse_kiwoom_csv(uploaded_file: object) -> pd.DataFrame:
    """Import a simple, header-based Kiwoom export without silently guessing columns."""
    for encoding in ("cp949", "euc-kr", "utf-8-sig", "utf-8"):
        try:
            source = pd.read_csv(uploaded_file, encoding=encoding)
            break
        except UnicodeDecodeError:
            uploaded_file.seek(0)
    else:
        raise ValueError("지원하는 인코딩으로 파일을 읽을 수 없습니다.")

    aliases = {"주문일자": "date", "체결시간": "time", "종목코드": "ticker", "매매구분": "side", "체결량": "quantity", "체결가": "price"}
    source = source.rename(columns=aliases)
    required = {"date", "ticker", "side", "quantity", "price"}
    if not required.issubset(source.columns):
        raise ValueError("지원되지 않는 양식입니다. 주문일자, 종목코드, 매매구분, 체결량, 체결가 열이 필요합니다.")
    side = source["side"].astype(str).str.strip().map({"매수": "Buy", "매도": "Sell", "Buy": "Buy", "Sell": "Sell"})
    return pd.DataFrame({
        "id": [str(uuid.uuid4()) for _ in range(len(source))], "date": source["date"], "time": source.get("time", "00:00:00"),
        "account": "Kiwoom", "ticker": source["ticker"], "side": side, "quantity": source["quantity"], "price": source["price"], "source": "kiwoom_csv",
    })


def render_dashboard() -> None:
    st.title(APP_TITLE)
    st.caption("개인 매매 기록과 시장 데이터를 한 곳에서 확인합니다. 데이터는 참고용이며 투자 조언이나 자동 주문 기능이 아닙니다.")
    with st.sidebar:
        ticker_input = st.text_input("조회 티커", DEFAULT_TICKER, help="예: SOXL, NVDA, TQQQ")
        lookback = st.selectbox("차트 기간", ["6mo", "1y", "2y", "5y"], index=1)
        if st.button("시장 데이터 새로고침", use_container_width=True):
            st.cache_data.clear()
    try:
        ticker = normalize_ticker(ticker_input)
        history = fetch_history(ticker, lookback)
    except (ValueError, KeyError, OSError) as exc:
        st.error(f"시장 데이터를 불러오지 못했습니다: {exc}")
        st.stop()

    journal = load_journal()
    ticker_trades = journal[journal["ticker"] == ticker].copy()
    position = summarize_position(ticker_trades)
    metrics = calculate_indicators(history)
    latest_price = metrics["price"]
    unrealized_pnl = (latest_price - position.average_cost) * position.quantity if position.quantity else 0.0

    columns = st.columns(5)
    columns[0].metric("현재가", f"${latest_price:,.2f}")
    columns[1].metric("보유 수량", f"{position.quantity:,.4g}주")
    columns[2].metric("평균 매수단가", "—" if not position.quantity else f"${position.average_cost:,.2f}")
    columns[3].metric("미실현 손익", f"${unrealized_pnl:,.2f}")
    columns[4].metric("실현 손익", f"${position.realized_pnl:,.2f}")

    overview, journal_tab, import_tab = st.tabs(["시장 개요", "매매 일지", "CSV 가져오기"])
    with overview:
        st.plotly_chart(build_chart(history, ticker_trades, ticker, position), use_container_width=True)
        detail = st.columns(3)
        detail[0].metric("20일 이동평균", f"${metrics['ma20']:,.2f}")
        detail[1].metric("RSI (14)", f"{metrics['rsi14']:.1f}")
        detail[2].metric("20일 변동성", f"{metrics['volatility']:.2f}%")
        st.info("RSI와 이동평균은 과거 가격을 요약한 지표이며, 미래 수익이나 매수·매도 결과를 보장하지 않습니다.")

    with journal_tab:
        with st.form("add_trade", clear_on_submit=True):
            st.subheader("거래 추가")
            c1, c2, c3, c4 = st.columns(4)
            date = c1.date_input("거래일", dt.date.today())
            time = c2.time_input("체결 시각", dt.datetime.now().time().replace(microsecond=0))
            side = c3.selectbox("구분", ["Buy", "Sell"], format_func=lambda x: "매수" if x == "Buy" else "매도")
            account = c4.text_input("계좌", "Manual")
            c5, c6, c7 = st.columns(3)
            trade_ticker = c5.text_input("티커", ticker)
            quantity = c6.number_input("수량", min_value=0.0001, value=1.0, step=1.0, format="%.4f")
            price = c7.number_input("체결 단가 (USD)", min_value=0.0001, value=float(latest_price), format="%.4f")
            if st.form_submit_button("거래 저장", type="primary"):
                try:
                    new_trade = pd.DataFrame([{"id": str(uuid.uuid4()), "date": date.isoformat(), "time": time.isoformat(), "account": account.strip() or "Manual", "ticker": normalize_ticker(trade_ticker), "side": side, "quantity": quantity, "price": price, "source": "manual"}])
                    save_journal(pd.concat([journal, new_trade], ignore_index=True))
                    st.success("거래를 저장했습니다.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
        st.subheader("전체 거래 기록")
        st.dataframe(journal.sort_values(["date", "time"], ascending=False), use_container_width=True, hide_index=True)
        st.download_button("CSV 내보내기", journal.to_csv(index=False).encode("utf-8-sig"), "trade_journal.csv", "text/csv")

    with import_tab:
        st.write("헤더가 있는 키움 CSV만 가져옵니다. 원본 파일은 변경하지 않으며, 불완전한 데이터는 저장하지 않습니다.")
        uploaded = st.file_uploader("키움 CSV 선택", type="csv")
        if uploaded and st.button("검증 후 가져오기", type="primary"):
            try:
                imported = parse_kiwoom_csv(uploaded)
                save_journal(pd.concat([journal, imported], ignore_index=True))
                st.success(f"{len(imported)}건을 가져왔습니다.")
                st.rerun()
            except (ValueError, OSError, pd.errors.ParserError) as exc:
                st.error(f"가져오기에 실패했습니다: {exc}")


if __name__ == "__main__":
    render_dashboard()
