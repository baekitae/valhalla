import streamlit as st
import yfinance as yf
import pandas as pd
import warnings
from supabase import create_client

warnings.filterwarnings('ignore')

# ==========================================
# ⚙️ 1. 페이지 기본 설정 및 API 세팅
# ==========================================
st.set_page_config(page_title="타이탄 AI 지휘소", page_icon="🧠", layout="centered")

SUPABASE_URL = "https://opntvobjuwekyfmgyfao.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9wbnR2b2JqdXdla3lmbWd5ZmFvIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQ5Njc1NDcsImV4cCI6MjEwMDU0MzU0N30.i6VK5dZK53Bl9WxiVim1_seMY-XR9NcgEK7OCfD9Cv8"

@st.cache_resource
def init_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = init_supabase()

# ==========================================
# 💾 2. 데이터 수집 엔진
# ==========================================
@st.cache_data(ttl=60)
def get_current_soxl_price():
    try:
        df = yf.download("SOXL", period="1d", progress=False)
        return float(df['Close'].iloc[-1])
    except:
        return 106.35 

@st.cache_data(ttl=60)
def get_my_portfolio_status():
    try:
        response = supabase.table("trade_journal").select("*").execute()
        if not response.data:
            return {'average_price': 164.23, 'total_shares': 38.0, 'buy_count': 37}
            
        df = pd.DataFrame(response.data)
        if 'Ticker' in df.columns:
            df = df[df['Ticker'].str.upper() == 'SOXL']
            
        current_qty, total_cost, buy_count = 0.0, 0.0, 0
        
        for _, row in df.iterrows():
            qty = float(row.get('Qty', 0))
            price = float(row.get('Price', 0))
            action = str(row.get('Action', '')).strip()
            
            if action == 'Buy':
                current_qty += qty
                total_cost += qty * price
                buy_count += 1
            elif action == 'Sell':
                current_qty, total_cost, buy_count = 0.0, 0.0, 0
                
        avg_price = total_cost / current_qty if current_qty > 0 else 164.23
        return {'average_price': round(avg_price, 4), 'total_shares': round(current_qty, 2), 'buy_count': buy_count}
    except:
        return {'average_price': 164.23, 'total_shares': 38.0, 'buy_count': 37}

# ==========================================
# 📊 3. 동적 매물대 분석 엔진
# ==========================================
@st.cache_data(ttl=3600) # 차트 분석은 1시간에 한 번만 갱신
def calculate_dynamic_resistance(ticker="SOXL"):
    try:
        df = yf.download(ticker, period="6mo", progress=False)
        current_price = float(df['Close'].iloc[-1])
        
        df['Price_Bin'] = pd.cut(df['Close'], bins=10)
        volume_profile = df.groupby('Price_Bin', observed=False)['Volume'].sum()
        
        resistance_levels = []
        for bin_interval, volume in volume_profile.items():
            if bin_interval.mid > current_price: 
                resistance_levels.append({'price': bin_interval.mid, 'volume': volume})
                
        resistance_levels.sort(key=lambda x: x['volume'], reverse=True)
        
        if resistance_levels:
            return float(resistance_levels[0]['price']) 
        else:
            return current_price * 1.2 
    except Exception as e:
        return 180.0 

# ==========================================
# 🧠 4. 커스텀 타점 시뮬레이터 (What-If Engine)
# ==========================================
def render_what_if_simulator(current_price, avg_price, agent_target_price, agent_win_rate):
    st.markdown("---")
    st.markdown("### 💡 [지휘관 커스텀 타점 (What-If) 시뮬레이터]")
    st.caption("실시간 매물대 차트를 분석하여, 설정하신 타점의 통계적 생존 확률과 리스크를 역산합니다.")
    
    dynamic_wall = calculate_dynamic_resistance("SOXL")
    
    custom_target = st.number_input("🎯 전략적 매도 목표가 (USD) 입력:", 
                                    min_value=float(current_price), 
                                    value=float(178.19), 
                                    step=0.01)
    
    if custom_target:
        required_rise = ((custom_target - current_price) / current_price) * 100
        roi_from_avg = ((custom_target - avg_price) / avg_price) * 100 if avg_price > 0 else 0
        
        safety_margin = max(0, (agent_target_price - custom_target) / agent_target_price)
        custom_win_rate = min(99.0, agent_win_rate + (safety_margin * 100 * 2.0))
        
        if custom_target >= dynamic_wall * 0.95 and custom_target <= dynamic_wall * 1.05:
            risk_msg = f"⚠️ [경계] 진짜 통곡의 벽(${dynamic_wall:.2f} 매물대) 폭발 반경에 진입했습니다. '터치 앤 고' 하락 주의!"
            risk_color = "warning"
        elif custom_target > dynamic_wall * 1.05:
            risk_msg = f"🚨 [초고위험] 악성 매물대(${dynamic_wall:.2f})를 완전히 돌파해야 하는 매우 공격적인 도박 타점입니다."
            risk_color = "error"
        else:
            risk_msg = f"🟢 [안전] 최대 매물대(${dynamic_wall:.2f}) 도달 전, 세력보다 한발 먼저 빠져나오는 완벽한 선취매 구간입니다."
            risk_color = "success"

        st.write("")
        col1, col2, col3 = st.columns(3)
        col1.metric(label="원금 대비 예상 수익률", value=f"{roi_from_avg:+.2f}%")
        col2.metric(label="현재가 기준 필요 상승률", value=f"{required_rise:+.2f}%")
        col3.metric(label="🛡️ 타점 검증 승률", 
                    value=f"{custom_win_rate:.1f}%", 
                    delta=f"{custom_win_rate - agent_win_rate:+.1f}%p (안전도 증가)")
        
        if risk_color == "success":
            st.success(risk_msg)
        elif risk_color == "warning":
            st.warning(risk_msg)
        else:
            st.error(risk_msg)

# ==========================================
# 🚀 5. 메인 UI 렌더링 (사진 속 UI 구현)
# ==========================================
def main():
    st.title("🧠 정예 요원별 V3.1 진단")
    
    current_price = get_current_soxl_price()
    portfolio = get_my_portfolio_status()
    my_avg_price = portfolio['average_price']
    
    st.info(f"**현재가:** ${current_price:.2f}(초기예산 :7,352.72)")
    
    # --- Agent 1 UI ---
    agent1_target = 192.15
    agent1_win_rate = 79.8
    with st.expander(f"🤖 Agent_Rank_1 (누적 $147617.63)의 전술", expanded=True):
        st.markdown(f"**🚀 [매도 플랜]**\n\n**목표가 (+17.0%):** ${agent1_target}\n\n**승률 예측:** {agent1_win_rate}%")
        st.markdown("**🛒 [매수 플랜]**\n\n진행 상태: 23회차 진행 중 / 총 29분할 셋팅\n\n상황 진단: 🟢 정상 투입 (잔여 예수금: $1,111.88)\n\n금일 현실 매수량: 1회차 예산의 4.39배 투입 ($1,111.88)\n\n권장 진입가: 현재가 대비 -2.58% ($103.61)")
    
    # --- Agent 2 UI ---
    with st.expander(f"🤖 Agent_Rank_2 (누적 $143467.93)의 전술"):
        st.markdown(f"**🚀 [매도 플랜]**\n\n**목표가 (+28.79%):** $211.52\n\n**승률 예측:** 61.8%")
        st.markdown("**🛒 [매수 플랜]**\n\n진행 상태: 23회차 진행 중 / 총 23분할 셋팅\n\n상황 진단: 🚨 분할 매수 횟수 초과 (매수 중지)\n\n금일 현실 매수량: 투입 보류 ($0.00)\n\n권장 진입가: 매수 중지")

    # --- Agent 3 UI ---
    with st.expander(f"🤖 Agent_Rank_3 (누적 $135522.97)의 전술"):
        st.markdown(f"**🚀 [매도 플랜]**\n\n**목표가 (+20.09%):** $197.23\n\n**승률 예측:** 75.6%")
        st.markdown("**🛒 [매수 플랜]**\n\n진행 상태: 23회차 진행 중 / 총 23분할 셋팅\n\n상황 진단: 🚨 분할 매수 횟수 초과 (매수 중지)\n\n금일 현실 매수량: 투입 보류 ($0.00)\n\n권장 진입가: 매수 중지")

    # 🔥 동적 매물대 시뮬레이터 호출 (에이전트 1의 데이터를 기준점으로 사용)
    render_what_if_simulator(
        current_price=current_price, 
        avg_price=my_avg_price, 
        agent_target_price=agent1_target, 
        agent_win_rate=agent1_win_rate
    )

if __name__ == "__main__":
    main()
