"""
Section 2: 문구 패턴 분석 — 금액/CTA/이모지/첫문장 유형별 CTR 비교.
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from modules.config import (
    BRAND_PRIMARY,
    MIN_SAMPLES_PER_CATEGORY,
    MIN_SAMPLES_FEATURE_SPLIT,
    MIN_LIFT_RELATIVE,
)

from ._shared import _layout
from ._classifiers import (
    _classify_money_amount,
    _first_sentence_type,
    _CTA_PATTERNS,
    _EMOJI_RE,
    _NUMBER_RE,
    _QUESTION_RE,
    _EXCLAMATION_RE,
)


# ──────────────────────────────────────────────
# Section 2: 문구 패턴 분석 (실행 가능한 인사이트)
# ──────────────────────────────────────────────

def _render_pattern_analysis(raw_df: pd.DataFrame):
    """실행 가능한 문구 패턴 분석 — 금액/CTA/이모지/첫문장 유형별 CTR 비교"""
    st.markdown(
        '<div style="font-size:0.92rem;font-weight:700;color:#191F28;'
        'margin-bottom:12px;">🎯 문구 패턴별 성과 분석</div>',
        unsafe_allow_html=True,
    )

    if raw_df.empty or '메시지' not in raw_df.columns:
        st.caption('패턴 분석을 위한 메시지 데이터가 없습니다.')
        return

    df = raw_df[raw_df['메시지'].notna() & (raw_df['메시지'].astype(str).str.strip() != '')].copy()
    if df.empty:
        st.caption('메시지가 있는 행이 없습니다.')
        return

    # 파생 피처
    df['_money'] = df['메시지'].apply(_classify_money_amount)
    df['_has_emoji'] = df['메시지'].apply(lambda t: bool(_EMOJI_RE.search(t)) if isinstance(t, str) else False)
    df['_has_number'] = df['메시지'].apply(lambda t: bool(_NUMBER_RE.search(t)) if isinstance(t, str) else False)
    df['_has_question'] = df['메시지'].apply(lambda t: bool(_QUESTION_RE.search(t)) if isinstance(t, str) else False)
    df['_has_exclaim'] = df['메시지'].apply(lambda t: bool(_EXCLAMATION_RE.search(t)) if isinstance(t, str) else False)
    df['_first_type'] = df['메시지'].apply(_first_sentence_type)

    # 가중 CTR 계산 — 공용 헬퍼 사용
    from modules._analytics_helpers import weighted_ctr as _weighted_ctr, get_sends_col
    _sends_col = get_sends_col(df)
    overall_ctr = _weighted_ctr(df)

    # ── 1. 혜택 금액대별 성과 ──
    money_df = df[df['_money'].notna()].copy()
    if len(money_df) >= MIN_SAMPLES_PER_CATEGORY:
        order = ['~1만원', '1~3만원', '3~5만원', '5~10만원', '10만원+']
        rows = []
        for bucket in order:
            sub = money_df[money_df['_money'] == bucket]
            # 표본 부족 구간 제외 (MIN_SAMPLES_PER_CATEGORY 미만)
            if len(sub) >= MIN_SAMPLES_PER_CATEGORY:
                rows.append({
                    '금액대': bucket,
                    'CTR': _weighted_ctr(sub),
                    '문구수': len(sub),
                    '발송': sub[_sends_col].sum(),
                })
        if rows:
            money_stats = pd.DataFrame(rows)
            c1, c2 = st.columns([3, 2])
            with c1:
                _money_ymax = float(money_stats['CTR'].max()) * 1.25  # 상단 라벨 여백
                fig = go.Figure(go.Bar(
                    x=money_stats['금액대'],
                    y=money_stats['CTR'],
                    marker_color=[
                        BRAND_PRIMARY if v == money_stats['CTR'].max() else '#B0BEC5'
                        for v in money_stats['CTR']
                    ],
                    text=[f'{v:.2f}% (n={n})' for v, n in zip(money_stats['CTR'], money_stats['문구수'])],
                    textposition='outside',
                    textfont=dict(size=11),
                    cliponaxis=False,
                ))
                fig.update_layout(**_layout(
                    title=dict(text='💰 혜택 금액대별 CTR', font=dict(size=13)),
                    height=300,
                    xaxis=dict(title='금액대', showgrid=False),
                    yaxis=dict(title='CTR (%)', showgrid=True, gridcolor='#F0F1F3',
                               range=[0, _money_ymax]),
                    margin=dict(t=60, l=60, r=30, b=48),
                ))
                st.plotly_chart(fig, use_container_width=True)
            with c2:
                best_row = money_stats.loc[money_stats['CTR'].idxmax()]
                # 중앙값(best 제외) 기준 비교 — 최악 대비가 아닌 나머지 중앙값 대비
                others = money_stats.drop(best_row.name)
                median_others = float(others['CTR'].median()) if len(others) else 0.0
                insight_tail = ''
                if median_others > 0 and (best_row['CTR'] / median_others) > (1 + MIN_LIFT_RELATIVE):
                    lift_pct = ((best_row['CTR'] / median_others) - 1) * 100
                    insight_tail = (
                        f' 나머지 구간 중앙값 대비 '
                        f'<b style="color:#2E7D32;">+{lift_pct:.0f}%</b> 수준입니다.'
                    )
                st.markdown(
                    f'<div style="background:#F8F9FA;border-radius:12px;padding:16px;height:100%;">'
                    f'<div style="font-size:0.78rem;font-weight:600;color:#8B95A1;margin-bottom:6px;">💡 인사이트</div>'
                    f'<div style="font-size:0.95rem;color:#191F28;line-height:1.6;">'
                    f'<b style="color:{BRAND_PRIMARY};">{best_row["금액대"]}</b> 구간이 '
                    f'CTR <b>{best_row["CTR"]:.2f}%</b>로 가장 높은 경향을 보입니다 '
                    f'(표본 {int(best_row["문구수"])}건).'
                    f'{insight_tail}'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )
            st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

    # ── 2. CTA 유형별 성과 ──
    cta_rows = []
    for cta_name in _CTA_PATTERNS.keys():
        mask = df['메시지'].apply(
            lambda t: any(kw.lower() in str(t).lower() for kw in _CTA_PATTERNS[cta_name])
        )
        sub = df[mask]
        # 카테고리별 충분 표본 요구
        if len(sub) >= MIN_SAMPLES_PER_CATEGORY:
            cta_rows.append({
                'CTA 유형': cta_name,
                'CTR': _weighted_ctr(sub),
                '문구수': len(sub),
            })
    if cta_rows:
        cta_stats = pd.DataFrame(cta_rows).sort_values('CTR', ascending=False).reset_index(drop=True)
        c1, c2 = st.columns([3, 2])
        with c1:
            _cta_xmax = float(cta_stats['CTR'].max()) * 1.35  # 우측 라벨 공간 확보
            fig = go.Figure(go.Bar(
                y=cta_stats['CTA 유형'],
                x=cta_stats['CTR'],
                orientation='h',
                marker_color=[
                    BRAND_PRIMARY if v == cta_stats['CTR'].max() else '#B0BEC5'
                    for v in cta_stats['CTR']
                ],
                text=[f'{v:.2f}% (n={n})' for v, n in zip(cta_stats['CTR'], cta_stats['문구수'])],
                textposition='outside',
                textfont=dict(size=11),
                cliponaxis=False,
            ))
            fig.update_layout(**_layout(
                title=dict(text='🎯 CTA 유형별 CTR', font=dict(size=13)),
                height=max(240, 40 * len(cta_stats) + 80),
                xaxis=dict(
                    title='CTR (%)', showgrid=True, gridcolor='#F0F1F3',
                    range=[0, _cta_xmax],
                ),
                yaxis=dict(autorange='reversed'),
                margin=dict(t=48, l=100, r=130, b=36),
            ))
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            best_cta = cta_stats.iloc[0]
            # 나머지 CTA의 중앙값 기준 (평균보다 outlier에 강건)
            median_others = float(cta_stats.iloc[1:]['CTR'].median()) if len(cta_stats) > 1 else 0.0
            lift_ratio = (best_cta['CTR'] / median_others) if median_others > 0 else 0.0
            insight_tail = ''
            if lift_ratio > (1 + MIN_LIFT_RELATIVE):
                lift_pct = (lift_ratio - 1) * 100
                insight_tail = (
                    f' 나머지 CTA 중앙값 대비 '
                    f'<b style="color:#2E7D32;">+{lift_pct:.0f}%</b> 수준.'
                )
            st.markdown(
                f'<div style="background:#F8F9FA;border-radius:12px;padding:16px;height:100%;">'
                f'<div style="font-size:0.78rem;font-weight:600;color:#8B95A1;margin-bottom:6px;">💡 인사이트</div>'
                f'<div style="font-size:0.95rem;color:#191F28;line-height:1.6;">'
                f'<b style="color:{BRAND_PRIMARY};">{best_cta["CTA 유형"]}</b> 유형이 '
                f'CTR <b>{best_cta["CTR"]:.2f}%</b>로 상대적으로 높은 경향 '
                f'(표본 {int(best_cta["문구수"])}건).'
                f'{insight_tail}'
                f'</div>'
                f'<div style="font-size:0.72rem;color:#8B95A1;margin-top:8px;line-height:1.5;">'
                f'※ 한 메시지가 여러 CTA에 동시에 매칭될 수 있어 카테고리 간 표본이 중복될 수 있습니다.'
                f'</div></div>',
                unsafe_allow_html=True,
            )
        st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

    # ── 3. 이모지/숫자/기호 효과 (2열) ──
    feature_rows = []
    for name, col in [('이모지 포함', '_has_emoji'), ('숫자 포함', '_has_number'),
                       ('느낌표 포함', '_has_exclaim'), ('물음표 포함', '_has_question')]:
        with_feat = df[df[col]]
        without_feat = df[~df[col]]
        # 양쪽 그룹 모두 최소 표본 필요 + 실효 차이(절대 15% 이상)만 채택
        if (len(with_feat) >= MIN_SAMPLES_FEATURE_SPLIT
                and len(without_feat) >= MIN_SAMPLES_FEATURE_SPLIT):
            ctr_w = _weighted_ctr(with_feat)
            ctr_wo = _weighted_ctr(without_feat)
            if ctr_wo <= 0:
                continue
            diff_pct = ((ctr_w / ctr_wo) - 1) * 100
            if abs(diff_pct) < 15:
                continue
            feature_rows.append({
                '패턴': name,
                '포함 CTR': ctr_w,
                '미포함 CTR': ctr_wo,
                '차이': diff_pct,
                '포함수': len(with_feat),
                '미포함수': len(without_feat),
            })

    if not feature_rows:
        st.caption('통계적으로 유의한 차이가 감지되지 않았습니다 (이모지/숫자/기호 포함 여부).')
        st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
    if feature_rows:
        feat_df = pd.DataFrame(feature_rows)
        c1, c2 = st.columns([3, 2])
        with c1:
            _feat_ymax = float(max(feat_df['포함 CTR'].max(), feat_df['미포함 CTR'].max())) * 1.3
            fig = go.Figure()
            fig.add_trace(go.Bar(
                name='포함',
                x=feat_df['패턴'],
                y=feat_df['포함 CTR'],
                marker_color=BRAND_PRIMARY,
                text=[f'{v:.2f}% (n={n})' for v, n in zip(feat_df['포함 CTR'], feat_df['포함수'])],
                textposition='outside',
                textfont=dict(size=10),
                cliponaxis=False,
            ))
            fig.add_trace(go.Bar(
                name='미포함',
                x=feat_df['패턴'],
                y=feat_df['미포함 CTR'],
                marker_color='#B0BEC5',
                text=[f'{v:.2f}% (n={n})' for v, n in zip(feat_df['미포함 CTR'], feat_df['미포함수'])],
                textposition='outside',
                textfont=dict(size=10),
                cliponaxis=False,
            ))
            fig.update_layout(**_layout(
                title=dict(text='✨ 기호·숫자 포함 여부별 CTR', font=dict(size=13)),
                height=320,
                barmode='group',
                xaxis=dict(showgrid=False),
                yaxis=dict(title='CTR (%)', showgrid=True, gridcolor='#F0F1F3',
                           range=[0, _feat_ymax]),
                margin=dict(t=60, l=60, r=30, b=48),
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1),
            ))
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            badges = []
            for _, r in feat_df.iterrows():
                color = '#2E7D32' if r['차이'] > 0 else '#E53935'
                sign = '+' if r['차이'] > 0 else ''
                badges.append(
                    f'<div style="background:white;border:1px solid #EBEEF2;border-radius:8px;'
                    f'padding:8px 10px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center;">'
                    f'<span style="font-size:0.82rem;color:#191F28;">{r["패턴"]}'
                    f'<span style="color:#8B95A1;font-size:0.72rem;margin-left:4px;">'
                    f'(표본 {int(r["포함수"])} vs {int(r["미포함수"])}건)</span></span>'
                    f'<span style="font-size:0.82rem;color:{color};font-weight:700;">{sign}{r["차이"]:.0f}%</span>'
                    f'</div>'
                )
            st.markdown(
                f'<div style="background:#F8F9FA;border-radius:12px;padding:12px;height:100%;">'
                f'<div style="font-size:0.78rem;font-weight:600;color:#8B95A1;margin-bottom:8px;">📊 포함 vs 미포함 CTR 차이 (상관관계 기반)</div>'
                + ''.join(badges) +
                f'</div>',
                unsafe_allow_html=True,
            )
        st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

    # ── 4. 첫 문장 유형별 성과 ──
    first_rows = []
    for ftype, sub in df.groupby('_first_type'):
        # 유형별 최소 표본 요구
        if len(sub) >= MIN_SAMPLES_PER_CATEGORY:
            first_rows.append({
                '첫 문장 유형': ftype,
                'CTR': _weighted_ctr(sub),
                '문구수': len(sub),
            })
    if first_rows:
        first_stats = pd.DataFrame(first_rows).sort_values('CTR', ascending=False).reset_index(drop=True)
        c1, c2 = st.columns([3, 2])
        with c1:
            _first_ymax = float(first_stats['CTR'].max()) * 1.25
            fig = go.Figure(go.Bar(
                x=first_stats['첫 문장 유형'],
                y=first_stats['CTR'],
                marker_color=[
                    BRAND_PRIMARY if v == first_stats['CTR'].max() else '#B0BEC5'
                    for v in first_stats['CTR']
                ],
                text=[f'{v:.2f}% (n={n})' for v, n in zip(first_stats['CTR'], first_stats['문구수'])],
                textposition='outside',
                textfont=dict(size=11),
                cliponaxis=False,
            ))
            fig.update_layout(**_layout(
                title=dict(text='📝 첫 문장 유형별 CTR', font=dict(size=13)),
                height=300,
                xaxis=dict(title='유형', showgrid=False),
                yaxis=dict(title='CTR (%)', showgrid=True, gridcolor='#F0F1F3',
                           range=[0, _first_ymax]),
                margin=dict(t=60, l=60, r=30, b=48),
            ))
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            best_first = first_stats.iloc[0]
            st.markdown(
                f'<div style="background:#F8F9FA;border-radius:12px;padding:16px;height:100%;">'
                f'<div style="font-size:0.78rem;font-weight:600;color:#8B95A1;margin-bottom:6px;">💡 인사이트</div>'
                f'<div style="font-size:0.95rem;color:#191F28;line-height:1.6;">'
                f'첫 문장이 <b style="color:{BRAND_PRIMARY};">{best_first["첫 문장 유형"]}</b>인 문구의 '
                f'CTR이 <b>{best_first["CTR"]:.2f}%</b>로 상대적으로 높은 경향이 있습니다 '
                f'(상관관계 기반, 표본 {int(best_first["문구수"])}건).'
                f'</div>'
                f'<div style="font-size:0.72rem;color:#8B95A1;margin-top:8px;line-height:1.5;">'
                f'※ 첫 문장 유형 분류는 키워드 휴리스틱(규칙 기반) 방식이며, 인과관계를 보장하지 않습니다.'
                f'</div></div>',
                unsafe_allow_html=True,
            )
