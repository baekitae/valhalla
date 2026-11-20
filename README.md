# Valhalla 무한매수 전술 관제소 V4.0

## 파일 구성

| 파일 | 역할 |
|---|---|
| `valhalla.py` | Streamlit 화면 (7개 페이지) · 저장소 · 시세 조회 |
| `valhalla_core.py` | 계산 로직 (평단·손익, 키움 파서, 지표, AI 엔진, 백테스트, 유전 알고리즘, 리그, 검증) |
| `jobs/daily_league.py` | 리그 자동 실행 스크립트 (GitHub Actions) |
| `jobs/daily_signals.py` | 신호 관제 매일 자동 점검: 주 1건 예측 기록·채점·섹터별 비교 저장 (GitHub Actions, KST 화~토 07:30) |
| `.github/workflows/daily_league.yml` | 매일 KST 화~토 07:10 자동 실행 |
| `.streamlit/config.toml` | 다크 테마 |
| `requirements.txt` | 배포용 패키지 |
| `tests/` | 자동 테스트 (배포에는 필요 없음) |

**`valhalla.py`와 `valhalla_core.py`는 반드시 같은 폴더에 있어야 합니다.**

## 배포 (Streamlit Community Cloud)

1. 위 파일들을 GitHub 저장소에 올립니다 (`tests/`는 선택).
2. 메인 파일을 `valhalla.py`로 지정합니다.
3. Supabase를 쓰면 Secrets에 기존과 똑같이 입력합니다.
   ```toml
   SUPABASE_URL = "https://xxxx.supabase.co"
   SUPABASE_KEY = "..."
   ```

## 기존 데이터 호환

- **Supabase 테이블**(`trade_journal`, `ai_ledger`, `valhalla_ai_models`)은 구조를 바꾸지 않았습니다. 그대로 사용하면 됩니다.
- **로컬 파일**(`trade_journal.csv`, `ai_ledger.csv`, `ai_models.json`)도 그대로 읽힙니다.
- 기존 요원(유전자 리스트)은 계속 동작합니다. 새로 훈련해 배치하면 순위·훈련일·검증 성적이 함께 저장됩니다.

## 테스트 실행 (선택)

```bash
pip install -r requirements-dev.txt
```

```bash
python -m pytest -q tests
```

## 동작 규칙 요약

- **평단:** 이동평균 원가법을 씁니다. 부분 매도 시 평단은 그대로 두고 원가만 비례해서 줄입니다. 전량 매도하면 사이클이 새로 시작됩니다.
- **AI 매매 규칙:** 훈련, 리그, 대시보드 모두 같은 규칙입니다.
  - 매수: 전일 종가 × (1 − 할인율) 지정가
  - 매도: 평단 × (1 + 목표수익률) 지정가
  - 분할 횟수와 예산 한도를 지키며, 수수료를 반영합니다.
- **도달 확률:** 공식으로 만든 '승률'이 아닙니다. 최근 5년 실제 차트에서 같은 상승폭에 기간 내 도달한 비율(경험적 확률)입니다.

## 리그 자동 실행 (GitHub Actions)

1. 저장소 **Settings → Secrets and variables → Actions**에 `SUPABASE_URL`, `SUPABASE_KEY`를 Streamlit Secrets와 같은 값으로 추가합니다.
2. **Actions → 발할라 리그 자동 실행 → Run workflow**로 바로 시험할 수 있습니다.
3. 매일 KST 화~토 07:10에 자동으로 실행되며 아래 일을 합니다.
   - 리그 설정(종목·시작일·자본·비용)대로 오늘까지 가상매매를 진행합니다.
   - `ai_ledger`의 해당 종목 장부를 교체합니다.
   - `valhalla_league_daily`에 날짜별 성적을 기록합니다.
   - `valhalla_job_runs`에 실행 결과를 남깁니다.

## 훈련 방식

- 평가 시나리오는 두 종류입니다.
  - 실제 학습 차트: 전체, 앞 절반, 뒤 절반
  - 가상 차트: 학습 기간의 하루 단위 움직임을 블록째 재표집해 처음부터 새로 만든 차트
- 가상 차트를 실제 차트 뒤에 이어 붙이지 않기 때문에, 특정 상승장 하나를 외우는 과적합이 줄어듭니다.
- 적합도 = (평균 수익 + 최악 시나리오 수익) ÷ 2 − 낙폭 페널티 × 평균 MDD

## 요원 검증

- **배치 전 (훈련소)**
  - 학습에 쓰지 않은 검증 구간 수익이 0보다 커야 합니다.
  - 기간별 안정성(6개월 구간)에서 수익을 낸 구간 비율이 50% 이상이어야 합니다.
  - 검증 구간 낙폭(MDD)이 그냥 보유했을 때보다 작아야 합니다.
  - 학습→검증 성적이 100%p 넘게 떨어지면 과적합 참고 경고를 붙입니다.
  - 기본 설정은 통과한 요원만 배치하는 것입니다.
- **배치 후 (리그)**
  - 리그는 배치일부터 시작합니다.
  - 20거래일을 관찰한 뒤 판정합니다.
    - ✅ 정상: 보유(Buy & Hold) 대비 −10%p 이내
    - ⚠️ 주의: 그 밖의 경우
    - ❌ 교체 권고: 손실 중이면서 낙폭이 허용치(훈련 검증 MDD의 1.5배, 최소 30%)를 넘은 경우

## 신호 관제 (반등 신호 점수판 · 예측 장부)

- **차트 신호 6개 (주봉):** 저점 유지, 주봉 상승, MACD 개선, RSI 상승, 20일선 회복, 1배 지수(SOXX) 동행
- **거시 신호 4개:** 10년물 금리 하락, 달러 약세, 유가 안정, VIX 진정
- **확률:** 과거에 같은 점수가 나왔던 주의 4·12주 뒤 실제 상승 비율. 표본이 적으면 평소 확률 쪽으로 보정합니다.
- **과최적화 점검:** 최근 2년은 확률 계산에 쓰지 않는 검증 구간으로 따로 보여 줍니다.
- **매일 자동 점검:** 매일 아침 53개 종목의 섹터·유형별 비교 결과를 `valhalla_signal_reports`에 저장하고, 화면은 최신 결과와 날짜별 추이를 보여 줍니다.
- **예측 장부:** 매일 아침 자동 점검이 마감된 주의 예측을 주 1건씩 남기고(같은 주는 다시 쓰지 않음), 기간이 지나면 실제 수익률로 채점합니다. 화면에서 확률 정확도(브라이어 점수)를 '평소 확률'과 비교합니다.

### 처음 한 번: Supabase 테이블 만들기 (SQL Editor)

`valhalla_signal_reports`(매일 비교 스냅샷)도 필요합니다. 구조는 `jobs/daily_signals.py`와 `valhalla_core.REPORT_MAP` 참고.

```sql
create table if not exists valhalla_predictions (
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
);
```

다른 테이블에 RLS 정책을 쓰고 있다면 이 테이블에도 같은 정책을 추가하세요.
대상 종목은 기본으로 `valhalla_core.SECTOR_GRID`의 13개 섹터 × (3배·2배·1배 ETF·개별 종목) 53개입니다 (반도체·나스닥100·S&P500·기술·금융·헬스케어·바이오·소형주·산업재·부동산·유틸리티·에너지·금광). 섹터나 유형이 다르면 확률·적중률을 섞지 않고 따로 계산합니다. 바꾸려면 워크플로에 `SIGNAL_TICKERS` 환경변수(예: `SOXL:SOXX,TQQQ:QQQ`)를 추가합니다.

## 종목 발굴 (타이탄봇 결과 읽기)

- 테마 순위 · 주도주 · 수급은 **타이탄봇이 매일 아침 계산**해 자기 DB에 저장하고, 발할라는 그 결과를 **읽기만** 합니다 (계산 엔진은 한 곳).
- 발할라 Streamlit Secrets에 타이탄봇의 DB_URI와 같은 값을 한 줄 추가하세요.
  ```toml
  TITAN_DB_URI = "postgresql+psycopg2://사용자:비밀번호@호스트:6543/postgres"
  ```
