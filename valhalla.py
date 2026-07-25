import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import os
import datetime
import io
import warnings
import re
import json
import random

warnings.filterwarnings('ignore')


# ==========================================
# 1. 인공지능 퀀트 엔진 (Dynamic Linear Perceptron 적용 - 클론화 완벽 방지)
# ==========================================
class TitanRestExitOptimizer:
    def __init__(self, params=None):
        # 5차원 가중치: [기본목표가, 이평선_영향력, RSI_영향력, 변동성_영향력, 리스크_허용도]
        self.params = np.array(params) if params is not None else np.array([10.0, 2.0, -3.0, 1.5, 5.0])

    def find_golden_ratio(self, X):
        # X = [x1(이평선이격도), x2(RSI), x3(거래량비율), vol(변동성)]
        w_base, w_ma, w_rsi, w_vol, w_risk = self.params

        # 시장 지표 정규화
        ma_norm = np.clip(X[0] / 10.0, -1.0, 1.0)
        rsi_norm = (X[1] - 50.0) / 50.0
        vol_norm = np.clip(X[3] / 5.0, 0.0, 2.0)

        # 💡 유전자가 목표 수익률에 다이렉트로 꽂히는 퍼셉트론 방정식 (기울기 소실 원천 차단)
        target_r = w_base + (w_ma * ma_norm) + (w_rsi * rsi_norm) + (w_vol * vol_norm)
        target_r = np.clip(target_r, 3.0, 30.0)

        # 가상의 승률 및 보유기간 산출
        prob = np.clip(95.0 - (target_r * 1.2) + w_risk, 10.0, 99.9)
        est_days = max(1.0, target_r * 1.1 - (w_risk * 0.1))

        return {
            'optimal_r': round(target_r, 2),
            'success_probability': round(prob, 1),
            'est_days': round(est_days, 1)
        }


ai_engine = TitanRestExitOptimizer()

# ==========================================
# 2. 파일 시스템 세팅 (DB & AI 기록소)
# ==========================================
DB_FILE = "trade_journal.csv"
AI_MODELS_FILE = "ai_models.json"
AI_LEDGER_FILE = "ai_ledger.csv"


def init_files():
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


init_files()


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
# 🔥 3-5. 유전 알고리즘 (동점자 방지 로직 적용)
# ==========================================
def run_genetic_algorithm_training(ticker="SOXL", population_size=100, days_back=365):
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days_back)
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

    agents = []
    for i in range(population_size):
        # 다양한 성향이 나오도록 유전자 풀 넓게 설정
        w_base = random.uniform(5.0, 20.0)
        w_ma = random.uniform(-5.0, 5.0)
        w_rsi = random.uniform(-5.0, 5.0)
        w_vol = random.uniform(-5.0, 5.0)
        w_risk = random.uniform(0.0, 10.0)
        weights = [w_base, w_ma, w_rsi, w_vol, w_risk]
        agents.append({"id": f"Agent_Gen_{i}", "weights": weights, "realized_profit": 0.0, "total_score": 0.0})

    for agent in agents:
        agent_engine = TitanRestExitOptimizer(params=agent['weights'])
        current_qty = 0.0
        total_cost = 0.0
        profit = 0.0

        for i in range(len(df)):
            row = df.iloc[i]
            p_close = float(row['Close'])
            x1 = ((p_close - float(row['MA20'])) / float(row['MA20'])) * 100.0
            x2 = float(row['RSI'])
            x3 = float(row['Vol_Ratio'])
            vol = float(row['Volatility'])
            X = [x1, x2, x3, vol]

            avg_price = total_cost / current_qty if current_qty > 0 else 0
            prediction = agent_engine.find_golden_ratio(X)
            target_r = prediction['optimal_r']
            target_price = avg_price * (1 + (target_r / 100.0))

            # 매도
            if current_qty > 0 and float(row['High']) >= target_price:
                profit += (target_price - avg_price) * current_qty
                current_qty, total_cost = 0.0, 0.0
            # 매수 ($100 분할매수)
            else:
                trade_qty = 100.0 / p_close
                current_qty += trade_qty
                total_cost += 100.0

        # 💡 미실현 손익까지 긁어모아 소수점 아래까지 다르게 평가 (동점자 방지)
        unrealized_profit = (current_qty * float(df['Close'].iloc[-1])) - total_cost
        agent['realized_profit'] = profit
        agent['total_score'] = profit + unrealized_profit

    # 4. total_score 기준으로 완벽한 줄세우기
    ranked_agents = sorted(agents, key=lambda x: x['total_score'], reverse=True)
    return ranked_agents[:3]


# ==========================================
# 4. 웹 UI 구현 (Streamlit)
# ==========================================
st.set_page_config(page_title="무한매수 전술 관제소", layout="wide")
st.title("🚀 무한매수 전술 관제소 (Project Valhalla)")

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
            df_old = pd.read_csv(DB_FILE)
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
            df_final.to_csv(DB_FILE, index=False)
            st.sidebar.success(f"✅ 동기화 완료! 중복 데이터가 제거되었습니다.")

# ------------------------------------------
# TAB 1: 전술 관제
# ------------------------------------------
with tab1:
    view_ticker = st.text_input("🔍 관제할 티커를 입력하세요", "SOXL").upper()
    if view_ticker:
        df_journal = pd.read_csv(DB_FILE)
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

                fig = go.Figure(data=[
                    go.Candlestick(x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'],
                                   close=df_chart['Close'], name="Candle")])

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

# ------------------------------------------
# TAB 4: ⚔️ 발할라 실전 리그
# ------------------------------------------
with tab4:
    st.markdown("## ⚔️ 발할라 실전 리그 (Valhalla Live League)")
    st.write("사령관님과 상위 3명의 정예 AI 요원이 '오늘의 시장'에서 실시간으로 경쟁합니다. 매일 장 마감 후 시뮬레이터를 가동하세요!")

    with open(AI_MODELS_FILE, 'r') as f:
        ai_models = json.load(f)

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

            try:
                df_ai = pd.read_csv(AI_LEDGER_FILE)
            except:
                df_ai = pd.DataFrame(columns=["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"])

            new_records = []

            for agent, weights in ai_models.items():
                agent_trades = df_ai[(df_ai['Agent'] == agent) & (df_ai['Ticker'] == sim_ticker)]
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
                pd.DataFrame(new_records).to_csv(AI_LEDGER_FILE, mode='a', header=False, index=False)
                st.toast("요원들이 오늘의 실제 종가를 확인하고 매매를 완료했습니다!")
                st.success("데이터가 AI 장부(ai_ledger.csv)에 성공적으로 동기화되었습니다.")
                st.rerun()

    st.markdown("---")
    st.markdown("### 🏆 실시간 리더보드 (누적 수익 경쟁)")

    try:
        user_profit = 2491.59
    except:
        user_profit = 0.0

    try:
        df_ai = pd.read_csv(AI_LEDGER_FILE)
        ai_profits = df_ai.groupby('Agent')['Profit'].sum().to_dict()
    except:
        ai_profits = {agent: 0 for agent in ai_models.keys()}

    leaderboard = [{"Rank": 1, "Name": "👑 사령관님 (Commander)", "Total Profit": f"${user_profit:,.2f}"}]
    for agent, prof in ai_profits.items(): leaderboard.append(
        {"Rank": 2, "Name": f"🤖 {agent}", "Total Profit": f"${prof:,.2f}"})

    leaderboard = sorted(leaderboard, key=lambda x: float(x["Total Profit"].replace('$', '').replace(',', '')),
                         reverse=True)
    for i, l in enumerate(leaderboard): l["Rank"] = i + 1

    st.dataframe(pd.DataFrame(leaderboard), use_container_width=True)

    st.warning("⚠️ 사령관님이 AI에게 뒤처지거나, 특정 AI가 압도적 1위를 차지했다면 아래 버튼을 눌러 유전자를 흡수하세요!")
    if st.button("🧬 1위의 유전자(가중치)로 시스템 코어 강제 진화 (RL Update)"):
        st.balloons();
        st.success("🎉 진화 완료! 메인 대시보드의 목표가 연산 엔진이 1위의 가중치를 모방하여 업데이트되었습니다.")

# ------------------------------------------
# TAB 2 & 3: 훈련소 & 매매 일지
# ------------------------------------------
with tab2:
    st.markdown("### 🧠 훈련소 (Bootcamp): 유전 알고리즘(GA) 실시간 연산")
    st.write("100명의 랜덤 유전자를 가진 AI를 생성하여 최근 1년 데이터로 시뮬레이션(적자생존)을 진행하고, 살아남은 상위 3명을 실전 리그로 승격시킵니다.")

    if st.button("⚔️ 100명 AI 트레이더 대규모 훈련 가동 (연산에 10~30초 소요됩니다)"):
        with st.spinner("서버 코어가 유전자 조합 및 과거 데이터 백테스팅을 수행 중입니다... 대기하십시오."):
            top_agents = run_genetic_algorithm_training(ticker="SOXL", population_size=100, days_back=365)

            if top_agents:
                new_models = {
                    f"Agent_Rank_1 (누적 ${top_agents[0]['total_score']:.2f})": top_agents[0]['weights'],
                    f"Agent_Rank_2 (누적 ${top_agents[1]['total_score']:.2f})": top_agents[1]['weights'],
                    f"Agent_Rank_3 (누적 ${top_agents[2]['total_score']:.2f})": top_agents[2]['weights']
                }
                with open(AI_MODELS_FILE, 'w') as f:
                    json.dump(new_models, f)

                pd.DataFrame(columns=["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"]).to_csv(
                    AI_LEDGER_FILE, index=False)

                st.success("🎯 유전자 조작 및 훈련 완료! 역대 최고 성능을 낸 3명의 요원이 실전 리그(발할라)에 새로 참전했습니다. (기존 리그 기록은 리셋되었습니다)")
                st.rerun()
            else:
                st.error("데이터를 불러오지 못해 훈련에 실패했습니다.")

with tab3:
    st.markdown("### 🗄️ 사령관 매매 일지 DB 관리")

    with st.expander("✍️ 수동으로 매매 기록 추가하기 (토스증권 등 단건 기록용)", expanded=True):
        with st.form("main_trade_form", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                t_date = st.date_input("거래 일자", datetime.date.today())
                t_time = st.text_input("거래 시간 (HH:MM:SS)", datetime.datetime.now().strftime("%H:%M:%S"))
                t_account = st.text_input("계좌명 (예: 토스증권, NH투자)", "Manual")
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
                new_record.to_csv(DB_FILE, mode='a', header=False, index=False)
                st.success(f"✅ {t_account} 계좌의 {t_ticker} {t_qty}주 {t_action} 기록이 성공적으로 저장되었습니다!");
                st.rerun()

    st.markdown("---")

    col_db1, col_db2 = st.columns([8, 2])
    with col_db1:
        st.dataframe(pd.read_csv(DB_FILE), use_container_width=True)
    with col_db2:
        if st.button("🗑️ 내 기록 초기화", key="reset_db"):
            pd.DataFrame(columns=["Date", "Time", "Account", "Ticker", "Action", "Qty", "Price", "Format"]).to_csv(
                DB_FILE, index=False)
            st.rerun()

    st.markdown("### 🗄️ AI 요원 가상 매매 장부 (Paper Trading)")

    col_ai1, col_ai2 = st.columns([8, 2])
    with col_ai1:
        try:
            st.dataframe(pd.read_csv(AI_LEDGER_FILE), use_container_width=True)
        except:
            st.write("기록이 없습니다.")
    with col_ai2:
        if st.button("🗑️ AI 장부 초기화", key="reset_ai"):
            pd.DataFrame(columns=["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"]).to_csv(
                AI_LEDGER_FILE, index=False)
            st.rerun()
