"""
매체 성과 — 개요 (Media Performance Overview)
─────────────────────────────────────────────
매체 성과 페이지의 진입 화면.

답하는 질문:
  - 어떤 매체가 가장 잘되고 있나?
  - 어떤 매체가 주의가 필요한가?
  - 매체별 분포는 어떻게 되나? (광고비/발송 집중도)

구성:
  1) 매체 헬스 스코어 카드 (Top 5) — A/B/C/D 등급
  2) 추천 액션 — 더 투자할 매체 / 점검할 매체
  3) 광고비 집중도 미니 시각화 (Top 5 매체가 차지하는 비중)
"""
import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_BG, COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING, COLOR_BLUE,
    BRAND_PRIMARY, compact_num, MIN_SENDS_FOR_CTR_CLAIM,
)
from modules.data_processing import calc_ctr_scalar, media_month_stats
from modules.ui_helpers import render_page_header, render_empty_state, esc_html_safe


# ──────────────────────────────────────────────
# 매체 헬스 스코어 계산
# ──────────────────────────────────────────────
def _grade_from_percentile(pct: float) -> tuple[str, str]:
    """백분위 → (등급, 색상). 100에 가까울수록 좋음."""
    if pct >= 75:
        return 'A', COLOR_SUCCESS
    if pct >= 50:
        return 'B', COLOR_BLUE
    if pct >= 25:
        return 'C', COLOR_WARNING
    return 'D', COLOR_DANGER


def _compute_media_health(df: pd.DataFrame, cur_month: str) -> pd.DataFrame:
    """매체별 헬스 스코어 계산.

    스코어 = (CTR 백분위 + 발송 효율 백분위) / 2
    CTR 신뢰성: 발송 5천+ 매체만 평가 (그 이하는 grade='-')
    """
    stats = media_month_stats(df, cur_month).copy()
    if stats.empty:
        return stats

    # CTR 신뢰성 마스크
    stats['_qualified'] = stats['발송량'] >= MIN_SENDS_FOR_CTR_CLAIM

    # CTR 백분위 (qualified만)
    qual = stats[stats['_qualified']].copy()
    if not qual.empty and qual['CTR'].nunique() > 1:
        qual_sorted = qual.sort_values('CTR').reset_index()
        qual_sorted['_ctr_pct'] = (qual_sorted.index + 1) / len(qual_sorted) * 100
        ctr_pct_map = dict(zip(qual_sorted['매체명'], qual_sorted['_ctr_pct']))
        stats['_ctr_pct'] = stats['매체명'].map(ctr_pct_map).fillna(0)
    else:
        stats['_ctr_pct'] = 50.0

    # 광고비 효율 백분위 (CPC = 광고비/클릭, 낮을수록 좋음)
    stats['_cpc'] = np.where(
        stats['클릭수'] > 0,
        stats['집행금액'] / stats['클릭수'],
        np.inf,
    )
    qual = stats[stats['_qualified'] & np.isfinite(stats['_cpc'])].copy()
    if not qual.empty and qual['_cpc'].nunique() > 1:
        # CPC는 낮을수록 좋음 → 역순 정렬
        qual_sorted = qual.sort_values('_cpc', ascending=False).reset_index()
        qual_sorted['_cpc_pct'] = (qual_sorted.index + 1) / len(qual_sorted) * 100
        cpc_pct_map = dict(zip(qual_sorted['매체명'], qual_sorted['_cpc_pct']))
        stats['_cpc_pct'] = stats['매체명'].map(cpc_pct_map).fillna(50)
    else:
        stats['_cpc_pct'] = 50.0

    # 종합 스코어 (CTR 60% + CPC 40% 가중)
    stats['_score'] = stats['_ctr_pct'] * 0.6 + stats['_cpc_pct'] * 0.4
    stats.loc[~stats['_qualified'], '_score'] = np.nan  # 신뢰성 부족 → 등급 보류

    return stats


# ──────────────────────────────────────────────
# UI 카드
# ──────────────────────────────────────────────
def _media_card(row: pd.Series, *, rank: int) -> str:
    """매체 한 줄 카드."""
    name = str(row['매체명'])
    sends = float(row.get('발송량', 0) or 0)
    clicks = float(row.get('클릭수', 0) or 0)
    cost = float(row.get('집행금액', 0) or 0)
    ctr = float(row.get('CTR', 0) or 0)
    cpc = (cost / clicks) if clicks > 0 else 0
    score = float(row.get('_score', 0) or 0) if not pd.isna(row.get('_score')) else None

    if score is not None:
        grade, grade_color = _grade_from_percentile(score)
    else:
        grade, grade_color = '—', COLOR_TEXT_TER

    accent_color = grade_color

    cpc_text = f'{cpc:,.0f}원' if cpc > 0 else '—'

    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {accent_color};'
        f'border-radius:8px;padding:12px 16px;background:{COLOR_CARD};margin-bottom:8px;">'
        f'  <div style="display:flex;align-items:center;gap:12px;">'
        # 순위 + 등급
        f'    <div style="display:flex;flex-direction:column;align-items:center;'
        f'min-width:40px;border-right:1px solid {COLOR_BORDER_SUBTLE};padding-right:10px;">'
        f'      <div style="font-size:0.65rem;color:{COLOR_TEXT_TER};font-weight:600;">#{rank}</div>'
        f'      <div style="font-size:1.1rem;color:{grade_color};font-weight:700;line-height:1;">{grade}</div>'
        f'    </div>'
        # 매체명
        f'    <div style="flex:1;">'
        f'      <div style="font-size:0.92rem;color:{COLOR_TEXT};font-weight:600;letter-spacing:-0.005em;">'
        f'{esc_html_safe(name)}</div>'
        f'      <div style="font-size:0.7rem;color:{COLOR_TEXT_SEC};margin-top:2px;">'
        f'발송 {compact_num(sends, "건")} · CPC {cpc_text}</div>'
        f'    </div>'
        # CTR
        f'    <div style="text-align:right;min-width:80px;">'
        f'      <div style="font-size:1.1rem;color:{COLOR_TEXT};font-weight:600;line-height:1;">'
        f'{ctr:.2f}%</div>'
        f'      <div style="font-size:0.65rem;color:{COLOR_TEXT_TER};margin-top:2px;">CTR</div>'
        f'    </div>'
        f'  </div>'
        f'</div>'
    )


def _summary_strip_card(title: str, value: str, sub: str, *, color: str = COLOR_TEXT) -> str:
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


def _concentration_bar(stats: pd.DataFrame) -> str:
    """광고비 집중도 시각화 — Top 5 vs 나머지 비율 가로 막대."""
    if stats.empty:
        return ''
    total = stats['집행금액'].sum()
    if total <= 0:
        return ''
    top5 = stats.nlargest(5, '집행금액')
    top5_sum = top5['집행금액'].sum()
    top5_pct = top5_sum / total * 100
    rest_pct = 100 - top5_pct

    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;padding:14px 16px;'
        f'background:{COLOR_CARD};margin-top:12px;">'
        f'  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">'
        f'    <div style="font-size:0.78rem;color:{COLOR_TEXT};font-weight:600;">'
        f'광고비 집중도 (Top 5)</div>'
        f'    <div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};">'
        f'<b style="color:{COLOR_TEXT};">{top5_pct:.0f}%</b> · Top 5 매체</div>'
        f'  </div>'
        f'  <div style="display:flex;height:10px;border-radius:6px;overflow:hidden;'
        f'background:{COLOR_BORDER_SUBTLE};">'
        f'    <div style="width:{top5_pct}%;background:{BRAND_PRIMARY};"></div>'
        f'    <div style="width:{rest_pct}%;background:{COLOR_BORDER};"></div>'
        f'  </div>'
        f'  <div style="display:flex;justify-content:space-between;margin-top:6px;'
        f'font-size:0.7rem;color:{COLOR_TEXT_TER};">'
        f'    <span>Top 5: {compact_num(top5_sum, "원")}</span>'
        f'    <span>나머지 {len(stats) - len(top5)}개: {compact_num(total - top5_sum, "원")}</span>'
        f'  </div>'
        f'</div>'
    )


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────
def render(df: pd.DataFrame):
    if df is None or df.empty or '년월' not in df.columns:
        render_empty_state('데이터가 없거나 전처리가 필요합니다', icon='📭')
        return

    months = sorted([m for m in df['년월'].dropna().unique() if m])
    if not months:
        render_empty_state('월 데이터가 없습니다', icon='📭')
        return

    cur_month = months[-1]
    n_media = df[df['년월'] == cur_month]['매체명'].nunique() if '매체명' in df.columns else 0

    render_page_header(
        title='매체 성과',
        kicker='분석',
        subtitle=(
            f'<b>{cur_month}</b> 기준 운영 매체 <b>{n_media}개</b>. '
            f'헬스 스코어가 높은 순으로 표시되며, 발송 <b>{MIN_SENDS_FOR_CTR_CLAIM:,}건</b> '
            f'이상인 매체만 A~D 등급 산정.'
        ),
    )

    # ── 헬스 스코어 계산 ──
    stats = _compute_media_health(df, cur_month)
    if stats.empty:
        render_empty_state(f'{cur_month}에 매체 데이터가 없습니다', icon='📭')
        return

    # ── 요약 strip (4분할) ──
    qualified = stats[stats['_qualified']].copy()
    n_qual = len(qualified)
    n_unqual = len(stats) - n_qual

    if not qualified.empty:
        best_row = qualified.loc[qualified['_score'].idxmax()]
        worst_row = qualified.loc[qualified['_score'].idxmin()]
        best_name = str(best_row['매체명'])
        worst_name = str(worst_row['매체명'])
        avg_ctr = float(qualified['CTR'].mean())
    else:
        best_name = '—'
        worst_name = '—'
        avg_ctr = 0.0

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_summary_strip_card(
            '평가 매체', f'{n_qual}개',
            f'발송 {MIN_SENDS_FOR_CTR_CLAIM:,}건+ qualified' + (f' · 미충족 {n_unqual}개' if n_unqual > 0 else ''),
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_summary_strip_card(
            '최고 효율 매체', best_name,
            f'스코어 {float(best_row["_score"]):.0f}점 · CTR {float(best_row["CTR"]):.2f}%' if not qualified.empty else '데이터 부족',
            color=COLOR_SUCCESS,
        ), unsafe_allow_html=True)
    with c3:
        st.markdown(_summary_strip_card(
            '점검 필요 매체', worst_name,
            f'스코어 {float(worst_row["_score"]):.0f}점 · CTR {float(worst_row["CTR"]):.2f}%' if not qualified.empty else '데이터 부족',
            color=COLOR_DANGER,
        ), unsafe_allow_html=True)
    with c4:
        st.markdown(_summary_strip_card(
            '평균 CTR', f'{avg_ctr:.2f}%',
            'qualified 매체 평균',
        ), unsafe_allow_html=True)

    st.markdown('<div class="space-lg"></div>', unsafe_allow_html=True)

    # ── 매체별 랭킹 카드 (2열) ──
    col_left, col_right = st.columns([1, 1])

    # 좌: 매체 헬스 스코어 Top 5
    with col_left:
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:6px;letter-spacing:-0.01em;">매체 헬스 스코어</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
            f'스코어 = CTR 백분위 × 60% + CPC 효율 × 40%</div>',
            unsafe_allow_html=True,
        )

        # qualified 먼저, 그 다음 unqualified
        ranked = pd.concat([
            qualified.sort_values('_score', ascending=False),
            stats[~stats['_qualified']].sort_values('집행금액', ascending=False),
        ])

        for i, (_, row) in enumerate(ranked.head(6).iterrows(), start=1):
            st.markdown(_media_card(row, rank=i), unsafe_allow_html=True)

        # 더 있으면 expander
        if len(ranked) > 6:
            with st.expander(f'나머지 {len(ranked) - 6}개 매체 보기'):
                for i, (_, row) in enumerate(ranked.iloc[6:].iterrows(), start=7):
                    st.markdown(_media_card(row, rank=i), unsafe_allow_html=True)

    # 우: 추천 액션 + 집중도
    with col_right:
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:6px;letter-spacing:-0.01em;">추천 액션</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
            f'스코어·집중도 기반 자동 제안</div>',
            unsafe_allow_html=True,
        )

        recs = []
        if not qualified.empty:
            # 최고 매체 — 발송 증액 검토
            recs.append({
                'tone': COLOR_SUCCESS,
                'title': f'💪 {best_name} — 예산 증액 검토',
                'body': (f'스코어 {float(best_row["_score"]):.0f}점, CTR {float(best_row["CTR"]):.2f}%. '
                         f'현재 발송 {compact_num(float(best_row["발송량"]), "건")} → 추가 투자 시 '
                         f'효율 유지 가능성 높음.'),
            })
            # 최저 매체 — 점검
            if float(worst_row['_score']) < 40:
                recs.append({
                    'tone': COLOR_DANGER,
                    'title': f'⚠ {worst_name} — 카피·SEG 점검',
                    'body': (f'스코어 {float(worst_row["_score"]):.0f}점으로 낮음. '
                             f'CTR {float(worst_row["CTR"]):.2f}%, 발송 '
                             f'{compact_num(float(worst_row["발송량"]), "건")}. '
                             f'카피 변경 또는 타겟 SEG 재검토 권장.'),
                })

        # 집중도 경고
        total_cost = float(stats['집행금액'].sum())
        if total_cost > 0:
            top5_cost = float(stats.nlargest(5, '집행금액')['집행금액'].sum())
            concentration = top5_cost / total_cost * 100
            if concentration > 80 and len(stats) > 5:
                recs.append({
                    'tone': COLOR_WARNING,
                    'title': '📊 광고비가 소수 매체에 집중됨',
                    'body': (f'Top 5 매체가 광고비의 <b>{concentration:.0f}%</b> 차지. '
                             f'리스크 분산을 위해 신규 매체 테스트 검토 권장.'),
                })

        if not recs:
            recs.append({
                'tone': COLOR_BLUE,
                'title': '안정적 운영',
                'body': '특별한 개선 신호가 감지되지 않았습니다. 매체 트렌드/히트맵 탭에서 상세 추이를 확인해 보세요.',
            })

        for r in recs:
            st.markdown(
                f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {r["tone"]};'
                f'border-radius:8px;padding:12px 16px;background:{COLOR_CARD};margin-bottom:8px;">'
                f'  <div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:600;'
                f'margin-bottom:4px;">{r["title"]}</div>'
                f'  <div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};line-height:1.55;">'
                f'{r["body"]}</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

        # 집중도 미니 바
        st.markdown(_concentration_bar(stats), unsafe_allow_html=True)

    # ── 하단 안내 ──
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER_SUBTLE};border-radius:8px;'
        f'padding:10px 14px;background:{COLOR_BG};font-size:0.74rem;color:{COLOR_TEXT_SEC};'
        f'line-height:1.55;">'
        f'💡 더 깊은 분석은 위 탭에서 — '
        f'<b>트렌드</b>(월별 추이), <b>매체 히트맵</b>(요일·시간대 패턴), '
        f'<b>매체 트렌드</b>(업종 평균 대비 매체별 변화).'
        f'</div>',
        unsafe_allow_html=True,
    )
