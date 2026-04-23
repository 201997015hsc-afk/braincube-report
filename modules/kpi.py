"""
KPI 요약 + 업종 벤치마크 비교 + 극단값 인사이트
Linear/Notion 프리뷰 스타일.
"""
import streamlit as st
import pandas as pd

from modules.config import (
    kpi_card_html, insight_card_html, compact_num,
    BRAND_PRIMARY, COLOR_DANGER, COLOR_BLUE, COLOR_SUCCESS,
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER,
    COLOR_BORDER, COLOR_BORDER_SUBTLE, COLOR_BG,
)
from modules.data_processing import aggregate_metrics, calc_ctr_scalar, render_ref_period_selector
from modules.firebase_connector import (
    get_benchmark_stats, calc_percentile, calc_percentile_lower, percentile_grade,
)
from modules.ui_helpers import render_page_header, esc_html_safe


def _render_benchmark_row(label: str, our_val: str, bench_val: str, diff_text: str,
                          grade: str, grade_bg: str, grade_color: str):
    """벤치마크 한 행 — Linear 스타일 (얇은 보더 카드)"""
    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;'
        f'padding:12px 16px;margin-bottom:8px;background:{COLOR_BG};'
        f'display:flex;align-items:center;gap:16px;">'
        f'  <div style="flex:1;">'
        f'    <div style="font-size:0.72rem;color:{COLOR_TEXT_SEC};font-weight:500;'
        f'margin-bottom:3px;">{esc_html_safe(label)}</div>'
        f'    <div style="font-size:0.92rem;color:{COLOR_TEXT};">'
        f'      <b style="color:{COLOR_TEXT};font-weight:600;">{our_val}</b>'
        f'      <span style="color:{COLOR_TEXT_SEC};margin-left:6px;">vs 업종 {bench_val}</span>'
        f'      <span style="color:{COLOR_TEXT_TER};font-size:0.78rem;margin-left:6px;">({diff_text})</span>'
        f'    </div>'
        f'  </div>'
        f'  <div style="background:{grade_bg};color:{grade_color};font-size:0.72rem;'
        f'font-weight:600;padding:4px 10px;border-radius:6px;letter-spacing:-0.005em;">'
        f'{esc_html_safe(grade)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def render(df: pd.DataFrame):
    ref_df, period_label = render_ref_period_selector(df, "kpi")

    total_cost = ref_df['집행금액'].sum()
    total_send = ref_df['발송량'].sum()
    total_click = ref_df['클릭수'].sum()
    total_ctr = calc_ctr_scalar(total_click, total_send)

    # ── 페이지 헤더 ──
    render_page_header(
        title="KPI 및 인사이트",
        kicker="성과 분석",
        subtitle=(
            f"{period_label} 기준 주요 지표와 극단값 분석. "
            f"매체 <b>{ref_df['매체명'].nunique() if '매체명' in ref_df.columns else 0}개</b> · "
            f"집행 <b>{compact_num(total_cost, '원')}</b>"
        ),
    )

    # ── 4-KPI 그리드 ──
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(kpi_card_html("총 집행금액", f"{total_cost:,.0f}원"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card_html("총 발송량", f"{total_send:,.0f}건"), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card_html("총 클릭수", f"{total_click:,.0f}회"), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card_html("평균 CTR", f"{total_ctr:.2f}%"), unsafe_allow_html=True)

    st.markdown('<div class="space-lg"></div>', unsafe_allow_html=True)

    # ── 업종 벤치마크 비교 (2-column 그리드) ──
    try:
        bench = get_benchmark_stats()
    except Exception:
        bench = None

    if bench and bench['avg_ctr'] > 0 and total_send > 0:
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:4px;letter-spacing:-0.01em;">업종 벤치마크 비교</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:14px;">'
            f'Firebase 실시간 데이터 기준</div>',
            unsafe_allow_html=True,
        )

        client_cpc = total_cost / total_click if total_click > 0 else 0

        # CTR 벤치
        ctr_pct = calc_percentile(total_ctr, bench['ctr_values'])
        ctr_label, ctr_bg, ctr_color = percentile_grade(ctr_pct)
        ctr_diff = total_ctr - bench['avg_ctr']
        ctr_sign = "+" if ctr_diff >= 0 else ""
        _render_benchmark_row(
            "CTR",
            f"{total_ctr:.2f}%",
            f"{bench['avg_ctr']:.2f}%",
            f"{ctr_sign}{ctr_diff:.2f}%p",
            ctr_label, ctr_bg, ctr_color,
        )

        # CPC 벤치 (집행·클릭 있을 때만)
        if client_cpc > 0:
            cpc_pct = calc_percentile_lower(client_cpc, bench['cpc_values'])
            cpc_label, cpc_bg, cpc_color = percentile_grade(cpc_pct)
            cpc_diff = ((client_cpc - bench['avg_cpc']) / bench['avg_cpc'] * 100) if bench['avg_cpc'] > 0 else 0
            cpc_sign = "+" if cpc_diff >= 0 else ""
            _render_benchmark_row(
                "CPC",
                f"{client_cpc:,.0f}원",
                f"{bench['avg_cpc']:,.0f}원",
                f"{cpc_sign}{cpc_diff:.1f}%",
                cpc_label, cpc_bg, cpc_color,
            )

        st.markdown('<div class="space-lg"></div>', unsafe_allow_html=True)

    # ── 극단값 인사이트 ──
    period_desc = f"({period_label}) " if period_label else ""
    st.markdown(
        f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:4px;letter-spacing:-0.01em;">핵심 인사이트</div>'
        f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:14px;">'
        f'{period_desc}기간 중 주목할 만한 극단값을 자동으로 탐지했습니다</div>',
        unsafe_allow_html=True,
    )

    daily = aggregate_metrics(ref_df, ['날짜', '일자', '짧은_요일'])
    valid = daily[daily['집행금액'] > 0]

    if valid.empty:
        st.markdown(
            f'<div style="color:{COLOR_TEXT_SEC};font-size:0.85rem;padding:12px 0;">'
            f'집행금액이 있는 일자가 없어 극단값 분석을 건너뜁니다.</div>',
            unsafe_allow_html=True,
        )
        return

    max_cost = valid.loc[valid['집행금액'].idxmax()]
    min_cost = valid.loc[valid['집행금액'].idxmin()]
    valid_ctr = valid[(valid['발송량'] > valid['발송량'].median() * 0.1) & (valid['CTR'] > 0)]

    i1, i2, i3 = st.columns(3)
    with i1:
        st.markdown(insight_card_html(
            "비용 최대 집행일",
            f"{max_cost['일자']} ({max_cost['짧은_요일']})",
            f"{max_cost['집행금액']:,.0f}원 소진",
            COLOR_DANGER,
        ), unsafe_allow_html=True)
    with i2:
        st.markdown(insight_card_html(
            "비용 최소 집행일",
            f"{min_cost['일자']} ({min_cost['짧은_요일']})",
            f"{min_cost['집행금액']:,.0f}원 소진",
            COLOR_BLUE,
        ), unsafe_allow_html=True)
    with i3:
        if not valid_ctr.empty:
            best = valid_ctr.loc[valid_ctr['CTR'].idxmax()]
            st.markdown(insight_card_html(
                "CTR 최고 효율일",
                f"{best['일자']} ({best['짧은_요일']})",
                f"CTR {best['CTR']:.2f}% · 클릭 {best['클릭수']:,.0f}회",
                COLOR_SUCCESS,
            ), unsafe_allow_html=True)
