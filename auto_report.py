"""
자동 리포트 발송 스케줄러
백그라운드에서 실행되며, .email_config.json의 스케줄에 따라
.last_report_data.csv 데이터를 기반으로 PPTX 리포트를 생성·발송합니다.

사용법:
    python auto_report.py          # 스케줄 루프 실행
    python auto_report.py --once   # 즉시 1회 발송 (테스트용)
"""
import json
import os
import sys
import time
import logging
from datetime import datetime

# ── 경로 설정 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

_CONFIG_PATH = os.path.join(BASE_DIR, ".email_config.json")
_DATA_PATH = os.path.join(BASE_DIR, ".last_report_data.csv")
_PID_PATH = os.path.join(BASE_DIR, ".scheduler.pid")
_STOP_PATH = os.path.join(BASE_DIR, ".scheduler.stop")
_LOG_PATH = os.path.join(BASE_DIR, ".scheduler.log")

# ── 로깅 ──
logging.basicConfig(
    filename=_LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8",
)
log = logging.getLogger("auto_report")


def _load_config() -> dict | None:
    if not os.path.exists(_CONFIG_PATH):
        return None
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"설정 로드 실패: {e}")
        return None


def _load_data():
    """저장된 CSV 데이터를 DataFrame으로 로드 + 파생 컬럼 재생성"""
    if not os.path.exists(_DATA_PATH):
        return None
    try:
        import pandas as pd
        from modules.config import WEEKDAY_MAP
        df = pd.read_csv(_DATA_PATH, encoding="utf-8-sig")
        # 날짜 컬럼 복원 + 파생 컬럼 생성
        if '날짜' in df.columns:
            df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
            df = df.dropna(subset=['날짜'])
        if '매체명' not in df.columns:
            df['매체명'] = 'LMS 전체'
        # 파생 컬럼 (모든 분석 모듈이 의존)
        if '년월' not in df.columns and '날짜' in df.columns:
            df['년월'] = df['날짜'].dt.strftime('%Y년 %m월')
        if '일자' not in df.columns and '날짜' in df.columns:
            df['일자'] = df['날짜'].dt.strftime('%Y년 %m월 %d일')
        if '요일번호' not in df.columns and '날짜' in df.columns:
            df['요일번호'] = df['날짜'].dt.dayofweek
            df['요일'] = df['요일번호'].map(
                {0: '월요일', 1: '화요일', 2: '수요일', 3: '목요일', 4: '금요일', 5: '토요일', 6: '일요일'}
            )
            df['짧은_요일'] = df['요일번호'].map(WEEKDAY_MAP)
        return df
    except Exception as e:
        log.error(f"데이터 로드 실패: {e}")
        return None


def _should_send_now(schedule: str, send_hour: int = 9) -> bool:
    """현재 시각이 발송 조건에 맞는지 확인 (매 분 체크)"""
    now = datetime.now()
    if now.hour != send_hour or now.minute != 0:
        return False

    if schedule == "매주 월요일":
        return now.weekday() == 0  # 월요일
    elif schedule == "매월 1일":
        return now.day == 1
    elif schedule == "매월 15일":
        return now.day == 15
    else:  # "수동 발송만"
        return False


_MAX_RETRIES = 3
_RETRY_DELAY = 30  # seconds


def _send_report(config: dict, df, retries: int = _MAX_RETRIES) -> bool:
    """리포트 생성 및 발송 (실패 시 자동 재시도)"""
    company_name = config.get("company_name", "")
    service_name = config.get("service_name", "")

    # PPTX 생성 (1회만 — 재시도 시 재사용)
    pptx_bytes = None
    try:
        from modules.report import generate_pptx
        pptx_bytes = generate_pptx(df, company_name, service_name=service_name)
        log.info("PPTX 생성 완료")
    except Exception as e:
        log.warning(f"PPTX 생성 실패 (본문만 발송): {e}")

    # PDF 원페이저 생성
    pdf_bytes = None
    try:
        from modules.onepager_pdf import generate_onepager
        pdf_bytes = generate_onepager(df, company_name, service_name=service_name)
        log.info("PDF 원페이저 생성 완료")
    except Exception as e:
        log.warning(f"PDF 생성 실패: {e}")

    # 이메일 발송 (재시도 포함)
    from modules.email_report import send_email
    for attempt in range(1, retries + 1):
        try:
            ok, msg = send_email(config, company_name, df, pptx_bytes, pdf_bytes, service_name=service_name)
            if ok:
                log.info(f"발송 성공 (시도 {attempt}/{retries}): {msg}")
                config["last_sent"] = datetime.now().isoformat()
                try:
                    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
                        json.dump(config, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
                return True
            else:
                log.warning(f"발송 실패 (시도 {attempt}/{retries}): {msg}")
        except Exception as e:
            log.error(f"발송 예외 (시도 {attempt}/{retries}): {e}")

        if attempt < retries:
            log.info(f"{_RETRY_DELAY}초 후 재시도...")
            time.sleep(_RETRY_DELAY)

    log.error(f"발송 최종 실패 ({retries}회 시도)")
    return False


def _check_stop_signal() -> bool:
    """중지 시그널 파일 존재 여부 확인"""
    return os.path.exists(_STOP_PATH)


def _cleanup():
    """종료 시 PID·STOP 파일 정리"""
    for path in (_PID_PATH, _STOP_PATH):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError:
            pass


def run_once():
    """즉시 1회 발송 (--once 모드)"""
    config = _load_config()
    if not config:
        log.error(".email_config.json 파일이 없습니다.")
        sys.exit(1)

    df = _load_data()
    if df is None:
        log.error(".last_report_data.csv 파일이 없습니다.")
        sys.exit(1)

    ok = _send_report(config, df)
    sys.exit(0 if ok else 1)


def run_loop():
    """스케줄 루프 (메인 모드)"""
    # PID 기록
    with open(_PID_PATH, "w") as f:
        f.write(str(os.getpid()))

    log.info(f"스케줄러 시작 (PID: {os.getpid()})")

    try:
        while True:
            # 중지 시그널 확인
            if _check_stop_signal():
                log.info("중지 시그널 감지 → 종료")
                break

            # 설정 로드 (매 루프마다 → 설정 변경 즉시 반영)
            config = _load_config()
            if not config:
                time.sleep(60)
                continue

            schedule = config.get("schedule", "수동 발송만")
            send_hour = int(config.get("send_hour", 9))

            if _should_send_now(schedule, send_hour):
                log.info(f"발송 조건 충족 (스케줄: {schedule}, 시간: {send_hour}시)")
                df = _load_data()
                if df is not None:
                    _send_report(config, df)
                else:
                    log.warning("데이터 파일 없음 — 발송 스킵")

            # 60초 간격으로 체크
            time.sleep(60)

    except KeyboardInterrupt:
        log.info("키보드 인터럽트 → 종료")
    finally:
        _cleanup()
        log.info("스케줄러 종료")


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_once()
    else:
        run_loop()
