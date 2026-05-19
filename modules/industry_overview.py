"""
업종 · 경쟁 — 개요 (Industry & Competitor Overview)
─────────────────────────────────────────────────
업종·경쟁 페이지의 진입 화면.

답하는 질문:
  - 우리는 업종 내에서 어디쯤인가? (CTR 백분위)
  - 동종업계는 어떤 매체를 많이 쓰나? (우리가 안 쓰는 것 포함)
  - 누가 제일 잘하나? (Top 광고주)
  - 다음에 뭘 검토하면 좋을까?
"""
import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_BG, COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING, COLOR_BLUE,
    BRAND_PRIMARY, compact_num, MIN_SENDS_FOR_CTR_CLAIM,
)
from modules.data_processing import calc_ctr_scalar
from modules.ui_helpers import render_page_header, render_empty_state, esc_html_safe
from modules.firebase_connector import (
    load_from_firestore, get_benchmark_stats, calc_percentile,
)


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
def _detect_advertiser(df: pd.DataFrame) -> str:
    for col in ('_브랜드', '광고주', '대행사'):
        if col in df.columns:
            vals = df[col].dropna().astype(str)
            vals = vals[vals.str.strip() != '']
            if not vals.empty:
                return str(vals.iloc[0])
    return ''


def _detect_industry(df: pd.DataFrame) -> str:
    if '분야' not in df.columns:
        return ''
    counts = df['분야'].dropna().astype(str).str.strip().value_counts()
    counts = counts[counts.index != '']
    return str(counts.index[0]) if not counts.empty else ''


def _summary_card(title: str, value: str, sub: str, *, color: str = None) -> str:
    color = color or COLOR_TEXT
    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;padding:12px 14px;'
        f'background:{COLOR_CARD};">'
        f'  <div style="font-size:0.65rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;margin-bottom:4px;">{esc_html_safe(title)}</div>'
        f'  <div style="font-size:0.95rem;color:{color};font-weight:700;line-height:1.2;">'
        f'{esc_html_safe(value)}</div>'
        f'  <div style="font-size:0.7rem;color:{COLOR_TEXT_TER};margin-top:3px;line-height:1.4;">'
        f'{esc_html_safe(sub)}</div>'
        f'</div>'
    )


def _media_compare_row(media: str, our_use: bool, ind_brands: int, *, sub: str = '') -> str:
    """동종업계 매체 한 줄 — 우리가 쓰는지 + 동종업계 몇 곳이 쓰는지."""
    if our_use:
        badge = (
            f'<span style="font-size:0.65rem;color:{COLOR_SUCCESS};'
            f'background:#E8F7F0;padding:2px 8px;border-radius:999px;font-weight:600;">우리 집행 중</span>'
        )
        accent = COLOR_SUCCESS
    else:
        badge = (
            f'<span style="font-size:0.65rem;color:{BRAND_PRIMARY};'
            f'background:#FFF6E8;padding:2px 8px;border-radius:999px;font-weight:600;">미집행 — 기회 매체</span>'
        )
        accent = BRAND_PRIMARY

    sub_html = f'<div style="font-size:0.7rem;color:{COLOR_TEXT_TER};margin-top:3px;">{esc_html_safe(sub)}</div>' if sub else ''

    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {accent};'
        f'border-radius:8px;padding:10px 14px;background:{COLOR_CARD};margin-bottom:6px;">'
        f'  <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;">'
        f'    <div style="flex:1;">'
        f'      <div style="font-size:0.88rem;color:{COLOR_TEXT};font-weight:600;letter-spacing:-0.005em;">'
        f'{esc_html_safe(media)}</div>'
        f'      {sub_html}'
        f'    </div>'
        f'    <div style="text-align:right;">'
        f'      <div style="font-size:0.95rem;color:{COLOR_TEXT};font-weight:700;line-height:1;">{ind_brands}곳</div>'
        f'      <div style="font-size:0.65rem;color:{COLOR_TEXT_TER};margin-top:2px;">동종업계 집행</div>'
        f'    </div>'
        f'    <div>{badge}</div>'
        f'  </div>'
        f'</div>'
    )


def _percentile_chip(label: str, pct: float | None) -> str:
    """백분위 칩."""
    if pct is None:
        return (
            f'<span style="font-size:0.7rem;color:{COLOR_TEXT_TER};background:{COLOR_BG};'
            f'padding:3px 8px;border-radius:6px;">{esc_html_safe(label)} 측정 불가</span>'
        )
    if pct >= 75:
        color, bg, tier = COLOR_SUCCESS, '#E8F7F0', f'상위 {100-pct:.0f}%'
    elif pct >= 50:
        color, bg, tier = COLOR_BLUE, '#EAF3FF', f'상위 {100-pct:.0f}%'
    elif pct >= 25:
        color, bg, tier = COLOR_WARNING, '#FFF6E5', '중위권'
    else:
        color, bg, tier = COLOR_DANGER, '#FFF0EF', f'하위 {pct:.0f}%'
    return (
        f'<span style="display:inline-flex;align-items:center;gap:6px;font-size:0.78rem;'
        f'color:{color};background:{bg};padding:6px 12px;border-radius:8px;font-weight:600;">'
        f'<span style="color:{COLOR_TEXT};">{esc_html_safe(label)}</span> {tier}'
        f'</span>'
    )


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────
def render(df: pd.DataFrame):
    if df is None or df.empty:
        render_empty_state('표시할 데이터가 없습니다', icon='📭')
        return

    adv_name = _detect_advertiser(df)
    industry = _detect_industry(df)

    if not industry:
        render_empty_state(
            '업종 정보가 없습니다',
            '업종 정보가 있는 데이터에서 동종업계 비교 분석이 가능합니다.',
            icon='🔎',
        )
        return

    render_page_header(
        title='업종 · 경쟁',
        kicker='분석',
        subtitle=(
            f'<b>{esc_html_safe(adv_name)}</b>의 <b>{esc_html_safe(industry)}</b> 업종 내 위치와 '
            f'동종업계 매체 활용 패턴을 확인합니다.'
        ),
    )

    # ── 우리 지표 계산 (메시징 캠페인, 발송 5천+) ──
    valid = df[df['발송량'] >= MIN_SENDS_FOR_CTR_CLAIM].copy()
    our_send = float(valid['발송량'].sum())
    our_click = float(valid['클릭수'].fillna(0).sum())
    our_cost = float(valid['집행금액'].sum())
    our_ctr = calc_ctr_scalar(our_click, our_send)
    our_cpc = (our_cost / our_click) if our_click > 0 else 0

    # ── 업종 벤치마크 ──
    bench = get_benchmark_stats(industry)
    if not bench or bench.get('total_campaigns', 0) == 0:
        render_empty_state(
            f'{industry} 업종 벤치마크 데이터가 부족합니다',
            'Firebase 데이터가 더 쌓이면 자동으로 활성화됩니다.',
            icon='📊',
        )
        return

    bench_avg_ctr = float(bench.get('avg_ctr', 0))
    bench_med_ctr = float(bench.get('med_ctr', 0))
    bench_avg_cpc = float(bench.get('avg_cpc', 0))
    ctr_distribution = bench.get('ctr_values', [])
    cpc_distribution = bench.get('cpc_values', [])
    n_ind_camp = int(bench.get('total_campaigns', 0))

    # ── 우리 백분위 ──
    our_ctr_pct = calc_percentile(our_ctr, ctr_distribution) if our_ctr > 0 else None
    our_cpc_pct = (
        calc_percentile(-our_cpc, [-c for c in cpc_distribution if c > 0])
        if our_cpc > 0 and cpc_distribution else None
    )

    # ── 동종업계 광고주 수 ──
    full = load_from_firestore()
    n_ind_brands = 0
    ind_media_ranking = pd.DataFrame()
    top_brands = []
    if full is not None and not full.empty:
        ind_df = full[full['분야'] == industry]
        n_ind_brands = ind_df['_브랜드'].dropna().nunique() if not ind_df.empty else 0

        # 동종업계 매체 사용 빈도
        if not ind_df.empty and '매체' in ind_df.columns:
            ind_media_ranking = (
                ind_df.dropna(subset=['매체'])
                .groupby(ind_df['매체'].astype(str).str.strip())
                .agg(
                    광고주수=('_브랜드', 'nunique'),
                    캠페인수=('_date', 'size'),
                )
                .reset_index()
                .rename(columns={'매체': '매체명'})
                .sort_values(['광고주수', '캠페인수'], ascending=[False, False])
            )

        # 동종업계 Top 광고주 (자기 자신 제외)
        if not ind_df.empty:
            brand_stats = (
                ind_df[ind_df['_브랜드'] != adv_name]
                .groupby('_브랜드')
                .agg(
                    캠페인수=('_date', 'size'),
                    총광고비=('광고비', 'sum'),
                )
                .reset_index()
                .sort_values('총광고비', ascending=False)
                .head(3)
            )
            top_brands = brand_stats.to_dict('records')

    # ── 4 요약 strip ──
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_summary_card(
            '우리 CTR', f'{our_ctr:.2f}%',
            f'발송 {compact_num(our_send, "건")} 기준',
        ), unsafe_allow_html=True)
    with c2:
        diff = our_ctr - bench_avg_ctr
        color = COLOR_SUCCESS if diff > 0 else COLOR_DANGER
        sign = '+' if diff > 0 else ''
        st.markdown(_summary_card(
            '업종 평균 대비', f'{sign}{diff:.2f}%p',
            f'{industry} 평균 {bench_avg_ctr:.2f}% · 중앙값 {bench_med_ctr:.2f}%',
            color=color,
        ), unsafe_allow_html=True)
    with c3:
        st.markdown(_summary_card(
            '업종 캠페인', f'{n_ind_camp:,}건',
            f'광고주 {n_ind_brands}곳 운영 중',
        ), unsafe_allow_html=True)
    with c4:
        cpc_text = f'{our_cpc:,.0f}원' if our_cpc > 0 else '—'
        bench_cpc_text = f'{bench_avg_cpc:,.0f}원' if bench_avg_cpc > 0 else '—'
        st.markdown(_summary_card(
            '우리 CPC', cpc_text,
            f'업종 평균 {bench_cpc_text}',
        ), unsafe_allow_html=True)

    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    # ── 백분위 한 줄 ──
    chips = []
    if our_ctr_pct is not None:
        chips.append(_percentile_chip('CTR', our_ctr_pct))
    if our_cpc_pct is not None:
        chips.append(_percentile_chip('CPC 효율', our_cpc_pct))
    if chips:
        st.markdown(
            f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:24px;">'
            + ''.join(chips) +
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── 2열: 동종업계 매체 패턴 / Top 광고주 ──
    col_left, col_right = st.columns([1.2, 1])

    with col_left:
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:6px;letter-spacing:-0.01em;">동종업계 인기 매체</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
            f'{industry} 업종 광고주가 많이 쓰는 매체 순. 우리 집행 여부 표시.</div>',
            unsafe_allow_html=True,
        )

        if ind_media_ranking.empty:
            st.markdown(_summary_card('데이터 부족', '—', '동종업계 매체 데이터가 부족합니다.'),
                        unsafe_allow_html=True)
        else:
            our_media = set(df['매체명'].dropna().astype(str).str.strip().unique()) if '매체명' in df.columns else set()
            top10 = ind_media_ranking.head(10)
            for _, r in top10.iterrows():
                name = str(r['매체명'])
                if not name:
                    continue
                our_use = name in our_media
                sub = f'캠페인 {int(r["캠페인수"]):,}건'
                st.markdown(_media_compare_row(name, our_use, int(r['광고주수']), sub=sub),
                            unsafe_allow_html=True)

    with col_right:
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:6px;letter-spacing:-0.01em;">동종업계 Top 광고주</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
            f'광고비 기준 (자기 자신 제외)</div>',
            unsafe_allow_html=True,
        )

        if not top_brands:
            st.markdown(_summary_card('비교 광고주 없음', '—',
                                       f'{industry} 업종에 비교할 다른 광고주가 없습니다.'),
                        unsafe_allow_html=True)
        else:
            for i, b in enumerate(top_brands, start=1):
                st.markdown(
                    f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;'
                    f'padding:12px 14px;background:{COLOR_CARD};margin-bottom:8px;">'
                    f'  <div style="display:flex;align-items:center;gap:10px;">'
                    f'    <div style="font-size:0.7rem;color:{BRAND_PRIMARY};font-weight:700;'
                    f'min-width:24px;">#{i}</div>'
                    f'    <div style="flex:1;">'
                    f'      <div style="font-size:0.88rem;color:{COLOR_TEXT};font-weight:600;">'
                    f'{esc_html_safe(str(b["_브랜드"]))}</div>'
                    f'      <div style="font-size:0.7rem;color:{COLOR_TEXT_TER};margin-top:3px;">'
                    f'캠페인 {int(b["캠페인수"]):,}건 · 광고비 {compact_num(float(b["총광고비"]), "원")}</div>'
                    f'    </div>'
                    f'  </div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

        # 추천 액션
        st.markdown('<div class="space-sm"></div>', unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-size:0.85rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:6px;letter-spacing:-0.01em;">추천 액션</div>',
            unsafe_allow_html=True,
        )

        recs = []
        # 미집행 인기 매체 찾기
        if not ind_media_ranking.empty:
            our_media = set(df['매체명'].dropna().astype(str).str.strip().unique()) if '매체명' in df.columns else set()
            unused = ind_media_ranking[~ind_media_ranking['매체명'].astype(str).str.strip().isin(our_media)].head(3)
            if not unused.empty:
                names = ', '.join(unused['매체명'].astype(str).head(3).tolist())
                recs.append(f'미집행 인기 매체 검토: <b style="color:{COLOR_TEXT};">{esc_html_safe(names)}</b> → 기회 매체 탭에서 상세 보기')

        if our_ctr_pct is not None:
            if our_ctr_pct >= 75:
                recs.append(f'CTR {our_ctr:.2f}%로 업종 상위권. 메시지 카피를 다른 매체에도 확장 검토.')
            elif our_ctr_pct < 25:
                recs.append(f'CTR {our_ctr:.2f}%는 업종 하위권. 카피·타이밍 페이지에서 카피 개선 포인트 점검 권장.')

        if not recs:
            recs.append('업종 대비 특이 신호 없음. 위 탭에서 상세 분석을 진행하세요.')

        for r in recs:
            st.markdown(
                f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {BRAND_PRIMARY};'
                f'border-radius:8px;padding:10px 14px;background:{COLOR_CARD};margin-bottom:6px;'
                f'font-size:0.78rem;color:{COLOR_TEXT_SEC};line-height:1.55;">{r}</div>',
                unsafe_allow_html=True,
            )

    # ── 하단 안내 ──
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER_SUBTLE};border-radius:8px;'
        f'padding:10px 14px;background:{COLOR_BG};font-size:0.74rem;color:{COLOR_TEXT_SEC};'
        f'line-height:1.55;">'
        f'💡 더 깊은 분석은 위 탭에서 — '
        f'<b>경쟁사 분석</b>(업종별 벤치마크), <b>업종 인텔리전스</b>(광고주 포지셔닝), '
        f'<b>기회 매체</b>(미집행 매체 + 타사 레퍼런스).'
        f'</div>',
        unsafe_allow_html=True,
    )
