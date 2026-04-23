"""
월별 성과 트렌드 분석 + 주차별 드릴다운 — Linear/Notion 프리뷰 스타일.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from modules.config import (
    BRAND_PRIMARY,
    COLOR_BLUE, COLOR_DANGER, COLOR_SUCCESS,
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER,
    COLOR_BORDER, COLOR_BORDER_SUBTLE, compact_num,
    CHART_COLORS,
)
from modules.data_processing import aggregate_metrics, add_week_columns, MIN_RELIABLE_SENDS
from modules.charts import bar_chart, dual_axis_bar_line, line_chart
from modules.insights import detect_trend, render_insights
from modules.firebase_connector import get_benchmark_monthly, get_benchmark_stats
from modules.ui_helpers import render_page_header


def _render_weekly_drilldown(df: pd.DataFrame, month: str):
    week_df = add_week_columns(df[df['년월'] == month])
    w = aggregate_metrics(week_df, ['주차'])

    t1, t2, t3 = st.tabs([f"집행금액 · {month}", "발송량 & 클릭수", "CTR"])
    with t1:
        st.plotly_chart(bar_chart(w, '주차', '집행금액', "", COLOR_BLUE), width='stretch')
    with t2:
        st.plotly_chart(
            dual_axis_bar_line(w, '주차', '발송량', '클릭수', "발송량", "클릭수"),
            width='stretch',
        )
    with t3:
        st.plotly_chart(line_chart(w, '주차', 'CTR', "", CHART_COLORS[2]), width='stretch')
        low_w = w[w['발송량'] < MIN_RELIABLE_SENDS]['주차'].tolist()
        if low_w:
            st.caption(f"⚠️ 발송량 부족 주차: {', '.join(low_w)}")


def render(df: pd.DataFrame):
    monthly = aggregate_metrics(df, ['년월'])
    n_months = len(monthly)
    total_cost = float(monthly['집행금액'].sum()) if not monthly.empty else 0

    # ── 페이지 헤더 ──
    render_page_header(
        title="트렌드 분석",
        kicker="성과 분석",
        subtitle=(
            f"월별 성과 추이와 주차별 드릴다운. "
            f"기간 <b>{n_months}개월</b> · 누적 집행 <b>{compact_num(total_cost, '원')}</b> · "
            "그래프를 클릭하면 해당 월의 주차별 상세로 이동합니다."
        ),
    )

    # ── 인사이트 칩 ──
    render_insights(detect_trend(monthly))

    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs(["월별 집행금액", "발송량 & 클릭수", "월별 CTR"])
    events: list = []

    try:
        with tab1:
            fig1 = bar_chart(monthly, '년월', '집행금액', "월별 총 집행금액", BRAND_PRIMARY)
            events.append(st.plotly_chart(fig1, width='stretch', on_select="rerun"))
        with tab2:
            fig2 = dual_axis_bar_line(monthly, '년월', '발송량', '클릭수', title="발송량 대비 클릭수")
            events.append(st.plotly_chart(fig2, width='stretch', on_select="rerun"))
        with tab3:
            fig3 = line_chart(monthly, '년월', 'CTR', "월별 CTR 추이", CHART_COLORS[2])

            # ── 업종 평균 CTR 오버레이 ──
            try:
                _bench_stats = get_benchmark_stats()
                _bench_monthly = get_benchmark_monthly()
            except Exception:
                _bench_stats, _bench_monthly = None, None

            _overlay_added = False
            # 방법 1: 월별 트렌드 매칭 (월 포맷 정규화)
            if _bench_monthly is not None and not _bench_monthly.empty:
                client_months = set(monthly['년월'].tolist())
                bm = _bench_monthly.copy()
                bm['_norm'] = bm['년월'].str.replace(' ', '')
                client_norm = {m.replace(' ', ''): m for m in client_months}
                bm = bm[bm['_norm'].isin(client_norm.keys())]
                if not bm.empty:
                    bm['_display'] = bm['_norm'].map(
                        lambda n: next((v for k, v in client_norm.items() if k == n), n)
                    )
                    fig3.add_trace(go.Scatter(
                        x=bm['_display'], y=bm['CTR'],
                        mode='lines+markers', name='업종 평균',
                        line=dict(dash='dot', color=COLOR_TEXT_SEC, width=2),
                        marker=dict(size=5, color=COLOR_TEXT_SEC, symbol='diamond'),
                        hovertemplate='업종 평균 CTR: %{y:.2f}%<extra></extra>',
                    ))
                    _overlay_added = True

            # 방법 2: 월별 매칭 실패 시 전체 평균 수평선
            if not _overlay_added and _bench_stats and _bench_stats['avg_ctr'] > 0:
                fig3.add_hline(
                    y=_bench_stats['avg_ctr'],
                    line_dash='dot', line_color=COLOR_TEXT_SEC, line_width=1.5,
                    annotation_text=f"업종 평균 {_bench_stats['avg_ctr']:.2f}%",
                    annotation_position='top right',
                    annotation_font=dict(size=10, color=COLOR_TEXT_SEC),
                )
                _overlay_added = True

            if _overlay_added:
                fig3.update_layout(
                    showlegend=True,
                    legend=dict(
                        orientation='h', yanchor='bottom', y=1.02,
                        xanchor='right', x=1,
                        font=dict(size=11, color=COLOR_TEXT_SEC),
                    ),
                )
            events.append(st.plotly_chart(fig3, width='stretch', on_select="rerun"))

            # 발송량 부족 월 안내
            low_vol = monthly[monthly['발송량'] < MIN_RELIABLE_SENDS]['년월'].tolist()
            if low_vol:
                st.caption(
                    f"⚠️ 발송량 {MIN_RELIABLE_SENDS:,}건 미만으로 CTR 신뢰도가 낮은 기간: "
                    f"{', '.join(low_vol)}"
                )

            # 업종 평균 대비 최근 월 요약 (Linear 감성 인라인 카드)
            try:
                bench_stats = get_benchmark_stats()
            except Exception:
                bench_stats = None
            if bench_stats and bench_stats['avg_ctr'] > 0:
                latest_month = monthly.iloc[-1] if not monthly.empty else None
                if latest_month is not None and latest_month['CTR'] > 0:
                    diff = latest_month['CTR'] - bench_stats['avg_ctr']
                    sign = "+" if diff >= 0 else ""
                    color = COLOR_SUCCESS if diff >= 0 else COLOR_DANGER
                    st.markdown(
                        f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;'
                        f'padding:10px 14px;margin-top:12px;background:#FAFBFC;'
                        f'display:flex;align-items:center;gap:12px;font-size:0.85rem;">'
                        f'<span style="color:{COLOR_TEXT_SEC};">최근 월 CTR</span>'
                        f'<b style="color:{COLOR_TEXT};">{latest_month["CTR"]:.2f}%</b>'
                        f'<span style="color:{COLOR_TEXT_SEC};">vs 업종 평균</span>'
                        f'<b style="color:{COLOR_TEXT};">{bench_stats["avg_ctr"]:.2f}%</b>'
                        f'<span style="color:{color};font-weight:600;margin-left:auto;">'
                        f'{sign}{diff:.2f}%p</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
    except Exception:
        st.warning("차트 클릭 기능을 사용하려면 `pip install --upgrade streamlit`으로 업데이트해 주세요.")
        return

    clicked_month = next(
        (e["selection"]["points"][0]["x"]
         for e in events if e and len(e.get("selection", {}).get("points", [])) > 0),
        None,
    )

    if clicked_month:
        st.markdown('<div class="space-lg"></div>', unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:4px;letter-spacing:-0.01em;">{clicked_month} 주차별 상세 분석</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
            f'월간 트렌드를 주 단위로 쪼개서 확인</div>',
            unsafe_allow_html=True,
        )
        _render_weekly_drilldown(df, clicked_month)
    else:
        st.caption("위 차트에서 궁금한 월을 클릭해 보세요.")
