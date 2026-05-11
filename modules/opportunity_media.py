"""
기회 매체 (Opportunity Media)
─────────────────────────────
현재 광고주가 아직 시도하지 않은 매체를 보여주고,
같은 매체를 집행한 타사 레퍼런스(평균 발송량/광고비/CTR + 대표 광고주)를 함께 제시.

데이터 소스: Firebase Firestore 'campaigns' 컬렉션 (최근 6개월)
정렬 기준: 동종업계 광고주 수 ▶ 전체 광고주 수 ▶ 캠페인 수
"""
import streamlit as st
import pandas as pd

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_SUCCESS, COLOR_WARNING, COLOR_BG,
    BRAND_PRIMARY, COLOR_BLUE, compact_num, esc_html,
)
from modules.ui_helpers import render_page_header, render_empty_state, esc_html_safe
from modules.firebase_connector import load_opportunity_media, load_from_firestore


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
def _detect_current_advertiser(df: pd.DataFrame) -> str | None:
    """현재 보고 있는 광고주(브랜드)명 추정.
    1) df['_브랜드'] 첫 행 (Firebase 모드)
    2) st.session_state firebase_advertiser
    3) 없으면 None
    """
    if df is not None and not df.empty and '_브랜드' in df.columns:
        try:
            vals = df['_브랜드'].dropna().astype(str)
            if not vals.empty:
                return vals.iloc[0]
        except Exception:
            pass
    # 세션 폴백
    try:
        sk = st.session_state.get('_data_key', '') or ''
        # _data_key 형식: 'firebase_<client_id>_<adv_name>'
        if sk.startswith('firebase_'):
            parts = sk.split('_', 2)
            if len(parts) >= 3:
                return parts[2]
    except Exception:
        pass
    return None


def _detect_industry(df: pd.DataFrame) -> str:
    """현재 광고주의 대표 업종."""
    if df is None or df.empty or '분야' not in df.columns:
        return ''
    counts = df['분야'].dropna().astype(str).str.strip().value_counts()
    counts = counts[counts.index != '']
    if counts.empty:
        return ''
    return str(counts.index[0])


def _badge(text: str, *, tone: str = 'neutral') -> str:
    """작은 상태 배지 HTML."""
    tones = {
        'success': (COLOR_SUCCESS, '#E8F7F0'),
        'info':    (COLOR_BLUE,    '#EAF3FF'),
        'warn':    (COLOR_WARNING, '#FFF6E5'),
        'neutral': (COLOR_TEXT_SEC, COLOR_BORDER_SUBTLE),
    }
    fg, bg = tones.get(tone, tones['neutral'])
    return (
        f'<span style="display:inline-block;font-size:0.68rem;font-weight:600;'
        f'color:{fg};background:{bg};padding:2px 8px;border-radius:999px;'
        f'letter-spacing:-0.005em;line-height:1.5;">{esc_html_safe(text)}</span>'
    )


def _stat_block(label: str, value: str, *, sub: str = '') -> str:
    """카드 안의 작은 메트릭 블록."""
    sub_html = (
        f'<div style="font-size:0.66rem;color:{COLOR_TEXT_TER};margin-top:1px;">{esc_html_safe(sub)}</div>'
        if sub else ''
    )
    return (
        f'<div style="display:flex;flex-direction:column;gap:0;">'
        f'  <div style="font-size:0.66rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;margin-bottom:3px;">{esc_html_safe(label)}</div>'
        f'  <div style="font-size:0.95rem;color:{COLOR_TEXT};font-weight:600;line-height:1.2;">'
        f'{esc_html_safe(value)}</div>'
        f'{sub_html}'
        f'</div>'
    )


def _render_media_card(row: pd.Series, my_industry: str) -> None:
    """매체 한 줄 카드 (흰 배경 + 좌측 3px 컬러 액센트 + 메트릭 4분할)."""
    is_same_ind = bool(row.get('동종업계_여부', False))
    accent = BRAND_PRIMARY if is_same_ind else COLOR_TEXT_SEC

    media_name = str(row.get('매체명', ''))
    prod = str(row.get('광고상품', '') or '')
    n_brands = int(row.get('광고주수', 0))
    n_same_brands = int(row.get('동종업계_광고주수', 0))
    n_camp = int(row.get('총_캠페인수', 0))
    avg_sends = float(row.get('평균_발송량', 0) or 0)
    avg_cost = float(row.get('평균_광고비', 0) or 0)
    avg_ctr = float(row.get('평균_CTR', 0) or 0)

    rep_all = row.get('대표_광고주', []) or []
    rep_same = row.get('대표_광고주_동종업계', []) or []

    # 배지: 동종업계 / 신규 매체 등
    badges = []
    if is_same_ind and my_industry:
        badges.append(_badge(f'{my_industry} 집행', tone='success'))
    if n_brands >= 10:
        badges.append(_badge('인기 매체', tone='info'))
    elif n_brands <= 2:
        badges.append(_badge('소수 집행', tone='warn'))
    if prod:
        badges.append(_badge(prod, tone='neutral'))
    badges_html = ' '.join(badges) if badges else ''

    # 대표 광고주 — 동종업계 우선, 없으면 전체
    rep_label = '동종업계 레퍼런스' if rep_same else '집행 레퍼런스'
    rep_list = rep_same if rep_same else rep_all
    rep_chips = ' '.join(
        f'<span style="display:inline-block;font-size:0.72rem;color:{COLOR_TEXT};'
        f'background:{COLOR_BG};border:1px solid {COLOR_BORDER_SUBTLE};'
        f'padding:3px 8px;border-radius:6px;margin-right:4px;margin-top:3px;">'
        f'{esc_html_safe(str(b))}</span>'
        for b in rep_list[:3]
    ) or f'<span style="color:{COLOR_TEXT_TER};font-size:0.72rem;">집행 이력 없음</span>'

    # CTR 표시는 메시징 캠페인에 한정해서만 유의미 (DA만 있는 매체는 0)
    ctr_str = f'{avg_ctr:.2f}%' if avg_ctr > 0 else '—'

    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {accent};'
        f'border-radius:8px;padding:14px 18px;background:{COLOR_CARD};margin-bottom:10px;">'
        # 상단: 매체명 + 배지
        f'  <div style="display:flex;align-items:center;justify-content:space-between;'
        f'gap:12px;margin-bottom:10px;flex-wrap:wrap;">'
        f'    <div style="font-size:1rem;font-weight:600;color:{COLOR_TEXT};letter-spacing:-0.01em;">'
        f'{esc_html_safe(media_name)}</div>'
        f'    <div style="display:flex;gap:6px;flex-wrap:wrap;">{badges_html}</div>'
        f'  </div>'
        # 중단: 4분할 메트릭
        f'  <div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;'
        f'padding:10px 0 12px 0;border-top:1px solid {COLOR_BORDER_SUBTLE};'
        f'border-bottom:1px solid {COLOR_BORDER_SUBTLE};margin-bottom:10px;">'
        f'    {_stat_block("집행 광고주", f"{n_brands}곳", sub=(f"업종 내 {n_same_brands}곳" if my_industry else f"캠페인 {n_camp}건"))}'
        f'    {_stat_block("평균 발송량", compact_num(avg_sends, "건") if avg_sends > 0 else "—", sub="캠페인당")}'
        f'    {_stat_block("평균 광고비", compact_num(avg_cost, "원") if avg_cost > 0 else "—", sub="캠페인당")}'
        f'    {_stat_block("평균 CTR", ctr_str, sub="메시징 기준")}'
        f'  </div>'
        # 하단: 대표 광고주
        f'  <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">'
        f'    <div style="font-size:0.7rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;">{rep_label}</div>'
        f'    <div>{rep_chips}</div>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────
def render(df: pd.DataFrame):
    adv_name = _detect_current_advertiser(df)
    if not adv_name:
        render_empty_state(
            '광고주 정보를 인식할 수 없습니다',
            '기회 매체 분석은 Firebase 연동된 브랜드에서만 동작합니다. '
            '사이드바에서 클라이언트(브랜드 연결됨)를 선택해주세요.',
            icon='🔎',
        )
        return

    # 현재 광고주 업종 — Firebase 원본에서 우선 조회, fallback은 df
    full = load_from_firestore()
    industry = ''
    if full is not None and not full.empty and '_브랜드' in full.columns and '분야' in full.columns:
        _me = full[full['_브랜드'] == adv_name]
        if not _me.empty:
            _ic = _me['분야'].dropna().astype(str).str.strip().value_counts()
            _ic = _ic[_ic.index != '']
            if not _ic.empty:
                industry = str(_ic.index[0])
    if not industry:
        industry = _detect_industry(df)

    n_used = df['매체명'].nunique() if (df is not None and '매체명' in df.columns) else 0

    render_page_header(
        title='기회 매체',
        kicker='업종 인사이트',
        subtitle=(
            f'<b>{esc_html_safe(adv_name)}</b>이(가) 아직 시도하지 않은 매체와 '
            f'타사 집행 레퍼런스. 현재 집행 매체 <b>{n_used}개</b>'
            + (f' · 업종 <b>{esc_html_safe(industry)}</b>' if industry else '')
        ),
    )

    # 로드
    opp = load_opportunity_media(adv_name, months_back=6)
    if opp is None:
        render_empty_state(
            'Firebase 데이터를 불러올 수 없습니다',
            '연결 설정을 확인해주세요.',
            icon='🔌',
        )
        return
    if opp.empty:
        render_empty_state(
            '미집행 매체가 없습니다',
            f'{esc_html_safe(adv_name)}은(는) 최근 6개월 기준 Firebase에 등록된 모든 매체를 이미 집행했어요. 🎉',
            icon='✨',
        )
        return

    # ── 필터 옵션 ──
    same_ind_count = int(opp['동종업계_여부'].sum())
    total_count = len(opp)
    options = [f'전체 ({total_count}개)']
    if industry and same_ind_count > 0:
        options.insert(0, f'{industry} 집행한 매체만 ({same_ind_count}개)')

    col_filter, col_meta = st.columns([2, 1])
    with col_filter:
        sel = st.radio(
            '보기 필터',
            options=options,
            horizontal=True,
            label_visibility='collapsed',
            key='opp_media_filter',
        )
    with col_meta:
        st.markdown(
            f'<div style="text-align:right;font-size:0.78rem;color:{COLOR_TEXT_SEC};'
            f'padding-top:6px;">최근 6개월 · 동종업계 우선 정렬</div>',
            unsafe_allow_html=True,
        )

    # 필터 적용
    view = opp.copy()
    if sel.startswith(industry) and industry:
        view = view[view['동종업계_여부']]

    if view.empty:
        render_empty_state(
            '조건에 맞는 매체가 없습니다',
            '필터를 "전체"로 바꿔보세요.',
            icon='📭',
        )
        return

    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    # ── 요약 통계 ──
    n_rows = len(view)
    n_same = int(view['동종업계_여부'].sum())
    sum_brands = int(view['광고주수'].sum())
    avg_cost_med = float(view[view['평균_광고비'] > 0]['평균_광고비'].median() or 0)

    c1, c2, c3, c4 = st.columns(4)
    for col, (label, value, sub) in zip(
        [c1, c2, c3, c4],
        [
            ('미집행 매체', f'{n_rows}개', '아직 안 써본 매체'),
            ('동종업계 집행', f'{n_same}개', f'{industry} 광고주가 사용' if industry else '업종 정보 없음'),
            ('레퍼런스 광고주', f'{sum_brands}곳', '집행 실적 보유'),
            ('평균 광고비(중앙값)', compact_num(avg_cost_med, '원') if avg_cost_med > 0 else '—', '캠페인 1건당'),
        ],
    ):
        with col:
            st.markdown(
                f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;'
                f'padding:14px 16px;background:{COLOR_CARD};">'
                f'  <div style="font-size:0.7rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
                f'letter-spacing:0.04em;font-weight:600;margin-bottom:6px;">{esc_html_safe(label)}</div>'
                f'  <div style="font-size:1.2rem;color:{COLOR_TEXT};font-weight:600;line-height:1.2;">'
                f'{esc_html_safe(value)}</div>'
                f'  <div style="font-size:0.7rem;color:{COLOR_TEXT_TER};margin-top:4px;">'
                f'{esc_html_safe(sub)}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    st.markdown('<div class="space-lg"></div>', unsafe_allow_html=True)

    # ── 매체 카드 리스트 ──
    st.markdown(
        f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:6px;letter-spacing:-0.01em;">매체별 레퍼런스</div>'
        f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:14px;">'
        f'동종업계 광고주가 집행한 매체부터 정렬됩니다. '
        f'평균값은 캠페인 1건당 기준이에요.</div>',
        unsafe_allow_html=True,
    )

    # 한 번에 너무 많으면 부담 — 30개까지 카드로, 나머지는 표로
    CARD_LIMIT = 30
    head = view.head(CARD_LIMIT)
    tail = view.iloc[CARD_LIMIT:]

    for _, row in head.iterrows():
        _render_media_card(row, industry)

    if not tail.empty:
        with st.expander(f'기타 매체 {len(tail)}개 (표 형태로 보기)'):
            display = tail[[
                '매체명', '광고상품', '광고주수', '동종업계_광고주수',
                '총_캠페인수', '평균_발송량', '평균_광고비', '평균_CTR',
            ]].copy()
            display.columns = [
                '매체명', '광고상품', '집행 광고주', '동종업계 광고주',
                '캠페인수', '평균 발송량', '평균 광고비', '평균 CTR(%)',
            ]
            display['평균 발송량'] = display['평균 발송량'].apply(lambda v: f'{v:,.0f}')
            display['평균 광고비'] = display['평균 광고비'].apply(lambda v: f'{v:,.0f}')
            display['평균 CTR(%)'] = display['평균 CTR(%)'].apply(lambda v: f'{v:.2f}' if v > 0 else '—')
            st.dataframe(display, use_container_width=True, hide_index=True)

    # ── 하단 안내 ──
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER_SUBTLE};border-radius:8px;'
        f'padding:12px 14px;background:{COLOR_BG};font-size:0.78rem;color:{COLOR_TEXT_SEC};'
        f'line-height:1.55;">'
        f'<b style="color:{COLOR_TEXT};">활용 팁</b><br>'
        f'• <b>동종업계 집행</b> 배지가 있는 매체는 같은 업종 광고주가 이미 검증한 채널이에요.<br>'
        f'• 평균 발송량/광고비는 캠페인 1건당 평균 — 실제 집행 규모 가늠용 참고치입니다.<br>'
        f'• CTR은 메시징(LMS/MMS) 캠페인 기준이며, DA/CPA만 있는 매체는 "—"로 표시됩니다.'
        f'</div>',
        unsafe_allow_html=True,
    )
