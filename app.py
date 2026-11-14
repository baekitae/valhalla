"""Project Valhalla — 무한매수 전술 관제소 V4.0

실행:  streamlit run app.py
계산 로직은 valhalla_core.py 에 있습니다 (같은 폴더에 두어야 합니다).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import logging
import os
import re
import tempfile
import warnings
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from plotly.subplots import make_subplots

import valhalla_core as core

try:
    from supabase import create_client
except ImportError:  # Supabase 미사용 환경에서도 실행 가능
    create_client = None
try:
    from deep_translator import GoogleTranslator
except ImportError:
    GoogleTranslator = None

logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("valhalla")
warnings.filterwarnings("ignore", category=FutureWarning, module="yfinance")  # yfinance 내부 경고만 숨김

# ==========================================================================
# 설정
# ==========================================================================
APP_VERSION = "V4.0"
KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(os.environ.get("VALHALLA_DATA_DIR") or Path(__file__).resolve().parent)  # 로컬 CSV 저장 위치
LOCAL_FILES = {"trade_journal": BASE_DIR / "trade_journal.csv", "ai_ledger": BASE_DIR / "ai_ledger.csv"}
MODELS_FILE = BASE_DIR / "ai_models.json"
MODELS_TABLE = "valhalla_ai_models"
SB_PAGE = 1000
FALLBACK_FX = 1400.0

UP, DOWN, NEUTRAL = "#F04452", "#3182F6", "#8B95A1"      # 한국식: 상승 빨강 / 하락 파랑
ACCENT, GOLD, MAGENTA = "#3B82F6", "#F5B841", "#C084FC"
SERIES = ["#3B82F6", "#F5B841", "#22C55E", "#C084FC", "#F97316"]

SOXL_HOLDINGS = {"NVDA": 11.5, "AVGO": 8.5, "AMD": 7.5, "QCOM": 6.0, "TSM": 5.0, "AMAT": 4.5,
                 "MU": 4.5, "INTC": 4.0, "TXN": 4.0, "ASML": 4.0}
MACRO_TICKERS = {"SPY": "S&P 500", "QQQ": "나스닥 100", "TLT": "미국 장기채 (금리)", "USO": "원유 (유가)"}

CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css');
html, body, .stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp h1, .stApp h2,
.stApp h3, .stApp h4, .stApp table, .stApp [data-testid="stMarkdownContainer"],
.stApp [data-testid="stMetricValue"], .stApp [data-testid="stMetricLabel"] {
  font-family: 'Pretendard Variable', Pretendard, -apple-system, 'Segoe UI', sans-serif !important;
}
.stApp [data-testid="stIconMaterial"] { font-family: 'Material Symbols Rounded' !important; }
.block-container { padding-top: 1.4rem; padding-bottom: 3rem; max-width: 1560px; }
h1, h2, h3 { letter-spacing: -0.02em; }
.vh-header { display:flex; justify-content:space-between; align-items:flex-end; gap:1rem; flex-wrap:wrap;
  padding-bottom: .9rem; margin-bottom: 1.1rem; border-bottom: 1px solid rgba(148,163,184,.16); }
.vh-eyebrow { font-size:.72rem; font-weight:700; letter-spacing:.14em; color:#60A5FA; text-transform:uppercase; }
.vh-title { font-size:1.65rem; font-weight:700; margin:.15rem 0 .1rem; }
.vh-sub { color:#94A3B8; font-size:.9rem; }
.vh-badges { display:flex; gap:.4rem; flex-wrap:wrap; }
.vh-badge { display:inline-flex; align-items:center; gap:.35rem; padding:.22rem .65rem; border-radius:999px;
  font-size:.75rem; font-weight:600; border:1px solid rgba(148,163,184,.25); color:#CBD5E1; background:rgba(148,163,184,.08); }
.vh-dot { width:7px; height:7px; border-radius:50%; display:inline-block; }
.vh-section { display:flex; align-items:center; gap:.5rem; margin:1.6rem 0 .6rem; font-size:1.02rem; font-weight:700; }
.vh-section::before { content:""; width:4px; height:1.05rem; border-radius:2px; background:#3B82F6; }
.vh-caption { color:#94A3B8; font-size:.82rem; margin:-.35rem 0 .7rem; }
[data-testid="stMetric"] { background:rgba(148,163,184,.05); border-radius:10px; padding:.7rem .9rem; }
[data-testid="stMetricValue"] { font-size:1.35rem; font-weight:700; font-variant-numeric: tabular-nums; }
[data-testid="stMetricLabel"] p { color:#94A3B8; font-size:.8rem; font-weight:600; }
.vh-kv { display:flex; justify-content:space-between; padding:.32rem 0; border-bottom:1px dashed rgba(148,163,184,.15);
  font-size:.88rem; }
.vh-kv span:first-child { color:#94A3B8; }
.vh-kv span:last-child { font-weight:600; font-variant-numeric: tabular-nums; }
.vh-pill { display:inline-block; padding:.12rem .55rem; border-radius:6px; font-size:.75rem; font-weight:700; }
.vh-pill.ok { background:rgba(34,197,94,.14); color:#4ADE80; }
.vh-pill.warn { background:rgba(245,184,65,.14); color:#F5B841; }
.vh-pill.stop { background:rgba(240,68,82,.14); color:#F87171; }
.vh-pill.info { background:rgba(59,130,246,.14); color:#60A5FA; }
.kw-pos { background:rgba(240,68,82,.16); color:#F87171; padding:0 3px; border-radius:3px; font-weight:700; }
.kw-neg { background:rgba(49,130,246,.18); color:#60A5FA; padding:0 3px; border-radius:3px; font-weight:700; }
.vh-brand { font-weight:800; font-size:1.15rem; letter-spacing:-.02em; }
.vh-brand small { display:block; font-weight:500; font-size:.75rem; color:#94A3B8; letter-spacing:0; }
.vh-foot { color:#64748B; font-size:.75rem; margin-top:2rem; }
</style>
"""


# ==========================================================================
# 공용 헬퍼
# ==========================================================================
def now_kst() -> dt.datetime:
    return dt.datetime.now(KST)


def today_kst() -> dt.date:
    return now_kst().date()


def fmt_usd(x, digits: int = 2) -> str:
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.{digits}f}"


def md_usd(x, digits: int = 2) -> str:
    """마크다운 안에서 $가 수식(LaTeX)으로 해석되지 않도록 이스케이프."""
    return fmt_usd(x, digits).replace("$", "\\$")


def fmt_krw(x) -> str:
    return "—" if x is None else f"₩{x:,.0f}"


def fmt_qty(x: float) -> str:
    return f"{x:,.0f}주" if abs(x - round(x)) < 1e-6 else f"{x:,.4f}주"


def pct_text(x) -> str:
    return "—" if x is None or not np.isfinite(float(x)) else f"{float(x):+.1f}%"


def kr_delta(value) -> str:
    if value is None or not np.isfinite(value) or abs(value) < 1e-12:
        return "off"
    return "red" if value > 0 else "blue"


def fx_rate() -> float:
    return float(st.session_state.get("fx_rate", FALLBACK_FX))


def flash(message: str, icon: str = ":material/check_circle:") -> None:
    st.session_state.setdefault("_flash", []).append((message, icon))


def show_flash() -> None:
    for message, icon in st.session_state.pop("_flash", []):
        st.toast(message, icon=icon)


def page_header(eyebrow: str, title: str, subtitle: str, badges: list[tuple[str, str]] | None = None) -> None:
    badge_html = "".join(f'<span class="vh-badge"><span class="vh-dot" style="background:{c}"></span>{t}</span>'
                         for t, c in (badges or []))
    st.markdown(f"""<div class="vh-header"><div><div class="vh-eyebrow">{eyebrow}</div>
        <div class="vh-title">{title}</div><div class="vh-sub">{subtitle}</div></div>
        <div class="vh-badges">{badge_html}</div></div>""", unsafe_allow_html=True)


def section(title: str, caption: str | None = None) -> None:
    st.markdown(f'<div class="vh-section">{title}</div>', unsafe_allow_html=True)
    if caption:
        st.markdown(f'<div class="vh-caption">{caption}</div>', unsafe_allow_html=True)


def kv_rows(pairs: list[tuple[str, str]]) -> None:
    def _clean(v: str) -> str:   # HTML 블록 안에서는 $를 엔티티로 (수식 해석·역슬래시 노출 방지)
        return str(v).replace("\\$", "&#36;").replace("$", "&#36;")
    st.markdown("".join(f'<div class="vh-kv"><span>{k}</span><span>{_clean(v)}</span></div>' for k, v in pairs),
                unsafe_allow_html=True)


def pill(text: str, kind: str = "info") -> str:
    return f'<span class="vh-pill {kind}">{text}</span>'


def base_layout(fig: go.Figure, height: int = 420, legend: bool = True) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Pretendard Variable, Pretendard, sans-serif", size=12, color="#CBD5E1"),
        margin=dict(l=8, r=8, t=36, b=8), hovermode="x unified", showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0, bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="#131A26", bordercolor="#232C3B"),
    )
    fig.update_xaxes(gridcolor="rgba(148,163,184,.08)", zeroline=False, showspikes=True, spikethickness=1,
                     spikecolor="rgba(148,163,184,.4)", spikedash="dot")
    fig.update_yaxes(gridcolor="rgba(148,163,184,.08)", zeroline=False)
    return fig


def show_chart(fig: go.Figure, key: str | None = None) -> None:
    st.plotly_chart(fig, theme=None, key=key, config={"displaylogo": False, "modeBarButtonsToRemove":
                                                         ["lasso2d", "select2d", "autoScale2d"]})


# ==========================================================================
# 저장소 (Supabase 우선, 없으면 로컬 CSV)
# ==========================================================================
@st.cache_resource(show_spinner=False)
def get_supabase():
    if create_client is None:
        return None
    try:
        url, key = st.secrets.get("SUPABASE_URL"), st.secrets.get("SUPABASE_KEY")
    except Exception:  # secrets.toml 자체가 없는 경우
        return None
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception:
        LOGGER.exception("Supabase 연결 실패 → 로컬 저장소 사용")
        return None


def backend() -> str:
    return "supabase" if get_supabase() is not None else "local"


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def init_local_storage() -> None:
    if backend() != "local":
        return
    for table, path in LOCAL_FILES.items():
        if not path.exists():
            _atomic_write(path, core.empty_table(table).to_csv(index=False))
    if not MODELS_FILE.exists():  # 최초 1회만 기본 요원 생성 (삭제 후에는 빈 목록 유지)
        defaults = {
            "Agent_Alpha (기본)": {"genes": [15.0, 2.0, -3.0, 1.0, 8.0, 2.0, 1.5, 40.0], "rank": 1},
            "Agent_Beta (기본)": {"genes": [5.0, 0.5, -1.0, 0.5, 2.0, 1.0, 0.5, 30.0], "rank": 2},
            "Agent_Gamma (기본)": {"genes": [10.0, 1.0, -2.0, 1.5, 5.0, 3.0, 2.0, 20.0], "rank": 3},
        }
        _atomic_write(MODELS_FILE, json.dumps(defaults, ensure_ascii=False, indent=2))


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", force_ascii=False)) if not df.empty else []


def _sb_fetch_all(table: str, order_col: str | None = "id") -> list[dict]:
    """Supabase는 한 번에 최대 1,000행만 돌려주므로 끝까지 페이지를 넘겨 읽습니다."""
    sb, rows, start = get_supabase(), [], 0
    while True:
        query = sb.table(table).select("*")
        if order_col:
            query = query.order(order_col)
        batch = query.range(start, start + SB_PAGE - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < SB_PAGE:
            return rows
        start += SB_PAGE


def _sb_insert(table: str, records: list[dict]) -> None:
    for i in range(0, len(records), 500):
        get_supabase().table(table).insert(records[i:i + 500]).execute()


def _sb_replace(table: str, records: list[dict], key_col: str = "id", sentinel=-1,
                order_col: str | None = "id") -> None:
    """전체 교체. 삽입이 실패하면 백업본을 되돌려 넣어 '빈 테이블' 사고를 막습니다."""
    backup = _sb_fetch_all(table, order_col)
    get_supabase().table(table).delete().neq(key_col, sentinel).execute()
    try:
        _sb_insert(table, records)
    except Exception:
        LOGGER.exception("%s 저장 실패 → 백업 복원 시도", table)
        try:
            _sb_insert(table, [{k: v for k, v in r.items() if k != "id"} for r in backup])
        except Exception:
            LOGGER.exception("%s 백업 복원 실패", table)
        raise


@st.cache_data(ttl=30, show_spinner=False)
def _load_table(table: str, backend_name: str) -> pd.DataFrame:
    if backend_name == "supabase":
        frame = pd.DataFrame(_sb_fetch_all(table))
    else:
        path = LOCAL_FILES[table]
        if not path.exists():
            return core.empty_table(table)
        frame = pd.read_csv(path, dtype=str, keep_default_na=False)  # 계좌번호 앞자리 0 보존
    return core.coerce_table(table, frame)


def load_table(table: str, strict: bool = False) -> tuple[pd.DataFrame, bool]:
    try:
        return _load_table(table, backend()).copy(), True
    except Exception as exc:
        LOGGER.exception("%s 읽기 실패", table)
        if strict:
            raise
        st.error(f"데이터를 불러오지 못했습니다 ({table}): {exc}", icon=":material/error:")
        return core.empty_table(table), False


def save_table(table: str, frame: pd.DataFrame) -> int:
    clean = core.validate_table(table, frame) if not frame.dropna(how="all").empty else core.empty_table(table)
    if backend() == "supabase":
        _sb_replace(table, _records(clean))
    else:
        _atomic_write(LOCAL_FILES[table], clean.to_csv(index=False))
    _load_table.clear()
    return len(clean)


def append_rows(table: str, frame: pd.DataFrame) -> int:
    clean = core.validate_table(table, frame)
    if backend() == "supabase":
        _sb_insert(table, _records(clean))
    else:
        existing, _ = load_table(table, strict=True)
        _atomic_write(LOCAL_FILES[table], pd.concat([existing, clean], ignore_index=True).to_csv(index=False))
    _load_table.clear()
    return len(clean)


@st.cache_data(ttl=30, show_spinner=False)
def _load_models_raw(backend_name: str) -> dict:
    if backend_name == "supabase":
        return {row["agent_name"]: row["genes"] for row in _sb_fetch_all(MODELS_TABLE, order_col=None)}
    if not MODELS_FILE.exists():
        return {}
    return json.loads(MODELS_FILE.read_text(encoding="utf-8") or "{}")


def load_models() -> list[dict]:
    try:
        raw = _load_models_raw(backend())
    except Exception as exc:
        LOGGER.exception("AI 모델 로드 실패")
        st.error(f"AI 요원 정보를 불러오지 못했습니다: {exc}", icon=":material/error:")
        return []
    models = []
    for order, (name, value) in enumerate(raw.items()):
        try:
            for _ in range(2):  # text 컬럼(문자열) / jsonb 컬럼(이미 파싱됨) 모두 지원
                if isinstance(value, str):
                    value = json.loads(value)
            meta = value if isinstance(value, dict) else {"genes": value}
            match = re.search(r"Rank_(\d+)", name)
            models.append({**meta, "name": name, "genes": core.pad_genes(meta.get("genes", [])),
                           "rank": int(meta.get("rank") or (match.group(1) if match else order + 1))})
        except (TypeError, ValueError, json.JSONDecodeError):
            LOGGER.warning("손상된 요원 데이터 건너뜀: %s", name)
    return sorted(models, key=lambda m: (m["rank"], m["name"]))


def save_models(models: list[dict]) -> None:
    payload = {m["name"]: {k: v for k, v in m.items() if k != "name"} for m in models}
    if backend() == "supabase":
        records = [{"agent_name": n, "genes": json.dumps(meta, ensure_ascii=False)} for n, meta in payload.items()]
        _sb_replace(MODELS_TABLE, records, key_col="agent_name", sentinel="__none__", order_col=None)
    else:
        _atomic_write(MODELS_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
    _load_models_raw.clear()


# ==========================================================================
# 시세 데이터 (yfinance)
# ==========================================================================
def _clean_ohlcv(raw: pd.DataFrame | None) -> pd.DataFrame | None:
    if raw is None or raw.empty:
        return None
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):     # 최신 yfinance는 단일 종목도 MultiIndex
        df.columns = df.columns.get_level_values(0)
    df = df.loc[:, ~df.columns.duplicated()]
    need = ["Open", "High", "Low", "Close", "Volume"]
    if any(c not in df.columns for c in need):
        return None
    index = pd.to_datetime(df.index)
    df.index = index.tz_localize(None) if index.tz is not None else index
    df = df[need].apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
    df["Volume"] = df["Volume"].fillna(0)
    for col in ("Open", "High", "Low"):
        df[col] = df[col].fillna(df["Close"])
    return None if df.empty else df


def _download(ticker: str, period: str, adjusted: bool) -> pd.DataFrame | None:
    try:
        raw = yf.download(ticker, period=period, interval="1d", auto_adjust=adjusted,
                          progress=False, threads=False)
    except Exception:
        LOGGER.exception("시세 다운로드 실패: %s", ticker)
        return None
    return _clean_ohlcv(raw)


@st.cache_data(ttl=60, show_spinner=False)
def load_recent(ticker: str) -> pd.DataFrame | None:
    return _download(ticker, "2y", False)


@st.cache_data(ttl=3600, show_spinner=False)
def load_long(ticker: str, period: str = "5y", adjusted: bool = False) -> pd.DataFrame | None:
    return _download(ticker, period, adjusted)


@st.cache_data(ttl=300, show_spinner=False)
def fetch_usdkrw() -> float | None:
    df = _download("USDKRW=X", "5d", False)   # 주말에도 직전 영업일 값 확보
    return float(df["Close"].iloc[-1]) if df is not None else None


@st.cache_data(ttl=900, show_spinner=False)
def load_snapshot(tickers: tuple[str, ...]) -> dict[str, tuple[float, float]]:
    out = {}
    try:
        raw = yf.download(list(tickers), period="5d", auto_adjust=False, progress=False, group_by="column")
    except Exception:
        LOGGER.exception("스냅샷 다운로드 실패")
        return out
    if raw is None or raw.empty:
        return out
    closes = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].set_axis([tickers[0]], axis=1)
    for t in tickers:
        if t in closes:
            s = closes[t].dropna()
            if len(s) >= 2:
                out[t] = (float(s.iloc[-1]), float((s.iloc[-1] / s.iloc[-2] - 1) * 100))
    return out


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_headlines(ticker: str, limit: int = 3) -> list[dict]:
    try:
        news = yf.Ticker(ticker).news or []
    except Exception:
        LOGGER.exception("뉴스 조회 실패: %s", ticker)
        return []
    out = []
    for item in news:
        if not isinstance(item, dict):
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else {}
        title = item.get("title") or content.get("title")
        link = (item.get("link") or (content.get("canonicalUrl") or {}).get("url")
                or (content.get("clickThroughUrl") or {}).get("url") or "")
        publisher = item.get("publisher") or (content.get("provider") or {}).get("displayName") or ""
        if title:
            out.append({"title": title, "link": link, "publisher": publisher})
        if len(out) >= limit:
            break
    return out


@st.cache_data(ttl=86_400, show_spinner=False)
def translate_ko(text: str) -> str | None:
    if GoogleTranslator is None:
        return None
    try:
        return GoogleTranslator(source="en", target="ko").translate(text)
    except Exception:
        LOGGER.warning("번역 실패")
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def load_treasury() -> dict | None:
    df = _download("^TNX", "6mo", False)
    if df is None or len(df) < 6:
        return None
    c = df["Close"]
    current, peak = float(c.iloc[-1]), float(c.max())
    return {"series": c, "current": current, "peak": peak, "peak_date": c.idxmax(),
            "drop_pct": (peak - current) / peak * 100, "trend5": current - float(c.iloc[-6])}


@st.cache_data(ttl=1800, show_spinner=False)
def cached_walk_forward(ticker: str, horizon: int, fee_bps: int) -> dict | None:
    df = load_long(ticker, "5y", True)
    return None if df is None else core.walk_forward(df["Close"], horizon, 60, fee_bps)


@st.cache_data(ttl=600, show_spinner=False)
def run_league(ticker: str, start: str, capital: float, fee_bps: float,
               genes: tuple[tuple[float, ...], ...], names: tuple[str, ...]) -> dict | None:
    days = (today_kst() - dt.date.fromisoformat(start)).days
    period = "2y" if days < 550 else "5y" if days < 1700 else "max"
    df = load_long(ticker, period, False)
    if df is None:
        return None
    idx = df.index
    start_i = max(int(idx.searchsorted(pd.Timestamp(start))), core.FEATURE_WARMUP + 1)
    if start_i >= len(df):
        return None
    feats = core.compute_features(df["Close"], df["Volume"])
    fee = fee_bps / 10_000
    res = core.simulate(np.array(genes), df["Open"], df["High"], df["Low"], df["Close"], feats, start_i,
                        capital, fee, record=True, dates=list(idx.strftime("%Y-%m-%d")),
                        agent_names=list(names), ticker=ticker, keep_curve=True)
    close = df["Close"].to_numpy()
    curve = pd.DataFrame(res.equity_curve, index=idx[start_i:], columns=list(names))
    curve["Buy & Hold"] = capital * close[start_i:] / close[start_i - 1] * (1 - fee)
    bh_ret, bh_mdd = core.buy_and_hold(close, start_i, fee)
    summary = pd.DataFrame({
        "요원": list(names), "총자산": res.final_equity, "수익률(%)": res.return_pct,
        "실현손익": res.realized, "평가손익": res.qty * close[-1] - res.cost,
        "MDD(%)": res.mdd_pct, "체결수": res.n_trades.astype(int), "보유수량": res.qty,
    })
    return {"summary": summary, "trades": res.trades, "curve": curve, "bh_ret": bh_ret, "bh_mdd": bh_mdd,
            "start": str(idx[start_i].date()), "end": str(idx[-1].date()), "last_close": float(close[-1])}


# ==========================================================================
# 차트
# ==========================================================================
def plot_dates(frame: pd.DataFrame, correct: bool) -> pd.Series:
    """한국 새벽(09시 이전) 체결은 미국 현지 전일 거래이므로 -1일 보정."""
    dates = pd.to_datetime(frame["Date"])
    if not correct:
        return dates
    dawn = pd.to_datetime(frame["Time"], format="%H:%M:%S", errors="coerce").dt.hour < 9
    return dates - pd.to_timedelta(dawn.fillna(False).astype(int), unit="D")


def price_chart(df: pd.DataFrame, ticker: str, trades: pd.DataFrame, pos: core.Position,
                target_price: float | None, resistance: float | None, support: float | None,
                window: str, correct_time: bool) -> go.Figure:
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.78, 0.22], vertical_spacing=0.025)
    fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
                                 name=ticker, increasing=dict(line=dict(color=UP), fillcolor=UP),
                                 decreasing=dict(line=dict(color=DOWN), fillcolor=DOWN)), row=1, col=1)
    for n, color in ((20, GOLD), (60, MAGENTA)):
        fig.add_trace(go.Scatter(x=df.index, y=df["Close"].rolling(n).mean(), name=f"MA{n}",
                                 line=dict(color=color, width=1.2), opacity=0.85), row=1, col=1)
    vol_colors = np.where(df["Close"] >= df["Open"], UP, DOWN)
    fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=vol_colors, opacity=0.55, name="거래량",
                         showlegend=False), row=2, col=1)
    lines = []
    if pos.qty > 0:
        lines.append((pos.avg, f"평단 {fmt_usd(pos.avg, 4)}", GOLD, "dot"))
        if target_price:
            lines.append((target_price, f"목표 {fmt_usd(target_price)}", MAGENTA, "solid"))
    if resistance:
        lines.append((resistance, f"매물 저항 {fmt_usd(resistance)}", "#F97316", "dash"))
    if support:
        lines.append((support, f"매물 지지 {fmt_usd(support)}", "#22C55E", "dash"))
    for y, label, color, dash in lines:
        fig.add_hline(y=y, line=dict(color=color, width=1.2, dash=dash), row=1, col=1,
                      annotation=dict(text=label, font=dict(color=color, size=11), bgcolor="rgba(11,15,23,.7)"),
                      annotation_position="right")
    for action, symbol, color, label in (("Buy", "triangle-up", "#22C55E", "매수"),
                                         ("Sell", "triangle-down", "#F87171", "매도")):
        part = trades[trades["Action"] == action]
        if part.empty:
            continue
        fig.add_trace(go.Scatter(
            x=plot_dates(part, correct_time), y=part["Price"], mode="markers", name=f"나의 {label}",
            marker=dict(symbol=symbol, size=11, color=color, line=dict(width=1, color="#0B0F17")),
            customdata=np.c_[part["Qty"], part["Account"], part["Time"]],
            hovertemplate=f"{label} %{{customdata[0]}}주 @ $%{{y:.2f}}<br>계좌 %{{customdata[1]}} · %{{customdata[2]}} KST<extra></extra>",
        ), row=1, col=1)
    months = {"3M": 3, "6M": 6, "1Y": 12, "2Y": 24}[window]
    end = df.index[-1] + pd.Timedelta(days=4)
    begin = max(df.index[0], df.index[-1] - pd.DateOffset(months=months))
    visible = df.loc[begin:]
    ys = [visible["Low"].min(), visible["High"].max()] + [y for y, *_ in lines]
    pad = (max(ys) - min(ys)) * 0.06
    base_layout(fig, height=600)
    fig.update_layout(xaxis_rangeslider_visible=False, margin=dict(l=8, r=110, t=36, b=8))
    fig.update_xaxes(range=[begin, end], rangebreaks=[dict(bounds=["sat", "mon"])])
    fig.update_yaxes(range=[min(ys) - pad, max(ys) + pad], row=1, col=1, title_text="USD", side="left")
    fig.update_yaxes(range=[0, visible["Volume"].max() * 1.15], row=2, col=1, showticklabels=False)
    return fig


# ==========================================================================
# 사이드바
# ==========================================================================
def render_sidebar() -> None:
    sb = st.sidebar
    sb.markdown(f'<div class="vh-brand">VALHALLA<small>무한매수 전술 관제소 · {APP_VERSION}</small></div>',
                unsafe_allow_html=True)
    sb.write("")
    is_cloud = backend() == "supabase"
    sb.markdown(f'<span class="vh-badge"><span class="vh-dot" style="background:{"#22C55E" if is_cloud else GOLD}">'
                f'</span>{"클라우드 DB 연결" if is_cloud else "로컬 CSV 저장소"}</span>', unsafe_allow_html=True)

    fetched = fetch_usdkrw()
    with sb.expander("환율", icon=":material/currency_exchange:", expanded=fetched is None):
        manual = st.toggle("수동 입력", value=fetched is None, key="fx_manual",
                           help="실시간 환율 조회가 실패했거나 다른 환율을 쓰고 싶을 때 사용합니다.")
        if manual or fetched is None:
            rate = st.number_input("USD/KRW", min_value=500.0, max_value=3000.0,
                                   value=float(fetched or FALLBACK_FX), step=1.0, key="fx_manual_value")
        else:
            rate = float(fetched)
        if fetched is None:
            st.caption("실시간 환율을 불러오지 못해 수동 값을 사용합니다.")
    st.session_state["fx_rate"] = rate
    sb.metric("USD/KRW", f"{rate:,.2f}", help="Yahoo Finance USDKRW=X, 5분 캐시")

    with sb.expander("키움 CSV 동기화", icon=":material/upload_file:"):
        files = st.file_uploader("거래/주문 내역 CSV", type=["csv"], accept_multiple_files=True,
                                 key="kiwoom_files")
        account = st.text_input("계좌명 (선택)", key="kiwoom_account", placeholder="비우면 파일명에서 추출",
                                help="파일명에 계좌번호가 없으면 직접 입력하세요. 날짜 숫자는 자동 제외됩니다.")
        if st.button("파싱 후 일지에 병합", type="primary", disabled=not files, icon=":material/sync:",
                     width="stretch"):
            sync_kiwoom(files, account)
    sb.markdown(f'<div class="vh-foot">{now_kst():%Y-%m-%d %H:%M} KST · 투자 판단의 책임은 본인에게 있습니다.</div>',
                unsafe_allow_html=True)


def sync_kiwoom(files, account: str) -> None:
    parsed, notes = [], []
    for f in files:
        df, stats = core.parse_kiwoom_csv(f.getvalue(), f.name, account)
        if stats["format"] is None:
            notes.append(f"{f.name}: 키움 형식을 인식하지 못했습니다")
        else:
            notes.append(f"{f.name}: {stats['parsed']}건 인식 · {stats['skipped']}건 제외 ({stats['format']})")
        if not df.empty:
            parsed.append(df)
    for note in notes:
        st.caption(note)
    if not parsed:
        st.warning("병합할 체결 내역이 없습니다.")
        return
    try:
        existing, _ = load_table("trade_journal", strict=True)   # 읽기 실패 시 덮어쓰기 금지
        merged = core.merge_journal(existing, pd.concat(parsed, ignore_index=True))
        save_table("trade_journal", merged)
    except Exception as exc:
        LOGGER.exception("동기화 실패")
        st.error(f"동기화 실패 — 기존 데이터는 변경되지 않았습니다: {exc}")
        return
    flash(f"동기화 완료: {len(existing):,}건 → {len(merged):,}건 ({len(merged) - len(existing):+,}건)")
    st.rerun()


# ==========================================================================
# 페이지 1: 전술 관제
# ==========================================================================
def agent_plan(model: dict, last_feats, ref_close: float, pos: core.Position, budget_usd: float) -> dict:
    p = core.agent_params(model["genes"], last_feats)
    split = int(p["split"])
    base = pos.avg if pos.qty > 0 else ref_close
    unit = budget_usd / split
    remaining = max(0.0, budget_usd - pos.cost)
    if pos.buy_count >= split:
        amount, status = 0.0, ("분할 횟수 소진 · 매수 중지", "stop")
    elif remaining < 1:
        amount, status = 0.0, ("잔여 예산 소진 · 매수 중지", "stop")
    else:
        amount = min(unit * p["buy_mult"], remaining)
        status = ("정상 투입", "ok") if amount >= 1 else ("과열 구간 · 금일 매수 없음", "info")
    target = base * (1 + p["target_r"] / 100)
    if pos.qty > 0 and ref_close >= target:
        status = ("현재가 ≥ 목표가 · 매도 조건 충족", "warn")
    return {**p, "split": split, "target": base * (1 + p["target_r"] / 100), "unit": unit,
            "amount": amount, "remaining": remaining, "status": status,
            "limit": ref_close * (1 - p["discount"] / 100)}


def page_dashboard() -> None:
    header_slot = st.container()          # 헤더는 위에 표시하되, 티커 입력 뒤에 채웁니다.
    journal, journal_ok = load_table("trade_journal")
    st.session_state.setdefault("dash_ticker", "SOXL")
    top = st.columns([1.1, 2.2, 1.3, 1.4], vertical_alignment="bottom")
    ticker = top[0].text_input("티커", key="dash_ticker").upper().strip() or "SOXL"
    t_trades = journal[journal["Ticker"] == ticker]
    accounts = sorted(t_trades["Account"].unique().tolist())
    selected = top[1].multiselect("추적 계좌", accounts, default=accounts, key=f"acc_{ticker}",
                                  placeholder="매매 기록이 없습니다")
    valid_dates = t_trades.loc[t_trades["Date"] != "", "Date"]
    first_date = dt.date.fromisoformat(valid_dates.min()) if not valid_dates.empty else today_kst()
    since = top[2].date_input("집계 시작일", value=first_date, key=f"since_{ticker}",
                              help="이 날짜 이후 체결로 평단을 계산합니다. 전량 매도하면 사이클은 자동으로 새로 시작됩니다.")
    budget_krw = top[3].number_input("총 작전 예산 (₩)", min_value=1_000_000, value=10_000_000,
                                     step=1_000_000, format="%d", key="budget_krw")
    budget_usd = budget_krw / fx_rate()

    data = load_recent(ticker)
    with header_slot:
        page_header("COMMAND CENTER", f"{ticker} 전술 관제",
                    "보유 포지션 · 차트 · AI 요원 매매 계획",
                    [(f"데이터 {data.index[-1]:%Y-%m-%d}" if data is not None else "시세 없음",
                      "#22C55E" if data is not None else UP), (f"₩{fx_rate():,.1f}/$", ACCENT)])
    if data is None:
        st.error(f"'{ticker}' 시세를 불러오지 못했습니다. 티커를 확인하거나 잠시 후 다시 시도하세요.",
                 icon=":material/cloud_off:")
        return

    trades = t_trades[t_trades["Account"].isin(selected) & (t_trades["Date"] >= since.isoformat())]
    pos = core.compute_position(trades)
    price = float(data["Close"].iloc[-1])
    change = (price / float(data["Close"].iloc[-2]) - 1) * 100 if len(data) > 1 else 0.0
    unreal = pos.unrealized(price)
    unreal_pct = (price / pos.avg - 1) * 100 if pos.qty > 0 else None
    models = load_models()
    split_ref = agent_plan(models[0], core.compute_features(data["Close"], data["Volume"])[-1], price, pos,
                           budget_usd)["split"] if models else None

    k = st.columns(6)
    k[0].metric("현재가", fmt_usd(price), f"{change:+.2f}%", delta_color=kr_delta(change), border=True)
    k[1].metric("보유 수량", fmt_qty(pos.qty) if pos.qty else "—", border=True)
    k[2].metric("평균 단가", fmt_usd(pos.avg) if pos.qty else "—", border=True,
                help=f"정확한 평단 {fmt_usd(pos.avg, 4)}" if pos.qty else None)
    k[3].metric("평가 손익", fmt_usd(unreal) if pos.qty else "—",
                f"{unreal_pct:+.2f}%" if unreal_pct is not None else None,
                delta_color=kr_delta(unreal_pct), border=True)
    k[4].metric("진행 회차", f"{pos.buy_count}회" + (f" / {split_ref}" if split_ref else ""),
                help="현재 사이클에서 매수한 날짜 수 (같은 날 분할 체결은 1회)", border=True)
    k[5].metric("실현 손익", fmt_usd(pos.realized), f"{fmt_krw(pos.realized * fx_rate())}",
                delta_color=kr_delta(pos.realized), delta_arrow="off", border=True)
    if pos.unmatched_sell_qty > 0:
        st.warning(f"보유량보다 많은 매도 {pos.unmatched_sell_qty:,.4g}주가 있습니다. "
                   "집계 시작일 이전 매수가 빠졌거나 계좌 선택이 누락됐을 수 있습니다.", icon=":material/warning:")
    if not journal_ok:
        st.info("매매 일지를 읽지 못해 포지션 없이 표시합니다.")

    profile = core.volume_profile(data["Close"].tail(126), data["Volume"].tail(126))
    resistance, support = core.key_levels(profile, price)

    left, right = st.columns([7, 3], gap="medium")
    with right:
        with st.container(border=True):
            st.markdown("**목표가 설정**")
            target_pct = st.slider("목표 수익률 (%)", 1.0, 50.0, 13.0, 0.5, key="target_pct")
            target_price = pos.avg * (1 + target_pct / 100) if pos.qty > 0 else None
            if pos.qty > 0:
                profit = (target_price - pos.avg) * pos.qty
                kv_rows([("목표가", md_usd(target_price)), ("현재가 대비", f"{(target_price / price - 1) * 100:+.2f}%"),
                         ("예상 수익", f"{md_usd(profit)} · {fmt_krw(profit * fx_rate())}")])
                st.write("")
                st.markdown("**수익금 역산**")
                cur = st.segmented_control("통화", ["USD", "KRW"], default="KRW", key="rev_cur",
                                           label_visibility="collapsed") or "KRW"
                if cur == "KRW":
                    want = st.number_input("원하는 수익금 (₩)", min_value=1_000, value=1_000_000, step=100_000)
                    want_usd = want / fx_rate()
                else:
                    want_usd = st.number_input("원하는 수익금 ($)", min_value=1.0, value=500.0, step=50.0)
                need_price = pos.avg + want_usd / pos.qty
                kv_rows([("필요 매도가", md_usd(need_price)),
                         ("평단 대비", f"{(need_price / pos.avg - 1) * 100:+.2f}%"),
                         ("현재가 대비", f"{(need_price / price - 1) * 100:+.2f}%")])
            else:
                st.caption("진행 중인 매수 사이클이 없습니다. 매매 일지에 체결을 추가하면 목표가가 계산됩니다.")
        with st.container(border=True):
            st.markdown("**매물대 (최근 6개월)**")
            kv_rows([("최대 저항", md_usd(resistance) if resistance else "현재가 위 매물 없음"),
                     ("최대 지지", md_usd(support) if support else "현재가 아래 매물 없음"),
                     ("저항까지", f"{(resistance / price - 1) * 100:+.2f}%" if resistance else "—")])
    with left:
        c1, c2 = st.columns([3, 2], vertical_alignment="center")
        window = c1.segmented_control("기간", ["3M", "6M", "1Y", "2Y"], default="6M", key="chart_window",
                                      label_visibility="collapsed") or "6M"
        correct_time = c2.toggle("미국 현지일 보정", value=True, key="time_fix",
                                 help="한국 시각 09시 이전(새벽) 체결은 미국 현지 전일 거래로 표시합니다.")
        show_chart(price_chart(data, ticker, trades, pos, target_price, resistance, support, window, correct_time),
                   key="main_chart")

    # ---- AI 요원 매매 계획 ----
    section("AI 요원 매매 계획", "직전 종가 기준 지표로 계산합니다. 도달 확률은 최근 5년 실제 차트에서 "
                              "같은 상승폭을 20거래일 안에 고가로 터치한 비율(경험적 확률)입니다.")
    if not models:
        st.info("배치된 AI 요원이 없습니다. 훈련소에서 요원을 훈련·배치하세요.", icon=":material/smart_toy:")
    else:
        feats = core.compute_features(data["Close"], data["Volume"])
        last = feats[-1]
        long = load_long(ticker, "5y", False)
        hist = long if long is not None else data
        cols = st.columns(min(len(models), 3))
        for i, model in enumerate(models[:3]):
            plan = agent_plan(model, last, price, pos, budget_usd)
            prob, n_obs = core.touch_probability(hist["High"], hist["Close"],
                                                 (plan["target"] / price - 1) * 100, 20)
            with cols[i].container(border=True):
                rank_badge = pill("RANK " + str(model["rank"]), "info")
                st.markdown(f"{rank_badge} &nbsp;**{html.escape(model['name'])}**", unsafe_allow_html=True)
                m1, m2 = st.columns(2)
                m1.metric(f"매도 목표 (+{plan['target_r']:.1f}%)", fmt_usd(plan["target"]))
                m2.metric("20일 도달률", f"{prob:.0f}%" if np.isfinite(prob) else "—",
                          help=f"과거 {n_obs:,}개 시점 기준")
                kv_rows([("매수 지정가", f"{md_usd(plan['limit'])} (−{plan['discount']:.1f}%)"),
                         ("금일 투입", f"{md_usd(plan['amount'])} · 1회분 ×{plan['buy_mult']:.2f}"),
                         ("분할 / 진행", f"{plan['split']}분할 / {pos.buy_count}회"),
                         ("잔여 예산", md_usd(plan["remaining"]))])
                st.markdown(pill(plan["status"][0], plan["status"][1]), unsafe_allow_html=True)
                with st.popover("유전자 보기", icon=":material/genetics:", width="stretch"):
                    st.dataframe(pd.DataFrame({"항목": core.GENE_NAMES, "값": model["genes"]}),
                                 hide_index=True, column_config={"값": st.column_config.NumberColumn(format="%.3f")})
                    if model.get("trained_at"):
                        st.caption(f"{model.get('ticker', '')} · {model['trained_at']} 훈련")
        render_what_if(hist, price, pos, models[0], last, budget_usd, resistance)
    st.markdown('<div class="vh-foot">모든 수치는 과거 데이터 기반 참고 지표이며 미래 수익을 보장하지 않습니다.</div>',
                unsafe_allow_html=True)


def render_what_if(hist: pd.DataFrame, price: float, pos: core.Position, model: dict, last_feats,
                   budget_usd: float, resistance: float | None) -> None:
    section("커스텀 타점 시뮬레이터", "원하는 매도가가 과거 차트에서 얼마나 자주 도달했는지 역산합니다.")
    plan = agent_plan(model, last_feats, price, pos, budget_usd)
    with st.container(border=True):
        c1, c2 = st.columns([2, 1])
        default = round(max(price * 1.01, plan["target"]), 2)
        custom = c1.number_input("매도 목표가 (USD)", min_value=round(price * 0.5, 2), value=default, step=0.01,
                                 key=f"whatif_{round(default, 2)}")
        horizon = c2.selectbox("기간", [5, 10, 20, 60], index=2, format_func=lambda d: f"{d}거래일", key="whatif_h")
        rise = (custom / price - 1) * 100
        prob, n_obs = core.touch_probability(hist["High"], hist["Close"], rise, horizon)
        agent_prob, _ = core.touch_probability(hist["High"], hist["Close"], (plan["target"] / price - 1) * 100,
                                               horizon)
        m = st.columns(4)
        m[0].metric("평단 대비 수익률", f"{(custom / pos.avg - 1) * 100:+.2f}%" if pos.qty else "—")
        m[1].metric("필요 상승률", f"{rise:+.2f}%")
        m[2].metric(f"{horizon}일 내 도달 확률", f"{prob:.1f}%" if np.isfinite(prob) else "—",
                    f"{prob - agent_prob:+.1f}%p vs 1위 요원" if np.isfinite(prob) and np.isfinite(agent_prob) else None,
                    delta_color=kr_delta(prob - agent_prob) if np.isfinite(prob) and np.isfinite(agent_prob) else "off", help=f"과거 {n_obs:,}개 시점 중 도달 비율")
        m[3].metric("예상 수익", fmt_usd((custom - pos.avg) * pos.qty) if pos.qty else "—")
        if resistance is None:
            st.info("현재가 위쪽에 큰 매물대가 없습니다 (최근 6개월 신고가 영역).", icon=":material/trending_up:")
        elif custom > resistance * 1.03:
            st.error(f"최대 매물대 {md_usd(resistance)}를 돌파해야 하는 공격적인 타점입니다.", icon=":material/report:")
        elif custom >= resistance * 0.97:
            st.warning(f"최대 매물대 {md_usd(resistance)} 부근입니다. 매물 소화 과정에서 되밀릴 수 있습니다.",
                       icon=":material/warning:")
        else:
            st.success(f"최대 매물대 {md_usd(resistance)} 도달 전에 빠져나오는 보수적인 타점입니다.",
                       icon=":material/verified:")


# ==========================================================================
# 페이지 2: 시장 레이더
# ==========================================================================
def page_radar() -> None:
    page_header("MARKET RADAR", "시장 레이더", "미국 10년물 금리 · 반도체 핵심 종목 · 매크로 뉴스 센티먼트")
    section("미국 10년물 국채 금리", "레버리지 성장주의 할인율·차입비용과 관련된 핵심 거시 변수입니다.")
    tnx = load_treasury()
    if tnx is None:
        st.warning("금리 데이터를 불러오지 못했습니다.", icon=":material/cloud_off:")
    else:
        m = st.columns([1, 1, 1, 1.4])
        m[0].metric("현재 금리", f"{tnx['current']:.3f}%", f"{tnx['trend5']:+.3f}%p (5거래일)",
                    delta_color=kr_delta(-tnx["trend5"]), border=True)
        m[1].metric("6개월 고점", f"{tnx['peak']:.3f}%", f"{tnx['peak_date']:%Y-%m-%d}", delta_color="off",
                    delta_arrow="off", border=True)
        m[2].metric("고점 대비", f"-{tnx['drop_pct']:.1f}%", border=True)
        if tnx["drop_pct"] >= 10 and tnx["trend5"] < 0:
            status, kind = "금리 피크아웃 진행 · 성장주 우호적 환경", "ok"
        elif tnx["drop_pct"] >= 5:
            status, kind = "금리 하락 전환 시도 · 추가 확인 필요", "warn"
        else:
            status, kind = "고금리 유지/상승 · 레버리지 ETF 비용 부담", "stop"
        with m[3].container(border=True):
            st.markdown(f"**금리 환경 진단**<br>{pill(status, kind)}", unsafe_allow_html=True)
            st.caption("기준: 6개월 고점 대비 하락률과 최근 5거래일 방향")
        fig = go.Figure(go.Scatter(x=tnx["series"].index, y=tnx["series"], line=dict(color=ACCENT, width=2),
                                   fill="tozeroy", fillcolor="rgba(59,130,246,.08)", name="10Y"))
        fig.add_hline(y=tnx["peak"], line=dict(color=GOLD, dash="dot", width=1),
                      annotation_text="6개월 고점", annotation_font_color=GOLD)
        base_layout(fig, 240, legend=False)
        fig.update_yaxes(range=[tnx["series"].min() * 0.97, tnx["peak"] * 1.02], ticksuffix="%")
        show_chart(fig, key="tnx")
        with st.expander("금리와 3배 레버리지 반도체 ETF의 관계", icon=":material/menu_book:"):
            st.markdown("""
- **할인율 효과:** 무위험 금리가 오르면 미래 이익 비중이 큰 반도체 성장주의 밸류에이션 배수가 낮아지기 쉽습니다.
- **차입·스왑 비용:** 3배 레버리지 ETF는 노출의 상당 부분을 스왑·차입으로 만들기 때문에 단기 금리가 높을수록 보유 비용이 커집니다.
- **변동성 손실:** 일일 재조정 구조상 방향 없이 출렁이는 장에서는 지수가 제자리여도 ETF 가격이 녹아내릴 수 있습니다.
- **관전 포인트:** 고용 둔화, 근원 물가(CPI·PCE) 안정, 유가 안정 등은 금리 하락 압력으로 작용하는 경우가 많습니다.

과거에 금리 고점 이후 반도체 레버리지 ETF가 크게 반등한 사례가 있었지만, 상관관계는 시기마다 달라지며 인과를 보장하지 않습니다.
""")

    section("반도체 생태계 & 매크로 레이더", "Yahoo Finance 최신 헤드라인을 키워드로 분류합니다 (참고용 단순 규칙).")
    c1, c2, _ = st.columns([1, 1.4, 3], vertical_alignment="center")
    run = c1.button("레이더 스캔", type="primary", icon=":material/radar:", width="stretch")
    translate = c2.toggle("헤드라인 한국어 번역", value=True, key="radar_translate")
    if run:
        with st.spinner("시세와 뉴스를 수집하는 중…"):
            st.session_state["radar"] = {
                "semis": scan_group(tuple(SOXL_HOLDINGS), {t: f"{w}%" for t, w in SOXL_HOLDINGS.items()}),
                "macro": scan_group(tuple(MACRO_TICKERS), MACRO_TICKERS),
                "at": now_kst().strftime("%Y-%m-%d %H:%M"),
            }
    radar = st.session_state.get("radar")
    if not radar:
        st.caption("스캔 버튼을 누르면 결과가 이 화면에 유지됩니다.")
        return
    st.caption(f"마지막 스캔: {radar['at']} KST")
    t1, t2 = st.tabs(["반도체 Top 10", "매크로"])
    with t1:
        render_radar(radar["semis"], "비중(추정)", translate)
    with t2:
        render_radar(radar["macro"], "설명", translate)


def scan_group(tickers: tuple[str, ...], meta: dict[str, str]) -> list[dict]:
    snap = load_snapshot(tickers)
    rows = []
    for t in tickers:
        heads = fetch_headlines(t)
        label, score, pos, neg = core.analyze_sentiment(" ".join(h["title"] for h in heads))
        price, chg = snap.get(t, (np.nan, np.nan))
        rows.append({"종목": t, "meta": meta.get(t, ""), "현재가": price, "등락률": chg,
                     "센티먼트": label if heads else "뉴스 없음", "점수": score if heads else 0,
                     "대표 헤드라인": heads[0]["title"] if heads else "", "headlines": heads, "pos": pos, "neg": neg})
    return rows


def render_radar(rows: list[dict], meta_label: str, translate: bool) -> None:
    if not rows:
        st.info("데이터가 없습니다.")
        return
    df = pd.DataFrame(rows).rename(columns={"meta": meta_label})
    st.dataframe(df[["종목", meta_label, "현재가", "등락률", "센티먼트", "점수", "대표 헤드라인"]], hide_index=True,
                 column_config={"현재가": st.column_config.NumberColumn(format="$%.2f"),
                                "등락률": st.column_config.NumberColumn(format="%+.2f%%"),
                                "점수": st.column_config.NumberColumn(format="%+d"),
                                "대표 헤드라인": st.column_config.TextColumn(width="large")})
    for row in rows:
        if not row["headlines"]:
            continue
        kind = {"호재": "▲", "악재": "▼"}.get(row["센티먼트"], "—")
        chg = f"{row['등락률']:+.2f}%" if np.isfinite(row["등락률"]) else "—"
        with st.expander(f"{kind} {row['종목']} · {row['센티먼트']} · {chg}"):
            reasons = []
            if row["pos"]:
                reasons.append(f"호재 키워드: {', '.join(row['pos'])}")
            if row["neg"]:
                reasons.append(f"악재 키워드: {', '.join(row['neg'])}")
            st.caption(" · ".join(reasons) or "매칭된 키워드 없음")
            for h in row["headlines"]:
                link = f' <a href="{h["link"]}" target="_blank" rel="noopener">원문</a>' if h["link"].startswith("http") else ""
                pub = f' <span style="color:#64748B">· {html.escape(h["publisher"])}</span>' if h["publisher"] else ""
                st.markdown(f"{core.highlight_keywords(h['title'])}{pub}{link}", unsafe_allow_html=True)
                if translate:
                    ko = translate_ko(h["title"])
                    st.caption(ko or "번역 서버 응답 없음")


# ==========================================================================
# 페이지 3: AI 리그
# ==========================================================================
def page_league() -> None:
    page_header("VALHALLA LEAGUE", "AI 실전 리그",
                "배치된 요원을 실제 일봉에 훈련과 동일한 규칙으로 적용한 가상 매매 성적")
    models = load_models()
    if not models:
        st.info("배치된 AI 요원이 없습니다.", icon=":material/smart_toy:")
        st.page_link(PAGES["training"], label="훈련소로 이동", icon=":material/arrow_forward:")
        return

    section("참전 요원")
    st.dataframe(pd.DataFrame([{
        "순위": m["rank"], "요원": m["name"], "훈련 종목": m.get("ticker", "—"), "훈련일": m.get("trained_at", "—"),
        "학습 평균수익": pct_text(m.get("train_ret")), "검증 수익": pct_text(m.get("test_ret"))} for m in models]),
        hide_index=True)

    section("리그 설정", "매수는 전일 종가 기준 지정가, 매도는 평단 기준 목표가 지정가로 체결을 가정합니다. "
                     "같은 날 여러 번 실행해도 결과가 중복되지 않습니다.")
    trained = [m.get("trained_at") for m in models if m.get("trained_at")]
    default_start = dt.date.fromisoformat(min(trained)) if trained else today_kst() - dt.timedelta(days=180)
    c = st.columns(4)
    ticker = c[0].text_input("종목", value=models[0].get("ticker", "SOXL"), key="league_ticker").upper().strip()
    start = c[1].date_input("리그 시작일", value=default_start, max_value=today_kst(), key="league_start")
    capital = c[2].number_input("요원별 자본 ($)", min_value=1_000.0, value=10_000.0, step=1_000.0, key="league_cap")
    fee_bps = c[3].number_input("편도 비용 (bp)", min_value=0, max_value=100, value=10, key="league_fee",
                                help="수수료 + 슬리피지. 키움 해외주식 기본 수수료는 약 0.07~0.25% 수준입니다.")
    with st.spinner("리그 계산 중…"):
        result = run_league(ticker, start.isoformat(), float(capital), float(fee_bps),
                            tuple(tuple(m["genes"]) for m in models), tuple(m["name"] for m in models))
    if result is None:
        st.error("시세가 없거나 시작일 이후 데이터가 부족합니다.", icon=":material/cloud_off:")
        return

    section("리더보드", f"기간 {result['start']} ~ {result['end']} · AI는 리그 기간, 사령관은 매매 일지 전체 기준입니다.")
    board = result["summary"].copy()
    board["구분"] = "AI"
    journal, _ = load_table("trade_journal")
    holdings = core.positions_by_account(journal)
    if not holdings.empty:
        cmd_unreal = 0.0
        for row in holdings[holdings["Qty"] > 0].itertuples():
            px = load_recent(row.Ticker)
            if px is not None:
                cmd_unreal += (float(px["Close"].iloc[-1]) - row.Avg) * row.Qty
        cmd_budget = st.session_state.get("budget_krw", 10_000_000) / fx_rate()
        cmd_pnl = holdings["Realized"].sum() + cmd_unreal
        board = pd.concat([board, pd.DataFrame([{
            "요원": "사령관 (실계좌)", "구분": "사령관", "총자산": cmd_budget + cmd_pnl,
            "수익률(%)": cmd_pnl / cmd_budget * 100, "실현손익": holdings["Realized"].sum(),
            "평가손익": cmd_unreal, "MDD(%)": np.nan, "체결수": len(journal), "보유수량": np.nan}])],
            ignore_index=True)
    board = board.sort_values("수익률(%)", ascending=False).reset_index(drop=True)
    board.insert(0, "순위", range(1, len(board) + 1))
    best_ret = float(board["수익률(%)"].iloc[0])
    m = st.columns(4)
    m[0].metric("1위", board["요원"].iloc[0], f"{best_ret:+.2f}%", delta_color=kr_delta(best_ret), border=True)
    m[1].metric("Buy & Hold", f"{result['bh_ret']:+.2f}%", f"MDD {result['bh_mdd']:.1f}%", delta_color="off",
                delta_arrow="off", border=True)
    m[2].metric("기준 종가", fmt_usd(result["last_close"]), result["end"], delta_color="off", delta_arrow="off",
                border=True)
    m[3].metric("AI 총 체결", f"{int(result['summary']['체결수'].sum()):,}건", border=True)
    money = st.column_config.NumberColumn(format="$%,.2f")
    st.dataframe(board[["순위", "요원", "구분", "수익률(%)", "총자산", "실현손익", "평가손익", "MDD(%)", "체결수"]],
                 hide_index=True, column_config={
                     "수익률(%)": st.column_config.NumberColumn(format="%+.2f%%"), "총자산": money,
                     "실현손익": money, "평가손익": money, "MDD(%)": st.column_config.NumberColumn(format="%.1f%%")})

    curve = result["curve"] / capital * 100
    fig = go.Figure()
    for i, col in enumerate(curve.columns):
        is_bh = col == "Buy & Hold"
        fig.add_trace(go.Scatter(x=curve.index, y=curve[col], name=col,
                                 line=dict(color=NEUTRAL if is_bh else SERIES[i % len(SERIES)], width=1.5 if is_bh else 2.2,
                                           dash="dot" if is_bh else "solid")))
    fig.add_hline(y=100, line=dict(color="rgba(148,163,184,.4)", dash="dash", width=1))
    base_layout(fig, 360)
    fig.update_yaxes(title_text="자산 (시작=100)")
    show_chart(fig, key="league_curve")

    section("가상 매매 장부 저장", "계산된 체결 내역으로 이 종목의 AI 장부를 교체합니다 (다른 종목 기록은 유지).")
    trades = result["trades"]
    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    if c1.button("장부에 정산 저장", icon=":material/save:", type="primary", width="stretch"):
        try:
            ledger, _ = load_table("ai_ledger", strict=True)
            save_table("ai_ledger", pd.concat([ledger[ledger["Ticker"] != ticker], trades], ignore_index=True))
            flash(f"{ticker} 리그 체결 {len(trades):,}건을 장부에 저장했습니다.")
            st.rerun()
        except Exception as exc:
            LOGGER.exception("장부 저장 실패")
            st.error(f"저장 실패: {exc}")
    c2.caption(f"이번 계산의 체결 {len(trades):,}건")
    with st.expander("체결 내역 미리보기", icon=":material/receipt_long:"):
        st.dataframe(trades.sort_values("Date", ascending=False), hide_index=True,
                     column_config={"Price": st.column_config.NumberColumn(format="$%.4f"),
                                    "Profit": st.column_config.NumberColumn(format="$%,.2f")})


# ==========================================================================
# 페이지 4: 훈련소
# ==========================================================================
def page_training() -> None:
    page_header("BOOTCAMP", "AI 훈련소", "유전 알고리즘 (선택 · 교차 · 변이) + 부트스트랩 시나리오 + 미사용 구간 검증")
    with st.form("train_form", border=True):
        c = st.columns(4)
        ticker = c[0].text_input("훈련 종목", "SOXL").upper().strip()
        years = c[1].selectbox("학습 데이터", [2, 3, 5], index=1, format_func=lambda y: f"최근 {y}년")
        population = c[2].slider("개체 수", 50, 1000, 200, 50)
        generations = c[3].slider("세대 수", 5, 50, 15)
        c = st.columns(4)
        n_scen = c[0].slider("평가 시나리오 수", 1, 12, 6, help="실제 차트 1개 + 부트스트랩 가상 미래 (평균으로 평가)")
        mc_days = c[1].slider("가상 미래 길이 (일)", 50, 400, 200, 50)
        fee_bps = c[2].number_input("편도 비용 (bp)", 0, 100, 10)
        penalty = c[3].slider("낙폭 페널티", 0.0, 2.0, 0.5, 0.1, help="적합도 = 평균수익률 − 페널티 × 평균 MDD")
        c = st.columns(4)
        train_ratio = c[0].slider("학습 구간 비율", 0.6, 0.9, 0.8, 0.05, help="나머지 뒤쪽 구간은 검증 전용")
        seed = c[1].number_input("난수 시드", 0, 99_999, 42, help="같은 시드 = 같은 결과 (재현 가능)")
        include = c[2].toggle("현재 요원을 초기 개체에 포함", value=True)
        submitted = c[3].form_submit_button("훈련 시작", type="primary", icon=":material/play_arrow:", width="stretch")

    if submitted:
        history = load_long(ticker, f"{years}y", False)
        if history is None:
            st.error(f"'{ticker}' 시세를 불러오지 못했습니다.")
        else:
            bar = st.progress(0.0, text="훈련 준비 중…")

            def on_progress(gen: int, total: int, best: float) -> None:
                bar.progress(gen / total, text=f"{gen}/{total} 세대 · 최고 적합도 {best:,.2f}")

            seeds = [m["genes"] for m in load_models()] if include else None
            try:
                result = core.run_evolution(history, population, generations, n_scen, mc_days, train_ratio,
                                            fee_bps, 10_000.0, penalty, int(seed), seeds, on_progress)
                st.session_state["train_result"] = {**result, "ticker": ticker,
                                                    "trained_at": today_kst().isoformat()}
                bar.empty()
            except ValueError as exc:
                bar.empty()
                st.error(str(exc))

    result = st.session_state.get("train_result")
    if not result:
        st.caption("설정 후 '훈련 시작'을 누르세요. 기본 설정 기준 수 초~수십 초가 걸립니다.")
        return

    section("훈련 결과", f"{result['ticker']} · 학습 {result['train_period'][0]} ~ {result['train_period'][1]} · "
                     f"검증 {result['test_period'][0]} ~ {result['test_period'][1]} · 시나리오 {result['n_scenarios']}개")
    top = pd.DataFrame(result["top"])
    m = st.columns(4)
    m[0].metric("1위 적합도", f"{top['fitness'].iloc[0]:,.2f}", border=True)
    m[1].metric("1위 학습 평균수익", f"{top['train_ret'].iloc[0]:+.1f}%", border=True)
    m[2].metric("1위 검증 수익", f"{top['test_ret'].iloc[0]:+.1f}%",
                f"{top['test_ret'].iloc[0] - result['buy_hold_test_ret']:+.1f}%p vs 보유",
                delta_color=kr_delta(top["test_ret"].iloc[0] - result["buy_hold_test_ret"]), border=True)
    m[3].metric("검증 구간 Buy & Hold", f"{result['buy_hold_test_ret']:+.1f}%",
                f"MDD {result['buy_hold_test_mdd']:.1f}%", delta_color="off", delta_arrow="off", border=True)
    if (top["test_ret"] < 0).all():
        st.warning("상위 요원 모두 검증 구간에서 손실입니다. 학습 구간에 과적합됐을 가능성이 큽니다.", icon=":material/warning:")

    left, right = st.columns([3, 2], gap="medium")
    with left:
        prog = result["progress"]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=prog["세대"], y=prog["최고 적합도"], name="최고", line=dict(color=ACCENT, width=2.4)))
        fig.add_trace(go.Scatter(x=prog["세대"], y=prog["평균 적합도"], name="평균", line=dict(color=NEUTRAL, dash="dot")))
        base_layout(fig, 300)
        fig.update_layout(title=dict(text="세대별 적합도", font=dict(size=13)))
        show_chart(fig, key="train_curve")
    with right:
        table = top.rename(columns={"rank": "순위", "fitness": "적합도", "train_ret": "학습수익(%)",
                                    "train_mdd": "학습MDD(%)", "train_worst": "최악시나리오(%)",
                                    "test_ret": "검증수익(%)", "test_mdd": "검증MDD(%)", "test_trades": "검증체결"})
        pct = st.column_config.NumberColumn(format="%+.1f")
        st.dataframe(table.drop(columns=["genes"]), hide_index=True, column_config={
            "적합도": st.column_config.NumberColumn(format="%.2f"), "학습수익(%)": pct, "최악시나리오(%)": pct,
            "검증수익(%)": pct, "학습MDD(%)": st.column_config.NumberColumn(format="%.1f"),
            "검증MDD(%)": st.column_config.NumberColumn(format="%.1f")})
        with st.expander("상위 3명 유전자", icon=":material/genetics:"):
            genes = pd.DataFrame([r["genes"] for r in result["top"]], columns=core.GENE_NAMES,
                                 index=[f"Rank {r['rank']}" for r in result["top"]]).T
            st.dataframe(genes, column_config={c: st.column_config.NumberColumn(format="%.3f") for c in genes.columns})

    section("실전 배치", "상위 3명이 기존 요원을 대체하고, AI 가상 매매 장부는 초기화됩니다.")
    c1, c2 = st.columns([2, 1], vertical_alignment="center")
    ok = c1.checkbox("기존 요원 교체와 AI 장부 초기화에 동의합니다.", key="deploy_ok")
    if c2.button("상위 3명 배치", type="primary", disabled=not ok, icon=":material/rocket_launch:", width="stretch"):
        models = [{"name": f"Agent_Rank_{r['rank']} ({result['ticker']})", "rank": r["rank"], "genes": r["genes"],
                   "ticker": result["ticker"], "trained_at": result["trained_at"],
                   "train_ret": round(r["train_ret"], 2), "test_ret": round(r["test_ret"], 2)}
                  for r in result["top"]]
        try:
            save_models(models)
            save_table("ai_ledger", core.empty_table("ai_ledger"))
            run_league.clear()
            flash("상위 3명이 실전 리그에 배치되었습니다.", ":material/rocket_launch:")
            st.session_state.pop("deploy_ok", None)
            st.rerun()
        except Exception as exc:
            LOGGER.exception("배치 실패")
            st.error(f"배치 실패: {exc}")


# ==========================================================================
# 페이지 5: 매매 일지
# ==========================================================================
def page_journal() -> None:
    page_header("TRADE JOURNAL", "매매 일지", "체결 입력 · 편집 · 백업 · 초기화")
    journal, ok = load_table("trade_journal")
    holdings = core.positions_by_account(journal)
    m = st.columns(4)
    m[0].metric("총 체결", f"{len(journal):,}건", border=True)
    m[1].metric("종목 / 계좌", f"{journal['Ticker'].nunique()} / {journal['Account'].nunique()}", border=True)
    realized = float(holdings["Realized"].sum()) if not holdings.empty else 0.0
    m[2].metric("누적 실현 손익", fmt_usd(realized), fmt_krw(realized * fx_rate()), delta_color=kr_delta(realized),
                delta_arrow="off", border=True)
    open_pos = holdings[holdings["Qty"] > 0] if not holdings.empty else holdings
    m[3].metric("보유 중 포지션", f"{len(open_pos)}개", border=True)
    if not open_pos.empty:
        with st.expander("계좌·종목별 보유 현황", icon=":material/account_balance_wallet:"):
            st.dataframe(open_pos, hide_index=True, column_config={
                "Avg": st.column_config.NumberColumn("평단", format="$%.4f"),
                "Cost": st.column_config.NumberColumn("원가", format="$%,.2f"),
                "Realized": st.column_config.NumberColumn("실현손익", format="$%,.2f"),
                "Qty": st.column_config.NumberColumn("수량", format="%.4g")})

    tabs = st.tabs(["체결 입력", "일지 편집", "AI 가상 장부", "백업 · 초기화"])
    with tabs[0]:
        with st.form("trade_form", clear_on_submit=True, border=True):
            c = st.columns(4)
            t_date = c[0].date_input("체결일 (KST)", today_kst())
            t_time = c[1].text_input("체결 시각 (KST)", now_kst().strftime("%H:%M:%S"))
            t_account = c[2].text_input("계좌", "Manual")
            t_ticker = c[3].text_input("티커", "SOXL")
            c = st.columns(4)
            t_action = c[0].segmented_control("구분", ["Buy", "Sell"], default="Buy",
                                              format_func=lambda a: "매수" if a == "Buy" else "매도")
            t_price = c[1].number_input("체결 단가 ($)", min_value=0.0001, value=None, format="%.4f",
                                        placeholder="예: 32.1500")
            t_qty = c[2].number_input("수량 (주)", min_value=0.0001, value=1.0, step=1.0, format="%.4f")
            saved = c[3].form_submit_button("일지에 저장", type="primary", icon=":material/add:", width="stretch")
        if saved:
            row = pd.DataFrame([{"Date": t_date.isoformat(), "Time": t_time, "Account": t_account,
                                 "Ticker": t_ticker, "Action": t_action or "", "Qty": t_qty, "Price": t_price,
                                 "Format": "manual"}])
            try:
                append_rows("trade_journal", row)
                flash(f"{t_ticker.upper()} {'매수' if t_action == 'Buy' else '매도'} {t_qty:g}주 저장 완료")
                st.rerun()
            except ValueError as exc:
                st.error(f"입력값을 확인하세요: {exc}", icon=":material/error:")
            except Exception as exc:
                LOGGER.exception("저장 실패")
                st.error(f"저장 실패: {exc}")

    with tabs[1]:
        if not ok:
            st.error("일지를 읽지 못해 편집을 잠갔습니다 (빈 데이터로 덮어쓰기 방지).")
        else:
            st.caption("셀을 직접 고치거나 행을 추가/삭제한 뒤 저장하세요. 저장 전 전체 행을 검증합니다.")
            edited = st.data_editor(core.sort_trades(journal), num_rows="dynamic", hide_index=True, key="journal_editor",
                                    column_config={
                                        "Action": st.column_config.SelectboxColumn("구분", options=["Buy", "Sell"], required=True),
                                        "Qty": st.column_config.NumberColumn("수량", min_value=0.0, format="%.4g"),
                                        "Price": st.column_config.NumberColumn("단가", min_value=0.0, format="$%.4f"),
                                        "Date": st.column_config.TextColumn("체결일", help="YYYY-MM-DD"),
                                        "Time": st.column_config.TextColumn("시각", help="HH:MM:SS (KST)")})
            c1, c2, _ = st.columns([1, 1, 3])
            if c1.button("변경 내용 저장", type="primary", icon=":material/save:", width="stretch"):
                try:
                    n = save_table("trade_journal", edited)
                    flash(f"일지 {n:,}건 저장 완료")
                    st.rerun()
                except ValueError as exc:
                    st.error(f"저장하지 않았습니다 — {exc}", icon=":material/error:")
                except Exception as exc:
                    LOGGER.exception("일지 저장 실패")
                    st.error(f"저장 실패: {exc}")
            c2.download_button("CSV 백업", journal.to_csv(index=False).encode("utf-8-sig"),
                               f"trade_journal_{today_kst():%Y%m%d}.csv", "text/csv", icon=":material/download:",
                               width="stretch", on_click="ignore")

    with tabs[2]:
        ledger, _ = load_table("ai_ledger")
        if ledger.empty:
            st.caption("AI 장부가 비어 있습니다. 실전 리그 화면에서 '장부에 정산 저장'을 실행하세요.")
        else:
            summary = ledger.groupby(["Agent", "Ticker"], as_index=False).agg(
                체결=("Action", "size"), 실현손익=("Profit", "sum"))
            st.dataframe(summary, hide_index=True,
                         column_config={"실현손익": st.column_config.NumberColumn(format="$%,.2f")})
            st.dataframe(ledger.sort_values("Date", ascending=False), hide_index=True,
                         column_config={"Price": st.column_config.NumberColumn(format="$%.4f"),
                                        "Profit": st.column_config.NumberColumn(format="$%,.2f")})
            st.download_button("장부 CSV 백업", ledger.to_csv(index=False).encode("utf-8-sig"),
                               f"ai_ledger_{today_kst():%Y%m%d}.csv", "text/csv", icon=":material/download:",
                               on_click="ignore")

    with tabs[3]:
        st.caption("초기화 전에 'CSV 백업'으로 내려받아 두는 것을 권장합니다. 아래 작업은 되돌릴 수 없습니다.")
        danger_action("매매 일지 전체 삭제", "reset_journal",
                      lambda: save_table("trade_journal", core.empty_table("trade_journal")))
        danger_action("AI 가상 장부 초기화", "reset_ledger",
                      lambda: save_table("ai_ledger", core.empty_table("ai_ledger")))

        def wipe_models():
            save_models([])
            run_league.clear()
        danger_action("배치된 AI 요원 전체 삭제", "reset_models", wipe_models)


def danger_action(label: str, key: str, action) -> None:
    with st.container(border=True):
        c1, c2, c3 = st.columns([2.2, 1.6, 1.2], vertical_alignment="center")
        c1.markdown(f"**{label}**")
        ok = c2.checkbox("확인했습니다", key=f"{key}_ok")
        if c3.button("실행", key=f"{key}_btn", disabled=not ok, icon=":material/delete_forever:", width="stretch"):
            try:
                action()
                st.session_state.pop(f"{key}_ok", None)
                flash(f"{label} 완료", ":material/delete:")
                st.rerun()
            except Exception as exc:
                LOGGER.exception("%s 실패", label)
                st.error(f"{label} 실패: {exc}")


# ==========================================================================
# 페이지 6: 예측 검증실
# ==========================================================================
def page_validation() -> None:
    page_header("VALIDATION LAB", "예측 검증실",
                "단순 추세 모델을 '그 시점까지의 데이터'만으로 평가하는 워크포워드 검증 (최근 5년)")
    with st.container(border=True):
        c = st.columns([1, 1, 1, 1], vertical_alignment="bottom")
        ticker = c[0].text_input("검증 티커", "SOXL", key="val_ticker").upper().strip()
        horizon = c[1].selectbox("예측·보유 기간", [5, 10, 20, 60], index=2, format_func=lambda x: f"{x}거래일")
        fee = c[2].number_input("편도 비용 (bp)", 0, 100, 10, key="val_fee")
        run = c[3].button("검증 실행", type="primary", icon=":material/science:", width="stretch")
    if run:
        with st.spinner("워크포워드 검증 중…"):
            res = cached_walk_forward(ticker, horizon, int(fee))
        if res is None:
            st.error("검증에 필요한 가격 데이터가 부족하거나 불러오지 못했습니다.")
        else:
            st.session_state["validation"] = {**res, "ticker": ticker}
    v = st.session_state.get("validation")
    if not v:
        st.caption("티커와 기간을 고른 뒤 '검증 실행'을 누르세요.")
        return
    h = v["horizon"]
    section(f"{v['ticker']} · {h}거래일 예측 성능")
    m = st.columns(4)
    edge = v["hit_rate"] - v["always_up_rate"]
    m[0].metric("방향 적중률", f"{v['hit_rate']:.1f}%", f"{edge:+.1f}%p vs '항상 상승'",
                delta_color="normal", help=f"'항상 상승'이라고만 예측해도 {v['always_up_rate']:.1f}% 적중")
    mae_gap = v["model_mae"] - v["naive_mae"]
    m[1].metric("모델 평균 오차", fmt_usd(v["model_mae"]), f"{mae_gap:+.2f} vs 기준선(현재가 유지)",
                delta_color="inverse")
    m[2].metric("전략 누적수익", f"{v['strategy_total']:+.1f}%", f"보유 {v['buyhold_total']:+.1f}%",
                delta_color="off", delta_arrow="off")
    m[3].metric("전략 MDD", f"{v['mdd']:.1f}%", f"보유 {v['buyhold_mdd']:.1f}%", delta_color="off", delta_arrow="off")
    verdict = []
    if edge <= 0:
        verdict.append("방향 적중률이 '항상 상승' 기준보다 높지 않습니다")
    if mae_gap >= 0:
        verdict.append("가격 오차가 '현재가 유지' 기준선보다 크거나 같습니다")
    if verdict:
        st.warning(" · ".join(verdict) + " → 이 신호에 의존할 근거가 약합니다.", icon=":material/warning:")
    else:
        st.success("두 기준선을 모두 이겼습니다. 다만 과거 성과는 미래를 보장하지 않습니다.", icon=":material/verified:")
    st.caption(f"평가 구간 {v['periods']}회 중 {v['exposures']}회 보유 · 비용 반영 구간당 평균 수익률 {v['avg_return']:+.2f}%")

    fig = go.Figure()
    t = v["trades"]
    fig.add_trace(go.Scatter(x=t.index, y=t["strategy_equity"], name="추세 전략", line=dict(color=ACCENT, width=2.4)))
    fig.add_trace(go.Scatter(x=t.index, y=t["buyhold_equity"], name="Buy & Hold", line=dict(color=NEUTRAL, dash="dot")))
    fig.add_hline(y=1, line=dict(color="rgba(148,163,184,.4)", dash="dash", width=1))
    base_layout(fig, 340)
    fig.update_yaxes(title_text="누적 자본 배수", type="log")
    show_chart(fig, key="val_curve")

    section(f"{h}거래일 후 가격 분포", "최근 1년 실제 일간 수익률을 재표집(부트스트랩)한 1만 회 시뮬레이션")
    p = st.columns(4)
    p[0].metric("현재가", fmt_usd(v["current_price"]))
    p[1].metric("중앙값", fmt_usd(v["median"]), f"{(v['median'] / v['current_price'] - 1) * 100:+.1f}%",
                delta_color=kr_delta(v["median"] - v["current_price"]))
    p[2].metric("50% 구간", f"{fmt_usd(v['p25'])} – {fmt_usd(v['p75'])}")
    p[3].metric("90% 구간", f"{fmt_usd(v['p05'])} – {fmt_usd(v['p95'])}")
    st.info(f"현재가보다 낮을 확률 추정치 {v['loss_probability']:.1f}%. 과거 수익률을 재표집한 결과이며 미래를 보장하지 않습니다.",
            icon=":material/info:")
    with st.expander("측정 방법과 한계", icon=":material/menu_book:"):
        st.markdown(f"""
- **기준선 ①:** 미래 가격 = 현재 가격 (가격 오차 비교용)
- **기준선 ②:** '항상 상승'으로 예측 (방향 적중률 비교용)
- **모델:** 직전 60거래일 로그 추세를 다음 {h}거래일로 연장 (±30% 상한)
- **검증:** 각 시점에는 그날까지의 가격만 사용, 보유 기간마다 한 번만 거래 (중복 표본 방지)
- **분포:** 정규분포 대신 실제 수익률을 재표집해 급락(두꺼운 꼬리)을 반영합니다. 그래도 레버리지 ETF는 오차가 클 수 있습니다.
""")


# ==========================================================================
# 진입점
# ==========================================================================
PAGES: dict[str, st.Page] = {}


def main() -> None:
    st.set_page_config(page_title="Valhalla 관제소", page_icon=":material/monitoring:", layout="wide",
                       initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)
    init_local_storage()
    PAGES.update({
        "dashboard": st.Page(page_dashboard, title="전술 관제", icon=":material/monitoring:", url_path="dashboard", default=True),
        "radar": st.Page(page_radar, title="시장 레이더", icon=":material/radar:", url_path="radar"),
        "league": st.Page(page_league, title="AI 실전 리그", icon=":material/emoji_events:", url_path="league"),
        "training": st.Page(page_training, title="AI 훈련소", icon=":material/model_training:", url_path="training"),
        "journal": st.Page(page_journal, title="매매 일지", icon=":material/menu_book:", url_path="journal"),
        "validation": st.Page(page_validation, title="예측 검증실", icon=":material/science:", url_path="validation"),
    })
    nav = st.navigation({"운용": [PAGES["dashboard"], PAGES["radar"], PAGES["league"]],
                         "연구 · 관리": [PAGES["training"], PAGES["validation"], PAGES["journal"]]})
    render_sidebar()
    show_flash()
    nav.run()


if __name__ == "__main__":
    main()
