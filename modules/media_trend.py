"""
업계 전반 매체 트렌드 분석
──────────────────────────
Firebase 벤치마크 데이터에서 매체별×월별 CTR/CPC 변화를 추적.
"이번 달 어떤 매체가 상승세고, 어떤 매체가 하락세인가?"에 답하는 섹션.
⚠ 데이터 신뢰도 원칙:
- 매체당 최소 캠페인 수·발송량 가드
- 상대 효과 크기 15% 이상만 트렌드로 인정
- 2개월 데이터만 있을 때 "단기 비교 (추세로 해석 주의)" 표기
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from modules.config import (
    BRAND_PRIMARY, COLOR_BLUE, PLOTLY_LAYOUT,
    section_header, kpi_card_html, alert_card_html, compact_num,
    MIN_SAMPLES_PER_CATEGORY, MIN_SENDS_FOR_CTR_CLAIM, CTR_CHANGE_THRESHOLD,
)
from modules.firebase_connector import load_benchmark


def _layout(**overrides) -> dict:
    base = dict(PLOTLY_LAYOUT)
    base.update(overrides)
    return base


# ──────────────────────────────────────────────
# 데이터 집계
# ──────────────────────────────────────────────

def _aggregate_media_monthly(bench: pd.DataFrame, industry: str = None) -> pd.DataFrame:
    """매체 × 년월 기준 집계 → CTR/CPC/발송건/캠페인수"""
    if bench is None or bench.empty:
        return pd.DataFrame()

    df = bench.copy()
    # 업종 필터
    if industry and industry != '전체':
        df = df[df['분야'] == industry]

    # 클릭 트래킹된 캠페인만
    if '_has_click' in df.columns:
        df = df[df['_has_click']].copy()
    if df.empty or '매체' not in df.columns or '_date' not in df.columns:
        return pd.DataFrame()

    df = df.dropna(subset=['_date', '매체']).copy()
    if df.empty:
        return pd.DataFrame()

    # 년월 표준 포맷 (zero-padded)
    df['년월'] = df['_date'].dt.strftime('%Y년 %m월')

    grp = df.groupby(['매체', '년월'], as_index=False).agg(
        발송건=('발송건', 'sum'),
        클릭수=('클릭수', 'sum'),
        광고비=('광고비', 'sum'),
        캠페인수=('매체', 'size'),
    )
    grp['CTR'] = np.where(grp['발송건'] > 0, grp['클릭수'] / grp['발송건'] * 100, 0.0)
    grp['CPC'] = np.where(grp['클릭수'] > 0, grp['광고비'] / grp['클릭수'], 0.0)
    return grp


def _compute_mom_trends(monthly: pd.DataFrame, metric: str = 'CTR') -> pd.DataFrame:
    """매체별 전월 대비 변화(MoM) 계산.

    Returns columns: 매체, 이전월, 당월, 이전값, 당월값, 변화량, 상대변화%, 발송건_이전, 발송건_당월, 캠페인수_이전, 캠페인수_당월
    """
    if monthly.empty:
        return pd.DataFrame()

    months = sorted(monthly['년월'].unique())
    if len(months) < 2:
        return pd.DataFrame()

    prev_month, curr_month = months[-2], months[-1]

    prev = monthly[monthly['년월'] == prev_month].set_index('매체')
    curr = monthly[monthly['년월'] == curr_month].set_index('매체')

    common = prev.index.intersection(curr.index)
    rows = []
    for media in common:
        p = prev.loc[media]
        c = curr.loc[media]
        p_val = float(p[metric])
        c_val = float(c[metric])
        p_sends = int(p['발송건'])
        c_sends = int(c['발송건'])
        p_camps = int(p['캠페인수'])
        c_camps = int(c['캠페인수'])

        # 신뢰도 가드: 양월 모두 최소 발송량 + 캠페인 수
        if (p_sends < MIN_SENDS_FOR_CTR_CLAIM or c_sends < MIN_SENDS_FOR_CTR_CLAIM
                or p_camps < 2 or c_camps < 2):
            continue

        change = c_val - p_val
        rel_change = ((c_val / p_val) - 1) * 100 if p_val > 0 else 0.0
        rows.append({
            '매체': media,
            '이전월': prev_month,
            '당월': curr_month,
            '이전값': p_val,
            '당월값': c_val,
            '변화량': change,
            '상대변화%': rel_change,
            '발송건_이전': p_sends,
            '발송건_당월': c_sends,
            '캠페인수_이전': p_camps,
            '캠페인수_당월': c_camps,
        })
    return pd.DataFrame(rows)


# ──────────────────────────────────────────────
# 차트 헬퍼
# ──────────────────────────────────────────────

def _build_ranking_chart(df: pd.DataFrame, metric: str, direction: str,
                         color: str, suffix: str) -> go.Figure:
    """상승/하락 TOP N 가로 막대 차트"""
    df = df.copy()
    # 하락 차트는 절대값으로 표시하되 색/기호만 빨강
    bar_vals = df['변화량'].abs() if direction == 'down' else df['변화량']
    if direction == 'down':
        txt = [
            f'{v:.2f}{suffix} ({curr:.2f}{suffix})'
            for v, curr in zip(df['변화량'], df['당월값'])
        ]
    else:
        txt = [
            f'+{v:.2f}{suffix} ({curr:.2f}{suffix})'
            for v, curr in zip(df['변화량'], df['당월값'])
        ]

    x_max = float(bar_vals.max()) * 1.45 if len(bar_vals) else 1.0

    fig = go.Figure(go.Bar(
        y=df['매체'],
        x=bar_vals,
        orientation='h',
        marker_color=color,
        text=txt,
        textposition='outside',
        textfont=dict(size=11),
        cliponaxis=False,
    ))
    fig.update_layout(**_layout(
        height=max(260, 40 * len(df) + 80),
        xaxis=dict(
            title=f'{metric} 변화 ({"절대값" if direction == "down" else "순증"})',
            showgrid=True, gridcolor='#F0F1F3',
            range=[0, x_max] if x_max > 0 else None,
        ),
        yaxis=dict(autorange='reversed'),
        showlegend=False,
        margin=dict(t=32, l=120, r=170, b=48),
    ))
    return fig


def _build_stacked_compare(df: pd.DataFrame, metric: str, suffix: str) -> go.Figure:
    """이전월·당월 값을 나란히 비교하는 그룹 바 (전체 매체)"""
    if df.empty:
        return go.Figure()

    df_sorted = df.sort_values('변화량', ascending=False).reset_index(drop=True)
    y_max = float(max(df_sorted['이전값'].max(), df_sorted['당월값'].max())) * 1.2

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name=f'이전월 ({df_sorted["이전월"].iloc[0]})',
        x=df_sorted['매체'], y=df_sorted['이전값'],
        marker_color='#B0BEC5',
        text=[f'{v:.2f}' for v in df_sorted['이전값']],
        textposition='outside', textfont=dict(size=9),
        cliponaxis=False,
    ))
    fig.add_trace(go.Bar(
        name=f'당월 ({df_sorted["당월"].iloc[0]})',
        x=df_sorted['매체'], y=df_sorted['당월값'],
        marker_color=BRAND_PRIMARY,
        text=[f'{v:.2f}' for v in df_sorted['당월값']],
        textposition='outside', textfont=dict(size=9),
        cliponaxis=False,
    ))
    fig.update_layout(**_layout(
        title=dict(text=f'매체별 {metric} — 전월 vs 당월', font=dict(size=13)),
        height=max(320, 35 * len(df_sorted) + 100),
        barmode='group',
        xaxis=dict(showgrid=False, tickangle=-30),
        yaxis=dict(title=f'{metric} ({suffix})', showgrid=True,
                   gridcolor='#F0F1F3', range=[0, y_max]),
        legend=dict(orientation='h', yanchor='bottom', y=1.02,
                    xanchor='center', x=0.5),
        margin=dict(t=72, l=60, r=30, b=80),
    ))
    return fig


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────

def render(df: pd.DataFrame):
    st.markdown(section_header(
        '매체 트렌드',
        '업계 전반에서 어떤 매체가 상승세이고 어떤 매체가 하락세인지 확인합니다. '
        'Firebase 벤치마크 데이터 기반 · 전월 대비 MoM 변화 분석.',
    ), unsafe_allow_html=True)

    # ── Firebase 데이터 로드 ──
    try:
        bench = load_benchmark()
    except Exception:
        bench = None

    if bench is None or bench.empty:
        st.info('🔥 Firebase 벤치마크 데이터가 연결되면 매체 트렌드가 표시됩니다.')
        return

    # ── 업종 선택 (빈 문자열/숫자만 있는 값 제외) ──
    industries = sorted([
        ind for ind in bench['분야'].dropna().unique().tolist()
        if str(ind).strip() and not str(ind).strip().isdigit()
    ])
    if not industries:
        st.warning('업종 정보가 없는 데이터입니다.')
        return

    from modules.ui_helpers import help_text as _help_text
    opts = ['전체'] + industries
    col_sel, col_info = st.columns([1, 2])
    with col_sel:
        selected_ind = st.selectbox(
            '📂 업종', opts, key='media_trend_industry',
            help=_help_text("industry"),
        )
    with col_info:
        ind_filtered = bench if selected_ind == '전체' else bench[bench['분야'] == selected_ind]
        n_rows = len(ind_filtered)
        n_advs = ind_filtered['광고주'].nunique() if '광고주' in ind_filtered.columns else 0
        st.markdown(
            f'<div style="padding:10px 0 0;font-size:0.85rem;color:#4E5968;">'
            f'<b style="color:{BRAND_PRIMARY}">{selected_ind}</b> — '
            f'광고주 <b>{n_advs}</b>개 · 캠페인 <b>{n_rows:,}</b>건</div>',
            unsafe_allow_html=True,
        )

    # ── 메트릭 선택 (CTR / CPC) ──
    metric = st.radio(
        '지표',
        ['CTR', 'CPC'],
        format_func=lambda x: '📈 CTR (클릭률)' if x == 'CTR' else '💸 CPC (클릭 비용)',
        horizontal=True,
        key='media_trend_metric',
    )
    suffix = '%p' if metric == 'CTR' else '원'
    value_suffix = '%' if metric == 'CTR' else '원'
    # CTR은 상승이 좋음 / CPC는 하락이 좋음
    up_is_good = (metric == 'CTR')

    st.divider()

    # ── 집계 + 트렌드 계산 ──
    monthly = _aggregate_media_monthly(bench, industry=selected_ind)
    if monthly.empty:
        st.info('해당 업종의 매체별 월별 데이터가 부족합니다.')
        return

    n_months = monthly['년월'].nunique()
    if n_months < 2:
        st.markdown(
            f'<div style="background:#F8F9FA;border-radius:12px;padding:16px 18px;'
            f'font-size:0.85rem;color:#4E5968;line-height:1.6;">'
            f'📉 전월 대비 트렌드 분석은 <b>최소 2개월 이상</b>의 데이터가 필요합니다. '
            f'(현재 {n_months}개월)</div>',
            unsafe_allow_html=True,
        )
        return

    trend_df = _compute_mom_trends(monthly, metric=metric)
    if trend_df.empty:
        st.info(f'전월·당월 모두에 {MIN_SENDS_FOR_CTR_CLAIM:,}건 이상 발송된 매체가 없어 분석을 생략합니다.')
        return

    # 표본 경고: 2개월만 있을 때
    if n_months == 2:
        st.caption(
            f'※ 현재 {n_months}개월 데이터만 존재합니다 — 단기 변동일 수 있어 '
            f'추세로 해석할 때 주의가 필요합니다.'
        )

    # 상승/하락 분리
    up_df = trend_df[trend_df['변화량'] > 0].sort_values('변화량', ascending=False).head(5)
    down_df = trend_df[trend_df['변화량'] < 0].sort_values('변화량').head(5)

    # ── KPI 카드 (4개) ──
    k_total = len(trend_df)
    k_up = int((trend_df['변화량'] > 0).sum())
    k_down = int((trend_df['변화량'] < 0).sum())
    k_flat = k_total - k_up - k_down
    # 유의미한 변화 (CTR은 0.3%p 이상, CPC는 10% 이상)
    if metric == 'CTR':
        meaningful = int((trend_df['변화량'].abs() >= CTR_CHANGE_THRESHOLD).sum())
    else:
        meaningful = int((trend_df['상대변화%'].abs() >= 10).sum())

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(kpi_card_html('분석 매체 수', f'{k_total}개'), unsafe_allow_html=True)
    with k2:
        st.markdown(kpi_card_html('상승 매체', f'{k_up}개'), unsafe_allow_html=True)
    with k3:
        st.markdown(kpi_card_html('하락 매체', f'{k_down}개'), unsafe_allow_html=True)
    with k4:
        st.markdown(kpi_card_html('유의미한 변화', f'{meaningful}개'), unsafe_allow_html=True)

    st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

    # ── 상승/하락 TOP 5 (좌우 2컬럼) ──
    st.markdown(
        f'<div style="font-size:0.92rem;font-weight:700;color:#191F28;'
        f'margin-bottom:12px;">🏆 {metric} {"상승" if up_is_good else "효율"} TOP 5 · 하락 TOP 5</div>',
        unsafe_allow_html=True,
    )
    c_up, c_down = st.columns(2)
    with c_up:
        title_up = f'🟢 {metric} 상승 TOP {len(up_df)}' + (' (긍정)' if up_is_good else ' (비용 증가)')
        st.markdown(
            f'<div style="font-size:0.82rem;font-weight:600;color:'
            f'{"#2E7D32" if up_is_good else "#E53935"};margin-bottom:6px;">{title_up}</div>',
            unsafe_allow_html=True,
        )
        if up_df.empty:
            st.caption('상승한 매체가 없습니다.')
        else:
            fig_up = _build_ranking_chart(
                up_df, metric, direction='up',
                color='#2E7D32' if up_is_good else '#E53935',
                suffix=value_suffix,
            )
            st.plotly_chart(fig_up, use_container_width=True)

    with c_down:
        title_down = f'🔴 {metric} 하락 TOP {len(down_df)}' + (' (주의)' if up_is_good else ' (효율 개선)')
        st.markdown(
            f'<div style="font-size:0.82rem;font-weight:600;color:'
            f'{"#E53935" if up_is_good else "#2E7D32"};margin-bottom:6px;">{title_down}</div>',
            unsafe_allow_html=True,
        )
        if down_df.empty:
            st.caption('하락한 매체가 없습니다.')
        else:
            fig_down = _build_ranking_chart(
                down_df, metric, direction='down',
                color='#E53935' if up_is_good else '#2E7D32',
                suffix=value_suffix,
            )
            st.plotly_chart(fig_down, use_container_width=True)

    st.markdown('<div style="height:20px"></div>', unsafe_allow_html=True)

    # ── 전월 vs 당월 비교 (전체 매체) ──
    st.markdown(
        f'<div style="font-size:0.92rem;font-weight:700;color:#191F28;'
        f'margin-bottom:12px;">📊 매체별 {metric} 전체 비교</div>',
        unsafe_allow_html=True,
    )
    fig_all = _build_stacked_compare(trend_df, metric, value_suffix)
    st.plotly_chart(fig_all, use_container_width=True)

    # ── 상세 표 ──
    with st.expander('📋 상세 데이터 (모든 매체)', expanded=False):
        display_df = trend_df.sort_values('변화량', ascending=False).reset_index(drop=True)
        display_df['이전값'] = display_df['이전값'].round(2)
        display_df['당월값'] = display_df['당월값'].round(2)
        display_df['변화량'] = display_df['변화량'].round(2)
        display_df['상대변화%'] = display_df['상대변화%'].round(1)
        st.dataframe(
            display_df[[
                '매체', '이전월', '이전값', '당월', '당월값',
                '변화량', '상대변화%', '발송건_이전', '발송건_당월',
                '캠페인수_이전', '캠페인수_당월',
            ]],
            hide_index=True,
            use_container_width=True,
        )

    # ── 해석 인사이트 ──
    if not up_df.empty or not down_df.empty:
        points = []
        if not up_df.empty:
            top1 = up_df.iloc[0]
            points.append(
                f"<b>{top1['매체']}</b>이 {metric} "
                f"<b>{top1['이전값']:.2f}{value_suffix} → {top1['당월값']:.2f}{value_suffix}</b>로 "
                f"가장 큰 폭 상승 ({'+' if top1['변화량'] > 0 else ''}{top1['변화량']:.2f}{suffix})"
            )
        if not down_df.empty:
            bot1 = down_df.iloc[0]
            points.append(
                f"<b>{bot1['매체']}</b>이 {metric} "
                f"<b>{bot1['이전값']:.2f}{value_suffix} → {bot1['당월값']:.2f}{value_suffix}</b>로 "
                f"가장 큰 폭 하락 ({bot1['변화량']:.2f}{suffix})"
            )
        if meaningful == 0:
            points.append(
                f'전반적으로 매체별 변동폭이 작습니다 — 업계 전반이 비슷한 수준을 유지 중입니다.'
            )
        st.markdown(
            f'<div style="background:#FFF8F0;border-left:3px solid {BRAND_PRIMARY};'
            f'border-radius:0 10px 10px 0;padding:14px 18px;margin-top:16px;'
            f'font-size:0.88rem;color:#4E5968;line-height:1.7;">'
            + '<br>'.join(f'• {p}' for p in points) +
            f'</div>',
            unsafe_allow_html=True,
        )
