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
CORE_VERSION = "2026.11.28-2"

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


def expected_session(now_et) -> pd.Timestamp:
    """미국 동부 시각 기준, 일봉이 있어야 하는 마지막 거래일(주말 제외, 휴장일은 모름)."""
    day = pd.Timestamp(now_et.date())
    if now_et.hour < 16 or (now_et.hour == 16 and now_et.minute < 15):
        day -= pd.Timedelta(days=1)
    while day.weekday() >= 5:
        day -= pd.Timedelta(days=1)
    return day


def intraday_to_daily(intraday: pd.DataFrame | None) -> pd.DataFrame | None:
    """분·시간봉 → 날짜별 일봉 (정규장 봉만 들어온다고 가정)."""
    if intraday is None or intraday.empty:
        return None
    g = intraday.groupby(intraday.index.normalize())
    return pd.DataFrame({"Open": g["Open"].first(), "High": g["High"].max(), "Low": g["Low"].min(),
                         "Close": g["Close"].last(), "Volume": g["Volume"].sum()})


def fill_missing_days(daily: pd.DataFrame, patch: pd.DataFrame | None) -> pd.DataFrame:
    """일봉 끝에 빠진 날(야후가 마감 직후 종가를 비워 보내는 경우)을 분봉으로 만든 일봉으로 채움."""
    if patch is None or patch.empty or daily is None or daily.empty:
        return daily
    extra = patch[patch.index > daily.index[-1]]
    return pd.concat([daily, extra[daily.columns]]) if not extra.empty else daily


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


def bucket_labels(dates: pd.DatetimeIndex, ends: pd.DatetimeIndex) -> pd.Series:
    """각 날짜 → 그 날짜를 포함하는 묶음의 마지막 날(ends 중 같거나 뒤의 첫 날). 마지막 묶음 뒤 날짜는 NaT."""
    pos = ends.searchsorted(dates, side="left")
    lab = pd.Series(pd.NaT, index=dates, dtype="datetime64[ns]")
    ok = pos < len(ends)
    lab[ok] = ends[pos[ok]]
    return lab


def _group_last(series: pd.Series, ends: pd.DatetimeIndex | None, index: pd.DatetimeIndex) -> pd.Series:
    """주(금요일) 또는 사용자 묶음 단위의 마지막 값."""
    if ends is None:
        return series.resample("W-FRI").last().reindex(index)
    lab = bucket_labels(series.index, ends)
    return series.groupby(lab.to_numpy()).last().reindex(index)


def to_weekly(daily: pd.DataFrame, ends: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """일봉 OHLCV → 주봉 (금요일 기준 라벨). ends를 주면 '그 날짜로 끝나는 5거래일 묶음' 단위."""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    if ends is None:
        w = daily.resample("W-FRI").agg(agg)
    else:
        lab = bucket_labels(daily.index, ends)
        w = daily[lab.notna().to_numpy()].groupby(lab.dropna().to_numpy()).agg(agg)
    return w.dropna(subset=["Close"])


def macd_hist(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    macd = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    return macd - macd.ewm(span=signal, adjust=False).mean()


def chart_signal_table(daily: pd.DataFrame, partner_daily: pd.DataFrame | None = None,
                       ends: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """주별 차트 신호(True/False)와 점수. partner_daily 는 SOXX 같은 1배 지수."""
    w = to_weekly(daily, ends)
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
    out["above_ma20d"] = _group_last(above, ends, out.index)
    if partner_daily is not None and not partner_daily.empty:
        pc = to_weekly(partner_daily, ends)["Close"].reindex(out.index)
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


def macro_signal_table(macro_daily: dict[str, pd.DataFrame | None], index: pd.DatetimeIndex,
                       ends: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """주별 거시 신호. macro_daily = {ticker: 일봉 DataFrame}. 없는 지표는 NaN."""
    out = pd.DataFrame(index=index)

    def weekly_close(ticker):
        df = macro_daily.get(ticker)
        if df is None or df.empty:
            return None
        return _group_last(df["Close"], ends, index).ffill(limit=1)

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
                         macro_daily: dict[str, pd.DataFrame | None] | None,
                         ends: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    chart = chart_signal_table(daily, partner_daily, ends)
    macro = macro_signal_table(macro_daily or {}, chart.index, ends)
    return chart.join(macro)


def build_daily_signal_history(daily: pd.DataFrame, partner_daily: pd.DataFrame | None,
                               macro_daily: dict[str, pd.DataFrame | None] | None) -> pd.DataFrame:
    """일봉 버전: 매일 '오늘로 끝나는 최근 5거래일'을 한 주로 보고 같은 신호를 계산.
    5가지 시작점(위상)으로 주봉을 만들어 계산한 뒤 날짜순으로 합침 → 하루마다 한 줄."""
    idx = daily.index
    n = len(idx)
    parts = []
    for phase in range(5):
        ends = idx[[(n - 1 - i) % 5 == phase for i in range(n)]]
        if len(ends) < 40:
            continue
        parts.append(build_signal_history(daily, partner_daily, macro_daily, ends))
    return pd.concat(parts).sort_index() if parts else pd.DataFrame()


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


def _lagged(series: pd.Series, n: int, step: int, how: str) -> pd.Series:
    """최근 n주 값의 최소/최대. step=1은 주봉(연속 행), step=5는 일봉 버전(5거래일 간격)."""
    if step == 1:
        r = series.rolling(n)
        return r.min() if how == "min" else r.max()
    lagged = pd.concat([series.shift(step * j) for j in range(n)], axis=1)
    out = lagged.min(axis=1) if how == "min" else lagged.max(axis=1)
    return out.where(lagged.notna().all(axis=1))


def action_signals(history: pd.DataFrame, rules: dict | None = None, step: int = 1) -> pd.DataFrame:
    """주별 행동 신호. action: '매수' / '매도' / '' (조건이 처음 켜진 주에만), state: 신호 이후 상태.
    step=5면 일봉 버전 기록(하루 한 줄)에서 'n주 연속'을 5거래일 간격으로 확인."""
    r = rules or ACTION_RULES
    b, s = r["buy"], r["sell"]
    out = history.copy()
    chart = out["chart_score"].astype(float)
    buy_on = (_lagged(chart, b["weeks"], step, "min") >= b["chart_min"]) & (out["macro_score"] >= b["macro_min"])
    above = out["above_ma20d"].map(lambda v: bool(v) if pd.notna(v) else True)
    sell_on = _lagged(chart, s["weeks"], step, "max") <= s["chart_max"]
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
REBOUND_PLAYERS = {"rebound_base": "과거 확률 (기준)", "macro_check": "거시 체크리스트", "exp_v3": "🧪 실험 v3 (도전 규칙 매수 + 대표 규칙 매도)"}
ENSEMBLE_RULES = {"min_weeks": 20, "lookback": 26, "cap": 0.5, "shrink": 0.5}


def player_label(pid: str) -> str:
    if pid.startswith("rule_v"):
        return f"규칙 {pid[5:]}"
    if pid.startswith("agent_"):
        return AGENT_PLAYERS.get(pid, {}).get("label") or f"🤖 AI 요원 {pid[6:]}위"
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


# ---- AI 요원 선수: 훈련된 유전자 그대로 (원칙을 덮어씌우지 않음) ----
#  simulate()와 같은 규칙: t일 주문은 t-1일 지표로 결정, 지정가 매수(전일 종가×(1-할인)), 평단×(1+목표) 지정가 매도,
#  1회분 = 예산 ÷ 분할 × 공포 매수배수(최대 3배), 분할 횟수·잔여 현금 한도. 매도하면 사이클 새로 시작.
AGENT_PLAYERS: dict[str, dict] = {}


def set_agents(models: list[dict], top: int = 3) -> None:
    """앱·자동 점검이 DB의 요원(순위순)을 선수로 등록. pid = agent_<순위>."""
    AGENT_PLAYERS.clear()
    for m in sorted(models or [], key=lambda m: int(m.get("rank") or 99))[:top]:
        r = int(m.get("rank") or 0)
        AGENT_PLAYERS[f"agent_{r}"] = {"genes": pad_genes(m.get("genes") or []), "label": f"🤖 AI 요원 {r}위",
                                       "ticker": str(m.get("ticker") or "SOXL").upper(),
                                       "trained_at": m.get("trained_at"), "years": m.get("years")}


def agents_for(ticker: str | None) -> dict[str, dict]:
    return {k: v for k, v in AGENT_PLAYERS.items() if ticker and v["ticker"] == str(ticker).upper()}


def agent_exposure(daily: pd.DataFrame, genes, start: str = TECHNIQUE_START, capital: float = 10_000.0,
                   fee: float = _FEE) -> pd.Series:
    """AI 요원 한 명의 날짜별 투자 비중(주식 평가액 ÷ 계좌 총액)."""
    d = daily.dropna(subset=["Close"])
    feats = compute_features(d["Close"], d["Volume"] if "Volume" in d else pd.Series(1.0, index=d.index))
    o = (d["Open"] if "Open" in d else d["Close"]).fillna(d["Close"]).to_numpy(float)
    h = (d["High"] if "High" in d else d["Close"]).fillna(d["Close"]).to_numpy(float)
    l = (d["Low"] if "Low" in d else d["Close"]).fillna(d["Close"]).to_numpy(float)
    c = d["Close"].to_numpy(float)
    g = pad_genes(genes)
    first = int(np.searchsorted(d.index.values, np.datetime64(pd.Timestamp(start))))
    first = max(first, 21)
    cash, qty, cost, buys = capital, 0.0, 0.0, 0
    out = np.full(len(c), np.nan)
    for t in range(first, len(c)):
        p = {k: float(v[0]) for k, v in action_params(g, feats[t - 1]).items()}
        sold = False
        if qty > 1e-12:
            target = cost / qty * (1 + p["target_r"] / 100)
            if h[t] >= target:
                cash += qty * max(target, o[t]) * (1 - fee)
                qty = cost = 0.0
                buys, sold = 0, True
        limit = c[t - 1] * (1 - p["discount"] / 100)
        amount = min(capital / p["split"] * p["buy_mult"], cash)
        if not sold and buys < p["split"] and amount >= 1.0 and l[t] <= limit:
            fill = min(limit, o[t])
            qty += amount * (1 - fee) / fill
            cost += amount
            cash -= amount
            buys += 1
        eq = cash + qty * c[t]
        out[t] = qty * c[t] / eq if eq > 0 else 0.0
    return pd.Series(out, index=d.index)


def agent_segments(meta: dict, today: pd.Timestamp | None = None) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    """요원 성적을 나눠 볼 구간: 훈련 전(본 적 없는 과거) / 훈련에 쓴 기간 / 훈련 뒤(실전)."""
    today = today or pd.Timestamp.today().normalize()
    try:
        tr = pd.Timestamp(meta.get("trained_at"))
    except Exception:
        tr = pd.NaT
    if pd.isna(tr):
        return [("전체", pd.Timestamp(TECHNIQUE_START), today)]
    yrs = float(meta.get("years") or 5)
    t0 = tr - pd.DateOffset(days=int(yrs * 365.25))
    segs = [("훈련 전 (본 적 없는 과거)", pd.Timestamp(TECHNIQUE_START), t0), ("훈련에 쓴 기간", t0, tr)]
    if today > tr:
        segs.append(("훈련 뒤 (실전)", tr, today))
    return segs


def segment_compare(positions: pd.DataFrame, close: pd.Series, pids: list[str],
                    segments: list[tuple[str, pd.Timestamp, pd.Timestamp]], fee_bps: float = 10) -> pd.DataFrame:
    """같은 구간·같은 잣대(주별 비중 × 다음 주 수익률)로 선수 비교."""
    rows = []
    for name, a, b in segments:
        m = (positions.index >= a) & (positions.index < b)
        if m.sum() < 12:
            continue
        bt = backtest_players(positions.loc[m, [p for p in pids if p in positions]], close[m], test_weeks=0, fee_bps=fee_bps)
        for _, r in bt.iterrows():
            rows.append({"구간": f"{name} {a:%Y-%m}~{b:%Y-%m}", "선수": r["선수"], "id": r["id"],
                         "누적(%)": r["전체 누적(%)"], "연평균(%)": r["연평균(%)"], "MDD(%)": r["전체 MDD(%)"],
                         "칼마": r["칼마"], "평균 비중(%)": r["평균 비중(%)"], "주 수": r["주 수"]})
    return pd.DataFrame(rows)


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


def agent_positions(index: pd.DatetimeIndex, daily: pd.DataFrame, ticker: str | None, weekly: bool = True) -> pd.DataFrame:
    out = {}
    for pid, a in agents_for(ticker).items():
        e = agent_exposure(daily, a["genes"])
        out[pid] = (e.resample("W-FRI").last() if weekly else e).reindex(index).round(4)
    return pd.DataFrame(out, index=index)


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
    if agents_for(ticker):
        out = out.join(agent_positions(hist.index, daily, ticker))
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

        def stats(x: pd.Series) -> tuple[float, float, float, float, int]:
            eq = (1 + x).cumprod()
            dd = eq / eq.cummax() - 1
            years = len(x) / 52
            cagr = float((eq.iloc[-1] ** (1 / years) - 1) * 100) if years > 0 and eq.iloc[-1] > 0 else float("nan")
            mdd = float(dd.min() * 100)
            calmar = cagr / abs(mdd) if mdd < 0 else float("nan")
            under = (dd < -1e-9).astype(int)
            longest = int(under.groupby((under == 0).cumsum()).sum().max()) if len(under) else 0
            return float((eq.iloc[-1] - 1) * 100), mdd, cagr, calmar, longest

        full_ret, full_mdd, full_cagr, full_calmar, full_under = stats(wr)
        recent = wr[wr.index > cut]
        if len(recent) > 4:
            rec_ret, rec_mdd, rec_cagr, rec_calmar, _ = stats(recent)
        else:
            rec_ret = rec_mdd = rec_cagr = rec_calmar = float("nan")
        trades = len(position_moves(p)) / max(len(p) / 52, 1e-9)
        rows.append({"선수": player_label(pid), "id": pid, "평균 비중(%)": round(float(p.mean() * 100), 0),
                     "전체 누적(%)": round(full_ret, 1), "연평균(%)": round(full_cagr, 1),
                     "전체 MDD(%)": round(full_mdd, 1), "칼마": round(full_calmar, 2),
                     "최장 회복(주)": full_under, "연 매매(회)": round(trades, 1),
                     "최근 2년 누적(%)": round(rec_ret, 1), "최근 2년 MDD(%)": round(rec_mdd, 1),
                     "최근 2년 칼마": round(rec_calmar, 2), "주 수": int(valid.sum())})
    return pd.DataFrame(rows)


# ---- 일봉 리그: 모든 선수가 매일 비중을 냄 (오늘 종가에 정한 비중 × 내일 수익률로 채점) ----
DAILY_TABLE = "valhalla_daily_positions"
DAILY_LIVE_START = "2026-11-24"


def daily_player_positions(daily: pd.DataFrame, ref_daily: pd.DataFrame | None, day_hist: pd.DataFrame,
                           rules: list[tuple[int, dict]], ticker: str | None = None) -> pd.DataFrame:
    """일봉 버전 선수들의 날짜별 비중표. 규칙은 '최근 5거래일' 일봉 버전, 추세·변동성은 매일 계산,
    레버리지 ETF면 매매기법 연구소 기법·찰스 시스템(원래 매일 매매)."""
    idx = daily.index
    out = pd.DataFrame(index=idx)
    for v, rule in rules:
        if not day_hist.empty:
            out[f"rule_v{v}"] = rule_positions(action_signals(day_hist, rule, step=5)).reindex(idx)
    if ref_daily is not None and not ref_daily.empty:
        c = ref_daily["Close"]
        ma = c.rolling(200).mean()
        tr = (c > ma).astype(float).where(ma.notna())
        out["trend200"] = tr.reindex(tr.index.union(idx)).ffill().reindex(idx)
    r = daily["Close"].pct_change()
    vol = r.rolling(20).std()
    normal = vol.rolling(252, min_periods=126).median()
    out["voltarget"] = (normal / vol).clip(0, 1).where(normal.notna() & (vol > 0))
    out["hold"] = 1.0
    if ticker is not None and category_of(ticker) in TECHNIQUE_CATEGORIES:
        out["inf_mine"] = infinite_exposure(daily).reindex(idx)
        out["inf_v2"] = infinite_exposure(daily, star=True).reindex(idx)
        out["inf_v2_safe"] = infinite_exposure(daily, star=True, quarter_cut=True, trend_filter=True).reindex(idx)
        out["rebal50"] = rebalance_exposure(daily).reindex(idx)
        out["charles"] = charles_exposure(daily, ref_daily).reindex(idx)
    if agents_for(ticker):
        out = out.join(agent_positions(idx, daily, ticker, weekly=False))
    return out.round(4)


def daily_today(pos: pd.DataFrame, step: float = 0.25) -> pd.DataFrame:
    """선수별 오늘 비중, 어제 대비 변화, 오늘 매수·매도 표시(마지막 표시 때보다 25%p 이상 바뀌면)."""
    rows = []
    day = pos.index[-1]
    for pid in pos.columns:
        s_ = pos[pid].dropna()
        if s_.empty:
            continue
        moves = position_moves(s_, step)
        today = moves[moves.index == day]
        last = moves.tail(1)
        rows.append({"id": pid, "오늘 비중": float(s_.iloc[-1]),
                     "어제 대비(%p)": round(float((s_.iloc[-1] - s_.iloc[-2]) * 100), 0) if len(s_) > 1 else 0.0,
                     "오늘 신호": today["action"].iloc[0] if not today.empty else "",
                     "마지막 신호": f"{last['action'].iloc[0]} {last.index[0]:%m/%d}" if not last.empty else "—"})
    return pd.DataFrame(rows)


def grade_daily(rows: pd.DataFrame, close: pd.Series) -> pd.DataFrame:
    """일봉 리그 기록에 다음 거래일 수익률(ret_1d, %)을 채움."""
    if rows is None or rows.empty:
        return rows
    out = rows.copy()
    c = close.dropna()
    if "ret_1d" not in out:
        out["ret_1d"] = np.nan
    for i, row in out.iterrows():
        if pd.notna(row.get("ret_1d")):
            continue
        d = pd.Timestamp(row["day"])
        if d in c.index:
            k = c.index.get_loc(d)
            if k + 1 < len(c):
                out.at[i, "ret_1d"] = round(float((c.iloc[k + 1] / c.iloc[k] - 1) * 100), 4)
    return out


def daily_league_table(rows: pd.DataFrame, tickers: list[str] | None = None) -> pd.DataFrame:
    """실전 일봉 리그 경기표: 종목별 (비중×다음날 수익) 누적을 종목 평균, '그냥 보유' 대비."""
    if rows is None or rows.empty:
        return pd.DataFrame()
    d = rows.dropna(subset=["ret_1d"]).copy()
    if tickers:
        d = d[d["ticker"].isin(tickers)]
    if d.empty:
        return pd.DataFrame()
    d["pr"] = d["position"].astype(float) * d["ret_1d"].astype(float) / 100
    res = []
    for p, g in d.groupby("player"):
        per = g.sort_values("day").groupby("ticker")["pr"].apply(lambda x: float((1 + x).prod() - 1))
        res.append({"id": p, "선수": player_label(p), "누적(%)": round(float(per.mean() * 100), 2),
                    "채점 일수": int(g["day"].nunique()), "종목 수": int(g["ticker"].nunique()),
                    "평균 비중(%)": round(float(g["position"].mean() * 100), 0)})
    t = pd.DataFrame(res).sort_values("누적(%)", ascending=False).reset_index(drop=True)
    hold = t.loc[t["id"] == "hold", "누적(%)"]
    t["보유 대비(%p)"] = (t["누적(%)"] - (float(hold.iloc[0]) if not hold.empty else np.nan)).round(2)
    return t


def backtest_daily(positions: pd.DataFrame, close: pd.Series, test_days: int = 504,
                   fee_bps: float = 10) -> pd.DataFrame:
    """일 단위 과거 성적: 오늘 종가 비중 × 내일 수익률 (거래 비용 반영). 주봉 전략은 금요일 비중을 다음 주 내내 유지."""
    ret = close.pct_change().shift(-1)
    cut = close.index[-1] - pd.Timedelta(days=int(test_days * 365 / 252))
    rows = []
    for pid in positions.columns:
        p_all = positions[pid].astype(float)
        valid = p_all.notna() & ret.notna()
        if valid.sum() < 50:
            continue
        p, r = p_all[valid], ret[valid]
        wr = p * r - p.diff().abs().fillna(p.iloc[0]) * fee_bps / 10_000

        def stats(x):
            eq = (1 + x).cumprod()
            dd = eq / eq.cummax() - 1
            yrs = len(x) / 252
            cagr = float((eq.iloc[-1] ** (1 / yrs) - 1) * 100) if eq.iloc[-1] > 0 else float("nan")
            mdd = float(dd.min() * 100)
            under = (dd < -1e-9).astype(int)
            longest = int(under.groupby((under == 0).cumsum()).sum().max())
            return float((eq.iloc[-1] - 1) * 100), mdd, cagr, (cagr / abs(mdd) if mdd < 0 else float("nan")), longest

        tot, mdd, cagr, calmar, longest = stats(wr)
        rec = wr[wr.index > cut]
        rtot, rmdd, _, rcal, _ = stats(rec) if len(rec) > 20 else (np.nan,) * 5
        trades = len(position_moves(p)) / max(len(p) / 252, 1e-9)
        rows.append({"전략": pid, "연평균(%)": round(cagr, 1), "최대 낙폭(%)": round(mdd, 1),
                     "칼마": round(calmar, 2), "최장 회복(주)": round(longest / 5), "연 매매(회)": round(trades, 1),
                     "최근 2년 누적(%)": round(rtot, 1), "최근 2년 MDD(%)": round(rmdd, 1),
                     "최근 2년 칼마": round(rcal, 2), "기간": f"{p.index[0]:%Y}~"})
    return pd.DataFrame(rows)


def weekly_to_daily_positions(weekly_pos: pd.Series, daily_index: pd.DatetimeIndex) -> pd.Series:
    """금요일 종가에 정한 비중을 다음 금요일 전까지 매일 유지."""
    return weekly_pos.reindex(weekly_pos.index.union(daily_index)).ffill().reindex(daily_index)


def experiment_rules(active: dict, challengers: list[tuple[int, dict]]) -> dict[str, dict]:
    """과거 성적 비교 전용 실험 규칙 (실전 장부에는 기록하지 않음).
    v3 = 도전 규칙(첫 번째)의 매수 조건 + 대표 규칙의 매도 조건."""
    if not challengers:
        return {}
    cv, crule = challengers[0]
    return {"exp_v3": {"buy": dict(crule["buy"]), "sell": dict(active["sell"])}}


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


# ==========================================================================
# 18. 반등 체크리스트 (거시 지표) + 일정 달력 + 반등 판별전 선수 '거시 체크리스트'
#   조건 4개: ① 10년물 금리 4.5% 미만 ② 10년물 금리가 한 달 전보다 낮음
#            ③ VIX 30 미만 ④ 신용 스프레드(무디스 BAA - 10년물)가 3개월 전보다 넓어지지 않음
#   선수 확률 = 0.2 + 0.6 × (충족 수 / 4)  → 0개 20%, 4개 80%
#   하이일드 스프레드(4% 기준)와 근원 PCE는 화면 표시용 (FRED의 하이일드 시계열은 최근 3년만 제공)
# ==========================================================================
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
FRED_SERIES = {"hy": "BAMLH0A0HYM2", "baa": "BAA10Y", "core_pce": "PCEPILFE"}
MACRO_CHECK_PLAYER = "macro_check"
CHECK_RULES = {"tnx_max": 4.5, "vix_max": 30.0, "hy_max": 4.0, "tnx_days": 21, "credit_days": 63}

# 날짜는 한국 시간. confirmed=False는 요청·예정 단계(바뀔 수 있음).
EVENT_CALENDAR = [
    ("2026-11-25 22:30", "미국 10월 PCE 물가 · 3분기 GDP 수정치", "연준이 가장 중시하는 물가. 높으면 금리 인상 우려", True),
    ("2026-11-27 03:00", "추수감사절 휴장(26일) 뒤 반나절 장", "27일 금요일은 오후 1시(미국 동부) 조기 폐장", True),
    ("2026-12-01 00:00", "하원 위원회, 젠슨 황 출석 요청일 (12/1~2)", "비공개 증언 요청 단계 · 날짜 바뀔 수 있음", False),
    ("2026-12-03 00:00", "상원 은행위원회 청문회 (젠슨 황 출석 요청)", "출석 동의 보도는 있으나 날짜 미확정", False),
    ("2026-12-10 04:00", "FOMC 금리 결정 (12/8~9 회의)", "12월 금리 인상 여부 · 경제전망 발표", True),
    ("2026-12-10 06:00", "브로드컴 4분기 실적 (12/9 장 마감 뒤)", "맞춤형 AI 칩 수요 확인", True),
    ("2026-12-10 15:00", "TSMC 11월 매출 (무렵)", "AI 칩 생산 수요의 월간 확인", False),
    ("2026-12-18 06:00", "마이크론 실적 (12/17 장 마감 뒤)", "2027년 AI 메모리 가격 반영 첫 전망", True),
    ("2026-12-23 22:30", "미국 11월 PCE 물가", "연말 마지막 물가 지표", True),
]


def upcoming_events(now: pd.Timestamp, days: int = 45) -> pd.DataFrame:
    rows = []
    for when, name, note, ok in EVENT_CALENDAR:
        t = pd.Timestamp(when)
        if now - pd.Timedelta(hours=12) <= t <= now + pd.Timedelta(days=days):
            rows.append({"when": t, "일정": name, "메모": note, "확정": ok})
    return pd.DataFrame(rows, columns=["when", "일정", "메모", "확정"])


def check_conditions(tnx: pd.Series, vix: pd.Series, credit: pd.Series, rules: dict | None = None) -> pd.DataFrame:
    """날짜별 체크리스트 4조건(참/거짓)과 '거시 체크리스트' 선수 확률. 입력은 일별 시계열(%)."""
    th = {**CHECK_RULES, **(rules or {})}
    idx = tnx.dropna().index.union(vix.dropna().index)
    t = tnx.reindex(idx).ffill()
    v = vix.reindex(idx).ffill()
    c = credit.reindex(idx.union(credit.dropna().index)).ffill().reindex(idx)
    out = pd.DataFrame(index=idx)
    out["tnx_low"] = t < th["tnx_max"]
    out["tnx_falling"] = t < t.shift(th["tnx_days"])
    out["vix_calm"] = v < th["vix_max"]
    out["credit_ok"] = c <= c.shift(th["credit_days"])
    valid = t.notna() & v.notna() & c.notna() & t.shift(th["tnx_days"]).notna() & c.shift(th["credit_days"]).notna()
    k = out[["tnx_low", "tnx_falling", "vix_calm", "credit_ok"]].sum(axis=1)
    out["met"] = k.where(valid)
    out["prob"] = (0.2 + 0.6 * k / 4).where(valid)
    return out


def check_calls_at(checks: pd.DataFrame, weeks: pd.Series) -> pd.Series:
    """반등 사건 주(금요일)마다 그날까지의 마지막 체크리스트 확률."""
    p = checks["prob"].dropna()
    vals = []
    for wk in weeks:
        upto = p[p.index <= wk]
        vals.append(float(upto.iloc[-1]) if not upto.empty else np.nan)
    return pd.Series(vals, index=pd.DatetimeIndex(weeks))


def checklist_snapshot(tnx: pd.Series, vix: pd.Series, credit: pd.Series, hy: pd.Series | None,
                       core_pce: pd.Series | None) -> list[dict]:
    """화면용 현재 상태. status: ok / warn / stop."""
    th = CHECK_RULES
    rows = []
    t = tnx.dropna()
    if len(t) > th["tnx_days"]:
        now, before = float(t.iloc[-1]), float(t.iloc[-1 - th["tnx_days"]])
        rows.append({"지표": "미국 10년물 금리", "현재": f"{now:.2f}%", "기준": f"{th['tnx_max']}% 아래",
                     "상태": "ok" if now < th["tnx_max"] else ("warn" if now < 5.0 else "stop"),
                     "변화": f"{now - before:+.2f}%p (한 달)", "설명": "금리가 내려와야 성장주·레버리지 ETF 부담이 줄어요"})
        rows.append({"지표": "10년물 금리 방향", "현재": "하락" if now < before else "상승",
                     "기준": "한 달 전보다 낮음", "상태": "ok" if now < before else "stop",
                     "변화": f"{before:.2f}% → {now:.2f}%", "설명": "수준보다 방향이 먼저 바뀌는 경우가 많아요"})
    v = vix.dropna()
    if len(v):
        now = float(v.iloc[-1])
        rows.append({"지표": "VIX (공포 지수)", "현재": f"{now:.1f}", "기준": f"{th['vix_max']:.0f} 아래",
                     "상태": "ok" if now < 20 else ("warn" if now < th["vix_max"] else "stop"),
                     "변화": f"{now - float(v.iloc[max(0, len(v) - 6)]):+.1f} (5일)", "설명": "30 위는 패닉 구간"})
    c = credit.dropna()
    if len(c) > th["credit_days"]:
        now, before = float(c.iloc[-1]), float(c.iloc[-1 - th["credit_days"]])
        rows.append({"지표": "신용 스프레드 (BAA−10년물)", "현재": f"{now:.2f}%p", "기준": "3개월 전보다 좁거나 같음",
                     "상태": "ok" if now <= before else "warn", "변화": f"{now - before:+.2f}%p (3개월)",
                     "설명": "회사채 시장 불안이 번지는지 보는 지표"})
    if hy is not None and len(hy.dropna()):
        now = float(hy.dropna().iloc[-1])
        rows.append({"지표": "하이일드 스프레드", "현재": f"{now:.2f}%", "기준": f"{th['hy_max']}% 아래",
                     "상태": "ok" if now < 3.5 else ("warn" if now < th["hy_max"] else "stop"),
                     "변화": f"{hy.dropna().index[-1]:%m/%d} 기준", "설명": "4%를 넘으면 2018년 말 같은 신용 불안 경계"})
    if core_pce is not None and len(core_pce.dropna()) > 12:
        x = core_pce.dropna()
        yoy = float((x.iloc[-1] / x.iloc[-13] - 1) * 100)
        prev = float((x.iloc[-2] / x.iloc[-14] - 1) * 100)
        rows.append({"지표": "근원 PCE 물가 (전년 대비)", "현재": f"{yoy:.1f}%", "기준": "낮아지는 중",
                     "상태": "ok" if yoy < prev else ("warn" if yoy == prev else "stop"),
                     "변화": f"{prev:.1f}% → {yoy:.1f}% ({x.index[-1]:%Y-%m})", "설명": "연준이 가장 중시하는 물가"})
    return rows


# ==========================================================================
# 19. 포트폴리오 선수 · 리밸런싱 선수 (토스 장기 계좌용)
#   테마 순위: 타이탄봇과 같은 식(1주 20%·1달 30%·3달 50% 수익률 순위 가중합, 4주 전 순위와 비교)
#   주도 테마 = 순위 3위 안 + 50일선 위 중 1등, 떠오르는 테마 = 4주 전보다 3계단 이상 오른 테마 중 가장 많이 오른 것
#   핵심 = SOXL (규칙 v1 상태로 보유/현금), 테마 칸은 해당 테마의 레버리지 ETF(없으면 2배, 그것도 없으면 1배)
# ==========================================================================
THEME_ETFS = {   # 테마: (1배 대표 ETF, 레버리지 후보들(앞에서부터, 배수))
    "반도체": ("SOXX", [("SOXL", 3)]), "AI 소프트웨어": ("IGV", []), "AI 전력·인프라": ("GRID", []),
    "원전·우라늄": ("URA", []), "방산·우주": ("ITA", [("DFEN", 3)]), "사이버보안": ("CIBR", [("UCYB", 2)]),
    "바이오": ("XBI", [("LABU", 3)]), "헬스케어": ("XLV", [("CURE", 3)]), "금융": ("XLF", [("FAS", 3)]),
    "에너지": ("XLE", [("ERX", 2)]), "산업재": ("XLI", [("DUSL", 3)]), "소비재": ("XLY", [("WANT", 3)]),
    "금·귀금속": ("GDX", [("NUGT", 2)]), "크립토": ("IBIT", [("BITX", 2)]), "중국 인터넷": ("KWEB", [("CWEB", 2)]),
    "리츠": ("VNQ", [("DRN", 3)]),
}
THEME_WEIGHTS = {"ret_1w": (5, 0.2), "ret_1m": (21, 0.3), "ret_3m": (63, 0.5)}
PORTFOLIO_TARGET = {"core": 0.50, "lead": 0.25, "rising": 0.25}
PORTFOLIO_START = "2018-06-01"
PORTFOLIO_LIVE_START = "2026-11-27"
PORTFOLIO_TABLE = "valhalla_portfolio_holdings"
PORTFOLIO_SETTINGS = "valhalla_portfolio_settings"
HARVEST = {"cash": 0.20, "band": 0.25}
PORTFOLIO_PLAYERS = {
    "pf_harvest": "리밸런싱 · 수익 실현형 (오르면 팔아 현금, 떨어지면 다시 삼)",
    "pf_month": "포트폴리오 · 월간 리밸런싱", "pf_week": "리밸런싱 · 매주", "pf_band": "리밸런싱 · 밴드 ±5%p",
    "pf_none": "리밸런싱 · 안 함(테마 바뀔 때만)", "pf_trend": "포트폴리오 + 추세 필터 (월간)",
    "bm_soxl": "기준 · SOXL 그냥 보유", "bm_spy": "기준 · S&P500 그냥 보유",
}


def theme_rank_history(closes: dict[str, pd.Series], dates: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    """날짜별 테마 점수·순위·50일선 위 여부·4주 전 순위 (그날까지의 데이터만)."""
    frame = pd.DataFrame({k: v for k, v in closes.items()}).sort_index()
    score_parts = []
    for _, (n, w) in THEME_WEIGHTS.items():
        r = frame / frame.shift(n) - 1
        score_parts.append(r.rank(axis=1, pct=True) * w)
    score = sum(score_parts) * 100
    valid = frame.notna() & frame.shift(63).notna()
    score = score.where(valid)
    rank = score.rank(axis=1, ascending=False, method="min")
    rank_prev = rank.shift(20)
    above = (frame > frame.rolling(50).mean()).where(valid)
    pick = lambda df: df.reindex(frame.index.union(dates)).ffill(limit=3).reindex(dates)
    return {"score": pick(score), "rank": pick(rank), "rank_prev": pick(rank_prev), "above50": pick(above)}


def pick_themes(rank: pd.Series, rank_prev: pd.Series, above50: pd.Series, exclude: tuple = ("반도체",)) -> tuple:
    """(주도 테마, 떠오르는 테마). 반도체는 핵심(SOXL)과 겹치므로 제외."""
    r = rank.dropna()
    r = r[~r.index.isin(exclude)]
    if r.empty:
        return None, None
    lead_c = [t for t in r.sort_values().index if r[t] <= 3 + len(exclude) and bool(above50.get(t, False))]
    lead = lead_c[0] if lead_c else r.sort_values().index[0]
    chg = (rank_prev.reindex(r.index) - r).dropna()
    chg = chg[(chg >= 3) & (chg.index != lead)]
    if not chg.empty:
        best = chg[chg == chg.max()].index
        rising = min(best, key=lambda t: r[t])
    else:
        rest = [t for t in r.sort_values().index if t != lead]
        rising = rest[0] if rest else None
    return lead, rising


def theme_vehicle(theme: str | None, prices: dict[str, pd.Series], when: pd.Timestamp) -> tuple[str | None, int]:
    """그 날짜에 거래 가능한 테마 ETF (레버리지 우선)."""
    if theme is None:
        return None, 0
    base, levs = THEME_ETFS[theme]
    for t, k in levs:
        s_ = prices.get(t)
        if s_ is not None and not s_.dropna().empty and s_.dropna().index[0] <= when - pd.Timedelta(days=30):
            return t, k
    return base, 1


PORTFOLIO_HOLD = {"lead_keep_rank": 5, "rising_min_weeks": 4}


def theme_schedule(weeks: pd.DatetimeIndex, ranks: dict, rules: dict | None = None) -> pd.DataFrame:
    """주별 (주도, 떠오르는) 테마. 잦은 교체를 막는 유지 규칙:
    주도 테마는 순위 5위 안에 있는 동안 유지, 떠오르는 테마는 최소 4주 유지 후 다시 고름."""
    th = {**PORTFOLIO_HOLD, **(rules or {})}
    rows, lead, rising, held = [], None, None, 0
    for w in weeks:
        r, rp, ab = ranks["rank"].loc[w], ranks["rank_prev"].loc[w], ranks["above50"].loc[w]
        new_lead, new_rising = pick_themes(r, rp, ab)
        if lead is None or pd.isna(r.get(lead)) or r.get(lead) > th["lead_keep_rank"]:
            lead = new_lead
        held += 1
        if rising is None or rising == lead or held >= th["rising_min_weeks"] or pd.isna(r.get(rising)):
            cand = new_rising if new_rising != lead else None
            if cand is None:
                rest = [t for t in r.dropna().sort_values().index if t not in (lead, "반도체")]
                cand = rest[0] if rest else None
            if cand != rising:
                rising, held = cand, 0
            elif held >= th["rising_min_weeks"]:
                held = 0
        rows.append({"week": w, "lead": lead, "rising": rising})
    return pd.DataFrame(rows).set_index("week")


def portfolio_targets(week: pd.Timestamp, ranks: dict, prices: dict[str, pd.Series], core_on: float,
                      target: dict | None = None, trend: bool = False, themes: tuple | None = None) -> dict[str, float]:
    """한 주의 목표 비중 {티커 또는 'CASH': 비중}. themes=(주도, 떠오르는)를 주면 그대로 씀."""
    tg = {**PORTFOLIO_TARGET, **(target or {})}
    if themes is not None:
        lead, rising = themes
    else:
        lead, rising = pick_themes(ranks["rank"].loc[week], ranks["rank_prev"].loc[week], ranks["above50"].loc[week])
    out: dict[str, float] = {}

    def add(t, w):
        out[t] = out.get(t, 0.0) + w

    add("SOXL" if core_on >= 0.5 else "CASH", tg["core"])
    for theme, w in ((lead, tg["lead"]), (rising, tg["rising"])):
        vehicle, _ = theme_vehicle(theme, prices, week)
        if vehicle is None:
            add("CASH", w)
            continue
        if trend:
            base = THEME_ETFS[theme][0]
            b = prices.get(base, pd.Series(dtype=float)).dropna()
            b = b[b.index <= week]
            if len(b) < 200 or b.iloc[-1] < b.tail(200).mean():
                add("CASH", w)
                continue
        add(vehicle, w)
    cash_rest = 1 - sum(out.values())
    if cash_rest > 1e-9:
        add("CASH", cash_rest)
    return out


def simulate_portfolio(weekly: pd.DataFrame, targets: dict[pd.Timestamp, dict], policy: str,
                       band: float = 0.05, fee_bps: float = 10) -> pd.DataFrame:
    """주간 시뮬레이션. weekly = 주별 종가(열=티커). policy: week/month/band/none.
    반환: 주별 가치(value), 회전율(turnover), 보유 비중(weights)."""
    rets = weekly.pct_change()
    dates = [d for d in weekly.index if d in targets]
    hold: dict[str, float] = {}
    value, rows, prev_month = 1.0, [], None
    for i, d in enumerate(dates):
        if i > 0:
            grow = 0.0
            for t, w in hold.items():
                r = 0.0 if t == "CASH" else float(rets.at[d, t]) if pd.notna(rets.at[d, t]) else 0.0
                grow += w * (1 + r)
            hold = {t: w * (1 + (0.0 if t == "CASH" else float(rets.at[d, t]) if pd.notna(rets.at[d, t]) else 0.0)) / grow
                    for t, w in hold.items()}
            value *= grow
        tgt = targets[d]
        drift = max(abs(tgt.get(k, 0) - hold.get(k, 0)) for k in set(tgt) | set(hold)) if hold else 1.0
        full = (not hold or policy == "week"
                or (policy == "month" and d.month != prev_month)
                or (policy == "band" and drift > band))
        before = dict(hold)
        if policy == "harvest" and hold:
            # 수익 실현형: 목표보다 band(상대 25%) 넘게 커진 종목은 초과분을 팔아 현금으로,
            # 목표보다 band 넘게 작아진 종목은 현금으로 목표까지 다시 삼 (현금이 모자라면 있는 만큼)
            removed = {k: w for k, w in hold.items() if k not in tgt}
            if removed:          # 테마·핵심 상태 교체는 그 칸만 바꿈
                freed = sum(removed.values())
                hold = {k: w for k, w in hold.items() if k in tgt}
                added = [k for k in tgt if k not in hold and k != "CASH"]
                tw = sum(tgt[k] for k in added)
                for k in added:
                    hold[k] = freed * tgt[k] / tw
                if not added:
                    hold["CASH"] = hold.get("CASH", 0.0) + freed
            for k in [k for k in tgt if k != "CASH"]:
                w, t_ = hold.get(k, 0.0), tgt[k]
                if w > t_ * (1 + band):
                    hold["CASH"] = hold.get("CASH", 0.0) + (w - t_)
                    hold[k] = t_
            for k in [k for k in tgt if k != "CASH"]:
                w, t_ = hold.get(k, 0.0), tgt[k]
                if w < t_ * (1 - band) and hold.get("CASH", 0.0) > 1e-9:
                    buy = min(t_ - w, hold["CASH"])
                    hold[k] = w + buy
                    hold["CASH"] -= buy
        elif full:
            hold = dict(tgt)
        else:
            # 테마·핵심 상태가 바뀐 칸만 교체: 빠진 종목을 팔아 새 종목을 목표 비율대로 삼 (나머지는 그대로)
            removed = {k: w for k, w in hold.items() if k not in tgt}
            added = [k for k in tgt if k not in hold]
            if removed or added:
                freed = sum(removed.values())
                hold = {k: w for k, w in hold.items() if k in tgt}
                if added:
                    tw = sum(tgt[k] for k in added)
                    for k in added:
                        hold[k] = hold.get(k, 0.0) + freed * tgt[k] / tw
                elif freed > 0:
                    k = "CASH" if "CASH" in tgt else max(tgt, key=tgt.get)
                    hold[k] = hold.get(k, 0.0) + freed
        turnover = sum(abs(hold.get(k, 0) - before.get(k, 0)) for k in set(hold) | set(before) if k != "CASH")
        if turnover > 0:
            value *= 1 - turnover * fee_bps / 10_000
        prev_month = d.month
        rows.append({"week": d, "value": value, "turnover": turnover, "weights": dict(hold)})
    return pd.DataFrame(rows).set_index("week")


def portfolio_stats(value: pd.Series, live_start: str | None = None) -> dict:
    v = value.dropna()
    r = v.pct_change().dropna()
    yrs = len(r) / 52
    cagr = (v.iloc[-1] / v.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
    dd = v / v.cummax() - 1
    under = (dd < -1e-9).astype(int)
    longest = int(under.groupby((under == 0).cumsum()).sum().max()) if len(under) else 0
    cut = v.index[-1] - pd.Timedelta(weeks=104)
    rv = v[v.index >= cut]
    out = {"연평균(%)": round(cagr * 100, 1), "최대 낙폭(%)": round(float(dd.min() * 100), 1),
           "칼마": round(cagr / abs(dd.min()), 2) if dd.min() < 0 else np.nan, "최장 회복(주)": longest,
           "최근 2년(%)": round(float((rv.iloc[-1] / rv.iloc[0] - 1) * 100), 1) if len(rv) > 4 else np.nan}
    if live_start:
        lv = v[v.index >= pd.Timestamp(live_start)]
        out["실전(%)"] = round(float((lv.iloc[-1] / lv.iloc[0] - 1) * 100), 2) if len(lv) > 1 else np.nan
    return out


PORTFOLIO_TICKERS = sorted({"SOXL", "SPY"} | {b for b, _ in THEME_ETFS.values()} |
                           {t for _, levs in THEME_ETFS.values() for t, _ in levs})


def portfolio_lab(daily: dict[str, pd.Series], core_state: pd.Series, asof: pd.Timestamp,
                  target: dict | None = None) -> dict:
    """테마 순위 재현 → 유지 규칙 → 주별 목표 비중 → 선수별 시뮬레이션. daily = {티커: 일별 종가}."""
    weeks = pd.date_range(PORTFOLIO_START, asof, freq="W-FRI")
    weeks = weeks[weeks <= asof]
    ranks = theme_rank_history({th: daily[b] for th, (b, _) in THEME_ETFS.items() if b in daily}, weeks)
    cs = core_state.reindex(core_state.index.union(weeks)).ffill().reindex(weeks).fillna(1.0)
    weekly = pd.DataFrame({t: s_.resample("W-FRI").last() for t, s_ in daily.items()}).reindex(weeks)
    sched = theme_schedule(weeks, ranks)
    th_of = {d: (sched.at[d, "lead"], sched.at[d, "rising"]) for d in weeks}
    base_t = {d: portfolio_targets(d, ranks, daily, float(cs.loc[d]), target, themes=th_of[d]) for d in weeks}
    trend_t = {d: portfolio_targets(d, ranks, daily, float(cs.loc[d]), target, trend=True, themes=th_of[d])
               for d in weeks}
    harvest_t = {d: with_cash_buffer(t, HARVEST["cash"]) for d, t in base_t.items()}
    runs = {"pf_harvest": (harvest_t, "harvest"), "pf_month": (base_t, "month"), "pf_week": (base_t, "week"),
            "pf_band": (base_t, "band"), "pf_none": (base_t, "none"), "pf_trend": (trend_t, "month"),
            "bm_soxl": ({d: {"SOXL": 1.0} for d in weeks}, "none"), "bm_spy": ({d: {"SPY": 1.0} for d in weeks}, "none")}
    curves, rows = {}, []
    for pid, (tg, pol) in runs.items():
        sim = simulate_portfolio(weekly, tg, pol, band=HARVEST["band"] if pol == "harvest" else 0.05)
        curves[pid] = sim["value"]
        rows.append({"id": pid, "선수": PORTFOLIO_PLAYERS[pid], **portfolio_stats(sim["value"], PORTFOLIO_LIVE_START),
                     "연 회전율(%)": round(float(sim["turnover"].sum() / max(len(sim) / 52, 1e-9) * 100), 0)})
    last = weeks[-1]
    lead, rising = th_of[last]
    raw = pick_themes(ranks["rank"].loc[last], ranks["rank_prev"].loc[last], ranks["above50"].loc[last])
    return {"table": pd.DataFrame(rows), "curves": pd.DataFrame(curves), "week": last,
            "base": base_t[last], "trend": trend_t[last], "harvest": harvest_t[last], "lead": lead, "rising": rising, "raw": raw,
            "core_on": float(cs.loc[last]),
            "vehicles": {th: theme_vehicle(th, daily, last) for th in (lead, rising) if th},
            "ranks": ranks["rank"].loc[last].dropna().sort_values(),
            "switches": int((sched["lead"] != sched["lead"].shift()).sum() + (sched["rising"] != sched["rising"].shift()).sum())}


def with_cash_buffer(target: dict[str, float], cash: float) -> dict[str, float]:
    """목표 비중에 현금 완충(예: 20%)을 넣음: 나머지 비중을 그만큼 줄이고 현금에 더함."""
    out = {k: v * (1 - cash) for k, v in target.items()}
    out["CASH"] = out.get("CASH", 0.0) + cash
    return out


def harvest_orders(holdings: pd.DataFrame, prices: dict[str, float], target: dict[str, float],
                   cash: float = 0.0, new_cash: float = 0.0, band: float | None = None) -> pd.DataFrame:
    """수익 실현형 리밸런싱 주문: 목표보다 band(상대) 넘게 커진 종목은 초과분 매도 → 현금,
    목표보다 band 넘게 작아진 종목(또는 아직 없는 목표 종목)은 현금+새 돈으로 목표까지 매수."""
    band = HARVEST["band"] if band is None else band
    cur = {r["ticker"]: float(r["shares"]) * prices.get(r["ticker"], np.nan) for _, r in holdings.iterrows()}
    cur = {k: v for k, v in cur.items() if pd.notna(v)}
    pool = cash + new_cash
    total = sum(cur.values()) + pool
    keys = sorted(set(cur) | {k for k in target if k != "CASH"})
    rows, sells = [], {}
    for k in keys:
        t_ = target.get(k, 0.0) * total
        v = cur.get(k, 0.0)
        if t_ == 0 and v > 0:
            sells[k] = 0.0          # 목표에 없는 종목은 자동으로 팔지 않음(장기 보유분) → 표시만
        elif v > t_ * (1 + band):
            sells[k] = v - t_
    pool += sum(sells.values())
    buys = {}
    need = {k: target.get(k, 0.0) * total - cur.get(k, 0.0) for k in keys
            if target.get(k, 0.0) > 0 and cur.get(k, 0.0) < target.get(k, 0.0) * total * (1 - band)}
    s_ = sum(need.values())
    scale = min(1.0, pool / s_) if s_ > 0 else 0.0
    for k, n in need.items():
        buys[k] = n * scale
    for k in keys:
        px = prices.get(k)
        v, tw = cur.get(k, 0.0), target.get(k, 0.0)
        amt = sells.get(k, 0.0) or buys.get(k, 0.0)
        if k in sells and sells[k] > 1:
            act, why = "매도", f"목표보다 {(v / (tw * total) - 1) * 100:.0f}% 커짐 → 초과분 현금화"
        elif k in buys and buys[k] > 1:
            act, why = "매수", "목표보다 많이 작아짐 → 현금으로 다시 채움" if v > 0 else "목표 종목 새로 담기"
        elif tw == 0 and v > 0:
            act, why = "유지", "목표에 없는 종목(장기 보유) · 자동 매도하지 않음"
        else:
            act, why = "유지", f"목표 ±{band * 100:.0f}% 안"
        rows.append({"종목": k, "지금 금액($)": round(v, 2), "지금 비중(%)": round(v / total * 100, 1) if total else 0,
                     "목표 비중(%)": round(tw * 100, 1), "주문": act if amt > 1 or act == "유지" else "유지",
                     "금액($)": round(amt, 2) if amt > 1 else np.nan,
                     "주식 수": round(amt / px, 4) if amt > 1 and px else np.nan, "이유": why})
    left = pool - sum(buys.values())
    rows.append({"종목": "현금", "지금 금액($)": round(cash + new_cash, 2),
                 "지금 비중(%)": round((cash + new_cash) / total * 100, 1) if total else 0,
                 "목표 비중(%)": round(target.get("CASH", 0.0) * 100, 1), "주문": "",
                 "금액($)": round(left, 2), "주식 수": np.nan, "이유": "주문 뒤 남는 현금"})
    return pd.DataFrame(rows)


PORTFOLIO_CHAMPION = "pf_harvest"
PORTFOLIO_PROMOTE = {"weeks": 52, "mdd_slack": 5.0}
PORTFOLIO_MODE = {"pf_harvest": "수익 실현형", "pf_month": "목표에 정확히", "pf_week": "목표에 정확히",
                  "pf_band": "목표에 정확히", "pf_none": "목표에 정확히", "pf_trend": "목표에 정확히"}
PORTFOLIO_TIMING = {"pf_harvest": "매주 확인: 목표보다 많이 커진 종목만 팔고, 많이 작아진 종목만 다시 삼",
                    "pf_month": "매달 첫 금요일에 목표 비중으로 맞춤 (그 사이엔 테마가 바뀐 칸만 교체)",
                    "pf_week": "매주 금요일 목표 비중으로 맞춤", "pf_band": "어느 종목이든 목표와 5%p 넘게 벌어지면 전체를 맞춤",
                    "pf_none": "테마가 바뀐 칸만 교체, 나머지는 그대로", "pf_trend": "매달 맞추되 200일선 아래 테마는 현금"}


def portfolio_verdicts(curves: pd.DataFrame, champion: str, live_start: str = PORTFOLIO_LIVE_START,
                       th: dict | None = None) -> dict[str, tuple[str, str]]:
    """리밸런싱 방식 교체 판정: 실전 기간(live_start 이후)만으로 챔피언과 비교.
    52주 이상 + 칼마가 더 높고 + 최대 낙폭이 5%p 넘게 나쁘지 않으면 '교체 추천'."""
    th = {**PORTFOLIO_PROMOTE, **(th or {})}
    live = curves[curves.index >= pd.Timestamp(live_start)]
    out = {}
    if champion not in live.columns:
        return out
    weeks = len(live) - 1

    def stat(v):
        v = v.dropna()
        if len(v) < 3:
            return np.nan, np.nan, np.nan
        yrs = (len(v) - 1) / 52
        cagr = (v.iloc[-1] / v.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan
        mdd = float((v / v.cummax() - 1).min() * 100)
        cal = cagr * 100 / abs(mdd) if mdd < 0 else (np.inf if cagr > 0 else np.nan)
        return cagr * 100, mdd, cal

    c_cagr, c_mdd, c_cal = stat(live[champion])
    for pid in live.columns:
        if pid == champion or pid.startswith("bm_"):
            continue
        if weeks < th["weeks"]:
            out[pid] = ("short", f"실전 {max(weeks, 0)}주 / 필요 {th['weeks']}주")
            continue
        cagr, mdd, cal = stat(live[pid])
        detail = f"실전 칼마 {cal:.2f} vs {c_cal:.2f} · 낙폭 {mdd:.1f}% vs {c_mdd:.1f}%"
        better = (cal > c_cal) or (np.isinf(cal) and np.isinf(c_cal) and cagr > c_cagr)
        if pd.notna(cal) and pd.notna(c_cal) and better and mdd >= c_mdd - th["mdd_slack"]:
            out[pid] = ("promote", "교체 추천 · " + detail)
        else:
            out[pid] = ("hold", "챔피언 유지 · " + detail)
    return out


def rebalance_orders(holdings: pd.DataFrame, prices: dict[str, float], target: dict[str, float],
                     new_cash: float = 0.0, buy_only: bool = False) -> pd.DataFrame:
    """현재 보유(ticker, shares) + 새로 넣을 돈 → 목표 비중에 맞추는 주문(소수점 주 허용).
    buy_only면 팔지 않고 새 돈으로 모자란 종목만 채움(세금이 생기는 매도 피하기)."""
    cur = {r["ticker"]: float(r["shares"]) * prices.get(r["ticker"], np.nan) for _, r in holdings.iterrows()}
    cur = {k: v for k, v in cur.items() if pd.notna(v)}
    total = sum(cur.values()) + new_cash
    keys = sorted(set(cur) | {k for k in target if k != "CASH"})
    want = {k: target.get(k, 0.0) * total for k in keys}
    diff = {k: want[k] - cur.get(k, 0.0) for k in keys}
    if buy_only:
        need = {k: max(d, 0.0) for k, d in diff.items()}
        s_ = sum(need.values())
        scale = min(1.0, new_cash / s_) if s_ > 0 else 0.0
        diff = {k: need[k] * scale for k in keys}
    rows = []
    for k in keys:
        px = prices.get(k)
        if px is None or not np.isfinite(px) or abs(diff[k]) < 1:
            act = "유지"
        else:
            act = "매수" if diff[k] > 0 else "매도"
        rows.append({"종목": k, "지금 금액($)": round(cur.get(k, 0.0), 2), "지금 비중(%)": round(cur.get(k, 0.0) / total * 100, 1) if total else 0,
                     "목표 비중(%)": round(target.get(k, 0.0) * 100, 1), "주문": act,
                     "금액($)": round(abs(diff[k]), 2), "주식 수": round(abs(diff[k]) / px, 4) if px else np.nan})
    cash_t = target.get("CASH", 0.0)
    rows.append({"종목": "현금", "지금 금액($)": round(new_cash, 2), "지금 비중(%)": round(new_cash / total * 100, 1) if total else 0,
                 "목표 비중(%)": round(cash_t * 100, 1), "주문": "", "금액($)": np.nan, "주식 수": np.nan})
    return pd.DataFrame(rows)


# ==========================================================================
# 20. 나의 투자 원칙 · 위험 관리 · 세금 장부 · 무한매수 사이클
# ==========================================================================
SETTINGS_KEY_PLAN = "plan"
INSIGHT_TABLE = "valhalla_insights"
DEFAULT_PRINCIPLES = {
    "buy_target_krw": 250_000, "buy_max_krw": 300_000,     # 사용자 원칙: 무한매수 1회(하루) 매수 25만 원, 최대 30만 원
    "extra": [],                                            # 사용자가 나중에 추가하는 원칙(문장)
}
TAX = {"deduction_krw": 2_500_000, "rate": 0.22}
CYCLE_METHODS = {"①": ("내 방식 40분할 +10% 전량 익절", 40, 0.10, False),
                 "②": ("V2형 별지점 1/4 분할 익절", 40, 0.10, True),
                 "④": ("V2형 + 쿼터 손절 + 200일선", 40, 0.10, True),
                 "기타": ("직접 관리", 40, 0.10, False)}


def daily_buy_amounts(journal: pd.DataFrame, fx: float) -> pd.DataFrame:
    """계좌·날짜별 매수 합계(원). 무한매수 1회 = 하루 매수로 봄."""
    if journal is None or journal.empty:
        return pd.DataFrame(columns=["Account", "Date", "Ticker", "usd", "krw"])
    b = journal[journal["Action"] == "Buy"].copy()
    b["usd"] = b["Qty"].astype(float) * b["Price"].astype(float)
    g = b.groupby(["Account", "Date", "Ticker"], as_index=False)["usd"].sum()
    g["krw"] = (g["usd"] * fx).round(0)
    return g.sort_values(["Date", "Account"])


def check_buy_principle(amounts: pd.DataFrame, target: float, hard: float) -> dict:
    """원칙 점검: 하루 매수가 최대치를 넘은 날, 목표를 넘은 날."""
    if amounts is None or amounts.empty:
        return {"days": 0, "over_hard": pd.DataFrame(), "over_target": 0, "share_ok": np.nan}
    over_h = amounts[amounts["krw"] > hard]
    over_t = amounts[(amounts["krw"] > target) & (amounts["krw"] <= hard)]
    return {"days": len(amounts), "over_hard": over_h, "over_target": len(over_t),
            "share_ok": round(float((amounts["krw"] <= hard).mean() * 100), 1)}


def buy_qty_for_budget(price_usd: float, fx: float, target: float, hard: float) -> dict:
    """원칙 금액 안에서 살 수 있는 주식 수(정수, 키움 LOC 기준)."""
    if not price_usd or price_usd <= 0:
        return {"qty": 0, "krw": 0}
    per = price_usd * fx
    q = int(target // per)
    if q == 0 and per <= hard:
        q = 1
    return {"qty": q, "krw": round(q * per), "per_share_krw": round(per)}


def leverage_of(ticker: str) -> int:
    cat = category_of(ticker) if ticker in SIGNAL_UNIVERSE else None
    if cat == "3배 ETF":
        return 3
    if cat == "2배 ETF":
        return 2
    for _, levs in THEME_ETFS.values():
        for t, k in levs:
            if t == ticker:
                return k
    return 1


SEMI_TICKERS = {"SOXL", "USD", "SOXX", "NVDA", "AVGO", "MU", "MRVL", "AMD", "TSM", "SMH", "SKHY"}


def exposure_table(rows: list[dict]) -> pd.DataFrame:
    """rows = [{account, ticker, qty, price}] → 금액·레버리지·반도체 여부."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["value"] = df["qty"].astype(float) * df["price"].astype(float)
    df["lev"] = df["ticker"].map(leverage_of)
    df["semi"] = df["ticker"].isin(SEMI_TICKERS)
    return df


def stress_test(exp: pd.DataFrame, shocks=(-0.10, -0.20, -0.30), cash: float = 0.0) -> pd.DataFrame:
    """기초자산이 하루에 shock만큼 빠질 때 예상 손실(레버리지 배수 × 하락, -100% 한도). 현금은 그대로."""
    total = float(exp["value"].sum()) + cash if not exp.empty else cash
    rows = []
    for sc in shocks:
        for name, mask in (("반도체만", exp["semi"]), ("시장 전체", pd.Series(True, index=exp.index))):
            loss = float((exp.loc[mask, "value"] * (exp.loc[mask, "lev"] * sc).clip(lower=-1.0)).sum())
            rows.append({"시나리오": f"{name} {sc * 100:.0f}%", "예상 손실($)": round(loss, 0),
                         "전체 자산 대비(%)": round(loss / total * 100, 1) if total else np.nan})
    return pd.DataFrame(rows)


def realized_by_year(journal: pd.DataFrame, year: int) -> pd.DataFrame:
    """계좌별 그해 실현 손익(USD, 이동평균 원가법). 일지에 빠진 매수가 있으면 과소·과대 계산될 수 있음."""
    rows = []
    if journal is None or journal.empty:
        return pd.DataFrame(columns=["Account", "Ticker", "realized_usd", "sells"])
    for (acc, tk), g in journal.groupby(["Account", "Ticker"]):
        qty = cost = 0.0
        real, n = 0.0, 0
        for r in sort_trades(g).itertuples(index=False):
            q, pr = float(r.Qty), float(r.Price)
            if not (q > 0 and pr > 0):
                continue
            if r.Action == "Buy":
                qty += q
                cost += q * pr
            elif r.Action == "Sell" and qty > 1e-12:
                sq = min(q, qty)
                avg = cost / qty
                if str(r.Date)[:4] == str(year):
                    real += (pr - avg) * sq
                    n += 1
                cost -= avg * sq
                qty -= sq
                if qty <= 1e-9:
                    qty = cost = 0.0
        rows.append({"Account": acc, "Ticker": tk, "realized_usd": round(real, 2), "sells": n})
    return pd.DataFrame(rows)


def harvest_plan(realized_krw: float, accounts: pd.DataFrame, price: float, fx: float) -> dict:
    """올해 과세 대상 이익을 0으로 만들려면 몇 주를 팔아야 하나 (같은 날 재매수 전제).
    accounts = [Account, Qty, Avg]. 손실이 큰 계좌부터 채움."""
    taxable = max(0.0, realized_krw - TAX["deduction_krw"])
    out = {"realized_krw": realized_krw, "taxable_krw": taxable, "tax_krw": round(taxable * TAX["rate"]),
           "rows": pd.DataFrame(), "shares": 0, "covered_krw": 0.0}
    if taxable <= 0 or accounts is None or accounts.empty or not price:
        return out
    acc = accounts.copy()
    acc["loss_per_share_krw"] = (acc["Avg"].astype(float) - price) * fx
    acc = acc[acc["loss_per_share_krw"] > 0].sort_values("loss_per_share_krw", ascending=False)
    need, rows = taxable, []
    for _, r in acc.iterrows():
        if need <= 0:
            break
        per = float(r["loss_per_share_krw"])
        q = int(min(float(r["Qty"]), np.ceil(need / per)))
        if q <= 0:
            continue
        rows.append({"계좌": r["Account"], "팔 주식 수": q, "주당 손실(원)": round(per), "실현 손실(원)": round(q * per)})
        need -= q * per
    df = pd.DataFrame(rows)
    out.update({"rows": df, "shares": int(df["팔 주식 수"].sum()) if not df.empty else 0,
                "covered_krw": float(df["실현 손실(원)"].sum()) if not df.empty else 0.0})
    return out


def cycle_status(qty: float, avg: float, t: float, price: float, method: str = "①", budget_krw: float = 0,
                 unit_krw: float = 250_000, fx: float = 1350.0) -> dict:
    """무한매수 사이클 상태: 회차, 목표가(①=평단+10%, V2형=별지점), 손익, 남은 예산."""
    _, splits, target, star = CYCLE_METHODS.get(method, CYCLE_METHODS["기타"])
    sell_px = avg * (1 + target) if avg else np.nan
    star_px = avg * (1 + target - t / 2 / 100 * (40 / splits)) if (star and avg) else np.nan
    invested_krw = qty * avg * fx
    return {"회차": round(t, 1), "진행률(%)": round(t / splits * 100, 0), "평단($)": round(avg, 2),
            "목표가($)": round(sell_px, 2) if pd.notna(sell_px) else np.nan,
            "별지점($)": round(star_px, 2) if pd.notna(star_px) else np.nan,
            "현재 손익(%)": round((price / avg - 1) * 100, 1) if avg else np.nan,
            "투입(원)": round(invested_krw), "남은 예산(원)": round(max(budget_krw - invested_krw, 0)) if budget_krw else np.nan,
            "남은 회차": int(max(budget_krw - invested_krw, 0) // unit_krw) if budget_krw and unit_krw else np.nan}


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


# ==========================================================================
# 21. 내 상황 시나리오 관제 (자동 시나리오 판정 · 이벤트 관문 · 계좌별 대응)
#   시나리오: A 추가 하락 / B 박스권 / C 매물대 돌파 / D 강한 회복
#   이벤트 관문: 큰 발표 전에는 새 매수·매도를 미루고, 발표가 지나고 시장이 소화한 뒤 대응
#   예측 도구가 아니라 '미리 정한 대응을 지키게 하는' 도구. 원칙 금액을 넘는 제안은 만들지 않음.
# ==========================================================================
SCENARIO_KEY = "scenario"                 # plan 설정 안의 하위 키
SCENARIO_DEFAULTS = {
    "exit_target": 102.0,                 # 탈출 목표가 (올해 이익만큼만 손해 보고 나오는 가격)
    "tranches": [70.0, 85.0, 102.0],      # D 시나리오 단계적 탈출 가격 (각 1/3)
    "resistance": None,                   # 매물대 직접 입력 (None = 차트에서 자동 계산)
    "cash_per_account_krw": 500_000,      # 키움 계좌당 예수금
    "rebuy": "미정",                      # 12월 다시 사기: 미정 / 전부 / 절반 / 비율 직접
    "loss_krw": 0,                        # 12월에 손절(손실 실현)할 금액(원) → 팔 주식 수 계산
    "rebuy_pct": 70.0,                    # 비율 직접: 다시 살 비율(%)
    "rebought": False,                    # 12월 다시 사기 완료 여부
    "rebuy_price": None,                  # 다시 산 가격
    "dip_steps": [0.15, 0.30],            # 총알을 쓰는 하락폭 (다시 산 가격 또는 지금 가격 기준)
    "take_profit": 0.10,                  # 새 사이클 익절 (기법 ① = 평단 +10%)
    "wait_before_h": 24,                  # 큰 발표 몇 시간 전부터 보류
    "settle_after_h": 12,                 # 발표 뒤 몇 시간은 소화 시간 (다음 장 종가 확인)
}
REBUY_MODES = ["미정", "전부", "절반", "비율 직접"]


def shares_for_loss(loss_krw: float, accounts: pd.DataFrame, price: float, fx: float) -> dict:
    """손절할 금액(원) → 팔 주식 수. 1주당 손실이 큰 계좌부터, 필요한 만큼만. accounts = [Account, Qty, Avg]."""
    if not loss_krw or accounts is None or accounts.empty or not price:
        return {"shares": 0, "covered_krw": 0.0, "rows": pd.DataFrame()}
    hp = harvest_plan(float(loss_krw) + TAX["deduction_krw"], accounts, price, fx)
    return {"shares": hp["shares"], "covered_krw": hp["covered_krw"], "rows": hp["rows"]}


def allocate_cash_out(qtys: list[float], total_krw: float, price: float, fx: float) -> list[int]:
    """총 얼마치를 현금으로 남길지 → 계좌별로 다시 사지 않을 주식 수.
    보유 수량 비율로 나누고, 반올림 차이는 남는 몫이 큰 계좌부터 채움(최대한 목표 금액에 가깝게)."""
    q = [int(np.floor(x or 0)) for x in qtys]
    per = price * fx
    if per <= 0 or total_krw <= 0 or sum(q) == 0:
        return [0] * len(q)
    need = min(int(round(total_krw / per)), sum(q))
    raw = [need * x / sum(q) for x in q]
    out = [min(int(np.floor(r)), x) for r, x in zip(raw, q)]
    for i in sorted(range(len(q)), key=lambda i: -(raw[i] - out[i])):
        if sum(out) >= need:
            break
        if out[i] < q[i]:
            out[i] += 1
    return out


def rebuy_keep(qty: float, mode: str, s: dict, cash_krw: float, price: float, fx: float) -> int:
    """12월에 판 뒤 다시 살 주식 수. 손실 실현(세금 상쇄)은 다시 사는 양과 상관없이 판 순간 확정."""
    qty = int(np.floor(qty))
    if mode == "절반":
        return qty // 2
    if mode == "비율 직접":
        return int(np.floor(qty * min(max(float(s.get("rebuy_pct") or 0), 0), 100) / 100))
    return qty


def rebuy_label(mode: str, s: dict) -> str:
    if mode == "비율 직접":
        return f"{float(s.get('rebuy_pct') or 0):g}%"
    return mode


SCENARIO_NAMES = {"A": "A · 추가 하락", "B": "B · 박스권", "C": "C · 매물대 돌파", "D": "D · 강한 회복"}
BIG_EVENT_WORDS = ("FOMC", "PCE", "CPI", "고용", "실적", "청문회", "GDP")


def scenario_settings(plan: dict) -> dict:
    return {**SCENARIO_DEFAULTS, **((plan or {}).get(SCENARIO_KEY) or {})}


def volume_resistance(df: pd.DataFrame, price: float, lookback: int = 250, step_pct: float = 0.005,
                      smooth_pct: float = 0.03, band_share: float = 0.7) -> dict:
    """가격대별 거래량(매물대)을 띠로.
    - 칸 너비 = 지금 가격의 0.5% (예: $33이면 약 $0.17)
    - 하루 거래량을 그날 저가~고가에 고르게 나눠 담음(종가 한 점보다 실제에 가까움)
    - 약 3% 폭으로 부드럽게 한 뒤, 지금 가격 위에서 가장 두꺼운 곳 = 저항 중심
    - 중심 거래량의 70% 이상인 이어진 구간 = 저항 띠 (아래쪽도 같은 방식으로 지지 띠)"""
    nan = {"resistance": np.nan, "res_low": np.nan, "res_high": np.nan, "support": np.nan, "sup_low": np.nan, "sup_high": np.nan}
    d = df.dropna(subset=["Close"]).tail(lookback)
    if d.empty or "Volume" not in d or not price:
        return nan
    hi = d["High"].fillna(d["Close"]).to_numpy(float)
    lo = d["Low"].fillna(d["Close"]).to_numpy(float)
    vol = d["Volume"].fillna(0).to_numpy(float)
    step = max(price * step_pct, 0.01)
    base = float(lo.min())
    n = int(np.ceil((float(hi.max()) - base) / step)) + 1
    prof = np.zeros(n)
    for h, l, v in zip(hi, lo, vol):
        i0, i1 = int((l - base) // step), int((h - base) // step)
        prof[i0:i1 + 1] += v / (i1 - i0 + 1)
    k = max(1, int(round(price * smooth_pct / step)))
    sm = np.convolve(prof, np.ones(k) / k, mode="same")
    mids = base + (np.arange(n) + 0.5) * step

    def band(mask):
        if not mask.any() or sm[mask].max() <= 0:
            return np.nan, np.nan, np.nan
        idx = np.where(mask)[0]
        c = int(idx[np.argmax(sm[idx])])
        thr = sm[c] * band_share
        l_, r_ = c, c
        while l_ - 1 >= 0 and mask[l_ - 1] and sm[l_ - 1] >= thr:
            l_ -= 1
        while r_ + 1 < n and mask[r_ + 1] and sm[r_ + 1] >= thr:
            r_ += 1
        return round(float(mids[c]), 2), round(float(mids[l_] - step / 2), 2), round(float(mids[r_] + step / 2), 2)

    rc, rl, rh = band(mids > price * 1.03)
    sc, sl, sh = band(mids < price * 0.97)
    return {"resistance": rc, "res_low": rl, "res_high": rh, "support": sc, "sup_low": sl, "sup_high": sh}


def is_big_event(name: str) -> bool:
    return any(w in name for w in BIG_EVENT_WORDS)


def event_gate(now: pd.Timestamp, before_h: float = 24, after_h: float = 12, calendar=None) -> dict:
    """큰 발표 기준 지금 상태.
    wait   = 발표 before_h 시간 전 ~ 발표 시각: 새 매수·매도 보류
    settle = 발표 ~ 발표+after_h: 시장이 소화 중, 다음 장 종가로 확인
    after  = 발표가 지나고 소화도 끝남(3일 안): 계획대로 대응
    clear  = 가까운 큰 발표 없음"""
    cal = calendar if calendar is not None else EVENT_CALENDAR
    evs = sorted(((pd.Timestamp(w), n, note, ok) for w, n, note, ok in cal if is_big_event(n)), key=lambda x: x[0])
    nxt = next(((t, n) for t, n, _, _ in evs if t > now), None)
    for t, n, _, ok in evs:
        if t - pd.Timedelta(hours=before_h) <= now < t:
            return {"state": "wait", "event": n, "when": t, "until": t + pd.Timedelta(hours=after_h), "confirmed": ok, "next": nxt}
        if t <= now < t + pd.Timedelta(hours=after_h):
            return {"state": "settle", "event": n, "when": t, "until": t + pd.Timedelta(hours=after_h), "confirmed": ok, "next": nxt}
    past = [(t, n, ok) for t, n, _, ok in evs if t + pd.Timedelta(hours=after_h) <= now <= t + pd.Timedelta(days=3)]
    if past:
        t, n, ok = past[-1]
        return {"state": "after", "event": n, "when": t, "until": None, "confirmed": ok, "next": nxt}
    return {"state": "clear", "event": None, "when": None, "until": None, "confirmed": True, "next": nxt}


GATE_TEXT = {
    "wait": "큰 발표 전이에요. 새 매수·매도는 발표 뒤로 미뤄요. 이미 걸어 둔 익절 지정가는 그대로 두세요.",
    "settle": "발표 직후라 시장이 결과를 소화하는 중이에요. 다음 장 종가를 보고 움직여요.",
    "after": "발표가 지나갔어요. 이제 아래 시나리오 대응을 실행해도 돼요.",
    "clear": "가까운 큰 발표가 없어요. 시나리오 대응대로 하면 돼요.",
}


def scenario_checks(price: float, soxl_low: float, sox: float, sox_line: float, resistance: float,
                    week_close: float, v1_on: bool | None, rebound_outcome, tranche1: float,
                    res_low: float | None = None) -> list[dict]:
    """시나리오를 가르는 체크 항목(참/거짓)."""
    def row(name, ok, value, meaning):
        return {"항목": name, "충족": None if ok is None else bool(ok), "지금": value, "뜻": meaning}
    out = [
        row("SOXL 최근 저점 유지", None if pd.isna(soxl_low) else price >= soxl_low,
            f"${price:.2f} / 저점 ${soxl_low:.2f}" if pd.notna(soxl_low) else "—", "깨지면 A(추가 하락)"),
        row("SOX 반등 기준선 유지", None if (pd.isna(sox) or pd.isna(sox_line)) else sox >= sox_line,
            f"{sox:,.0f} / 기준 {sox_line:,.0f}" if pd.notna(sox) and pd.notna(sox_line) else "—", "깨지면 '일시 반등' 확정 → A"),
        row("매물대 띠 진입", None if res_low is None or pd.isna(res_low) else price >= res_low,
            f"${price:.2f} / 띠 아래쪽 ${res_low:.2f}" if res_low is not None and pd.notna(res_low) else "—", "들어가면 물량 구간 · 추격 매수 금지"),
        row("매물대 띠 위 (오늘 종가)", None if pd.isna(resistance) else price > resistance,
            f"${price:.2f} / 띠 위쪽 ${resistance:.2f}" if pd.notna(resistance) else "—", "넘으면 C 후보"),
        row("매물대 띠 위 (주간 종가 확인)", None if (pd.isna(resistance) or pd.isna(week_close)) else week_close > resistance,
            f"주간 ${week_close:.2f}" if pd.notna(week_close) else "—", "주간 종가로 넘어야 C 확정"),
        row("규칙 v1 매수 켜짐", v1_on, "켜짐" if v1_on else ("꺼짐" if v1_on is not None else "—"), "켜지면 C·D 신뢰도 ↑"),
        row(f"1차 탈출가 ${tranche1:.0f} 도달", price >= tranche1, f"${price:.2f}", "넘으면 D(강한 회복)"),
    ]
    if rebound_outcome is not None:
        out.append(row("반등 판별전 결과", bool(rebound_outcome), "추세 전환" if rebound_outcome else "일시 반등", "참고"))
    return out


def pick_scenario(price: float, soxl_low: float, sox: float, sox_line: float, resistance: float,
                  week_close: float, tranche1: float, res_low: float | None = None) -> tuple[str, str]:
    """resistance = 매물대 띠의 위쪽(돌파 기준), res_low = 띠의 아래쪽(물량이 나오기 시작하는 곳)."""
    rl = resistance if res_low is None or pd.isna(res_low) else res_low
    if pd.notna(soxl_low) and price < soxl_low:
        return "A", f"SOXL이 최근 저점 ${soxl_low:.2f} 아래로 내려갔어요."
    if pd.notna(sox) and pd.notna(sox_line) and sox < sox_line:
        return "A", f"SOX가 반등 기준선 {sox_line:,.0f} 아래예요 (일시 반등 확정)."
    if price >= tranche1:
        return "D", f"1차 탈출가 ${tranche1:.0f} 위예요."
    if pd.notna(resistance) and pd.notna(week_close) and week_close > resistance:
        return "C", f"주간 종가 ${week_close:.2f}가 매물대 띠 위쪽 ${resistance:.2f}를 넘어 안착했어요."
    if pd.notna(resistance) and price > resistance:
        return "B", f"오늘은 매물대 띠 위쪽 ${resistance:.2f}를 넘었지만 주간 종가 확인 전이라 아직 B예요 (C 후보)."
    if pd.notna(rl) and price >= rl:
        return "B", f"매물대 띠(${rl:.2f}~{resistance:.2f}) 안이에요. 물량이 나오는 구간이라 추격 매수 금지 (B 상단)."
    lo = f"${soxl_low:.2f}" if pd.notna(soxl_low) else "저점"
    hi = f"${rl:.2f}~{resistance:.2f}" if pd.notna(resistance) else "매물대"
    return "B", f"저점 {lo}과 매물대 띠 {hi} 사이 박스권이에요."


def scenario_accounts(cycles: pd.DataFrame, s: dict, price: float, fx: float, unit_krw: float) -> pd.DataFrame:
    """계좌별 남은 총알·다음 매수가·매도 지정가. 12월 다시 사기 전이면 '전부/절반' 두 경우를 나란히."""
    if cycles is None or cycles.empty or not price:
        return pd.DataFrame()
    cash = float(s["cash_per_account_krw"] or 0)
    ref = float(s["rebuy_price"] or price) if s.get("rebought") else price
    modes = [s["rebuy"]] if s.get("rebuy") in REBUY_MODES[1:] else ["전부", "절반", "비율 직접"]
    rows = []
    for j, (_, r) in enumerate(cycles.iterrows()):
        qty, avg = float(r.get("보유 수량") or 0), float(r.get("평단($)") or 0)
        for m in modes:
            if s.get("rebought"):
                keep = qty
            else:
                keep = rebuy_keep(qty, m, s, cash, ref, fx)
            c = cash if s.get("rebought") else cash + (np.floor(qty) - keep) * ref * fx
            bullets = int(c // unit_krw) if unit_krw else 0
            new_avg = ref
            dips = [round(ref * (1 - d), 2) for d in s["dip_steps"]]
            rows.append({"계좌": r.get("계좌"), "다시 사기": rebuy_label(m, s), "보유(주)": int(keep),
                         "현금화(주)": 0 if s.get("rebought") else int(np.floor(qty) - keep), "새 평단($)": round(new_avg, 2),
                         "예수금(원)": round(c), "남은 총알(발)": bullets,
                         "다음 매수가($)": ", ".join(f"{x:.2f}" for x in dips[:max(1, min(len(dips), bullets))]) if bullets else "총알 없음",
                         "익절 지정가($)": round(new_avg * (1 + float(s["take_profit"])), 2),
                         "지금 평가(원)": round(keep * price * fx),
                         "옛 평단($)": round(avg, 2)})
    return pd.DataFrame(rows)


def scenario_actions(code: str, gate: str, s: dict, price: float, resistance: float, unit_krw: float,
                     max_krw: float, fx: float) -> list[str]:
    """시나리오 × 이벤트 관문 → 오늘 할 일 문장. 원칙 금액을 넘는 제안은 없음."""
    q = buy_qty_for_budget(price, fx, unit_krw, max_krw)
    shot = f"1발 = {q['qty']}주(≈{q['krw']:,}원)"
    pre = [] if s.get("rebought") else ["12월 손실 실현 전이에요. 팔고 같은 날 다시 사는 계획은 시나리오와 상관없이 그대로 진행해요."]
    tp = float(s["take_profit"]) * 100
    acts = {
        "A": [f"매일 사지 않아요. 총알은 다시 산 가격에서 -{s['dip_steps'][0]*100:.0f}%, -{s['dip_steps'][-1]*100:.0f}%에만 써요 ({shot}).",
              "원칙 금액을 넘겨 '물타기'하지 않아요. 현금을 남기는 게 목표예요.",
              "더 낮은 가격에서 12월에 다시 사면 새 평단이 더 낮아져요."],
        "B": [f"새 사이클은 평단 +{tp:.0f}% 익절 지정가를 걸어 두고, 체결되면 그 현금으로 25만 원짜리 분할 사이클을 다시 돌려요.",
              f"총알은 지정한 하락 가격에서만 써요 ({shot}). 박스권에서 쌓는 익절이 회복의 주 엔진이에요.",
              f"매물대 띠(위쪽 ${resistance:.2f}) 안에서는 한 번 밀리기 쉬워요. 거기서 추격 매수하지 않아요." if pd.notna(resistance)
              else "매물대 근처에선 추격 매수하지 않아요."],
        "C": ["주간 종가로 매물대를 넘었어요. 새 사이클 물량은 익절 규칙대로 팔아요.",
              "판 돈 일부는 규칙 v1 계좌로 옮겨 추세에 태워요 (v1이 켜져 있을 때).",
              "다시 매물대 띠 위쪽 아래로 주간 마감하면 B로 돌아가요."],
        "D": [f"단계적 탈출: ${s['tranches'][0]:.0f}에서 1/3, ${s['tranches'][1]:.0f}에서 1/3, ${s['tranches'][2]:.0f}에서 나머지.",
              "규칙 v1이 켜져 있으면 마지막 1/3은 v1 매도 신호까지 들고 가요.",
              "한 번에 다 팔지도, 다 들고 있지도 않아요."],
    }[code]
    if gate in ("wait", "settle"):
        return pre + [GATE_TEXT[gate], "발표가 지나면 아래가 할 일이에요: " + acts[0]]
    return pre + acts


def grade_scenarios(calls: pd.Series, close: pd.Series, horizon: int = 20) -> pd.DataFrame:
    """지난 판정 채점: horizon 거래일 뒤 수익률로 맞았는지. A=하락, B=±20% 안, C·D=상승."""
    close = close.dropna()
    rows = []
    for day, code in calls.dropna().items():
        day = pd.Timestamp(day)
        after = close[close.index >= day]
        if len(after) <= horizon:
            rows.append({"날짜": day, "판정": code, f"{horizon}일 뒤(%)": np.nan, "맞음": None})
            continue
        r = float((after.iloc[horizon] / after.iloc[0] - 1) * 100)
        ok = r < 0 if code == "A" else (abs(r) <= 20 if code == "B" else r > 0)
        rows.append({"날짜": day, "판정": code, f"{horizon}일 뒤(%)": round(r, 1), "맞음": ok})
    return pd.DataFrame(rows)


def scenario_snapshot(soxl: pd.DataFrame, sox_close: float, sox_line: float, s: dict, v1_on, rebound_outcome,
                      now: pd.Timestamp) -> dict:
    """앱과 자동 점검이 함께 쓰는 판정 묶음."""
    d = soxl.dropna(subset=["Close"])
    price = float(d["Close"].iloc[-1])
    prev = d.iloc[:-1]                      # 오늘 장중 저점은 빼고 '직전까지의' 저점과 비교 (오늘 종가가 그 아래면 A)
    low = float(prev["Low"].tail(60).min()) if "Low" in prev else float(prev["Close"].tail(60).min())
    vr = volume_resistance(d, price)
    if s.get("resistance"):                      # 직접 입력 = 띠 아래쪽, 위쪽은 +3%
        r_lo = float(s["resistance"])
        r_hi = float(s.get("resistance_high") or r_lo * 1.03)
    else:
        r_lo, r_hi = vr["res_low"], vr["res_high"]
    res = r_hi
    wk = d["Close"].resample("W-FRI").last().dropna()
    last_fri = _friday(d.index[-1]) if d.index[-1].weekday() == 4 else _friday(d.index[-1]) - pd.Timedelta(weeks=1)
    wk = wk[wk.index <= last_fri]
    week_close = float(wk.iloc[-1]) if len(wk) else np.nan
    t1 = float(s["tranches"][0])
    code, why = pick_scenario(price, low, sox_close, sox_line, res, week_close, t1, r_lo)
    gate = event_gate(now, float(s["wait_before_h"]), float(s["settle_after_h"]))
    return {"price": price, "low": low, "resistance": res, "res_low": r_lo, "res_peak": vr["resistance"],
            "resistance_auto": vr["resistance"], "auto_band": (vr["res_low"], vr["res_high"]),
            "support": vr["support"], "sup_band": (vr["sup_low"], vr["sup_high"]),
            "week_close": week_close, "sox": sox_close, "sox_line": sox_line, "code": code, "why": why, "gate": gate,
            "checks": scenario_checks(price, low, sox_close, sox_line, res, week_close, v1_on, rebound_outcome, t1, r_lo),
            "exit_gap": (float(s["exit_target"]) / price - 1) * 100}


# ==========================================================================
# 22. 가격 확인 신호등 (기한 있는 점검표) · ETF 발행 주식 수 추적
# ==========================================================================
PRICE_CHECKS_KEY = "price_checks"
DEFAULT_PRICE_CHECKS = [
    {"name": "1차 확인 · 하락 종료 1차 증거", "kind": "above", "price": 36.0, "start": "2026-11-30", "end": "2026-12-02",
     "note": "종가 $36~38 위 마감 (미달이면 횡보로 시간 지연)"},
    {"name": "2차 확인 · 하락 추세 완결", "kind": "above", "price": 45.0, "start": "2026-11-27", "end": "2026-12-11",
     "note": "종가 $45 위 돌파 (4차 반등 고점)"},
    {"name": "무효화 · 바닥 시나리오 폐기", "kind": "never_below", "price": 25.0, "start": "2026-11-26", "end": "",
     "note": "종가 $25 아래면 바닥 시나리오 폐기 (기한 없음)"},
]
SHARES_TABLE = "valhalla_etf_shares"


def evaluate_price_checks(close: pd.Series, checks: list[dict], today: pd.Timestamp) -> pd.DataFrame:
    """기한 있는 가격 조건을 종가로 판정.
    above: 기간 안에 종가가 price 이상인 날이 있으면 ✅, 기한 지나도록 없으면 ❌, 진행 중이면 ⏳, 시작 전이면 📅.
    never_below: 시작일 뒤로 종가가 price 아래인 날이 한 번이라도 있으면 ❌, 아니면 ✅(유지 중)."""
    c = close.dropna()
    today = pd.Timestamp(today).normalize()
    last = float(c.iloc[-1]) if len(c) else np.nan
    rows = []
    for ck in checks:
        p = float(ck["price"])
        a = pd.Timestamp(ck["start"]) if ck.get("start") else c.index.min()
        b = pd.Timestamp(ck["end"]) if ck.get("end") else None
        win = c[(c.index >= a) & ((c.index <= b) if b is not None else True)]
        if ck["kind"] == "never_below":
            broke = win[win < p]
            status = f"❌ 깨짐 ({broke.index[0]:%m/%d} ${broke.iloc[0]:.2f})" if len(broke) else "✅ 유지 중"
            best = f"최저 종가 ${win.min():.2f}" if len(win) else "—"
        else:
            hit = win[win >= p]
            if len(hit):
                status = f"✅ 달성 ({hit.index[0]:%m/%d} ${hit.iloc[0]:.2f})"
            elif today < a:
                status = "📅 시작 전"
            elif b is not None and today > b:
                status = "❌ 기한 내 미달"
            else:
                status = "⏳ 진행 중"
            best = f"기간 최고 종가 ${win.max():.2f}" if len(win) else "—"
        left = (b - today).days if b is not None else None
        rows.append({"점검": ck["name"], "조건": ("종가 ≥ " if ck["kind"] == "above" else "종가가 계속 ≥ ") + f"${p:g}",
                     "기간": f"{a:%m/%d}~" + (f"{b:%m/%d}" if b is not None else "기한 없음"),
                     "남은 날": ("기한 지남" if left is not None and left < 0 else (f"D-{left}" if left is not None else "—")),
                     "상태": status, "지금": f"${last:.2f} ({(last / p - 1) * 100:+.0f}%)" if pd.notna(last) else "—",
                     "기록": best, "설명": ck.get("note", "")})
    return pd.DataFrame(rows)


def shares_trend(rows: pd.DataFrame) -> dict:
    """발행 주식 수 기록 → 최근 값, 전일·5일·기록 시작 대비 변화, 최근 고점 대비."""
    if rows is None or rows.empty:
        return {}
    s = rows.sort_values("day").set_index("day")["shares"].astype(float)
    s.index = pd.to_datetime(s.index)
    out = {"latest": float(s.iloc[-1]), "day": s.index[-1], "n": len(s), "series": s}
    for k, n in (("d1", 1), ("d5", 5)):
        out[k] = float(s.iloc[-1] / s.iloc[-1 - n] - 1) * 100 if len(s) > n else np.nan
    out["since"] = float(s.iloc[-1] / s.iloc[0] - 1) * 100 if len(s) > 1 else np.nan
    out["from_peak"] = float(s.iloc[-1] / s.max() - 1) * 100
    out["falling"] = bool(len(s) > 5 and s.iloc[-1] < s.iloc[-6] and s.iloc[-1] < s.max())
    return out


# ==========================================================================
# 23. 분산 점검 (핵심/위성) · 새 돈 배분 · 공제 안에서 세금 없이 덜어내기
#   핵심 = 장기로 모은 종목, 분산은 '새 돈'으로. 위성 = 3배 테마·SOXL v1 (계좌의 일부만).
# ==========================================================================
DIVERSIFY_KEY = "diversify"
DIVERSIFY_DEFAULT = {"single": 0.20, "sector": 0.30, "leverage": 0.15, "bigtech": 0.50, "satellite": 0.20,
                     "monthly_krw": 0}
# 티커 → (업종, S&P500 업종 칸) — 모르는 티커는 '기타'
SECTOR_OF = {
    "NVDA": ("반도체", "IT"), "AVGO": ("반도체", "IT"), "MRVL": ("반도체", "IT"), "AMD": ("반도체", "IT"),
    "MU": ("반도체", "IT"), "TSM": ("반도체", "IT"), "INTC": ("반도체", "IT"), "QCOM": ("반도체", "IT"),
    "SOXL": ("반도체", "IT"), "SOXX": ("반도체", "IT"), "SMH": ("반도체", "IT"), "USD": ("반도체", "IT"),
    "MSFT": ("소프트웨어", "IT"), "ORCL": ("소프트웨어", "IT"), "CRM": ("소프트웨어", "IT"), "PLTR": ("소프트웨어", "IT"),
    "AAPL": ("IT 하드웨어", "IT"), "SMCI": ("IT 하드웨어", "IT"), "DELL": ("IT 하드웨어", "IT"),
    "META": ("커뮤니케이션", "커뮤니케이션"), "GOOG": ("커뮤니케이션", "커뮤니케이션"), "GOOGL": ("커뮤니케이션", "커뮤니케이션"),
    "NFLX": ("커뮤니케이션", "커뮤니케이션"),
    "AMZN": ("경기소비재", "경기소비재"), "TSLA": ("경기소비재", "경기소비재"),
    "KO": ("필수소비재", "필수소비재"), "PEP": ("필수소비재", "필수소비재"), "PG": ("필수소비재", "필수소비재"),
    "COST": ("필수소비재", "필수소비재"), "XLP": ("필수소비재", "필수소비재"),
    "VST": ("유틸리티", "유틸리티"), "CEG": ("유틸리티", "유틸리티"), "XLU": ("유틸리티", "유틸리티"),
    "XLV": ("헬스케어", "헬스케어"), "CURE": ("헬스케어", "헬스케어"), "LLY": ("헬스케어", "헬스케어"), "UNH": ("헬스케어", "헬스케어"),
    "XLF": ("금융", "금융"), "FAS": ("금융", "금융"), "JPM": ("금융", "금융"), "BRK-B": ("금융", "금융"),
    "XLI": ("산업재", "산업재"), "XLE": ("에너지", "에너지"), "ERX": ("에너지", "에너지"),
    "XLRE": ("부동산", "부동산"), "DRN": ("부동산", "부동산"), "XLB": ("소재", "소재"),
    "QQQ": ("지수 ETF", "지수"), "VOO": ("지수 ETF", "지수"), "SPY": ("지수 ETF", "지수"), "VTI": ("지수 ETF", "지수"),
    "JEPQ": ("배당·인컴 ETF", "지수"), "SCHD": ("배당·인컴 ETF", "지수"), "JEPI": ("배당·인컴 ETF", "지수"),
    "TQQQ": ("지수 ETF", "지수"), "UPRO": ("지수 ETF", "지수"),
}
BIGTECH = {"AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "GOOGL", "META", "TSLA"}
LEVERAGED = {"SOXL", "TQQQ", "UPRO", "USD", "FAS", "CURE", "DRN", "LABU", "ERX", "NUGT", "DFEN", "DUSL", "WANT",
             "UCYB", "CWEB", "BITX", "TECL", "FNGU", "BULZ", "NVDL", "TSLL"}
# S&P500 업종 비중(대략, 새 돈 배분의 기준점) · 칸별 1배 ETF
SP500_SECTOR = {"IT": 0.32, "금융": 0.13, "헬스케어": 0.10, "경기소비재": 0.10, "커뮤니케이션": 0.10, "산업재": 0.08,
                "필수소비재": 0.055, "에너지": 0.03, "유틸리티": 0.025, "부동산": 0.02, "소재": 0.02}
SECTOR_ETF = {"IT": "XLK", "금융": "XLF", "헬스케어": "XLV", "경기소비재": "XLY", "커뮤니케이션": "XLC", "산업재": "XLI",
              "필수소비재": "XLP", "에너지": "XLE", "유틸리티": "XLU", "부동산": "XLRE", "소재": "XLB"}


def div_sector(ticker: str) -> tuple[str, str]:
    t = str(ticker).upper()
    if t in SECTOR_OF:
        return SECTOR_OF[t]
    for theme, (base, levs) in THEME_ETFS.items():
        if t == base or t in [x for x, _ in levs]:
            return (theme, "기타")
    return ("기타", "기타")


def holdings_values(hold: pd.DataFrame, prices: dict[str, float]) -> pd.DataFrame:
    """[ticker, shares, avg_price] + 현재가 → 평가액·원가·업종 표 (달러)."""
    h = hold.copy()
    h["ticker"] = h["ticker"].astype(str).str.upper()
    h["price"] = h["ticker"].map(prices).astype(float)
    h["value"] = h["shares"].astype(float) * h["price"]
    h["cost"] = h["shares"].astype(float) * h["avg_price"].astype(float)
    h["sector"] = h["ticker"].map(lambda t: div_sector(t)[0])
    h["sp_sector"] = h["ticker"].map(lambda t: div_sector(t)[1])
    h["bigtech"] = h["ticker"].isin(BIGTECH)
    h["leveraged"] = h["ticker"].isin(LEVERAGED)
    return h.dropna(subset=["value"])


def diversification_check(hv: pd.DataFrame, cash_usd: float, limits: dict) -> pd.DataFrame:
    """한도 점검표: 한 종목 / 한 업종 / 레버리지 / 빅테크. 비중은 계좌 총액(현금 포함) 기준."""
    tot = float(hv["value"].sum()) + float(cash_usd or 0)
    if tot <= 0:
        return pd.DataFrame()
    w = hv.groupby("ticker")["value"].sum() / tot
    sw = hv[hv["sector"] != "지수 ETF"].groupby("sector")["value"].sum() / tot
    rows = []

    def add(name, lim, cur, who):
        rows.append({"점검": name, "한도(%)": round(lim * 100), "지금(%)": round(cur * 100, 1),
                     "상태": "⚠️ 넘음" if cur > lim + 1e-9 else "✅", "가장 큰 것": who})
    add("한 종목", limits["single"], float(w.max()), f"{w.idxmax()} {w.max() * 100:.0f}%")
    add("한 업종", limits["sector"], float(sw.max()) if len(sw) else 0.0,
        f"{sw.idxmax()} {sw.max() * 100:.0f}%" if len(sw) else "—")
    lev = float(hv.loc[hv["leveraged"], "value"].sum()) / tot
    add("레버리지 ETF", limits["leverage"], lev, ", ".join(hv.loc[hv["leveraged"], "ticker"]) or "없음")
    bt = float(hv.loc[hv["bigtech"], "value"].sum()) / tot
    add("빅테크 8개 합계", limits["bigtech"], bt, ", ".join(hv.loc[hv["bigtech"]].sort_values("value", ascending=False)["ticker"].head(4)))
    return pd.DataFrame(rows)


def new_money_plan(hv: pd.DataFrame, cash_usd: float, new_usd: float, limits: dict) -> pd.DataFrame:
    """새 돈 배분: S&P500 업종 비중보다 모자란 칸에 모자란 만큼 비례 배분. 한도를 넘은 업종(예: 반도체가 속한 IT)은 제외.
    지수 ETF·배당 ETF는 업종 분산으로 보지 않음(이미 분산된 돈)."""
    if new_usd <= 0:
        return pd.DataFrame()
    tot = float(hv["value"].sum()) + float(cash_usd or 0) + new_usd
    sp = hv[hv["sp_sector"].isin(SP500_SECTOR)].groupby("sp_sector")["value"].sum() / tot
    over = set()
    sw = hv.groupby("sector")["value"].sum() / tot
    for sec, v in sw.items():
        if v > limits["sector"] and sec not in ("지수 ETF", "배당·인컴 ETF"):
            over.add(hv.loc[hv["sector"] == sec, "sp_sector"].iloc[0])
    gaps = {k: max(SP500_SECTOR[k] - float(sp.get(k, 0.0)), 0.0) for k in SP500_SECTOR if k not in over}
    g = sum(gaps.values())
    rows = []
    for k, gap in sorted(gaps.items(), key=lambda x: -x[1]):
        if gap <= 0:
            continue
        amt = new_usd * gap / g
        if amt < new_usd * 0.03:
            continue
        rows.append({"업종": k, "S&P500 비중(%)": round(SP500_SECTOR[k] * 100, 1), "내 비중(%)": round(float(sp.get(k, 0)) * 100, 1),
                     "넣을 금액($)": round(amt, 2), "ETF 후보": SECTOR_ETF[k]})
    return pd.DataFrame(rows)


def tax_free_trim(hv: pd.DataFrame, room_krw: float, fx: float, focus: list[str]) -> pd.DataFrame:
    """남은 공제(room_krw) 안에서 이익 난 종목을 얼마나 팔면 세금 0원인지. 매도액 = 이익 ÷ 이익 비율."""
    rows = []
    for _, r in hv[hv["ticker"].isin(focus)].iterrows():
        gain = (r["value"] - r["cost"]) * fx
        if gain <= 0 or room_krw <= 0:
            continue
        ratio = gain / (r["value"] * fx)
        sell_krw = min(room_krw / ratio, r["value"] * fx)
        rows.append({"종목": r["ticker"], "이익 비율(%)": round(ratio * 100, 1), "팔 금액(원)": round(sell_krw),
                     "팔 주식 수": round(sell_krw / fx / r["price"], 4), "실현 이익(원)": round(sell_krw * ratio)})
    return pd.DataFrame(rows)
