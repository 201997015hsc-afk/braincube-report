"""
UI 공용 헬퍼 — Loading state, Empty state, 데이터 신선도 배지, 툴팁 텍스트.

모든 페이지에서 일관된 UX를 제공하기 위한 재사용 컴포넌트.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import streamlit as st


# ──────────────────────────────────────────────
# Loading State
# ──────────────────────────────────────────────
@contextmanager
def with_spinner(message: str = "불러오는 중..."):
    """Streamlit 스피너 래퍼. 예외 발생 시에도 안전하게 동작.

    사용:
        with with_spinner("Firebase 데이터 로드 중..."):
            data = load_from_firestore()
    """
    try:
        with st.spinner(message):
            yield
    except Exception:
        # 스피너 컨텍스트는 종료됐지만 예외는 상위로 전파
        raise


# ──────────────────────────────────────────────
# Empty State
# ──────────────────────────────────────────────
def render_empty_state(
    title: str,
    description: str = "",
    *,
    icon: str = "📭",
    actions: Optional[list[tuple[str, str]]] = None,
):
    """데이터가 없을 때 사용자에게 다음 액션을 안내하는 통일된 empty state.

    Args:
        title: 메인 메시지 (예: "분석할 데이터가 없습니다")
        description: 보조 설명
        icon: 왼쪽 이모지
        actions: [(label, hint), ...] 형태의 액션 안내 리스트
                 예: [("엑셀 업로드", "왼쪽 사이드바 → 데이터 업로드"),
                      ("Firebase 연결", "관리자에게 문의")]
    """
    from modules.config import BRAND_PRIMARY, COLOR_BORDER, COLOR_TEXT, COLOR_TEXT_SEC

    action_html = ""
    if actions:
        items = "".join(
            f'<li style="margin:6px 0"><b>{label}</b>'
            + (f' <span style="color:{COLOR_TEXT_SEC};font-weight:400">— {hint}</span>' if hint else "")
            + '</li>'
            for label, hint in actions
        )
        action_html = (
            f'<ul style="margin:10px 0 0 0;padding-left:20px;'
            f'font-size:.85rem;color:{COLOR_TEXT};">{items}</ul>'
        )

    st.markdown(
        f'<div style="background:#FFFFFF;border:1px dashed {COLOR_BORDER};'
        f'border-radius:16px;padding:28px 32px;margin:12px 0;text-align:left;">'
        f'  <div style="display:flex;align-items:center;gap:12px;margin-bottom:6px;">'
        f'    <div style="font-size:1.8rem;">{icon}</div>'
        f'    <div style="font-weight:700;font-size:1rem;color:{COLOR_TEXT};">{title}</div>'
        f'  </div>'
        + (f'  <div style="font-size:.85rem;color:{COLOR_TEXT_SEC};line-height:1.5;">{description}</div>' if description else '')
        + action_html
        + '</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────
# 데이터 신선도 배지
# ──────────────────────────────────────────────
def _format_relative_time(dt: datetime) -> str:
    """datetime 객체 → '방금 전', '10분 전', '2시간 전', '어제', '3일 전' 등."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    secs = int((now - dt).total_seconds())
    if secs < 0:
        return "방금"
    if secs < 60:
        return "방금 전"
    mins = secs // 60
    if mins < 60:
        return f"{mins}분 전"
    hours = mins // 60
    if hours < 24:
        return f"{hours}시간 전"
    days = hours // 24
    if days == 1:
        return "어제"
    if days < 7:
        return f"{days}일 전"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks}주 전"
    months = days // 30
    if months < 12:
        return f"{months}개월 전"
    return f"{days // 365}년 전"


def render_freshness_badge(
    last_updated: Optional[datetime] = None,
    *,
    source: str = "Firebase",
    show_absolute: bool = True,
):
    """'📡 최신 데이터: 10분 전 (Firebase)' 배지.

    last_updated가 None이면 '시각 미상'으로 표시.
    사이드바/헤더에서 호출.
    """
    from modules.config import COLOR_TEXT_SEC, COLOR_SUCCESS, COLOR_WARNING

    if last_updated is None:
        rel = "시각 미상"
        color = COLOR_TEXT_SEC
        abs_str = ""
    else:
        rel = _format_relative_time(last_updated)
        # 7일 이상 지났으면 경고 색상
        age_days = (datetime.now(timezone.utc) - (
            last_updated if last_updated.tzinfo else last_updated.replace(tzinfo=timezone.utc)
        )).days
        color = COLOR_WARNING if age_days >= 7 else COLOR_SUCCESS
        abs_str = (
            f' <span style="color:{COLOR_TEXT_SEC};font-size:.70rem">'
            f'({last_updated.strftime("%Y-%m-%d %H:%M")})</span>'
            if show_absolute else ""
        )

    st.markdown(
        f'<div style="font-size:.75rem;color:{COLOR_TEXT_SEC};'
        f'padding:6px 10px;background:rgba(0,0,0,.02);border-radius:8px;'
        f'margin:4px 0;">'
        f'📡 {source} 최신 데이터: '
        f'<b style="color:{color}">{rel}</b>{abs_str}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────
# 공통 필터 help 텍스트 (일관성 확보용)
# ──────────────────────────────────────────────
HELP_TEXT = {
    "industry": "업종을 선택하면 해당 분야 벤치마크와 비교 분석이 활성화됩니다.",
    "product": "LMS 외 PUSH, MMS, DA 등을 포함하려면 '전체'를 선택하세요.",
    "advertiser": "특정 광고주만 필터링. '전체'는 업종 내 모든 광고주 포함.",
    "date_range": "분석 대상 기간. 최근 30일이 기본값입니다.",
    "client": "등록된 클라이언트를 선택하면 저장된 데이터가 자동 로드됩니다. '새 업로드'는 일회성 분석용.",
    "firebase_brand": "회사 대시보드 데이터와 자동 연동. 직접계약·대대행 모두 통합 집계됩니다.",
    "ref_period": "기준 기간 — 전월 대비·전기 대비 등 비교 연산의 기준점.",
    "min_sends": "통계적 신뢰도 확보용 최소 발송량 기준. 이 수치 미만은 분석에서 제외.",
}


def help_text(key: str) -> str:
    """표준 help 텍스트 반환. 키가 없으면 빈 문자열."""
    return HELP_TEXT.get(key, "")


# ──────────────────────────────────────────────
# Top Breadcrumb Bar (Linear/Notion 스타일)
# ──────────────────────────────────────────────
def render_topbar(
    context: Optional[str] = None,
    current_page: Optional[str] = None,
    meta_right: Optional[str] = None,
):
    """페이지 상단에 breadcrumb + 우측 메타 정보를 렌더.

    Args:
        context: 왼쪽 경로 첫 부분 (예: 클라이언트명, 광고주명)
        current_page: breadcrumb의 현재 페이지 이름 (강조됨)
        meta_right: 우측 표시할 메타 (예: "Firebase · 2시간 전 · admin")
    """
    from modules.config import COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER_SUBTLE

    left_html = ""
    if context and current_page:
        left_html = (
            f'<span style="color:{COLOR_TEXT_SEC};">{esc_html_safe(context)}</span>'
            f'<span style="margin:0 8px;color:{COLOR_TEXT_SEC};opacity:0.5;">/</span>'
            f'<span style="color:{COLOR_TEXT};font-weight:500;">{esc_html_safe(current_page)}</span>'
        )
    elif current_page:
        left_html = (
            f'<span style="color:{COLOR_TEXT};font-weight:500;">{esc_html_safe(current_page)}</span>'
        )
    elif context:
        left_html = f'<span style="color:{COLOR_TEXT_SEC};">{esc_html_safe(context)}</span>'

    right_html = (
        f'<span style="color:{COLOR_TEXT_TER};">{esc_html_safe(meta_right)}</span>'
        if meta_right else ""
    )

    st.markdown(
        f'<div class="linear-topbar" style="'
        f'display:flex;justify-content:space-between;align-items:center;'
        f'padding:10px 0 14px 0;border-bottom:1px solid {COLOR_BORDER_SUBTLE};'
        f'margin-bottom:20px;font-size:0.82rem;">'
        f'  <div>{left_html}</div>'
        f'  <div>{right_html}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def esc_html_safe(text) -> str:
    """HTML 이스케이프 (로컬 import 회피)."""
    if text is None:
        return ""
    import html as _html
    return _html.escape(str(text), quote=True)


# ──────────────────────────────────────────────
# Linear/Notion 스타일 페이지 헤더
# ──────────────────────────────────────────────
def render_page_header(
    title: str,
    subtitle: Optional[str] = None,
    kicker: Optional[str] = None,
):
    """페이지 상단에 표시할 큰 헤더 (kicker + title + subtitle).

    프리뷰 레퍼런스:
        업종 인사이트             ← kicker (카테고리명, 옅은 회색)
        경쟁사 분석               ← title (크고 굵은)
        분양 업종 · LMS 기준 ...  ← subtitle (설명)
    """
    from modules.config import COLOR_TEXT, COLOR_TEXT_TER, COLOR_TEXT_SEC

    kicker_html = (
        f'<div style="font-size:0.78rem;color:{COLOR_TEXT_TER};font-weight:500;'
        f'margin-bottom:4px;letter-spacing:-0.005em;">{esc_html_safe(kicker)}</div>'
        if kicker else ""
    )
    subtitle_html = (
        f'<div style="font-size:0.92rem;color:{COLOR_TEXT_SEC};line-height:1.55;'
        f'margin-top:6px;">{subtitle}</div>'
        if subtitle else ""
    )

    st.markdown(
        f'<div class="linear-page-head" style="margin-bottom:28px;">'
        f'  {kicker_html}'
        f'  <h1 style="font-size:1.5rem;font-weight:600;color:{COLOR_TEXT};'
        f'letter-spacing:-0.02em;margin:0 0 4px 0;line-height:1.3;">{esc_html_safe(title)}</h1>'
        f'  {subtitle_html}'
        f'</div>',
        unsafe_allow_html=True,
    )
