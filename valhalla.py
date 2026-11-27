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

import importlib

import valhalla_core as core

# Streamlit Cloud는 코드가 바뀌어도 이미 불러온 모듈을 메모리에 남겨 둘 수 있습니다.
# 새 함수가 없으면(= 예전 버전) 계산 모듈을 다시 불러옵니다.
REQUIRED_CORE_VERSION = "2026.11.28-1"
if getattr(core, "CORE_VERSION", None) != REQUIRED_CORE_VERSION:
    core = importlib.reload(core)

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
    return core.clean_ohlcv(raw)


def _expected_session() -> pd.Timestamp:
    return core.expected_session(dt.datetime.now(ET))


def _download(ticker: str, period: str, adjusted: bool) -> pd.DataFrame | None:
    try:
        raw = yf.download(ticker, period=period, interval="1d", auto_adjust=adjusted,
                          progress=False, threads=False)
    except Exception:
        LOGGER.exception("시세 다운로드 실패: %s", ticker)
        return None
    df = _clean_ohlcv(raw)
    # 야후는 장 마감 뒤 몇 시간 동안 그날 일봉 종가를 비워 보내기도 함 → 30분봉으로 빠진 날을 채움
    if df is not None and df.index[-1] < _expected_session():
        try:
            intr = _clean_ohlcv(yf.download(ticker, period="5d", interval="30m", prepost=False,
                                            auto_adjust=False, progress=False, threads=False))
            df = core.fill_missing_days(df, core.intraday_to_daily(intr))
        except Exception:
            LOGGER.exception("분봉 보정 실패: %s", ticker)
    return df


@st.cache_data(ttl=60, show_spinner=False)
def load_recent(ticker: str) -> pd.DataFrame | None:
    return _download(ticker, "2y", False)


@st.cache_data(ttl=3600, show_spinner=False)
def load_long(ticker: str, period: str = "5y", adjusted: bool = False) -> pd.DataFrame | None:
    return _download(ticker, period, adjusted)


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def load_fred(series_id: str) -> pd.Series | None:
    """미국 연준(FRED) 공개 CSV. 실패하면 None."""
    try:
        df = pd.read_csv(core.FRED_CSV.format(sid=series_id), na_values=".")
        df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0])
        return pd.to_numeric(df.set_index(df.columns[0]).iloc[:, 0], errors="coerce").dropna()
    except Exception:
        LOGGER.exception("FRED %s 불러오기 실패", series_id)
        return None


@st.cache_data(ttl=3 * 3600, show_spinner=False)
def load_check_inputs() -> dict:
    """반등 체크리스트 입력: 10년물·VIX(야후), 신용 스프레드·하이일드·근원 PCE(FRED)."""
    def close(t):
        d = load_long(t, "max", False)
        return d["Close"].dropna() if d is not None and not d.empty else pd.Series(dtype=float)
    return {"tnx": close("^TNX"), "vix": close("^VIX"),
            "credit": load_fred(core.FRED_SERIES["baa"]), "hy": load_fred(core.FRED_SERIES["hy"]),
            "core_pce": load_fred(core.FRED_SERIES["core_pce"])}


def render_rebound_checklist() -> None:
    section("반등 체크리스트", "반도체 반등이 '진짜'인지 볼 때 함께 확인할 거시 지표입니다. 기준선은 고정해 두고 매일 자동으로 갱신돼요. "
                        "이 체크리스트는 선수 경기장의 반등 판별전에 '거시 체크리스트' 선수로도 참가해 실력을 검증받습니다.")
    data = load_check_inputs()
    if data["tnx"].empty or data["vix"].empty:
        st.caption("지표 데이터를 불러오지 못했습니다.")
        return
    credit = data["credit"] if data["credit"] is not None else pd.Series(dtype=float)
    rows = core.checklist_snapshot(data["tnx"], data["vix"], credit, data["hy"], data["core_pce"])
    label = {"ok": "🟢 좋음", "warn": "🟡 주의", "stop": "🔴 부담"}
    st.dataframe(pd.DataFrame([{**r, "상태": label[r["상태"]]} for r in rows])[["지표", "상태", "현재", "기준", "변화", "설명"]],
                 hide_index=True, width="stretch")
    if not credit.empty:
        ck = core.check_conditions(data["tnx"], data["vix"], credit).dropna(subset=["prob"])
        if not ck.empty:
            last = ck.iloc[-1]
            st.caption(f"거시 체크리스트 선수의 지금 판단: 4개 조건 중 {int(last['met'])}개 충족 → 추세 전환 확률 "
                       f"{last['prob'] * 100:.0f}% (조건: 10년물 4.5% 아래 · 10년물 한 달 전보다 낮음 · VIX 30 아래 · "
                       "신용 스프레드 3개월 전보다 좁음). 하이일드 스프레드와 PCE는 참고 표시만 하고 선수 계산에는 넣지 않아요.")
    ev = core.upcoming_events(pd.Timestamp(now_kst().replace(tzinfo=None)))
    if not ev.empty:
        st.markdown("**다가오는 일정 (한국 시간)**")
        st.dataframe(pd.DataFrame({"날짜": ev["when"].dt.strftime("%m/%d(%a) %H:%M").str.replace("Mon", "월").str.replace(
            "Tue", "화").str.replace("Wed", "수").str.replace("Thu", "목").str.replace("Fri", "금").str.replace(
            "Sat", "토").str.replace("Sun", "일"),
            "일정": ev["일정"], "확정": ev["확정"].map({True: "확정", False: "미확정"}), "메모": ev["메모"]}),
            hide_index=True, width="stretch")
        st.caption("미확정 일정은 요청·예정 단계라 바뀔 수 있어요. 바뀌면 Claude에게 알려주시면 고쳐 둘게요.")


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
    df = load_long(ticker, core.league_period(start), False)
    if df is None:
        return None
    return core.league_run(df, start, capital, fee_bps, list(genes), list(names), ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_rolling(ticker: str, period: str, window: int, fee_bps: float,
                   genes: tuple[tuple[float, ...], ...], names: tuple[str, ...]):
    df = load_long(ticker, period, False)
    if df is None:
        return None, None
    return core.rolling_validation(df, list(genes), list(names), window, 10_000.0, fee_bps)


@st.cache_data(ttl=60, show_spinner=False)
def load_job_runs(backend_name: str) -> pd.DataFrame:
    if backend_name != "supabase":
        return pd.DataFrame()
    try:
        rows = get_supabase().table("valhalla_job_runs").select("*").order("id", desc=True).limit(10).execute().data
        return pd.DataFrame(rows or [])
    except Exception:
        LOGGER.warning("자동 실행 기록 조회 실패 (테이블 없음?)")
        return pd.DataFrame()


def render_rolling(table: pd.DataFrame, summary: pd.DataFrame, key: str) -> None:
    """기간별 안정성 검증 결과 표시."""
    if table is None or table.empty:
        st.info("검증할 기간이 부족합니다.")
        return
    pct = st.column_config.NumberColumn(format="%+.1f")
    st.dataframe(summary, hide_index=True, column_config={
        "수익 구간(%)": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100),
        "보유 대비 우세(%)": st.column_config.ProgressColumn(format="%d%%", min_value=0, max_value=100),
        "평균 수익(%)": pct, "최악 구간(%)": pct, "평균 MDD(%)": st.column_config.NumberColumn(format="%.1f")})
    names = [c for c in table.columns if c not in ("구간", "보유(%)")]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=table["구간"], y=table["보유(%)"], name="Buy & Hold", marker_color=NEUTRAL, opacity=0.6))
    for i, n in enumerate(names):
        fig.add_trace(go.Bar(x=table["구간"], y=table[n], name=n, marker_color=SERIES[i % len(SERIES)]))
    base_layout(fig, 300)
    fig.update_layout(barmode="group")
    st.caption("구간별 수익률(%) — 각 구간을 새 자본으로 따로 매매")
    show_chart(fig, key=key)
    with st.expander("구간별 상세", icon=":material/table:"):
        st.dataframe(table, hide_index=True)


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
    _plan = load_settings(core.SETTINGS_KEY_PLAN, backend())
    _saved = int((_plan.get("agent_budget") or {}).get(ticker, 10_000_000))

    def _save_budget():
        if backend() == "supabase":
            cur = load_settings(core.SETTINGS_KEY_PLAN, backend())
            save_settings(core.SETTINGS_KEY_PLAN, {**cur, "agent_budget": {**(cur.get("agent_budget") or {}),
                                                                           ticker: int(st.session_state[f"budget_krw_{ticker}"])}})

    budget_krw = top[3].number_input("총 작전 예산 (₩)", min_value=100_000, value=_saved, step=100_000, format="%d",
                                     key=f"budget_krw_{ticker}", on_change=_save_budget,
                                     help="고른 계좌들에 이번 사이클에 쓸 돈 전체(이미 산 금액 + 남은 현금). 바꾸면 저장돼요. "
                                          "그 안에서 언제·얼마를 살지는 AI 요원이 정해요.")
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
        prin = {**core.DEFAULT_PRINCIPLES, **load_settings(core.SETTINGS_KEY_PLAN, backend())}
        cap_usd = float(prin["buy_max_krw"]) / fx_rate()
        far = pos.qty > 0 and pos.avg > price * 2
        if far:
            st.warning(f"지금 매도 목표는 평단 ${pos.avg:,.2f} 기준이라 현재가 ${price:,.2f}에서 +{(pos.avg / price - 1) * 100:.0f}% 넘게 "
                       "떨어져 있어요. 사이클을 새로 시작(12월 손실 실현 뒤 다시 사기)하기 전까지는 참고용이에요.",
                       icon=":material/info:")
        for i, model in enumerate(models[:3]):
            plan = agent_plan(model, last, price, pos, budget_usd)
            over_rule = plan["amount"] > cap_usd
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
                         ("금일 투입", f"{md_usd(plan['amount'])} (≈{plan['amount'] * fx_rate():,.0f}원) · 1회분 ×{plan['buy_mult']:.2f}"),
                         ("분할 / 진행", f"{plan['split']}분할 / {pos.buy_count}회"),
                         ("잔여 예산", md_usd(plan["remaining"]))])
                st.markdown(pill(plan["status"][0], plan["status"][1]), unsafe_allow_html=True)
                if over_rule:
                    st.caption(f"요원 판단(1회분 ×{plan['buy_mult']:.2f})으로 오늘은 하루 원칙 {int(prin['buy_max_krw']):,}원보다 많이 사요. "
                               "총예산 안에서 요원이 배분한 금액이에요.")
                with st.popover("유전자 보기", icon=":material/genetics:", width="stretch"):
                    st.dataframe(pd.DataFrame({"항목": core.GENE_NAMES, "값": model["genes"]}),
                                 hide_index=True, column_config={"값": st.column_config.NumberColumn(format="%.3f")})
                    if model.get("trained_at"):
                        st.caption(f"{model.get('ticker', '')} · {model['trained_at']} 훈련")
        render_what_if(hist, price, pos, models[0], last, budget_usd, resistance)
    render_breakeven(ticker, pos.avg if pos.qty else None)
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


BREAKEVEN_INDEX = {"SOXL": "^SOX", "USD": "^SOX", "SOXS": "^SOX"}


def _secret_float(name: str) -> float | None:
    try:
        v = st.secrets.get(name)
        return float(v) if v not in (None, "") else None
    except Exception:
        return None


def render_breakeven(ticker: str, journal_avg: float | None) -> None:
    """본전 거리: 평단 회복에 필요한 기초 지수 수준 (즉시 가정 + 시간이 걸릴 때)."""
    cat = core.category_of(ticker)
    lev = {"3배 ETF": 3, "2배 ETF": 2}.get(cat)
    if lev is None:
        return
    idx_sym = BREAKEVEN_INDEX.get(ticker, core.partner_of(ticker))
    section("본전 거리", f"{ticker} 평단을 회복하려면 기초 지수({idx_sym})가 어디까지 올라야 하는지 계산합니다. "
                       "'지금 즉시'는 지수가 출렁임 없이 바로 오를 때(가장 낙관적)이고, 시간이 걸릴수록 레버리지의 "
                       "변동성 손실과 비용 때문에 지수가 더 올라야 합니다. 매수·매도 신호가 아니라 장기 보유 부담을 보는 지표예요.")
    default_avg = _secret_float(f"TOTAL_AVG_{ticker}") or journal_avg or 0.0
    c = st.columns([1, 1.6, 1], vertical_alignment="bottom")
    avg = c[0].number_input("기준 평단 (USD)", min_value=0.0, value=float(round(default_avg, 2)), step=0.01,
                            key=f"be_avg_{ticker}",
                            help="여러 계좌를 합친 통합 평단을 넣으세요. Streamlit Secrets에 "
                                 f"TOTAL_AVG_{ticker}를 넣어두면 기본값으로 쓰입니다.")
    extra_default = st.secrets.get(f"TARGETS_{ticker}", "") if hasattr(st, "secrets") else ""
    extra = c[1].text_input("추가 목표가 (쉼표로 구분)", value=str(extra_default or ""), key=f"be_tg_{ticker}",
                            placeholder="예: 107, 163.41, 178.19 (1차, 2차, 3차 순서)")
    if avg <= 0:
        st.caption("기준 평단을 넣으면 계산합니다.")
        return
    idx_df, px_df = load_long(idx_sym, "5y", False), load_long(ticker, "5y", False)
    if idx_df is None or px_df is None or idx_df.empty or px_df.empty:
        st.caption("지수 또는 가격 데이터를 불러오지 못했습니다.")
        return
    ic, pc = idx_df["Close"].dropna(), px_df["Close"].dropna()
    vol = core.realized_vol(ic)
    vol = vol if np.isfinite(vol) else 0.35
    c[2].metric("지수 1년 변동성", f"{vol * 100:.0f}%", help="최근 252거래일 실제 변동성. 시간 가정 계산에 씁니다.")
    extras = []
    for part in str(extra).replace("$", "").split(","):
        try:
            v = float(part.strip())
            if v > 0:
                extras.append(v)
        except ValueError:
            continue
    targets = {} if any(abs(v - avg) < 0.005 for v in extras) else {"기준 평단": avg}
    for n, v in enumerate(extras, 1):
        targets[f"{n}차 목표" + (" (평단)" if abs(v - avg) < 0.005 else "")] = v
    i_now, p_now = float(ic.iloc[-1]), float(pc.iloc[-1])
    table = core.breakeven_table(i_now, p_now, targets, lev, vol)
    hist = core.needed_gain_history(ic, pc, avg, lev)
    m = st.columns(4)
    m[0].metric(f"지금 {idx_sym}", f"{i_now:,.0f}", help=f"{ticker} ${p_now:,.2f} 기준")
    m[1].metric("즉시 기준 필요 상승", f"{hist.iloc[-1]:+.0f}%")
    for j, (d, lab) in enumerate([(5, "5거래일 전보다"), (20, "20거래일 전보다")]):
        if len(hist) > d:
            chg = hist.iloc[-1] - hist.iloc[-1 - d]
            m[2 + j].metric(f"필요 상승 · {lab}", f"{chg:+.1f}%p", "본전에서 멀어짐" if chg > 0 else "본전에 가까워짐",
                            delta_color="inverse" if chg > 0 else "normal")
    show = table.drop(columns=[c for c in table.columns if c.endswith("(%)")])
    for col in [c for c in show.columns if c not in ("목표", "목표가")]:
        show[col] = [f"{v:,.0f} ({p:+.0f}%)" for v, p in zip(table[col], table[f"{col} 상승률(%)"])]
    st.dataframe(show, hide_index=True, width="stretch",
                 column_config={"목표가": st.column_config.NumberColumn(format="$%.2f")})
    st.caption(f"시간 가정: 매년 변동성 손실({lev * (lev - 1) // 2}×변동성²)과 비용(약 {core.LEV_COST * 100:.1f}%)만큼 "
               "더 깎인다고 보고 계산한 대략치입니다. 실제는 지수가 얼마나 출렁이며 오르느냐에 따라 달라져요.")
    tail = hist.tail(250)
    fig = go.Figure(go.Scatter(x=tail.index, y=tail.values, name="필요 상승률", line=dict(color=GOLD, width=2)))
    base_layout(fig, 260)
    fig.update_yaxes(title_text="본전까지 필요한 지수 상승률(%) · 즉시 기준")
    show_chart(fig, key=f"be_chart_{ticker}")


# ==========================================================================
# 공용: 설정 저장소 (valhalla_portfolio_settings, key별 JSON)
# ==========================================================================
@st.cache_data(ttl=60, show_spinner=False)
def load_settings(key: str, backend_name: str) -> dict:
    if backend_name != "supabase":
        return {}
    try:
        rows = core.sb_fetch_all(get_supabase(), core.PORTFOLIO_SETTINGS, {"key": key})
        return rows[0]["value"] if rows else {}
    except Exception as exc:
        LOGGER.warning("설정 조회 실패(%s): %s", key, exc)
        return {}


def save_settings(key: str, value: dict) -> None:
    get_supabase().table(core.PORTFOLIO_SETTINGS).upsert({"key": key, "value": value}, on_conflict="key").execute()
    load_settings.clear()
    load_portfolio.clear()


@st.cache_data(ttl=300, show_spinner=False)
def load_insight(backend_name: str) -> dict:
    if backend_name != "supabase":
        return {}
    try:
        rows = core._rows(get_supabase().table(core.INSIGHT_TABLE).select("*").order("day", desc=True).limit(1).execute())
        return {**rows[0]["payload"], "_day": rows[0]["day"]} if rows else {}
    except Exception as exc:
        LOGGER.warning("인사이트 조회 실패: %s", exc)
        return {}


@st.cache_data(ttl=300, show_spinner=False)
def load_last_run(backend_name: str) -> dict:
    if backend_name != "supabase":
        return {}
    try:
        rows = core._rows(get_supabase().table("valhalla_job_runs").select("*").eq("source", "signals")
                          .order("id", desc=True).limit(1).execute())
        return rows[0] if rows else {}
    except Exception:
        return {}


def kiwoom_cycles(plan: dict, journal: pd.DataFrame) -> pd.DataFrame:
    """키움 무한매수 계좌 표: 저장된 표가 있으면 그것, 없으면 매매 일지로 계산한 값."""
    cols = ["계좌", "기법", "보유 수량", "평단($)", "회차", "예산(원)", "1회 매수(원)"]
    saved = plan.get("cycles")
    if saved:
        return pd.DataFrame(saved)[cols]
    rows = []
    if journal is not None and not journal.empty:
        for acc, g in journal[journal["Ticker"] == "SOXL"].groupby("Account"):
            pos = core.compute_position(g)
            if pos.qty > 0:
                rows.append({"계좌": str(acc), "기법": "①", "보유 수량": round(pos.qty, 4), "평단($)": round(pos.avg, 2),
                             "회차": pos.buy_count, "예산(원)": 10_000_000,
                             "1회 매수(원)": plan.get("buy_target_krw", core.DEFAULT_PRINCIPLES["buy_target_krw"])})
    return pd.DataFrame(rows, columns=cols)


@st.cache_data(ttl=600, show_spinner=False)
def load_insight_history(backend_name: str) -> pd.DataFrame:
    if backend_name != "supabase":
        return pd.DataFrame()
    try:
        rows = core.sb_fetch_all(get_supabase(), core.INSIGHT_TABLE)
    except Exception:
        try:
            rows = core._rows(get_supabase().table(core.INSIGHT_TABLE).select("*").order("day").execute())
        except Exception as exc:
            LOGGER.warning("인사이트 기록 조회 실패: %s", exc)
            return pd.DataFrame()
    return pd.DataFrame([{"day": r["day"], "code": (r.get("payload") or {}).get("scenario", {}).get("code")} for r in rows])


@st.cache_data(ttl=600, show_spinner=False)
def load_shares(backend_name: str, ticker: str = "SOXL") -> pd.DataFrame:
    if backend_name != "supabase":
        return pd.DataFrame()
    try:
        return pd.DataFrame(core.sb_fetch_all(get_supabase(), core.SHARES_TABLE, {"ticker": ticker}))
    except Exception as exc:
        LOGGER.warning("발행 주식 수 조회 실패: %s", exc)
        return pd.DataFrame()


def render_price_checks(plan: dict, soxl: pd.DataFrame, now: pd.Timestamp) -> None:
    """가격 확인 신호등(기한·남은 날짜) + SOXL 발행 주식 수 추적."""
    checks = plan.get(core.PRICE_CHECKS_KEY) or core.DEFAULT_PRICE_CHECKS
    st.markdown("**🚦 가격 확인 신호등 (종가 기준 · 매일 자동)**")
    tbl = core.evaluate_price_checks(soxl["Close"], checks, now)
    st.dataframe(tbl, hide_index=True, width="stretch")
    st.caption("✅ 달성·유지 · ⏳ 기한 안 진행 중 · 📅 시작 전 · ❌ 미달·깨짐. 날짜는 미국 거래일(종가) 기준. "
               "기준은 SOXL 기술적·수급 브리핑(11/27)에서 가져왔어요. 엘리엇 파동 해석은 주관적이라, 가격이 실제로 기준을 지키는지로만 판정해요.")
    with st.expander("신호등 기준 고치기", expanded=False):
        ed = st.data_editor(pd.DataFrame(checks), num_rows="dynamic", hide_index=True, width="stretch", key="pc_edit",
                            column_config={"name": "점검", "kind": st.column_config.SelectboxColumn(
                                "종류", options=["above", "never_below"], help="above = 기간 안에 이 가격 이상 마감, never_below = 이 가격 아래로 마감하면 안 됨"),
                                "price": st.column_config.NumberColumn("가격($)", format="%.2f"),
                                "start": "시작일(YYYY-MM-DD)", "end": "마감일(비우면 기한 없음)", "note": "설명"})
        c = st.columns(2)
        if c[0].button("신호등 저장", type="primary", key="pc_save") and backend() == "supabase":
            rows = [{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in ed.to_dict("records") if r.get("price")]
            save_settings(core.SETTINGS_KEY_PLAN, {**plan, core.PRICE_CHECKS_KEY: rows})
            flash("가격 신호등 기준을 저장했어요.")
            st.rerun()
        if c[1].button("브리핑 기본값으로", key="pc_reset") and backend() == "supabase":
            save_settings(core.SETTINGS_KEY_PLAN, {**plan, core.PRICE_CHECKS_KEY: core.DEFAULT_PRICE_CHECKS})
            st.rerun()

    st.markdown("**📉 SOXL 발행 주식 수 (진짜 바닥 신호 ②)**")
    tr = core.shares_trend(load_shares(backend()))
    if not tr:
        st.caption("아직 기록이 없어요. 매일 아침 자동 점검이 운용사 자료로 기록해요.")
        return
    c = st.columns(4)
    c[0].metric("발행 주식 수", f"{tr['latest'] / 1e6:,.1f}백만 주", f"{tr['day']:%m/%d}", delta_color="off",
                delta_arrow="off", border=True)
    c[1].metric("전일 대비", f"{tr['d1']:+.2f}%" if pd.notna(tr["d1"]) else "—", border=True)
    c[2].metric("5거래일 대비", f"{tr['d5']:+.2f}%" if pd.notna(tr["d5"]) else "—", border=True)
    c[3].metric("기록 중 최고 대비", f"{tr['from_peak']:+.2f}%", f"기록 {tr['n']}일", delta_color="off",
                delta_arrow="off", border=True)
    if tr["n"] > 1:
        st.line_chart(tr["series"] / 1e6, height=160, y_label="백만 주")
    st.caption(("🟢 최근 5거래일 동안 줄고 있어요. 개인 매물이 운용사 환매로 빠져나가는 신호일 수 있어요. "
                if tr["falling"] else "⚪ 아직 줄어드는 흐름이 아니에요. ")
               + "발행 주식 수가 줄면 개인이 레버리지 ETF에서 손을 떼고 있다는 뜻(브리핑의 진짜 바닥 신호). "
                 "다만 반등 때 차익 실현으로도 줄 수 있어 레버리지 ETF 자금 흐름과 같이 봐야 해요.")


def render_scenario(plan: dict, ins: dict, now: pd.Timestamp, fx: float) -> None:
    """내 상황 시나리오 관제: 이벤트 관문 → 시나리오 판정 → 체크리스트 → 오늘 할 일 → 계좌별 총알."""
    s = core.scenario_settings(plan)
    section("내 상황 시나리오", "시장 흐름을 매일 체크해서 A·B·C·D 중 지금 어디인지 판정하고, 큰 발표 전에는 기다렸다가 지나간 뒤 대응을 제안해요. "
            "미래를 맞히는 도구가 아니라 미리 정한 대응을 지키게 하는 도구예요.")
    soxl = load_long("SOXL", "2y", False)
    if soxl is None or soxl.empty:
        st.info("SOXL 시세를 불러오지 못했어요. 잠시 뒤 새로고침하세요.")
        return
    sox = (load_snapshot(("^SOX",)).get("^SOX") or (np.nan,))[0]
    reb = (ins.get("rebound") or {}).get("latest") or {}
    sox_line = float(reb["prior_low"]) if reb.get("prior_low") else np.nan
    v1_on = None
    pos, _ = load_arena(backend())
    if not pos.empty:
        v1 = pos[(pos["ticker"] == "SOXL") & (pos["player"] == "rule_v1")].sort_values("week")
        if not v1.empty:
            v1_on = float(v1["position"].iloc[-1]) >= 0.5
    snap = core.scenario_snapshot(soxl, sox, sox_line, s, v1_on, reb.get("outcome"), now)
    g = snap["gate"]
    code = snap["code"]

    gate_kind = {"wait": st.warning, "settle": st.warning, "after": st.success, "clear": st.info}[g["state"]]
    head = {"wait": "⏸ 발표 대기", "settle": "⏳ 발표 소화 중", "after": "▶ 발표 지남 · 대응 가능", "clear": "▶ 대응 가능"}[g["state"]]
    ev_txt = f" — {g['event']} ({g['when']:%m/%d %H:%M}{'' if g['confirmed'] else ', 미확정'})" if g["event"] else ""
    nxt = g.get("next")
    nxt_txt = f" · 다음 큰 발표: {nxt[0]:%m/%d %H:%M} {nxt[1]}" if nxt and g["state"] in ("after", "clear") else ""
    gate_kind(f"**{head}**{ev_txt}. {core.GATE_TEXT[g['state']]}{nxt_txt}", icon=":material/event:")

    c = st.columns(4)
    c[0].metric("지금 시나리오", core.SCENARIO_NAMES[code], border=True)
    c[1].metric("SOXL", f"${snap['price']:.2f}", f"최근 저점 ${snap['low']:.2f}", delta_color="off", delta_arrow="off", border=True)
    c[2].metric("매물대 띠 (저항)", f"${snap['res_low']:.2f}~{snap['resistance']:.2f}" if pd.notna(snap["resistance"]) else "—",
                f"아래쪽까지 +{(snap['res_low'] / snap['price'] - 1) * 100:.0f}%" if pd.notna(snap["res_low"]) else None,
                delta_color="off", delta_arrow="off", border=True)
    c[3].metric("탈출 목표", f"${float(s['exit_target']):.0f}", f"+{snap['exit_gap']:.0f}% 필요", delta_color="off",
                delta_arrow="off", border=True)
    st.markdown(f"**판정 이유:** {snap['why']}")
    sb_ = snap["sup_band"]
    st.caption(f"매물대 띠 = 최근 1년 가격대별 거래량이 가장 두꺼운 구간 (중심 ${snap['res_peak']:.2f}). 띠 아래쪽부터 물량이 나오고, "
               f"주간 종가가 띠 위쪽을 넘어야 C로 판정해요."
               + (f" 아래 지지 띠는 ${sb_[0]:.2f}~{sb_[1]:.2f}." if pd.notna(sb_[0]) else "")
               if pd.notna(snap["res_peak"]) else "매물대를 계산하지 못했어요.")

    left, right = st.columns([1.1, 1])
    with left:
        st.markdown("**오늘 할 일**")
        for a in core.scenario_actions(code, g["state"], s, snap["price"], snap["resistance"],
                                       float(plan["buy_target_krw"]), float(plan["buy_max_krw"]), fx):
            st.markdown(f"- {a}")
    with right:
        st.markdown("**체크리스트 (자동)**")
        ck = pd.DataFrame(snap["checks"])
        ck["충족"] = ck["충족"].map({True: "✅", False: "❌", None: "—"})
        st.dataframe(ck, hide_index=True, width="stretch")

    try:
        render_price_checks(plan, soxl, now)
    except Exception as exc:
        LOGGER.exception("가격 신호등 실패")
        st.warning(f"가격 신호등을 그리지 못했어요: {exc}")

    journal, _ = load_table("trade_journal")
    acc = core.scenario_accounts(kiwoom_cycles(plan, journal), s, snap["price"], fx, float(plan["buy_target_krw"]))
    cyc_ = kiwoom_cycles(plan, journal)
    if float(s.get("loss_krw") or 0) > 0 and not cyc_.empty and not s.get("rebought"):
        sl = core.shares_for_loss(float(s["loss_krw"]), cyc_.rename(columns={"계좌": "Account", "보유 수량": "Qty", "평단($)": "Avg"})
                                  [["Account", "Qty", "Avg"]], snap["price"], fx)
        if sl["shares"]:
            st.info(f"**{float(s['loss_krw']):,.0f}원 손절하려면 SOXL {sl['shares']}주를 팔아요** (실현 손실 ≈{sl['covered_krw']:,.0f}원, "
                    f"${snap['price']:.2f}·환율 {fx:,.0f}원 기준). 계좌별: "
                    + ", ".join(f"{r['계좌']} {int(r['팔 주식 수'])}주" for _, r in sl["rows"].iterrows())
                    + ". 같은 날 다시 사면 보유 수량은 그대로예요. 판 날 가격·환율로 다시 확인하세요.", icon=":material/calculate:")
            if sl["covered_krw"] < float(s["loss_krw"]) * 0.99:
                st.warning("지금 계좌 표의 손실만으로는 그 금액을 다 채우지 못해요.", icon=":material/warning:")
    with st.expander("계좌별 남은 총알 · 다음 매수가 · 익절 지정가", expanded=False):
        if acc.empty:
            st.caption("세금·사이클 페이지의 계좌 표가 비어 있어요.")
        else:
            st.dataframe(acc, hide_index=True, width="stretch",
                         column_config={"예수금(원)": st.column_config.NumberColumn(format="%d"),
                                        "지금 평가(원)": st.column_config.NumberColumn(format="%d")})
            st.caption(("12월 다시 사기 방식이 미정이라 전부·절반·비율을 나란히 보여 줘요. 덜 다시 산 만큼 판 돈이 예수금에 더해져요. "
                        if s.get("rebuy") not in core.REBUY_MODES[1:] else
                        ("" if s.get("rebought") else f"다시 사기 방식: {core.rebuy_label(s['rebuy'], s)}. ")) +
                       f"새 평단은 {'다시 산 가격' if s.get('rebought') else '지금 가격(다시 산다고 가정)'} 기준, "
                       f"총알 1발 = {int(plan['buy_target_krw']):,}원(원칙).")

    with st.expander("시나리오 설정 (탈출 목표 · 예수금 · 12월 다시 사기 · 이벤트 대기 시간)", expanded=False):
        f = st.columns(3)
        ex = f[0].number_input("탈출 목표가 ($)", 1.0, 1000.0, float(s["exit_target"]), 1.0, key="sc_ex")
        tr = f[1].text_input("단계적 탈출 가격 (쉼표)", ", ".join(f"{x:g}" for x in s["tranches"]), key="sc_tr")
        ab = snap["auto_band"]
        rs = f[2].number_input("매물대 띠 아래쪽 직접 입력 ($, 0 = 자동)", 0.0, 1000.0, float(s["resistance"] or 0.0), 0.25, key="sc_rs",
                               help=(f"자동 계산 띠 ${ab[0]:.2f}~{ab[1]:.2f} (최근 1년, 가격의 0.5% 단위, 하루 거래량을 저가~고가에 나눠 담음). "
                                     "직접 넣으면 그 값이 띠 아래쪽, +3%가 위쪽이 돼요.") if pd.notna(ab[0]) else None)
        f = st.columns(3)
        cash = f[0].number_input("키움 계좌당 예수금 (원)", 0, 100_000_000, int(s["cash_per_account_krw"]), 50_000, key="sc_cash")
        modes = core.REBUY_MODES
        rb = f[1].selectbox("12월 다시 사기 방식", modes, modes.index(s["rebuy"]) if s["rebuy"] in modes else 0, key="sc_rb",
                            help="미정이면 세 가지를 나란히 비교해요. 손실 실현(세금 상쇄)은 다시 사는 양과 상관없이 판 순간 확정돼요.")
        tp = f[2].number_input("새 사이클 익절 (%)", 1.0, 100.0, float(s["take_profit"]) * 100, 1.0, key="sc_tp")
        f = st.columns(3)
        rpct = f[0].number_input("비율 직접: 다시 살 비율 (%)", 0.0, 100.0, float(s["rebuy_pct"]), 5.0, key="sc_rpct")
        loss = f[1].number_input("12월 손절할 금액 (원)", 0, 1_000_000_000, int(s["loss_krw"]), 100_000, key="sc_loss",
                                 help="예: 올해 이익만큼 손절하려면 그 금액. 세금만 0원으로 만들려면 올해 이익 − 250만 원.")
        f[2].caption("손절 금액을 넣으면 몇 주를 팔아야 하는지 계좌별로 계산해요. '비율 직접'은 판 뒤 보유 주식 중 그 비율만 다시 사는 방식이에요.")
        f = st.columns(3)
        done = f[0].checkbox("12월 다시 사기 완료", bool(s.get("rebought")), key="sc_done")
        rp = f[1].number_input("다시 산 가격 ($)", 0.0, 1000.0, float(s["rebuy_price"] or 0.0), 0.01, key="sc_rp")
        dips = f[2].text_input("총알 쓰는 하락폭 (%, 쉼표)", ", ".join(f"{x * 100:g}" for x in s["dip_steps"]), key="sc_dip")
        f = st.columns(3)
        wb = f[0].number_input("큰 발표 몇 시간 전부터 대기", 0, 96, int(s["wait_before_h"]), 1, key="sc_wb")
        sa = f[1].number_input("발표 뒤 소화 시간 (시간)", 0, 72, int(s["settle_after_h"]), 1, key="sc_sa")
        if f[2].button("시나리오 설정 저장", type="primary", key="sc_save") and backend() == "supabase":
            try:
                new = {**s, "exit_target": ex, "tranches": sorted(float(x) for x in tr.split(",") if x.strip()),
                       "resistance": rs or None, "cash_per_account_krw": cash, "rebuy": rb, "rebuy_pct": rpct, "loss_krw": loss, "take_profit": tp / 100,
                       "rebought": done, "rebuy_price": rp or None,
                       "dip_steps": sorted(float(x) / 100 for x in dips.split(",") if x.strip()),
                       "wait_before_h": wb, "settle_after_h": sa}
                if len(new["tranches"]) < 3 or not new["dip_steps"]:
                    raise ValueError("탈출 가격은 3개, 하락폭은 1개 이상 적어 주세요.")
                save_settings(core.SETTINGS_KEY_PLAN, {**plan, core.SCENARIO_KEY: new})
                flash("시나리오 설정을 저장했어요.")
                st.rerun()
            except ValueError as exc:
                st.error(f"저장하지 못했어요: {exc}")
        st.caption("큰 발표 = 이름에 FOMC·PCE·CPI·고용·실적·청문회·GDP가 들어간 일정 (시장 레이더 일정표).")

    hist = load_insight_history(backend())
    if not hist.empty and hist["code"].notna().any():
        with st.expander("지난 판정 채점 (20거래일 뒤)", expanded=False):
            calls = hist.dropna(subset=["code"]).set_index(pd.to_datetime(hist.dropna(subset=["code"])["day"]))["code"]
            gr = core.grade_scenarios(calls, soxl["Close"])
            done_ = gr.dropna(subset=["맞음"])
            if len(done_):
                st.metric("맞힌 비율", f"{done_['맞음'].mean() * 100:.0f}%", f"{len(done_)}건 채점", delta_color="off", delta_arrow="off")
            gr["날짜"] = gr["날짜"].dt.strftime("%Y-%m-%d")
            gr["맞음"] = gr["맞음"].map({True: "✅", False: "❌", None: "대기"})
            st.dataframe(gr.iloc[::-1], hide_index=True, width="stretch")
            st.caption("A=20거래일 뒤 하락, B=±20% 안, C·D=상승이면 맞음. 매일 아침 자동 점검이 판정을 기록해요.")


# ==========================================================================
# 페이지: 발할라 브리핑 (오늘 할 일 · 지금 상태 · 일정 · 데이터가 알려준 교훈)
# ==========================================================================
def page_briefing() -> None:
    page_header("BRIEFING", "발할라 브리핑", "오늘 할 일 · 지금 상태 · 이번 주 일정 · 지금까지 데이터가 알려준 것",
                [("매일 아침 자동 갱신", ACCENT)])
    ins = load_insight(backend())
    plan = {**core.DEFAULT_PRINCIPLES, **load_settings(core.SETTINGS_KEY_PLAN, backend())}
    holdings, pset = load_portfolio(backend())
    fx = fetch_usdkrw() or 1350.0
    now = pd.Timestamp(now_kst().replace(tzinfo=None))
    snap = load_snapshot(("SOXL", "SOXX", "^SOX"))
    soxl = snap.get("SOXL")
    tasks: list[tuple[str, str]] = []

    # 12월 손실 실현
    dec1 = pd.Timestamp(f"{now.year}-12-01")
    if pd.Timestamp(f"{now.year}-11-20") <= now <= pd.Timestamp(f"{now.year}-12-22"):
        d = (dec1.normalize() - now.normalize()).days
        tasks.append(("warn", f"12월 손실 실현 {'D-' + str(d) if d > 0 else '진행 기간'} · 세금·사이클 페이지에서 팔 주식 수를 확인하세요 "
                              "(팔고 같은 날 다시 사기, 연내 결제 마감 전에)."))
    # 반등 판별 감시선
    reb = (ins.get("rebound") or {}).get("latest") or {}
    if reb and reb.get("outcome") is None:
        tasks.append(("info", f"반도체 반등({str(reb.get('date'))[:10]} {reb.get('trigger')}) 판정 대기 · SOX {reb['prior_low']:,.0f} 아래로 "
                              "내려가면 '일시 반등' 확정이에요. 그 전까지는 추세 전환 가능성 열어두기."))
    # 토스 리밸런싱
    port = ins.get("portfolio") or {}
    if port and not holdings.empty:
        champ = pset.get("champion", core.PORTFOLIO_CHAMPION)
        tgt = {k: float(v) for k, v in (port.get("base") or {}).items()}
        tick = tuple(sorted({str(t).upper() for t in holdings["ticker"]} | {k for k in tgt if k != "CASH"}))
        prices = {t: v[0] for t, v in load_snapshot(tick).items()}
        if champ == "pf_harvest":
            orders = core.harvest_orders(holdings.assign(ticker=holdings["ticker"].str.upper()), prices,
                                         core.with_cash_buffer(tgt, float(pset.get("cash_buffer", core.HARVEST["cash"]))),
                                         cash=float(pset.get("cash_krw", 0) or 0) / fx, band=float(pset.get("band", core.HARVEST["band"])))
        else:
            orders = core.rebalance_orders(holdings.assign(ticker=holdings["ticker"].str.upper()), prices, tgt)
        act = orders[orders["주문"].isin(["매수", "매도"])]
        if not act.empty:
            txt = ", ".join(f"{r['종목']} {r['주문']} ${r['금액($)']:,.0f}" for _, r in act.head(4).iterrows())
            tasks.append(("info", f"토스 계좌 리밸런싱 제안 ({core.PORTFOLIO_PLAYERS[champ].split(' (')[0]}): {txt} · 내 포트폴리오 페이지"))
    # 일봉 리그 오늘 신호 (SOXL)
    led = load_daily_ledger(backend())
    if not led.empty:
        s_ = led[led["ticker"] == "SOXL"].copy()
        if not s_.empty:
            s_["day"] = pd.to_datetime(s_["day"])
            piv = s_.pivot_table(index="day", columns="player", values="position", aggfunc="last").sort_index()
            today = core.daily_today(piv)
            fired = today[today["오늘 신호"] != ""]
            if not fired.empty:
                tasks.append(("stop", f"일봉 리그 SOXL {piv.index[-1]:%m/%d} 신호: " + ", ".join(
                    f"{core.player_label(r['id'])} {r['오늘 신호']}" for _, r in fired.iterrows())))
    # 원칙 점검 (최근 2주 매수)
    journal, _ = load_table("trade_journal")
    amt = core.daily_buy_amounts(journal, fx)
    recent = amt[pd.to_datetime(amt["Date"]) >= now - pd.Timedelta(days=14)] if not amt.empty else amt
    if not recent.empty:
        chk = core.check_buy_principle(recent, plan["buy_target_krw"], plan["buy_max_krw"])
        if len(chk["over_hard"]):
            tasks.append(("stop", f"투자 원칙 위반 {len(chk['over_hard'])}건: 최근 2주 하루 매수가 {plan['buy_max_krw']:,}원을 넘은 날이 있어요."))
    if not journal.empty and pd.to_datetime(journal["Date"]).max() < now - pd.Timedelta(days=14):
        tasks.append(("warn", f"매매 일지가 {pd.to_datetime(journal['Date']).max():%m/%d}에서 멈춰 있어요. 키움 CSV를 동기화하면 원칙 점검·세금 장부가 정확해져요."))
    # 가까운 일정
    ev = core.upcoming_events(now, days=3)
    for _, e in ev.iterrows():
        tasks.append(("info", f"{e['when']:%m/%d %H:%M} {e['일정']}{'' if e['확정'] else ' (미확정)'} · {e['메모']}"))
    # 데이터 상태
    run = load_last_run(backend())
    if run:
        ran = pd.Timestamp(run["ran_at"])
        if (now - ran) > pd.Timedelta(hours=30):
            tasks.append(("warn", f"자동 점검이 {ran:%m/%d %H:%M} 이후 멈춰 있어요. 숫자가 오래됐을 수 있어요."))

    section("오늘 할 일", f"{now:%m월 %d일 %H:%M} 기준. 행동이 필요한 것만 모았어요.")
    if not tasks:
        st.success("오늘은 따로 할 일이 없어요. 차트를 덜 보는 것도 전략이에요.", icon=":material/self_improvement:")
    for kind, text in tasks:
        icon = {"stop": ":material/priority_high:", "warn": ":material/schedule:", "info": ":material/info:"}[kind]
        (st.error if kind == "stop" else st.warning if kind == "warn" else st.info)(text, icon=icon)

    try:
        render_scenario(plan, ins, now, fx)
    except Exception as exc:
        LOGGER.exception("시나리오 관제 실패")
        st.warning(f"시나리오 관제를 그리지 못했어요: {exc}")

    section("지금 상태")
    c = st.columns(4)
    if soxl:
        c[0].metric("SOXL", f"${soxl[0]:.2f}", f"{soxl[1]:+.1f}%", border=True)
    pos, _ = load_arena(backend())
    if not pos.empty:
        v1 = pos[(pos["ticker"] == "SOXL") & (pos["player"] == "rule_v1")].sort_values("week")
        if not v1.empty:
            c[1].metric("규칙 v1 (SOXL)", "보유" if float(v1["position"].iloc[-1]) >= 0.5 else "쉬는 중",
                        f"{str(v1['week'].iloc[-1])[:10]} 주", delta_color="off", delta_arrow="off", border=True)
    champ_p = load_champion_state(backend())["champion"]["player"]
    c[2].metric("경기장 챔피언", core.player_label(champ_p), border=True)
    if port:
        c[3].metric("토스 테마", f"{port.get('lead') or '—'} · {port.get('rising') or '—'}",
                    "주도 · 떠오르는", delta_color="off", delta_arrow="off", border=True)

    section("이번 주 일정 (한국 시간)")
    ev = core.upcoming_events(now, days=10)
    if ev.empty:
        st.caption("가까운 일정이 없어요.")
    else:
        st.dataframe(pd.DataFrame({"날짜": ev["when"].dt.strftime("%m/%d %H:%M"), "일정": ev["일정"],
                                   "확정": ev["확정"].map({True: "확정", False: "미확정"})}), hide_index=True, width="stretch")

    section("지금까지 데이터가 알려준 것", "실제 기록과 백테스트에서 나온 교훈이에요. 매일 아침 숫자가 갱신돼요.")
    lessons = []
    if ins.get("beat_share") is not None:
        lessons.append(f"**신호만 믿고 사면 안 돼요.** 매수 신호가 '평소'보다 나았던 종목은 최근 2년 검증 구간에서 {ins['beat_share']:.0f}%뿐이에요. (연구실)")
    rb = ins.get("rebound") or {}
    if rb.get("reversal_share") is not None:
        lessons.append(f"**큰 반등에 흥분하지 마세요.** 1994년 이후 반도체 큰 반등 {rb['events']}건 중 추세 전환은 {rb['reversal_share']:.0f}%, "
                       f"하루 +5% 급반등만 보면 {rb.get('day5_share') or 0:.0f}%예요. (선수 경기장 → 반등 판별전)")
    if rb.get("macro"):
        m = rb["macro"]
        lessons.append(f"**흔한 거시 조건으로는 바닥을 못 맞혀요.** 금리·VIX·신용 스프레드 체크리스트는 {m['n']}건에서 '과거 확률'과 거의 같은 "
                       f"정확도였어요(차이 {m['vs_base']:+.3f}). (시장 레이더)")
    if port.get("table"):
        t = pd.DataFrame(port["table"]).set_index("id")
        if {"pf_harvest", "bm_soxl"} <= set(t.index):
            lessons.append(f"**현금 완충이 낙폭을 줄여요.** 2018년 이후 SOXL만 들고 있으면 최대 낙폭 {t.loc['bm_soxl', '최대 낙폭(%)']:.0f}%, "
                           f"SOXL 핵심 + 테마 + 현금 20% 수익 실현형은 {t.loc['pf_harvest', '최대 낙폭(%)']:.0f}%였어요. (내 포트폴리오)")
    lessons.append(f"**한 번에 많이 사지 않기.** 무한매수는 하루 {plan['buy_target_krw']:,}원, 최대 {plan['buy_max_krw']:,}원까지. "
                   "올해 손실의 핵심은 종목보다 '3배 레버리지 한 종목에 자산 대부분'이었다는 점이에요. (원칙·위험)")
    for l_ in lessons:
        st.markdown(f"- {l_}")
    if ins:
        st.caption(f"교훈 숫자 기준일 {str(ins.get('_day'))[:10]} (매일 아침 자동 점검).")
    else:
        st.caption("아직 자동 점검의 인사이트 기록이 없어요. 다음 자동 점검 뒤부터 숫자가 채워져요.")


# ==========================================================================
# 페이지: 원칙 · 위험 관리
# ==========================================================================
def page_principles() -> None:
    page_header("PRINCIPLES & RISK", "원칙 · 위험 관리", "나의 투자 원칙과 지키고 있는지 점검 · 키움+토스 전체 위험",
                [("재기를 위한 안전장치", GOLD)])
    plan = {**core.DEFAULT_PRINCIPLES, **load_settings(core.SETTINGS_KEY_PLAN, backend())}
    fx = fetch_usdkrw() or 1350.0
    section("나의 투자 원칙")
    c = st.columns([1, 1, 2])
    tgt = c[0].number_input("무한매수 하루 매수 목표 (원)", 50_000, 2_000_000, int(plan["buy_target_krw"]), 10_000, key="pp_t")
    mx = c[1].number_input("하루 매수 최대 (원)", 50_000, 2_000_000, int(plan["buy_max_krw"]), 10_000, key="pp_m")
    extra = c[2].text_area("추가 원칙 (한 줄에 하나)", "\n".join(plan.get("extra") or []), height=110, key="pp_x",
                           placeholder="예) 3배 레버리지는 전체 자산의 50%까지\n예) 현금은 항상 20% 이상")
    if st.button("원칙 저장", type="primary", key="pp_save") and backend() == "supabase":
        save_settings(core.SETTINGS_KEY_PLAN, {**plan, "buy_target_krw": tgt, "buy_max_krw": mx,
                                               "extra": [x.strip() for x in extra.splitlines() if x.strip()]})
        flash("투자 원칙을 저장했어요.")
        st.rerun()
    st.markdown(f"1. **무한매수는 한 계좌에서 하루 {tgt:,}원씩, 아무리 많아도 {mx:,}원을 넘기지 않는다.**")
    for i, x in enumerate(plan.get("extra") or [], start=2):
        st.markdown(f"{i}. {x}")
    snap = load_snapshot(("SOXL",))
    if "SOXL" in snap:
        q = core.buy_qty_for_budget(snap["SOXL"][0], fx, tgt, mx)
        st.info(f"오늘 SOXL ${snap['SOXL'][0]:.2f} · 환율 {fx:,.0f}원 → 원칙 안에서 하루 **{q['qty']}주** "
                f"(약 {q['krw']:,}원, 1주 {q.get('per_share_krw', 0):,}원)", icon=":material/shopping_cart:")

    section("원칙을 지키고 있나요?", "키움 매매 일지로 계좌·날짜별 하루 매수 금액을 점검해요 (현재 환율로 원화 환산한 대략치).")
    journal, _ = load_table("trade_journal")
    amt = core.daily_buy_amounts(journal, fx)
    if amt.empty:
        st.caption("매매 일지가 비어 있어요.")
    else:
        chk = core.check_buy_principle(amt, tgt, mx)
        m = st.columns(3)
        m[0].metric("점검한 매수일", f"{chk['days']}일", border=True)
        m[1].metric(f"{mx:,}원 넘은 날", f"{len(chk['over_hard'])}일", border=True)
        m[2].metric("원칙 지킨 비율", f"{chk['share_ok']:.0f}%", border=True)
        if len(chk["over_hard"]):
            st.dataframe(chk["over_hard"].rename(columns={"Account": "계좌", "Date": "날짜", "Ticker": "종목", "usd": "매수($)", "krw": "매수(원)"})
                         .sort_values("날짜", ascending=False).head(30), hide_index=True, width="stretch")
        st.caption(f"매매 일지 마지막 날짜: {pd.to_datetime(journal['Date']).max():%Y-%m-%d}. 12월 새 사이클부터는 이 표가 0건이어야 해요.")

    section("전체 위험 (키움 + 토스)", "레버리지 ETF가 하루에 크게 빠지면 내 전체 자산이 얼마나 줄어드는지 미리 봐요.")
    cycles = kiwoom_cycles(plan, journal)
    holdings, pset = load_portfolio(backend())
    tick = tuple(sorted({"SOXL"} | {str(t).upper() for t in holdings["ticker"]}))
    prices = {t: v[0] for t, v in load_snapshot(tick).items()}
    rows = [{"account": f"키움 {r['계좌']}", "ticker": "SOXL", "qty": float(r["보유 수량"]), "price": prices.get("SOXL", np.nan)}
            for _, r in cycles.iterrows()]
    rows += [{"account": "토스", "ticker": str(r["ticker"]).upper(), "qty": float(r["shares"]),
              "price": prices.get(str(r["ticker"]).upper(), np.nan)} for _, r in holdings.iterrows()]
    cash_usd = (float(plan.get("kiwoom_cash_krw", 0) or 0) + float(pset.get("cash_krw", 0) or 0)) / fx
    exp = core.exposure_table([r for r in rows if pd.notna(r["price"])])
    if exp.empty:
        st.caption("보유 내역이 없어요.")
        return
    total = float(exp["value"].sum()) + cash_usd
    lev_share = float(exp.loc[exp["lev"] >= 2, "value"].sum() / total * 100)
    semi_share = float(exp.loc[exp["semi"], "value"].sum() / total * 100)
    top = exp.groupby("ticker")["value"].sum().sort_values(ascending=False)
    m = st.columns(4)
    m[0].metric("전체 자산", fmt_usd(total), fmt_krw(total * fx), delta_color="off", delta_arrow="off", border=True)
    m[1].metric("레버리지(2·3배) 비중", f"{lev_share:.0f}%", border=True)
    m[2].metric("반도체 비중", f"{semi_share:.0f}%", border=True)
    m[3].metric("가장 큰 종목", f"{top.index[0]} {top.iloc[0] / total * 100:.0f}%", border=True)
    st.dataframe(core.stress_test(exp, cash=cash_usd), hide_index=True, width="stretch",
                 column_config={"예상 손실($)": st.column_config.NumberColumn(format="$%.0f"),
                                "전체 자산 대비(%)": st.column_config.NumberColumn(format="%.1f%%")})
    st.caption("하루에 기초 지수가 그만큼 빠질 때 3배 ETF는 약 3배, 2배는 2배로 빠진다고 본 대략치예요. 현금은 그대로 둬요. "
               "키움 보유는 '세금·사이클' 페이지의 계좌 표를, 현금은 아래 입력값을 써요.")
    kc = st.number_input("키움 계좌들 현금 합계 (원)", 0, 1_000_000_000, int(plan.get("kiwoom_cash_krw", 0) or 0), 100_000, key="pp_kc")
    if st.button("현금 저장", key="pp_kc_save") and backend() == "supabase":
        save_settings(core.SETTINGS_KEY_PLAN, {**plan, "kiwoom_cash_krw": kc})
        st.rerun()


# ==========================================================================
# 페이지: 세금 장부 · 무한매수 사이클
# ==========================================================================
def page_ledger() -> None:
    page_header("TAX & CYCLES", "세금 · 사이클", "무한매수 계좌별 사이클 관리 · 올해 해외주식 양도세와 12월 손실 실현 계산",
                [("세금은 참고용 · 최종 확인은 증권사", GOLD)])
    plan = {**core.DEFAULT_PRINCIPLES, **load_settings(core.SETTINGS_KEY_PLAN, backend())}
    fx = fetch_usdkrw() or 1350.0
    journal, _ = load_table("trade_journal")
    price = (load_snapshot(("SOXL",)).get("SOXL") or (np.nan,))[0]
    t1, t2 = st.tabs(["무한매수 사이클", "세금 장부 · 12월 손실 실현"])
    with t1:
        st.caption("계좌마다 기법·보유 수량·평단·회차·예산을 적어 두면 목표가와 남은 회차를 계산해요. 처음에는 매매 일지로 채웠어요 "
                   "(일지가 멈춘 뒤 거래는 직접 고쳐 주세요). 기법: ①=40분할 +10% 전량 익절, ②=V2 별지점, ④=V2+쿼터 손절+200일선.")
        cyc = kiwoom_cycles(plan, journal)
        edited = st.data_editor(cyc, num_rows="dynamic", width="stretch", key="cyc_editor",
                                column_config={"기법": st.column_config.SelectboxColumn(options=list(core.CYCLE_METHODS)),
                                               "평단($)": st.column_config.NumberColumn(format="%.2f"),
                                               "예산(원)": st.column_config.NumberColumn(format="%d"),
                                               "1회 매수(원)": st.column_config.NumberColumn(format="%d")})
        if st.button("계좌 표 저장", type="primary", key="cyc_save") and backend() == "supabase":
            save_settings(core.SETTINGS_KEY_PLAN, {**plan, "cycles": edited.dropna(subset=["계좌"]).to_dict("records")})
            flash("무한매수 계좌 표를 저장했어요.")
            st.rerun()
        if pd.notna(price) and not edited.empty:
            rows = []
            for _, r in edited.dropna(subset=["계좌"]).iterrows():
                unit = float(r.get("1회 매수(원)") or plan["buy_target_krw"])
                stt = core.cycle_status(float(r["보유 수량"] or 0), float(r["평단($)"] or 0), float(r["회차"] or 0), price,
                                        str(r.get("기법") or "①"), float(r.get("예산(원)") or 0), unit, fx)
                q = core.buy_qty_for_budget(price, fx, min(unit, plan["buy_max_krw"]), plan["buy_max_krw"])
                over = " ⚠️원칙 초과" if unit > plan["buy_max_krw"] else ""
                rows.append({"계좌": r["계좌"], "기법": r.get("기법"), **stt, "오늘 살 수량": f"{q['qty']}주 (≈{q['krw']:,}원){over}"})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         column_config={"투입(원)": st.column_config.NumberColumn(format="%d"),
                                        "남은 예산(원)": st.column_config.NumberColumn(format="%d")})
            st.caption(f"SOXL ${price:.2f} · 환율 {fx:,.0f}원. 목표가는 ①이면 평단 +10%, V2형은 별지점(평단 × (1.10 − 회차/200))도 함께 봐요. "
                       f"오늘 살 수량은 원칙({plan['buy_target_krw']:,}원, 최대 {plan['buy_max_krw']:,}원) 안에서 계산했어요.")
    with t2:
        year = now_kst().year
        real = core.realized_by_year(journal, year)
        kiwoom_usd = float(real.loc[real["Ticker"] == "SOXL", "realized_usd"].sum()) if not real.empty else 0.0
        st.caption(f"매매 일지로 계산한 키움 {year}년 실현 손익: ${kiwoom_usd:,.0f} (≈{kiwoom_usd * fx:,.0f}원, 현재 환율 환산). "
                   "일지가 8월 이후 비어 있거나 매수가 빠져 있으면 실제와 달라요. 정확한 값은 증권사 '양도소득 예상' 화면 숫자를 아래에 넣으세요.")
        c = st.columns(3)
        k_in = c[0].number_input("키움 올해 실현 이익 (원)", -1_000_000_000, 1_000_000_000,
                                 int(plan.get("realized_kiwoom_krw", round(kiwoom_usd * fx))), 100_000, key="tx_k")
        t_in = c[1].number_input("토스 올해 실현 이익 (원)", -1_000_000_000, 1_000_000_000,
                                 int(plan.get("realized_toss_krw", 0) or 0), 100_000, key="tx_t")
        if c[2].button("저장", key="tx_save") and backend() == "supabase":
            save_settings(core.SETTINGS_KEY_PLAN, {**plan, "realized_kiwoom_krw": k_in, "realized_toss_krw": t_in})
            st.rerun()
        cyc = kiwoom_cycles(plan, journal).rename(columns={"계좌": "Account", "보유 수량": "Qty", "평단($)": "Avg"})
        hp = core.harvest_plan(k_in + t_in, cyc[["Account", "Qty", "Avg"]], price, fx)
        m = st.columns(4)
        m[0].metric("올해 실현 이익 합계", f"{hp['realized_krw']:,.0f}원", border=True)
        m[1].metric("공제 뒤 과세 대상", f"{hp['taxable_krw']:,.0f}원", f"공제 {core.TAX['deduction_krw']:,}원", delta_color="off",
                    delta_arrow="off", border=True)
        m[2].metric("예상 세금 (22%)", f"{hp['tax_krw']:,.0f}원", border=True)
        m[3].metric("상쇄에 필요한 SOXL 매도", f"{hp['shares']}주", f"실현 손실 {hp['covered_krw']:,.0f}원", delta_color="off",
                    delta_arrow="off", border=True)
        if not hp["rows"].empty:
            st.dataframe(hp["rows"], hide_index=True, width="stretch")
            if hp["covered_krw"] < hp["taxable_krw"]:
                st.warning("지금 계좌 표의 손실만으로는 과세 대상을 다 상쇄하지 못해요.", icon=":material/warning:")
        st.caption(f"SOXL ${price:.2f}, 환율 {fx:,.0f}원 기준. 손실이 큰 계좌부터 필요한 만큼만 팔고 같은 날 다시 사는 계획이에요. "
                   "세금은 살 때·팔 때 환율로 계산돼서 실제와 다를 수 있어요. 손실은 다음 해로 넘어가지 않으니 필요한 만큼만, "
                   "미국 주식은 다음 영업일 결제라 연말 마감 전에 여유 있게 하세요. 최종 판단은 증권사·세무 상담으로 확인하세요.")


# ==========================================================================
# 페이지: 내 포트폴리오 (토스 장기 계좌) · 포트폴리오 선수 · 리밸런싱 선수
# ==========================================================================
@st.cache_data(ttl=3 * 3600, show_spinner=False)
def cached_portfolio_lab(rule_key: str, target_key: str, core_version: str = "") -> dict:
    """테마 순위 재현 → 주별 목표 비중 → 리밸런싱 방식별 시뮬레이션. 그날까지의 데이터만 사용."""
    rule = core.normalize_rules(json.loads(rule_key))
    daily = {}
    for t in core.PORTFOLIO_TICKERS:
        d = load_long(t, "max", True)
        if d is not None and not d.empty:
            daily[t] = d["Close"].dropna()
    if "SOXL" not in daily:
        return {}
    asof = us_asof()
    hist = load_signal_history("SOXL", "SOXX")
    core_state = core.rule_positions(core.action_signals(core.complete_weeks(hist, asof), rule)) if hist is not None \
        else pd.Series(dtype=float)
    return core.portfolio_lab(daily, core_state, asof, json.loads(target_key))


@st.cache_data(ttl=60, show_spinner=False)
def load_portfolio(backend_name: str) -> tuple[pd.DataFrame, dict]:
    if backend_name != "supabase":
        return pd.DataFrame(columns=["ticker", "shares", "avg_price"]), {}
    try:
        sb = get_supabase()
        h = pd.DataFrame(core.sb_fetch_all(sb, core.PORTFOLIO_TABLE, {"account": "toss"}))
        st_rows = core.sb_fetch_all(sb, core.PORTFOLIO_SETTINGS, {"key": "toss"})
        return (h[["ticker", "shares", "avg_price"]] if not h.empty else pd.DataFrame(columns=["ticker", "shares", "avg_price"]),
                (st_rows[0]["value"] if st_rows else {}))
    except Exception as exc:
        LOGGER.warning("포트폴리오 조회 실패: %s", exc)
        return pd.DataFrame(columns=["ticker", "shares", "avg_price"]), {}


def save_portfolio(edited: pd.DataFrame) -> None:
    sb = get_supabase()
    sb.table(core.PORTFOLIO_TABLE).delete().eq("account", "toss").execute()
    rows = [{"account": "toss", "ticker": str(r["ticker"]).strip().upper(), "shares": float(r["shares"]),
             "avg_price": float(r["avg_price"]) if pd.notna(r["avg_price"]) else None}
            for _, r in edited.iterrows() if str(r.get("ticker") or "").strip() and pd.notna(r.get("shares"))]
    if rows:
        sb.table(core.PORTFOLIO_TABLE).insert(rows).execute()
    load_portfolio.clear()


def save_portfolio_settings(value: dict) -> None:
    get_supabase().table(core.PORTFOLIO_SETTINGS).upsert({"key": "toss", "value": value}, on_conflict="key").execute()
    load_portfolio.clear()


def page_portfolio() -> None:
    page_header("MY PORTFOLIO", "내 포트폴리오", "토스 장기 계좌 · SOXL 핵심 + 주도 테마 + 떠오르는 테마 · 리밸런싱 제안")
    holdings, settings = load_portfolio(backend())
    target = {**core.PORTFOLIO_TARGET, **(settings.get("target") or {})}
    state = load_rule_state(backend())
    with st.expander("목표 비율 설정", icon=":material/tune:"):
        c = st.columns(3)
        core_w = c[0].slider("SOXL 핵심", 0, 100, int(target["core"] * 100), 5, key="pf_core")
        lead_w = c[1].slider("주도 테마", 0, 100, int(target["lead"] * 100), 5, key="pf_lead")
        rise_w = c[2].slider("떠오르는 테마", 0, 100, int(target["rising"] * 100), 5, key="pf_rise")
        cash_w = 100 - core_w - lead_w - rise_w
        if cash_w < 0:
            st.error("합이 100%를 넘어요.")
        else:
            st.caption(f"나머지 현금 {cash_w}%. SOXL 핵심 칸은 규칙 v{state['version']}이 매도 상태면 현금으로 쉬어요.")
            if st.button("이 비율 저장", key="pf_save_target") and backend() == "supabase":
                save_portfolio_settings({**settings, "target": {"core": core_w / 100, "lead": lead_w / 100,
                                                                "rising": rise_w / 100}})
                st.rerun()
        target = {"core": core_w / 100, "lead": lead_w / 100, "rising": rise_w / 100}
    with st.spinner("테마 순위와 포트폴리오 선수 계산 중… (처음에는 1분쯤 걸려요)"):
        lab = cached_portfolio_lab(json.dumps(state["rules"]), json.dumps(target), core.CORE_VERSION)
    if not lab:
        st.warning("시세를 불러오지 못했습니다.")
        return

    section("이번 주 목표 포트폴리오", f"{lab['week']:%Y-%m-%d} 금요일 마감 기준. 테마 순위는 타이탄봇과 같은 계산식이에요.")
    variant = st.segmented_control("방식", ["기본", "추세 필터"], default="기본", key="pf_variant") or "기본"
    tgt = lab["base"] if variant == "기본" else lab["trend"]
    cols = st.columns(3)
    cols[0].metric(f"SOXL 핵심 · 규칙 v{state['version']}", "보유" if lab["core_on"] >= 0.5 else "쉬는 중(현금)",
                   f"목표 {target['core'] * 100:.0f}%", delta_color="off", delta_arrow="off", border=True)
    for col, (label, th) in zip(cols[1:], (("주도 테마", lab["lead"]), ("떠오르는 테마", lab["rising"]))):
        veh, k = lab["vehicles"].get(th, (None, 0))
        col.metric(label, th or "—", f"{veh} ({k}배)" if veh else "", delta_color="off", delta_arrow="off", border=True)
    st.dataframe(pd.DataFrame([{"종목": k if k != "CASH" else "현금", "목표 비중(%)": round(v * 100, 1)}
                               for k, v in sorted(tgt.items(), key=lambda x: -x[1])]), hide_index=True, width="stretch")
    if variant == "추세 필터":
        st.caption("추세 필터: 테마의 1배 대표 ETF가 200일선 아래면 그 칸은 현금으로 쉬어요.")
    raw = lab.get("raw", (None, None))
    if raw != (lab["lead"], lab["rising"]):
        st.caption(f"이번 주 순위만 보면 주도 {raw[0]} · 떠오르는 {raw[1]}이지만, 잦은 교체를 막는 유지 규칙(주도 테마는 5위 안이면 유지, "
                   "떠오르는 테마는 최소 4주 유지) 때문에 위 테마를 유지해요.")
    st.caption("레버리지 ETF가 없는 테마는 2배, 그것도 없으면 1배 대표 ETF를 써요. 반도체는 핵심(SOXL)과 겹쳐서 테마 칸에서 빼요. "
               f"{core.PORTFOLIO_START[:4]}년 이후 테마 교체 {lab.get('switches', 0)}번.")

    section("내 토스 계좌", "주식 모으기로 모은 장기 투자 계좌. 표를 직접 고치고 저장하세요 (티커 · 주식 수 · 평균 단가 $).")
    edited = st.data_editor(holdings, num_rows="dynamic", width="stretch", key="pf_editor",
                            column_config={"ticker": st.column_config.TextColumn("티커"),
                                           "shares": st.column_config.NumberColumn("주식 수", format="%.4f", min_value=0),
                                           "avg_price": st.column_config.NumberColumn("평균 단가($)", format="%.2f")})
    if st.button("보유 내역 저장", type="primary", key="pf_save") and backend() == "supabase":
        save_portfolio(edited)
        flash("토스 보유 내역을 저장했어요.")
        st.rerun()
    fx = fetch_usdkrw() or 1350.0
    tick = tuple(sorted({str(t).upper() for t in edited["ticker"].dropna() if str(t).strip()} |
                        {k for k in tgt if k != "CASH"}))
    snap = load_snapshot(tick) if tick else {}
    prices = {t: v[0] for t, v in snap.items()}
    if not edited.empty:
        view = edited.dropna(subset=["ticker"]).copy()
        view["ticker"] = view["ticker"].str.upper()
        view["현재가"] = view["ticker"].map(prices)
        view["평가($)"] = (view["shares"].astype(float) * view["현재가"]).round(2)
        view["손익(%)"] = ((view["현재가"] / view["avg_price"].astype(float) - 1) * 100).round(1)
        tot = view["평가($)"].sum()
        view["비중(%)"] = (view["평가($)"] / tot * 100).round(1) if tot else 0
        m = st.columns(3)
        m[0].metric("평가 금액", fmt_usd(tot), fmt_krw(tot * fx), delta_color="off", delta_arrow="off", border=True)
        cost = (view["shares"].astype(float) * view["avg_price"].astype(float)).sum()
        m[1].metric("평가 손익", fmt_usd(tot - cost), f"{(tot / cost - 1) * 100:+.1f}%" if cost else None, border=True)
        m[2].metric("종목 수", f"{len(view)}개", border=True)
        st.dataframe(view.rename(columns={"ticker": "티커", "shares": "주식 수", "avg_price": "평균 단가"}),
                     hide_index=True, width="stretch")

    section("리밸런싱 제안", "많이 오른 종목은 일부 팔아 현금으로 챙기고, 많이 떨어진 종목은 그 현금으로 다시 사는 방식이 기본이에요. "
                         "토스는 소수점 매매가 돼서 주식 수를 소수로 보여줘요.")
    champ = settings.get("champion", core.PORTFOLIO_CHAMPION)
    st.markdown(f"👑 **지금 쓰는 리밸런싱 방식: {core.PORTFOLIO_PLAYERS[champ]}** · {core.PORTFOLIO_TIMING[champ]}")
    default_mode = core.PORTFOLIO_MODE.get(champ, "수익 실현형")
    mode = st.segmented_control("주문 계산 방식", ["수익 실현형", "새 돈으로만", "목표에 정확히"], default=default_mode,
                                key="pf_mode") or default_mode
    c = st.columns(4)
    cash_krw = c[0].number_input("계좌의 현금 (원)", min_value=0, value=int(settings.get("cash_krw", 0) or 0), step=10000,
                                 key="pf_cash")
    new_krw = c[1].number_input("이번에 넣을 돈 (원)", min_value=0, value=int(settings.get("monthly_krw", 0) or 0),
                                step=10000, key="pf_newcash")
    buf = c[2].slider("현금 완충 비율", 0, 50, int(settings.get("cash_buffer", core.HARVEST["cash"]) * 100), 5,
                      key="pf_buf", help="수익 실현형에서 떨어질 때 다시 살 돈으로 늘 남겨 두는 현금 비율", format="%d%%")
    band = c[3].slider("매매 기준 (목표 대비)", 10, 50, int(settings.get("band", core.HARVEST["band"]) * 100), 5,
                       key="pf_band", help="목표 비중보다 이만큼(상대 %) 커지면 초과분을 팔고, 이만큼 작아지면 다시 사요",
                       format="±%d%%")
    if st.button("이 설정 저장", key="pf_save_reb") and backend() == "supabase":
        save_portfolio_settings({**settings, "cash_krw": cash_krw, "monthly_krw": new_krw,
                                 "cash_buffer": buf / 100, "band": band / 100})
        st.rerun()
    hold_now = edited.dropna(subset=["ticker"]).assign(ticker=lambda d: d["ticker"].str.upper())
    if mode == "수익 실현형":
        tgt_h = core.with_cash_buffer({k: v for k, v in tgt.items()}, buf / 100)
        orders = core.harvest_orders(hold_now, prices, tgt_h, cash=cash_krw / fx, new_cash=new_krw / fx, band=band / 100)
        st.caption(f"목표 비중에 현금 {buf}%를 남겨 두고, 각 종목이 목표보다 {band}% 넘게 커지면 초과분을 팔아 현금으로, "
                   f"{band}% 넘게 작아지면 현금으로 다시 사요. 목표에 없는 기존 종목(예: 장기로 모은 종목)은 자동으로 팔지 않아요.")
    else:
        orders = core.rebalance_orders(hold_now, prices, tgt, new_cash=(cash_krw + new_krw) / fx,
                                       buy_only=(mode == "새 돈으로만"))
    st.dataframe(orders, hide_index=True, width="stretch",
                 column_config={"금액($)": st.column_config.NumberColumn(format="$%.2f"),
                                "지금 금액($)": st.column_config.NumberColumn(format="$%.2f")})
    st.caption(f"환율 {fx:,.0f}원 기준. 제안일 뿐이고 실제 주문은 직접 판단해서 하세요. 이익 난 종목을 팔면 해외주식 양도세(연 250만 원 공제 뒤 22%) "
               "계산에 들어가고, 손실 종목을 팔면 그해 손실로 잡혀요(12월 손실 실현 계획과 함께 고려).")

    _, mins = load_arena(backend())
    pm = mins[mins["ticker"] == "PORTFOLIO"].sort_values("week") if not mins.empty else mins
    with st.expander("AI 위원회 포트폴리오 회의록 (매주 토요일 아침)", icon=":material/groups:", expanded=not pm.empty):
        if pm.empty:
            st.caption("아직 회의록이 없어요. 이번 주 토요일 아침 회의부터 이 계좌의 리밸런싱을 논의해 기록합니다.")
        else:
            last = pm.iloc[-1]
            st.markdown(f"**{str(last['week'])[:10]} 주 · {last['decision']}**")
            st.markdown(last["minutes"] or "—")

    section("포트폴리오 선수 · 리밸런싱 선수 성적",
            f"{core.PORTFOLIO_START[:4]}년부터 매주 금요일에 위 규칙대로 했다면? 같은 목표 비중을 리밸런싱 방식만 바꿔 비교해요. "
            f"실전 기록은 {core.PORTFOLIO_LIVE_START}부터 이어서 쌓여요.")
    tbl = lab["table"].sort_values("칼마", ascending=False).copy()
    verdicts = core.portfolio_verdicts(lab["curves"], champ)
    tbl["선수"] = [("👑 " if i == champ else "") + n for i, n in zip(tbl["id"], tbl["선수"])]
    tbl["교체 판정"] = [("현재 방식" if i == champ else "기준선" if i.startswith("bm_") else verdicts.get(i, ("short", "—"))[1])
                    for i in tbl["id"]]
    st.dataframe(tbl.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={**{c_: st.column_config.NumberColumn(format="%+.1f%%")
                                   for c_ in ("연평균(%)", "최대 낙폭(%)", "최근 2년(%)", "실전(%)")},
                                "교체 판정": st.column_config.TextColumn(width="large")})
    th_ = core.PORTFOLIO_PROMOTE
    st.caption(f"교체 기준: {core.PORTFOLIO_LIVE_START}부터의 실전 기록만으로, {th_['weeks']}주 이상 지나고 칼마가 지금 방식보다 높고 "
               f"최대 낙폭이 {th_['mdd_slack']:.0f}%p 넘게 나쁘지 않으면 '교체 추천'. 바꿀지는 직접 결정해요. 과거 성적은 참고만 해요.")
    for pid, (code, text) in verdicts.items():
        if code != "promote":
            continue
        cc = st.columns([3, 1], vertical_alignment="center")
        cc[0].success(f"**{core.PORTFOLIO_PLAYERS[pid]}** — {text}", icon=":material/emoji_events:")
        if cc[1].button("이 방식으로 바꾸기", key=f"pf_crown_{pid}", type="primary", width="stretch") and backend() == "supabase":
            hist_ = list(settings.get("champion_history") or [])
            hist_.append({"from": champ, "to": pid, "at": f"{now_kst():%Y-%m-%d %H:%M}", "why": text})
            save_portfolio_settings({**settings, "champion": pid, "champion_history": hist_})
            st.rerun()
    if settings.get("champion_history"):
        with st.expander("방식 교체 이력"):
            st.dataframe(pd.DataFrame([{"날짜": h["at"], "이전": core.PORTFOLIO_PLAYERS.get(h["from"], h["from"]),
                                        "새 방식": core.PORTFOLIO_PLAYERS.get(h["to"], h["to"]), "이유": h["why"]}
                                       for h in settings["champion_history"]]), hide_index=True, width="stretch")
    curves = lab["curves"]
    fig = go.Figure()
    for i, pid in enumerate(curves.columns):
        fig.add_trace(go.Scatter(x=curves.index, y=curves[pid], name=core.PORTFOLIO_PLAYERS[pid],
                                 line=dict(width=2.5 if pid == "pf_month" else 1.5,
                                           color=PLAYER_COLORS[i % len(PLAYER_COLORS)])))
    base_layout(fig, 360)
    fig.update_yaxes(type="log", title_text="자산 (시작 = 1, 로그)")
    show_chart(fig, key="pf_curves")
    st.caption("칼마 = 연평균 ÷ |최대 낙폭|. 높을수록 위험 대비 수익이 좋아요. 거래 비용 0.1% 반영, 세금은 미반영. "
               "레버리지 ETF가 상장되기 전 기간은 1배 ETF로 대신했어요.")


# ==========================================================================
# 페이지 2: 시장 레이더
# ==========================================================================
def page_radar() -> None:
    page_header("MARKET RADAR", "시장 레이더", "미국 10년물 금리 · 반도체 핵심 종목 · 매크로 뉴스 센티먼트")
    render_rebound_checklist()
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
        "학습 수익": pct_text(m.get("train_ret")), "검증 수익": pct_text(m.get("test_ret"))} for m in models]),
        hide_index=True)

    section("리그 설정", "매수는 전일 종가 기준 지정가, 매도는 평단 기준 목표가 지정가로 체결을 가정합니다. "
                     "매일 아침 7시(KST, 화~토) 자동 실행이 같은 설정으로 장부와 일별 성적을 저장합니다.")
    lead = models[0]
    trained = [m.get("trained_at") for m in models if m.get("trained_at")]
    trained_end = max(trained) if trained else None
    saved_start = lead.get("league_start") or trained_end
    default_start = dt.date.fromisoformat(saved_start) if saved_start else today_kst() - dt.timedelta(days=180)
    c = st.columns(4)
    ticker = c[0].text_input("종목", value=lead.get("league_ticker") or lead.get("ticker", "SOXL"),
                             key="league_ticker").upper().strip()
    start = c[1].date_input("리그 시작일", value=default_start, max_value=today_kst(), key="league_start")
    capital = c[2].number_input("요원별 자본 ($)", min_value=1_000.0, value=float(lead.get("league_capital", 10_000.0)),
                                step=1_000.0, key="league_cap")
    fee_bps = c[3].number_input("편도 비용 (bp)", min_value=0, max_value=100, value=int(lead.get("league_fee", 10)),
                                key="league_fee",
                                help="수수료 + 슬리피지. 키움 해외주식 기본 수수료는 약 0.07~0.25% 수준입니다.")
    if trained_end and start.isoformat() < trained_end:
        st.warning(f"리그 시작일이 훈련일({trained_end})보다 앞입니다. 훈련에 이미 쓴 기간이 섞여서 "
                   "성적이 실제보다 좋아 보일 수 있어요. 진짜 실전 검증은 훈련일 이후부터입니다.",
                   icon=":material/warning:")
    changed = (ticker != (lead.get("league_ticker") or lead.get("ticker")) or start.isoformat() != saved_start
               or float(capital) != float(lead.get("league_capital", 10_000.0))
               or int(fee_bps) != int(lead.get("league_fee", 10)))
    if changed and st.button("이 설정을 자동 실행에 저장", icon=":material/schedule:"):
        try:
            save_models([{**m, "league_ticker": ticker, "league_start": start.isoformat(),
                          "league_capital": float(capital), "league_fee": int(fee_bps)} for m in models])
            flash("자동 실행 설정을 저장했습니다.")
            st.rerun()
        except Exception as exc:
            st.error(f"저장 실패: {exc}")
    with st.spinner("리그 계산 중…"):
        result = run_league(ticker, start.isoformat(), float(capital), float(fee_bps),
                            tuple(tuple(m["genes"]) for m in models), tuple(m["name"] for m in models))
    if result is None:
        latest = load_recent(ticker)
        if latest is not None and pd.Timestamp(start) > latest.index[-1]:
            st.info(f"리그는 {start:%Y-%m-%d} 이후 첫 거래일부터 시작됩니다. 다음 날 아침 자동 실행 뒤 성적이 표시돼요. "
                    "그동안 아래 기간별 안정성 검증으로 요원을 미리 점검할 수 있습니다.", icon=":material/hourglass_top:")
            section("기간별 안정성 검증", "최근 5년을 6개월(126거래일)씩 잘라 각 구간을 처음부터 따로 매매합니다.")
            r_table, r_sum = cached_rolling(ticker, "5y", 126, float(fee_bps), tuple(tuple(m["genes"]) for m in models),
                                            tuple(m["name"] for m in models))
            render_rolling(r_table, r_sum, "league_rolling_wait")
        else:
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
        cmd_budget = st.session_state.get("budget_krw_SOXL", int(((load_settings(core.SETTINGS_KEY_PLAN, backend())
                                                                    .get("agent_budget") or {}).get("SOXL", 10_000_000)))) / fx_rate()
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

    section("실전 검증", f"리그 기간의 성적으로 요원 상태를 판정합니다 (최소 {core.LIVE_MIN_DAYS}거래일 관찰 후). "
                       "기준: 보유(Buy & Hold) 대비 −10%p 이내면 정상, 손실 중 낙폭이 훈련 검증 MDD의 1.5배(최소 30%)를 넘으면 교체 권고.")
    by_name = {m["name"]: m for m in models}
    verdicts = []
    for _, row in result["summary"].iterrows():
        name, ret, mdd = row["요원"], float(row["수익률(%)"]), float(row["MDD(%)"])
        mdl = by_name.get(name, {})
        state, why = core.judge_live(ret, mdd, result["bh_ret"], result["days"], mdl.get("test_mdd"))
        label = {"watch": "👀 관찰 중", "ok": "✅ 정상", "warn": "⚠️ 주의", "retire": "❌ 교체 권고"}[state]
        verdicts.append({"요원": name, "판정": label, "리그 수익(%)": round(ret, 2),
                         "보유 대비(%p)": round(ret - result["bh_ret"], 2), "리그 MDD(%)": round(mdd, 1),
                         "훈련 검증 수익(%)": mdl.get("test_ret"), "설명": why})
    vdf = pd.DataFrame(verdicts)
    st.dataframe(vdf, hide_index=True, column_config={
        "리그 수익(%)": st.column_config.NumberColumn(format="%+.2f"),
        "보유 대비(%p)": st.column_config.NumberColumn(format="%+.2f"),
        "훈련 검증 수익(%)": st.column_config.NumberColumn(format="%+.1f")})
    if (vdf["판정"] == "❌ 교체 권고").any():
        st.error("교체 권고 요원이 있습니다. 훈련소에서 다시 훈련해 검증을 통과한 요원으로 바꾸는 것을 권합니다.",
                 icon=":material/report:")

    section("기간별 안정성 검증", "최근 5년을 6개월(126거래일)씩 잘라 각 구간을 처음부터 따로 매매합니다. "
                              "특정 시기에만 운 좋게 맞은 요원인지 확인합니다 (학습에 쓴 기간도 포함되니 참고용).")
    with st.spinner("구간별 검증 중…"):
        r_table, r_sum = cached_rolling(ticker, "5y", 126, float(fee_bps), tuple(tuple(m["genes"]) for m in models),
                                        tuple(m["name"] for m in models))
    render_rolling(r_table, r_sum, "league_rolling")

    runs = load_job_runs(backend())
    with st.expander("자동 실행 기록", icon=":material/schedule:"):
        if runs.empty:
            st.caption("아직 자동 실행 기록이 없습니다. GitHub 저장소 Actions 탭에서 'Run workflow'로 시험해 보세요.")
        else:
            st.dataframe(runs.drop(columns=["id"], errors="ignore"), hide_index=True)

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
    page_header("BOOTCAMP", "AI 훈련소", "유전 알고리즘 (선택 · 교차 · 변이) + 부트스트랩 시나리오 + 미사용 구간 · 기간별 검증")
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
        penalty = c[3].slider("낙폭 페널티", 0.0, 2.0, 0.5, 0.1,
                              help="적합도 = (평균 수익 + 최악 시나리오 수익) ÷ 2 − 페널티 × 평균 MDD. 올리면 낙폭 작은 요원이 뽑힙니다.")
        c = st.columns(4)
        train_ratio = c[0].slider("학습 구간 비율", 0.6, 0.9, 0.8, 0.05, help="나머지 뒤쪽 구간은 검증 전용")
        seed = c[1].number_input("난수 시드", 0, 99_999, 42, help="같은 시드 = 같은 결과 (재현 가능)")
        include = c[2].toggle("현재 요원을 초기 개체에 포함", value=False,
                             help="현재 요원이 검증을 통과하지 못했다면 끄는 것이 좋습니다 (과적합 유전자를 물려받음).")
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
                st.session_state["train_result"] = {**result, "ticker": ticker, "years": years,
                                                    "fee_bps": fee_bps, "trained_at": today_kst().isoformat()}
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
    m[1].metric("1위 학습 수익 (실제 차트)", f"{top['train_ret'].iloc[0]:+.1f}%",
                f"가상 차트 평균 {top['synthetic_ret'].iloc[0]:+.1f}%", delta_color="off", delta_arrow="off", border=True)
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
        st.caption("세대별 적합도")
        show_chart(fig, key="train_curve")
    with right:
        table = top.rename(columns={"rank": "순위", "fitness": "적합도", "train_ret": "학습수익(%)",
                                    "train_avg": "시나리오평균(%)", "synthetic_ret": "가상차트(%)",
                                    "train_mdd": "학습MDD(%)", "train_worst": "최악시나리오(%)",
                                    "test_ret": "검증수익(%)", "test_mdd": "검증MDD(%)", "test_trades": "검증체결"})
        pct = st.column_config.NumberColumn(format="%+.1f")
        st.dataframe(table.drop(columns=["genes"]), hide_index=True, column_config={
            "적합도": st.column_config.NumberColumn(format="%.2f"), "학습수익(%)": pct, "최악시나리오(%)": pct,
            "시나리오평균(%)": pct, "가상차트(%)": pct,
            "검증수익(%)": pct, "학습MDD(%)": st.column_config.NumberColumn(format="%.1f"),
            "검증MDD(%)": st.column_config.NumberColumn(format="%.1f")})
        with st.expander("상위 3명 유전자", icon=":material/genetics:"):
            genes = pd.DataFrame([r["genes"] for r in result["top"]], columns=core.GENE_NAMES,
                                 index=[f"Rank {r['rank']}" for r in result["top"]]).T
            st.dataframe(genes, column_config={c: st.column_config.NumberColumn(format="%.3f") for c in genes.columns})

    section("배치 전 검증", "기준: " + " · ".join(core.TRAIN_RULES))
    names = [f"Rank {r['rank']}" for r in result["top"]]
    with st.spinner("기간별 안정성 검증 중…"):
        r_table, r_sum = cached_rolling(result["ticker"], f"{result.get('years', 3)}y", 126, float(result.get("fee_bps", 10)),
                                        tuple(tuple(r["genes"]) for r in result["top"]), tuple(names))
    pos_ratio = dict(zip(r_sum["요원"], r_sum["수익 구간(%)"])) if r_sum is not None and not r_sum.empty else {}
    judged = []
    for r, n in zip(result["top"], names):
        verdict, reasons = core.judge_training(r["test_ret"], r["train_ret"], pos_ratio.get(n), r.get("test_mdd"),
                                               result.get("buy_hold_test_mdd"))
        judged.append({**r, "passed": verdict == "pass", "reasons": reasons})
    st.dataframe(pd.DataFrame([{"요원": n, "판정": "✅ 통과" if j["passed"] else "❌ 불통과",
                                "검증수익(%)": j["test_ret"], "수익 구간(%)": pos_ratio.get(n),
                                "사유": " · ".join(j["reasons"]) or "모든 기준 충족"} for n, j in zip(names, judged)]),
                 hide_index=True, column_config={"검증수익(%)": st.column_config.NumberColumn(format="%+.1f")})
    render_rolling(r_table, r_sum, "train_rolling")

    section("실전 배치", "선택한 요원이 기존 요원을 대체하고, AI 가상 매매 장부는 초기화됩니다. 리그는 오늘부터 시작합니다.")
    only_passed = st.toggle("검증 통과 요원만 배치", value=True, key="deploy_only_passed")
    chosen = [j for j in judged if j["passed"] or not only_passed]
    if not chosen:
        st.warning("검증을 통과한 요원이 없습니다. 학습 기간·시나리오 수·낙폭 페널티를 바꿔 다시 훈련하거나, "
                   "위 토글을 끄고 배치하세요 (권장하지 않음).", icon=":material/warning:")
        return
    c1, c2 = st.columns([2, 1], vertical_alignment="center")
    ok = c1.checkbox(f"{len(chosen)}명 배치 · 기존 요원 교체와 AI 장부 초기화에 동의합니다.", key="deploy_ok")
    if c2.button(f"{len(chosen)}명 배치", type="primary", disabled=not ok, icon=":material/rocket_launch:", width="stretch"):
        models = [{"name": f"Agent_Rank_{i + 1} ({result['ticker']})", "rank": i + 1, "genes": r["genes"],
                   "ticker": result["ticker"], "trained_at": result["trained_at"], "years": result.get("years"),
                   "train_ret": round(r["train_ret"], 2), "test_ret": round(r["test_ret"], 2),
                   "test_mdd": round(r["test_mdd"], 2), "passed": r["passed"],
                   "league_ticker": result["ticker"], "league_start": today_kst().isoformat(),
                   "league_capital": 10_000.0, "league_fee": int(result.get("fee_bps", 10))}
                  for i, r in enumerate(chosen)]
        try:
            save_models(models)
            save_table("ai_ledger", core.empty_table("ai_ledger"))
            run_league.clear()
            flash(f"{len(chosen)}명이 실전 리그에 배치되었습니다.", ":material/rocket_launch:")
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
# 페이지: 신호 관제 (반등 신호 점수판 · 과거 확률 · 예측 장부)
# ==========================================================================
PRED_TABLE = "valhalla_predictions"
PRED_FILE = BASE_DIR / "predictions.csv"
PRED_COLUMNS = ["week", "ticker", "close", "chart_score", "macro_score", "signals", "macro_signals",
                "p_up_4w", "base_4w", "n_4w", "p_up_12w", "base_12w", "n_12w", "ret_4w", "ret_12w"]
PRED_SQL = f"""create table if not exists {PRED_TABLE} (
  id bigserial primary key,
  week date not null,
  ticker text not null default 'SOXL',
  created_at timestamptz default now(),
  close double precision, chart_score int, macro_score int,
  signals text, macro_signals text,
  p_up_4w double precision, base_4w double precision, n_4w int,
  p_up_12w double precision, base_12w double precision, n_12w int,
  ret_4w double precision, ret_12w double precision,
  unique (week, ticker)
);"""
ET = ZoneInfo("America/New_York")


def us_asof() -> pd.Timestamp:
    """미국 정규장이 마감된 마지막 날짜 (주봉 마감 판정용)."""
    now = dt.datetime.now(ET)
    day = pd.Timestamp(now.date())
    return day if now.hour >= 16 else day - pd.Timedelta(days=1)


@st.cache_data(ttl=3600, show_spinner=False)
def load_signal_history(ticker: str, partner: str) -> pd.DataFrame | None:
    daily = load_long(ticker, "max", True)
    if daily is None or len(daily) < 300:
        return None
    partner_df = load_long(partner, "max", True) if partner else None
    macro = {t: load_long(t, "max", False) for t in core.MACRO_TICKERS_SIGNAL.values()}
    return core.build_signal_history(daily, partner_df, macro)


@st.cache_data(ttl=1800, show_spinner=False)
def load_daily_signal_history(ticker: str, partner: str, asof: str) -> pd.DataFrame:
    """일봉 버전 신호 기록 (장이 끝난 날까지만). asof는 캐시를 날짜별로 나누는 용도."""
    daily = load_long(ticker, "max", True)
    if daily is None or len(daily) < 300:
        return pd.DataFrame()
    daily = daily[daily.index <= pd.Timestamp(asof)]
    partner_df = load_long(partner, "max", True) if partner else None
    macro = {t: load_long(t, "max", False) for t in core.MACRO_TICKERS_SIGNAL.values()}
    return core.build_daily_signal_history(daily, partner_df, macro)


@st.cache_data(ttl=1800, show_spinner=False)
def cached_daily_positions(ticker: str, partner: str, rules_key: str, asof: str, core_version: str = "") -> pd.DataFrame:
    """일봉 리그 선수들의 날짜별 비중 (+ close 열)."""
    rules = [(int(v), core.normalize_rules(r)) for v, r in json.loads(rules_key)]
    daily = load_long(ticker, "max", True)
    if daily is None or len(daily) < 300:
        return pd.DataFrame()
    daily = daily[daily.index <= pd.Timestamp(asof)]
    partner_df = load_long(partner, "max", True) if partner else None
    dh = load_daily_signal_history(ticker, partner, asof)
    ref = core.trend_reference(ticker, daily, partner_df)
    pos = core.daily_player_positions(daily, ref, dh, rules, ticker)
    pos["close"] = daily["Close"]
    return pos


def render_daily_rules(ticker: str, partner: str, rules: list[tuple[int, dict]]) -> None:
    """일봉 리그: 모든 선수의 오늘 비중과 오늘 신호."""
    asof = f"{us_asof():%Y-%m-%d}"
    pos = cached_daily_positions(ticker, partner, json.dumps([[v, r] for v, r in rules]), asof, core.CORE_VERSION)
    if pos.empty:
        return
    players = pos.drop(columns=["close"])
    today = core.daily_today(players)
    if today.empty:
        return
    day = players.index[-1]
    with st.container(border=True):
        st.markdown(f"**일봉 리그 · {day:%m/%d} 장 마감 기준** · 모든 선수가 매일 비중을 냅니다. 규칙은 '오늘로 끝나는 최근 "
                    "5거래일'로 매일 판단하고, 무한매수 기법·찰스 시스템은 원래 방식대로 매일 매매해요. "
                    f"비중이 25%p 이상 바뀐 날을 매수·매도로 표시해요. ({ticker} ${pos['close'].iloc[-1]:.2f})")
        today.insert(0, "선수", [core.player_label(i) for i in today["id"]])
        today["오늘 비중"] = (today["오늘 비중"] * 100).round(0)
        fired = today[today["오늘 신호"] != ""]
        if not fired.empty:
            st.markdown("오늘 신호: " + " · ".join(
                pill(f"{r['선수']} {r['오늘 신호']}", "stop" if r["오늘 신호"] == "매수" else "info")
                for _, r in fired.iterrows()), unsafe_allow_html=True)
        st.dataframe(today.drop(columns=["id"]), hide_index=True, width="stretch",
                     column_config={"오늘 비중": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%"),
                                    "어제 대비(%p)": st.column_config.NumberColumn(format="%+.0f")})


@st.cache_data(ttl=3600, show_spinner=False)
def cached_daily_vs_weekly(ticker: str, partner: str, rules_key: str, asof: str, core_version: str = "") -> pd.DataFrame:
    """일봉 리그 모든 선수 + 주봉 규칙(금요일에 정한 비중을 다음 주 내내 유지)을 일 단위로 비교."""
    rules = [(int(v), core.normalize_rules(r)) for v, r in json.loads(rules_key)]
    pos = cached_daily_positions(ticker, partner, rules_key, asof, core_version)
    hist = load_signal_history(ticker, partner)
    if pos.empty or hist is None:
        return pd.DataFrame()
    wk = core.complete_weeks(hist, us_asof())
    close = pos["close"]
    players = pos.drop(columns=["close"])
    names = {c: core.player_label(c) + " · 일봉" for c in players.columns}
    names["hold"] = "그냥 보유"
    for v, rule in rules:
        players[f"w{v}"] = core.weekly_to_daily_positions(core.rule_positions(core.action_signals(wk, rule)), close.index)
        names[f"w{v}"] = f"규칙 v{v} · 주봉"
    start = max(players[c].first_valid_index() for c in [f"rule_v{rules[0][0]}", f"w{rules[0][0]}"])
    bt = core.backtest_daily(players[players.index >= start], close[close.index >= start])
    bt["전략"] = bt["전략"].map(names)
    return bt


@st.cache_data(ttl=300, show_spinner=False)
def load_daily_ledger(backend_name: str) -> pd.DataFrame:
    if backend_name != "supabase":
        return pd.DataFrame()
    try:
        return pd.DataFrame(core.sb_fetch_all(get_supabase(), core.DAILY_TABLE, {}))
    except Exception as exc:
        LOGGER.warning("일봉 리그 기록 조회 실패: %s", exc)
        return pd.DataFrame()


@st.cache_data(ttl=60, show_spinner=False)
def load_predictions(backend_name: str) -> tuple[pd.DataFrame, str | None]:
    """(장부, 오류 메시지). Supabase 테이블이 없으면 오류 메시지를 돌려줌."""
    try:
        if backend_name == "supabase":
            rows = get_supabase().table(PRED_TABLE).select("*").order("week").execute().data or []
            df = pd.DataFrame(rows)
        else:
            df = pd.read_csv(PRED_FILE) if PRED_FILE.exists() else pd.DataFrame()
    except Exception as exc:
        LOGGER.warning("예측 장부 조회 실패: %s", exc)
        return pd.DataFrame(columns=PRED_COLUMNS), str(exc)
    for col in PRED_COLUMNS:
        if col not in df:
            df[col] = np.nan
    return df[PRED_COLUMNS], None


@st.cache_data(ttl=120, show_spinner=False)
def load_rule_state(backend_name: str) -> dict:
    """활성 규칙·승인 대기 제안·이력 (Supabase 없으면 기본 규칙)."""
    default = {"version": 1, "rules": core.normalize_rules(core.ACTION_RULES), "pending": None, "history": [],
               "challengers": [], "live": pd.DataFrame()}
    if backend_name != "supabase":
        return default
    try:
        sb = get_supabase()
        version, rules = core.load_active_rule(sb)
        try:
            live = pd.DataFrame(_sb_fetch_all(core.RULE_SIGNAL_TABLE))
        except Exception:
            live = pd.DataFrame()
        return {"version": version, "rules": rules, "pending": core.pending_proposal(sb), "history": core.rule_history(sb),
                "challengers": core.load_challengers(sb), "live": live}
    except Exception as exc:
        LOGGER.warning("규칙 조회 실패 (테이블 없음?): %s", exc)
        return default


@st.cache_data(ttl=600, show_spinner=False)
def load_reports(backend_name: str) -> pd.DataFrame:
    """매일 자동 점검 스냅샷 (Supabase 전용)."""
    if backend_name != "supabase":
        return pd.DataFrame()
    try:
        return pd.DataFrame(_sb_fetch_all(core.REPORT_TABLE))
    except Exception as exc:
        LOGGER.warning("자동 점검 기록 조회 실패: %s", exc)
        return pd.DataFrame()


def save_prediction(pred: dict) -> None:
    row = {k: pred.get(k) for k in PRED_COLUMNS if k in pred}
    if backend() == "supabase":
        get_supabase().table(PRED_TABLE).upsert(row, on_conflict="week,ticker").execute()
    else:
        df, _ = load_predictions("local")
        df = df[~((df["week"].astype(str) == row["week"]) & (df["ticker"] == row["ticker"]))]
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        _atomic_write(PRED_FILE, df.to_csv(index=False))
    load_predictions.clear()


def _signal_rows(last: pd.Series) -> pd.DataFrame:
    rows = []
    for key, label, desc in core.CHART_SIGNALS:
        v = last.get(key)
        rows.append({"구분": "차트", "신호": label, "상태": "—" if pd.isna(v) else ("✅ 켜짐" if v else "⬜ 꺼짐"),
                     "기준": desc})
    for key, label, desc, ticker in core.MACRO_SIGNALS:
        v = last.get(key)
        rows.append({"구분": "거시", "신호": label, "상태": "—" if pd.isna(v) else ("✅ 켜짐" if v else "⬜ 꺼짐"),
                     "기준": f"{desc} ({ticker})"})
    return pd.DataFrame(rows)


def _streak(history: pd.DataFrame, threshold: int = 3) -> int:
    n = 0
    for score in history["chart_score"].iloc[::-1]:
        if score >= threshold:
            n += 1
        else:
            break
    return n


def render_actions(done: pd.DataFrame, ticker: str, partner: str) -> None:
    """매수·매도 행동 신호: 현재 상태, 차트 표시, 과거 적중률, '신호대로 했다면' 비교."""
    state = load_rule_state(backend())
    rules, text = state["rules"], core.rule_text(state["rules"])
    section(f"매수 · 매도 신호 · 규칙 v{state['version']}", f"매수: {text['매수']} · 매도: {text['매도']}. "
                             "마감된 주봉 기준이며, 조건이 처음 켜진 주에만 표시합니다.")
    sig = core.action_signals(done, rules)
    if sig.empty:
        st.caption("신호를 계산할 데이터가 부족합니다.")
        return
    last = sig.iloc[-1]
    last_act = sig[sig["action"] != ""].tail(1)
    b, sl = rules["buy"], rules["sell"]
    recent = sig["chart_score"].tail(b["weeks"]).tolist()
    c = st.columns([1.3, 1, 1])
    with c[0].container(border=True):
        if last["action"] == "매수":
            st.markdown(f"**이번 주 신호** {pill('매수 신호 발생', 'stop')}", unsafe_allow_html=True)
        elif last["action"] == "매도":
            st.markdown(f"**이번 주 신호** {pill('매도 신호 발생', 'info')}", unsafe_allow_html=True)
        else:
            st.markdown(f"**이번 주 신호** {pill('새 신호 없음', 'warn')}", unsafe_allow_html=True)
        state_txt = {"매수": "매수 신호 이후 구간 (조건이 다시 켜져도 매도 신호 전까지는 새로 표시하지 않음)",
                     "매도": "매도 신호 이후 구간 (매수 신호를 기다리는 중)"}.get(last["state"], "아직 신호 없음")
        st.caption(f"현재 상태: {state_txt}")
        if not last_act.empty:
            r = last_act.iloc[0]
            st.caption(f"마지막 신호: {r.name:%Y-%m-%d} {r['action']} (그때 종가 {fmt_usd(r['close'])}, "
                       f"지금 {(last['close'] / r['close'] - 1) * 100:+.1f}%)")
    c[1].metric("매수 조건 진행", f"{sum(x >= b['chart_min'] for x in recent)} / {b['weeks']}주",
                f"거시 {int(last['macro_score'])}점 (필요 {b['macro_min']}점)", delta_color="off", delta_arrow="off",
                border=True, help=text["매수"])
    c[2].metric("매도 조건 진행", f"{sum(x <= sl['chart_max'] for x in sig['chart_score'].tail(sl['weeks']))} / {sl['weeks']}주",
                "20일선 아래" if last["above_ma20d"] == False else "20일선 위",  # noqa: E712
                delta_color="off", delta_arrow="off", border=True, help=text["매도"])

    for cv, crule in state.get("challengers", []):
        last_c = core.action_signals(done, crule).iloc[-1]
        st.caption(f"도전 규칙 v{cv} ({core.rule_text(crule)['매수']}): 이번 주 "
                   f"{last_c['action'] + ' 신호' if last_c['action'] else '새 신호 없음'} · 현재 상태 {last_c['state'] or '신호 없음'}")
    render_daily_rules(ticker, partner, [(state["version"], rules)] + list(state.get("challengers", [])))
    render_player_chart(ticker, partner, sig.index[-1] - pd.Timedelta(weeks=156))

    bt = core.action_backtest(sig)
    if not bt:
        return
    fmt = {"적중률(%)": st.column_config.NumberColumn(format="%.1f%%"),
           "평소 확률(%)": st.column_config.NumberColumn(format="%.1f%%"),
           "평균 수익(%)": st.column_config.NumberColumn(format="%+.1f%%"),
           "평소 평균(%)": st.column_config.NumberColumn(format="%+.1f%%")}
    t1, t2, t3 = st.tabs(["매수 신호 성적", "매도 신호 성적", "신호대로 했다면"])
    for tab, kind, note in ((t1, "buy", "적중 = 신호 뒤 실제로 오름"), (t2, "sell", "적중 = 신호 뒤 실제로 내림 (피한 하락)")):
        with tab:
            st.caption(f"{note}. '평소 확률'보다 높아야 쓸모 있는 신호입니다. 학습 {bt['start']:%Y-%m}~{bt['cutoff']:%Y-%m} / 검증 = 최근 2년.")
            a, bcol = st.columns(2)
            a.markdown("**학습 구간**")
            a.dataframe(bt[f"train_{kind}"], hide_index=True, width="stretch", column_config=fmt)
            bcol.markdown("**검증 구간 (최근 2년)**")
            bcol.dataframe(bt[f"test_{kind}"], hide_index=True, width="stretch", column_config=fmt)
    with t3:
        sm = bt["summary"]
        k = st.columns(4)
        k[0].metric("신호대로 누적", f"{sm['strategy']:+.0f}%", f"보유 {sm['hold']:+.0f}%", delta_color="off", delta_arrow="off")
        k[1].metric("최대 낙폭", f"{sm['strategy_mdd']:.0f}%", f"보유 {sm['hold_mdd']:.0f}%", delta_color="off", delta_arrow="off")
        k[2].metric("보유 기간 비율", f"{sm['exposure']:.0f}%")
        k[3].metric("신호 횟수", f"{sm['trades']}회")
        eq = bt["equity"]
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=eq.index, y=eq["신호대로"], name="신호대로 (매수→보유, 매도→현금)", line=dict(color=ACCENT, width=2.2)))
        fig2.add_trace(go.Scatter(x=eq.index, y=eq["계속 보유"], name="계속 보유", line=dict(color=NEUTRAL, dash="dot")))
        base_layout(fig2, 300)
        fig2.update_yaxes(type="log", title_text="누적 자본 배수")
        show_chart(fig2, key="act_equity")
        st.caption("매수 신호 다음 주부터 보유, 매도 신호 다음 주부터 현금으로 가정 (편도 비용 0.1% 반영). "
                   "과거 성과는 미래를 보장하지 않으며, 이 신호는 매수·매도 권유가 아닙니다.")
    try:
        th = titan_latest("theme_radar", "US") if get_titan_engine() is not None else pd.DataFrame()
        semi = th[th["theme"] == "반도체"] if not th.empty else th
        if not semi.empty:
            r = semi.iloc[0]
            st.info(f"함께 볼 것 · 반도체 테마: {int(r['rank'])}위 / {len(th)}개 {('· ' + r['status']) if r['status'] else ''} "
                    f"(4주 전 {int(r['rank_prev']) if pd.notna(r['rank_prev']) else '—'}위). "
                    "매수 신호가 '떠오르는 테마'와 겹치면 더 믿을 만합니다.", icon=":material/hub:")
    except Exception:
        LOGGER.warning("반도체 테마 상태 조회 실패")


def _ev_table(ev_cur: dict | None, ev_new: dict | None) -> pd.DataFrame:
    labels = {"plus_share": "평소보다 나은 종목(%)", "avg_diff": "평균 적중률 차이(%p)", "signals": "매수 신호 수",
              "median_mdd": "신호대로 MDD 중앙값(%)", "median_ret": "신호대로 누적 중앙값(%)", "n_tickers": "평가 종목 수"}
    return pd.DataFrame([{"항목": v, "지금 규칙": (ev_cur or {}).get(k), "제안 규칙": (ev_new or {}).get(k)}
                         for k, v in labels.items()])


def render_rule_improvement() -> None:
    state = load_rule_state(backend())
    section("규칙 개선 · 대표 규칙 vs 도전 규칙", "매달 1일 자동 점검: 후보 432개 중 학습 구간으로 1개를 고르고, 최근 2년에서 지금 규칙보다 "
                     "확실히 나을 때만 제안합니다. 바로 적용하거나, 도전자로 등록해 실전 신호로 경쟁시킬 수 있어요.")
    if backend() != "supabase":
        st.caption("Supabase 연결이 있어야 규칙 버전 관리가 동작합니다.")
        return
    text = core.rule_text(state["rules"])
    st.markdown(f"**대표 규칙 v{state['version']}** · 매수: {text['매수']} · 매도: {text['매도']}")
    p = state["pending"]
    if p:
        params = p["params"] if isinstance(p["params"], dict) else json.loads(p["params"])
        ntext = core.rule_text(core.normalize_rules(params))
        ev = p.get("evaluation") or {}
        if isinstance(ev, str):
            ev = json.loads(ev)
        with st.container(border=True):
            st.markdown(f"**새 규칙 제안 v{p['version']}** {pill('승인 대기', 'warn')}", unsafe_allow_html=True)
            st.markdown(f"- 매수: {ntext['매수']}\n- 매도: {ntext['매도']}")
            st.caption(f"제안 이유: {p.get('reason') or '—'}")
            a, b = st.columns(2)
            a.markdown("**검증 구간 (최근 2년, 고를 때 안 쓴 구간)**")
            a.dataframe(_ev_table(ev.get("current_test"), ev.get("candidate_test")), hide_index=True, width="stretch")
            b.markdown("**학습 구간 (참고)**")
            b.dataframe(_ev_table(ev.get("current_train"), ev.get("candidate_train")), hide_index=True, width="stretch")
            c1, c2, c3, _ = st.columns([1, 1.3, 1, 2])
            now_iso = now_kst().isoformat()
            if c1.button("적용", type="primary", icon=":material/check:", key="rule_accept",
                         help="지금 규칙을 이 규칙으로 바로 교체합니다."):
                core.decide_proposal(get_supabase(), int(p["id"]), "accept", now_iso)
                load_rule_state.clear()
                load_champion_state.clear()
                flash(f"규칙 v{p['version']}을 적용했습니다.")
                st.rerun()
            full = len(state.get("challengers", [])) >= core.MAX_CHALLENGERS
            if c2.button("경쟁시키기", icon=":material/sports_score:", key="rule_challenge", disabled=full,
                         help="지금 규칙은 그대로 두고, 오늘부터 실전 신호로 승부를 가립니다."):
                core.decide_proposal(get_supabase(), int(p["id"]), "challenge", now_iso)
                load_rule_state.clear()
                load_champion_state.clear()
                flash(f"규칙 v{p['version']}을 도전자로 등록했습니다. 매일 실전 신호로 경쟁합니다.")
                st.rerun()
            if c3.button("거절", icon=":material/close:", key="rule_reject"):
                core.decide_proposal(get_supabase(), int(p["id"]), "reject", now_iso)
                load_rule_state.clear()
                load_champion_state.clear()
                flash("제안을 거절했습니다. 지금 규칙을 유지합니다.")
                st.rerun()
    else:
        st.caption("승인 대기 중인 제안이 없습니다.")
    challengers = state.get("challengers", [])
    if challengers:
        st.markdown("**대표 규칙 vs 도전 규칙 (오늘부터 쌓이는 실전 신호로만 비교)**")
        roles = {state["version"]: "대표", **{v: "도전" for v, _ in challengers}}
        live = state.get("live", pd.DataFrame())
        live = live[live["rule_version"].isin(list(roles))] if not live.empty else live
        table = core.rule_competition(live, roles)
        if table.empty:
            st.caption("아직 실전 신호가 없습니다. 매일 아침 자동 점검이 마감된 주의 신호를 기록하고, 4주 뒤 채점합니다.")
        else:
            st.dataframe(table, hide_index=True, width="stretch")
        verdicts = core.promotion_verdicts(table, state["version"]) if not table.empty else {}
        now_iso = now_kst().isoformat()
        for v, crule in challengers:
            row = next((h for h in state["history"] if int(h["version"]) == v), None)
            verdict = verdicts.get(v, "표본 부족 (실전 신호 기록 대기)")
            kind = "ok" if verdict.startswith("승격") else "stop" if verdict.startswith("열세") else "warn"
            with st.container(border=True):
                st.markdown(f"**도전자 v{v}** {pill(verdict.split(' (')[0], kind)} · 매수: {core.rule_text(crule)['매수']}",
                            unsafe_allow_html=True)
                st.caption(verdict + f" · 승격 기준: 매수 채점 {core.PROMOTE_MIN['graded']}건·서로 다른 {core.PROMOTE_MIN['weeks']}주 이상, "
                           f"대표 규칙보다 적중률 +{core.PROMOTE_MIN['hit_gain']:.0f}%p")
                b1, b2, _ = st.columns([1, 1, 3])
                if row and b1.button("대표 규칙으로 승격", key=f"promote_{v}", icon=":material/military_tech:",
                                     type="primary" if verdict.startswith("승격") else "secondary"):
                    core.decide_proposal(get_supabase(), int(row["id"]), "accept", now_iso)
                    load_rule_state.clear()
                    load_champion_state.clear()
                    flash(f"v{v}을 대표 규칙으로 올렸습니다.")
                    st.rerun()
                if row and b2.button("경쟁 종료", key=f"retire_{v}", icon=":material/flag:"):
                    core.decide_proposal(get_supabase(), int(row["id"]), "retire", now_iso)
                    load_rule_state.clear()
                    load_champion_state.clear()
                    flash(f"v{v}의 경쟁을 종료했습니다.")
                    st.rerun()
    runs = load_job_runs(backend())
    if not runs.empty and "source" in runs:
        last = runs[runs["source"] == "rules"].head(1)
        if not last.empty:
            st.caption(f"마지막 규칙 점검 {last.iloc[0]['ran_at']}: {last.iloc[0]['summary']}")
    if state["history"]:
        with st.expander("규칙 버전 이력", icon=":material/history:"):
            rows = []
            for h in state["history"]:
                prm = h["params"] if isinstance(h["params"], dict) else json.loads(h["params"])
                t = core.rule_text(core.normalize_rules(prm))
                rows.append({"버전": h["version"], "상태": {"active": "대표(사용 중)", "proposed": "승인 대기", "rejected": "거절",
                                                          "challenger": "도전자", "retired": "이전 규칙"}.get(h["status"], h["status"]),
                             "매수": t["매수"], "매도": t["매도"], "이유": h.get("reason"),
                             "등록": str(h.get("created_at"))[:16], "결정": str(h.get("decided_at") or "")[:16]})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


@st.cache_data(ttl=300, show_spinner=False)
def load_arena(backend_name: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(선수 비중 기록, AI 위원회 회의록)."""
    if backend_name != "supabase":
        return pd.DataFrame(), pd.DataFrame()
    try:
        pos = pd.DataFrame(_sb_fetch_all(core.POSITION_TABLE))
    except Exception as exc:
        LOGGER.warning("선수 기록 조회 실패: %s", exc)
        pos = pd.DataFrame()
    try:
        mins = pd.DataFrame(_sb_fetch_all(core.MINUTES_TABLE))
    except Exception:
        mins = pd.DataFrame()
    return pos, mins


@st.cache_data(ttl=3600, show_spinner=False)
def cached_player_positions(ticker: str, partner: str, rules_key: str) -> pd.DataFrame:
    """계산 가능한 선수들의 주별 비중표 (마감된 주까지). 마지막 열에 종가."""
    daily = load_long(ticker, "max", True)
    if daily is None:
        return pd.DataFrame()
    partner_df = load_long(partner, "max", True) if partner else None
    hist = core.complete_weeks(load_signal_history(ticker, partner), us_asof())
    rules = [(int(v), core.normalize_rules(r)) for v, r in json.loads(rules_key)]
    pos = core.player_positions(hist, daily, core.trend_reference(ticker, daily, partner_df), rules, ticker)
    pos["close"] = hist["close"]
    return pos


@st.cache_data(ttl=3600, show_spinner=False)
def cached_agent_compare(ticker: str, partner: str, rules_key: str, champ: str, agents_key: str,
                         core_version: str = "") -> pd.DataFrame:
    """AI 요원 vs 챔피언 vs 무한매수 원칙(① 40분할): 훈련 전·훈련 기간·훈련 뒤 구간별 같은 잣대."""
    pos = cached_player_positions(ticker, partner, rules_key)
    ags = core.agents_for(ticker)
    if pos.empty or not ags:
        return pd.DataFrame()
    meta = next(iter(ags.values()))
    pids = [champ, "inf_mine", *ags.keys()]
    return core.segment_compare(pos.drop(columns=["close"]), pos["close"], pids, core.agent_segments(meta))


@st.cache_data(ttl=3600, show_spinner=False)
def cached_player_backtest(ticker: str, partner: str, rules_key: str, core_version: str = "") -> pd.DataFrame:
    pos = cached_player_positions(ticker, partner, rules_key)
    if pos.empty:
        return pd.DataFrame()
    players = pos.drop(columns=["close"])
    rules = [(int(v), core.normalize_rules(r)) for v, r in json.loads(rules_key)]
    exps = core.experiment_rules(rules[0][1], rules[1:])
    if exps:
        hist = core.complete_weeks(load_signal_history(ticker, partner), us_asof())
        for pid, rule in exps.items():
            players[pid] = core.rule_positions(core.action_signals(hist, rule)).reindex(players.index)
    return core.backtest_players(players, pos["close"])


PLAYER_COLORS = ["#F04452", "#3182F6", "#F5A524", "#22C55E", "#A855F7", "#14B8A6", "#EC4899", "#94A3B8",
                 "#EAB308", "#06B6D4", "#F97316", "#84CC16"]


def render_player_chart(ticker: str, partner: str, view_start: pd.Timestamp) -> None:
    """선수를 골라 차트에 매수·매도 시점과 주별 비중을 겹쳐 봄."""
    state = load_rule_state(backend())
    rules = [(state["version"], state["rules"])] + list(state.get("challengers", []))
    pos = cached_player_positions(ticker, partner, json.dumps([[v, r] for v, r in rules]))
    if pos.empty:
        return
    close = pos["close"]
    players = [c for c in pos.columns if c != "close" and pos[c].notna().any()]
    live, _ = load_arena(backend())
    live = live[live["ticker"] == ticker] if not live.empty else live
    for pid in ("ai_council", "ensemble"):
        if not live.empty and (live["player"] == pid).any():
            players.append(pid)
    champ = load_champion_state(backend())["champion"]["player"]
    lead = f"rule_v{state['version']}"
    default = [p for p in dict.fromkeys([champ, lead]) if p in players] or players[:1]
    label = {p: ("👑 " if p == champ else "") + core.player_label(p) for p in players}
    picked = st.multiselect("차트에 표시할 선수 (최대 4명)", players, default=default, max_selections=4,
                            format_func=lambda p: label[p], key=f"chart_players_{ticker}")
    if not picked:
        st.caption("선수를 골라 주세요.")
        return
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.72, 0.28], vertical_spacing=0.04)
    view_close = close[close.index >= view_start]
    fig.add_trace(go.Scatter(x=view_close.index, y=view_close, name=f"{ticker} 주봉 종가",
                             line=dict(color=GOLD, width=2)), row=1, col=1)
    single = len(picked) == 1
    notes = []
    for i, pid in enumerate(picked):
        color = PLAYER_COLORS[players.index(pid) % len(PLAYER_COLORS)]
        if pid in pos.columns:
            series = pos[pid]
        else:
            g = live[live["player"] == pid].copy()
            series = pd.Series(g["position"].astype(float).to_numpy(), index=pd.to_datetime(g["week"])).sort_index()
        moves = core.position_moves(series)
        moves = moves[moves.index >= view_start]
        for kind, sym in (("매수", "triangle-up"), ("매도", "triangle-down")):
            pts = moves[moves["action"] == kind]
            if pts.empty:
                continue
            y = close.reindex(pts.index)
            txt = [f"{kind} {a * 100:.0f}→{b * 100:.0f}%" for a, b in zip(pts["from"], pts["to"])]
            fill = (UP if kind == "매수" else DOWN) if single else color
            fig.add_trace(go.Scatter(x=pts.index, y=y, mode="markers+text" if single else "markers",
                                     name=f"{label[pid]} {kind}", text=[kind] * len(pts) if single else None,
                                     textposition="bottom center" if kind == "매수" else "top center",
                                     textfont=dict(color=fill, size=11), hovertext=txt, hoverinfo="text+x",
                                     marker=dict(symbol=sym, size=13, color=fill, line=dict(width=1.5, color="#0B1220")),
                                     legendgroup=pid), row=1, col=1)
        w = series[series.index >= view_start] * 100
        fig.add_trace(go.Scatter(x=w.index, y=w, name=f"{label[pid]} 비중", line=dict(color=color, width=2, shape="hv"),
                                 legendgroup=pid, showlegend=not single), row=2, col=1)
        cur = series.dropna()
        if not cur.empty:
            last_move = core.position_moves(series).tail(1)
            lm = (f" · 마지막 {last_move['action'].iloc[0]} {last_move.index[0]:%Y-%m-%d}"
                  if not last_move.empty else "")
            notes.append(f"**{label[pid]}** 이번 주 비중 {cur.iloc[-1] * 100:.0f}%{lm}")
    base_layout(fig, 460)
    fig.update_yaxes(type="log", title_text="가격 (로그)", row=1, col=1)
    fig.update_yaxes(range=[-5, 105], title_text="비중 %", row=2, col=1)
    show_chart(fig, key=f"player_chart_{ticker}")
    st.caption(" · ".join(notes) + ". 비중이 25%p 이상 늘면 매수(▲), 줄면 매도(▼)로 표시합니다. "
               "AI 위원회·앙상블은 실전 기록이 있는 주만 나옵니다.")


@st.cache_data(ttl=120, show_spinner=False)
def load_champion_state(backend_name: str) -> dict:
    """경기장 챔피언과 이력."""
    if backend_name != "supabase":
        return {"champion": {"player": "rule_v1", "reason": "기본"}, "history": []}
    try:
        sb = get_supabase()
        return {"champion": core.load_champion(sb), "history": core.champion_history(sb)}
    except Exception as exc:
        LOGGER.warning("챔피언 조회 실패 (테이블 없음?): %s", exc)
        return {"champion": {"player": "rule_v1", "reason": "기본"}, "history": []}


OUTCOME_TXT = {1.0: "추세 전환", 0.0: "일시 반등"}


@st.cache_data(ttl=3600, show_spinner=False)
def cached_rebound_history(rules_key: str, core_version: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    """SOX 반등 사건(1994~)과 정답, 그리고 사건 주의 선수별 SOXL 비중(= 추세 전환 확률)."""
    df = load_long("^SOX", "max", False)
    if df is None or df.empty:
        df = load_long("SOXX", "max", True)
    if df is None or df.empty:
        return pd.DataFrame(), pd.DataFrame()
    close = df["Close"].dropna()
    lab = core.label_rebounds(core.detect_rebounds(close), close)
    if lab.empty:
        return lab, pd.DataFrame()
    lab["base_rate"] = core.rebound_base_rates(lab).to_numpy()
    pos = cached_player_positions("SOXL", core.partner_of("SOXL"), rules_key)
    calls = pos.drop(columns=["close"]).reindex(pd.DatetimeIndex(lab["week"])) if not pos.empty else pd.DataFrame(
        index=pd.DatetimeIndex(lab["week"]))
    calls[core.REBOUND_BASE_PLAYER] = lab["base_rate"].to_numpy()
    data = load_check_inputs()
    if data["credit"] is not None and not data["tnx"].empty and not data["vix"].empty:
        ck = core.check_conditions(data["tnx"], data["vix"], data["credit"])
        calls[core.MACRO_CHECK_PLAYER] = core.check_calls_at(ck, lab["week"]).to_numpy()
    return lab, calls


def _rebound_board(lab: pd.DataFrame, calls: pd.DataFrame) -> None:
    board = core.rebound_scoreboard(lab, calls)
    if board.empty:
        st.caption("아직 채점된 사건이 없습니다.")
        return
    board.insert(0, "선수", [core.player_label(i) for i in board["id"]])
    st.dataframe(board.drop(columns=["id"]), hide_index=True, width="stretch",
                 column_config={"브라이어": st.column_config.NumberColumn(format="%.3f", help="0에 가까울수록 정확"),
                                "기준 대비": st.column_config.NumberColumn(format="%+.3f",
                                                                       help="음수면 '과거 확률'보다 잘 맞힘"),
                                "적중률(%)": st.column_config.NumberColumn(format="%.0f%%"),
                                "전환 예상 평균(%)": st.column_config.NumberColumn(format="%.0f%%")})


def render_rebound_league(rules: list, pos: pd.DataFrame) -> None:
    th = core.REBOUND_RULES
    st.caption(f"반도체 지수(SOX)가 고점 대비 {th['dd_min']:.0f}% 넘게 빠진 상태에서 하루 +{th['day_jump']:.0f}% 이상 "
               f"또는 20일 저점 대비 +{th['from_low']:.0f}% 반등하면 '반등 사건'이 열립니다. 그 주 금요일에 각 선수의 "
               f"SOXL 비중을 '추세 전환 확률'로 보고, {th['horizon_weeks']}주 동안 직전 저점을 깨지 않고 13주 뒤 더 높으면 "
               "추세 전환, 저점을 깨면 그 즉시 일시 반등으로 채점합니다. 기준 선수 '과거 확률'보다 잘 맞혀야 실력입니다.")
    lab, calls = cached_rebound_history(json.dumps([[v, r] for v, r in rules]), core.CORE_VERSION)
    if lab.empty:
        st.caption("지수 데이터를 불러오지 못했습니다.")
        return
    last = lab.iloc[-1]
    done = lab.dropna(subset=["outcome"])
    day = done[done["trigger"].str.startswith("하루")]
    with st.container(border=True):
        status = OUTCOME_TXT.get(last["outcome"]) if pd.notna(last["outcome"]) else "판정 대기"
        st.markdown(f"**가장 최근 사건** · {last['event_date']:%Y-%m-%d} {last['trigger']} "
                    f"(고점 대비 {last['depth']:.0f}% 빠진 뒤) · {pill(status, 'warn' if status == '판정 대기' else 'info')}",
                    unsafe_allow_html=True)
        if status == "판정 대기":
            end = last["week"] + pd.Timedelta(weeks=th["horizon_weeks"])
            st.caption(f"SOX가 {last['prior_low']:,.0f} 아래로 내려가면 바로 '일시 반등' 확정, 그렇지 않고 "
                       f"{end:%Y-%m-%d} 무렵 {last['judge_close'] if pd.notna(last['judge_close']) else 0:,.0f}보다 높으면 '추세 전환'.")
        live_week = last["week"]
        mine = pos[(pos["ticker"] == "SOXL") & (pd.to_datetime(pos["week"]) == live_week)] if not pos.empty else pos
        if mine is not None and not mine.empty:
            pv = mine.set_index("player")["position"].astype(float)
        elif live_week in calls.index:
            pv = calls.loc[live_week].drop(core.REBOUND_BASE_PLAYER, errors="ignore").dropna()
        else:
            pv = pd.Series(dtype=float)
        if core.MACRO_CHECK_PLAYER in calls.columns and live_week in calls.index and pd.notna(calls.loc[live_week, core.MACRO_CHECK_PLAYER]):
            pv = pd.concat([pd.Series({core.MACRO_CHECK_PLAYER: float(calls.loc[live_week, core.MACRO_CHECK_PLAYER])}),
                            pv.drop(core.MACRO_CHECK_PLAYER, errors="ignore")])
        if not pv.empty:
            pv = pd.concat([pd.Series({core.REBOUND_BASE_PLAYER: float(last["base_rate"])}), pv])
            st.caption("이번 사건에 대한 선수별 추세 전환 확률: " + " · ".join(
                f"{core.player_label(k)} {v * 100:.0f}%" for k, v in pv.items()))
        else:
            st.caption(f"선수들의 답은 그 주 금요일 마감 뒤 기록됩니다. 기준 선수(과거 확률)는 {last['base_rate'] * 100:.0f}%.")
    m = st.columns(3)
    m[0].metric("과거 반등 사건", f"{len(done)}건", f"{done['event_date'].min():%Y}년부터" if len(done) else None,
                delta_color="off", delta_arrow="off")
    m[1].metric("그중 추세 전환", f"{done['outcome'].mean() * 100:.0f}%" if len(done) else "—")
    m[2].metric("하루 +5% 반등만 보면", f"{day['outcome'].mean() * 100:.0f}%" if len(day) else "—",
                f"{len(day)}건", delta_color="off", delta_arrow="off")
    a, b = st.tabs(["연습 경기 (과거 사건)", "실전 경기"])
    with a:
        st.caption("SOXL 상장(2010) 이후 사건으로, 계산 가능한 선수들이 그때 어떻게 판단했을지 채점합니다.")
        _rebound_board(lab[lab["week"] >= pd.Timestamp("2010-06-01")], calls)
        if core.MACRO_CHECK_PLAYER in calls.columns:
            long_board = core.rebound_scoreboard(lab, calls[[core.MACRO_CHECK_PLAYER, core.REBOUND_BASE_PLAYER]])
            if not long_board.empty:
                lb = long_board.set_index("id")
                if core.MACRO_CHECK_PLAYER in lb.index:
                    m = lb.loc[core.MACRO_CHECK_PLAYER]
                    st.caption(f"거시 체크리스트는 1994년부터 채점할 수 있어요: {int(m['사건 수'])}건 · 브라이어 {m['브라이어']:.3f} "
                               f"(과거 확률 대비 {m['기준 대비']:+.3f}, 음수면 더 잘 맞힘) · 적중률 {m['적중률(%)']:.0f}%.")
        show = lab.tail(15).iloc[::-1]
        st.dataframe(pd.DataFrame({"날짜": show["event_date"].dt.strftime("%Y-%m-%d"), "계기": show["trigger"],
                                   "직전 낙폭": show["depth"].map(lambda v: f"{v:.0f}%"),
                                   "결과": show["outcome"].map(lambda v: OUTCOME_TXT.get(v, "판정 대기")),
                                   "과거 확률": show["base_rate"].map(lambda v: f"{v * 100:.0f}%")}),
                     hide_index=True, width="stretch")
    with b:
        st.caption(f"{core.REBOUND_LIVE_START} 이후 사건만 실전으로 기록합니다. 사건 주의 실제 기록된 비중으로 채점해요.")
        live_lab = lab[lab["week"] >= pd.Timestamp(core.REBOUND_LIVE_START)]
        if live_lab.empty or pos.empty:
            st.caption("아직 실전 사건이 없습니다.")
        else:
            soxl = pos[pos["ticker"] == "SOXL"].copy()
            soxl["week"] = pd.to_datetime(soxl["week"])
            live_calls = soxl.pivot_table(index="week", columns="player", values="position", aggfunc="last")
            live_calls = live_calls.reindex(pd.DatetimeIndex(live_lab["week"]))
            live_calls[core.REBOUND_BASE_PLAYER] = live_lab["base_rate"].to_numpy()
            if core.MACRO_CHECK_PLAYER in calls.columns:
                live_calls[core.MACRO_CHECK_PLAYER] = calls[core.MACRO_CHECK_PLAYER].reindex(live_calls.index).to_numpy()
            _rebound_board(live_lab, live_calls)
            st.caption(f"실전 사건 {len(live_lab)}건 · 채점 완료 {int(live_lab['outcome'].notna().sum())}건. "
                       "사건이 드물어서 실력 차이가 보이려면 몇 년이 걸려요.")


def render_arena(ticker: str, partner: str) -> None:
    section("선수 경기장", "모든 선수가 매주 종목별 비중(0~100%)을 내고, 다음 주 실제 수익률로 똑같이 채점합니다. "
                       "규칙이든 기준 선수든 AI 위원회든 앙상블이든, 챔피언을 이기면 누구나 챔피언이 될 수 있어요.")
    state = load_rule_state(backend())
    rules = [(state["version"], state["rules"])] + list(state.get("challengers", []))
    pos, mins = load_arena(backend())
    champ_state = load_champion_state(backend())
    champ = champ_state["champion"]["player"]
    alive = {f"rule_v{state['version']}"} | {f"rule_v{v}" for v, _ in state.get("challengers", [])}
    cur_pos = None
    if not pos.empty:
        mine = pos[(pos["ticker"] == ticker) & (pos["player"] == champ)]
        if not mine.empty:
            cur_pos = float(mine.sort_values("week")["position"].iloc[-1])
    msg = f"👑 현재 챔피언: **{core.player_label(champ)}**"
    if cur_pos is not None:
        msg += f" · 이번 주 {ticker} 비중 **{cur_pos * 100:.0f}%**"
    st.info(msg + " — 경기장의 공식 판단은 챔피언의 비중을 따릅니다.", icon=":material/military_tech:")
    if champ.startswith("rule_v") and champ not in alive:
        st.warning(f"챔피언 {core.player_label(champ)}은 경쟁이 종료되어 더 이상 기록되지 않습니다. 아래에서 새 챔피언을 정해 주세요.",
                   icon=":material/warning:")
    t1, t2, t5, t4, t3 = st.tabs(["실전 경기표", f"{ticker} 과거 성적", "일봉 리그", "반등 판별전", "AI 위원회 회의록"])
    with t5:
        st.caption(f"모든 선수가 매일 장 마감 비중을 내고, 다음 거래일 수익률로 채점합니다 ({core.DAILY_LIVE_START}부터). "
                   "매일 아침 자동 점검이 기록해요. 과거 성적은 옆 탭 아래쪽 표에 있어요.")
        led = load_daily_ledger(backend())
        if led.empty:
            st.caption("아직 일봉 리그 기록이 없습니다. 다음 자동 점검부터 쌓여요.")
        else:
            lev = [t for t in core.SIGNAL_UNIVERSE if core.category_of(t) in core.TECHNIQUE_CATEGORIES]
            dscope = st.segmented_control("범위", ["이 종목", f"레버리지 ETF {len(lev)}개", "전체"], default="이 종목",
                                          key="daily_scope") or "이 종목"
            tick = [ticker] if dscope == "이 종목" else (lev if dscope.startswith("레버리지") else None)
            tbl = core.daily_league_table(led, tick)
            if tbl.empty:
                st.caption("채점된 기록이 아직 없습니다 (기록 다음 거래일에 채점).")
            else:
                st.dataframe(tbl.drop(columns=["id"]), hide_index=True, width="stretch",
                             column_config={"누적(%)": st.column_config.NumberColumn(format="%+.2f%%"),
                                            "보유 대비(%p)": st.column_config.NumberColumn(format="%+.2f")})
                st.caption(f"채점 {int(tbl['채점 일수'].max())}거래일째. 몇 주는 지나야 순위가 의미 있어요.")
    with t4:
        render_rebound_league(rules, pos)
    with t1:
        if pos.empty:
            st.caption("아직 실전 기록이 없습니다. 매일 아침 자동 점검이 선수별 비중을 기록하고, 1주 뒤부터 채점합니다.")
        else:
            lev = [t for t in core.SIGNAL_UNIVERSE if core.category_of(t) in core.TECHNIQUE_CATEGORIES]
            scopes = {"AI 위원회 10개 종목": core.COUNCIL_TICKERS, f"레버리지 ETF {len(lev)}개": lev,
                      "전체 53개 종목": None}
            scope = st.segmented_control("범위", list(scopes), default="AI 위원회 10개 종목",
                                         key="arena_scope") or "AI 위원회 10개 종목"
            tick_scope = scopes[scope]
            table = core.arena_table(pos, tick_scope)
            st.caption("① 내 방식 · ② V2형 · ④ V2+손절+200일선 · ⑥ 50:50(매매기법 연구소와 같은 규칙)과 찰스 SOXL 시스템(TradingView 공개 스크립트)은 "
                       "레버리지 ETF에서만 뜁니다. 같은 조건끼리 비교하려면 '레버리지 ETF' 범위를 보세요. "
                       "챔피언 도전 판정은 항상 같은 종목·같은 주끼리만 맞대어 계산합니다.")
            if table.empty:
                st.caption("채점된 기록이 아직 없습니다 (비중을 낸 다음 주 금요일 종가가 나오면 채점).")
            else:
                verdicts = core.arena_verdicts(pos, champ, tick_scope)
                table["선수"] = [("👑 " if i == champ else "") + n for i, n in zip(table["id"], table["선수"])]
                table["챔피언 도전 판정"] = [("현재 챔피언" if i == champ else verdicts.get(i, ("short", "기록 없음"))[1])
                                     for i in table["id"]]
                st.dataframe(table.drop(columns=["id"]), hide_index=True, width="stretch",
                             column_config={"누적(%)": st.column_config.NumberColumn(format="%+.2f%%"),
                                            "보유 대비(%p)": st.column_config.NumberColumn(format="%+.2f"),
                                            "챔피언 도전 판정": st.column_config.TextColumn(width="large")})
                weeks = int(table["채점 주 수"].max())
                th = core.ARENA_PROMOTE
                st.caption(f"승격 기준: 챔피언과 같은 종목·같은 주로 {th['weeks']}주 이상 맞대결해서 누적 수익이 "
                           f"{th['gain']:.0f}%p 이상 높고, 최대 낙폭이 {th['mdd_slack']:.0f}%p 넘게 나쁘지 않을 것. "
                           "기준을 넘으면 '승격 추천'이 뜨고, 올릴지는 직접 결정합니다."
                           + (f" 지금은 채점 {weeks}주째라 아직 아무도 도전 자격이 없어요." if weeks < th["weeks"] else ""))
                ready = [i for i, (code, _) in verdicts.items() if code == "promote"]
                for pid in ready:
                    cols = st.columns([3, 1], vertical_alignment="center")
                    cols[0].success(f"**{core.player_label(pid)}** — {verdicts[pid][1]}", icon=":material/emoji_events:")
                    if cols[1].button("챔피언으로 올리기", key=f"crown_{pid}", type="primary", width="stretch"):
                        core.crown_champion(get_supabase(), pid, f"{scope} 경기에서 승격: {verdicts[pid][1]}",
                                            pd.Timestamp.now(tz="Asia/Seoul").isoformat())
                        load_champion_state.clear()
                        st.rerun()
            hist_rows = champ_state.get("history") or []
            if hist_rows:
                with st.expander("챔피언 이력"):
                    st.dataframe(pd.DataFrame([{"선수": core.player_label(h["player"]),
                                                "상태": "현재" if h.get("status") == "champion" else "이전",
                                                "이유": h.get("reason"), "등극": str(h.get("created_at") or "")[:16],
                                                "물러남": str(h.get("ended_at") or "")[:16]} for h in hist_rows]),
                                 hide_index=True, width="stretch")
            latest = pos[(pos["ticker"] == ticker)]
            if not latest.empty:
                wk = latest["week"].max()
                cur = latest[latest["week"] == wk].copy()
                cur["선수"] = cur["player"].map(core.player_label)
                cur["이번 주 비중"] = (cur["position"].astype(float) * 100).round(0)
                st.markdown(f"**{ticker} · {str(wk)[:10]} 주 선수별 비중**")
                st.dataframe(cur[["선수", "이번 주 비중", "note"]].rename(columns={"note": "메모"}), hide_index=True,
                             width="stretch", column_config={"이번 주 비중": st.column_config.ProgressColumn(
                                 min_value=0, max_value=100, format="%.0f%%")})
    with t2:
        st.caption("계산 가능한 선수(규칙·추세 필터·변동성 조절·그냥 보유, 레버리지 ETF면 연구소 기법 4개)를 과거 전체에 적용한 결과입니다. "
                   "AI 위원회와 앙상블은 과거로 검증할 수 없어 실전 기록만 봅니다.")
        bt = cached_player_backtest(ticker, partner, json.dumps([[v, r] for v, r in rules]), core.CORE_VERSION)
        if bt.empty:
            st.caption("데이터가 부족합니다.")
        else:
            sort_by = st.segmented_control("정렬", ["칼마", "연평균(%)", "전체 MDD(%)", "최근 2년 칼마"],
                                           default="칼마", key=f"bt_sort_{ticker}") or "칼마"
            bt = bt.sort_values(sort_by, ascending=False, na_position="last")
            pct = {c: st.column_config.NumberColumn(format="%+.1f%%")
                   for c in ("전체 누적(%)", "연평균(%)", "전체 MDD(%)", "최근 2년 누적(%)", "최근 2년 MDD(%)")}
            st.dataframe(bt.drop(columns=["id"]), hide_index=True, width="stretch",
                         column_config={**pct,
                                        "칼마": st.column_config.NumberColumn(format="%.2f", help="연평균 ÷ |최대 낙폭|. 높을수록 위험 대비 수익이 좋음"),
                                        "최근 2년 칼마": st.column_config.NumberColumn(format="%.2f"),
                                        "최장 회복(주)": st.column_config.NumberColumn(help="고점 아래에 머문 가장 긴 기간"),
                                        "연 매매(회)": st.column_config.NumberColumn(format="%.1f", help="비중이 25%p 이상 바뀐 횟수, 1년 평균")})
            if "exp_v3" in set(bt["id"]):
                v3 = bt.set_index("id").loc["exp_v3"]
                lead = bt.set_index("id").get("최근 2년 칼마", pd.Series(dtype=float))
                champ_id = f"rule_v{rules[0][0]}"
                note = ""
                if champ_id in lead.index and pd.notna(v3["최근 2년 칼마"]):
                    better = v3["최근 2년 칼마"] > lead[champ_id] and v3["최근 2년 누적(%)"] > bt.set_index("id").loc[champ_id, "최근 2년 누적(%)"]
                    note = (" 최근 2년(학습에 안 쓴 구간)에서 대표 규칙보다 수익과 칼마가 모두 높아요. 도전 규칙으로 올려볼 만해요."
                            if better else " 최근 2년(학습에 안 쓴 구간)에서는 대표 규칙보다 확실히 낫지 않아요. 아직 올릴 근거가 부족해요.")
                st.caption("🧪 실험 v3는 과거 성적 비교 전용이라 실전 장부·경기장 순위·앙상블에는 들어가지 않아요." + note)
            st.caption("모든 선수가 같은 자금, 이번 주 비중 × 다음 주 수익률, 거래 비용 0.1%로 계산했어요.")
        if core.agents_for(ticker):
            champ_pid = load_champion_state(backend())["champion"]["player"]
            ags = core.agents_for(ticker)
            akey = json.dumps({k: v["genes"] for k, v in ags.items()})
            cmp_ = cached_agent_compare(ticker, partner, json.dumps([[v, r] for v, r in rules]), champ_pid, akey,
                                        core.CORE_VERSION)
            st.markdown(f"**🤖 AI 요원 vs 👑 {core.player_label(champ_pid)} vs 무한매수 원칙 · 구간별 대결**")
            if cmp_.empty:
                st.caption("비교할 데이터가 부족해요.")
            else:
                st.dataframe(cmp_.drop(columns=["id"]), hide_index=True, width="stretch",
                             column_config={c: st.column_config.NumberColumn(format="%+.1f%%")
                                            for c in ("누적(%)", "연평균(%)", "MDD(%)")} |
                                           {"칼마": st.column_config.NumberColumn(format="%.2f")})
                meta = next(iter(ags.values()))
                yrs_txt = f"최근 {meta['years']}년" if meta.get("years") else "최근 몇 년인지 기록이 없어 가장 긴 5년으로 가정한"
                st.caption(f"요원은 {meta.get('trained_at') or '—'}에 {yrs_txt} 차트로 훈련됐어요. "
                           "'훈련에 쓴 기간' 성적은 시험 문제를 미리 본 셈이라 부풀려져 있어요. 진짜 실력은 '훈련 전(본 적 없는 과거)'과 "
                           "'훈련 뒤(실전)' 구간으로 보세요. 무한매수 원칙 = ① 40분할·평단 +10%(하루 1회분 고정). "
                           "요원은 유전자 그대로(분할·공포 매수배수 최대 3배·지정가·익절을 스스로 결정). 셋 다 같은 자금으로 계산.")
        st.markdown("**일봉 리그 과거 성적 · 모든 선수를 매일 판단하면? (주봉 규칙과 비교)**")
        dvw = cached_daily_vs_weekly(ticker, partner, json.dumps([[v, r] for v, r in rules]),
                                     f"{us_asof():%Y-%m-%d}", core.CORE_VERSION)
        if dvw.empty:
            st.caption("비교할 데이터가 부족합니다.")
        else:
            st.dataframe(dvw, hide_index=True, width="stretch",
                         column_config={c: st.column_config.NumberColumn(format="%+.1f%%")
                                        for c in ("연평균(%)", "최대 낙폭(%)", "최근 2년 누적(%)", "최근 2년 MDD(%)")})
            st.caption("일 단위로 채점했어요(오늘 종가 비중 × 내일 수익률, 거래 비용 0.1%). 주봉 규칙은 금요일에 정한 비중을 "
                       "다음 주 내내 유지해요. 일봉 선수가 칼마·최근 2년 성적에서 확실히 낫지 않다면, 빠른 반응이 잦은 매매로 상쇄된 거예요.")
    with t3:
        if mins.empty:
            st.caption("아직 회의록이 없습니다. AI 위원회(Claude)가 매주 토요일 아침 10개 종목을 회의하고 여기에 기록합니다.")
        else:
            mins = mins.sort_values(["week", "ticker"], ascending=[False, True])
            wk = mins["week"].max()
            st.markdown(f"**{str(wk)[:10]} 주 회의 결과**")
            this = mins[mins["week"] == wk]
            st.dataframe(this[["ticker", "decision", "position", "prob_up"]].rename(columns={
                "ticker": "종목", "decision": "결정", "position": "비중", "prob_up": "4주 상승 확률(%)"}),
                hide_index=True, width="stretch",
                column_config={"비중": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f")})
            pick = st.selectbox("회의록 보기", this["ticker"].tolist(),
                                index=this["ticker"].tolist().index(ticker) if ticker in this["ticker"].tolist() else 0,
                                key="minutes_pick")
            st.markdown(this[this["ticker"] == pick]["minutes"].iloc[0] or "—")


def signal_selector(prefix: str) -> tuple[str, str]:
    """섹터 → 유형 → 종목 선택 (페이지마다 같은 모양)."""
    c = st.columns([1, 1, 1, 1, 1.4], vertical_alignment="bottom")
    sector = c[0].selectbox("섹터", core.SECTORS + ["직접 입력"], key=f"{prefix}_sector")
    if sector == "직접 입력":
        ticker = c[2].text_input("종목", "SOXL", key=f"{prefix}_ticker").upper().strip()
        partner = c[3].text_input("비교 지수", core.partner_of(ticker), key=f"{prefix}_partner").upper().strip()
    else:
        cats = [k for k in core.CATEGORIES if any(v[1] == k and v[2] == sector for v in core.SIGNAL_UNIVERSE.values())]
        cat = c[1].selectbox("유형", cats, key=f"{prefix}_cat_{sector}")
        options = [t for t, (_, k, sec) in core.SIGNAL_UNIVERSE.items() if k == cat and sec == sector]
        ticker = c[2].selectbox("종목", options, key=f"{prefix}_pick_{sector}_{cat}")
        partner = core.partner_of(ticker)
        c[3].text_input("비교 지수", partner, disabled=True, key=f"{prefix}_partner_{ticker}")
    c[4].caption("섹터와 레버리지(3배·2배·1배·개별 종목)가 다르면 확률과 적중률을 섞지 않고 따로 계산합니다.")
    return ticker, partner


def page_signals() -> None:
    page_header("SIGNAL ROOM", "신호 관제",
                "반등 신호 점수판 · 과거 같은 점수의 실제 결과 · 매주 남기는 예측과 채점",
                [("주봉 기준", ACCENT), ("확률 = 과거 실제 비율", GOLD)])
    ticker, partner = signal_selector("sig")
    with st.spinner("10년 이상 주봉 데이터로 신호를 계산하는 중…"):
        hist = load_signal_history(ticker, partner)
    if hist is None or hist.empty:
        st.error("가격 데이터를 불러오지 못했습니다. 잠시 뒤 다시 시도하세요.", icon=":material/cloud_off:")
        return

    asof = us_asof()
    done = core.complete_weeks(hist, asof)
    partial = len(done) < len(hist)
    last = hist.iloc[-1]
    week_label = hist.index[-1].strftime("%Y-%m-%d")
    bt = core.score_backtest(done)
    n_chart, n_macro = len(core.CHART_SIGNALS), len(core.MACRO_SIGNALS)

    section(f"이번 주 점수판 · {week_label} 주" + (" (진행 중)" if partial else " (마감)"),
            "진행 중인 주는 금요일 종가가 나오기 전이라 점수가 바뀔 수 있습니다. 판단은 마감된 주봉으로 하세요.")
    m = st.columns(4)
    m[0].metric("차트 신호", f"{int(last['chart_score'])} / {n_chart}", border=True)
    m[1].metric("거시 신호", f"{int(last['macro_score'])} / {n_macro}", border=True)
    done_score = int(done["chart_score"].iloc[-1]) if len(done) else int(last["chart_score"])
    for i, hz in enumerate(core.HORIZONS):
        p, n = core.shrunk_probability(bt[f"train_{hz}"], done_score)
        base = bt[f"train_{hz}"].attrs.get("base", float("nan"))
        if np.isfinite(p):
            m[2 + i].metric(f"{hz}주 뒤 상승 확률", f"{p:.0f}%", f"{p - base:+.0f}%p vs 평소 {base:.0f}%",
                            delta_color=kr_delta(p - base), border=True,
                            help=f"마감된 주 점수({done_score}점)와 같은 점수였던 과거 {n}주의 실제 결과 (표본 보정 포함)")
        else:
            m[2 + i].metric(f"{hz}주 뒤 상승 확률", "—", border=True)

    streak = _streak(done)
    if streak >= 2:
        st.success(f"마감된 주봉 기준으로 차트 신호 3점 이상이 {streak}주 연속입니다. 반등 가능성이 높아진 구간이에요.",
                   icon=":material/trending_up:")
    elif streak == 1:
        st.info("마감된 주봉 기준으로 차트 신호 3점 이상이 1주 나왔습니다. 다음 주에도 이어지는지 확인하세요.",
                icon=":material/hourglass_top:")
    else:
        st.warning("마감된 주봉 기준으로 아직 반등 신호가 모이지 않았습니다 (3점 미만).", icon=":material/trending_down:")

    st.dataframe(_signal_rows(last), hide_index=True, width="stretch",
                 column_config={"기준": st.column_config.TextColumn(width="large")})

    recent = hist.tail(52)
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    colors = [UP if s >= 3 else NEUTRAL for s in recent["chart_score"]]
    fig.add_trace(go.Bar(x=recent.index, y=recent["chart_score"], name="차트 신호 점수", marker_color=colors,
                         opacity=.75), secondary_y=False)
    fig.add_trace(go.Scatter(x=recent.index, y=recent["close"], name=f"{ticker} 주봉 종가",
                             line=dict(color=GOLD, width=2)), secondary_y=True)
    fig.add_hline(y=3, line=dict(color="rgba(240,68,82,.5)", dash="dot", width=1), secondary_y=False)
    base_layout(fig, 320)
    fig.update_yaxes(range=[0, n_chart], dtick=1, title_text="점수", secondary_y=False)
    fig.update_yaxes(type="log", title_text="가격", secondary_y=True, showgrid=False)
    show_chart(fig, key="sig_trend")

    render_actions(done, ticker, partner)
    st.caption("선수 경기장(챔피언·일봉 리그·반등 판별전·회의록)과 규칙 개선은 '선수 경기장' 페이지로, 점수별 과거 성적·섹터 비교·예측 장부는 "
               "'연구실' 페이지로 옮겼어요.")

def page_arena() -> None:
    page_header("ARENA", "선수 경기장", "규칙·기준 선수·연구소 기법·AI 위원회·앙상블의 실전 경기 · 챔피언 · 일봉 리그 · 반등 판별전",
                [("실전 채점", ACCENT), ("챔피언은 직접 결정", GOLD)])
    ticker, partner = signal_selector("arena")
    render_arena(ticker, partner)
    render_rule_improvement()


def page_research() -> None:
    page_header("RESEARCH", "연구실", "점수별 과거 성적 · 섹터·유형별 비교 · 예측 장부 (매일 볼 필요는 없는 연구용 자료)",
                [("주봉 기준", ACCENT)])
    ticker, partner = signal_selector("res")
    with st.spinner("10년 이상 주봉 데이터로 신호를 계산하는 중…"):
        hist = load_signal_history(ticker, partner)
    if hist is None or hist.empty:
        st.error("가격 데이터를 불러오지 못했습니다.", icon=":material/cloud_off:")
        return
    asof = us_asof()
    done = core.complete_weeks(hist, asof)
    bt = core.score_backtest(done)
    section("점수별 과거 성적", f"{bt['start']:%Y-%m} ~ {bt['cutoff']:%Y-%m}은 학습 구간, 그 뒤 최근 2년은 확률 계산에 쓰지 않은 "
                         "검증 구간입니다. 두 구간의 방향이 비슷해야 믿을 만한 신호예요.")
    tabs = st.tabs([f"{hz}주 뒤" for hz in core.HORIZONS] + ["거시 점수별"])
    fmt = {"상승 확률(%)": st.column_config.NumberColumn(format="%.1f%%"),
           "평균 수익(%)": st.column_config.NumberColumn(format="%+.1f%%"),
           "중앙값(%)": st.column_config.NumberColumn(format="%+.1f%%")}
    for tab, hz in zip(tabs, core.HORIZONS):
        with tab:
            a, b = st.columns(2)
            for col, key, name in ((a, f"train_{hz}", "학습 구간"), (b, f"test_{hz}", "검증 구간 (최근 2년)")):
                table = bt[key]
                col.markdown(f"**{name}** · 평소 상승 확률 {table.attrs['base']:.1f}% · {table.attrs['n']}주")
                col.dataframe(table, hide_index=True, width="stretch", column_config=fmt)
    with tabs[-1]:
        mbt = core.score_backtest(done, "macro_score")
        a, b = st.columns(2)
        for col, key, name in ((a, "train_4", "학습 구간 · 4주 뒤"), (b, "test_4", "검증 구간 · 4주 뒤")):
            table = mbt[key]
            col.markdown(f"**{name}** · 평소 {table.attrs['base']:.1f}%")
            col.dataframe(table, hide_index=True, width="stretch", column_config=fmt)

    section("섹터 · 유형별 과거 비교", "같은 매수 규칙을 13개 섹터의 3배·2배·1배 ETF·개별 종목에 똑같이 적용한 과거 성적입니다. "
                                  "'차이(%p)'가 한 섹터가 아니라 여러 섹터에서 꾸준히 플러스여야 우연이 아닌 신호예요.")
    n_uni = len(core.SIGNAL_UNIVERSE)
    reports = load_reports(backend())
    latest_day = str(reports["date"].max())[:10] if not reports.empty else None
    if latest_day:
        st.caption(f"매일 아침 자동 점검 결과 · 기준일 {latest_day} (미국 장 마감 기준). 아래 버튼은 지금 바로 다시 계산할 때만 쓰세요.")
    if st.button(f"지금 다시 계산 ({n_uni}개 종목 · 3~5분)", icon=":material/compare_arrows:", key="cmp_run"):
        rows = []
        prog = st.progress(0.0, text="종목별 신호 계산 중…")
        for i, (t, (p, _, _)) in enumerate(core.SIGNAL_UNIVERSE.items()):
            try:
                h = load_signal_history(t, p)
                if h is not None and not h.empty:
                    row = core.ticker_summary(core.action_signals(core.complete_weeks(h, us_asof()),
                                                              load_rule_state(backend())["rules"]), t)
                    if row:
                        rows.append(row)
            except Exception:
                LOGGER.exception("비교 계산 실패: %s", t)
            prog.progress((i + 1) / n_uni, text=f"{t} 완료 ({i + 1}/{n_uni})")
        prog.empty()
        st.session_state["sig_compare"] = pd.DataFrame(rows)
    cmp_df = st.session_state.get("sig_compare")
    if cmp_df is None and latest_day:
        cmp_df = core.report_to_compare(reports[reports["date"].astype(str).str[:10] == latest_day])
    tables = core.compare_tables(cmp_df) if cmp_df is not None and not cmp_df.empty else {}
    if tables:
        share = tables["plus_share"]
        if np.isfinite(share):
            msg = f"검증 구간(최근 2년)에서 매수 신호가 평소보다 나았던 종목: {share:.0f}%"
            (st.success if share >= 60 else st.warning if share >= 40 else st.error)(
                msg + (" → 여러 섹터에서 통하는 편" if share >= 60 else " → 섹터·종목에 따라 갈림" if share >= 40
                       else " → 이 규칙은 믿기 어려움"), icon=":material/fact_check:")
        g1, g2, g3, g4 = st.tabs(["섹터 × 유형 격자", "유형별", "섹터별", "날짜별 추이"])
        with g4:
            trend = core.plus_share_trend(reports)
            if trend.empty or len(trend) < 2:
                st.caption("자동 점검이 이틀 이상 쌓이면 추이가 표시됩니다. 이 선이 꾸준히 50% 위에 있어야 신호가 계속 통하는 거예요.")
            else:
                figt = go.Figure()
                for i, col in enumerate(trend.columns):
                    figt.add_trace(go.Scatter(x=trend.index, y=trend[col], name=col, mode="lines+markers",
                                              line=dict(width=3 if col == "전체" else 1.5,
                                                        color=ACCENT if col == "전체" else SERIES[(i + 1) % len(SERIES)])))
                figt.add_hline(y=50, line=dict(color="rgba(148,163,184,.5)", dash="dot"))
                base_layout(figt, 300)
                figt.update_yaxes(range=[0, 100], ticksuffix="%", title_text="평소보다 나은 종목 비율")
                show_chart(figt, key="cmp_trend")
        with g1:
            st.caption("칸의 숫자 = 검증 구간 매수 신호 4주 적중률 − 평소 확률 (%p). 빨강이 많을수록 신호가 통한 곳입니다.")
            def _cell(v):
                if pd.isna(v):
                    return ""
                a = min(abs(float(v)) / 20, 1) * .55
                rgb = "240,68,82" if v > 0 else "49,130,246"
                return f"background-color: rgba({rgb},{a:.2f})"
            st.dataframe(tables["grid"].style.format("{:+.1f}", na_rep="—").map(_cell), width="stretch",
                         height=36 * (len(tables["grid"]) + 1) + 4)
        with g2:
            st.dataframe(tables["by_cat"], hide_index=True, width="stretch")
        with g3:
            st.dataframe(tables["by_sec"], hide_index=True, width="stretch")
        with st.expander("종목별 상세", icon=":material/table_chart:"):
            st.dataframe(cmp_df, hide_index=True, width="stretch")
    else:
        st.caption("버튼을 누르면 결과가 이 화면에 유지됩니다.")

    section("예측 장부", "매일 아침 자동 점검이 마감된 주의 예측을 주 1건씩 남기고, 4·12주가 지나면 실제 결과로 채점합니다. "
                     "기록된 예측은 고치지 않습니다.")
    ledger, err = load_predictions(backend())
    if err and backend() == "supabase":
        st.warning("예측 장부 테이블이 아직 없습니다. Supabase → SQL Editor에서 아래 SQL을 한 번 실행하세요.",
                   icon=":material/table_chart:")
        st.code(PRED_SQL, language="sql")
    else:
        all_ledger = ledger
        health = core.live_health(all_ledger)
        if health:
            kinds = {"정상": "ok", "주의": "stop", "표본 부족": "warn"}
            st.markdown("**실전 건강 상태 (매일 자동 채점)** &nbsp;" + " ".join(
                pill(f"{h['유형']}: {h['상태']}", kinds[h["상태"]]) for h in health), unsafe_allow_html=True)
            st.caption(" · ".join(f"{h['유형']} {h['설명']}" for h in health))
            if any(h["상태"] == "주의" for h in health):
                st.warning("실전에서 '평소 확률'보다 못한 유형이 있습니다. 그 유형의 신호는 당분간 참고하지 마세요.",
                           icon=":material/health_and_safety:")
        ledger = ledger[ledger["ticker"] == ticker] if not ledger.empty else ledger
        by_cat = core.ledger_by_category(all_ledger, 4)
        if not by_cat.empty:
            st.markdown("**실전 성적 (4주 예측)** · 종목 하나보다 유형·섹터 전체가 더 믿을 만한 근거예요.")
            a, b = st.columns(2)
            a.dataframe(by_cat, hide_index=True, width="stretch")
            b.dataframe(core.ledger_by_sector(all_ledger, 4), hide_index=True, width="stretch")
        st.markdown(f"**{ticker} 예측 기록**")
        graded = core.grade_predictions(ledger, done["close"])
        if graded is None or graded.empty:
            st.caption("아직 기록된 예측이 없습니다. 자동 실행이 돌거나 아래 버튼으로 첫 예측을 남기면 여기에 쌓입니다.")
        else:
            for hz in core.HORIZONS:
                table, summ = core.calibration(graded, hz)
                if not summ or summ.get("n", 0) == 0:
                    st.caption(f"{hz}주 예측: 채점할 수 있는 기록이 아직 없습니다 ({hz}주가 지나야 채점).")
                    continue
                k = st.columns(4)
                k[0].metric(f"{hz}주 예측 채점 수", f"{summ['n']}건")
                k[1].metric("방향 적중률", f"{summ['direction_hit']:.0f}%",
                            help="50% 이상이라고 한 주에 실제로 올랐고, 50% 미만이라고 한 주에 실제로 내린 비율")
                k[2].metric("확률 오차 (브라이어)", f"{summ['brier']:.3f}",
                            f"{summ['brier'] - summ['brier_base']:+.3f} vs 평소 확률", delta_color="inverse",
                            help="낮을수록 좋습니다. '평소 상승 확률'만 말했을 때보다 낮아야 신호가 쓸모 있어요.")
                k[3].metric("실제 상승 비율", f"{summ['up_rate']:.0f}%")
                st.dataframe(table, hide_index=True, width="stretch")
                if summ["n"] < 20:
                    st.caption("채점 건수가 20건 미만이라 아직 판단하기 이릅니다. 최소 20~30건이 쌓인 뒤 평가하세요.")
            show = graded.sort_values("week", ascending=False)
            st.dataframe(show, hide_index=True, width="stretch",
                         column_order=["week", "close", "chart_score", "macro_score", "p_up_4w", "ret_4w",
                                       "p_up_12w", "ret_12w", "signals", "macro_signals"],
                         column_config={"week": "주(금)", "close": st.column_config.NumberColumn("종가", format="$%.2f"),
                                        "chart_score": "차트 점수", "macro_score": "거시 점수",
                                        "signals": "켜진 차트 신호", "macro_signals": "켜진 거시 신호",
                                        "p_up_4w": st.column_config.NumberColumn("4주 상승확률", format="%.0f%%"),
                                        "p_up_12w": st.column_config.NumberColumn("12주 상승확률", format="%.0f%%"),
                                        "ret_4w": st.column_config.NumberColumn("4주 실제", format="%+.1f%%"),
                                        "ret_12w": st.column_config.NumberColumn("12주 실제", format="%+.1f%%")})
        last_done = done.index[-1].strftime("%Y-%m-%d") if len(done) else None
        recorded = set(ledger["week"].astype(str).str[:10]) if not ledger.empty else set()
        if last_done and last_done not in recorded:
            if st.button(f"마감된 주({last_done}) 예측 지금 기록", icon=":material/edit_note:"):
                pred = core.make_prediction(done, ticker)
                try:
                    save_prediction(pred)
                    flash("예측을 기록했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"기록 실패: {exc}")

    with st.expander("이 점수판을 쓰는 방법과 한계", icon=":material/menu_book:"):
        st.markdown("""
- **한 주가 아니라 흐름으로 보세요.** 차트 신호 3점 이상이 **2주 연속**(마감 주봉 기준)일 때를 반등 확인 신호로 봅니다.
- **확률은 과거 실제 비율입니다.** 같은 점수가 나왔던 주 이후 4·12주 동안 실제로 올랐던 비율이며, 표본이 적은 점수는 평소 확률 쪽으로 보정합니다.
- **검증 구간을 꼭 확인하세요.** 학습 구간에서만 좋고 최근 2년 검증 구간에서 다르면 우연이었을 가능성이 큽니다.
- **조건을 자주 바꾸지 마세요.** 결과를 보고 신호 기준을 계속 고치면 과거에만 맞는 규칙이 됩니다. 점검은 예측이 20~30건 쌓인 뒤 한 달에 한 번이면 충분합니다.
- **3배 레버리지 ETF는 오차가 큽니다.** 이 화면은 판단을 돕는 참고 자료이며 매수·매도를 권유하지 않습니다.
""")


# ==========================================================================
# 페이지: 종목 발굴 (타이탄봇이 매일 계산한 테마·주도주·수급을 읽기만 함)
# ==========================================================================
TITAN_APP_URL = "https://titanbot.streamlit.app"


@st.cache_resource(show_spinner=False)
def _titan_engine(uri: str):
    from sqlalchemy import create_engine
    return create_engine(uri, pool_pre_ping=True, pool_recycle=1800)


def get_titan_engine():
    """Secrets를 매번 읽음 (연결 정보가 없던 상태가 캐시에 남지 않도록)."""
    try:
        uri = st.secrets.get("TITAN_DB_URI")
    except Exception:
        uri = None
    uri = uri or os.environ.get("TITAN_DB_URI")
    return _titan_engine(uri) if uri else None


@st.cache_data(ttl=600, show_spinner=False)
def titan_query(sql: str, params: tuple = ()) -> pd.DataFrame:
    from sqlalchemy import text
    eng = get_titan_engine()
    if eng is None:
        return pd.DataFrame()
    try:
        with eng.connect() as conn:
            return pd.read_sql(text(sql), conn, params=dict(params))
    except Exception as exc:
        LOGGER.warning("타이탄 DB 조회 실패: %s", exc)
        return pd.DataFrame()


def titan_latest(table: str, market: str) -> pd.DataFrame:
    return titan_query(f"SELECT * FROM {table} WHERE market=:m AND date = "
                       f"(SELECT MAX(date) FROM {table} WHERE market=:m) ORDER BY id", (("m", market),))


def hit_stats(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for hz, label in (("ret_5d", "5거래일 뒤"), ("ret_20d", "20거래일 뒤")):
        s = pd.to_numeric(df.get(hz), errors="coerce").dropna() if not df.empty else pd.Series(dtype=float)
        rows.append({"기간": label, "채점 수": len(s),
                     "적중률(%)": round(float((s > 0).mean() * 100), 1) if len(s) else None,
                     "평균 수익(%)": round(float(s.mean()), 2) if len(s) else None})
    return pd.DataFrame(rows)


def page_discover() -> None:
    page_header("DISCOVERY", "종목 발굴", "돈이 몰리는 테마 · 주도주 · 수급 (타이탄봇이 매일 아침 계산한 결과)",
                [("계산: 타이탄봇", MAGENTA), ("매일 자동 채점", GOLD)])
    if get_titan_engine() is None:
        st.warning("타이탄봇 DB 연결 정보가 없습니다. Streamlit → 발할라 앱 Settings → Secrets에 아래 한 줄을 추가하세요. "
                   "값은 타이탄봇의 DB_URI와 같습니다.", icon=":material/link_off:")
        st.code('TITAN_DB_URI = "postgresql+psycopg2://사용자:비밀번호@호스트:6543/postgres"', language="toml")
        return
    market = st.segmented_control("시장", ["US", "KR"], default="US", key="disc_mkt",
                                  format_func=lambda m: "미국" if m == "US" else "한국") or "US"
    themes_df = titan_latest("theme_radar", market)
    if themes_df.empty:
        st.info("아직 기록이 없습니다. 타이탄봇의 '테마·주도주·수급' 탭에서 계산하거나 내일 아침 자동 실행을 기다려 주세요.",
                icon=":material/hourglass_top:")
        st.link_button("타이탄봇 열기", TITAN_APP_URL, icon=":material/open_in_new:")
        return

    themes_df = themes_df.sort_values("rank")
    section(f"테마 순위 · 기준일 {themes_df['date'].iloc[0]}",
            "테마 점수 = 1주(20%) · 1개월(30%) · 3개월(50%) 수익률 순위의 가중 합. 4주 전보다 순위가 3계단 이상 오르면 '떠오르는 테마'.")
    hot = themes_df[themes_df["status"].isin(["주도 테마", "떠오르는 테마"])]
    cards = st.columns(max(min(len(hot), 4), 1))
    if hot.empty:
        st.caption("지금은 뚜렷하게 돈이 몰리는 테마가 없습니다.")
    for col, (_, r) in zip(cards, hot.head(4).iterrows()):
        col.metric(r["status"], r["theme"], f"3개월 {r['ret_3m']:+.1f}% · {int(r['rank'])}위",
                   delta_color=kr_delta(r["ret_3m"]), border=True)
    fig = go.Figure(go.Bar(x=themes_df["score"], y=themes_df["theme"], orientation="h",
                           marker_color=[UP if s in ("주도 테마", "떠오르는 테마") else NEUTRAL for s in themes_df["status"]],
                           text=[f"{v:.0f}" for v in themes_df["score"]], textposition="outside"))
    base_layout(fig, max(260, 26 * len(themes_df)), legend=False)
    fig.update_yaxes(autorange="reversed")
    fig.update_xaxes(range=[0, 105], title_text="테마 점수")
    show_chart(fig, key="disc_themes")
    with st.expander("테마 상세 표", icon=":material/table_chart:"):
        st.dataframe(themes_df[["rank", "theme", "ticker", "status", "score", "ret_1w", "ret_1m", "ret_3m", "rs_3m",
                                "rank_prev"]], hide_index=True, width="stretch",
                     column_config={"rank": "순위", "theme": "테마", "ticker": "대표 ETF", "status": "상태",
                                    "score": st.column_config.NumberColumn("점수", format="%.0f"),
                                    "ret_1w": st.column_config.NumberColumn("1주", format="%+.1f%%"),
                                    "ret_1m": st.column_config.NumberColumn("1개월", format="%+.1f%%"),
                                    "ret_3m": st.column_config.NumberColumn("3개월", format="%+.1f%%"),
                                    "rs_3m": st.column_config.NumberColumn("지수 대비", format="%+.1f%%p"),
                                    "rank_prev": "4주 전 순위"})

    section("주도주 후보", "지수 대비 강도(50) + 52주 고점 근접(30) + 이동평균선 정배열(20). 상위 테마 종목을 먼저 보여 줍니다.")
    leaders = titan_latest("leader_radar", market)
    if market == "KR":
        kr = titan_query("SELECT * FROM radar_signals WHERE market='KR' AND date = "
                         "(SELECT MAX(date) FROM radar_signals WHERE market='KR') ORDER BY rs DESC")
        if kr.empty:
            st.caption("한국 주도주 후보(타이탄 KRX 레이더) 기록이 아직 없습니다.")
        else:
            st.caption(f"타이탄 KRX 레이더 · 기준일 {kr['date'].iloc[0]} (거래대금 상위 · 정배열 · 코스피 대비 RS)")
            st.dataframe(kr[["ticker", "name", "price", "rs", "gap"]].head(20), hide_index=True, width="stretch",
                         column_config={"ticker": "코드", "name": "종목", "price": st.column_config.NumberColumn("현재가", format="localized"),
                                        "rs": st.column_config.NumberColumn("코스피 대비(%p)", format="%+.1f"),
                                        "gap": st.column_config.NumberColumn("20일선 이격(%)", format="%+.1f")})
    elif leaders.empty:
        st.caption("기록 없음")
    else:
        top = list(themes_df.head(5)["theme"])
        lv = leaders.assign(_top=leaders["theme"].isin(top)).sort_values(["_top", "score"], ascending=[False, False])
        st.dataframe(lv[["theme", "ticker", "price", "score", "rs_3m", "ret_3m", "from_high", "aligned"]].head(25),
                     hide_index=True, width="stretch",
                     column_config={"theme": "테마", "ticker": "종목", "price": st.column_config.NumberColumn("현재가", format="$%.2f"),
                                    "score": st.column_config.ProgressColumn("주도주 점수", min_value=0, max_value=100, format="%.0f"),
                                    "rs_3m": st.column_config.NumberColumn("지수 대비", format="%+.1f%%p"),
                                    "ret_3m": st.column_config.NumberColumn("3개월", format="%+.1f%%"),
                                    "from_high": st.column_config.NumberColumn("52주 고점 대비", format="%+.1f%%"),
                                    "aligned": st.column_config.CheckboxColumn("정배열")})

    section("수급", "한국: 최근 5거래일 외국인·기관 순매수 · 미국: 평소의 2배 이상 거래되며 상승 (매수세 유입 추정)")
    flows = titan_latest("flow_radar", market)
    if flows.empty:
        st.caption("기록 없음")
    elif market == "KR":
        st.dataframe(flows[["ticker", "name", "foreign_net", "inst_net"]], hide_index=True, width="stretch",
                     column_config={"ticker": "코드", "name": "종목",
                                    "foreign_net": st.column_config.NumberColumn("외국인 순매수(원)", format="localized"),
                                    "inst_net": st.column_config.NumberColumn("기관 순매수(원)", format="localized")})
    else:
        st.dataframe(flows[["ticker", "price", "chg_1d", "vol_ratio"]], hide_index=True, width="stretch",
                     column_config={"ticker": "종목", "price": st.column_config.NumberColumn("현재가", format="$%.2f"),
                                    "chg_1d": st.column_config.NumberColumn("등락", format="%+.1f%%"),
                                    "vol_ratio": st.column_config.NumberColumn("거래량 배수", format="%.1fx")})

    section("이 신호들은 실제로 맞았나", "기록한 종목의 5·20거래일 뒤 수익률로 채점합니다. 표본이 30개는 넘어야 의미가 있어요.")
    a, b = st.columns(2)
    a.markdown("**주도주 후보**")
    a.dataframe(hit_stats(titan_query("SELECT ret_5d, ret_20d FROM leader_radar WHERE market=:m", (("m", market),))),
                hide_index=True, width="stretch")
    b.markdown("**수급 종목**")
    b.dataframe(hit_stats(titan_query("SELECT ret_5d, ret_20d FROM flow_radar WHERE market=:m", (("m", market),))),
                hide_index=True, width="stretch")
    st.caption("이 화면은 후보를 좁혀 주는 참고 자료입니다. 새 종목을 사기 전에 '신호 관제'로 타이밍을 함께 확인하세요.")
    st.link_button("타이탄봇에서 테마 구성 편집 · 다시 계산", TITAN_APP_URL, icon=":material/open_in_new:")


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
        "dashboard": st.Page(page_dashboard, title="전술 관제", icon=":material/monitoring:", url_path="dashboard"),
        "signals": st.Page(page_signals, title="신호 관제", icon=":material/insights:", url_path="signals"),
        "discover": st.Page(page_discover, title="종목 발굴", icon=":material/travel_explore:", url_path="discover"),
        "radar": st.Page(page_radar, title="시장 레이더", icon=":material/radar:", url_path="radar"),
        "briefing": st.Page(page_briefing, title="브리핑", icon=":material/today:", url_path="briefing", default=True),
        "arena": st.Page(page_arena, title="선수 경기장", icon=":material/emoji_events:", url_path="arena"),
        "research": st.Page(page_research, title="연구실", icon=":material/biotech:", url_path="research"),
        "principles": st.Page(page_principles, title="원칙 · 위험", icon=":material/shield:", url_path="principles"),
        "ledger": st.Page(page_ledger, title="세금 · 사이클", icon=":material/receipt_long:", url_path="ledger"),
        "portfolio": st.Page(page_portfolio, title="내 포트폴리오", icon=":material/account_balance_wallet:",
                             url_path="portfolio"),
        "league": st.Page(page_league, title="AI 실전 리그", icon=":material/emoji_events:", url_path="league"),
        "training": st.Page(page_training, title="AI 훈련소", icon=":material/model_training:", url_path="training"),
        "journal": st.Page(page_journal, title="매매 일지", icon=":material/menu_book:", url_path="journal"),
        "validation": st.Page(page_validation, title="예측 검증실", icon=":material/science:", url_path="validation"),
    })
    nav = st.navigation({"운용": [PAGES["briefing"], PAGES["dashboard"], PAGES["portfolio"], PAGES["signals"],
                                 PAGES["arena"], PAGES["discover"], PAGES["radar"]],
                         "계좌 · 원칙": [PAGES["principles"], PAGES["ledger"]],
                         "연구 · 관리": [PAGES["research"], PAGES["journal"]],
                         "보관함": [PAGES["league"], PAGES["training"], PAGES["validation"]]})
    try:
        core.set_agents(load_models())          # AI 요원을 선수 경기장 선수로 등록 (유전자 그대로)
    except Exception:
        LOGGER.exception("요원 선수 등록 실패")
    render_sidebar()
    show_flash()
    nav.run()


if __name__ == "__main__":
    main()
