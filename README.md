# Valhalla 무한매수 전술 관제소 V4.0

## 파일 구성

| 파일 | 역할 |
|---|---|
| `valhalla.py` | Streamlit 화면 (6개 페이지) · 저장소 · 시세 조회 |
| `valhalla_core.py` | 계산 로직 (평단·손익, 키움 파서, 지표, AI 엔진, 백테스트, 유전 알고리즘, 리그, 검증) |
| `jobs/daily_league.py` | 리그 자동 실행 스크립트 (GitHub Actions) |
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

## 요원 검증

- **배치 전 (훈련소)**
  - 학습에 쓰지 않은 검증 구간 수익이 0보다 커야 합니다.
  - 기간별 안정성(6개월 구간)에서 수익을 낸 구간 비율이 50% 이상이어야 합니다.
  - 학습 성적에서 검증 성적으로 50%p 넘게 떨어지면 과적합으로 봅니다.
  - 기본 설정은 통과한 요원만 배치하는 것입니다.
- **배치 후 (리그)**
  - 리그는 배치일부터 시작합니다.
  - 20거래일을 관찰한 뒤 판정합니다.
    - ✅ 정상: 보유(Buy & Hold) 대비 −10%p 이내
    - ⚠️ 주의: 그 밖의 경우
    - ❌ 교체 권고: 손실 중이면서 낙폭이 허용치(훈련 검증 MDD의 1.5배, 최소 30%)를 넘은 경우
