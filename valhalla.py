import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from scipy.optimize import minimize
from supabase import create_client
import os
import datetime
import io
import warnings
import re
import json
import random

warnings.filterwarnings('ignore')


# ==========================================
# 1. 인공지능 퀀트 엔진 (Dynamic Linear Perceptron 적용)
# ==========================================
class TitanRestExitOptimizer:
    def __init__(self, params=None):
        self.params = np.array(params) if params is not None else np.array([10.0, 2.0, -3.0, 1.5, 5.0])

    def find_golden_ratio(self, X):
        w_base, w_ma, w_rsi, w_vol, w_risk = self.params
        ma_norm = np.clip(X[0] / 10.0, -1.0, 1.0)
        rsi_norm = (X[1] - 50.0) / 50.0
        vol_norm = np.clip(X[2] / 5.0, 0.0, 2.0)
        target_r = w_base + (w_ma * ma_norm) + (w_rsi * rsi_norm) + (w_vol * vol_norm)
        target_r = np.clip(target_r, 3.0, 30.0)
        prob = np.clip(95.0 - (target_r * 1.2) + w_risk, 10.0, 99.9)
        est_days = max(1.0, target_r * 1.1 - (w_risk * 0.1))

        return {
            'optimal_r': round(target_r, 2),
            'success_probability': round(prob, 1),
            'est_days': round(est_days, 1)
        }


ai_engine = TitanRestExitOptimizer()

# ==========================================
# 2. 하이브리드 DB 통신 모듈 (CSV + Supabase)
# ==========================================
DB_FILE = "trade_journal.csv"
AI_MODELS_FILE = "ai_models.json"
AI_LEDGER_FILE = "ai_ledger.csv"


@st.cache_resource
def get_supabase_client():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except:
        return None


supabase = get_supabase_client()


def init_local_files():
    if not os.path.exists(DB_FILE):
        pd.DataFrame(columns=["Date", "Time", "Account", "Ticker", "Action", "Qty", "Price", "Format"]).to_csv(DB_FILE,
                                                                                                               index=False)
    if not os.path.exists(AI_MODELS_FILE):
        initial_models = {
            "Agent_Alpha (초기화)": [15.0, 2.0, -3.0, 1.0, 8.0],
            "Agent_Beta (초기화)": [5.0, 0.5, -1.0, 0.5, 2.0],
            "Agent_Gamma (초기화)": [10.0, 1.0, -2.0, 1.5, 5.0]
        }
        with open(AI_MODELS_FILE, 'w') as f:
            json.dump(initial_models, f)
    if not os.path.exists(AI_LEDGER_FILE):
        pd.DataFrame(columns=["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"]).to_csv(
            AI_LEDGER_FILE, index=False)


if not supabase:
    init_local_files()


def read_db(table_name):
    if supabase:
        res = supabase.table(table_name).select("*").execute()
        df = pd.DataFrame(res.data)
        if df.empty:
            if table_name == "trade_journal":
                return pd.DataFrame(columns=["Date", "Time", "Account", "Ticker", "Action", "Qty", "Price", "Format"])
            else:
                return pd.DataFrame(columns=["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"])
        if 'id' in df.columns: df = df.drop(columns=['id'])
        return df
    else:
        file_name = DB_FILE if table_name == "trade_journal" else AI_LEDGER_FILE
        try:
            return pd.read_csv(file_name)
        except:
            return pd.DataFrame()


def overwrite_db(table_name, df_new):
    if supabase:
        supabase.table(table_name).delete().neq('id', 0).execute()  # 전체 삭제
        if not df_new.empty:
            records = df_new.to_dict(orient='records')
            supabase.table(table_name).insert(records).execute()
    else:
        file_name = DB_FILE if table_name == "trade_journal" else AI_LEDGER_FILE
        df_new.to_csv(file_name, index=False)


def append_db(table_name, df_append):
    if supabase:
        records = df_append.to_dict(orient='records')
        supabase.table(table_name).insert(records).execute()
    else:
        file_name = DB_FILE if table_name == "trade_journal" else AI_LEDGER_FILE
        df_append.to_csv(file_name, mode='a', header=False, index=False)


# ==========================================
# 3. 실시간 차트 데이터 로더 & 파서
# ==========================================
@st.cache_data(ttl=3600)
def get_realtime_data(ticker):
    end_date = datetime.date.today() + datetime.timedelta(days=1)
    start_date = end_date - datetime.timedelta(days=365)
    df = yf.download(ticker, start=start_date, end=end_date, progress=False)

    if df.empty: return None, None, None
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)

    close, volume = df['Close'], df['Volume']
    current_price = close.iloc[-1]

    ma20 = close.rolling(window=20).mean()
    x1 = ((current_price - ma20.iloc[-1]) / ma20.iloc[-1]) * 100.0
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    x2 = 100.0 - (100.0 / (1.0 + rs)).iloc[-1]
    x3 = (volume.iloc[-1] / volume.rolling(window=20).mean().iloc[-1])
    vol = close.pct_change().rolling(window=20).std().iloc[-1] * 100.0

    return df, [round(x1, 2), round(x2, 2), round(x3, 2), round(vol, 2)], round(current_price, 2)


def parse_kiwoom_csv_to_db(file_obj):
    filename = file_obj.name
    match = re.search(r'\d+', filename)
    account_tag = match.group() if match else "Default"
    file_bytes = file_obj.getvalue()
    df = None
    for enc in ['cp949', 'euc-kr', 'utf-8', 'utf-8-sig']:
        try:
            df = pd.read_csv(io.BytesIO(file_bytes), encoding=enc, header=None, names=range(30), engine='python')
            break
        except:
            continue
    if df is None or df.empty: return pd.DataFrame()
    format_type = None;
    start_idx = 0
    for idx, row in df.iterrows():
        first_cell = str(row[0]).strip()
        if first_cell == '주문일자':
            format_type = '2row'; start_idx = idx + 2; break
        elif first_cell == '거래일자':
            format_type = '3row'; start_idx = idx + 3; break
    if format_type is None: return pd.DataFrame()
    data_rows = df.iloc[start_idx:].reset_index(drop=True)
    trades = []

    def format_date(raw_date):
        d = str(raw_date).strip().replace('/', '-').replace('.', '-')
        if len(d) == 8 and '-' not in d: d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d

    if format_type == '2row':
        for i in range(0, len(data_rows) - 1, 2):
            r1, r2 = data_rows.iloc[i], data_rows.iloc[i + 1]
            if pd.isna(r1[0]) or str(r1[0]).strip() in ['', 'nan']: continue
            date_str = format_date(r1[0])
            time_str = str(r2[0]).strip() if pd.notna(r2[0]) else f"00:00:{i % 60:02d}"
            ticker = str(r1[2]).replace("'", "").strip()
            action = "Buy" if "매수" in str(r2[3]) else "Sell"
            try:
                qty = float(str(r1[6]).replace(',', '')); price = float(str(r2[6]).replace(',', ''))
            except:
                continue
            if qty > 0: trades.append(
                {"Date": date_str, "Time": time_str, "Account": account_tag, "Ticker": ticker, "Action": action,
                 "Qty": qty, "Price": price, "Format": "2row"})
    elif format_type == '3row':
        for i in range(0, len(data_rows) - 2, 3):
            r1, r2 = data_rows.iloc[i], data_rows.iloc[i + 1]
            if pd.isna(r1[0]) or str(r1[0]).strip() in ['', 'nan']: continue
            if str(r1[1]).strip() != '매매': continue
            date_str = format_date(r1[0])
            time_str = str(r2[10]).strip() if len(r2) > 10 and pd.notna(r2[10]) else f"00:00:{i % 60:02d}"
            ticker = str(r2[0]).replace("'", "").strip()
            action = "Buy" if "매수" in str(r2[1]) else "Sell"
            try:
                qty = float(str(r1[3]).replace(',', '')); price = float(str(r2[2]).replace(',', ''))
            except:
                continue
            if qty > 0: trades.append(
                {"Date": date_str, "Time": time_str, "Account": account_tag, "Ticker": ticker, "Action": action,
                 "Qty": qty, "Price": price, "Format": "3row"})
    return pd.DataFrame(trades)


# ==========================================
# 4. 하이브리드 유전 알고리즘 (베이스 훈련 + 파인튜닝)
# ==========================================
def run_genetic_algorithm_training(ticker="SOXL", population_size=100):
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=3650)  # 약 10년 (다양성 확보)
    df = yf.download(ticker, start=start_date, end=end_date, progress=False)

    if df.empty: return None
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)

    df['MA20'] = df['Close'].rolling(window=20).mean()
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))
    df['Vol_Ratio'] = df['Volume'] / df['Volume'].rolling(window=20).mean()
    df['Volatility'] = df['Close'].pct_change().rolling(window=20).std() * 100
    df.dropna(inplace=True)

    trading_days_per_year = 252
    if len(df) < trading_days_per_year * 2:
        return None

    df_history = df.iloc[:-trading_days_per_year]  # 베이스 훈련용 (과거)
    df_recent = df.iloc[-trading_days_per_year:]  # 파인 튜닝용 (최근 1년)

    agents = []
    for i in range(population_size):
        weights = [
            random.uniform(5.0, 20.0),
            random.uniform(-5.0, 5.0),
            random.uniform(-5.0, 5.0),
            random.uniform(-5.0, 5.0),
            random.uniform(0.0, 10.0)
        ]
        agents.append({"id": f"Agent_Gen_{i}", "weights": weights, "base_score": 0.0, "final_score": 0.0})

    def simulate_agent(agent_weights, test_data):
        agent_engine = TitanRestExitOptimizer(params=agent_weights)
        current_qty, total_cost, profit = 0.0, 0.0, 0.0

        for i in range(len(test_data)):
            row = test_data.iloc[i]
            p_close, p_high = float(row['Close']), float(row['High'])
            X = [
                ((p_close - float(row['MA20'])) / float(row['MA20'])) * 100.0,
                float(row['RSI']),
                float(row['Vol_Ratio']),
                float(row['Volatility'])
            ]

            avg_price = total_cost / current_qty if current_qty > 0 else 0
            prediction = agent_engine.find_golden_ratio(X)
            target_price = avg_price * (1 + (prediction['optimal_r'] / 100.0))

            if current_qty > 0 and p_high >= target_price:
                profit += (target_price - avg_price) * current_qty
                current_qty, total_cost = 0.0, 0.0
            else:
                trade_qty = 100.0 / p_close
                current_qty += trade_qty
                total_cost += 100.0

        unrealized = (current_qty * float(test_data['Close'].iloc[-1])) - total_cost
        return profit + unrealized

    # PHASE 1: 베이스 훈련 (과거 데이터 중 랜덤 2개 구간)
    random_starts = [random.randint(0, len(df_history) - trading_days_per_year) for _ in range(2)]

    for agent in agents:
        total_base_score = 0
        for start_idx in random_starts:
            chunk = df_history.iloc[start_idx: start_idx + trading_days_per_year]
            total_base_score += simulate_agent(agent['weights'], chunk)
        agent['base_score'] = total_base_score

    survivors = sorted(agents, key=lambda x: x['base_score'], reverse=True)[:int(population_size / 2)]

    # PHASE 2: 파인 튜닝 (최근 1년 데이터로 트렌드 적응)
    for agent in survivors:
        recent_score = simulate_agent(agent['weights'], df_recent)
        agent['final_score'] = (agent['base_score'] * 0.3) + (recent_score * 0.7)

    elite_agents = sorted(survivors, key=lambda x: x['final_score'], reverse=True)[:3]

    for agent in elite_agents:
        agent['total_score'] = agent['final_score']

    return elite_agents


# ==========================================
# 5. 웹 UI 구현 (Streamlit)
# ==========================================
st.set_page_config(page_title="무한매수 전술 관제소", layout="wide")
st.title("🚀 무한매수 전술 관제소 (Project Valhalla)")

if supabase:
    st.sidebar.success("🟢 클라우드 DB (Supabase) 연결됨")
else:
    st.sidebar.warning("🟡 로컬 DB (CSV) 사용 중")

tab1, tab4, tab2, tab3 = st.tabs(["📊 전술 관제", "⚔️ 발할라 실전 리그", "🧠 훈련소 (진화)", "🗄️ 매매 일지"])

# ------------------------------------------
# 사이드바 (대량 매매 일지 동기화)
# ------------------------------------------
st.sidebar.header("📂 대량 매매 일지 동기화")
uploaded_files = st.sidebar.file_uploader("키움증권 CSV 파일을 드롭하세요", accept_multiple_files=True, type=['csv'])
if uploaded_files:
    if st.sidebar.button("🚀 업로드된 파일 파싱 및 DB 합치기"):
        parsed_dfs = []
        for file in uploaded_files:
            df_parsed = parse_kiwoom_csv_to_db(file)
            if not df_parsed.empty: parsed_dfs.append(df_parsed)
        if parsed_dfs:
            df_new = pd.concat(parsed_dfs)
            df_old = read_db("trade_journal")

            if 'Time' not in df_old.columns: df_old.insert(1, 'Time', "00:00:00")
            if 'Account' not in df_old.columns: df_old.insert(2, 'Account', "Default")
            if 'Format' not in df_old.columns: df_old['Format'] = "unknown"

            df_combined = pd.concat([df_old, df_new])
            df_combined['Date'] = pd.to_datetime(df_combined['Date']).dt.strftime('%Y-%m-%d')
            df_combined['group_id'] = df_combined['Date'] + "_" + df_combined['Account'] + "_" + df_combined[
                'Ticker'] + "_" + df_combined['Action']
            groups_with_2row = df_combined[df_combined['Format'] == '2row']['group_id'].unique()
            mask = ~((df_combined['group_id'].isin(groups_with_2row)) & (df_combined['Format'] == '3row'))
            df_resolved = df_combined[mask].drop(columns=['group_id'])
            df_final = df_resolved.drop_duplicates(
                subset=["Date", "Time", "Account", "Ticker", "Action", "Qty", "Price"])
            df_final = df_final.sort_values(["Date", "Time"])

            overwrite_db("trade_journal", df_final)
            st.sidebar.success(f"✅ 동기화 완료! 클라우드에 저장되었습니다.")

# ------------------------------------------
# TAB 1: 전술 관제
# ------------------------------------------
with tab1:
    view_ticker = st.text_input("🔍 관제할 티커를 입력하세요", "SOXL").upper()
    if view_ticker:
        df_journal = read_db("trade_journal")
        if not df_journal.empty:
            if 'Time' not in df_journal.columns: df_journal.insert(1, 'Time', "00:00:00")
            if 'Account' not in df_journal.columns: df_journal.insert(2, 'Account', "Default")
            available_accounts = df_journal[df_journal['Ticker'] == view_ticker]['Account'].unique().tolist()

            col_opt1, col_opt2 = st.columns(2)
            with col_opt1:
                cycle_start = st.date_input(f"📅 사이클 시작일", value=datetime.date(2026, 4, 20))
            with col_opt2:
                selected_accounts = st.multiselect(f"🏷️ 추적 계좌", options=available_accounts, default=available_accounts)

            df_chart, state_vector, current_price = get_realtime_data(view_ticker)

            if df_chart is not None:
                col_chart, col_ai = st.columns([7, 3])
                trades = df_journal[
                    (df_journal['Ticker'] == view_ticker) & (df_journal['Date'] >= cycle_start.strftime("%Y-%m-%d")) & (
                        df_journal['Account'].isin(selected_accounts))].sort_values(["Date", "Time"])

                current_qty, total_cost, avg_price = 0.0, 0.0, 0.0
                for _, row in trades.iterrows():
                    qty, price = float(row['Qty']), float(row['Price'])
                    if row['Action'] == 'Buy':
                        current_qty += qty; total_cost += qty * price; avg_price = total_cost / current_qty if current_qty > 0 else 0
                    elif row['Action'] == 'Sell':
                        current_qty -= qty
                        if current_qty <= 0.01:
                            current_qty, total_cost, avg_price = 0.0, 0.0, 0.0
                        else:
                            total_cost = current_qty * avg_price

                prediction = ai_engine.find_golden_ratio(state_vector)
                ai_target_price = current_price * (1 + prediction['optimal_r'] / 100) if prediction else 0

                with col_ai:
                    st.markdown(f"### 🛡️ 사령관의 포지션")
                    st.info(f"**수량:** {int(current_qty)} 주")
                    user_target_pct = st.slider("🎯 목표 수익률 조절 (%)", 1.0, 50.0, 13.0, 0.5)
                    user_target_price = avg_price * (1 + user_target_pct / 100) if avg_price > 0 else 0
                    if current_qty > 0:
                        st.metric("💰 나의 평단가", f"${avg_price:,.4f}");
                        st.metric(f"🎯 나의 목표가 (+{user_target_pct}%)", f"${user_target_price:,.2f}")
                    else:
                        st.write("현재 진행 중인 사이클(잔고)이 없습니다.")

                    st.markdown("---")
                    st.markdown(f"### 🧠 AI 전술 분석")
                    st.info(f"현재가: ${current_price:,.2f}")
                    if prediction:
                        st.metric("🤖 AI 최적 매도 목표가", f"+{prediction['optimal_r']}%", f"${ai_target_price:,.2f}")
                        st.metric("승률 (Probability)", f"{prediction['success_probability']}%")
                        st.metric("예상 보유 기간", f"약 {prediction['est_days']}일")

                with col_chart:
                    time_correction = st.checkbox("🇺🇸 미국 주식 시차 보정 (-1일 적용)", value=True)

                    fig = go.Figure(data=[go.Candlestick(x=df_chart.index, open=df_chart['Open'], high=df_chart['High'],
                                                         low=df_chart['Low'], close=df_chart['Close'], name="Candle")])

                    if prediction: fig.add_hline(y=ai_target_price, line_dash="dash", line_color="cyan",
                                                 annotation_text=f"AI 목표가 (${ai_target_price:.2f})",
                                                 annotation_position="top left")
                    if current_qty > 0:
                        fig.add_hline(y=avg_price, line_dash="dot", line_color="yellow",
                                      annotation_text=f"나의 평단가 (${avg_price:.4f})", annotation_position="bottom right")
                        fig.add_hline(y=user_target_price, line_dash="solid", line_color="magenta",
                                      annotation_text=f"나의 목표가 (+{user_target_pct}%, ${user_target_price:.2f})",
                                      annotation_position="top right")

                    buys = trades[trades['Action'] == 'Buy'].copy()
                    if not buys.empty:
                        buys['Plot_Date'] = pd.to_datetime(buys['Date']) - (
                            pd.Timedelta(days=1) if time_correction else pd.Timedelta(days=0))
                        fig.add_trace(go.Scatter(x=buys['Plot_Date'], y=buys['Price'], mode='markers', name='나의 매수',
                                                 marker=dict(symbol='triangle-up', color='lime', size=14,
                                                             line=dict(width=1, color='black')),
                                                 text=buys['Qty'].astype(str) + "주 매수", hoverinfo="text+y+x"))

                    sells = trades[trades['Action'] == 'Sell'].copy()
                    if not sells.empty:
                        sells['Plot_Date'] = pd.to_datetime(sells['Date']) - (
                            pd.Timedelta(days=1) if time_correction else pd.Timedelta(days=0))
                        fig.add_trace(go.Scatter(x=sells['Plot_Date'], y=sells['Price'], mode='markers', name='나의 매도',
                                                 marker=dict(symbol='triangle-down', color='red', size=14,
                                                             line=dict(width=1, color='black')),
                                                 text=sells['Qty'].astype(str) + "주 매도", hoverinfo="text+y+x"))

                    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])]);
                    fig.update_layout(title=f"{view_ticker} 차트 및 전술 오버레이", yaxis_title="Price (USD)",
                                      template="plotly_dark", height=600)
                    st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("현재 기록된 매매 일지가 없습니다. 사이드바에서 CSV를 업로드하거나 일지를 수동으로 추가하세요.")

# ------------------------------------------
# TAB 4: ⚔️ 발할라 실전 리그
# ------------------------------------------
with tab4:
    st.markdown("## ⚔️ 발할라 실전 리그 (Valhalla Live League)")
    st.write("사령관님과 상위 3명의 정예 AI 요원이 '오늘의 시장'에서 실시간으로 경쟁합니다. 매일 장 마감 후 시뮬레이터를 가동하세요!")

    try:
        with open(AI_MODELS_FILE, 'r') as f:
            ai_models = json.load(f)
    except:
        ai_models = {}

    col1, col2 = st.columns([1, 1])
    with col1:
        st.info("🧬 **현재 참전 중인 정예 AI 요원**")
        for agent, weights in ai_models.items():
            st.write(f"- **{agent}** (고유 진화 가중치 탑재)")

    with col2:
        st.info("🚨 **일일 가상 매매(Paper Trading) 시동**")
        sim_ticker = st.selectbox("종목 선택", ["SOXL", "FNGU", "CURE"])

        if st.button(f"📅 오늘({datetime.date.today()})의 AI 가상 매매 실행"):
            df_chart, state_vector, current_price = get_realtime_data(sim_ticker)
            df_ai = read_db("ai_ledger")

            new_records = []
            for agent, weights in ai_models.items():
                agent_trades = df_ai[
                    (df_ai['Agent'] == agent) & (df_ai['Ticker'] == sim_ticker)] if not df_ai.empty else pd.DataFrame()
                current_qty, total_cost = 0.0, 0.0

                for _, row in agent_trades.iterrows():
                    qty, price = float(row['Qty']), float(row['Price'])
                    if row['Action'] == 'Buy':
                        current_qty += qty;
                        total_cost += qty * price
                    elif row['Action'] == 'Sell':
                        current_qty -= qty
                        if current_qty <= 0.01:
                            current_qty, total_cost = 0.0, 0.0
                        else:
                            total_cost = current_qty * (total_cost / (current_qty + qty))

                avg_price = total_cost / current_qty if current_qty > 0 else 0

                agent_engine = TitanRestExitOptimizer(params=weights)
                prediction = agent_engine.find_golden_ratio(state_vector)

                target_r = prediction['optimal_r'] if prediction else 10.0
                target_price = avg_price * (1 + (target_r / 100.0))

                action, trade_qty, profit = "Hold", 0.0, 0.0

                if current_qty > 0 and current_price >= target_price:
                    action = "Sell"
                    trade_qty = round(current_qty, 4)
                    profit = (current_price - avg_price) * trade_qty
                else:
                    action = "Buy"
                    trade_qty = round(100.0 / current_price, 4)
                    profit = 0.0

                if action != "Hold":
                    new_records.append(
                        {"Date": datetime.date.today().strftime("%Y-%m-%d"), "Agent": agent, "Ticker": sim_ticker,
                         "Action": action, "Qty": trade_qty, "Price": current_price, "Capital": 10000,
                         "Profit": round(profit, 2)})

            if new_records:
                append_db("ai_ledger", pd.DataFrame(new_records))
                st.toast("요원들이 오늘의 실제 종가를 확인하고 매매를 완료했습니다!")
                st.success("데이터가 AI 장부에 성공적으로 동기화되었습니다.")
                st.rerun()

    st.markdown("---")
    st.markdown("### 🏆 실시간 리더보드 (누적 수익 경쟁)")

    try:
        user_profit = 2491.59
    except:
        user_profit = 0.0

    df_ai = read_db("ai_ledger")
    if not df_ai.empty:
        ai_profits = df_ai.groupby('Agent')['Profit'].sum().to_dict()
    else:
        ai_profits = {agent: 0 for agent in ai_models.keys()}

    leaderboard = [{"Rank": 1, "Name": "👑 사령관님 (Commander)", "Total Profit": f"${user_profit:,.2f}"}]
    for agent, prof in ai_profits.items(): leaderboard.append(
        {"Rank": 2, "Name": f"🤖 {agent}", "Total Profit": f"${prof:,.2f}"})

    leaderboard = sorted(leaderboard, key=lambda x: float(x["Total Profit"].replace('$', '').replace(',', '')),
                         reverse=True)
    for i, l in enumerate(leaderboard): l["Rank"] = i + 1

    st.dataframe(pd.DataFrame(leaderboard), use_container_width=True)

# ------------------------------------------
# TAB 2 & 3: 훈련소 & 매매 일지
# ------------------------------------------
with tab2:
    st.markdown("### 🧠 훈련소 (Bootcamp): 하이브리드 유전 알고리즘(GA) 실시간 연산")
    st.write("100명의 랜덤 유전자를 가진 AI를 생성하여 과거 10년 역사(베이스 훈련)에서 적자생존을 거친 뒤, 최근 1년(파인 튜닝)의 트렌드에 적응시켜 최고 정예 요원 3명을 선발합니다.")

    if st.button("⚔️ 100명 AI 트레이더 하이브리드 훈련 가동 (연산에 시간이 소요됩니다)"):
        with st.spinner("서버 코어가 10년 치 백테스팅과 파인튜닝을 수행 중입니다... 대기하십시오."):
            top_agents = run_genetic_algorithm_training(ticker="SOXL", population_size=100)

            if top_agents:
                new_models = {
                    f"Agent_Rank_1 (누적 점수 {top_agents[0]['total_score']:.2f})": top_agents[0]['weights'],
                    f"Agent_Rank_2 (누적 점수 {top_agents[1]['total_score']:.2f})": top_agents[1]['weights'],
                    f"Agent_Rank_3 (누적 점수 {top_agents[2]['total_score']:.2f})": top_agents[2]['weights']
                }
                with open(AI_MODELS_FILE, 'w') as f:
                    json.dump(new_models, f)

                overwrite_db("ai_ledger", pd.DataFrame(
                    columns=["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"]))

                st.success("🎯 하이브리드 유전자 훈련 완료! 역대 최고 성능을 낸 3명의 요원이 실전 리그(발할라)에 새로 참전했습니다.")
                st.rerun()
            else:
                st.error("데이터를 불러오지 못해 훈련에 실패했습니다. (최소 2년 이상의 데이터 필요)")

with tab3:
    st.markdown("### 🗄️ 사령관 매매 일지 DB 관리")

    with st.expander("✍️ 수동으로 매매 기록 추가하기", expanded=True):
        with st.form("main_trade_form", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                t_date = st.date_input("거래 일자", datetime.date.today())
                t_time = st.text_input("거래 시간 (HH:MM:SS)", datetime.datetime.now().strftime("%H:%M:%S"))
                t_account = st.text_input("계좌명", "Manual")
            with col2:
                t_ticker = st.text_input("종목 티커", "SOXL").upper()
                t_action = st.radio("매매 구분", ["매수 (Buy)", "매도 (Sell)"])
            with col3:
                t_price = st.number_input("체결 단가 ($)", min_value=0.0, format="%.4f")
                t_qty = st.number_input("수량 (주)", min_value=1, step=1)

            submitted = st.form_submit_button("💾 일지 DB에 저장")
            if submitted:
                action_val = "Buy" if "매수" in t_action else "Sell"
                new_record = pd.DataFrame(
                    {"Date": [t_date.strftime("%Y-%m-%d")], "Time": [t_time], "Account": [t_account],
                     "Ticker": [t_ticker], "Action": [action_val], "Qty": [t_qty], "Price": [t_price],
                     "Format": ["manual"]})
                append_db("trade_journal", new_record)
                st.success(f"✅ 성공적으로 클라우드 DB에 저장되었습니다!");
                st.rerun()

    st.markdown("---")

    col_db1, col_db2 = st.columns([8, 2])
    with col_db1:
        df_display = read_db("trade_journal")
        st.dataframe(df_display, use_container_width=True)
    with col_db2:
        if st.button("🗑️ 내 기록 초기화", key="reset_db"):
            overwrite_db("trade_journal", pd.DataFrame(
                columns=["Date", "Time", "Account", "Ticker", "Action", "Qty", "Price", "Format"]))
            st.rerun()

    st.markdown("### 🗄️ AI 요원 가상 매매 장부 (Paper Trading)")

    col_ai1, col_ai2 = st.columns([8, 2])
    with col_ai1:
        df_ai_disp = read_db("ai_ledger")
        if not df_ai_disp.empty:
            st.dataframe(df_ai_disp, use_container_width=True)
        else:
            st.write("기록이 없습니다.")
    with col_ai2:
        if st.button("🗑️ AI 장부 초기화", key="reset_ai"):
            overwrite_db("ai_ledger", pd.DataFrame(
                columns=["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"]))
            st.rerun()
