"""
중앙 로깅 설정 모듈
──────────────────
Streamlit Cloud stdout에 구조화된 로그를 남기고,
Sentry가 설정되어 있으면 자동 연동한다.

사용법:
  from modules.log_setup import get_logger
  log = get_logger(__name__)
  log.info("메시지")
  log.warning("경고", extra={"client_id": "abc"})
  log.exception("예상치 못한 에러")  # stack trace 포함
"""
import logging
import os
import sys
from typing import Optional

_CONFIGURED = False


def _configure_root():
    """루트 로거 1회 설정. stdout + Sentry (설정된 경우)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    root = logging.getLogger()
    # 기존 핸들러 제거 (Streamlit/uvicorn이 붙인 것 제거)
    for h in list(root.handlers):
        root.removeHandler(h)

    # stdout 핸들러 (Streamlit Cloud 로그 탭에 수집됨)
    h = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter(
        '%(asctime)s %(levelname)-7s %(name)s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    h.setFormatter(fmt)
    root.addHandler(h)
    root.setLevel(logging.INFO)

    # 시끄러운 서드파티 로거 억제
    for noisy in ('urllib3', 'google', 'grpc', 'firebase_admin'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Sentry 연동 (선택적 — 환경변수/secrets에 SENTRY_DSN 있으면 활성화)
    _try_init_sentry()


def _try_init_sentry():
    """Sentry SDK 연동 — sentry-sdk 설치 + DSN 설정 시에만 활성화."""
    dsn: Optional[str] = None
    # 우선: 환경변수
    dsn = os.environ.get('SENTRY_DSN') or dsn
    # Streamlit secrets
    if not dsn:
        try:
            import streamlit as st
            if hasattr(st, 'secrets') and 'SENTRY_DSN' in st.secrets:
                dsn = str(st.secrets['SENTRY_DSN'])
        except Exception:
            pass

    if not dsn:
        return  # Sentry 사용 안 함

    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_logging = LoggingIntegration(
            level=logging.INFO,          # info 이상 breadcrumb
            event_level=logging.ERROR,   # error 이상만 이벤트 전송
        )
        sentry_sdk.init(
            dsn=dsn,
            integrations=[sentry_logging],
            traces_sample_rate=0.0,      # 성능 추적 비활성 (비용 절감)
            profiles_sample_rate=0.0,
            environment=os.environ.get('STREAMLIT_ENV', 'production'),
            send_default_pii=False,      # PII 전송 안 함
        )
        logging.getLogger(__name__).info("Sentry 연동 활성화")
    except ImportError:
        logging.getLogger(__name__).info(
            "Sentry DSN 설정됨 but sentry-sdk 미설치 — 무시"
        )
    except Exception as e:
        logging.getLogger(__name__).warning(f"Sentry 초기화 실패: {e}")


def get_logger(name: str) -> logging.Logger:
    """모듈용 로거 반환. 최초 호출 시 자동 설정."""
    _configure_root()
    return logging.getLogger(name)


# ──────────────────────────────────────────────
# 세션 상태 정리 (메모리 누수 방지)
# ──────────────────────────────────────────────
def cleanup_session_state():
    """Streamlit 세션 상태에서 수명이 짧은 플래그들을 정리.

    대상:
      - _editing_*   : 인라인 편집 폼 플래그 (컨텍스트 떠나면 불필요)
      - _tmp_*       : 일시 계산 결과
      - _toast_msg   : 이미 표시된 토스트 (pop 되지 않은 찌꺼기)

    호출 시점: main() 진입 시 매번. O(n) 스캔이라 키 수백 개 수준엔 영향 없음.
    DataFrame 같은 큰 객체는 보존 (_df_raw, df_loaded 등).
    """
    try:
        import streamlit as st
        keys_to_drop = []
        for k in list(st.session_state.keys()):
            # 편집 폼 플래그 — 가장 바깥 expander가 닫히면 필요 없음
            if isinstance(k, str) and k.startswith('_editing_'):
                if not st.session_state.get(k):
                    keys_to_drop.append(k)
            # 임시 네임스페이스
            elif isinstance(k, str) and k.startswith('_tmp_'):
                keys_to_drop.append(k)
        for k in keys_to_drop:
            try:
                del st.session_state[k]
            except Exception:
                pass
    except Exception:
        pass
