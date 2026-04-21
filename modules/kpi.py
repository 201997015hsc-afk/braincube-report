"""
KPI 요약 카드 + 극단값 인사이트 섹션 (Toss-style)
"""
import streamlit as st
import pandas as pd

from modules.config import (
    kpi_card_html, insight_card_html, section_header,
    COLOR_DANGER, COLOR_BLUE, COLOR_SUCCESS,
)
from modules.data_processing import aggregate_metrics, calc_ctr_scalar, render_ref_period_selector
from modules.firebase_connector import (
    get_benchmark_stats, calc_percentile, calc_percentile_lower, percentile_grade,
)


def render(df: pd.DataFrame):
    ref_df, period_label = render_ref_period_selector(df, "kpi")

    total_cost = ref_df['집행금액'].sum()
    total_send = ref_df['발송량'].sum()
    total_click = ref_df['클릭수'].sum()
    total_ctr = calc_ctr_scalar(total_click, total_send)

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(kpi_card_html("총 집행금액", f"{total_cost:,.0f}원"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card_html("총 발송량", f"{total_send:,.0f}건"), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card_html("총 클릭수", f"{total_click:,.0f}회"), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card_html("평균 CTR", f"{total_ctr:.2f}%"), unsafe_allow_html=True)

    # ── 업종 벤치마크 비교 ──
    try:
        bench = get_benchmark_stats()
    except Exception:
        bench = None
    if bench and bench['avg_ctr'] > 0 and total_send > 0:
        client_cpc = total_cost / total_click if total_click > 0 else 0
        ctr_pct = calc_percentile(total_ctr, bench['ctr_values'])
        ctr_label, ctr_bg, ctr_color = percentile_grade(ctr_pct)
        ctr_diff = total_ctr - bench['avg_ctr']
        ctr_sign = "+" if ctr_diff >= 0 else ""

        cpc_html = ""
        if client_cpc > 0:
            cpc_pct = calc_percentile_lower(client_cpc, bench['cpc_values'])
            cpc_label, cpc_bg, cpc_color = percentile_grade(cpc_pct)
            cpc_diff = ((client_cpc - bench['avg_cpc']) / bench['avg_cpc'] * 100) if bench['avg_cpc'] > 0 else 0
            cpc_sign = "+" if cpc_diff >= 0 else ""
            cpc_html = (
                f'<div style="flex:1;background:#F8F9FA;border-radius:12px;padding:10px 16px;'
                f'display:flex;align-items:center;justify-content:space-between;">'
                f'<div><span style="font-size:.72rem;color:#8B95A1;font-weight:600;">🔥 업종 평균 CPC</span>'
                f'<span style="font-weight:800;color:#191F28;margin-left:8px;">{bench["avg_cpc"]:,.0f}원</span>'
                f'<span style="font-size:.72rem;color:#8B95A1;margin-left:6px;">자사 {client_cpc:,.0f}원 ({cpc_sign}{cpc_diff:.1f}%)</span></div>'
                f'<div style="background:{cpc_bg};color:{cpc_color};font-size:.72rem;'
                f'font-weight:700;padding:3px 10px;border-radius:16px;">{cpc_label}</div></div>'
            )

        st.markdown(
            f'<div style="display:flex;gap:8px;margin:10px 0 0;">'
            f'<div style="flex:1;background:#F8F9FA;border-radius:12px;padding:10px 16px;'
            f'display:flex;align-items:center;justify-content:space-between;">'
            f'<div><span style="font-size:.72rem;color:#8B95A1;font-weight:600;">🔥 업종 평균 CTR</span>'
            f'<span style="font-weight:800;color:#191F28;margin-left:8px;">{bench["avg_ctr"]:.2f}%</span>'
            f'<span style="font-size:.72rem;color:#8B95A1;margin-left:6px;">자사 {total_ctr:.2f}% ({ctr_sign}{ctr_diff:.2f}%p)</span></div>'
            f'<div style="background:{ctr_bg};color:{ctr_color};font-size:.72rem;'
            f'font-weight:700;padding:3px 10px;border-radius:16px;">{ctr_label}</div></div>'
            f'{cpc_html}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)
    period_desc = f"({period_label}) " if period_label else ""
    st.markdown(section_header("핵심 인사이트", f"{period_desc}기간 중 주목할 만한 극단값을 자동으로 탐지했습니다."), unsafe_allow_html=True)

    daily = aggregate_metrics(ref_df, ['날짜', '일자', '짧은_요일'])
    valid = daily[daily['집행금액'] > 0]

    if valid.empty:
        st.info("집행금액이 있는 일자가 없어 극단값 분석을 건너뜁니다.")
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

    st.markdown("<br>", unsafe_allow_html=True)
