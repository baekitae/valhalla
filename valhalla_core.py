"""Valhalla 핵심 계산 모듈.

Streamlit에 의존하지 않는 순수 계산 로직만 모아 두었습니다.
(평단/손익 계산, 키움 CSV 파싱, 지표, AI 전략 엔진, 시뮬레이션, 유전 알고리즘, 검증)
→ pytest로 단위 테스트할 수 있습니다.
"""
from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass
from typing import Callable, Iterable

import numpy as np
import pandas as pd

# ==========================================================================
# 1. 테이블 스키마 & 정규화
# ==========================================================================
JOURNAL_COLUMNS = ["Date", "Time", "Account", "Ticker", "Action", "Qty", "Price", "Format"]
LEDGER_COLUMNS = ["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"]
TABLE_COLUMNS = {"trade_journal": JOURNAL_COLUMNS, "ai_ledger": LEDGER_COLUMNS}
NUMERIC_COLUMNS = ("Qty", "Price", "Capital", "Profit")
TRADE_KEY = ["Date", "Time", "Account", "Ticker", "Action", "Qty", "Price"]
COLUMN_DEFAULTS = {"Time": "00:00:00", "Account": "Default", "Format": "unknown",
                   "Capital": 0.0, "Profit": 0.0}

_TIME_RE = re.compile(r"^\s*(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?")
_DATE_RE = re.compile(r"(\d{4})\D?(\d{1,2})\D?(\d{1,2})")


def normalize_time(value) -> str:
    """'9:5', '09:05:00', '090500' → 'HH:MM:SS'. 해석 불가 시 '00:00:00'."""
    text = "" if value is None else str(value).strip()
    match = _TIME_RE.match(text)
    if match:
        h, m, s = int(match.group(1)), int(match.group(2)), int(match.group(3) or 0)
    elif re.fullmatch(r"\d{6}", text):
        h, m, s = int(text[:2]), int(text[2:4]), int(text[4:])
    elif re.fullmatch(r"\d{4}", text):
        h, m, s = int(text[:2]), int(text[2:]), 0
    else:
        return "00:00:00"
    if not (0 <= h < 24 and 0 <= m < 60 and 0 <= s < 60):
        return "00:00:00"
    return f"{h:02d}:{m:02d}:{s:02d}"


def normalize_date(value) -> str | None:
    """'2026/05/14', '2026.5.14', '20260514', Timestamp → 'YYYY-MM-DD'. 실패 시 None."""
    if value is None:
        return None
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    match = _DATE_RE.search(str(value).strip())
    if not match:
        return None
    y, m, d = (int(g) for g in match.groups())
    try:
        return pd.Timestamp(year=y, month=m, day=d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _text(series: pd.Series) -> pd.Series:
    out = series.astype("object").where(series.notna(), "").astype(str).str.strip()
    return out.replace({"nan": "", "None": "", "NaT": ""})


def empty_table(table: str) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="float64" if c in NUMERIC_COLUMNS else "object")
                         for c in TABLE_COLUMNS[table]})


def coerce_table(table: str, frame: pd.DataFrame | None) -> pd.DataFrame:
    """DB에서 읽은 데이터를 표준 스키마/타입으로 맞춥니다 (검증 없이 관대하게)."""
    if table not in TABLE_COLUMNS:
        raise ValueError(f"지원하지 않는 테이블입니다: {table}")
    if frame is None or frame.empty:
        return empty_table(table)
    df = frame.copy()
    for col in TABLE_COLUMNS[table]:
        if col not in df.columns:
            df[col] = COLUMN_DEFAULTS.get(col, "")
    df = df[TABLE_COLUMNS[table]].copy()
    for col in TABLE_COLUMNS[table]:
        if col in NUMERIC_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        else:
            df[col] = _text(df[col])
    if "Ticker" in df:
        df["Ticker"] = df["Ticker"].str.upper()
    if "Time" in df:
        df["Time"] = df["Time"].map(normalize_time)
    if "Date" in df:
        df["Date"] = df["Date"].map(lambda v: normalize_date(v) or "")
    return df.reset_index(drop=True)


def validate_table(table: str, frame: pd.DataFrame) -> pd.DataFrame:
    """저장 직전 엄격 검증. 문제가 있으면 행 번호와 함께 ValueError."""
    if table not in TABLE_COLUMNS:
        raise ValueError(f"지원하지 않는 테이블입니다: {table}")
    required = {"Date", "Ticker", "Action", "Qty", "Price"} | ({"Agent"} if table == "ai_ledger" else set())
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"필수 열이 없습니다: {', '.join(sorted(missing))}")
    df = coerce_table(table, frame.dropna(how="all"))
    problems: list[str] = []

    def _report(mask: pd.Series, message: str) -> None:
        rows = (np.flatnonzero(mask.to_numpy()) + 1).tolist()
        if rows:
            shown = ", ".join(map(str, rows[:10])) + (" …" if len(rows) > 10 else "")
            problems.append(f"{message} (행 {shown})")

    _report(df["Date"] == "", "날짜 형식이 올바르지 않습니다")
    _report(df["Ticker"] == "", "티커가 비어 있습니다")
    _report(~df["Action"].isin(["Buy", "Sell"]), "구분은 Buy 또는 Sell이어야 합니다")
    for col in ("Qty", "Price"):
        _report(df[col].isna() | (df[col] <= 0), f"{col}은 0보다 큰 숫자여야 합니다")
    if table == "trade_journal":
        df["Account"] = df["Account"].replace("", "Default")
        df["Format"] = df["Format"].replace("", "manual")
    else:
        _report(df["Agent"] == "", "Agent 이름이 비어 있습니다")
        df["Capital"] = df["Capital"].fillna(0.0)
        df["Profit"] = df["Profit"].fillna(0.0)
    if problems:
        raise ValueError(" / ".join(problems))
    return df


def sort_trades(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    return df.sort_values(["Date", "Time"], kind="stable").reset_index(drop=True)


# ==========================================================================
# 2. 포지션 & 손익 (부분 매도 지원, 이동평균 원가법)
# ==========================================================================
@dataclass
class Position:
    qty: float = 0.0
    cost: float = 0.0
    buy_count: int = 0            # 현재 사이클에서 매수한 '날짜 수' (같은 날 분할 체결은 1회차)
    realized: float = 0.0         # 실현 손익 누계
    cycle_start: str | None = None
    closed_cycles: int = 0
    unmatched_sell_qty: float = 0.0  # 보유량보다 많이 판 수량 (데이터 누락 신호)

    @property
    def avg(self) -> float:
        return self.cost / self.qty if self.qty > 1e-12 else 0.0

    def unrealized(self, price: float) -> float:
        return (price - self.avg) * self.qty if self.qty > 0 else 0.0


def compute_position(trades: pd.DataFrame) -> Position:
    pos = Position()
    last_buy_date = None
    if trades is None or trades.empty:
        return pos
    for row in sort_trades(trades).itertuples(index=False):
        qty, price, date = float(row.Qty), float(row.Price), row.Date
        if not (qty > 0 and price > 0):
            continue
        if row.Action == "Buy":
            pos.qty += qty
            pos.cost += qty * price
            if pos.cycle_start is None:
                pos.cycle_start = date
            if date != last_buy_date:
                pos.buy_count += 1
                last_buy_date = date
        elif row.Action == "Sell":
            if pos.qty <= 1e-12:
                pos.unmatched_sell_qty += qty
                continue
            sell_qty = min(qty, pos.qty)
            pos.unmatched_sell_qty += qty - sell_qty
            avg = pos.avg
            pos.realized += (price - avg) * sell_qty
            pos.cost -= avg * sell_qty          # 평단은 유지, 원가만 비례 차감
            pos.qty -= sell_qty
            if pos.qty <= 1e-9:                 # 전량 매도 → 사이클 종료
                pos.qty, pos.cost, pos.buy_count = 0.0, 0.0, 0
                pos.cycle_start, last_buy_date = None, None
                pos.closed_cycles += 1
    return pos


def positions_by_account(journal: pd.DataFrame) -> pd.DataFrame:
    """계좌·종목별 포지션 (계좌마다 평단이 다르므로 분리 계산)."""
    rows = []
    if journal is None or journal.empty:
        return pd.DataFrame(columns=["Account", "Ticker", "Qty", "Avg", "Cost", "Realized"])
    for (account, ticker), group in journal.groupby(["Account", "Ticker"], sort=True):
        pos = compute_position(group)
        rows.append({"Account": account, "Ticker": ticker, "Qty": pos.qty, "Avg": pos.avg,
                     "Cost": pos.cost, "Realized": pos.realized})
    return pd.DataFrame(rows)


# ==========================================================================
# 3. 키움증권 CSV 파서
# ==========================================================================
ENCODINGS = ("utf-8-sig", "cp949", "euc-kr")   # UTF-8을 먼저 시도해야 한글이 깨지지 않습니다.
_SKIP_SIDE_WORDS = ("정정", "취소", "거부", "확인")


def extract_account_tag(filename: str) -> str:
    """파일명에서 계좌번호 후보 추출. 날짜처럼 보이는 숫자(20260514, 260514)는 제외."""
    stem = re.sub(r"\.[^.]+$", "", filename or "")
    candidates = []
    for token in re.findall(r"\d+", stem):
        is_date8 = len(token) == 8 and normalize_date(token) is not None and token[:2] in ("19", "20")
        is_date6 = len(token) == 6 and normalize_date("20" + token) is not None
        if len(token) >= 4 and not is_date8 and not is_date6:
            candidates.append(token)
    return max(candidates, key=len) if candidates else "Default"


def _num(value) -> float:
    text = str(value).replace(",", "").replace("$", "").replace("'", "").strip()
    return float(text)


def _side(text) -> str | None:
    text = str(text)
    if any(word in text for word in _SKIP_SIDE_WORDS):
        return None
    if "매수" in text:
        return "Buy"
    if "매도" in text:
        return "Sell"
    return None


def parse_kiwoom_csv(file_bytes: bytes, filename: str, account_override: str | None = None):
    """키움 해외주식 거래/주문 내역 CSV → (거래 DataFrame, 통계 dict)."""
    stats = {"format": None, "parsed": 0, "skipped": 0, "encoding": None}
    raw = None
    for enc in ENCODINGS:
        try:
            raw = pd.read_csv(io.BytesIO(file_bytes), encoding=enc, header=None, names=range(80),
                              engine="python", dtype=str, keep_default_na=False)
            stats["encoding"] = enc
            break
        except (UnicodeDecodeError, pd.errors.ParserError, ValueError):
            continue
    if raw is None or raw.empty:
        return empty_table("trade_journal"), stats

    account = (account_override or "").strip() or extract_account_tag(filename)
    format_type, start = None, 0
    for idx, first in enumerate(raw[0].astype(str).str.strip()):
        if first == "주문일자":
            format_type, start = "2row", idx + 2
            break
        if first == "거래일자":
            format_type, start = "3row", idx + 3
            break
    stats["format"] = format_type
    if format_type is None:
        return empty_table("trade_journal"), stats

    rows = raw.iloc[start:].reset_index(drop=True)
    trades = []

    def _blank(value) -> bool:
        return str(value).strip() in ("", "nan")

    step = 2 if format_type == "2row" else 3
    for i in range(0, len(rows) - (step - 1), step):
        r1, r2 = rows.iloc[i], rows.iloc[i + 1]
        if _blank(r1[0]):
            continue
        try:
            if format_type == "2row":
                date, time_raw, ticker, side = r1[0], r2[0], r1[2], _side(r2[3])
                qty, price = _num(r1[6]), _num(r2[6])
            else:
                if str(r1[1]).strip() != "매매":
                    stats["skipped"] += 1
                    continue
                date, time_raw, ticker, side = r1[0], r2[10], r2[0], _side(r2[1])
                qty, price = _num(r1[3]), _num(r2[2])
        except (ValueError, KeyError, IndexError):
            stats["skipped"] += 1
            continue
        date = normalize_date(date)
        ticker = str(ticker).replace("'", "").strip().upper()
        if side is None or date is None or not ticker or qty <= 0 or price <= 0:
            stats["skipped"] += 1
            continue
        trades.append({"Date": date, "Time": normalize_time(time_raw), "Account": account,
                       "Ticker": ticker, "Action": side, "Qty": qty, "Price": price,
                       "Format": format_type})
    stats["parsed"] = len(trades)
    return coerce_table("trade_journal", pd.DataFrame(trades)), stats


def _with_occurrence(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["_qk"] = out["Qty"].round(6)
    out["_pk"] = out["Price"].round(6)
    key = ["Date", "Time", "Account", "Ticker", "Action", "_qk", "_pk"]
    out["_n"] = out.groupby(key, sort=False).cumcount()
    return out


def merge_journal(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    """기존 일지 + 새 업로드 병합.

    * 같은 파일을 다시 올려도 중복되지 않고,
    * 같은 초·같은 가격의 '진짜' 분할 체결 여러 건은 보존합니다.
      (키별 건수 = max(기존 건수, 새 건수) 인 멀티셋 합집합)
    * 같은 날/계좌/종목/구분에 체결내역(2row)이 있으면 거래내역(3row) 요약은 버립니다.
    """
    old = coerce_table("trade_journal", existing)
    new = coerce_table("trade_journal", incoming)
    combined = pd.concat([old.assign(_src=0), new.assign(_src=1)], ignore_index=True)
    if combined.empty:
        return empty_table("trade_journal")
    group = combined["Date"] + "|" + combined["Account"] + "|" + combined["Ticker"] + "|" + combined["Action"]
    has_2row = set(group[combined["Format"] == "2row"])
    combined = combined[~(group.isin(has_2row) & (combined["Format"] == "3row"))]

    old_part = _with_occurrence(combined[combined["_src"] == 0])
    new_part = _with_occurrence(combined[combined["_src"] == 1])
    key = ["Date", "Time", "Account", "Ticker", "Action", "_qk", "_pk", "_n"]
    merged = pd.concat([old_part, new_part], ignore_index=True).drop_duplicates(subset=key, keep="first")
    return sort_trades(merged[JOURNAL_COLUMNS])


# ==========================================================================
# 4. 기술적 지표
# ==========================================================================
FEATURE_NAMES = ["MA20 이격도(%)", "RSI(14)", "거래량 배수", "변동성(20일, %)"]
FEATURE_WARMUP = 21
NEUTRAL_FEATURES = np.array([0.0, 50.0, 1.0, 2.0])


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """HTS와 같은 Wilder 방식 RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rsi = 100 - 100 / (1 + gain / loss)
    rsi = rsi.where(loss != 0, 100.0)
    rsi = rsi.where(~((gain == 0) & (loss == 0)), 50.0)
    return rsi.where(gain.notna())


def compute_features(close, volume) -> np.ndarray:
    """(T, 4) 배열: [MA20 이격도, RSI, 거래량 배수, 변동성]. 초기 구간은 NaN."""
    c = pd.Series(np.asarray(close, dtype=float))
    v = pd.Series(np.asarray(volume, dtype=float))
    ma20 = c.rolling(20).mean()
    feats = pd.DataFrame({
        "ma": (c / ma20 - 1) * 100,
        "rsi": wilder_rsi(c),
        "volr": v / v.rolling(20).mean().replace(0, np.nan),
        "vola": c.pct_change().rolling(20).std() * 100,
    })
    return feats.replace([np.inf, -np.inf], np.nan).to_numpy()


def sanitize_features(x) -> np.ndarray:
    arr = np.asarray(x, dtype=float).reshape(-1)[:4]
    return np.where(np.isfinite(arr), arr, NEUTRAL_FEATURES)


# ==========================================================================
# 5. AI 전략 엔진 (Titan Rest V3 유전자 8개)
# ==========================================================================
GENE_NAMES = ["기본 목표수익", "이격도 가중", "RSI 가중", "변동성 가중",
              "리스크 회피", "매수 할인율", "공포 매수배수", "분할 횟수"]
GENE_BOUNDS = np.array([[5, 20], [-5, 5], [-5, 5], [-5, 5], [0, 10], [0, 5], [-2, 5], [20, 40]], dtype=float)
DEFAULT_GENES = [10.0, 2.0, -3.0, 1.5, 5.0, 1.0, 1.0, 40.0]
MAX_BUY_MULTIPLIER = 3.0
MAX_DISCOUNT_PCT = 15.0


def pad_genes(genes) -> list[float]:
    values = [float(v) for v in list(genes)[:8]]
    values += DEFAULT_GENES[len(values):]
    return values


def action_params(genes, x) -> dict[str, np.ndarray]:
    """유전자(N,8)와 지표(4,)로 매도 목표/매수 계획 계산. 모든 값은 (N,) 배열."""
    g = np.atleast_2d(np.asarray(genes, dtype=float))
    ma, rsi, _volr, vola = sanitize_features(x)
    ma_n = np.clip(ma / 10.0, -1.0, 1.0)
    rsi_n = (rsi - 50.0) / 50.0
    vol_n = np.clip(vola / 5.0, 0.0, 2.0)
    w = g.T
    target_r = np.clip(w[0] + w[1] * ma_n + w[2] * rsi_n + w[3] * vol_n - 0.5 * w[4], 3.0, 30.0)
    fear = -rsi_n - ma_n
    return {
        "target_r": target_r,
        "buy_mult": np.clip(1.0 + fear * w[6], 0.0, MAX_BUY_MULTIPLIER),
        "discount": np.clip(w[5] * (1.0 + vol_n), 0.0, MAX_DISCOUNT_PCT),
        "split": np.round(np.clip(w[7], 20.0, 40.0)),
    }


def agent_params(genes, x) -> dict[str, float]:
    p = action_params(pad_genes(genes), x)
    return {k: float(v[0]) for k, v in p.items()}


# ==========================================================================
# 6. 벡터화 백테스트 (훈련·리그·검증 모두 같은 규칙)
#   * t일의 주문은 t-1일까지의 정보(지표, 종가)로만 결정 → 미래 정보 사용 없음
#   * 매도: 지정가(평단×(1+r)). 시가가 더 높게 갭상승하면 시가 체결
#   * 매수: 지정가(전일 종가×(1-할인)). 시가가 더 낮으면 시가 체결
#   * 분할 횟수·잔여 예수금 가드레일, 매매 수수료 반영
# ==========================================================================
@dataclass
class SimResult:
    final_equity: np.ndarray
    return_pct: np.ndarray
    realized: np.ndarray
    mdd_pct: np.ndarray
    n_trades: np.ndarray
    qty: np.ndarray
    cost: np.ndarray
    cash: np.ndarray
    equity_curve: np.ndarray | None = None
    trades: pd.DataFrame | None = None


def simulate(genes, open_, high, low, close, feats, start: int, capital: float = 10_000.0,
             fee_rate: float = 0.001, record: bool = False, dates: Iterable[str] | None = None,
             agent_names: list[str] | None = None, ticker: str = "", keep_curve: bool = False) -> SimResult:
    G = np.atleast_2d(np.asarray(genes, dtype=float))
    n = G.shape[0]
    o, h, l, c = (np.asarray(a, dtype=float) for a in (open_, high, low, close))
    T = len(c)
    start = max(int(start), 1)
    cash = np.full(n, float(capital))
    qty, cost, buys = np.zeros(n), np.zeros(n), np.zeros(n)
    realized, peak, mdd, n_trades = np.zeros(n), np.full(n, float(capital)), np.zeros(n), np.zeros(n)
    dates = list(dates) if dates is not None else None
    names = agent_names or [f"Agent_{i}" for i in range(n)]
    log: list[dict] = []
    curve = np.full((max(T - start, 0), n), np.nan) if keep_curve else None

    for t in range(start, T):
        p = action_params(G, feats[t - 1])
        holding = qty > 1e-12
        avg = np.where(holding, cost / np.maximum(qty, 1e-12), 0.0)
        target = avg * (1.0 + p["target_r"] / 100.0)
        sell = holding & (h[t] >= target)
        if sell.any():
            fill = np.maximum(target, o[t])
            proceeds = qty * fill * (1.0 - fee_rate)
            pnl = proceeds - cost
            if record:
                for i in np.flatnonzero(sell):
                    log.append({"Date": dates[t], "Agent": names[i], "Ticker": ticker, "Action": "Sell",
                                "Qty": round(qty[i], 6), "Price": round(fill[i], 4),
                                "Capital": capital, "Profit": round(pnl[i], 2)})
            realized += np.where(sell, pnl, 0.0)
            cash += np.where(sell, proceeds, 0.0)
            qty, cost, buys = (np.where(sell, 0.0, arr) for arr in (qty, cost, buys))
            n_trades += sell

        limit = c[t - 1] * (1.0 - p["discount"] / 100.0)
        amount = np.minimum(capital / p["split"] * p["buy_mult"], cash)
        buy = (~sell) & (buys < p["split"]) & (amount >= 1.0) & (l[t] <= limit)
        if buy.any():
            fill = np.minimum(limit, o[t])
            add = amount * (1.0 - fee_rate) / fill
            if record:
                for i in np.flatnonzero(buy):
                    log.append({"Date": dates[t], "Agent": names[i], "Ticker": ticker, "Action": "Buy",
                                "Qty": round(add[i], 6), "Price": round(fill[i], 4),
                                "Capital": capital, "Profit": 0.0})
            qty = qty + np.where(buy, add, 0.0)
            cost = cost + np.where(buy, amount, 0.0)
            cash = cash - np.where(buy, amount, 0.0)
            buys = buys + buy
            n_trades += buy

        equity = cash + qty * c[t]
        peak = np.maximum(peak, equity)
        mdd = np.maximum(mdd, 1.0 - equity / peak)
        if curve is not None:
            curve[t - start] = equity

    final = cash + qty * c[-1] if T else cash
    trades = pd.DataFrame(log, columns=LEDGER_COLUMNS) if record else None
    return SimResult(final, (final / capital - 1) * 100, realized, mdd * 100, n_trades,
                     qty, cost, cash, curve, trades)


def buy_and_hold(close, start: int, fee_rate: float = 0.001) -> tuple[float, float]:
    """기준선: 시작일 종가에 전액 매수 후 보유. (수익률 %, MDD %)"""
    c = np.asarray(close, dtype=float)[max(start - 1, 0):]
    if len(c) < 2:
        return 0.0, 0.0
    equity = c / c[0] * (1 - fee_rate)
    mdd = (1 - equity / np.maximum.accumulate(equity)).max() * 100
    return float((equity[-1] - 1) * 100), float(mdd)


# ==========================================================================
# 7. 시나리오 생성 (블록 부트스트랩) & 유전 알고리즘
# ==========================================================================
def bootstrap_paths(ohlcv: pd.DataFrame, n_days: int, n_paths: int, rng: np.random.Generator,
                    block: int = 5) -> list[pd.DataFrame]:
    """실제 하루 단위 (수익률, 갭, 고가폭, 저가폭, 거래량)를 블록째로 재표집.
    정규분포 가정보다 레버리지 ETF의 급락(두꺼운 꼬리)과 변동성 군집을 잘 보존합니다."""
    df = ohlcv[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    prev = df["Close"].shift(1)
    parts = pd.DataFrame({
        "ret": np.log(df["Close"] / prev),
        "gap": np.log(df["Open"] / prev),
        "hi": np.log(df["High"] / df["Close"]),
        "lo": np.log(df["Low"] / df["Close"]),
        "vol": df["Volume"],
    }).replace([np.inf, -np.inf], np.nan).dropna()
    m = len(parts)
    if m < block + 1 or n_days <= 0:
        return []
    values = parts.to_numpy()
    last_close = float(df["Close"].iloc[-1])
    last_date = pd.Timestamp(ohlcv.index[-1])
    future_index = pd.bdate_range(last_date + pd.Timedelta(days=1), periods=n_days)
    paths = []
    for _ in range(n_paths):
        idx: list[int] = []
        while len(idx) < n_days:
            s = int(rng.integers(0, m - block + 1))
            idx.extend(range(s, s + block))
        sample = values[np.array(idx[:n_days])]
        close = last_close * np.exp(np.cumsum(sample[:, 0]))
        prev_close = np.concatenate([[last_close], close[:-1]])
        open_ = prev_close * np.exp(sample[:, 1])
        high = np.maximum.reduce([close * np.exp(sample[:, 2]), open_, close])
        low = np.minimum.reduce([close * np.exp(sample[:, 3]), open_, close])
        paths.append(pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                                   "Volume": sample[:, 4]}, index=future_index))
    return paths


@dataclass
class Scenario:
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    f: np.ndarray


def _scenario(df: pd.DataFrame) -> Scenario:
    return Scenario(df["Open"].to_numpy(float), df["High"].to_numpy(float), df["Low"].to_numpy(float),
                    df["Close"].to_numpy(float), compute_features(df["Close"], df["Volume"]))


def evaluate_population(G: np.ndarray, scenarios: list[Scenario], capital: float, fee_rate: float,
                        risk_penalty: float, n_real: int = 1) -> dict[str, np.ndarray]:
    """적합도 = (평균 수익 + 최악 시나리오 수익) / 2 − 낙폭 페널티 × 평균 MDD.

    평균만 보면 한 차트에서 크게 번 요원이 뽑히기 쉬워서, 가장 나빴던 시나리오 성적을 절반 반영합니다.
    scenarios[0] 은 실제 학습 차트 전체, 앞쪽 n_real 개는 실제 차트(전체·구간), 나머지는 가상 차트입니다.
    """
    rets, mdds, trades = [], [], []
    for sc in scenarios:
        r = simulate(G, sc.o, sc.h, sc.l, sc.c, sc.f, FEATURE_WARMUP + 1, capital, fee_rate)
        rets.append(r.return_pct)
        mdds.append(r.mdd_pct)
        trades.append(r.n_trades)
    rets, mdds = np.array(rets), np.array(mdds)
    ret, mdd, worst = rets.mean(axis=0), mdds.mean(axis=0), rets.min(axis=0)
    return {"fitness": 0.5 * (ret + worst) - risk_penalty * mdd, "ret": ret, "mdd": mdd, "worst": worst,
            "real_ret": rets[0], "real_mdd": mdds[0], "synthetic_ret": rets[n_real:].mean(axis=0) if len(rets) > n_real else ret,
            "trades": np.mean(trades, axis=0)}


def build_scenarios(train: pd.DataFrame, n_scenarios: int, mc_days: int, rng: np.random.Generator,
                    block: int = 10) -> tuple[list[Scenario], int]:
    """평가용 시나리오: 실제 학습 차트(전체 + 앞/뒤 절반) + 서로 독립된 가상 차트.

    가상 차트는 실제 차트 뒤에 이어 붙이지 않고, 짧은 준비 구간(지표 계산용) 뒤에 학습 기간의
    하루 단위 움직임을 블록째 재표집해 처음부터 새로 만든 차트입니다.
    → 모든 시나리오가 같은 실제 상승장을 공유하던 문제(실제 차트를 외우는 과적합)를 막습니다.
    """
    scenarios = [_scenario(train)]
    n = len(train)
    w = FEATURE_WARMUP + 1
    if n >= 2 * (w + 60):
        half = n // 2
        scenarios.append(_scenario(train.iloc[:half]))
        scenarios.append(_scenario(train.iloc[half - w:]))
    n_real = len(scenarios)
    seed = train.iloc[:w]
    length = max(int(mc_days), 120)
    last_close = float(train["Close"].iloc[-1])
    for path in bootstrap_paths(train, length, max(int(n_scenarios) - 1, 0), rng, block):
        # 가상 차트는 학습 마지막 종가에서 출발하므로, 준비 구간 마지막 종가 수준으로 옮깁니다 (전략은 % 기준이라 영향 없음)
        path = path.copy()
        path[["Open", "High", "Low", "Close"]] *= float(seed["Close"].iloc[-1]) / last_close
        path.index = pd.bdate_range(seed.index[-1] + pd.Timedelta(days=1), periods=len(path))
        scenarios.append(_scenario(pd.concat([seed, path])))
    return scenarios, n_real


def run_evolution(history: pd.DataFrame, population: int = 200, generations: int = 15,
                  n_scenarios: int = 6, mc_days: int = 200, train_ratio: float = 0.8,
                  fee_bps: float = 10.0, capital: float = 10_000.0, risk_penalty: float = 0.5,
                  seed: int = 42, seed_genes: list[list[float]] | None = None,
                  progress_cb: Callable[[int, int, float], None] | None = None) -> dict:
    """진짜 유전 알고리즘: 엘리트 보존 + 토너먼트 선택 + 교차 + 변이, 세대 반복.

    * 학습 구간(앞 train_ratio)의 실제 차트 1개 + 부트스트랩 미래 시나리오 여러 개의 '평균'으로 평가
      → 운 좋은 경로 하나에 과적합되는 것을 줄입니다.
    * 학습에 쓰지 않은 뒤쪽 구간(Out-of-sample)으로 최종 성적을 따로 검증합니다.
    """
    hist = history[["Open", "High", "Low", "Close", "Volume"]].dropna().astype(float)
    n = len(hist)
    split = int(n * train_ratio)
    if split < 120 or n - split < 40:
        raise ValueError("학습/검증에 필요한 데이터가 부족합니다. 기간을 늘려 주세요.")
    rng = np.random.default_rng(seed)
    fee = fee_bps / 10_000
    train = hist.iloc[:split]
    scenarios, n_real = build_scenarios(train, n_scenarios, mc_days, rng)

    lo, hi = GENE_BOUNDS[:, 0], GENE_BOUNDS[:, 1]
    span = hi - lo
    P = max(int(population), 10)
    G = lo + rng.random((P, 8)) * span
    if seed_genes:
        seeds = np.clip(np.array([pad_genes(s) for s in seed_genes])[: P // 4], lo, hi)
        G[: len(seeds)] = seeds
    n_elite = max(2, P // 10)
    progress = []
    scores = None
    for gen in range(int(generations)):
        scores = evaluate_population(G, scenarios, capital, fee, risk_penalty, n_real)
        order = np.argsort(-scores["fitness"])
        progress.append({"세대": gen + 1, "최고 적합도": float(scores["fitness"][order[0]]),
                         "평균 적합도": float(np.mean(scores["fitness"])),
                         "최고 평균수익(%)": float(scores["ret"][order[0]])})
        if progress_cb:
            progress_cb(gen + 1, int(generations), float(scores["fitness"][order[0]]))
        if gen == generations - 1:
            break
        elite = G[order[:n_elite]]
        fit = scores["fitness"]

        def tournament() -> np.ndarray:
            cand = rng.integers(0, P, size=3)
            return G[cand[np.argmax(fit[cand])]]

        children = []
        for _ in range(P - n_elite):
            a, b = tournament(), tournament()
            u = rng.random(8)
            child = u * a + (1 - u) * b
            mutate = rng.random(8) < 0.2
            child = child + mutate * rng.normal(0, 0.1, 8) * span
            children.append(np.clip(child, lo, hi))
        G = np.vstack([elite, np.array(children)])

    order = np.argsort(-scores["fitness"])[:3]
    top_g = G[order]

    full = _scenario(hist)
    test_start = max(split, FEATURE_WARMUP + 1)
    oos = simulate(top_g, full.o, full.h, full.l, full.c, full.f, test_start, capital, fee)
    bh_ret, bh_mdd = buy_and_hold(full.c, test_start, fee)
    top = []
    for rank, i in enumerate(order):
        top.append({"rank": rank + 1, "genes": [round(float(v), 4) for v in G[i]],
                    "fitness": float(scores["fitness"][i]), "train_ret": float(scores["real_ret"][i]),
                    "train_avg": float(scores["ret"][i]), "synthetic_ret": float(scores["synthetic_ret"][i]),
                    "train_mdd": float(scores["mdd"][i]), "train_worst": float(scores["worst"][i]),
                    "test_ret": float(oos.return_pct[rank]), "test_mdd": float(oos.mdd_pct[rank]),
                    "test_trades": int(oos.n_trades[rank])})
    idx = hist.index
    return {"top": top, "progress": pd.DataFrame(progress),
            "buy_hold_test_ret": bh_ret, "buy_hold_test_mdd": bh_mdd,
            "train_period": (str(idx[0].date()), str(idx[split - 1].date())),
            "test_period": (str(idx[split].date()), str(idx[-1].date())),
            "n_scenarios": len(scenarios)}


# ==========================================================================
# 8. 확률/매물대/워크포워드 검증
# ==========================================================================
def touch_probability(high, close, rise_pct: float, horizon: int) -> tuple[float, int]:
    """과거에 '종가 대비 rise_pct% 이상'을 horizon 거래일 안에 고가로 터치한 비율 (경험적 확률)."""
    h = pd.Series(np.asarray(high, dtype=float))
    c = pd.Series(np.asarray(close, dtype=float))
    if rise_pct <= 0:
        return 100.0, int(len(c))
    future_max = h[::-1].rolling(horizon, min_periods=horizon).max()[::-1].shift(-1)
    ratio = (future_max / c - 1).dropna()
    if ratio.empty:
        return float("nan"), 0
    return float((ratio >= rise_pct / 100).mean() * 100), int(len(ratio))


def volume_profile(close, volume, bins: int = 24) -> pd.DataFrame:
    c = pd.Series(np.asarray(close, dtype=float))
    v = pd.Series(np.asarray(volume, dtype=float))
    valid = c.notna() & v.notna()
    c, v = c[valid], v[valid]
    if c.empty or c.min() == c.max():
        return pd.DataFrame(columns=["low", "high", "mid", "volume", "share"])
    cats = pd.cut(c, bins=bins)
    prof = v.groupby(cats, observed=False).sum()
    out = pd.DataFrame({"low": [iv.left for iv in prof.index], "high": [iv.right for iv in prof.index],
                        "volume": prof.to_numpy()})
    out["mid"] = (out["low"] + out["high"]) / 2
    out["share"] = out["volume"] / out["volume"].sum() * 100
    return out


def key_levels(profile: pd.DataFrame, current: float) -> tuple[float | None, float | None]:
    """(현재가 위 최대 매물대 = 저항, 현재가 아래 최대 매물대 = 지지)."""
    if profile.empty:
        return None, None
    above = profile[profile["low"] > current]
    below = profile[profile["high"] < current]
    res = float(above.loc[above["volume"].idxmax(), "mid"]) if not above.empty else None
    sup = float(below.loc[below["volume"].idxmax(), "mid"]) if not below.empty else None
    return res, sup


def walk_forward(close: pd.Series, horizon: int = 20, lookback: int = 60, fee_bps: float = 10,
                 n_sims: int = 10_000, seed: int = 42) -> dict | None:
    close = close.dropna().astype(float)
    if len(close) < lookback + horizon + 100:
        return None
    log_c = np.log(close)
    model_log = ((log_c - log_c.shift(lookback)) / lookback * horizon).clip(-0.30, 0.30)
    res = pd.DataFrame({"price": close, "model_return": np.exp(model_log) - 1,
                        "actual_return": close.shift(-horizon) / close - 1}).dropna()
    if res.empty:
        return None
    res["model_price"] = res["price"] * (1 + res["model_return"])
    res["future_price"] = res["price"] * (1 + res["actual_return"])
    hit = (np.sign(res["model_return"]) == np.sign(res["actual_return"])).mean() * 100
    always_up = (res["actual_return"] > 0).mean() * 100
    naive_mae = (res["future_price"] - res["price"]).abs().mean()
    model_mae = (res["future_price"] - res["model_price"]).abs().mean()

    trades = res.iloc[::horizon].copy()
    trades["position"] = (trades["model_return"] > 0).astype(float)
    trades["turnover"] = trades["position"].diff().abs().fillna(trades["position"])
    fee = fee_bps / 10_000
    trades["strategy_return"] = trades["position"] * trades["actual_return"] - trades["turnover"] * fee
    trades["strategy_equity"] = (1 + trades["strategy_return"]).cumprod()
    trades["buyhold_equity"] = (1 + trades["actual_return"]).cumprod() * (1 - fee)

    def _mdd(eq: pd.Series) -> float:
        return float(((eq / eq.cummax()) - 1).min() * 100)

    daily_log = np.log(close).diff().dropna().tail(252).to_numpy()
    rng = np.random.default_rng(seed)
    sims = rng.choice(daily_log, size=(n_sims, horizon), replace=True).sum(axis=1)
    current = float(close.iloc[-1])
    prices = current * np.exp(sims)
    return {
        "results": res, "trades": trades, "current_price": current, "horizon": horizon,
        "hit_rate": float(hit), "always_up_rate": float(always_up),
        "naive_mae": float(naive_mae), "model_mae": float(model_mae),
        "avg_return": float(trades["strategy_return"].mean() * 100),
        "mdd": _mdd(trades["strategy_equity"]), "buyhold_mdd": _mdd(trades["buyhold_equity"]),
        "strategy_total": float((trades["strategy_equity"].iloc[-1] - 1) * 100),
        "buyhold_total": float((trades["buyhold_equity"].iloc[-1] - 1) * 100),
        "periods": int(len(trades)), "exposures": int(trades["position"].sum()),
        "median": float(np.median(prices)), "p25": float(np.percentile(prices, 25)),
        "p75": float(np.percentile(prices, 75)), "p05": float(np.percentile(prices, 5)),
        "p95": float(np.percentile(prices, 95)),
        "loss_probability": float((prices < current).mean() * 100),
    }


# ==========================================================================
# 8-1. 시세 정리 · 실전 리그 계산 · 요원 검증 (앱과 자동 실행 스크립트가 함께 사용)
# ==========================================================================
OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def clean_ohlcv(raw: pd.DataFrame | None) -> pd.DataFrame | None:
    """yfinance 결과 → 표준 OHLCV (MultiIndex·시간대·결측 정리)."""
    if raw is None or raw.empty:
        return None
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.loc[:, ~df.columns.duplicated()]
    if any(c not in df.columns for c in OHLCV):
        return None
    index = pd.to_datetime(df.index)
    df.index = index.tz_localize(None) if index.tz is not None else index
    df = df[OHLCV].apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
    df["Volume"] = df["Volume"].fillna(0)
    for col in ("Open", "High", "Low"):
        df[col] = df[col].fillna(df["Close"])
    return None if df.empty else df


def league_period(start: str, today: pd.Timestamp | None = None) -> str:
    days = ((today or pd.Timestamp.now()) - pd.Timestamp(start)).days
    return "2y" if days < 550 else "5y" if days < 1700 else "max"


def league_run(df: pd.DataFrame, start: str, capital: float, fee_bps: float, genes, names: list[str],
               ticker: str) -> dict | None:
    """리그 시작일부터 오늘까지 요원들을 실제 일봉에 적용. 같은 입력이면 언제 돌려도 같은 결과."""
    idx = df.index
    start_i = max(int(idx.searchsorted(pd.Timestamp(start))), FEATURE_WARMUP + 1)
    if start_i >= len(df):
        return None
    feats = compute_features(df["Close"], df["Volume"])
    fee = fee_bps / 10_000
    res = simulate(np.array(genes), df["Open"], df["High"], df["Low"], df["Close"], feats, start_i,
                   capital, fee, record=True, dates=list(idx.strftime("%Y-%m-%d")),
                   agent_names=list(names), ticker=ticker, keep_curve=True)
    close = df["Close"].to_numpy()
    curve = pd.DataFrame(res.equity_curve, index=idx[start_i:], columns=list(names))
    curve["Buy & Hold"] = capital * close[start_i:] / close[start_i - 1] * (1 - fee)
    bh_ret, bh_mdd = buy_and_hold(close, start_i, fee)
    summary = pd.DataFrame({
        "요원": list(names), "총자산": res.final_equity, "수익률(%)": res.return_pct,
        "실현손익": res.realized, "평가손익": res.qty * close[-1] - res.cost,
        "MDD(%)": res.mdd_pct, "체결수": res.n_trades.astype(int), "보유수량": res.qty,
    })
    return {"summary": summary, "trades": res.trades, "curve": curve, "bh_ret": bh_ret, "bh_mdd": bh_mdd,
            "start": str(idx[start_i].date()), "end": str(idx[-1].date()), "last_close": float(close[-1]),
            "days": int(len(df) - start_i)}


def rolling_validation(df: pd.DataFrame, genes, names: list[str], window: int = 126, capital: float = 10_000.0,
                       fee_bps: float = 10.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """기간별 안정성 검증: 전체 기간을 겹치지 않는 window 거래일 구간으로 잘라 각 구간을 처음부터 따로 매매.

    반환: (구간별 수익률 표, 요원별 요약 표)
    요약: 수익 구간 비율, 보유 대비 우세 구간 비율, 최악 구간 수익, 평균 수익, 평균 MDD
    """
    G = np.array(genes, dtype=float)
    feats = compute_features(df["Close"], df["Volume"])
    o, h, l, c = (df[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    fee = fee_bps / 10_000
    rows = []
    first = FEATURE_WARMUP + 1
    for s in range(first, len(df) - window + 1, window):
        e = s + window
        r = simulate(G, o[:e], h[:e], l[:e], c[:e], feats[:e], s, capital, fee)
        bh, bh_mdd = buy_and_hold(c[:e], s, fee)
        row = {"구간": f"{df.index[s]:%Y-%m} ~ {df.index[e - 1]:%Y-%m}", "보유(%)": round(bh, 1)}
        for name, ret, mdd in zip(names, r.return_pct, r.mdd_pct):
            row[name] = round(float(ret), 1)
            row[f"{name}__mdd"] = float(mdd)
        rows.append(row)
    table = pd.DataFrame(rows)
    if table.empty:
        return table, pd.DataFrame()
    summary = []
    for name in names:
        rets = table[name]
        summary.append({
            "요원": name, "구간 수": len(rets),
            "수익 구간(%)": round((rets > 0).mean() * 100, 0),
            "보유 대비 우세(%)": round((rets > table["보유(%)"]).mean() * 100, 0),
            "평균 수익(%)": round(rets.mean(), 1), "최악 구간(%)": round(rets.min(), 1),
            "평균 MDD(%)": round(table[f"{name}__mdd"].mean(), 1),
        })
    table = table.drop(columns=[c for c in table.columns if c.endswith("__mdd")])
    return table, pd.DataFrame(summary)


# 판정 기준 (화면에도 그대로 표시)
TRAIN_RULES = ("검증 구간(학습에 안 쓴 기간) 수익 > 0", "기간별 안정성: 수익 구간 비율 ≥ 50%",
               "검증 구간 낙폭(MDD)이 그냥 보유했을 때보다 작을 것")
LIVE_MIN_DAYS = 20


def judge_training(test_ret: float, train_ret: float, positive_ratio: float | None,
                   test_mdd: float | None = None, bh_mdd: float | None = None) -> tuple[str, list[str]]:
    """훈련 결과 판정 → ('pass' | 'fail', 사유 목록). 학습→검증 성적 하락은 참고 경고로만 붙입니다."""
    reasons = []
    if test_ret <= 0:
        reasons.append(f"검증 구간 손실 ({test_ret:+.1f}%)")
    if positive_ratio is not None and positive_ratio < 50:
        reasons.append(f"수익 구간 비율 {positive_ratio:.0f}% < 50%")
    if test_mdd is not None and bh_mdd is not None and test_mdd >= bh_mdd:
        reasons.append(f"검증 낙폭 {test_mdd:.0f}% ≥ 보유 낙폭 {bh_mdd:.0f}%")
    verdict = "fail" if reasons else "pass"
    if train_ret - test_ret > 100:
        reasons.append(f"참고: 학습 {train_ret:+.0f}% → 검증 {test_ret:+.0f}% (과적합 가능성)")
    return verdict, reasons


def judge_live(live_ret: float, live_mdd: float, bh_ret: float, days: int, test_mdd: float | None) -> tuple[str, str]:
    """실전 리그 판정 → (상태, 설명). 상태: watch / ok / warn / retire"""
    if days < LIVE_MIN_DAYS:
        return "watch", f"관찰 중 ({days}/{LIVE_MIN_DAYS}거래일)"
    mdd_limit = max(1.5 * (test_mdd or 0), 30.0)
    if live_ret < 0 and live_mdd > mdd_limit:
        return "retire", f"손실 중이고 MDD {live_mdd:.0f}% > 허용 {mdd_limit:.0f}% → 교체 권고"
    if live_ret >= bh_ret - 10 and live_mdd <= mdd_limit:
        return "ok", "보유 대비 −10%p 이내 성적 · 낙폭 허용 범위"
    return "warn", f"보유 대비 {live_ret - bh_ret:+.1f}%p · MDD {live_mdd:.0f}% → 지켜보기"


# ==========================================================================
# 9. 뉴스 키워드 센티먼트
# ==========================================================================
POS_WORDS = ["surge", "surges", "beat", "beats", "rally", "rallies", "strong", "growth", "gain", "gains",
             "jump", "jumps", "upgrade", "upgraded", "record", "boost", "bullish", "soar", "soars",
             "outperform", "raises", "tops"]
NEG_WORDS = ["miss", "misses", "drop", "drops", "weak", "fall", "falls", "slump", "lawsuit", "cut", "cuts",
             "downgrade", "downgraded", "loss", "losses", "plunge", "plunges", "delay", "delays", "sink",
             "sinks", "bearish", "probe", "tariff", "tariffs", "ban", "slides", "tumble", "tumbles"]
_POS_RE = re.compile(r"\b(" + "|".join(POS_WORDS) + r")\b", re.IGNORECASE)
_NEG_RE = re.compile(r"\b(" + "|".join(NEG_WORDS) + r")\b", re.IGNORECASE)


def analyze_sentiment(text: str) -> tuple[str, int, list[str], list[str]]:
    pos = sorted({m.lower() for m in _POS_RE.findall(text or "")})
    neg = sorted({m.lower() for m in _NEG_RE.findall(text or "")})
    score = len(pos) - len(neg)
    label = "호재" if score > 0 else "악재" if score < 0 else "중립"
    return label, score, pos, neg


def highlight_keywords(text: str) -> str:
    """HTML 이스케이프 후 키워드만 <span>으로 강조 (외부 문자열 주입 방지)."""
    safe = html.escape(text or "")
    safe = _POS_RE.sub(r'<span class="kw-pos">\1</span>', safe)
    return _NEG_RE.sub(r'<span class="kw-neg">\1</span>', safe)
