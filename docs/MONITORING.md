# 외부 모니터링 설정 가이드

이 문서는 Braincube AI Report 대시보드의 **외부 가용성/에러 모니터링**을 설정하는 방법을 설명합니다. 모두 **무료 티어** 기준입니다.

## 1. UptimeRobot — 서비스 가용성 감시 + 슬립 방지

**목적**:
- 5분마다 대시보드에 ping → 다운 시 이메일/SMS 알림
- Streamlit Community Cloud의 **7일 자동 슬립 방지**
- 실제 uptime 통계 수집

### 1-1. 가입 및 모니터 생성

1. https://uptimerobot.com 접속 → 무료 가입
2. 로그인 후 **"+ New Monitor"** 클릭
3. 다음 값 입력:

| 항목 | 값 |
|---|---|
| Monitor Type | `HTTP(s)` |
| Friendly Name | `Braincube AI Report` |
| URL | `https://<your-streamlit-url>/?health=1` ← **꼭 `?health=1` 포함** |
| Monitoring Interval | `5 minutes` (무료 최소값) |
| Monitor Timeout | `30 seconds` |
| HTTP Method | `GET` |

> ⚠️ URL 끝에 `?health=1`을 꼭 붙이세요. 이 페이지는 인증 없이 공개되며,
> Firebase 상태를 포함한 간단한 JSON을 반환합니다.

4. **Alert Contacts**: 이메일 주소 추가
5. **Create Monitor** 클릭

### 1-2. 기대 결과

- 5분마다 자동 ping → 앱이 깨어있음 → **7일 슬립 영구 차단**
- 다운 시 1~5분 안에 이메일 도착
- 대시보드에서 uptime 퍼센트 확인 가능

### 1-3. 슬립 방지 원리

Streamlit Community Cloud는 **7일간 트래픽이 없으면** 앱을 슬립 모드로 전환합니다.
UptimeRobot이 5분마다 접속하면 트래픽이 지속되어 슬립되지 않습니다.

---

## 2. Sentry — 에러 수집 및 알림

**목적**:
- 대시보드에서 발생하는 **파이썬 예외를 실시간 수집**
- 스택 트레이스, 사용자 환경, 재현 경로 등 자동 캡처
- 심각 에러 발생 시 이메일 알림

### 2-1. 가입 및 프로젝트 생성

1. https://sentry.io 접속 → 무료 가입 (GitHub 계정 연동 추천)
2. 로그인 후 **"Create Project"** 클릭
3. Platform: **Python** 선택
4. Alert Frequency: **Alert me on every new issue**
5. Project Name: `braincube-ai-report`
6. **Create Project** 클릭

### 2-2. DSN 키 복사

프로젝트 생성 후 표시되는 **DSN URL**을 복사. 형식 예시:
```
https://abc123def456@o789012.ingest.sentry.io/1234567
```

### 2-3. Streamlit Cloud Secrets에 등록

1. Streamlit Cloud 대시보드 → 앱 → **Settings** → **Secrets**
2. 기존 `[firebase]` 섹션 **아래**에 다음 추가:

```toml
# 기존 [firebase] 섹션 아래에 추가
SENTRY_DSN = "https://abc123def456@o789012.ingest.sentry.io/1234567"
```

3. **Save**

### 2-4. Sentry SDK 의존성 추가

로컬에서 `requirements.txt`에 다음 줄을 추가:

```
sentry-sdk>=1.40.0
```

커밋·푸시하면 Streamlit Cloud 재배포 시 자동 설치. 코드는 이미 `modules/log_setup.py`에서 DSN이 있으면 자동으로 초기화하도록 작성되어 있음.

### 2-5. 기대 결과

- Python 예외 발생 시 Sentry 대시보드에 **즉시 기록**
- 첫 발생 시 **이메일 알림**
- 스택 트레이스 + 사용자 환경 자동 캡처
- 무료 플랜: **월 5,000 이벤트**까지 (일반 사용 규모에 충분)

### 2-6. PII (개인정보) 보호

`log_setup.py`에서 `send_default_pii=False`로 설정되어 있어:
- 사용자 아이디, IP, 이메일은 Sentry로 전송되지 않음
- 스택 트레이스와 에러 메시지만 전송
- 조직 개인정보 정책 준수 가능

---

## 3. 헬스체크 엔드포인트

이 앱은 `?health=1` 쿼리로 접속 시 간단한 상태 JSON을 반환합니다 (인증 없음).

**예시 응답**:
```
{
  "status": "ok",
  "firebase": "ok",
  "time": "2026-04-22T12:00:00Z"
}
```

**상태 의미**:
- `status: "ok"` → 정상
- `status: "degraded"` → Firebase 연결 불안정 (앱 자체는 동작)

UptimeRobot이 이 페이지에 접속해 **HTTP 200 응답**을 확인하면 정상으로 판정합니다.

---

## 4. 체크리스트

배포 완료 후 다음을 순서대로 확인:

- [ ] `https://<your-url>/?health=1` 브라우저에서 열어 `status: "ok"` 확인
- [ ] UptimeRobot에 모니터 등록 (5분 간격)
- [ ] Sentry 프로젝트 생성 + DSN 복사
- [ ] `.streamlit/secrets.toml` (로컬) 또는 Streamlit Cloud Secrets에 `SENTRY_DSN` 추가
- [ ] `requirements.txt`에 `sentry-sdk>=1.40.0` 추가 → 커밋·푸시
- [ ] Streamlit Cloud 재배포 완료 후 로그에 `Sentry 연동 활성화` 확인
- [ ] 테스트: Sentry 대시보드에서 테스트 이벤트 발송 → 이메일 도착 확인

---

## 5. 비용 요약

| 서비스 | 플랜 | 비용 | 커버 |
|---|---|---|---|
| UptimeRobot | Free | $0 | 50개 모니터, 5분 간격 |
| Sentry | Developer | $0 | 월 5K 이벤트, 1명 |
| Streamlit Cloud | Community | $0 | 내부용 충분 |
| Firebase Firestore | Spark | $0 | 50K reads/day |
| **합계** | | **$0 / 월** | |

필요 시 Sentry Team($26/월), Firebase Blaze(사용량 과금)로 업그레이드 가능.
