"""
Section 3: 문구 길이 vs 성과 — 분위수 기반 동적 버킷.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from modules.config import BRAND_PRIMARY, MIN_SAMPLES_PER_CATEGORY

from ._shared import _layout


# ──────────────────────────────────────────────
# Section 3: 문구 길이 vs 성과
# ──────────────────────────────────────────────

def _render_length_analysis(raw_df: pd.DataFrame):
    """문구 길이와 성과(CTR) 관계 분석 — 분위수 기반 동적 버킷"""
    st.markdown(
        '<div style="font-size:0.92rem;font-weight:700;color:#191F28;'
        'margin-bottom:8px;">📏 문구 길이 vs 성과</div>',
        unsafe_allow_html=True,
    )

    if raw_df.empty or '문구길이' not in raw_df.columns:
        st.caption('문구 길이 데이터가 부족합니다.')
        return

    sends_col = '발송량' if '발송량' in raw_df.columns else '발송건'
    df = raw_df[raw_df['문구길이'] > 0].copy()
    if len(df) < MIN_SAMPLES_PER_CATEGORY * 3:  # 최소 3개 버킷 확보 가능할 때만
        st.caption(f'분석을 위한 데이터가 부족합니다 (최소 {MIN_SAMPLES_PER_CATEGORY * 3}건 필요, 현재 {len(df)}건).')
        return

    # ── 분위수 기반 동적 버킷 ──
    # 데이터 분포에 맞춰 4~5개 구간으로 자동 분할
    # 중복 경계 제거 (같은 길이가 많을 때)
    n_buckets = min(5, max(3, len(df) // MIN_SAMPLES_PER_CATEGORY))
    try:
        df['길이구간'] = pd.qcut(
            df['문구길이'], q=n_buckets, duplicates='drop',
        )
    except ValueError:
        st.caption('문구 길이 분포가 너무 단조로워 구간 분석이 어렵습니다.')
        return

    unique_buckets = df['길이구간'].cat.categories
    if len(unique_buckets) < 2:
        st.caption('문구 길이 분포가 너무 좁아 비교 분석이 어렵습니다 (대부분 비슷한 길이).')
        return

    # 구간 라벨을 가독성 있게 변환 (e.g., "(30.0, 120.0]" → "30~120자")
    def _fmt_interval(iv) -> str:
        lo = int(iv.left) if iv.left >= 0 else 0
        hi = int(iv.right)
        return f'{lo}~{hi}자'

    label_map = {iv: _fmt_interval(iv) for iv in unique_buckets}
    df['길이라벨'] = df['길이구간'].map(label_map)

    bucket = df.groupby('길이라벨', observed=True, sort=False).agg(
        캠페인수=('메시지', 'size'),
        발송건=(sends_col, 'sum'),
        클릭수=('클릭수', 'sum'),
        평균길이=('문구길이', 'mean'),
    ).reset_index()
    bucket['CTR'] = np.where(bucket['발송건'] > 0, bucket['클릭수'] / bucket['발송건'] * 100, 0)
    # 평균 길이 순으로 정렬 (짧은 → 긴)
    bucket = bucket.sort_values('평균길이').reset_index(drop=True)

    # 최소 표본 가드
    bucket = bucket[bucket['캠페인수'] >= max(3, MIN_SAMPLES_PER_CATEGORY // 2)].reset_index(drop=True)
    if len(bucket) < 2:
        st.caption('충분한 표본을 가진 길이 구간이 2개 이상 필요합니다.')
        return

    # Best 구간
    best_idx = bucket['CTR'].idxmax()
    best_range = bucket.loc[best_idx, '길이라벨']
    best_ctr = float(bucket.loc[best_idx, 'CTR'])
    best_n = int(bucket.loc[best_idx, '캠페인수'])
    best_sends = int(bucket.loc[best_idx, '발송건'])

    colors = [BRAND_PRIMARY if i == best_idx else '#D0D5DD' for i in bucket.index]

    _len_ymax = float(bucket['CTR'].max()) * 1.3
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=bucket['길이라벨'],
        y=bucket['CTR'],
        marker_color=colors,
        text=[f'{v:.2f}% (n={n})' for v, n in zip(bucket['CTR'], bucket['캠페인수'])],
        textposition='outside',
        textfont=dict(size=11),
        cliponaxis=False,
    ))
    fig.update_layout(**_layout(
        title=dict(text='문구 길이 구간별 CTR (분위수 기반)', font=dict(size=13)),
        height=320,
        xaxis=dict(title='문구 길이 구간', showgrid=False),
        yaxis=dict(title='CTR (%)', gridcolor='#F0F1F3', range=[0, _len_ymax]),
        showlegend=False,
        margin=dict(t=60, l=60, r=30, b=48),
    ))
    st.plotly_chart(fig, use_container_width=True)

    st.markdown(
        f'<div style="background:#FFF8F0;border-left:3px solid {BRAND_PRIMARY};'
        f'border-radius:0 10px 10px 0;padding:12px 16px;font-size:0.82rem;color:#4E5968;">'
        f'📌 가장 높은 CTR 구간: <b style="color:{BRAND_PRIMARY}">{best_range}</b> '
        f'(CTR {best_ctr:.2f}%, 표본 {best_n}건 / 발송 {best_sends:,}건)'
        f'<span style="color:#8B95A1;font-size:0.76rem;"> · 구간은 데이터 분포에 맞춰 분위수 기반으로 자동 분할됩니다. 길이가 CTR의 직접 원인이라는 뜻은 아닙니다.</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
