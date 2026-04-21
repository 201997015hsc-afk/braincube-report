"""
정기 리포트 이메일 발송 모듈
SMTP 설정을 기반으로 PPTX 리포트를 첨부하여 이메일 자동 발송.
사이드바에서 설정하고, 즉시 발송 또는 스케줄 예약이 가능합니다.
"""
import io
import json
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime

import subprocess
import sys

import streamlit as st
import pandas as pd

from modules.config import section_header, alert_card_html, kpi_card_html

# 설정 파일 경로
_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
_CONFIG_PATH = os.path.join(_BASE_DIR, ".email_config.json")
_DATA_PATH = os.path.join(_BASE_DIR, ".last_report_data.csv")
_PID_PATH = os.path.join(_BASE_DIR, ".scheduler.pid")
_STOP_PATH = os.path.join(_BASE_DIR, ".scheduler.stop")


# ──────────────────────────────────────────────
# 설정 저장/로드
# ──────────────────────────────────────────────

def _load_config() -> dict:
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "sender_email": "",
        "sender_password": "",
        "recipients": "",
        "schedule": "매월 1일",
        "service_name": "",
        "company_name": "",
    }


def _save_config(config: dict):
    try:
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_service_name() -> str:
    """저장된 서비스명 반환 (화이트라벨용). 비어있으면 빈 문자열."""
    return _load_config().get("service_name", "")


def _save_report_data(df: pd.DataFrame):
    """자동 발송용 데이터 스냅샷 저장"""
    try:
        df.to_csv(_DATA_PATH, index=False, encoding="utf-8-sig")
    except Exception:
        pass


# ──────────────────────────────────────────────
# 스케줄러 프로세스 관리
# ──────────────────────────────────────────────

def _is_scheduler_running() -> bool:
    """PID 파일로 스케줄러 프로세스 생존 확인"""
    if not os.path.exists(_PID_PATH):
        return False
    try:
        with open(_PID_PATH, "r") as f:
            pid = int(f.read().strip())
        # Windows: pid 존재 확인
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (ValueError, OSError, ProcessLookupError):
        # PID 파일 있지만 프로세스 없음 → 정리
        try:
            os.remove(_PID_PATH)
        except OSError:
            pass
        return False


def _start_scheduler():
    """auto_report.py를 백그라운드로 실행"""
    # 이전 stop 시그널 제거
    if os.path.exists(_STOP_PATH):
        os.remove(_STOP_PATH)
    script = os.path.join(_BASE_DIR, "auto_report.py")
    proc = subprocess.Popen(
        [sys.executable, script],
        cwd=_BASE_DIR,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with open(_PID_PATH, "w") as f:
        f.write(str(proc.pid))


def _stop_scheduler():
    """stop 시그널 파일 생성 → auto_report.py가 자체 종료"""
    with open(_STOP_PATH, "w") as f:
        f.write("stop")


# ──────────────────────────────────────────────
# 이메일 발송
# ──────────────────────────────────────────────

def _build_email_body(company_name: str, df: pd.DataFrame, service_name: str = "") -> str:
    """데이터 기반 이메일 본문 HTML 생성"""
    from modules.data_processing import calc_ctr_scalar
    total_cost = df['집행금액'].sum()
    total_send = df['발송량'].sum()
    total_click = df['클릭수'].sum()
    ctr = calc_ctr_scalar(total_click, total_send)
    period = f"{df['날짜'].min().strftime('%Y.%m.%d')} – {df['날짜'].max().strftime('%Y.%m.%d')}"

    signature = ""
    if service_name:
        signature = f"""
        <p style="color: #8B95A1; font-size: 12px; margin-top: 32px;">
            본 리포트는 {service_name}에 의해 자동 생성되었습니다.
        </p>"""

    return f"""
    <html><body style="font-family: 'Pretendard', -apple-system, sans-serif; color: #191F28; line-height: 1.6;">
    <div style="max-width: 600px; margin: 0 auto; padding: 24px;">
        <h2 style="margin-bottom: 4px;">{company_name} · LMS 성과 리포트</h2>
        <p style="color: #8B95A1; font-size: 14px;">{period} · {datetime.now().strftime('%Y년 %m월 %d일')} 생성</p>
        <hr style="border: none; border-top: 1px solid #E5E8EB; margin: 20px 0;">

        <table style="width: 100%; border-collapse: collapse;">
            <tr>
                <td style="padding: 12px; text-align: center; background: #F4F5F7; border-radius: 8px;">
                    <div style="font-size: 12px; color: #8B95A1;">집행금액</div>
                    <div style="font-size: 20px; font-weight: 800;">{total_cost:,.0f}원</div>
                </td>
                <td style="width: 8px;"></td>
                <td style="padding: 12px; text-align: center; background: #F4F5F7; border-radius: 8px;">
                    <div style="font-size: 12px; color: #8B95A1;">발송량</div>
                    <div style="font-size: 20px; font-weight: 800;">{total_send:,.0f}건</div>
                </td>
                <td style="width: 8px;"></td>
                <td style="padding: 12px; text-align: center; background: #F4F5F7; border-radius: 8px;">
                    <div style="font-size: 12px; color: #8B95A1;">클릭수</div>
                    <div style="font-size: 20px; font-weight: 800;">{total_click:,.0f}회</div>
                </td>
                <td style="width: 8px;"></td>
                <td style="padding: 12px; text-align: center; background: #F4F5F7; border-radius: 8px;">
                    <div style="font-size: 12px; color: #8B95A1;">CTR</div>
                    <div style="font-size: 20px; font-weight: 800;">{ctr:.2f}%</div>
                </td>
            </tr>
        </table>

        <p style="margin-top: 24px;">상세 분석 리포트가 PPTX 파일로 첨부되어 있습니다.</p>{signature}
    </div>
    </body></html>
    """


def send_email(
    config: dict,
    company_name: str,
    df: pd.DataFrame,
    pptx_bytes: bytes | None = None,
    pdf_bytes: bytes | None = None,
    service_name: str = "",
) -> tuple[bool, str]:
    """PPTX + PDF를 첨부하여 이메일 발송. (성공여부, 메시지) 반환."""
    try:
        recipients = [r.strip() for r in config["recipients"].split(",") if r.strip()]
        if not recipients:
            return False, "수신자 이메일이 입력되지 않았습니다."

        msg = MIMEMultipart("mixed")
        msg["From"] = config["sender_email"]
        msg["To"] = ", ".join(recipients)
        msg["Subject"] = f"[{company_name}] LMS 성과 분석 리포트 · {datetime.now().strftime('%Y년 %m월')}"

        # HTML 본문
        body = _build_email_body(company_name, df, service_name=service_name)
        msg.attach(MIMEText(body, "html", "utf-8"))

        # PPTX 첨부
        if pptx_bytes:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(pptx_bytes)
            encoders.encode_base64(part)
            filename = f"lms_report_{datetime.now():%Y%m%d}.pptx"
            part.add_header("Content-Disposition", f"attachment; filename={filename}")
            msg.attach(part)

        # PDF 원페이저 첨부
        if pdf_bytes:
            part_pdf = MIMEBase("application", "pdf")
            part_pdf.set_payload(pdf_bytes)
            encoders.encode_base64(part_pdf)
            pdf_name = f"{company_name or 'LMS'}_onepager_{datetime.now():%Y%m%d}.pdf"
            part_pdf.add_header("Content-Disposition", f"attachment; filename={pdf_name}")
            msg.attach(part_pdf)

        # SMTP 발송
        server = smtplib.SMTP(config["smtp_server"], config["smtp_port"])
        server.starttls()
        server.login(config["sender_email"], config["sender_password"])
        server.sendmail(config["sender_email"], recipients, msg.as_string())
        server.quit()

        attachments = []
        if pptx_bytes:
            attachments.append("PPTX")
        if pdf_bytes:
            attachments.append("PDF")
        att_str = f" (첨부: {', '.join(attachments)})" if attachments else ""
        return True, f"{len(recipients)}명에게 발송 완료{att_str}"

    except smtplib.SMTPAuthenticationError:
        return False, "SMTP 인증 실패 — 이메일/비밀번호(앱 비밀번호)를 확인하세요."
    except Exception as e:
        return False, f"발송 실패: {str(e)}"


# ──────────────────────────────────────────────
# UI 렌더
# ──────────────────────────────────────────────

def render(df: pd.DataFrame):
    st.markdown(section_header(
        "정기 리포트 이메일 발송",
        "PPTX 리포트를 자동 생성하여 클라이언트에게 이메일로 발송합니다.",
    ), unsafe_allow_html=True)

    config = _load_config()

    # 설정 UI
    with st.expander("📧 이메일 발송 설정", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            config["smtp_server"] = st.text_input("SMTP 서버", value=config["smtp_server"])
            config["smtp_port"] = st.number_input("포트", value=config["smtp_port"], step=1)
        with col2:
            config["sender_email"] = st.text_input("발신자 이메일", value=config["sender_email"])
            config["sender_password"] = st.text_input("앱 비밀번호", value=config["sender_password"], type="password")

        config["recipients"] = st.text_input(
            "수신자 이메일 (쉼표로 구분)",
            value=config["recipients"],
            placeholder="client@example.com, manager@example.com",
        )

        col_s1, col_s2, col_s3 = st.columns([2, 1, 2])
        with col_s1:
            config["schedule"] = st.selectbox(
                "발송 스케줄",
                ["매주 월요일", "매월 1일", "매월 15일", "수동 발송만"],
                index=["매주 월요일", "매월 1일", "매월 15일", "수동 발송만"].index(config.get("schedule", "매월 1일")),
            )
        with col_s2:
            config["send_hour"] = st.number_input(
                "발송 시각",
                min_value=0, max_value=23, value=int(config.get("send_hour", 9)),
                help="자동 발송 시각 (0~23시)",
            )
        with col_s3:
            config["service_name"] = st.text_input(
                "서비스명 (화이트라벨)",
                value=config.get("service_name", ""),
                placeholder="예: Braincube AI Marketing Solutions",
                help="PPTX 표지 & 이메일 서명에 표시됩니다. 비워두면 표시하지 않습니다.",
            )

        if st.button("설정 저장"):
            config["company_name"] = st.session_state.get("company", "")
            _save_config(config)
            _save_report_data(df)
            st.success("설정이 저장되었습니다. (데이터 스냅샷도 저장됨)")

    # Gmail 가이드
    st.markdown(alert_card_html(
        "info",
        "Gmail SMTP 설정 가이드",
        "Gmail 사용 시: SMTP 서버 <b>smtp.gmail.com</b> / 포트 <b>587</b><br>"
        "→ Google 계정 > 보안 > 2단계 인증 > <b>앱 비밀번호</b> 생성 후 입력하세요. (일반 비밀번호는 차단됩니다)",
    ), unsafe_allow_html=True)

    # 발송 미리보기
    st.markdown('<div style="margin-top:20px"></div>', unsafe_allow_html=True)
    _recip_str = config.get("recipients", "") or ""
    recipients_list = [r.strip() for r in str(_recip_str).split(",") if r.strip()]
    company_name = st.session_state.get("company", "")
    service_name = config.get("service_name", "")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(kpi_card_html("수신자", f"{len(recipients_list)}명"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card_html("발송 스케줄", config["schedule"]), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card_html("첨부 파일", "PPTX + PDF"), unsafe_allow_html=True)

    # 첨부 옵션
    att_col1, att_col2 = st.columns(2)
    with att_col1:
        attach_pptx = st.checkbox("PPTX 리포트 첨부", value=True, key="att_pptx")
    with att_col2:
        attach_pdf = st.checkbox("원페이저 PDF 첨부", value=True, key="att_pdf")

    # 즉시 발송 버튼
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("📤 지금 리포트 발송하기", type="primary"):
        if not config["sender_email"] or not config["sender_password"]:
            st.error("발신자 이메일과 앱 비밀번호를 먼저 설정해 주세요.")
        elif not recipients_list:
            st.error("수신자 이메일을 입력해 주세요.")
        else:
            with st.spinner("리포트 생성 및 발송 중..."):
                pptx_bytes = None
                pdf_bytes = None

                if attach_pptx:
                    try:
                        from modules.report import generate_pptx
                        pptx_bytes = generate_pptx(df, company_name, service_name=service_name)
                    except Exception as e:
                        st.warning(f"PPTX 생성 실패: {e}")

                if attach_pdf:
                    try:
                        from modules.onepager_pdf import generate_onepager
                        pdf_bytes = generate_onepager(df, company_name, service_name=service_name)
                    except Exception as e:
                        st.warning(f"PDF 생성 실패: {e}")

                ok, msg = send_email(config, company_name, df, pptx_bytes, pdf_bytes, service_name=service_name)
                if ok:
                    st.markdown(alert_card_html(
                        "success", "발송 완료",
                        f"{msg} · {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    ), unsafe_allow_html=True)
                else:
                    st.markdown(alert_card_html("danger", "발송 실패", msg), unsafe_allow_html=True)

    # ── 자동 발송 스케줄러 ──
    st.divider()
    st.markdown(section_header(
        "자동 발송 스케줄러",
        "설정된 스케줄에 따라 백그라운드에서 리포트를 자동 생성·발송합니다.",
    ), unsafe_allow_html=True)

    scheduler_running = _is_scheduler_running()
    has_data = os.path.exists(_DATA_PATH)
    has_smtp = bool(config.get("sender_email") and config.get("sender_password"))

    # 상태 표시
    if scheduler_running:
        st.markdown(alert_card_html(
            "success", "스케줄러 실행 중",
            f"스케줄: <b>{config['schedule']}</b> · 수신자: <b>{len(recipients_list)}명</b>",
        ), unsafe_allow_html=True)
    else:
        st.markdown(alert_card_html(
            "info", "스케줄러 중지됨",
            "아래 버튼으로 스케줄러를 시작할 수 있습니다.",
        ), unsafe_allow_html=True)

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        if st.button(
            "🛑 스케줄러 중지" if scheduler_running else "🚀 스케줄러 시작",
            type="secondary" if scheduler_running else "primary",
            disabled=(not has_data or not has_smtp) and not scheduler_running,
        ):
            if scheduler_running:
                _stop_scheduler()
                st.rerun()
            else:
                _start_scheduler()
                st.rerun()
    with col_b:
        if st.button("🔄 테스트 발송 (즉시 1회)", disabled=not has_data or not has_smtp):
            script = os.path.join(_BASE_DIR, "auto_report.py")
            result = subprocess.run(
                [sys.executable, script, "--once"],
                cwd=_BASE_DIR,
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                st.success("테스트 발송 완료")
            else:
                st.error(f"테스트 발송 실패: {result.stderr or result.stdout}")

    if not has_data:
        st.caption("⚠️ 먼저 '설정 저장'을 눌러 데이터 스냅샷을 저장해야 자동 발송이 가능합니다.")
    if not has_smtp:
        st.caption("⚠️ SMTP 이메일/비밀번호를 설정해 주세요.")

    st.divider()
