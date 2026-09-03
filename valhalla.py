import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from supabase import create_client
import os
import datetime
import io
import warnings
import re
import json
import random
from deep_translator import GoogleTranslator

warnings.filterwarnings('ignore')

# 🚨 스트림릿 절대 규칙: 페이지 설정은 무조건 최상단에 위치해야 합니다.
st.set_page_config(page_title="무한매수 전술 관제소 V3.1", layout="wide")

# ==========================================
# 1. 인공지능 퀀트 엔진 V3.0 (매수/매도 자율 진화형)
# ==========================================
class TitanRestV3Optimizer:
    def __init__(self, params=None):
        self.params = np.array(params) if params is not None else np.array([10.0, 2.0, -3.0, 1.5, 5.0, 1.0, 1.0, 40.0])
        self.w_split = max(20.0, min(40.0, self.params[7])) 

    def get_action_params(self, X):
        w_base, w_ma, w_rsi, w_vol, w_risk, w_buy_dist, w_buy_mul, _ = self.params
        
        ma_norm = np.clip(X[0] / 10.0, -1.0, 1.0)
        rsi_norm = (X[1] - 50.0) / 50.0
        vol_norm = np.clip(X[3] / 5.0, 0.0, 2.0)
        
        target_r = w_base + (w_ma * ma_norm) + (w_rsi * rsi_norm) + (w_vol * vol_norm) - (w_risk * 0.5)
        target_r = np.clip(target_r, 3.0, 30.0)
        prob = np.clip(95.0 - (target_r * 1.2) + w_risk, 10.0, 99.9)
        est_days = max(1.0, target_r * 1.1 - (w_risk * 0.1))
        
        fear_index = (-rsi_norm) + (-ma_norm) 
        buy_multiplier = max(0.0, 1.0 + (fear_index * w_buy_mul))
        buy_discount_pct = max(0.0, w_buy_dist * (1.0 + vol_norm))
        
        return {
            'optimal_r': round(target_r, 2),
            'success_probability': round(prob, 1),
            'est_days': round(est_days, 1),
            'buy_multiplier': round(buy_multiplier, 2),
            'buy_discount_pct': round(buy_discount_pct, 2),
            'split_ratio': int(self.w_split)
        }

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
        pd.DataFrame(columns=["Date", "Time", "Account", "Ticker", "Action", "Qty", "Price", "Format"]).to_csv(DB_FILE, index=False)
    if not os.path.exists(AI_MODELS_FILE):
        initial_models = {
            "Agent_Alpha (초기화)": [15.0, 2.0, -3.0, 1.0, 8.0, 2.0, 1.5, 40.0],
            "Agent_Beta (초기화)": [5.0, 0.5, -1.0, 0.5, 2.0, 1.0, 0.5, 30.0],
            "Agent_Gamma (초기화)": [10.0, 1.0, -2.0, 1.5, 5.0, 3.0, 2.0, 20.0]
        }
        with open(AI_MODELS_FILE, 'w') as f: json.dump(initial_models, f)
    if not os.path.exists(AI_LEDGER_FILE):
        pd.DataFrame(columns=["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"]).to_csv(AI_LEDGER_FILE, index=False)

if not supabase: init_local_files()

def read_db(table_name):
    if supabase:
        res = supabase.table(table_name).select("*").execute()
        df = pd.DataFrame(res.data)
        if df.empty:
            if table_name == "trade_journal": return pd.DataFrame(columns=["Date", "Time", "Account", "Ticker", "Action", "Qty", "Price", "Format"])
            else: return pd.DataFrame(columns=["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"])
        if 'id' in df.columns: df = df.drop(columns=['id'])
        return df
    else:
        file_name = DB_FILE if table_name == "trade_journal" else AI_LEDGER_FILE
        try: return pd.read_csv(file_name)
        except: return pd.DataFrame()

def overwrite_db(table_name, df_new):
    if supabase:
        supabase.table(table_name).delete().neq('id', 0).execute() 
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

def load_ai_models():
    if supabase:
        try:
            res = supabase.table("valhalla_ai_models").select("*").execute()
            if res.data:
                models = {}
                for row in res.data: models[row['agent_name']] = json.loads(row['genes'])
                return models
        except: pass
    try:
        with open(AI_MODELS_FILE, 'r') as f: return json.load(f)
    except: return {}

def save_ai_models(models_dict):
    if supabase:
        try:
            supabase.table("valhalla_ai_models").delete().neq("agent_name", "dummy").execute()
            records = [{"agent_name": k, "genes": json.dumps(v)} for k, v in models_dict.items()]
            supabase.table("valhalla_ai_models").insert(records).execute()
            return True
        except: return False
    else:
        with open(AI_MODELS_FILE, 'w') as f: json.dump(models_dict, f)
        return True

# ==========================================
# 3. 실시간 차트 & 환율 & 데이터 로더
# ==========================================
@st.cache_data(ttl=60)
def get_exchange_rate():
    try:
        df = yf.download("USDKRW=X", period="1d", progress=False)
        if not df.empty: return float(df['Close'].iloc[-1])
        return 1350.0 
    except:
        return 1350.0 

@st.cache_data(ttl=60)
def get_realtime_data(ticker):
    end_date = datetime.date.today() + datetime.timedelta(days=1)
    start_date = end_date - datetime.timedelta(days=365)
    df = yf.download(ticker, start=start_date, end=end_date, progress=False)
    
    if df.empty: return None, None, None
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
        
    close, volume = df['Close'], df['Volume']
    current_price = close.iloc[-1]
    
    ma20 = close.rolling(window=20).mean()
    x1 = ((current_price - ma20.iloc[-1]) / ma20.iloc[-1]) * 100.0 if not pd.isna(ma20.iloc[-1]) else 0.0
    
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    x2 = 100.0 - (100.0 / (1.0 + rs)).iloc[-1] if not pd.isna(rs.iloc[-1]) else 50.0
    
    x3 = (volume.iloc[-1] / volume.rolling(window=20).mean().iloc[-1]) if not pd.isna(volume.rolling(window=20).mean().iloc[-1]) else 1.0
    vol = close.pct_change().rolling(window=20).std().iloc[-1] * 100.0 if not pd.isna(close.pct_change().rolling(window=20).std().iloc[-1]) else 2.0
    
    # 💡 NaN 에러 방지 안전장치
    state_vals = [round(x1, 2), round(x2, 2), round(x3, 2), round(vol, 2)]
    clean_state = [0.0 if pd.isna(v) or np.isinf(v) else v for v in state_vals]
    
    return df, clean_state, round(current_price, 2)

def analyze_sentiment_with_reason(text):
    pos_words = ['surge', 'beat', 'up', 'buy', 'rally', 'strong', 'growth', 'gain', 'jump', 'upgrade', 'record', 'high', 'boost', 'bullish']
    neg_words = ['miss', 'down', 'drop', 'sell', 'weak', 'fall', 'slump', 'lawsuit', 'cut', 'downgrade', 'low', 'loss', 'plunge', 'delay', 'sink', 'unwind']
    text_lower = text.lower()
    
    found_pos = [w for w in pos_words if re.search(rf(r'\b{w}\b'), text_lower)]
    found_neg = [w for w in neg_words if re.search(rf(r'\b{w}\b'), text_lower)]
    
    score = len(found_pos) - len(found_neg)
    reason = []
    if found_pos: reason.append(f"🟢 호재: {', '.join(found_pos)}")
    if found_neg: reason.append(f"🔴 악재: {', '.join(found_neg)}")
    reason_str = " | ".join(reason) if reason else "매칭된 특이 키워드 없음 (중립)"
    
    if score > 0: return "🟢 호재 (상승 기대)", reason_str
    elif score < 0: return "🔴 악재 (하락 경계)", reason_str
    else: return "⚪ 관망 (중립)", reason_str

def highlight_keywords(text):
    pos_words = ['surge', 'beat', 'up', 'buy', 'rally', 'strong', 'growth', 'gain', 'jump', 'upgrade', 'record', 'high', 'boost', 'bullish']
    neg_words = ['miss', 'down', 'drop', 'sell', 'weak', 'fall', 'slump', 'lawsuit', 'cut', 'downgrade', 'low', 'loss', 'plunge', 'delay', 'sink', 'unwind']
    highlighted = text
    for w in pos_words:
        highlighted = re.sub(rf(r'\b({w})\b'), r'<span style="background-color: #a8f0c6; color: black; font-weight: bold; padding: 2px 4px; border-radius: 3px;">\1</span>', highlighted, flags=re.IGNORECASE)
    for w in neg_words:
        highlighted = re.sub(rf(r'\b({w})\b'), r'<span style="background-color: #ffb3b3; color: black; font-weight: bold; padding: 2px 4px; border-radius: 3px;">\1</span>', highlighted, flags=re.IGNORECASE)
    return highlighted

def translate_to_korean(text):
    try: return GoogleTranslator(source='en', target='ko').translate(text)
    except: return "번역 서버 응답 지연 (영문 원본 참조)"

@st.cache_data(ttl=1800)
def get_soxl_radar():
    tickers = ['NVDA', 'AMD', 'AVGO', 'TSM', 'QCOM', 'INTC', 'ASML', 'TXN', 'AMAT', 'MU']
    weight_map = {'NVDA': 11.5, 'AVGO': 8.5, 'AMD': 7.5, 'QCOM': 6.0, 'TSM': 5.0, 'AMAT': 4.5, 'MU': 4.5, 'INTC': 4.0, 'TXN': 4.0, 'ASML': 4.0}
    data = []
    try:
        df_prices = yf.download(tickers, period="5d", progress=False)
        closes = df_prices['Close'] if isinstance(df_prices.columns, pd.MultiIndex) else df_prices
    except: return []

    for t in tickers:
        try:
            if t not in closes.columns: continue
            valid_closes = closes[t].dropna()
            if len(valid_closes) < 2: continue
            curr_price = float(valid_closes.iloc[-1])
            change = (curr_price - float(valid_closes.iloc[-2])) / float(valid_closes.iloc[-2]) * 100
            
            stock = yf.Ticker(t)
            news = stock.news
            news_titles = []
            if news:
                for n in news[:3]:
                    if isinstance(n, dict):
                        title = n.get('title') or (n.get('content', {}).get('title', '') if isinstance(n.get('content'), dict) else '')
                        if title: news_titles.append(title)
            if news_titles:
                sentiment, reason_str = analyze_sentiment_with_reason(" ".join(news_titles))
                top_headline = news_titles[0]
            else:
                sentiment, top_headline, reason_str = "⚪ 뉴스 없음", "관련 기사가 없습니다.", ""
                
            data.append({"종목명": t, "비중(추정)": f"{weight_map.get(t, 0.0)}%", "현재가": f"${curr_price:.2f}", "변동률": f"{change:+.2f}%",
                         "뉴스 센티먼트 (예측)": sentiment, "최신 글로벌 헤드라인": top_headline, "모든헤드라인": news_titles, "판단근거": reason_str})
        except: continue
    return data

@st.cache_data(ttl=1800)
def get_macro_radar():
    macro_map = {'SPY': 'S&P 500 (글로벌 증시 전반)', 'TLT': '미국 장기채 ETF (금리 동향)', 'USO': '원유 ETF (유가 및 지정학 리스크)'}
    data = []
    try:
        df_prices = yf.download(list(macro_map.keys()), period="5d", progress=False)
        closes = df_prices['Close'] if isinstance(df_prices.columns, pd.MultiIndex) else df_prices
    except: return []

    for t, desc in macro_map.items():
        try:
            if t not in closes.columns: continue
            valid_closes = closes[t].dropna()
            if len(valid_closes) < 2: continue
            curr_price = float(valid_closes.iloc[-1])
            change = (curr_price - float(valid_closes.iloc[-2])) / float(valid_closes.iloc[-2]) * 100
            
            stock = yf.Ticker(t)
            news = stock.news
            news_titles = []
            if news:
                for n in news[:3]:
                    if isinstance(n, dict):
                        title = n.get('title') or (n.get('content', {}).get('title', '') if isinstance(n.get('content'), dict) else '')
                        if title: news_titles.append(title)
            if news_titles:
                sentiment, reason_str = analyze_sentiment_with_reason(" ".join(news_titles))
                top_headline = news_titles[0]
            else:
                sentiment, top_headline, reason_str = "⚪ 뉴스 없음", "관련 기사가 없습니다.", ""
                
            data.append({"거시경제 지표": desc, "현재가": f"${curr_price:.2f}", "변동률": f"{change:+.2f}%",
                         "매크로 센티먼트": sentiment, "핵심 글로벌 헤드라인": top_headline, "모든헤드라인": news_titles, "판단근거": reason_str})
        except: continue
    return data

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
        except: continue
    if df is None or df.empty: return pd.DataFrame()
    format_type = None; start_idx = 0
    for idx, row in df.iterrows():
        first_cell = str(row[0]).strip()
        if first_cell == '주문일자': format_type = '2row'; start_idx = idx + 2; break
        elif first_cell == '거래일자': format_type = '3row'; start_idx = idx + 3; break
    if format_type is None: return pd.DataFrame()
    data_rows = df.iloc[start_idx:].reset_index(drop=True)
    trades = []
    def format_date(raw_date):
        d = str(raw_date).strip().replace('/', '-').replace('.', '-')
        if len(d) == 8 and '-' not in d: d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        return d
    if format_type == '2row':
        for i in range(0, len(data_rows)-1, 2):
            r1, r2 = data_rows.iloc[i], data_rows.iloc[i+1]
            if pd.isna(r1[0]) or str(r1[0]).strip() in ['', 'nan']: continue
            date_str = format_date(r1[0])
            time_str = str(r2[0]).strip() if pd.notna(r2[0]) else f"00:00:{i%60:02d}"
            ticker = str(r1[2]).replace("'", "").strip()
            action = "Buy" if "매수" in str(r2[3]) else "Sell"
            try: qty = float(str(r1[6]).replace(',', '')); price = float(str(r2[6]).replace(',', ''))
            except: continue
            if qty > 0: trades.append({"Date": date_str, "Time": time_str, "Account": account_tag, "Ticker": ticker, "Action": action, "Qty": qty, "Price": price, "Format": "2row"})
    elif format_type == '3row':
        for i in range(0, len(data_rows)-2, 3):
            r1, r2 = data_rows.iloc[i], data_rows.iloc[i+1]
            if pd.isna(r1[0]) or str(r1[0]).strip() in ['', 'nan']: continue
            if str(r1[1]).strip() != '매매': continue
            date_str = format_date(r1[0])
            time_str = str(r2[10]).strip() if len(r2)>10 and pd.notna(r2[10]) else f"00:00:{i%60:02d}"
            ticker = str(r2[0]).replace("'", "").strip()
            action = "Buy" if "매수" in str(r2[1]) else "Sell"
            try: qty = float(str(r1[3]).replace(',', '')); price = float(str(r2[2]).replace(',', ''))
            except: continue
            if qty > 0: trades.append({"Date": date_str, "Time": time_str, "Account": account_tag, "Ticker": ticker, "Action": action, "Qty": qty, "Price": price, "Format": "3row"})
    return pd.DataFrame(trades)

# ------------------------------------------
# 4. 유전 알고리즘 훈련소 (V3.1 확장판 + 몬테카를로 시뮬레이션 탑재)
# ------------------------------------------
def run_genetic_algorithm_training(ticker="SOXL", population_size=1000, days_back=365, mc_future_days=100):
    end_date = datetime.date.today()
    start_date = end_date - datetime.timedelta(days=days_back)
    df = yf.download(ticker, start=start_date, end=end_date, progress=False)
    
    if df.empty: return None
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)
    
    returns = df['Close'].pct_change().dropna()
    mu = returns.mean()
    sigma = returns.std()
    
    if pd.isna(mu) or pd.isna(sigma): return None
    
    last_price = float(df['Close'].iloc[-1])
    last_vol = float(df['Volume'].iloc[-1])
    
    mc_closes = [last_price]
    for _ in range(mc_future_days):
        shock = np.random.normal(0, sigma * 1.5) 
        next_price = mc_closes[-1] * np.exp((mu - 0.5 * sigma**2) + shock)
        mc_closes.append(max(next_price, 1.0)) 
        
    future_dates = pd.date_range(start=df.index[-1] + datetime.timedelta(days=1), periods=mc_future_days)
    mc_df = pd.DataFrame({
        'Close': mc_closes[1:],
        'High': [p * random.uniform(1.0, 1.08) for p in mc_closes[1:]],
        'Low': [p * random.uniform(0.92, 1.0) for p in mc_closes[1:]],
        'Volume': [last_vol * random.uniform(0.5, 2.0) for _ in range(mc_future_days)]
    }, index=future_dates)
    
    combined_df = pd.concat([df[['Close', 'High', 'Low', 'Volume']], mc_df])
    
    combined_df['MA20'] = combined_df['Close'].rolling(window=20).mean()
    combined_df['MA20_diff'] = ((combined_df['Close'] - combined_df['MA20']) / combined_df['MA20']) * 100
    delta = combined_df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    combined_df['RSI'] = 100 - (100 / (1 + rs))
    combined_df['Vol_Ratio'] = combined_df['Volume'] / combined_df['Volume'].rolling(window=20).mean()
    combined_df['Volatility'] = combined_df['Close'].pct_change().rolling(window=20).std() * 100
    combined_df.dropna(inplace=True)
    
    agents = []
    for i in range(population_size):
        w_base = random.uniform(5.0, 20.0)
        w_ma = random.uniform(-5.0, 5.0)
        w_rsi = random.uniform(-5.0, 5.0)
        w_vol = random.uniform(-5.0, 5.0)
        w_risk = random.uniform(0.0, 10.0)
        w_buy_dist = random.uniform(0.0, 5.0) 
        w_buy_mul = random.uniform(-2.0, 5.0) 
        w_split = random.uniform(20.0, 40.0)  
        weights = [w_base, w_ma, w_rsi, w_vol, w_risk, w_buy_dist, w_buy_mul, w_split]
        agents.append({"id": f"Agent_Gen_{i}", "weights": weights, "total_score": 0.0})
    
    for agent in agents:
        engine = TitanRestV3Optimizer(params=agent['weights'])
        total_capital = 10000.0 
        remaining_capital = total_capital
        base_buy_amount = total_capital / engine.w_split
        
        current_qty = 0.0
        total_cost = 0.0
        profit = 0.0
        
        for i in range(len(combined_df)):
            row = combined_df.iloc[i]
            p_close, p_high, p_low = float(row['Close']), float(row['High']), float(row['Low'])
            X = [float(row['MA20_diff']), float(row['RSI']), float(row['Vol_Ratio']), float(row['Volatility'])]
            
            avg_price = total_cost / current_qty if current_qty > 0 else 0
            pred = engine.get_action_params(X)
            
            # NaN 발생 시 기본값 방어
            opt_r = pred['optimal_r']
            if pd.isna(opt_r): opt_r = 10.0
            
            target_price = avg_price * (1 + (opt_r / 100.0))
            if current_qty > 0 and p_high >= target_price:
                profit += (target_price - avg_price) * current_qty
                remaining_capital += target_price * current_qty
                current_qty, total_cost = 0.0, 0.0
            else:
                buy_target_price = p_close * (1 - (pred['buy_discount_pct'] / 100.0))
                if p_low <= buy_target_price and remaining_capital > 0:
                    attempt_amount = base_buy_amount * pred['buy_multiplier']
                    actual_amount = min(attempt_amount, remaining_capital)
                    if actual_amount > 0:
                        trade_qty = actual_amount / buy_target_price
                        current_qty += trade_qty
                        total_cost += actual_amount
                        remaining_capital -= actual_amount
        
        unrealized_profit = (current_qty * float(combined_df['Close'].iloc[-1])) - total_cost
        agent['total_score'] = profit + unrealized_profit

    ranked_agents = sorted(agents, key=lambda x: x['total_score'], reverse=True)
    return ranked_agents[:3]

# ==========================================
# 5. 웹 UI 구현 (Streamlit)
# ==========================================
st.title("🚀 무한매수 전술 관제소 (Project Valhalla V3.1)")

if supabase: st.sidebar.success("🟢 클라우드 DB 연결됨")
else: st.sidebar.warning("🟡 로컬 DB 사용 중")

krw_rate = get_exchange_rate()
st.sidebar.info(f"💱 **실시간 환율:** 1 USD = {krw_rate:,.2f} KRW")

tab1, tab5, tab4, tab2, tab3 = st.tabs(["📊 전술 관제", "📡 SOXL 생태계", "⚔️ 발할라 실전 리그", "🧠 훈련소 (진화)", "🗄️ 매매 일지"])

# ------------------------------------------
# 사이드바
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
            df_combined['group_id'] = df_combined['Date'].astype(str) + "_" + df_combined['Account'].astype(str) + "_" + df_combined['Ticker'].astype(str) + "_" + df_combined['Action'].astype(str)
            
            groups_with_2row = df_combined[df_combined['Format'] == '2row']['group_id'].unique()
            mask = ~((df_combined['group_id'].isin(groups_with_2row)) & (df_combined['Format'] == '3row'))
            df_resolved = df_combined[mask].drop(columns=['group_id'])
            df_final = df_resolved.drop_duplicates(subset=["Date", "Time", "Account", "Ticker", "Action", "Qty", "Price"]).sort_values(["Date", "Time"])
            
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
            with col_opt1: cycle_start = st.date_input(f"📅 사이클 시작일", value=datetime.date(2026, 4, 20))
            with col_opt2: selected_accounts = st.multiselect(f"🏷️ 추적 계좌", options=available_accounts, default=available_accounts)
                
            df_chart, state_vector, current_price = get_realtime_data(view_ticker)
            
            if df_chart is not None:
                col_chart, col_ai = st.columns([7, 3])
                
                trades = df_journal[(df_journal['Ticker'] == view_ticker) & 
                                    (df_journal['Date'] >= cycle_start.strftime("%Y-%m-%d")) & 
                                    (df_journal['Account'].isin(selected_accounts))].sort_values(["Date", "Time"])
                
                current_qty, total_cost, avg_price = 0.0, 0.0, 0.0
                for _, row in trades.iterrows():
                    qty, price = float(row['Qty']), float(row['Price'])
                    if row['Action'] == 'Buy': 
                        current_qty += qty
                        total_cost += qty * price
                        avg_price = total_cost / current_qty if current_qty > 0 else 0
                    elif row['Action'] == 'Sell': 
                        current_qty, total_cost, avg_price = 0.0, 0.0, 0.0
                
                with col_ai:
                    st.markdown(f"### 🛡️ 사령관의 포지션")
                    st.info(f"**총 보유 수량:** {int(current_qty)} 주")
                    user_target_pct = st.slider("🎯 목표 수익률 조절 (%)", 1.0, 50.0, 13.0, 0.5)
                    user_target_price = avg_price * (1 + user_target_pct / 100) if avg_price > 0 else 0
                    
                    if current_qty > 0:
                        st.metric("💰 나의 정확한 평단가", f"${avg_price:,.4f}")
                        st.metric(f"🎯 나의 목표가 (+{user_target_pct}%)", f"${user_target_price:,.2f}")
                        
                        st.markdown("---")
                        st.markdown("### 🧮 작전 수익 시뮬레이터")
                        expected_profit = (user_target_price - avg_price) * current_qty
                        expected_profit_krw = expected_profit * krw_rate
                        st.success(f"**현재 설정(+{user_target_pct}%) 매도 시:**\n\n예상 수익금 **${expected_profit:,.2f}**\n\n(약 **₩{expected_profit_krw:,.0f}**)")
                        
                        st.markdown("👇 **역산 계산기 (원하는 수익금 입력)**")
                        currency_choice = st.radio("입력 통화 기준", ["미국 달러 (USD)", "대한민국 원 (KRW)"], horizontal=True)
                        
                        if "KRW" in currency_choice:
                            desired_profit_input = st.number_input("목표 수익금 (₩)", min_value=1000, value=100000, step=10000)
                            desired_profit_usd = desired_profit_input / krw_rate
                        else:
                            desired_profit_input = st.number_input("목표 수익금 ($)", min_value=1.0, value=100.0, step=10.0)
                            desired_profit_usd = desired_profit_input
                            
                        req_price = avg_price + (desired_profit_usd / current_qty) if current_qty > 0 else 0
                        req_pct = ((req_price / avg_price) - 1.0) * 100.0 if avg_price > 0 else 0
                        
                        if "KRW" in currency_choice:
                            st.info(f"**₩{desired_profit_input:,.0f}** (약 ${desired_profit_usd:,.2f}) 벌려면\n\n➔ **+{req_pct:.2f}%** 에 매도 (목표가 ${req_price:.2f})")
                        else:
                            desired_profit_krw = desired_profit_usd * krw_rate
                            st.info(f"**${desired_profit_input:,.2f}** (약 ₩{desired_profit_krw:,.0f}) 벌려면\n\n➔ **+{req_pct:.2f}%** 에 매도 (목표가 ${req_price:.2f})")
                    else:
                        st.write("현재 진행 중인 매수 사이클이 없습니다.")
                    
                    st.markdown("---")
                    st.markdown(f"### 🧠 정예 요원별 V3.0 진단")
                    st.info(f"현재가: ${current_price:,.2f}")
                    
                    ai_models = load_ai_models()
                    if ai_models:
                        for agent_name, weights in ai_models.items():
                            if len(weights) < 8: weights = weights + [1.0, 1.0, 40.0] 
                            agent_engine = TitanRestV3Optimizer(params=weights)
                            prediction = agent_engine.get_action_params(state_vector)
                            
                            # 💡 nan 방어 장치 적용
                            opt_r = prediction['optimal_r']
                            if pd.isna(opt_r): opt_r = 10.0
                            
                            base_price = avg_price if current_qty > 0 else current_price
                            agent_target = base_price * (1 + opt_r / 100)
                            
                            with st.expander(f"🤖 {agent_name}의 전술", expanded=True):
                                st.markdown("##### 🚀 [매도 플랜]")
                                st.write(f"**목표가 (+{opt_r}%):** ${agent_target:,.2f}")
                                st.write(f"**승률 예측:** {prediction['success_probability']}%")
                                
                                st.markdown("##### 🛒 [매수 플랜]")
                                st.write(f"**가드레일 분할:** {prediction['split_ratio']}분할 셋팅")
                                st.write(f"**금일 매수량:** 1회차 예산의 **{prediction['buy_multiplier']}배** 투입")
                                st.write(f"**권장 진입가:** 현재가 대비 **-{prediction['buy_discount_pct']}%** (${current_price * (1 - prediction['buy_discount_pct']/100):.2f})")
                                
                                with st.popover(f"💡 {agent_name} 알고리즘 해설 보기"):
                                    w_base, w_ma, w_rsi, w_vol, w_risk, w_buy_dist, w_buy_mul, w_split = weights
                                    st.markdown(f'''
                                    * **기본 탐욕 지수:** {w_base:+.2f}%
                                    * **리스크 회피 성향:** {w_risk:+.2f}
                                    * **매수 배수 가중치:** {w_buy_mul:+.2f}
                                    * **매수 할인율 가중치:** {w_buy_dist:+.2f}
                                    * **예산 가드레일:** {int(w_split)}분할 통제중
                                    ''')
                    else:
                        st.warning("훈련소에서 요원을 먼저 훈련시켜주세요.")
                
                with col_chart:
                    time_correction = st.checkbox("🇺🇸 미국 주식 시차 보정 (한국 새벽 거래만 -1일 자동 적용)", value=True)
                    fig = go.Figure(data=[go.Candlestick(x=df_chart.index, open=df_chart['Open'], high=df_chart['High'], low=df_chart['Low'], close=df_chart['Close'], name="Candle")])
                    
                    if current_qty > 0: 
                        fig.add_hline(y=avg_price, line_dash="dot", line_color="yellow", annotation_text=f"나의 평단가 (${avg_price:.4f})", annotation_position="bottom right")
                        fig.add_hline(y=user_target_price, line_dash="solid", line_color="magenta", annotation_text=f"나의 목표가 (+{user_target_pct}%, ${user_target_price:.2f})", annotation_position="top right")
                    
                    buys = trades[trades['Action'] == 'Buy'].copy()
                    if not buys.empty:
                        if time_correction:
                            is_dawn = pd.to_datetime(buys['Time'], format='%H:%M:%S', errors='coerce').dt.hour < 9
                            buys['Plot_Date'] = pd.to_datetime(buys['Date']) - pd.to_timedelta(is_dawn.fillna(False).astype(int), unit='d')
                        else:
                            buys['Plot_Date'] = pd.to_datetime(buys['Date'])
                        fig.add_trace(go.Scatter(x=buys['Plot_Date'], y=buys['Price'], mode='markers', name='나의 매수',
                                                 marker=dict(symbol='triangle-up', color='lime', size=14, line=dict(width=1, color='black')),
                                                 text=buys['Qty'].astype(str) + "주 매수", hoverinfo="text+y+x"))

                    sells = trades[trades['Action'] == 'Sell'].copy()
                    if not sells.empty:
                        if time_correction:
                            is_dawn = pd.to_datetime(sells['Time'], format='%H:%M:%S', errors='coerce').dt.hour < 9
                            sells['Plot_Date'] = pd.to_datetime(sells['Date']) - pd.to_timedelta(is_dawn.fillna(False).astype(int), unit='d')
                        else:
                            sells['Plot_Date'] = pd.to_datetime(sells['Date'])
                        fig.add_trace(go.Scatter(x=sells['Plot_Date'], y=sells['Price'], mode='markers', name='나의 매도',
                                                 marker=dict(symbol='triangle-down', color='red', size=14, line=dict(width=1, color='black')),
                                                 text=sells['Qty'].astype(str) + "주 매도", hoverinfo="text+y+x"))
                    
                    fig.update_xaxes(rangebreaks=[dict(bounds=["sat", "mon"])]); fig.update_layout(title=f"{view_ticker} 차트 및 전술 오버레이", yaxis_title="Price (USD)", template="plotly_dark", height=600)
                    st.plotly_chart(fig, use_container_width=True)

# ------------------------------------------
# TAB 5: 📡 SOXL 생태계 및 매크로 레이더
# ------------------------------------------
with tab5:
    st.markdown("## 📡 SOXL 생태계 레이더 (Semiconductor Top 10)")
    st.write("SOXL을 견인하는 핵심 10대 기업과 거시 경제(SPY, TLT, USO)의 글로벌 뉴스를 AI가 스캔하여 호재와 악재를 예측합니다.")
    
    if st.button("🔄 실시간 생태계 및 매크로 스캔 가동"):
        with st.spinner("글로벌 뉴스 수집 및 AI 딥 스캔 작동 중... (약 10~20초 소요)"):
            radar_data = get_soxl_radar()
            macro_data = get_macro_radar()
            
            if radar_data:
                df_radar = pd.DataFrame(radar_data)
                st.dataframe(df_radar.drop(columns=["판단근거", "모든헤드라인"]), use_container_width=True)
                st.markdown("### 🧠 AI 반도체 뉴스 정밀 분석 (Deep Scan)")
                for item in radar_data:
                    if "뉴스 없음" in item["뉴스 센티먼트 (예측)"] or "분석 불가" in item["뉴스 센티먼트 (예측)"]: continue
                    with st.expander(f"{item['뉴스 센티먼트 (예측)']} | {item['종목명']} ({item['변동률']})"):
                        st.markdown(f"**💡 전체 종합 판단 근거:** `{item['판단근거']}`")
                        st.markdown("---")
                        for idx, title in enumerate(item['모든헤드라인']):
                            st.markdown(f"**📰 원문:** {highlight_keywords(title)}", unsafe_allow_html=True)
                            st.info(f"**🇰🇷 번역:** {translate_to_korean(title)}")
            
            st.markdown("---")
            st.markdown("## 🌎 글로벌 매크로 환경 레이더")
            if macro_data:
                df_macro = pd.DataFrame(macro_data)
                st.dataframe(df_macro.drop(columns=["판단근거", "모든헤드라인"]), use_container_width=True)
                st.markdown("### 🧠 AI 매크로 뉴스 정밀 분석 (Deep Scan)")
                for item in macro_data:
                    if "뉴스 없음" in item["매크로 센티먼트"] or "분석 불가" in item["매크로 센티먼트"]: continue
                    with st.expander(f"{item['매크로 센티먼트']} | {item['거시경제 지표']} ({item['변동률']})"):
                        st.markdown(f"**💡 전체 종합 판단 근거:** `{item['판단근거']}`")
                        st.markdown("---")
                        for idx, title in enumerate(item['모든헤드라인']):
                            st.markdown(f"**📰 원문:** {highlight_keywords(title)}", unsafe_allow_html=True)
                            st.info(f"**🇰🇷 번역:** {translate_to_korean(title)}")

# ------------------------------------------
# TAB 4: ⚔️ 발할라 실전 리그
# ------------------------------------------
with tab4:
    st.markdown("## ⚔️ 발할라 실전 리그 (Valhalla Live League)")
    ai_models = load_ai_models()
    
    col1, col2 = st.columns([1, 1])
    with col1:
        st.info("🧬 **현재 참전 중인 정예 AI 요원**")
        if ai_models:
            for agent in ai_models.keys(): st.write(f"- **{agent}** (V3.1 탑재 완료)")
        else: st.write("아직 선발된 AI 요원이 없습니다.")
    
    with col2:
        st.info("🚨 **일일 가상 매매(Paper Trading) 시동**")
        sim_ticker = st.selectbox("종목 선택", ["SOXL", "FNGU", "CURE"])
        if st.button(f"📅 오늘({datetime.date.today()})의 AI 가상 매매 실행"):
            df_chart, state_vector, current_price = get_realtime_data(sim_ticker)
            df_ai = read_db("ai_ledger")
            new_records = []
            
            for agent, weights in ai_models.items():
                if len(weights) < 8: weights = weights + [1.0, 1.0, 40.0]
                agent_trades = df_ai[(df_ai['Agent'] == agent) & (df_ai['Ticker'] == sim_ticker)] if not df_ai.empty else pd.DataFrame()
                current_qty, total_cost = 0.0, 0.0
                
                for _, row in agent_trades.iterrows():
                    qty, price = float(row['Qty']), float(row['Price'])
                    if row['Action'] == 'Buy':
                        current_qty += qty; total_cost += qty * price
                    elif row['Action'] == 'Sell':
                        current_qty, total_cost = 0.0, 0.0
                
                avg_price = total_cost / current_qty if current_qty > 0 else 0
                agent_engine = TitanRestV3Optimizer(params=weights)
                prediction = agent_engine.get_action_params(state_vector)
                
                opt_r = prediction['optimal_r']
                if pd.isna(opt_r): opt_r = 10.0
                
                target_price = avg_price * (1 + (opt_r / 100.0))
                action, trade_qty, profit = "Hold", 0.0, 0.0
                
                if current_qty > 0 and current_price >= target_price:
                    action, trade_qty, profit = "Sell", round(current_qty, 4), (current_price - avg_price) * current_qty
                else:
                    base_budget = 10000 / prediction['split_ratio']
                    buy_budget = base_budget * prediction['buy_multiplier']
                    if buy_budget > 0:
                        action, trade_qty, profit = "Buy", round(buy_budget / current_price, 4), 0.0
                
                if action != "Hold":
                    new_records.append({"Date": datetime.date.today().strftime("%Y-%m-%d"), "Agent": agent, "Ticker": sim_ticker, "Action": action, "Qty": trade_qty, "Price": current_price, "Capital": 10000, "Profit": round(profit, 2)})
            
            if new_records:
                append_db("ai_ledger", pd.DataFrame(new_records))
                st.toast("요원들이 오늘의 V3.1 매매를 완료했습니다!")
                st.rerun()

    st.markdown("---")
    st.markdown("### 🏆 실시간 리더보드 (누적 수익 경쟁)")
    try: user_profit = 2491.59 
    except: user_profit = 0.0
    df_ai = read_db("ai_ledger")
    ai_profits = df_ai.groupby('Agent')['Profit'].sum().to_dict() if not df_ai.empty else {agent: 0 for agent in ai_models.keys()}
    
    leaderboard = [{"Rank": 1, "Name": "👑 사령관님 (Commander)", "Total Profit": f"${user_profit:,.2f}"}]
    for agent, prof in ai_profits.items(): leaderboard.append({"Rank": 2, "Name": f"🤖 {agent}", "Total Profit": f"${prof:,.2f}"})
    leaderboard = sorted(leaderboard, key=lambda x: float(x["Total Profit"].replace('$','').replace(',','')), reverse=True)
    for i, l in enumerate(leaderboard): l["Rank"] = i + 1
    st.dataframe(pd.DataFrame(leaderboard), use_container_width=True)

# ------------------------------------------
# TAB 2 & 3: 훈련소 & 매매 일지 
# ------------------------------------------
with tab2:
    st.markdown("### 🧠 훈련소 (Bootcamp): V3.1 유전 알고리즘 대규모 연산")
    st.write("💡 **[인간의 가드레일 + AI의 자율성]** 1,000명의 AI가 예산 통제 속에서 적자생존 경쟁을 벌입니다.")
    
    st.markdown("#### ⚙️ 훈련 시뮬레이터 환경 설정")
    col_t1, col_t2 = st.columns(2)
    with col_t1:
        user_days_back = st.slider("📅 과거 실제 차트 학습 기간 (일)", min_value=365, max_value=1825, value=730, step=365, help="과거 며칠 동안의 실제 데이터를 학습할지 결정합니다. (기본 2년=730일)")
    with col_t2:
        user_mc_days = st.slider("🎲 몬테카를로 가상 미래 생성 (일)", min_value=50, max_value=500, value=200, step=50, help="학습 데이터 이후 이어질 예측 불허의 가상 미래 장세를 며칠이나 생성할지 결정합니다.")
    
    st.markdown("---")
    
    if st.button("⚔️ 1,000명 V3.1 AI 트레이더 대규모 훈련 가동"):
        with st.spinner(f"⚠️ 슈퍼컴퓨팅 가동 중! (과거 {user_days_back}일 + 무작위 미래 {user_mc_days}일의 험난한 여정... 약 1~2분 소요)"):
            top_agents = run_genetic_algorithm_training(
                ticker="SOXL", 
                population_size=1000, 
                days_back=user_days_back, 
                mc_future_days=user_mc_days
            )
            
            if top_agents:
                new_models = {
                    f"Agent_Rank_1 (누적 ${top_agents[0]['total_score']:.2f})": top_agents[0]['weights'],
                    f"Agent_Rank_2 (누적 ${top_agents[1]['total_score']:.2f})": top_agents[1]['weights'],
                    f"Agent_Rank_3 (누적 ${top_agents[2]['total_score']:.2f})": top_agents[2]['weights']
                }
                
                if save_ai_models(new_models):
                    overwrite_db("ai_ledger", pd.DataFrame(columns=["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"]))
                    st.success(f"🎯 훈련 완료! (총 {user_days_back + user_mc_days}일의 시뮬레이션 생존자 선발) 압도적 1~3위 요원이 실전 리그로 배정되었습니다.")
                else: 
                    st.error("DB 저장에 실패했습니다.")
                st.rerun()

with tab3:
    st.markdown("### 🗄️ 사령관 매매 일지 DB 관리")
    if st.button("🗑️ 훈련된 AI 요원 유전자 모두 삭제"):
        try:
            if supabase: supabase.table("valhalla_ai_models").delete().neq("agent_name", "dummy").execute()
            else:
                if os.path.exists(AI_MODELS_FILE): os.remove(AI_MODELS_FILE)
            st.success("✅ 삭제 완료!"); st.rerun()
        except Exception as e: st.error(f"삭제 실패: {e}")
            
    st.markdown("---")
    with st.expander("✍️ 수동으로 매매 기록 추가하기", expanded=True):
        with st.form("main_trade_form", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                t_date = st.date_input("거래 일자", datetime.date.today())
                t_time = st.text_input("체결 시간", datetime.datetime.now().strftime("%H:%M:%S"))
                t_account = st.text_input("계좌명", "Manual")
            with col2:
                t_ticker = st.text_input("종목 티커", "SOXL").upper()
                t_action = st.radio("매매 구분", ["매수 (Buy)", "매도 (Sell)"])
            with col3:
                t_price = st.number_input("체결 단가 ($)", min_value=0.0, format="%.4f")
                t_qty = st.number_input("수량 (주)", min_value=1, step=1)
            
            if st.form_submit_button("💾 일지 DB에 저장"):
                action_val = "Buy" if "매수" in t_action else "Sell"
                new_record = pd.DataFrame({"Date": [t_date.strftime("%Y-%m-%d")], "Time": [t_time], "Account": [t_account], "Ticker": [t_ticker], "Action": [action_val], "Qty": [t_qty], "Price": [t_price], "Format": ["manual"]})
                append_db("trade_journal", new_record)
                st.success(f"✅ 저장 완료!"); st.rerun() 
                
    st.markdown("---")
    st.markdown("#### 📝 매매 일지 직접 편집")
    col_db1, col_db2 = st.columns([8, 2])
    with col_db1:
        df_display = read_db("trade_journal")
        if not df_display.empty: df_display = df_display.sort_values(["Date", "Time"]).reset_index(drop=True)
        edited_df = st.data_editor(df_display, num_rows="dynamic", use_container_width=True)
        if st.button("💾 수동 편집 내용 덮어쓰기"):
            overwrite_db("trade_journal", edited_df)
            st.success("✅ 수정 내용 DB 반영 완료!"); st.rerun()
    with col_db2:
        if st.button("🗑️ 전체 기록 초기화", key="reset_db"):
            overwrite_db("trade_journal", pd.DataFrame(columns=["Date", "Time", "Account", "Ticker", "Action", "Qty", "Price", "Format"]))
            st.rerun()
            
    st.markdown("---")
    st.markdown("### 🗄️ AI 요원 가상 매매 장부")
    col_ai1, col_ai2 = st.columns([8, 2])
    with col_ai1:
        df_ai_disp = read_db("ai_ledger")
        if not df_ai_disp.empty: st.dataframe(df_ai_disp, use_container_width=True)
    with col_ai2:
        if st.button("🗑️ AI 장부 초기화"):
            overwrite_db("ai_ledger", pd.DataFrame(columns=["Date", "Agent", "Ticker", "Action", "Qty", "Price", "Capital", "Profit"]))
            st.rerun()
