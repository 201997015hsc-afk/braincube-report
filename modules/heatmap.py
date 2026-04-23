"""
매체별 성과 분석 섹션 (v6)
① 사분면 차트 — 어떤 매체가 좋은가? (효율 × 규모)
② 매체 카드 — 그 매체를 언제 보내면 좋은가? (클릭수 + CTR + 발송량)
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from modules.config import (
    TARGET_METRICS, WEEKDAY_ORDER_FULL, section_header,
    CHART_COLORS, PLOTLY_LAYOUT, compact_num,
)
from modules.data_processing import add_week_columns, calc_ctr
from modules.charts import heatmap
from modules.insights import detect_heatmap, render_insights
from modules.firebase_connector import get_benchmark_weekday


# ──────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────
MIN_SENDS_THRESHOLD = 5_000  # 이 이하 발송은 '데이터 부족'

# ──────────────────────────────────────────────
# 캐시
# ──────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _build_media_period_stats(df_hash: str, _df: pd.DataFrame, x_col: str) -> pd.DataFrame:
    grouped = (
        _df.groupby([x_col, '매체명'])
        .agg({'발송량': 'sum', '클릭수': 'sum', '집행금액': 'sum'})
        .reset_index()
    )
    grouped['CTR'] = np.where(
        grouped['발송량'] > 0,
        grouped['클릭수'] / grouped['발송량'] * 100,
        0,
    )
    return grouped


@st.cache_data(show_spinner=False)
def _build_media_totals(df_hash: str, _df: pd.DataFrame) -> pd.DataFrame:
    stats = (
        _df.groupby('매체명')
        .agg({'발송량': 'sum', '클릭수': 'sum', '집행금액': 'sum'})
        .reset_index()
    )
    stats['CTR'] = np.where(stats['발송량'] > 0, stats['클릭수'] / stats['발송량'] * 100, 0)
    return stats


# ──────────────────────────────────────────────
# CSS
# ──────────────────────────────────────────────

_CSS = """
<style>
/* ===== Linear/Notion 스타일 — 플랫·얇은 보더 ===== */
.mc-top {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 16px;
}
.mc-top-card {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 10px;
    padding: 14px 16px;
    box-shadow: none;
    text-align: left;
    transition: border-color 0.15s ease;
}
.mc-top-card:hover { border-color: #D1D5DB; }
.mc-top-icon { font-size: 1.1rem; }
.mc-top-label {
    font-size: 0.72rem; color: #6B7280; font-weight: 500; margin-top: 6px;
    letter-spacing: -0.005em;
}
.mc-top-value {
    font-weight: 600; font-size: 1rem; color: #111827; margin-top: 4px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    letter-spacing: -0.01em; font-feature-settings: "tnum";
}
.mc-top-sub {
    font-size: 0.72rem; color: #6B7280; margin-top: 4px;
}
.mc-top-sub b { color: #F7931D; font-weight: 600; }

/* 매체 카드 */
.mc-card {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 10px;
    padding: 18px 22px;
    margin-bottom: 10px;
    box-shadow: none;
    transition: border-color 0.15s ease;
}
.mc-card:hover { border-color: #D1D5DB; }
.mc-header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 12px;
}
.mc-name { display: flex; align-items: center; gap: 10px; }
.mc-dot { width: 10px; height: 10px; border-radius: 3px; flex-shrink: 0; }
.mc-title { font-weight: 600; font-size: 0.92rem; color: #111827; letter-spacing: -0.005em; }
.mc-rank {
    font-size: 0.70rem; font-weight: 500; padding: 2px 8px;
    border-radius: 5px; color: #FFFFFF; letter-spacing: -0.005em;
}
.mc-stats { display: flex; gap: 24px; margin-bottom: 12px; }
.mc-stat-label { font-size: 0.70rem; color: #6B7280; font-weight: 500; }
.mc-stat-value {
    font-weight: 600; font-size: 1rem; color: #111827; margin-top: 2px;
    letter-spacing: -0.01em; font-feature-settings: "tnum";
}
.mc-stat-value.accent { color: #F7931D; }
.mc-best-badge {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 0.72rem; font-weight: 500; color: #10B981;
    background: rgba(16,185,129,0.10); padding: 2px 8px; border-radius: 5px; margin-left: 10px;
    letter-spacing: -0.005em;
}
/* 바 행 */
.mc-bar-row {
    display: flex; align-items: center; gap: 8px; margin-bottom: 4px;
}
.mc-bar-label {
    font-size: 0.72rem; color: #6B7280; width: 52px; text-align: right;
    flex-shrink: 0; font-weight: 500;
}
.mc-bar-track {
    flex: 1; height: 22px; background: #F3F4F6; border-radius: 4px;
    overflow: hidden; position: relative;
}
.mc-bar-fill {
    height: 22px; border-radius: 4px;
    display: flex; align-items: center; padding: 0 8px;
    transition: width 0.5s cubic-bezier(0.4,0,0.2,1);
}
.mc-bar-inner-text {
    font-size: 0.70rem; font-weight: 500; color: #FFFFFF; white-space: nowrap;
    letter-spacing: -0.005em;
}
.mc-bar-outer {
    display: flex; align-items: center; gap: 6px; flex-shrink: 0; margin-left: 6px;
}
.mc-bar-clicks {
    font-size: 0.74rem; font-weight: 600; color: #111827; min-width: 48px; text-align: right;
    font-feature-settings: "tnum";
}
.mc-bar-ctr {
    font-size: 0.70rem; color: #6B7280; min-width: 52px;
}
.mc-bar-sends {
    font-size: 0.65rem; color: #9CA3AF; min-width: 52px;
}
.mc-bar-dim .mc-bar-fill { opacity: 0.35; }
.mc-bar-dim .mc-bar-clicks { color: #9CA3AF; }
.mc-bar-dim .mc-bar-ctr { color: #D1D5DB; }
</style>
"""


# ──────────────────────────────────────────────
# 사분면 차트
# ──────────────────────────────────────────────

def _quadrant_chart(totals: pd.DataFrame) -> go.Figure:
    valid = totals[(totals['발송량'] > 0) & (totals['클릭수'] > 0)].copy()
    if valid.empty:
        return None

    colors = [CHART_COLORS[i % len(CHART_COLORS)] for i in range(len(valid))]
    max_clicks = valid['클릭수'].max()
    valid['bubble_size'] = (valid['클릭수'] / max_clicks * 40).clip(lower=8)

    # 중앙선
    med_sends = valid['발송량'].median()
    med_ctr = valid['CTR'].median()

    fig = go.Figure()

    for i, (_, row) in enumerate(valid.iterrows()):
        fig.add_trace(go.Scatter(
            x=[row['발송량']],
            y=[row['CTR']],
            mode='markers+text',
            marker=dict(size=row['bubble_size'], color=colors[i], opacity=0.8,
                        line=dict(width=1.5, color='#FFFFFF')),
            text=[row['매체명']],
            textposition='top center',
            textfont=dict(size=11, color='#4E5968', family='Pretendard, sans-serif'),
            cliponaxis=False,
            hovertemplate=(
                f'<b>{row["매체명"]}</b><br>'
                f'발송: {row["발송량"]:,.0f}건<br>'
                f'CTR: {row["CTR"]:.2f}%<br>'
                f'클릭: {row["클릭수"]:,.0f}회'
                f'<extra></extra>'
            ),
            showlegend=False,
        ))

    # 중앙 기준선
    fig.add_hline(y=med_ctr, line_dash="dot", line_color="#E0E0E0", line_width=1)
    fig.add_vline(x=med_sends, line_dash="dot", line_color="#E0E0E0", line_width=1)

    # 사분면 라벨
    x_range = valid['발송량'].max() - valid['발송량'].min()
    y_range = valid['CTR'].max() - valid['CTR'].min()
    x_min = valid['발송량'].min() - x_range * 0.15
    x_max = valid['발송량'].max() + x_range * 0.25
    y_min = max(0, valid['CTR'].min() - y_range * 0.2)
    y_max = valid['CTR'].max() + y_range * 0.25

    labels = [
        dict(x=x_min + x_range * 0.08, y=y_max - y_range * 0.05,
             text="🌱 성장 가능<br><span style='font-size:10px;color:#8B95A1'>효율 좋음 · 발송 늘려볼 것</span>",
             font=dict(size=12, color="#2E7D32")),
        dict(x=x_max - x_range * 0.15, y=y_max - y_range * 0.05,
             text="⭐ 스타 매체<br><span style='font-size:10px;color:#8B95A1'>효율+규모 모두 우수</span>",
             font=dict(size=12, color="#F7931D")),
        dict(x=x_min + x_range * 0.08, y=y_min + y_range * 0.05,
             text="❓ 검토 대상<br><span style='font-size:10px;color:#8B95A1'>축소 또는 중단</span>",
             font=dict(size=12, color="#8B95A1")),
        dict(x=x_max - x_range * 0.15, y=y_min + y_range * 0.05,
             text="🔧 개선 필요<br><span style='font-size:10px;color:#8B95A1'>소재·타겟 점검</span>",
             font=dict(size=12, color="#3182F6")),
    ]
    for lb in labels:
        fig.add_annotation(
            x=lb['x'], y=lb['y'], text=lb['text'],
            showarrow=False, font=lb['font'],
            xanchor='left', yanchor='top',
        )

    layout = {**PLOTLY_LAYOUT}
    layout.update(
        title=dict(text=""),
        height=420,
        margin=dict(t=20, l=60, r=30, b=50),
        xaxis=dict(
            title=dict(text="발송량 (규모)", font=dict(size=12, color='#8B95A1')),
            showgrid=True, gridcolor='#F2F4F6', gridwidth=1,
            tickfont=dict(size=10, color='#8B95A1'),
            range=[x_min, x_max],
        ),
        yaxis=dict(
            title=dict(text="CTR % (효율)", font=dict(size=12, color='#8B95A1')),
            showgrid=True, gridcolor='#F2F4F6', gridwidth=1,
            tickfont=dict(size=10, color='#8B95A1'),
            range=[y_min, y_max],
        ),
    )
    fig.update_layout(**layout)
    return fig


# ──────────────────────────────────────────────
# 매체 카드 (클릭수 기반 + CTR·발송량 컨텍스트)
# ──────────────────────────────────────────────

_compact = compact_num  # 후방호환 alias


def _render_media_card(
    media: str, media_df: pd.DataFrame,
    x_col: str, cat_order: list[str],
    color: str, rank: int,
    global_max_clicks: float, is_monthly: bool,
):
    # 기간별 데이터
    period_data = media_df.set_index(x_col).reindex(cat_order)

    # 통계
    total_sends = media_df['발송량'].sum()
    total_clicks = media_df['클릭수'].sum()
    avg_ctr = (total_clicks / total_sends * 100) if total_sends > 0 else 0

    # 클릭수 기준 최고 기간
    clicks_by_period = period_data['클릭수'].fillna(0)
    best_period = clicks_by_period.idxmax() if clicks_by_period.max() > 0 else ""

    # 랭크 색상
    rank_colors = ['#F7931D', '#3182F6', '#20C997', '#845EF7', '#FF6B6B']
    rank_bg = rank_colors[rank % len(rank_colors)] if rank < 5 else '#8B95A1'

    # 바 행
    bar_rows = []
    for period in cat_order:
        row = period_data.loc[period] if period in period_data.index else None
        clicks = row['클릭수'] if row is not None and not pd.isna(row['클릭수']) else 0
        sends = row['발송량'] if row is not None and not pd.isna(row['발송량']) else 0
        ctr = row['CTR'] if row is not None and not pd.isna(row['CTR']) else 0

        # 바 길이: 전체 매체의 최대 클릭수 대비
        bar_pct = (clicks / global_max_clicks * 100) if global_max_clicks > 0 else 0
        bar_pct = max(bar_pct, 0)

        # 짧은 라벨
        short = period.replace('년 ', '.').replace('월', '') if is_monthly else period.replace('요일', '')

        # 발송량 부족 여부
        is_dim = sends < MIN_SENDS_THRESHOLD and sends > 0
        dim_cls = ' mc-bar-dim' if is_dim else ''
        is_best = (period == best_period and clicks > 0)

        # 발송량 텍스트
        sends_txt = f'{_compact(sends)}건' if sends > 0 else '-'
        ctr_txt = f'CTR {ctr:.2f}%' if sends >= MIN_SENDS_THRESHOLD and sends > 0 else ('소량' if sends > 0 else '')
        clicks_txt = f'{_compact(clicks)}' if clicks > 0 else '-'
        best_mark = ' ★' if is_best else ''

        if bar_pct >= 15:
            bar_rows.append(
                f'<div class="mc-bar-row{dim_cls}">'
                f'  <div class="mc-bar-label">{short}</div>'
                f'  <div class="mc-bar-track">'
                f'    <div class="mc-bar-fill" style="width:{bar_pct}%; background:{color};">'
                f'      <span class="mc-bar-inner-text">{clicks_txt}{best_mark}</span>'
                f'    </div>'
                f'  </div>'
                f'  <div class="mc-bar-outer">'
                f'    <span class="mc-bar-ctr">{ctr_txt}</span>'
                f'    <span class="mc-bar-sends">{sends_txt}</span>'
                f'  </div>'
                f'</div>'
            )
        else:
            bar_rows.append(
                f'<div class="mc-bar-row{dim_cls}">'
                f'  <div class="mc-bar-label">{short}</div>'
                f'  <div class="mc-bar-track">'
                f'    <div class="mc-bar-fill" style="width:{max(bar_pct, 1)}%; background:{color}; opacity:0.5;"></div>'
                f'  </div>'
                f'  <div class="mc-bar-outer">'
                f'    <span class="mc-bar-clicks">{clicks_txt}{best_mark}</span>'
                f'    <span class="mc-bar-ctr">{ctr_txt}</span>'
                f'    <span class="mc-bar-sends">{sends_txt}</span>'
                f'  </div>'
                f'</div>'
            )

    bars_html = "".join(bar_rows)

    # 최적 기간 뱃지
    period_word = "월" if is_monthly else "요일"
    best_badge = ""
    if best_period and clicks_by_period.max() > 0:
        short_best = best_period.replace('년 ', '.').replace('월', '월') if is_monthly else best_period.replace('요일', '')
        best_badge = f'<span class="mc-best-badge">★ 클릭 최다 {period_word}: {short_best}</span>'

    st.markdown(
        f'<div class="mc-card">'
        f'  <div class="mc-header">'
        f'    <div class="mc-name">'
        f'      <div class="mc-dot" style="background:{color}"></div>'
        f'      <div class="mc-title">{media}</div>'
        f'      {best_badge}'
        f'    </div>'
        f'    <div class="mc-rank" style="background:{rank_bg}">{rank + 1}위</div>'
        f'  </div>'
        f'  <div class="mc-stats">'
        f'    <div><div class="mc-stat-label">평균 CTR</div><div class="mc-stat-value accent">{avg_ctr:.2f}%</div></div>'
        f'    <div><div class="mc-stat-label">총 발송</div><div class="mc-stat-value">{_compact(total_sends)}건</div></div>'
        f'    <div><div class="mc-stat-label">총 클릭</div><div class="mc-stat-value">{_compact(total_clicks)}회</div></div>'
        f'  </div>'
        f'  <div>{bars_html}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────
# 요일별 최적 발송 가이드
# ──────────────────────────────────────────────

_WEEKDAY_CSS = """
<style>
/* ===== Linear/Notion 스타일 ===== */
.wd-guide {
    background: #FFFFFF;
    border: 1px solid #E5E7EB;
    border-radius: 10px;
    padding: 18px 22px;
    margin-bottom: 16px;
    box-shadow: none;
}
.wd-guide-title {
    font-weight: 600; font-size: 0.92rem; color: #111827; margin-bottom: 14px;
    letter-spacing: -0.005em;
}
.wd-row {
    display: flex; align-items: center; gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid #F3F4F6;
}
.wd-row:last-child { border-bottom: none; }
.wd-media {
    font-weight: 600; font-size: 0.85rem; color: #111827;
    width: 130px; flex-shrink: 0;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    letter-spacing: -0.005em;
}
.wd-days {
    display: flex; gap: 5px; flex: 1;
}
.wd-day {
    width: 40px; height: 32px;
    border-radius: 6px;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.74rem; font-weight: 500;
    transition: all 0.15s ease;
}
.wd-day-best {
    color: #FFFFFF; font-weight: 600; font-size: 0.78rem;
    box-shadow: none;
    transform: none;
}
.wd-day-good {
    color: #FFFFFF; opacity: 0.75;
}
.wd-day-normal {
    background: #F3F4F6; color: #6B7280;
}
.wd-day-low {
    background: #FAFBFC; color: #D1D5DB;
}
.wd-rec {
    font-size: 0.76rem; color: #4B5563; width: 120px; text-align: right;
    flex-shrink: 0;
}
.wd-rec b { color: #F7931D; font-weight: 600; }
.wd-legend {
    display: flex; gap: 16px; margin-top: 14px;
    padding-top: 12px; border-top: 1px solid #F3F4F6;
    font-size: 0.72rem; color: #6B7280;
}
.wd-legend-item {
    display: flex; align-items: center; gap: 4px;
}
.wd-legend-dot {
    width: 12px; height: 12px; border-radius: 3px;
}
</style>
"""

_SHORT_DAYS = ['월', '화', '수', '목', '금', '토', '일']


def _render_weekday_guide(period_stats: pd.DataFrame, totals: pd.DataFrame):
    """매체별 최적 발송 요일 가이드 — 7일 미니 블록 형태"""
    st.markdown(_WEEKDAY_CSS, unsafe_allow_html=True)

    valid = period_stats[period_stats['발송량'] >= MIN_SENDS_THRESHOLD].copy()
    if valid.empty:
        valid = period_stats[period_stats['발송량'] > 0].copy()
    if valid.empty:
        return

    # 매체별 요일 클릭수 피벗
    media_order = (
        totals[totals['클릭수'] > 0]
        .sort_values('클릭수', ascending=False)['매체명'].tolist()
    )

    rows_html = []
    for idx, media in enumerate(media_order):
        mdf = valid[valid['매체명'] == media]
        if mdf.empty:
            continue

        color = CHART_COLORS[idx % len(CHART_COLORS)]

        # 요일별 클릭수
        day_clicks = {}
        for _, r in mdf.iterrows():
            day_name = r['요일']
            day_clicks[day_name] = r['클릭수']

        max_click = max(day_clicks.values()) if day_clicks else 0
        if max_click == 0:
            continue

        # 7일 블록 생성
        day_blocks = []
        best_day = ""
        best_clicks = 0
        for full_day in WEEKDAY_ORDER_FULL:
            short = full_day.replace('요일', '')
            clicks = day_clicks.get(full_day, 0)
            ratio = clicks / max_click if max_click > 0 else 0

            if clicks > best_clicks:
                best_clicks = clicks
                best_day = short

            if ratio >= 0.95:
                # 최고
                day_blocks.append(
                    f'<div class="wd-day wd-day-best" style="background:{color};">{short}</div>'
                )
            elif ratio >= 0.7:
                # 좋음
                day_blocks.append(
                    f'<div class="wd-day wd-day-good" style="background:{color};">{short}</div>'
                )
            elif ratio >= 0.4:
                # 보통
                day_blocks.append(
                    f'<div class="wd-day wd-day-normal">{short}</div>'
                )
            else:
                # 낮음
                day_blocks.append(
                    f'<div class="wd-day wd-day-low">{short}</div>'
                )

        days_html = "".join(day_blocks)

        rows_html.append(
            f'<div class="wd-row">'
            f'  <div class="wd-media">{media}</div>'
            f'  <div class="wd-days">{days_html}</div>'
            f'  <div class="wd-rec"><b>{best_day}요일</b> 추천</div>'
            f'</div>'
        )

    if not rows_html:
        return

    # ── 업종 벤치마크 최적 요일 행 추가 ──
    try:
        bench_wd = get_benchmark_weekday()
    except Exception:
        bench_wd = None

    industry_row_html = ""
    if bench_wd is not None and not bench_wd.empty:
        ind_max_ctr = bench_wd['CTR'].max()
        ind_best_day = ""
        ind_blocks = []
        for _, r in bench_wd.iterrows():
            short = r['요일'].replace('요일', '')
            ratio = r['CTR'] / ind_max_ctr if ind_max_ctr > 0 else 0
            if ratio >= 0.95:
                ind_best_day = short
                ind_blocks.append(
                    f'<div class="wd-day wd-day-best" style="background:#6C63FF;">{short}</div>'
                )
            elif ratio >= 0.7:
                ind_blocks.append(
                    f'<div class="wd-day wd-day-good" style="background:#6C63FF;">{short}</div>'
                )
            elif ratio >= 0.4:
                ind_blocks.append(f'<div class="wd-day wd-day-normal">{short}</div>')
            else:
                ind_blocks.append(f'<div class="wd-day wd-day-low">{short}</div>')

        industry_row_html = (
            f'<div class="wd-row" style="background:#F8F7FF;border-radius:8px;padding:10px 12px;margin-bottom:4px;">'
            f'  <div class="wd-media" style="color:#6C63FF;">🔥 업종 평균</div>'
            f'  <div class="wd-days">{"".join(ind_blocks)}</div>'
            f'  <div class="wd-rec"><b style="color:#6C63FF;">{ind_best_day}요일</b> 최적</div>'
            f'</div>'
        )

    all_rows = "".join(rows_html)

    st.markdown(
        f'<div style="font-weight:700; font-size:0.95rem; color:#191F28; margin:24px 0 4px 0;">'
        f'매체별 최적 발송 요일</div>'
        f'<div style="font-size:0.78rem; color:#8B95A1; margin-bottom:10px;">'
        f'색이 진할수록 클릭이 많았던 요일입니다. 매체마다 잘 되는 요일이 다릅니다.</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div class="wd-guide">'
        f'{industry_row_html}'
        f'{all_rows}'
        f'<div class="wd-legend">'
        f'  <div class="wd-legend-item"><div class="wd-legend-dot" style="background:#F7931D;"></div> 자사 최적</div>'
        f'  <div class="wd-legend-item"><div class="wd-legend-dot" style="background:#F7931D; opacity:0.8;"></div> 좋음</div>'
        f'  <div class="wd-legend-item"><div class="wd-legend-dot" style="background:#6C63FF;"></div> 업종 최적</div>'
        f'  <div class="wd-legend-item"><div class="wd-legend-dot" style="background:#F2F4F6;"></div> 보통</div>'
        f'</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────
# 렌더
# ──────────────────────────────────────────────

def render(df: pd.DataFrame):
    st.markdown(_CSS, unsafe_allow_html=True)

    st.markdown(section_header(
        "매체별 성과 분석",
        "어떤 매체가 좋은지, 그리고 언제 보내면 좋은지를 함께 봅니다.",
    ), unsafe_allow_html=True)

    render_insights(detect_heatmap(df))

    # ── 보기 기준 ──
    view = st.radio("보기 기준", ["월별", "요일별"], horizontal=True, key="heat_x")
    is_monthly = view == "월별"
    x_col = "년월" if is_monthly else "요일"
    cat_order = sorted(df['년월'].unique()) if is_monthly else WEEKDAY_ORDER_FULL

    # ── 데이터 ──
    df_sig = f"{len(df)}_{df['날짜'].min()}_{df['날짜'].max()}"
    period_stats = _build_media_period_stats(df_sig, df, x_col)
    totals = _build_media_totals(df_sig, df)

    if period_stats.empty:
        st.caption("분석할 데이터가 부족합니다.")
        st.divider()
        return

    # ═══════════════════════════════════════════
    # ① 사분면: 어떤 매체가 좋은가?
    # ═══════════════════════════════════════════
    st.markdown(
        '<div style="font-weight:700; font-size:0.95rem; color:#191F28; margin:20px 0 4px 0;">'
        '어떤 매체가 좋은가?</div>'
        '<div style="font-size:0.78rem; color:#8B95A1; margin-bottom:10px;">'
        '오른쪽 위에 있을수록 좋은 매체입니다. 원 크기 = 실제 클릭수</div>',
        unsafe_allow_html=True,
    )

    fig_q = _quadrant_chart(totals)
    if fig_q:
        st.plotly_chart(fig_q, width='stretch')

    # ═══════════════════════════════════════════
    # ①-b 요일별: 최적 발송 요일 가이드
    # ═══════════════════════════════════════════
    if not is_monthly:
        _render_weekday_guide(period_stats, totals)

    # ═══════════════════════════════════════════
    # ② 매체 카드: 언제 보내면 좋은가?
    # ═══════════════════════════════════════════
    period_word = "월" if is_monthly else "요일"
    st.markdown(
        f'<div style="font-weight:700; font-size:0.95rem; color:#191F28; margin:24px 0 4px 0;">'
        f'언제 보내면 좋은가? ({period_word}별 클릭수)</div>'
        f'<div style="font-size:0.78rem; color:#8B95A1; margin-bottom:6px;">'
        f'막대 = 클릭수 (실적) · 발송 {MIN_SENDS_THRESHOLD:,}건 미만은 흐리게 표시 (데이터 부족)</div>',
        unsafe_allow_html=True,
    )

    # 클릭수 기준 정렬
    media_rank = (
        totals[totals['클릭수'] > 0]
        .sort_values('클릭수', ascending=False)
    )
    global_max_clicks = period_stats['클릭수'].max() if not period_stats.empty else 1

    for rank, (_, row) in enumerate(media_rank.iterrows()):
        media = row['매체명']
        media_data = period_stats[period_stats['매체명'] == media]
        color = CHART_COLORS[rank % len(CHART_COLORS)]
        _render_media_card(
            media, media_data, x_col, cat_order, color,
            rank, global_max_clicks, is_monthly,
        )

    # ── 기존 히트맵 (접기) ──
    with st.expander("📊 격자형 히트맵 보기"):
        # CTR 히트맵
        ctr_df = period_stats.copy()
        fig = heatmap(
            ctr_df, x_col, "매체명", "CTR",
            f"매체별 CTR {period_word}별 현황",
            "Oranges", cat_order, is_pct=True,
        )
        st.plotly_chart(fig, width='stretch')

        # 원본 지표
        detail_tabs = st.tabs([f"📊 {m}" for m in TARGET_METRICS])
        for tab, metric in zip(detail_tabs, TARGET_METRICS):
            with tab:
                fig_d = heatmap(
                    df, x_col, "매체명", metric,
                    f"매체별 {metric} {period_word}별 현황",
                    "Blues", cat_order, height=500,
                )
                st.plotly_chart(fig_d, width='stretch')

    # ── 월별 드릴다운 ──
    if is_monthly:
        months = ["선택 안 함"] + sorted(df['년월'].unique())
        selected = st.selectbox("🔍 특정 월의 주차별 상세", months)

        if selected != "선택 안 함":
            week_df = add_week_columns(df[df['년월'] == selected])
            if not week_df.empty:
                week_order = sorted(week_df['주차'].unique())
                ctr_week = _build_media_period_stats(f"w_{selected}_{len(week_df)}", week_df, '주차')
                fig_w = heatmap(
                    ctr_week, "주차", "매체명", "CTR",
                    f"{selected} 주차별 CTR 상세",
                    "Blues", week_order, height=500, is_pct=True,
                )
                st.plotly_chart(fig_w, width='stretch')

    st.divider()
