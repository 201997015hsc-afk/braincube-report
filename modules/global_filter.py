"""
글로벌 필터 바 (Global Filter Bar)
─────────────────────────────────
모든 페이지 공통 상단 필터 — 매체 / 광고상품.
세션 상태로 페이지 이동 시 유지.

기간 필터는 사이드바에 별도 존재 (날짜 picker 형태 유지).

UX:
  - 기본: compact pill 형태 (현재 필터 상태 표시)
  - 클릭 시 expanded: multiselect로 변경 가능
  - 적용 행수 / 전체 행수 함께 표시
  - "필터 초기화" 버튼
"""
import streamlit as st
import pandas as pd

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_BG, BRAND_PRIMARY, COLOR_BLUE,
)
from modules.ui_helpers import esc_html_safe


# 세션 키 (전역 — 페이지 이동해도 유지)
_KEY_MEDIA = '_gf_media'
_KEY_PRODUCT = '_gf_product'
_KEY_EXPANDED = '_gf_expanded'


# ──────────────────────────────────────────────
# 순수 필터 함수 (테스트 용이)
# ──────────────────────────────────────────────
def apply_filters(
    df: pd.DataFrame,
    *,
    media: list[str] | None = None,
    product: list[str] | None = None,
) -> pd.DataFrame:
    """필터 적용. 빈 리스트/None은 무필터(전체)."""
    if df is None or df.empty:
        return df
    out = df
    if media:
        if '매체명' in out.columns:
            out = out[out['매체명'].astype(str).isin(media)]
    if product:
        if '광고상품' in out.columns:
            out = out[out['광고상품'].astype(str).isin(product)]
    return out


def get_active_filters() -> dict:
    """현재 세션의 필터 상태."""
    return {
        'media': st.session_state.get(_KEY_MEDIA, []) or [],
        'product': st.session_state.get(_KEY_PRODUCT, []) or [],
    }


def reset_filters():
    """필터 초기화."""
    for k in (_KEY_MEDIA, _KEY_PRODUCT):
        if k in st.session_state:
            del st.session_state[k]


def is_filtered() -> bool:
    """필터가 활성화 되어 있는지."""
    f = get_active_filters()
    return bool(f['media']) or bool(f['product'])


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────
def _summary_text(active: dict, n_filtered: int, n_total: int) -> str:
    """현재 필터 요약 텍스트."""
    parts = []
    if active['media']:
        if len(active['media']) <= 2:
            parts.append(f'매체 {", ".join(active["media"])}')
        else:
            parts.append(f'매체 {len(active["media"])}개')
    else:
        parts.append('매체 전체')
    if active['product']:
        if len(active['product']) <= 2:
            parts.append(f'상품 {", ".join(active["product"])}')
        else:
            parts.append(f'상품 {len(active["product"])}개')
    else:
        parts.append('상품 전체')
    base = ' · '.join(parts)
    if n_filtered < n_total:
        return f'{base}  →  <b style="color:{COLOR_TEXT};">{n_filtered:,}</b> / {n_total:,}행'
    return f'{base}  →  {n_total:,}행 (전체)'


def render_filter_bar(df: pd.DataFrame) -> pd.DataFrame:
    """필터 바를 렌더링하고 필터된 DataFrame을 반환.

    - 기본은 collapsed compact pill 형태
    - 사용자가 "필터 변경" 클릭하면 multiselect 노출
    - 세션 상태로 페이지 이동 시 유지
    """
    if df is None or df.empty:
        return df

    # 옵션 추출
    media_options = sorted([
        str(m) for m in df['매체명'].dropna().unique() if str(m).strip()
    ]) if '매체명' in df.columns else []
    product_options = sorted([
        str(p) for p in df['광고상품'].dropna().unique() if str(p).strip()
    ]) if '광고상품' in df.columns else []

    # 옵션 없으면 필터바 자체를 안 보임 (정보가 없음)
    if not media_options and not product_options:
        return df

    expanded = st.session_state.get(_KEY_EXPANDED, False)
    active = get_active_filters()

    # 잘못된 값 정리 (옵션에 없는 값이 세션에 남았을 때)
    if active['media']:
        st.session_state[_KEY_MEDIA] = [m for m in active['media'] if m in media_options]
    if active['product']:
        st.session_state[_KEY_PRODUCT] = [p for p in active['product'] if p in product_options]

    # 필터 적용해서 행수 계산
    filtered = apply_filters(df, media=active['media'], product=active['product'])
    n_filtered = len(filtered)
    n_total = len(df)

    # ── compact 헤더 ──
    summary = _summary_text(active, n_filtered, n_total)
    badge = '필터 적용 중' if is_filtered() else '필터 없음'
    badge_color = BRAND_PRIMARY if is_filtered() else COLOR_TEXT_TER
    badge_bg = '#FFF6E8' if is_filtered() else COLOR_BG

    col_summary, col_toggle = st.columns([5, 1])
    with col_summary:
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;'
            f'border:1px solid {COLOR_BORDER};border-radius:8px;'
            f'padding:8px 14px;background:{COLOR_CARD};margin-bottom:8px;">'
            f'  <span style="font-size:0.68rem;font-weight:600;color:{badge_color};'
            f'background:{badge_bg};padding:3px 10px;border-radius:999px;'
            f'letter-spacing:-0.005em;">{badge}</span>'
            f'  <span style="font-size:0.78rem;color:{COLOR_TEXT_SEC};">{summary}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    with col_toggle:
        toggle_label = '필터 닫기' if expanded else '필터 변경'
        if st.button(toggle_label, use_container_width=True, key='_gf_toggle_btn'):
            st.session_state[_KEY_EXPANDED] = not expanded
            st.rerun()

    # ── expanded 영역 ──
    if expanded:
        with st.container(border=True):
            cols = []
            if media_options:
                cols.append('media')
            if product_options:
                cols.append('product')
            cols.append('reset')

            ncols = st.columns(len(cols))
            i = 0
            if 'media' in cols:
                with ncols[i]:
                    st.multiselect(
                        '매체',
                        options=media_options,
                        default=active['media'],
                        key=_KEY_MEDIA,
                        placeholder=f'전체 ({len(media_options)}개)',
                    )
                i += 1
            if 'product' in cols:
                with ncols[i]:
                    st.multiselect(
                        '광고상품',
                        options=product_options,
                        default=active['product'],
                        key=_KEY_PRODUCT,
                        placeholder=f'전체 ({len(product_options)}개)',
                    )
                i += 1
            with ncols[i]:
                st.markdown(
                    f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:8px;">초기화</div>',
                    unsafe_allow_html=True,
                )
                if st.button(
                    '모든 필터 해제',
                    use_container_width=True,
                    disabled=not is_filtered(),
                    key='_gf_reset_btn',
                ):
                    reset_filters()
                    st.rerun()

    # ── 필터 재적용 (multiselect 변경 즉시 반영) ──
    new_active = get_active_filters()
    if new_active != active:
        filtered = apply_filters(df, media=new_active['media'], product=new_active['product'])

    return filtered
