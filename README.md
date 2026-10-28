# FarmPick

KAMIS 가격정보를 정식·수확 일정과 연결하고, 시장별 가격 및 비용 가정으로 예상 수취액을 비교하는 Django 웹 서비스다.

## 주요 기능

- 농장별 수확·출하 요약, 정식·수확 중심 캘린더, 직접 등록 일정, 일반 날씨 예보
- 토마토·파프리카의 선정 트리 모델을 이용한 수확일 가격 전망과 실제 관측·전망 그래프
- 발행 당시 입력·결과·모델·그래프를 보존하는 전망 이력
- aT 정산자료 수집·조건별 조회, 검증 가능한 kg 대표가격과 시장 시계열
- KAMIS 전망과 과거 시장 가격비를 이용한 시장별 미래 단가 시나리오
- 수량·판매가능 비율·단가·운송·포장·수수료 등을 반영한 판로별 예상 수취액 비교

작업 위험도 예측·챗봇은 사용자 서비스에서 제외했다. 시장 미래 단가는 탐색적 시나리오이며 실제 농가 정산·소득 개선을 보증하지 않는다.

## 실행 환경

검증 환경은 Windows x64, PowerShell, Python 3.10.20, Django 5.2.14, SQLite다. 웹 전망은 scikit-learn 트리를 CPU에서 실행하므로 GPU가 필수는 아니다. TimesFM을 서버 시작 시 선로딩하지 않는다.

[requirements.txt](requirements.txt)는 직접 의존성, [requirements-lock.txt](requirements-lock.txt)는 전이 의존성까지 고정한 검증 환경 목록이다. 기존 모델 코드와의 호환성을 위해 실험용 CUDA PyTorch·TimesFM 의존성도 포함되어 설치 용량이 크다. 다른 OS·Python 버전은 별도 검증하지 않았다.

## 설치·설정

다음 명령은 저장소의 Django 루트에서 실행한다. 작업공간 전체를 받은 경우 먼저 `code` 폴더로 이동한다.

```powershell
py -3.10 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```

[.env.example](.env.example)을 참고해 소스코드 밖의 개인 경로에 환경 파일을 직접 만든다. 다음 경로는 가상 예시다.

```powershell
$env:FARMCAL_ENV_FILE = 'C:\YOUR_PRIVATE_DIRECTORY\FarmPick\.env'
& .\.venv\Scripts\python.exe manage.py migrate
& .\.venv\Scripts\python.exe manage.py check
& .\.venv\Scripts\python.exe manage.py runserver
```

`http://127.0.0.1:8000/`에서 회원가입하고 토마토 또는 파프리카 농장을 등록한다. 실제 사용자 DB를 공유하지 않으므로 수신자가 새 계정·농장·일정을 만든다. 가상환경 Python 경로를 직접 호출하므로 PowerShell 활성화 정책이나 미설정 `$python` 변수에 의존하지 않는다.

| 환경변수 | 용도 |
|---|---|
| `DJANGO_SECRET_KEY` | 필수 Django 비밀키. 수신자가 충분히 긴 임의 값을 직접 생성 |
| `KAMIS_API_KEY`, `KAMIS_ID` | KAMIS 인증 |
| `KMA_API_KEY`, `KMA_MID_API_KEY` | 기상청 일반 날씨 예보 |
| `AT_CODE_API_KEY` | 농축수산물 표준코드 API 인증 |
| `AT_REALTIME_API_KEY` | 전국 공영도매시장 실시간 경매 API 인증 |
| `AT_SETTLEMENT_API_KEY` | 전국 공영도매시장 정산정보 API 인증 |

aT 인증키는 각 API별로 따로 설정한다. 시장 정산 수집에는 표준코드·정산정보 활용 승인이 필요하다. 저장 자료 조회에는 해당 외부 API 인증이 필요하지 않다.

`FARMCAL_ENV_FILE`은 운영체제 환경변수에서 지정한다. 지정된 절대 경로의 파일만 읽으며 프로젝트 `.env`를 자동 탐색하지 않는다. 운영체제 환경변수가 파일 값보다 우선한다. 변경 후 기존 환경변수와 서버 재시작 여부를 확인한다. 기존 `farmcal` 경로·환경변수 이름은 호환성을 위해 유지했다. 이 저장소 설정은 로컬 시연용이며 운영 서버 배포 설정은 별도다.

## 사용 순서와 자료 준비

1. 농장 등록 후 캘린더에서 정식·수확 일정을 생성한다.
2. 수확일 전망 화면의 `KAMIS 가격자료 갱신`으로 관측자료를 수집한다. 모델 재학습은 아니다.
3. 대상일 전망·가격 기준·마지막 관측일을 확인하고 필요한 결과를 명시적으로 발행한다.
4. 시장 정산자료에서 작물·날짜·시장을 선택해 수집한다. 최대 7일, 시장 전체 선택, 진행·0건·실패·중단 표시를 지원한다.
5. 시장 분석에서 동일 품종·등급·크기·포장·산지 등의 조건을 선택하고 대표가격과 미래 단가 시나리오를 구분해 확인한다.
6. 수량·비용을 입력해 판로별 수취액을 비교한다. 생산비 미입력 시 순이익은 계산하지 않는다.

선정 모델과 고정 공개 KAMIS 입력은 저장소에 포함한다. 기준 입력의 마지막 관측은 2026-10-22이며 본인 농장의 같은 출처·가격 기준 신규 관측을 결합한다. 마지막 관측이 기준일보다 7일 넘게 오래되면 전망을 거부하므로 이후 시연에는 갱신이 필요하다. 학습일 이전으로 현재 모델을 소급 발행할 수 없다.

**수집한 시장 원본·사용자 DB는 Git에 포함하지 않는다.** 새 환경에서는 시장자료를 직접 수집한다. 공식 시장 목록의 기준 코드표만 포함한다. 웹 수집은 화면을 닫아도 지속되는 백그라운드 작업이 아니다.

### 긴 기간 시장자료 수집 예시

아래 날짜는 과거 검증 기간 예시다. 필요한 기간으로 바꾸고 기존 자료와 겹치지 않는 새 출력 폴더를 지정한다.

```powershell
$marketDays = 0..89 | ForEach-Object { ([datetime]'2026-07-29').AddDays($_).ToString('yyyy-MM-dd') }
& .\.venv\Scripts\python.exe manage.py inspect_market_data --source settlement --crop-name 토마토 --market-names 서울가락 인천삼산 --dates $marketDays --all-pages --page-size 1000 --import-to-web --output data/markets/new_tomato_collection
& .\.venv\Scripts\python.exe manage.py inspect_market_data --source settlement --crop-name 파프리카 --market-names 광주서부 전주 --dates $marketDays --all-pages --page-size 1000 --import-to-web --output data/markets/new_paprika_collection
```

대표가격용 수집에는 `--all-pages`를 사용한다. 날짜×시장×품목에 따라 요청 수가 늘어난다. 정상 0건과 실패는 구분한다. 따로 받은 스냅샷은 `manage.py import_market_data <폴더>`로 가져올 수 있다. 같은 스냅샷은 해시로 중복을 막는다.

## 계산·해석 기준

판매가능 수량은 수확량×판매가능 비율이다. 판매금액에서 운송·포장·수수료·기타 비용을 빼 수취액을 계산하고 생산비가 입력된 경우에만 순이익을 표시한다. 수량·금액은 Decimal로 반올림하며 손실을 0으로 숨기지 않는다. 여러 판로는 같은 물량을 전량 판매하는 각각의 대안이며 결과를 합산하지 않는다.

KAMIS 전망은 전국·상품·중도매인 판매 참고가격으로 실제 농가 판매단가와 다르다. 시장 대표가격은 단위 검증을 통과한 동일 조건의 총금액÷총중량이다. 미래 시장가격은 최근 동일 날짜의 시장/KAMIS 가격비 중앙값×KAMIS 전망이라는 가정이다. 충분한 관측이 없으면 산출하지 않고 가격이 없는 날은 그래프를 연결하지 않는다.

## 구현·검증 요약

2025 검증의 두 작물×6개 기간별 당일 WAPE 균형 평균으로 트리를 선정했다. 트리 14.8256%, 최근가 25.0318%였으며 2026 재사용 평가에서는 트리 18.3572%, 계절 중앙값 15.6136%로 계절법이 더 좋았다. 독립적인 새 최종 평가나 모든 기간의 우월성으로 해석하지 않는다. WAPE를 정확도 백분율로 바꾸지 않는다.

공개 시장자료의 탐색적 평가 조합은 토마토 2,646·파프리카 335개다. 가격비 WAPE는 전체 합산에서 36.93%·35.60%, 시장×기간별 균형 평균에서는 31.54%·50.47%로 집계에 따라 결론이 다르다. 90/180일은 표본이 없으며 60일 발행일도 2개다. 관측가격과 명시적 비용 가정의 124사례를 산술 대조했다. 실제 농가 정산·소득 개선을 입증한 것은 아니다.

전체 소스에서 회귀 테스트 256개와 실제 모델 HTTP 기능 확인 14개를 통과한 기록이 있다. 당시 로컬 반복 요청 p95는 전망 화면 206.76ms, 시장 분석 164.60ms, 혼합 4개 동시 작업 총 40요청 p95 721.73ms였다. 이는 당시 자료·코드의 서버 응답 측정이며 브라우저 렌더링·외부 API·운영 동시 접속 용량의 수치가 아니다.

## 코드 구조

```text
accounts/          인증·사용자별 농장·공통 메뉴
calendar_app/      수확·출하 요약·정식·수확 캘린더
config/            Django 설정·외부 환경 파일 로더
forecaster/        수집·전망·발행 이력·시장 분석·수취액
weather/           일반 날씨 예보
chatbot/           기존 DB 호환용 모델·마이그레이션
templates/         화면
static/            CSS·JavaScript·라이브러리·폰트 및 라이선스
model_artifacts/   선정 모델·릴리스 명세
data/              고정 KAMIS 입력·시장 선택용 코드표만 공유
reports/           웹 화면이 참조하는 집계 요약만 공유
```
