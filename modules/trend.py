"""
월별 성과 트렌드 분석 + 주차별 드릴다운 (Toss-style)
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from modules.config import BRAND_PRIMARY, section_header
from modules.data_processing import aggregate_metrics, add_week_columns, MIN_RELIABLE_SENDS
from modules.charts import bar_chart, dual_axis_bar_line, line_chart
from modules.insights import detect_trend, render_insights
from modules.firebase_connector import get_benchmark_monthly, get_benchmark_stats


def _render_weekly_drilldown(df: pd.DataFrame, month: str):
    week_df = add_week_columns(df[df['년월'] == month])
    w = aggregate_metrics(week_df, ['주차'])

    t1, t2, t3 = st.tabs([f"집행금액 · {month}", "발송량 & 클릭수", "CTR"])
    with t1:
        st.plotly_chart(bar_chart(w, '주차', '집행금액', "", '#3182F6'), width='stretch')
    with t2:
        st.plotly_chart(dual_axis_bar_line(w, '주차', '발송량', '클릭수', "발송량", "클릭수"), width='stretch')
    with t3:
        st.plotly_chart(line_chart(w, '주차', 'CTR', "", '#20C997'), width='stretch')
        low_w = w[w['발송량'] < MIN_RELIABLE_SENDS]['주차'].tolist()
        if low_w:
            st.caption(f"⚠️ 발송량 부족 주차: {', '.join(low_w)}")


def render(df: pd.DataFrame):
    st.markdown(section_header(
        "성과 트렌드 분석",
        "그래프의 막대나 점을 클릭하면 해당 월의 주차별 상세 데이터를 확인할 수 있습니다.",
    ), unsafe_allow_html=True)

    monthly = aggregate_metrics(df, ['년월'])

    # ── 인사이트 칩 ──
    render_insights(detect_trend(monthly))

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
            fig3 = line_chart(monthly, '년월', 'CTR', "월별 CTR 추이", '#20C997')
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
                # 공백 제거 등 정규화 시도
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
                        line=dict(dash='dot', color='#B0B8C1', width=2),
                        marker=dict(size=5, color='#B0B8C1', symbol='diamond'),
                        hovertemplate='업종 평균 CTR: %{y:.2f}%<extra></extra>',
                    ))
                    _overlay_added = True

            # 방법 2: 월별 매칭 실패 시 전체 평균 수평선
            if not _overlay_added and _bench_stats and _bench_stats['avg_ctr'] > 0:
                fig3.add_hline(
                    y=_bench_stats['avg_ctr'],
                    line_dash='dot', line_color='#B0B8C1', line_width=1.5,
                    annotation_text=f"업종 평균 {_bench_stats['avg_ctr']:.2f}%",
                    annotation_position='top right',
                    annotation_font=dict(size=10, color='#8B95A1'),
                )
                _overlay_added = True

            if _overlay_added:
                fig3.update_layout(
                    showlegend=True,
                    legend=dict(
                        orientation='h', yanchor='bottom', y=1.02,
                        xanchor='right', x=1,
                        font=dict(size=11, color='#8B95A1'),
                    ),
                )
            events.append(st.plotly_chart(fig3, width='stretch', on_select="rerun"))
            # 발송량 부족 월 안내
            low_vol = monthly[monthly['발송량'] < MIN_RELIABLE_SENDS]['년월'].tolist()
            if low_vol:
                st.caption(f"⚠️ 발송량 {MIN_RELIABLE_SENDS:,}건 미만으로 CTR 신뢰도가 낮은 기간: {', '.join(low_vol)}")
            # 업종 평균 대비 요약
            try:
                bench_stats = get_benchmark_stats()
            except Exception:
                bench_stats = None
            if bench_stats and bench_stats['avg_ctr'] > 0:
                latest_month = monthly.iloc[-1] if not monthly.empty else None
                if latest_month is not None and latest_month['CTR'] > 0:
                    diff = latest_month['CTR'] - bench_stats['avg_ctr']
                    sign = "+" if diff >= 0 else ""
                    badge_color = "#2E7D32" if diff >= 0 else "#E53935"
                    st.caption(
                        f"📊 최근 월 CTR **{latest_month['CTR']:.2f}%** vs "
                        f"업종 평균 **{bench_stats['avg_ctr']:.2f}%** "
                        f"(:{('green' if diff >= 0 else 'red')}[**{sign}{diff:.2f}%p**])"
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
        st.markdown(f"<br>", unsafe_allow_html=True)
        st.markdown(section_header(f"{clicked_month} 주차별 상세 분석"), unsafe_allow_html=True)
        _render_weekly_drilldown(df, clicked_month)
    else:
        st.caption("위 차트에서 궁금한 월을 클릭해 보세요.")

    st.divider()
