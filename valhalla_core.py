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

# 앱(valhalla.py)이 이 값과 다르면 모듈을 다시 불러옵니다. 계산 모듈을 바꿀 때마다 올려 주세요.
CORE_VERSION = "2026.11.24-2"

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


# ==========================================================================
# 10. 반등 신호 관제 (주봉 체크리스트 · 거시 신호 · 점수별 과거 확률 · 예측 장부)
#   - 모든 신호는 '그 주 금요일까지의 데이터'만 사용합니다 (미래 데이터 누수 없음).
#   - 확률은 공식이 아니라 과거에 같은 점수가 나왔던 주의 실제 결과 비율입니다.
#   - 최근 2년은 학습에 쓰지 않는 검증 구간으로 따로 보여 줍니다 (과최적화 점검).
# ==========================================================================
CHART_SIGNALS = [
    ("low_hold", "저점 유지", "이번 주 저가가 직전 8주 최저가보다 높음 (신저가 갱신 멈춤)"),
    ("weekly_up", "주봉 상승", "이번 주 종가가 지난주 종가보다 높음"),
    ("macd_up", "MACD 개선", "주봉 MACD 히스토그램이 지난주보다 커짐 (하락 힘 약화)"),
    ("rsi_up", "RSI 상승", "주봉 RSI(14)가 지난주보다 높음"),
    ("above_ma20d", "20일선 회복", "금요일 종가가 일봉 20일 이동평균선 위"),
    ("soxx_up", "1배 지수 동행", "SOXX(레버리지 없는 반도체 ETF)도 이번 주 상승"),
]
MACRO_SIGNALS = [
    ("rate_down", "금리 하락", "미국 10년물 금리가 4주 전보다 낮음", "^TNX"),
    ("dollar_down", "달러 약세", "달러 인덱스가 4주 전보다 낮음", "DX-Y.NYB"),
    ("oil_calm", "유가 안정", "WTI 유가 4주 상승률이 +10% 미만", "CL=F"),
    ("fear_easing", "공포 진정", "VIX가 이번 주 하락했고 8주 고점보다 15% 이상 낮음", "^VIX"),
]
MACRO_TICKERS_SIGNAL = {key: ticker for key, _, _, ticker in MACRO_SIGNALS}
HORIZONS = (4, 12)          # 주 단위 예측 기간
TEST_WEEKS = 104            # 최근 2년 = 검증 구간
SHRINK_K = 10               # 표본이 적은 점수는 전체 평균 쪽으로 당겨 과신을 막음


def signal_on(value) -> bool:
    return bool(pd.notna(value) and bool(value))


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """일봉 OHLCV → 주봉 (금요일 기준 라벨)."""
    w = daily.resample("W-FRI").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last",
                                     "Volume": "sum"})
    return w.dropna(subset=["Close"])


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    macd = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    return macd - macd.ewm(span=signal, adjust=False).mean()


def chart_signal_table(daily: pd.DataFrame, partner_daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """주별 차트 신호(True/False)와 점수. partner_daily 는 SOXX 같은 1배 지수."""
    w = to_weekly(daily)
    close = w["Close"]
    out = pd.DataFrame(index=w.index)
    out["close"] = close
    prior_low = w["Low"].shift(1).rolling(8, min_periods=8).min()
    out["low_hold"] = (w["Low"] > prior_low).where(prior_low.notna())
    out["weekly_up"] = (close > close.shift(1)).where(close.shift(1).notna())
    hist = macd_hist(close)
    out["macd_up"] = (hist > hist.shift(1)).where(close.index >= close.index[min(len(close) - 1, 34)])
    rsi = wilder_rsi(close)
    out["rsi_up"] = (rsi > rsi.shift(1)).where(rsi.shift(1).notna())
    ma20 = daily["Close"].rolling(20).mean()
    above = (daily["Close"] > ma20).where(ma20.notna())
    out["above_ma20d"] = above.resample("W-FRI").last().reindex(out.index)
    if partner_daily is not None and not partner_daily.empty:
        pc = to_weekly(partner_daily)["Close"].reindex(out.index)
        out["soxx_up"] = (pc > pc.shift(1)).where(pc.notna() & pc.shift(1).notna())
    else:
        out["soxx_up"] = np.nan
    keys = [k for k, _, _ in CHART_SIGNALS]
    out["chart_score"] = out[keys].apply(lambda r: int(sum(bool(v) for v in r if pd.notna(v))), axis=1)
    out["chart_valid"] = out[keys].notna().sum(axis=1)
    out["weekly_rsi"] = rsi
    out["macd_hist"] = hist
    for h in HORIZONS:
        out[f"ret_{h}w"] = (close.shift(-h) / close - 1) * 100
    return out


def macro_signal_table(macro_daily: dict[str, pd.DataFrame | None], index: pd.DatetimeIndex) -> pd.DataFrame:
    """주별 거시 신호. macro_daily = {ticker: 일봉 DataFrame}. 없는 지표는 NaN."""
    out = pd.DataFrame(index=index)

    def weekly_close(ticker):
        df = macro_daily.get(ticker)
        if df is None or df.empty:
            return None
        return df["Close"].resample("W-FRI").last().reindex(index).ffill(limit=1)

    tnx, dxy, oil, vix = (weekly_close(MACRO_TICKERS_SIGNAL[k]) for k in
                          ("rate_down", "dollar_down", "oil_calm", "fear_easing"))
    out["rate_down"] = (tnx < tnx.shift(4)).where(tnx.shift(4).notna()) if tnx is not None else np.nan
    out["dollar_down"] = (dxy < dxy.shift(4)).where(dxy.shift(4).notna()) if dxy is not None else np.nan
    if oil is not None:
        chg = oil / oil.shift(4) - 1
        out["oil_calm"] = (chg < 0.10).where(chg.notna())
    else:
        out["oil_calm"] = np.nan
    if vix is not None:
        peak = vix.rolling(8, min_periods=8).max()
        out["fear_easing"] = ((vix < vix.shift(1)) & (vix <= peak * 0.85)).where(peak.notna() & vix.shift(1).notna())
    else:
        out["fear_easing"] = np.nan
    keys = [k for k, _, _, _ in MACRO_SIGNALS]
    out["macro_score"] = out[keys].apply(lambda r: int(sum(bool(v) for v in r if pd.notna(v))), axis=1)
    out["macro_valid"] = out[keys].notna().sum(axis=1)
    return out


def build_signal_history(daily: pd.DataFrame, partner_daily: pd.DataFrame | None,
                         macro_daily: dict[str, pd.DataFrame | None] | None) -> pd.DataFrame:
    chart = chart_signal_table(daily, partner_daily)
    macro = macro_signal_table(macro_daily or {}, chart.index)
    return chart.join(macro)


def _bucket_stats(frame: pd.DataFrame, score_col: str, horizon: int, max_score: int) -> pd.DataFrame:
    col = f"ret_{horizon}w"
    data = frame.dropna(subset=[col])
    base = float((data[col] > 0).mean() * 100) if len(data) else float("nan")
    rows = []
    for s in range(max_score + 1):
        g = data.loc[data[score_col] == s, col]
        rows.append({"점수": s, "표본(주)": int(len(g)),
                     "상승 확률(%)": round(float((g > 0).mean() * 100), 1) if len(g) else None,
                     "평균 수익(%)": round(float(g.mean()), 1) if len(g) else None,
                     "중앙값(%)": round(float(g.median()), 1) if len(g) else None})
    table = pd.DataFrame(rows)
    table.attrs["base"] = base
    table.attrs["n"] = int(len(data))
    return table


def score_backtest(history: pd.DataFrame, score_col: str = "chart_score", min_valid: int | None = None,
                   test_weeks: int = TEST_WEEKS) -> dict:
    """점수별 과거 성적. 학습 구간(최근 2년 제외)과 검증 구간(최근 2년)을 나눠 계산."""
    max_score = len(CHART_SIGNALS) if score_col == "chart_score" else len(MACRO_SIGNALS)
    valid_col = "chart_valid" if score_col == "chart_score" else "macro_valid"
    need = max_score - 1 if min_valid is None else min_valid   # 신호가 대부분 계산된 주만 사용
    h = history[history[valid_col] >= need]
    cut = h.index[-1] - pd.Timedelta(weeks=test_weeks) if len(h) else None
    train = h[h.index <= cut] if cut is not None else h
    test = h[h.index > cut] if cut is not None else h.iloc[0:0]
    out = {"cutoff": cut, "start": h.index[0] if len(h) else None}
    for hz in HORIZONS:
        out[f"train_{hz}"] = _bucket_stats(train, score_col, hz, max_score)
        out[f"test_{hz}"] = _bucket_stats(test, score_col, hz, max_score)
    return out


def shrunk_probability(table: pd.DataFrame, score: int, k: float = SHRINK_K) -> tuple[float, int]:
    """해당 점수의 상승 확률 (표본이 적으면 전체 평균 쪽으로 보정). (확률%, 표본 수)"""
    base = table.attrs.get("base", float("nan"))
    row = table[table["점수"] == score]
    if row.empty or not np.isfinite(base):
        return base, 0
    n = int(row["표본(주)"].iloc[0])
    p = row["상승 확률(%)"].iloc[0]
    if n == 0 or p is None or pd.isna(p):
        return float(base), 0
    return float((p * n + base * k) / (n + k)), n


def complete_weeks(history: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    """asof(미국 날짜) 기준으로 마감된 주만 남김."""
    return history[history.index <= pd.Timestamp(asof).normalize()]


def make_prediction(history: pd.DataFrame, ticker: str = "SOXL") -> dict | None:
    """가장 최근 '마감된' 주의 신호로 예측 1건 생성 (과거 확률표는 그 주 이전 데이터만 사용)."""
    if history is None or history.empty:
        return None
    last = history.iloc[-1]
    week = history.index[-1]
    n_rows = len(history)
    pred = {"week": week.strftime("%Y-%m-%d"), "ticker": ticker, "close": round(float(last["close"]), 4),
            "chart_score": int(last["chart_score"]), "macro_score": int(last["macro_score"]),
            "signals": ",".join(lbl for key, lbl, _ in CHART_SIGNALS if signal_on(last.get(key))),
            "macro_signals": ",".join(lbl for key, lbl, _, _ in MACRO_SIGNALS if signal_on(last.get(key)))}
    for hz in HORIZONS:
        # 예측 시점에 이미 결과가 확정된 주만 사용 (j + hz <= 예측 주) → 미래 정보 누수 방지
        past = history.iloc[: max(n_rows - hz, 0)]
        table = _bucket_stats(past[past["chart_valid"] >= len(CHART_SIGNALS) - 1], "chart_score", hz,
                              len(CHART_SIGNALS))
        p, n = shrunk_probability(table, pred["chart_score"])
        pred[f"p_up_{hz}w"] = round(p, 1) if np.isfinite(p) else None
        pred[f"base_{hz}w"] = round(table.attrs["base"], 1) if np.isfinite(table.attrs["base"]) else None
        pred[f"n_{hz}w"] = n
    return pred


def grade_predictions(ledger: pd.DataFrame, weekly_close: pd.Series) -> pd.DataFrame:
    """예측 장부에 실제 결과(4·12주 뒤 수익률)를 채움. 기간이 안 지난 건 비워 둠."""
    if ledger is None or ledger.empty:
        return ledger
    out = ledger.copy()
    wc = weekly_close.dropna()
    for i, row in out.iterrows():
        week = pd.Timestamp(row["week"])
        if week not in wc.index:
            continue
        pos = wc.index.get_loc(week)
        for hz in HORIZONS:
            col = f"ret_{hz}w"
            if col in out.columns and pd.notna(row.get(col)):
                continue
            if pos + hz < len(wc):
                out.at[i, col] = round(float((wc.iloc[pos + hz] / wc.iloc[pos] - 1) * 100), 2)
    return out


def calibration(ledger: pd.DataFrame, horizon: int = 4) -> tuple[pd.DataFrame, dict]:
    """'AI가 60%라고 한 예측이 실제로 60% 맞았나' 표 + 요약(브라이어 점수 vs 기준선)."""
    pcol, rcol, bcol = f"p_up_{horizon}w", f"ret_{horizon}w", f"base_{horizon}w"
    if ledger is None or ledger.empty or rcol not in ledger:
        return pd.DataFrame(), {}
    d = ledger.dropna(subset=[pcol, rcol]).copy()
    if d.empty:
        return pd.DataFrame(), {"n": 0}
    d["hit"] = (d[rcol] > 0).astype(float)
    bins = [0, 40, 50, 60, 70, 101]
    labels = ["40% 미만", "40~50%", "50~60%", "60~70%", "70% 이상"]
    d["구간"] = pd.cut(d[pcol], bins=bins, labels=labels, right=False)
    table = (d.groupby("구간", observed=False)
               .agg(예측수=("hit", "size"), 예측확률평균=(pcol, "mean"), 실제상승비율=("hit", "mean"))
               .reset_index())
    table["예측확률평균"] = table["예측확률평균"].round(1)
    table["실제상승비율"] = (table["실제상승비율"] * 100).round(1)
    brier = float(((d[pcol] / 100 - d["hit"]) ** 2).mean())
    base = d[bcol].fillna(d[pcol]) / 100 if bcol in d else pd.Series(d["hit"].mean(), index=d.index)
    brier_base = float(((base - d["hit"]) ** 2).mean())
    direction_hit = float(((d[pcol] >= 50) == (d["hit"] == 1)).mean() * 100)
    return table, {"n": int(len(d)), "brier": brier, "brier_base": brier_base,
                   "direction_hit": direction_hit, "up_rate": float(d["hit"].mean() * 100)}


# ==========================================================================
# 11. 매수 · 매도 행동 신호 (규칙을 먼저 정하고, 과거 성적을 함께 보여 줌)
#   - 규칙은 결과를 보고 맞춘 값이 아니라 처음에 정한 단순한 기준입니다 (과최적화 방지).
#   - 신호는 '마감된 주봉'에서 조건이 처음 충족된 주에만 한 번 표시합니다.
# ==========================================================================
ACTION_RULES = {
    "buy": {"chart_min": 3, "weeks": 2, "macro_min": 2},
    "sell": {"chart_max": 1, "weeks": 2, "below_ma": True},
}
ACTION_RULE_TEXT = {
    "매수": "차트 신호 3점 이상이 2주 연속 + 거시 신호 2점 이상",
    "매도": "차트 신호 1점 이하가 2주 연속 + 금요일 종가가 20일선 아래",
}


def action_signals(history: pd.DataFrame, rules: dict | None = None) -> pd.DataFrame:
    """주별 행동 신호. action: '매수' / '매도' / '' (조건이 처음 켜진 주에만), state: 신호 이후 상태."""
    r = rules or ACTION_RULES
    b, s = r["buy"], r["sell"]
    out = history.copy()
    chart = out["chart_score"].astype(float)
    buy_on = (chart.rolling(b["weeks"]).min() >= b["chart_min"]) & (out["macro_score"] >= b["macro_min"])
    above = out["above_ma20d"].map(lambda v: bool(v) if pd.notna(v) else True)
    sell_on = chart.rolling(s["weeks"]).max() <= s["chart_max"]
    if s.get("below_ma", True):
        sell_on = sell_on & ~above
    out["buy_on"], out["sell_on"] = buy_on.fillna(False), sell_on.fillna(False)
    action, state, cur = [], [], ""
    prev_buy = prev_sell = False
    for bo, so in zip(out["buy_on"], out["sell_on"]):
        a = ""
        if bo and not prev_buy and cur != "매수":
            a, cur = "매수", "매수"
        elif so and not prev_sell and cur != "매도":
            a, cur = "매도", "매도"
        action.append(a)
        state.append(cur)
        prev_buy, prev_sell = bool(bo), bool(so)
    out["action"], out["state"] = action, state
    return out


def _action_stats(frame: pd.DataFrame, kind: str) -> pd.DataFrame:
    rows = []
    for hz in HORIZONS:
        col = f"ret_{hz}w"
        all_ret = frame[col].dropna()
        g = frame.loc[frame["action"] == kind, col].dropna()
        good = (g > 0) if kind == "매수" else (g < 0)
        base_good = (all_ret > 0) if kind == "매수" else (all_ret < 0)
        rows.append({"기간": f"{hz}주 뒤", "신호 수": int(len(g)),
                     "적중률(%)": round(float(good.mean() * 100), 1) if len(g) else None,
                     "평소 확률(%)": round(float(base_good.mean() * 100), 1) if len(all_ret) else None,
                     "평균 수익(%)": round(float(g.mean()), 1) if len(g) else None,
                     "평소 평균(%)": round(float(all_ret.mean()), 1) if len(all_ret) else None})
    return pd.DataFrame(rows)


def action_backtest(sig: pd.DataFrame, test_weeks: int = TEST_WEEKS, fee_bps: float = 10) -> dict:
    """매수·매도 신호의 과거 적중률(학습/검증 분리) + '신호대로 했다면' 누적 수익."""
    h = sig[sig["chart_valid"] >= len(CHART_SIGNALS) - 1]
    if h.empty:
        return {}
    cut = h.index[-1] - pd.Timedelta(weeks=test_weeks)
    out = {"cutoff": cut, "start": h.index[0]}
    for name, part in (("train", h[h.index <= cut]), ("test", h[h.index > cut])):
        out[f"{name}_buy"] = _action_stats(part, "매수")
        out[f"{name}_sell"] = _action_stats(part, "매도")
    # 신호 전략: 매수 신호 다음 주부터 보유, 매도 신호 다음 주부터 현금 (처음엔 보유 상태로 시작)
    weekly_ret = h["close"].pct_change().fillna(0)
    pos = h["state"].map({"매수": 1.0, "매도": 0.0, "": 1.0}).shift(1).fillna(1.0)
    turn = pos.diff().abs().fillna(0)
    strat = (1 + pos * weekly_ret - turn * fee_bps / 10_000).cumprod()
    hold = (1 + weekly_ret).cumprod()

    def mdd(eq):
        return float(((eq / eq.cummax()) - 1).min() * 100)

    out["equity"] = pd.DataFrame({"신호대로": strat, "계속 보유": hold})
    out["summary"] = {"strategy": float((strat.iloc[-1] - 1) * 100), "hold": float((hold.iloc[-1] - 1) * 100),
                      "strategy_mdd": mdd(strat), "hold_mdd": mdd(hold),
                      "exposure": float(pos.mean() * 100), "trades": int((h["action"] != "").sum())}
    return out


# ==========================================================================
# 12. 유형별 추적 종목 (3배 · 2배 · 1배 ETF · 개별 종목은 결과를 섞지 않고 따로 봄)
# ==========================================================================
CATEGORIES = ["3배 ETF", "2배 ETF", "1배 ETF", "개별 종목"]
# 섹터 × 레버리지 격자. 섹터마다 (3배, 2배, 1배, 개별 종목들, 1배 ETF의 비교 지수)
#  - 레버리지 ETF·개별 종목의 비교 지수 = 같은 섹터의 1배 ETF
#  - 해당 레버리지 상품이 없는 섹터는 None (예: 에너지 3배는 상장폐지)
SECTOR_GRID = {
    "반도체": ("SOXL", "USD", "SOXX", ["NVDA", "AVGO", "MU", "MRVL"], "QQQ"),
    "나스닥100": ("TQQQ", "QLD", "QQQ", ["AAPL"], "SPY"),
    "S&P500": ("UPRO", "SSO", "SPY", ["BRK-B"], "IWM"),
    "기술": ("TECL", "ROM", "XLK", ["MSFT"], "SPY"),
    "금융": ("FAS", "UYG", "XLF", ["JPM"], "SPY"),
    "헬스케어": ("CURE", "RXL", "XLV", ["LLY"], "SPY"),
    "바이오": ("LABU", "BIB", "XBI", ["VRTX"], "SPY"),
    "소형주": ("TNA", "UWM", "IWM", ["CROX"], "SPY"),
    "산업재": ("DUSL", "UXI", "XLI", ["CAT"], "SPY"),
    "부동산": ("DRN", "URE", "XLRE", ["PLD"], "SPY"),
    "유틸리티": ("UTSL", "UPW", "XLU", ["NEE"], "SPY"),
    "에너지": (None, "ERX", "XLE", ["XOM"], "SPY"),
    "금광": (None, "NUGT", "GDX", ["NEM"], "SPY"),
}


def _build_universe(grid: dict) -> dict[str, tuple[str, str, str]]:
    uni = {}
    for sector, (x3, x2, x1, stocks, bench) in grid.items():
        for t, cat in ((x3, "3배 ETF"), (x2, "2배 ETF")):
            if t:
                uni[t] = (x1, cat, sector)
        uni[x1] = (bench, "1배 ETF", sector)
        for t in stocks:
            uni[t] = (x1, "개별 종목", sector)
    return uni


SIGNAL_UNIVERSE = _build_universe(SECTOR_GRID)      # 티커: (비교 지수, 유형, 섹터)
SECTORS = list(SECTOR_GRID)


def category_of(ticker: str) -> str:
    return SIGNAL_UNIVERSE.get(str(ticker).upper(), (None, "기타", "기타"))[1]


def sector_of(ticker: str) -> str:
    return SIGNAL_UNIVERSE.get(str(ticker).upper(), (None, "기타", "기타"))[2]


def partner_of(ticker: str, default: str = "SOXX") -> str:
    return SIGNAL_UNIVERSE.get(str(ticker).upper(), (default, None, None))[0]


def ticker_summary(sig: pd.DataFrame, ticker: str) -> dict:
    """한 종목의 매수 신호 과거 성적 요약 (유형별 비교표용). 최근 2년 검증 구간 따로."""
    bt = action_backtest(sig)
    if not bt:
        return {}
    row = {"종목": ticker, "섹터": sector_of(ticker), "유형": category_of(ticker)}
    for part, label in (("train", "학습"), ("test", "검증")):
        t = bt[f"{part}_buy"].set_index("기간")
        r = t.loc["4주 뒤"] if "4주 뒤" in t.index else None
        row[f"{label} 신호 수"] = int(r["신호 수"]) if r is not None else 0
        hit, base = (r["적중률(%)"], r["평소 확률(%)"]) if r is not None else (None, None)
        row[f"{label} 4주 적중률(%)"] = hit
        row[f"{label} 평소(%)"] = base
        row[f"{label} 차이(%p)"] = round(hit - base, 1) if hit is not None and base is not None else None
    sm = bt["summary"]
    row["신호대로 누적(%)"] = round(sm["strategy"], 1)
    row["보유 누적(%)"] = round(sm["hold"], 1)
    row["신호대로 MDD(%)"] = round(sm["strategy_mdd"], 1)
    row["보유 MDD(%)"] = round(sm["hold_mdd"], 1)
    return row


def ledger_by_category(ledger: pd.DataFrame, horizon: int = 4) -> pd.DataFrame:
    """실전 예측 장부를 유형별로 채점 (저장된 실제 수익률 사용)."""
    if ledger is None or ledger.empty:
        return pd.DataFrame()
    d = ledger.copy()
    d["유형"] = d["ticker"].map(category_of)
    rows = []
    for cat in CATEGORIES + ["기타"]:
        g = d[d["유형"] == cat]
        if g.empty:
            continue
        _, summ = calibration(g, horizon)
        rows.append({"유형": cat, "종목 수": int(g["ticker"].nunique()), "예측 수": int(len(g)),
                     "채점 수": summ.get("n", 0),
                     "방향 적중률(%)": round(summ["direction_hit"], 1) if summ.get("n") else None,
                     "브라이어": round(summ["brier"], 3) if summ.get("n") else None,
                     "평소 확률 브라이어": round(summ["brier_base"], 3) if summ.get("n") else None})
    return pd.DataFrame(rows)


def ledger_by_sector(ledger: pd.DataFrame, horizon: int = 4) -> pd.DataFrame:
    """실전 예측 장부를 섹터별로 채점."""
    if ledger is None or ledger.empty:
        return pd.DataFrame()
    d = ledger.copy()
    d["섹터"] = d["ticker"].map(sector_of)
    rows = []
    for sec in SECTORS + ["기타"]:
        g = d[d["섹터"] == sec]
        if g.empty:
            continue
        _, summ = calibration(g, horizon)
        rows.append({"섹터": sec, "예측 수": int(len(g)), "채점 수": summ.get("n", 0),
                     "방향 적중률(%)": round(summ["direction_hit"], 1) if summ.get("n") else None,
                     "브라이어": round(summ["brier"], 3) if summ.get("n") else None,
                     "평소 확률 브라이어": round(summ["brier_base"], 3) if summ.get("n") else None})
    return pd.DataFrame(rows)


def compare_tables(cmp_df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """종목별 비교표 → 유형별 요약, 섹터별 요약, 섹터×유형 격자(검증 구간 차이)."""
    if cmp_df is None or cmp_df.empty:
        return {}
    agg = dict(종목수=("종목", "count"), 학습_차이=("학습 차이(%p)", "mean"), 검증_차이=("검증 차이(%p)", "mean"),
               신호대로=("신호대로 누적(%)", "median"), 보유=("보유 누적(%)", "median"),
               신호대로_MDD=("신호대로 MDD(%)", "median"), 보유_MDD=("보유 MDD(%)", "median"))
    names = {"학습_차이": "학습 차이(%p)", "검증_차이": "검증 차이(%p)", "신호대로": "신호대로 누적(%)",
             "보유": "보유 누적(%)", "신호대로_MDD": "신호대로 MDD(%)", "보유_MDD": "보유 MDD(%)"}
    by_cat = (cmp_df.groupby("유형").agg(**agg).round(1).reindex(CATEGORIES).dropna(how="all")
                    .reset_index().rename(columns=names))
    by_sec = (cmp_df.groupby("섹터").agg(**agg).round(1).reindex(SECTORS).dropna(how="all")
                    .reset_index().rename(columns=names))
    grid = (cmp_df.pivot_table(index="섹터", columns="유형", values="검증 차이(%p)", aggfunc="mean")
                  .reindex(index=SECTORS, columns=CATEGORIES).dropna(how="all").round(1))
    plus_share = float((cmp_df["검증 차이(%p)"].dropna() > 0).mean() * 100) if cmp_df["검증 차이(%p)"].notna().any() else float("nan")
    return {"by_cat": by_cat, "by_sec": by_sec, "grid": grid, "plus_share": plus_share}


# ==========================================================================
# 13. 매일 자동 점검 (비교 결과 스냅샷 · 실전 건강 상태)
# ==========================================================================
REPORT_TABLE = "valhalla_signal_reports"
REPORT_MAP = {"train_n": "학습 신호 수", "train_hit": "학습 4주 적중률(%)", "train_base": "학습 평소(%)",
              "train_diff": "학습 차이(%p)", "test_n": "검증 신호 수", "test_hit": "검증 4주 적중률(%)",
              "test_base": "검증 평소(%)", "test_diff": "검증 차이(%p)", "strat_ret": "신호대로 누적(%)",
              "hold_ret": "보유 누적(%)", "strat_mdd": "신호대로 MDD(%)", "hold_mdd": "보유 MDD(%)"}


def _num_or_none(v):
    try:
        f = float(v)
        return f if np.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def report_row(sig: pd.DataFrame, ticker: str, date: str) -> dict | None:
    """매일 저장할 종목별 스냅샷 (ticker_summary + 이번 주 상태)."""
    summ = ticker_summary(sig, ticker)
    if not summ:
        return None
    last = sig.iloc[-1]
    row = {"date": date, "ticker": ticker, "sector": summ["섹터"], "category": summ["유형"],
           "chart_score": int(last["chart_score"]), "macro_score": int(last["macro_score"]), "state": last["state"] or ""}
    for key, label in REPORT_MAP.items():
        v = _num_or_none(summ.get(label))
        row[key] = int(v) if v is not None and key.endswith("_n") else v
    return row


def report_to_compare(rows: pd.DataFrame) -> pd.DataFrame:
    """저장된 스냅샷 → 화면의 비교표(ticker_summary) 형식."""
    if rows is None or rows.empty:
        return pd.DataFrame()
    df = rows.rename(columns={"ticker": "종목", "sector": "섹터", "category": "유형", **REPORT_MAP})
    keep = ["종목", "섹터", "유형"] + list(REPORT_MAP.values())
    return df[[c for c in keep if c in df.columns]]


def plus_share_trend(reports: pd.DataFrame) -> pd.DataFrame:
    """날짜별 '검증 구간에서 평소보다 나았던 종목 비율' 추이 (전체·유형별)."""
    if reports is None or reports.empty:
        return pd.DataFrame()
    d = reports.dropna(subset=["test_diff"]).copy()
    d["plus"] = (d["test_diff"] > 0).astype(float) * 100
    total = d.groupby("date")["plus"].mean().rename("전체")
    by_cat = d.pivot_table(index="date", columns="category", values="plus", aggfunc="mean")
    out = pd.concat([total, by_cat.reindex(columns=[c for c in CATEGORIES if c in by_cat.columns])], axis=1)
    out.index = pd.to_datetime(out.index)
    return out.sort_index().round(1)


def live_health(ledger: pd.DataFrame, horizon: int = 4, min_n: int = 20) -> list[dict]:
    """실전 예측의 유형별 건강 상태: 표본 부족 / 정상(평소 확률보다 나음) / 주의(못함)."""
    out = []
    if ledger is None or ledger.empty:
        return out
    d = ledger.copy()
    d["유형"] = d["ticker"].map(category_of)
    for cat in CATEGORIES:
        g = d[d["유형"] == cat]
        _, summ = calibration(g, horizon) if not g.empty else (None, {})
        n = summ.get("n", 0) if summ else 0
        if n < min_n:
            status, msg = "표본 부족", f"채점 {n}건 / 판단에는 {min_n}건 이상 필요"
        elif summ["brier"] < summ["brier_base"]:
            status, msg = "정상", f"채점 {n}건 · 평소 확률보다 정확 (브라이어 {summ['brier']:.3f} < {summ['brier_base']:.3f})"
        else:
            status, msg = "주의", f"채점 {n}건 · 평소 확률보다 부정확 (브라이어 {summ['brier']:.3f} ≥ {summ['brier_base']:.3f})"
        out.append({"유형": cat, "상태": status, "설명": msg, "n": n})
    return out



# ==========================================================================
# 14. 규칙 개선 장치 (후보 규칙을 시험해서 '확실히 나을 때만' 교체 제안)
#   1) 후보 규칙 = 지금 규칙의 숫자를 조금씩 바꾼 조합
#   2) 학습 구간(최근 2년 제외)으로만 가장 좋은 후보 1개를 고름
#   3) 고를 때 쓰지 않은 최근 2년(검증 구간)에서 지금 규칙보다 분명히 나을 때만 '제안'
#   4) 적용은 사람이 화면에서 승인 → 버전 기록
# ==========================================================================
RULE_TABLE = "valhalla_signal_rules"
ADOPT_MIN = {"plus_share_gain": 10.0, "diff_gain": 3.0, "min_signals": 30, "mdd_slack": 5.0, "min_weeks": 8}


def rule_text(rules: dict | None = None) -> dict[str, str]:
    r = rules or ACTION_RULES
    b, s = r["buy"], r["sell"]
    macro = f" + 거시 신호 {b['macro_min']}점 이상" if b.get("macro_min", 0) > 0 else ""
    ma = " + 금요일 종가가 20일선 아래" if s.get("below_ma", True) else ""
    return {"매수": f"차트 신호 {b['chart_min']}점 이상이 {b['weeks']}주 연속{macro}",
            "매도": f"차트 신호 {s['chart_max']}점 이하가 {s['weeks']}주 연속{ma}"}


def normalize_rules(rules: dict | None) -> dict:
    base = {"buy": dict(ACTION_RULES["buy"]), "sell": dict(ACTION_RULES["sell"])}
    for side in ("buy", "sell"):
        base[side].update({k: v for k, v in ((rules or {}).get(side) or {}).items() if k in base[side]})
    return base


def candidate_rules() -> list[dict]:
    out = []
    for cmin in (2, 3, 4, 5):
        for bw in (1, 2, 3):
            for mmin in (0, 1, 2, 3):
                for smax in (0, 1, 2):
                    for sw in (1, 2, 3):
                        for below in (True, False):
                            out.append({"buy": {"chart_min": cmin, "weeks": bw, "macro_min": mmin},
                                        "sell": {"chart_max": smax, "weeks": sw, "below_ma": below}})
    return out


def _rule_key(rules: dict) -> tuple:
    r = normalize_rules(rules)
    return tuple(sorted(r["buy"].items())) + tuple(sorted(r["sell"].items()))


def evaluate_rule(histories: dict[str, pd.DataFrame], rules: dict, part: str = "train") -> dict:
    """여러 종목에 같은 규칙을 적용한 성적. part = 'train'(최근 2년 제외) 또는 'test'(최근 2년)."""
    diffs, signals, mdds, rets, holds, weeks = [], 0, [], [], [], set()
    for _, hist in histories.items():
        if hist is None or hist.empty:
            continue
        sig = action_signals(hist, rules)
        bt = action_backtest(sig)
        if not bt:
            continue
        buys = sig.index[(sig["action"] == "매수") & sig["ret_4w"].notna()]
        weeks.update(buys[buys > bt["cutoff"]] if part == "test" else buys[buys <= bt["cutoff"]])
        t = bt[f"{part}_buy"].set_index("기간")
        if "4주 뒤" not in t.index:
            continue
        r = t.loc["4주 뒤"]
        if r["신호 수"] > 0 and r["적중률(%)"] is not None and r["평소 확률(%)"] is not None:
            diffs.append(float(r["적중률(%)"]) - float(r["평소 확률(%)"]))
            signals += int(r["신호 수"])
        sm = bt["summary"]
        mdds.append(sm["strategy_mdd"])
        rets.append(sm["strategy"])
        holds.append(sm["hold"])
    if not diffs:
        return {"n_tickers": 0, "signals": 0, "weeks": 0, "avg_diff": None, "plus_share": None,
                "median_mdd": None, "median_ret": None, "median_hold": None}
    d = np.array(diffs)
    return {"n_tickers": int(len(d)), "signals": int(signals), "weeks": int(len(weeks)),
            "avg_diff": round(float(d.mean()), 2),
            "plus_share": round(float((d > 0).mean() * 100), 1),
            "median_mdd": round(float(np.median(mdds)), 1) if mdds else None,
            "median_ret": round(float(np.median(rets)), 1) if rets else None,
            "median_hold": round(float(np.median(holds)), 1) if holds else None}


def _train_score(ev: dict, min_signals: int, min_weeks: int = 0) -> float:
    """학습 구간 점수: 평균 차이 + 평소보다 나은 종목 비율 (신호가 너무 적거나 몰려 있으면 탈락)."""
    if not ev or ev["avg_diff"] is None or ev["signals"] < min_signals or ev.get("weeks", 0) < min_weeks:
        return -1e9
    return ev["avg_diff"] + (ev["plus_share"] - 50) * 0.2


def review_rules(histories: dict[str, pd.DataFrame], current: dict, candidates: list[dict] | None = None,
                 thresholds: dict | None = None, progress=None) -> dict:
    """규칙 점검: 학습 구간으로 최고 후보 1개 선택 → 검증 구간에서 지금 규칙보다 확실히 나을 때만 adopt=True."""
    th = {**ADOPT_MIN, **(thresholds or {})}
    current = normalize_rules(current)
    cands = candidates if candidates is not None else candidate_rules()
    min_train_signals = th["min_signals"] * 3
    best, best_score, best_train = None, -1e9, None
    for i, cand in enumerate(cands):
        if _rule_key(cand) == _rule_key(current):
            continue
        ev = evaluate_rule(histories, cand, "train")
        sc = _train_score(ev, min_train_signals, th["min_weeks"] * 3)
        if sc > best_score:
            best, best_score, best_train = cand, sc, ev
        if progress:
            progress(i + 1, len(cands))
    cur_train = evaluate_rule(histories, current, "train")
    cur_test = evaluate_rule(histories, current, "test")
    result = {"current": current, "current_train": cur_train, "current_test": cur_test,
              "candidate": best, "candidate_train": best_train, "candidate_test": None,
              "adopt": False, "reasons": [], "thresholds": th}
    if best is None:
        result["reasons"].append("조건을 만족하는 후보가 없습니다")
        return result
    cand_test = evaluate_rule(histories, best, "test")
    result["candidate_test"] = cand_test
    reasons, ok = [], True
    if cand_test["avg_diff"] is None or cur_test["avg_diff"] is None:
        return {**result, "reasons": ["검증 구간 신호가 없어 판단할 수 없습니다"]}
    if cand_test["signals"] < th["min_signals"]:
        ok = False
        reasons.append(f"검증 구간 신호 {cand_test['signals']}건 < {th['min_signals']}건")
    if cand_test.get("weeks", 0) < th["min_weeks"]:
        ok = False
        reasons.append(f"신호가 난 서로 다른 주 {cand_test.get('weeks', 0)}주 < {th['min_weeks']}주 (몇 번의 순간에 몰림)")
    gain_share = cand_test["plus_share"] - cur_test["plus_share"]
    gain_diff = cand_test["avg_diff"] - cur_test["avg_diff"]
    if gain_share < th["plus_share_gain"]:
        ok = False
        reasons.append(f"평소보다 나은 종목 비율 개선 {gain_share:+.1f}%p < +{th['plus_share_gain']:.0f}%p")
    if gain_diff < th["diff_gain"]:
        ok = False
        reasons.append(f"평균 적중률 차이 개선 {gain_diff:+.1f}%p < +{th['diff_gain']:.0f}%p")
    if (cand_test["median_mdd"] is not None and cur_test["median_mdd"] is not None
            and cand_test["median_mdd"] < cur_test["median_mdd"] - th["mdd_slack"]):
        ok = False
        reasons.append(f"최대 낙폭 악화 {cand_test['median_mdd']:.0f}% vs {cur_test['median_mdd']:.0f}%")
    if cand_test["plus_share"] < 50:
        ok = False
        reasons.append(f"후보도 평소보다 나은 종목이 절반 미만 ({cand_test['plus_share']:.0f}%)")
    result["adopt"] = ok
    result["reasons"] = reasons or [f"검증 구간에서 종목 비율 {gain_share:+.1f}%p, 평균 차이 {gain_diff:+.1f}%p 개선"]
    return result


# ---- 규칙 저장소 (Supabase 클라이언트를 받아 사용: 앱·자동 실행 공용) ----
def _rows(resp) -> list[dict]:
    return list(getattr(resp, "data", None) or [])


def load_active_rule(sb) -> tuple[int, dict]:
    """활성 규칙 (없으면 기본 규칙을 v1로 등록)."""
    rows = _rows(sb.table(RULE_TABLE).select("*").eq("status", "active").execute())
    if rows:
        row = sorted(rows, key=lambda r: r["version"])[-1]
        params = row["params"] if isinstance(row["params"], dict) else __import__("json").loads(row["params"])
        return int(row["version"]), normalize_rules(params)
    sb.table(RULE_TABLE).insert({"version": 1, "status": "active", "params": normalize_rules(ACTION_RULES),
                                 "reason": "처음 정한 기본 규칙"}).execute()
    return 1, normalize_rules(ACTION_RULES)


def pending_proposal(sb) -> dict | None:
    rows = _rows(sb.table(RULE_TABLE).select("*").eq("status", "proposed").execute())
    return sorted(rows, key=lambda r: r["id"])[-1] if rows else None


def rule_history(sb) -> list[dict]:
    return sorted(_rows(sb.table(RULE_TABLE).select("*").execute()), key=lambda r: r["id"])


def save_proposal(sb, result: dict) -> int:
    hist = rule_history(sb)
    version = max([int(r["version"]) for r in hist] or [1]) + 1
    ev = {k: result.get(k) for k in ("current_train", "current_test", "candidate_train", "candidate_test", "thresholds")}
    sb.table(RULE_TABLE).insert({"version": version, "status": "proposed", "params": normalize_rules(result["candidate"]),
                                 "reason": " · ".join(result["reasons"]), "evaluation": ev}).execute()
    return version


MAX_CHALLENGERS = 3


def decide_proposal(sb, proposal_id: int, decision, now_iso: str) -> None:
    """decision: True/'accept'(챔피언으로) · False/'reject'(거절) · 'challenge'(도전자로 경쟁) · 'retire'(경쟁 종료)."""
    decision = {True: "accept", False: "reject"}.get(decision, decision)
    old_versions = []
    if decision == "accept":
        for row in _rows(sb.table(RULE_TABLE).select("*").eq("status", "active").execute()):
            old_versions.append(int(row["version"]))
            sb.table(RULE_TABLE).update({"status": "retired", "decided_at": now_iso}).eq("id", row["id"]).execute()
    status = {"accept": "active", "reject": "rejected", "challenge": "challenger", "retire": "retired"}[decision]
    sb.table(RULE_TABLE).update({"status": status, "decided_at": now_iso}).eq("id", proposal_id).execute()
    if decision == "accept" and old_versions:
        # 경기장 챔피언이 옛 대표 규칙이었다면 새 대표 규칙이 자리를 이어받음
        try:
            new = _rows(sb.table(RULE_TABLE).select("version").eq("id", proposal_id).execute())
            cur = _rows(sb.table(CHAMPION_TABLE).select("*").eq("status", "champion").execute())
            if new and cur and cur[0]["player"] in {f"rule_v{v}" for v in old_versions}:
                crown_champion(sb, f"rule_v{new[0]['version']}",
                               f"대표 규칙 교체로 이어받음 (v{old_versions[0]} → v{new[0]['version']})", now_iso)
        except Exception:
            pass


def load_challengers(sb) -> list[tuple[int, dict]]:
    out = []
    for row in sorted(_rows(sb.table(RULE_TABLE).select("*").eq("status", "challenger").execute()),
                      key=lambda r: r["version"]):
        params = row["params"] if isinstance(row["params"], dict) else __import__("json").loads(row["params"])
        out.append((int(row["version"]), normalize_rules(params)))
    return out


# ---- 챔피언 vs 도전자: 실전 신호 기록·채점 ----
RULE_SIGNAL_TABLE = "valhalla_rule_signals"
PROMOTE_MIN = {"graded": 20, "weeks": 8, "hit_gain": 10.0}


def live_rule_row(sig: pd.DataFrame, ticker: str, version: int) -> dict | None:
    """가장 최근 마감 주에 이 규칙의 신호가 났으면 기록할 행."""
    if sig is None or sig.empty or not sig["action"].iloc[-1]:
        return None
    return {"rule_version": int(version), "ticker": ticker, "week": sig.index[-1].strftime("%Y-%m-%d"),
            "action": sig["action"].iloc[-1], "close": round(float(sig["close"].iloc[-1]), 4)}


def rule_competition(live: pd.DataFrame, versions: dict[int, str] | None = None) -> pd.DataFrame:
    """규칙 버전별 실전 성적. 매수 적중 = 4주 뒤 상승, 매도 적중 = 4주 뒤 하락."""
    if live is None or live.empty:
        return pd.DataFrame()
    rows = []
    for v, g in live.groupby("rule_version"):
        buy, sell = g[g["action"] == "매수"], g[g["action"] == "매도"]
        bg, sg = buy.dropna(subset=["ret_4w"]), sell.dropna(subset=["ret_4w"])
        rows.append({"버전": int(v), "역할": (versions or {}).get(int(v), ""),
                     "매수 신호": int(len(buy)), "매수 채점": int(len(bg)), "매수 신호 주 수": int(bg["week"].nunique()),
                     "매수 적중률(%)": round(float((bg["ret_4w"] > 0).mean() * 100), 1) if len(bg) else None,
                     "매수 뒤 4주 평균(%)": round(float(bg["ret_4w"].mean()), 2) if len(bg) else None,
                     "매도 신호": int(len(sell)), "매도 채점": int(len(sg)),
                     "매도 적중률(%)": round(float((sg["ret_4w"] < 0).mean() * 100), 1) if len(sg) else None,
                     "매도 뒤 4주 평균(%)": round(float(sg["ret_4w"].mean()), 2) if len(sg) else None})
    return pd.DataFrame(rows).sort_values("버전").reset_index(drop=True)


def promotion_verdicts(table: pd.DataFrame, champion: int, th: dict | None = None) -> dict[int, str]:
    """도전자별 판정: 표본 부족 / 승격 추천 / 열세 / 접전."""
    th = {**PROMOTE_MIN, **(th or {})}
    out = {}
    if table is None or table.empty:
        return out
    t = table.set_index("버전")
    champ_hit = t.loc[champion, "매수 적중률(%)"] if champion in t.index else None
    for v, r in t.iterrows():
        if v == champion:
            continue
        if r["매수 채점"] < th["graded"] or r["매수 신호 주 수"] < th["weeks"]:
            out[v] = f"표본 부족 (매수 채점 {r['매수 채점']}건 · {r['매수 신호 주 수']}주 / 필요 {th['graded']}건 · {th['weeks']}주)"
        elif champ_hit is None or pd.isna(champ_hit):
            out[v] = "챔피언 채점 기록이 없어 비교 대기"
        elif r["매수 적중률(%)"] >= champ_hit + th["hit_gain"]:
            out[v] = f"승격 추천 (매수 적중률 {r['매수 적중률(%)']:.0f}% vs 챔피언 {champ_hit:.0f}%)"
        elif r["매수 적중률(%)"] <= champ_hit - th["hit_gain"]:
            out[v] = f"열세 (매수 적중률 {r['매수 적중률(%)']:.0f}% vs 챔피언 {champ_hit:.0f}%) · 경쟁 종료 고려"
        else:
            out[v] = f"접전 (매수 적중률 {r['매수 적중률(%)']:.0f}% vs 챔피언 {champ_hit:.0f}%)"
    return out


# ==========================================================================
# 15. 선수 경기장: 모든 선수가 매주 종목별 '비중(0~1)'을 내고, 다음 주 수익률로 똑같이 채점
#   선수: 규칙(챔피언·도전자) · 추세 필터 · 변동성 조절 · 그냥 보유 · AI 위원회(Claude) · 앙상블
# ==========================================================================
POSITION_TABLE = "valhalla_positions"
MINUTES_TABLE = "valhalla_council_minutes"
COUNCIL_TICKERS = ["SOXL", "USD", "SOXX", "NVDA", "AVGO", "MRVL", "MU", "TQQQ", "QQQ", "SPY"]
BASE_PLAYERS = {"trend200": "추세 필터", "voltarget": "변동성 조절", "hold": "그냥 보유"}
LIVE_ONLY = {"ai_council": "AI 위원회", "ensemble": "앙상블"}
REBOUND_PLAYERS = {"rebound_base": "과거 확률 (기준)"}
ENSEMBLE_RULES = {"min_weeks": 20, "lookback": 26, "cap": 0.5, "shrink": 0.5}


def player_label(pid: str) -> str:
    if pid.startswith("rule_v"):
        return f"규칙 {pid[5:]}"
    return {**BASE_PLAYERS, **TECHNIQUE_PLAYERS, **LIVE_ONLY, **REBOUND_PLAYERS}.get(pid, pid)


def rule_positions(sig: pd.DataFrame) -> pd.Series:
    """규칙 신호 → 주별 비중 (매수 이후 1, 매도 이후 0, 첫 신호 전에는 보유 1)."""
    return sig["state"].map({"매수": 1.0, "매도": 0.0}).fillna(1.0)


def trend_positions(index: pd.DatetimeIndex, ref_daily: pd.DataFrame | None, ma: int = 200) -> pd.Series:
    """기준 지수 금요일 종가가 200일선 위면 1, 아래면 0 (기준 지수가 없으면 NaN)."""
    if ref_daily is None or ref_daily.empty:
        return pd.Series(np.nan, index=index)
    c = ref_daily["Close"]
    above = (c > c.rolling(ma).mean()).where(c.rolling(ma).mean().notna())
    return above.resample("W-FRI").last().reindex(index).astype(float)


def vol_positions(index: pd.DatetimeIndex, daily: pd.DataFrame, window: int = 20, base: int = 252) -> pd.Series:
    """변동성 조절: 비중 = 평소(1년 중앙값) 변동성 ÷ 최근 20일 변동성 (0~1). 평소보다 출렁이면 그만큼 줄임."""
    r = daily["Close"].pct_change()
    vol = r.rolling(window).std()
    normal = vol.rolling(base, min_periods=base // 2).median()
    pos = (normal / vol).clip(0, 1).where(normal.notna() & (vol > 0))
    return pos.resample("W-FRI").last().reindex(index)


# ---- 매매기법 연구소 선수들 (SOXL 매매기법 연구소 백테스트 엔진을 그대로 옮김) ----
#  일봉으로 계좌를 굴리고, 매주 금요일 종가 기준 '투자 비중(주식 평가액 ÷ 계좌 총액)'을 비중으로 냄.
#  분할매수 기법은 레버리지 ETF용이라 3배·2배 ETF에만 적용.
TECHNIQUE_PLAYERS = {"inf_mine": "① 내 방식 (40분할)", "inf_v2": "② V2형 (별지점)",
                     "inf_v2_safe": "④ V2+손절+200일선", "rebal50": "⑥ 50:50 리밸런싱",
                     "charles": "찰스 SOXL 시스템"}
TECHNIQUE_CATEGORIES = ("3배 ETF", "2배 ETF")
TECHNIQUE_START = "2016-01-01"
_FEE = 0.001


class _Acct:
    def __init__(self, cash):
        self.cash, self.sh, self.cost = cash, 0.0, 0.0

    @property
    def avg(self):
        return self.cost / self.sh if self.sh > 0 else 0.0

    def buy(self, px, usd):
        usd = min(usd, self.cash)
        if usd <= 1:
            return 0.0
        q = usd / (px * (1 + _FEE))
        self.cash -= q * px * (1 + _FEE); self.sh += q; self.cost += q * px * (1 + _FEE)
        return q

    def sell(self, px, q):
        q = min(q, self.sh)
        if q <= 1e-9:
            return 0.0
        c = self.avg * q
        self.cash += q * px * (1 - _FEE); self.sh -= q; self.cost -= c
        if self.sh < 1e-9:
            self.sh, self.cost = 0.0, 0.0
        return q

    def exposure(self, px):
        v = self.cash + self.sh * px
        return self.sh * px / v if v > 0 else 0.0


def _ohlc(daily: pd.DataFrame) -> pd.DataFrame:
    c = daily["Close"].astype(float)
    df = pd.DataFrame({"close": c,
                       "open": daily["Open"].astype(float) if "Open" in daily else c,
                       "high": daily["High"].astype(float) if "High" in daily else c}).dropna(subset=["close"])
    df["open"] = df["open"].fillna(df["close"]); df["high"] = df["high"].fillna(df["close"])
    df["ma200"] = df["close"].rolling(200).mean()
    return df


def infinite_exposure(daily: pd.DataFrame, splits: int = 40, target: float = 0.10, star: bool = False,
                      quarter_cut: bool = False, trend_filter: bool = False, start: str = TECHNIQUE_START) -> pd.Series:
    """무한매수법 계열 (연구소 엔진과 같은 규칙). 반환: 날짜별 투자 비중(0~1)."""
    df = _ohlc(daily)
    df = df[df.index >= pd.Timestamp(start)]
    a, T, unit, out = _Acct(10_000.0), 0.0, None, []
    for date, r in df.iterrows():
        px = r.close
        if a.sh == 0:
            T = 0
            if trend_filter and (np.isnan(r.ma200) or px < r.ma200):
                out.append(0.0); continue
            unit = a.cash / splits
        if a.sh > 0:
            lim = a.avg * (1 + target)
            fill = r.open if r.open >= lim else (lim if r.high >= lim else None)
            if fill:
                a.sell(fill, a.sh); T = 0
                out.append(a.exposure(px)); continue
            if star:
                sp = a.avg * (1 + target - T / 2 / 100 * (40 / splits))
                if px >= sp and T > 0:
                    a.sell(px, a.sh / 4); T *= 0.75
        if T < splits - 0.5 and a.cash > 1:
            if a.sh == 0:
                a.buy(px, unit); T += 1
            elif star:
                sp = a.avg * (1 + target - T / 2 / 100 * (40 / splits))
                n = 0.0
                if px <= a.avg:
                    a.buy(px, unit / 2); n += .5
                if px <= sp:
                    a.buy(px, unit / 2); n += .5
                T += n
            elif px <= a.avg * 1.05:
                a.buy(px, unit); T += 1
        elif a.sh > 0 and quarter_cut:
            a.sell(px, a.sh / 4); T *= 0.75
            unit = a.cash / (splits - T) if splits - T > 0 else unit
        out.append(a.exposure(px))
    return pd.Series(out, index=df.index, dtype=float)


def rebalance_exposure(daily: pd.DataFrame, w: float = 0.5, band: float = 0.10, every: int = 10,
                       start: str = TECHNIQUE_START) -> pd.Series:
    """고정 비중 리밸런싱: 2주마다 비중이 ±10% 넘게 벗어나면 50%로 맞춤."""
    df = _ohlc(daily)
    df = df[df.index >= pd.Timestamp(start)]
    a, out = _Acct(10_000.0), []
    for i, (date, r) in enumerate(df.iterrows()):
        v = a.cash + a.sh * r.close
        if i == 0:
            a.buy(r.close, v * w)
        elif i % every == 0:
            cur = a.sh * r.close / v
            if abs(cur - w) > band * w:
                diff = v * w - a.sh * r.close
                if diff > 0:
                    a.buy(r.close, diff)
                else:
                    a.sell(r.close, -diff / r.close)
        out.append(a.exposure(r.close))
    return pd.Series(out, index=df.index, dtype=float)


def _wilder_rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


CHARLES_RULES = {"ma": 200, "band": 2.0, "vol_out": 45.0, "vol_in": 30.0, "hot_trim": 25.0, "hot_back": 12.0,
                 "cap": 0.70, "fast": 20, "slope": 5, "st_band": 1.0, "st_pct": 0.25, "rsi_len": 14,
                 "rsi_low": 25.0, "rsi_back": 32.0, "rsi_exit": 55.0, "rb_max": 10, "rb_stop": 15.0,
                 "rb_pct": 0.15, "vol_len": 20}


def charles_exposure(daily: pd.DataFrame, sig_daily: pd.DataFrame | None, rules: dict | None = None) -> pd.Series:
    """찰스 SOXL 시스템 v5 (TradingView 공개 스크립트) 이식. 반환: 날짜별 보유 비중(신호 다음 날 종가 체결).
    큰 규칙: 신호 지수가 200일선 +2% 위·변동성 낮으면 70% 보유, 과열(+25%)이면 35%, 깨지면 0.
    큰 규칙이 쉴 때만 단기 1(20일선 위·상승 → 25%)과 단기 2(RSI 25 아래→32 회복 → 15%, 최대 10일)."""
    th = {**CHARLES_RULES, **(rules or {})}
    close = daily["Close"].astype(float).dropna()
    sig = (sig_daily["Close"].astype(float) if sig_daily is not None and not sig_daily.empty else close)
    sig = sig.reindex(close.index.union(sig.index)).ffill().reindex(close.index)
    s_ma = sig.rolling(th["ma"]).mean()
    dist = (sig / s_ma - 1) * 100
    s_vol = np.log(sig / sig.shift(1)).rolling(th["vol_len"]).std(ddof=0) * np.sqrt(252) * 100
    s_fast = sig.rolling(th["fast"]).mean()
    s_fast_prev = s_fast.shift(th["slope"])
    f_dist = (sig / s_fast - 1) * 100
    rsi = _wilder_rsi(close, th["rsi_len"])
    cross_up = (rsi > th["rsi_back"]) & (rsi.shift(1) <= th["rsi_back"])
    rsi_low5 = rsi.rolling(5).min().shift(1)
    trend_on = vol_off = hot = st_on = rb_on = False
    rb_bars, rb_entry, raw = 0, np.nan, []
    for i in range(len(close)):
        d, v, fd, px = dist.iloc[i], s_vol.iloc[i], f_dist.iloc[i], close.iloc[i]
        if pd.notna(d):
            if d > th["band"]:
                trend_on = True
            if d < -th["band"]:
                trend_on = False
            if d > th["hot_trim"]:
                hot = True
            if d < th["hot_back"]:
                hot = False
        if pd.notna(v):
            if v > th["vol_out"]:
                vol_off = True
            if v < th["vol_in"]:
                vol_off = False
        main = 0.0 if (not trend_on or vol_off) else (0.5 if hot else 1.0)
        main *= th["cap"]
        if pd.notna(fd) and pd.notna(s_fast_prev.iloc[i]):
            if fd > th["st_band"] and s_fast.iloc[i] > s_fast_prev.iloc[i]:
                st_on = True
            if fd < -th["st_band"]:
                st_on = False
        else:
            st_on = False
        trig = bool(cross_up.iloc[i]) and pd.notna(rsi_low5.iloc[i]) and rsi_low5.iloc[i] < th["rsi_low"]
        if not rb_on and trig:
            rb_on, rb_bars, rb_entry = True, 0, px
        elif rb_on:
            rb_bars += 1
            if rsi.iloc[i] > th["rsi_exit"] or rb_bars >= th["rb_max"] or px <= rb_entry * (1 - th["rb_stop"] / 100):
                rb_on = False
        sub = ((th["st_pct"] if st_on else 0.0) + (th["rb_pct"] if rb_on else 0.0)) if main == 0 else 0.0
        raw.append(min(1.0, main + sub))
    return pd.Series(raw, index=close.index).shift(1).fillna(0.0)   # 신호 다음 날 종가 체결


def technique_positions(index: pd.DatetimeIndex, daily: pd.DataFrame,
                        ref_daily: pd.DataFrame | None = None) -> pd.DataFrame:
    """연구소 기법 4개 + 찰스 시스템의 주별 비중 (금요일 종가 시점의 투자 비중)."""
    series = {"inf_mine": infinite_exposure(daily),
              "inf_v2": infinite_exposure(daily, star=True),
              "inf_v2_safe": infinite_exposure(daily, star=True, quarter_cut=True, trend_filter=True),
              "rebal50": rebalance_exposure(daily),
              "charles": charles_exposure(daily, ref_daily)}
    return pd.DataFrame({k: v.resample("W-FRI").last().reindex(index).round(4) for k, v in series.items()},
                        index=index)


def player_positions(hist: pd.DataFrame, daily: pd.DataFrame, ref_daily: pd.DataFrame | None,
                     rules: list[tuple[int, dict]], ticker: str | None = None) -> pd.DataFrame:
    """주별 × 선수 비중표 (계산 가능한 선수만: 규칙들·추세·변동성·보유 + 레버리지 ETF면 연구소 기법)."""
    out = pd.DataFrame(index=hist.index)
    for version, rule in rules:
        out[f"rule_v{version}"] = rule_positions(action_signals(hist, rule))
    out["trend200"] = trend_positions(hist.index, ref_daily)
    out["voltarget"] = vol_positions(hist.index, daily)
    out["hold"] = 1.0
    if ticker is not None and category_of(ticker) in TECHNIQUE_CATEGORIES:
        out = out.join(technique_positions(hist.index, daily, ref_daily))
    return out


def position_moves(pos: pd.Series, step: float = 0.25) -> pd.DataFrame:
    """비중 변화 → 차트 표시용 매수(늘림)·매도(줄임) 시점.
    잘게 흔들리는 선수(변동성 조절 등)가 매주 표시되지 않도록, 마지막 표시 때보다 step 이상 바뀌었을 때만 표시."""
    p = pos.dropna().astype(float)
    rows, ref = [], None
    for t, v in p.items():
        if ref is None:
            ref = v
            continue
        if abs(v - ref) >= step - 1e-9:
            rows.append({"week": t, "action": "매수" if v > ref else "매도", "from": ref, "to": v})
            ref = v
    return pd.DataFrame(rows, columns=["week", "action", "from", "to"]).set_index("week")


def trend_reference(ticker: str, daily: pd.DataFrame, partner_daily: pd.DataFrame | None) -> pd.DataFrame | None:
    """추세 필터 기준: 1배 ETF는 자기 자신, 레버리지 ETF·개별 종목은 같은 섹터 1배 ETF."""
    return daily if category_of(ticker) in ("1배 ETF", "기타") else partner_daily


def backtest_players(positions: pd.DataFrame, close: pd.Series, test_weeks: int = TEST_WEEKS,
                     fee_bps: float = 10) -> pd.DataFrame:
    """선수별 과거 성적: 이번 주 비중 × 다음 주 수익률 (거래 비용 반영). 전체 기간과 최근 2년."""
    ret = close.pct_change().shift(-1)            # 이번 주 금요일 → 다음 주 금요일
    rows = []
    cut = close.index[-1] - pd.Timedelta(weeks=test_weeks)
    for pid in positions.columns:
        pos = positions[pid].astype(float)
        valid = pos.notna() & ret.notna()
        if valid.sum() < 10:
            continue
        p, r = pos[valid], ret[valid]
        cost = p.diff().abs().fillna(p.iloc[0]) * fee_bps / 10_000
        wr = p * r - cost

        def stats(x: pd.Series) -> tuple[float, float]:
            eq = (1 + x).cumprod()
            return float((eq.iloc[-1] - 1) * 100), float(((eq / eq.cummax()) - 1).min() * 100)

        full_ret, full_mdd = stats(wr)
        recent = wr[wr.index > cut]
        rec_ret, rec_mdd = stats(recent) if len(recent) > 4 else (float("nan"), float("nan"))
        rows.append({"선수": player_label(pid), "id": pid, "평균 비중(%)": round(float(p.mean() * 100), 0),
                     "전체 누적(%)": round(full_ret, 1), "전체 MDD(%)": round(full_mdd, 1),
                     "최근 2년 누적(%)": round(rec_ret, 1), "최근 2년 MDD(%)": round(rec_mdd, 1),
                     "주 수": int(valid.sum())})
    return pd.DataFrame(rows)


def grade_positions(rows: pd.DataFrame, weekly_close: pd.Series) -> pd.DataFrame:
    """비중 기록에 다음 주 수익률(ret_1w, %)을 채움."""
    if rows is None or rows.empty:
        return rows
    out = rows.copy()
    wc = weekly_close.dropna()
    if "ret_1w" not in out:
        out["ret_1w"] = np.nan
    for i, row in out.iterrows():
        if pd.notna(row.get("ret_1w")):
            continue
        week = pd.Timestamp(row["week"])
        if week in wc.index:
            pos = wc.index.get_loc(week)
            if pos + 1 < len(wc):
                out.at[i, "ret_1w"] = round(float((wc.iloc[pos + 1] / wc.iloc[pos] - 1) * 100), 3)
    return out


def ensemble_weights(graded: pd.DataFrame, asof: pd.Timestamp, rules: dict | None = None) -> dict[str, float]:
    """앙상블 가중치: 이번 달 1일 이전 채점 기록만 사용(한 달에 한 번 바뀜).
    최근 26주 '보유 대비 초과수익'이 클수록 무겁게. 20주 미만이면 균등, 절반은 균등과 섞고, 한 선수 최대 50%."""
    th = {**ENSEMBLE_RULES, **(rules or {})}
    members = [p for p in (graded["player"].unique() if graded is not None and not graded.empty else [])
               if p not in ("ensemble", "hold")]
    if not members:
        return {}
    equal = {p: 1 / len(members) for p in members}
    month_start = pd.Timestamp(asof).to_period("M").start_time
    d = graded[(pd.to_datetime(graded["week"]) < month_start) & graded["ret_1w"].notna()]
    if d.empty:
        return equal
    hold = d[d["player"] == "hold"].set_index(["ticker", "week"])["ret_1w"]
    scores, enough = {}, True
    for p in members:
        g = d[d["player"] == p]
        weeks = sorted(g["week"].unique())[-th["lookback"]:]
        g = g[g["week"].isin(weeks)]
        if g["week"].nunique() < th["min_weeks"]:
            enough = False
        hr = hold.reindex(pd.MultiIndex.from_frame(g[["ticker", "week"]])).to_numpy()
        excess = g["position"].astype(float).to_numpy() * g["ret_1w"].to_numpy() - np.nan_to_num(hr, nan=0.0)
        scores[p] = float(np.nanmean(excess)) if len(excess) else 0.0
    if not enough:
        return equal
    raw = {p: max(s, 0.0) + 1e-6 for p, s in scores.items()}
    tot = sum(raw.values())
    w = {p: th["shrink"] * equal[p] + (1 - th["shrink"]) * raw[p] / tot for p in members}
    for _ in range(10):                                  # 상한 50% 적용 후 재분배
        over = {p: v for p, v in w.items() if v > th["cap"]}
        if not over:
            break
        extra = sum(v - th["cap"] for v in over.values())
        rest = [p for p in w if p not in over]
        for p in over:
            w[p] = th["cap"]
        for p in rest:
            w[p] += extra / len(rest)
    s = sum(w.values())
    return {p: round(v / s, 4) for p, v in w.items()}


def ensemble_position(member_positions: dict[str, float], weights: dict[str, float]) -> float | None:
    usable = {p: v for p, v in member_positions.items() if p in weights and v is not None and pd.notna(v)}
    if not usable:
        return None
    tot = sum(weights[p] for p in usable)
    return round(sum(weights[p] * v for p, v in usable.items()) / tot, 4) if tot > 0 else None


def arena_table(graded: pd.DataFrame, tickers: list[str] | None = None) -> pd.DataFrame:
    """실전 경기표: 선수별 누적 수익(종목 평균), 보유 대비, 평균 비중, 들어갔을 때 오른 비율, 빠졌을 때 피한 비율."""
    if graded is None or graded.empty:
        return pd.DataFrame()
    d = graded.dropna(subset=["ret_1w"]).copy()
    if tickers:
        d = d[d["ticker"].isin(tickers)]
    if d.empty:
        return pd.DataFrame()
    d["position"] = d["position"].astype(float)
    d["pr"] = d["position"] * d["ret_1w"] / 100
    hold = d[d["player"] == "hold"].set_index(["ticker", "week"])["ret_1w"] / 100
    rows = []
    for p, g in d.groupby("player"):
        per_ticker = g.sort_values("week").groupby("ticker")["pr"].apply(lambda x: float((1 + x).prod() - 1))
        h = hold.reindex(pd.MultiIndex.from_frame(g[["ticker", "week"]]))
        hold_ticker = (pd.Series(h.to_numpy(), index=g.index).groupby(g["ticker"])
                         .apply(lambda x: float((1 + x.fillna(0)).prod() - 1)))
        inn, out = g[g["position"] >= 0.5], g[g["position"] < 0.5]
        rows.append({"선수": player_label(p), "id": p, "채점 주 수": int(g["week"].nunique()), "종목 수": int(g["ticker"].nunique()),
                     "누적(%)": round(float(per_ticker.mean() * 100), 2),
                     "보유 대비(%p)": round(float((per_ticker - hold_ticker.reindex(per_ticker.index)).mean() * 100), 2),
                     "평균 비중(%)": round(float(g["position"].mean() * 100), 0),
                     "들어갔을 때 오른 비율(%)": round(float((inn["ret_1w"] > 0).mean() * 100), 1) if len(inn) else None,
                     "빠졌을 때 피한 비율(%)": round(float((out["ret_1w"] < 0).mean() * 100), 1) if len(out) else None})
    return pd.DataFrame(rows).sort_values("누적(%)", ascending=False).reset_index(drop=True)


# ---- 경기장 챔피언: 규칙뿐 아니라 모든 선수가 챔피언이 될 수 있음 ----
CHAMPION_TABLE = "valhalla_arena_champion"
ARENA_PROMOTE = {"weeks": 20, "gain": 3.0, "mdd_slack": 5.0}


def _curve_stats(rows: pd.DataFrame) -> tuple[float, float]:
    """종목별로 (비중×수익) 복리 누적·최대 낙폭을 구해 종목 평균 (%)."""
    rets, mdds = [], []
    for _, g in rows.sort_values("week").groupby("ticker"):
        eq = (1 + g["pr"]).cumprod()
        rets.append(float(eq.iloc[-1] - 1))
        mdds.append(float(((eq / eq.cummax()) - 1).min()))
    return (float(np.mean(rets) * 100), float(np.mean(mdds) * 100)) if rets else (float("nan"), float("nan"))


def arena_verdicts(graded: pd.DataFrame, champion: str, tickers: list[str] | None = None,
                   th: dict | None = None) -> dict[str, tuple[str, str]]:
    """챔피언과 '같은 종목·같은 주'만 맞대어 비교. 반환: 선수 → (코드, 설명).
    코드: short(표본 부족) · promote(승격 추천) · behind(열세) · close(접전) · wait(챔피언 기록 없음)."""
    th = {**ARENA_PROMOTE, **(th or {})}
    out: dict[str, tuple[str, str]] = {}
    if graded is None or graded.empty:
        return out
    d = graded.dropna(subset=["ret_1w"]).copy()
    if tickers:
        d = d[d["ticker"].isin(tickers)]
    if d.empty:
        return out
    d["pr"] = d["position"].astype(float) * d["ret_1w"].astype(float) / 100
    champ = d[d["player"] == champion][["ticker", "week", "pr"]]
    for p, g in d.groupby("player"):
        if p == champion:
            continue
        both = g[["ticker", "week", "pr"]].merge(champ, on=["ticker", "week"], suffixes=("", "_c"))
        weeks = int(both["week"].nunique())
        if champ.empty:
            out[p] = ("wait", "챔피언 채점 기록이 아직 없어 비교 대기")
            continue
        if weeks < th["weeks"]:
            out[p] = ("short", f"표본 부족 (챔피언과 맞대결 {weeks}주 / 필요 {th['weeks']}주)")
            continue
        r_p, m_p = _curve_stats(both[["ticker", "week", "pr"]])
        r_c, m_c = _curve_stats(both[["ticker", "week", "pr_c"]].rename(columns={"pr_c": "pr"}))
        gap = r_p - r_c
        detail = f"누적 {r_p:+.1f}% vs 챔피언 {r_c:+.1f}% · 최대 낙폭 {m_p:.1f}% vs {m_c:.1f}% ({weeks}주)"
        if gap >= th["gain"] and m_p >= m_c - th["mdd_slack"]:
            out[p] = ("promote", "승격 추천 · " + detail)
        elif gap <= -th["gain"]:
            out[p] = ("behind", "열세 · " + detail)
        else:
            out[p] = ("close", "접전 · " + detail)
    return out


def load_champion(sb, default: str | None = None) -> dict:
    """현재 경기장 챔피언. 기록이 없으면 활성 규칙을 챔피언으로 등록."""
    rows = _rows(sb.table(CHAMPION_TABLE).select("*").eq("status", "champion").order("id", desc=True).limit(1).execute())
    if rows:
        return rows[0]
    if default is None:
        version, _ = load_active_rule(sb)
        default = f"rule_v{version}"
    row = {"player": default, "status": "champion", "reason": "첫 챔피언 (기존 활성 규칙)"}
    sb.table(CHAMPION_TABLE).insert(row).execute()
    return row


def crown_champion(sb, player: str, reason: str, now_iso: str) -> None:
    """새 챔피언 등극. 이전 챔피언은 'former'로 남김(이력 보존)."""
    sb.table(CHAMPION_TABLE).update({"status": "former", "ended_at": now_iso}).eq("status", "champion").execute()
    sb.table(CHAMPION_TABLE).insert({"player": player, "status": "champion", "reason": reason}).execute()


def champion_history(sb) -> list[dict]:
    return _rows(sb.table(CHAMPION_TABLE).select("*").order("id", desc=True).limit(30).execute())


# ==========================================================================
# 16. 본전 거리: 레버리지 ETF 평단 회복에 필요한 기초 지수 수준
#   즉시 가정: 레버리지 L배면 가격 비율 = 지수 비율^L (출렁임 없이 바로 오를 때, 가장 낙관적)
#   시간 가정: 매년 (L(L-1)/2)·σ² (변동성 손실) + 비용(차입·운용보수)만큼 더 깎인다고 보고 필요 지수를 올림
# ==========================================================================
LEV_COST = 0.0875          # 연 비용 가정: 차입 약 2×금리(8%) + 운용보수(0.75%)
BREAKEVEN_HORIZONS = (0.0, 0.5, 1.0, 2.0)


def leverage_drag(vol: float, lev: int = 3, cost: float = LEV_COST) -> float:
    """연간 끌림(로그 기준): 변동성 손실 + 비용."""
    return lev * (lev - 1) / 2 * vol ** 2 + cost


def needed_index(index_now: float, price_now: float, target: float, lev: int = 3,
                 years: float = 0.0, vol: float = 0.0, cost: float = LEV_COST) -> float:
    ratio = target / price_now * np.exp(leverage_drag(vol, lev, cost) * years if years else 0.0)
    return float(index_now * ratio ** (1 / lev))


def breakeven_table(index_now: float, price_now: float, targets: dict[str, float], lev: int, vol: float,
                    horizons=BREAKEVEN_HORIZONS) -> pd.DataFrame:
    rows = []
    for name, tgt in targets.items():
        row = {"목표": name, "목표가": tgt}
        for h in horizons:
            need = needed_index(index_now, price_now, tgt, lev, h, vol)
            label = "지금 즉시" if h == 0 else (f"{int(h * 12)}개월 뒤" if h < 1 else f"{int(h)}년 뒤")
            row[label] = round(need, 0)
            row[f"{label} 상승률(%)"] = round((need / index_now - 1) * 100, 1)
        rows.append(row)
    return pd.DataFrame(rows)


def needed_gain_history(index_close: pd.Series, price_close: pd.Series, target: float, lev: int = 3) -> pd.Series:
    """날짜별 '지금 즉시' 기준 본전까지 필요한 지수 상승률(%). 줄어들면 본전이 가까워진 것."""
    both = pd.concat([index_close.rename("i"), price_close.rename("p")], axis=1).dropna()
    return ((target / both["p"]) ** (1 / lev) - 1) * 100


def realized_vol(close: pd.Series, days: int = 252) -> float:
    r = np.log(close / close.shift(1)).dropna().tail(days)
    return float(r.std() * np.sqrt(252)) if len(r) > 20 else float("nan")


# ==========================================================================
# 17. 반등 판별전: 큰 반등이 '추세 전환'인지 '일시 반등'인지 가리는 경기
#   사건: 반도체 지수(SOX)가 고점 대비 15% 이상 빠진 상태에서 하루 +5% 이상 또는 20일 저점 대비 +15% 반등
#   판정 시점: 사건이 있는 주의 금요일 종가 (선수들의 그 주 SOXL 비중 = '추세 전환' 확률)
#   정답: 13주 안에 직전 저점을 깨지 않고 13주 뒤 종가가 판정 시점보다 높으면 추세 전환, 아니면 일시 반등
#        (저점을 깨는 순간 바로 '일시 반등'으로 확정)
# ==========================================================================
REBOUND_TABLE = "valhalla_rebound_events"
REBOUND_RULES = {"day_jump": 5.0, "from_low": 15.0, "low_days": 20, "dd_min": 15.0,
                 "horizon_weeks": 13, "cooldown_weeks": 4}
REBOUND_LIVE_START = "2026-11-16"
REBOUND_BASE_PLAYER = "rebound_base"


def _friday(d: pd.Timestamp) -> pd.Timestamp:
    return d.to_period("W-FRI").end_time.normalize()


def detect_rebounds(close: pd.Series, rules: dict | None = None) -> pd.DataFrame:
    th = {**REBOUND_RULES, **(rules or {})}
    c = close.dropna().astype(float)
    ret = c.pct_change() * 100
    low = c.rolling(th["low_days"], min_periods=5).min()
    from_low = (c / low - 1) * 100
    high = c.rolling(252, min_periods=60).max()
    depth = (low / high - 1) * 100
    day_hit = ret >= th["day_jump"]
    low_hit = (from_low >= th["from_low"]) & (from_low.shift(1) < th["from_low"])
    cond = (day_hit | low_hit) & (depth <= -th["dd_min"])
    rows, last_week, last_day, last_low = [], None, None, None
    for d in c.index[cond.fillna(False).to_numpy()]:
        wk = _friday(d)
        if last_week is not None and wk == last_week:
            continue
        if last_week is not None and (wk - last_week).days < 7 * th["cooldown_weeks"]:
            # 쉬는 기간이라도 직전 사건이 이미 저점을 깨서 '일시 반등'으로 끝났다면 새 사건으로 봄
            between = c[(c.index > last_day) & (c.index < d)]
            if between.empty or between.min() >= last_low:
                continue
        last_week, last_day, last_low = wk, d, float(low[d])
        trig = f"하루 {ret[d]:+.1f}%" if day_hit[d] else f"20일 저점 대비 {from_low[d]:+.1f}%"
        rows.append({"week": wk, "event_date": d, "trigger": trig, "jump": round(float(ret[d]), 2),
                     "prior_low": float(low[d]), "depth": round(float(depth[d]), 1)})
    return pd.DataFrame(rows, columns=["week", "event_date", "trigger", "jump", "prior_low", "depth"])


def label_rebounds(events: pd.DataFrame, close: pd.Series, horizon_weeks: int | None = None) -> pd.DataFrame:
    """각 사건의 정답. outcome: 1=추세 전환, 0=일시 반등, NaN=판정 대기."""
    h = horizon_weeks or REBOUND_RULES["horizon_weeks"]
    c = close.dropna().astype(float)
    out = events.copy()
    for col in ("judge_close", "outcome", "resolved_on", "end_close"):
        out[col] = np.nan if col != "resolved_on" else None
    for i, r in out.iterrows():
        upto = c[c.index <= r["week"]]
        if upto.empty:
            continue
        judge_day, judge = upto.index[-1], float(upto.iloc[-1])
        out.at[i, "judge_close"] = judge
        end = judge_day + pd.Timedelta(weeks=h)
        after = c[(c.index > judge_day) & (c.index <= end)]
        broke = after[after < r["prior_low"]]
        if not broke.empty:
            out.at[i, "outcome"], out.at[i, "resolved_on"] = 0.0, broke.index[0]
        elif c.index[-1] >= end and not after.empty:
            out.at[i, "end_close"] = float(after.iloc[-1])
            out.at[i, "outcome"] = 1.0 if after.iloc[-1] > judge else 0.0
            out.at[i, "resolved_on"] = after.index[-1]
    return out


def rebound_base_rates(labeled: pd.DataFrame, prior: float = 2.0) -> pd.Series:
    """기준 선수: 그 사건 이전에 판정이 끝난 과거 사건들의 추세 전환 비율(0.5 쪽으로 살짝 당김)."""
    rates = []
    for _, r in labeled.iterrows():
        done = labeled[labeled["resolved_on"].notna() & (pd.to_datetime(labeled["resolved_on"]) <= r["week"])]
        n, k = len(done), float(done["outcome"].sum()) if len(done) else 0.0
        rates.append(round((k + prior) / (n + 2 * prior), 3))
    return pd.Series(rates, index=labeled.index)


def rebound_scoreboard(labeled: pd.DataFrame, calls: pd.DataFrame) -> pd.DataFrame:
    """calls: week × 선수 확률표(0~1). 판정 끝난 사건만 채점. 브라이어 낮을수록, 적중률 높을수록 좋음."""
    done = labeled.dropna(subset=["outcome"]).set_index("week")
    rows = []
    for pid in calls.columns:
        p = calls[pid].reindex(done.index)
        ok = p.notna()
        if not ok.any():
            continue
        y, q = done.loc[ok, "outcome"].astype(float), p[ok].astype(float)
        rows.append({"id": pid, "사건 수": int(ok.sum()), "브라이어": round(float(((q - y) ** 2).mean()), 3),
                     "적중률(%)": round(float(((q >= 0.5) == (y == 1)).mean() * 100), 1),
                     "전환 예상 평균(%)": round(float(q.mean() * 100), 0)})
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    base = t.loc[t["id"] == REBOUND_BASE_PLAYER, "브라이어"]
    t["기준 대비"] = (t["브라이어"] - (float(base.iloc[0]) if not base.empty else np.nan)).round(3)
    return t.sort_values("브라이어").reset_index(drop=True)


def sb_fetch_all(sb, table: str, eq: dict | None = None, page: int = 1000) -> list[dict]:
    """Supabase 1,000행 제한을 넘겨 끝까지 읽기."""
    rows, start = [], 0
    while True:
        q = sb.table(table).select("*")
        for k, v in (eq or {}).items():
            q = q.eq(k, v)
        batch = _rows(q.order("id").range(start, start + page - 1).execute())
        rows.extend(batch)
        if len(batch) < page:
            return rows
        start += page
