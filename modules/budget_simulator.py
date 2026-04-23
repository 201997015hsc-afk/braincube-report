"""
예산 증액 시뮬레이터
예산 → 발송량 → 클릭수 인과 체인을 시뮬레이션합니다.
매체별 건당 발송 단가(CPS)와 CTR을 기반으로
증액 시 기대되는 추가 성과를 자동 계산합니다.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from modules.config import (
    section_header, alert_card_html, kpi_card_html,
    CHART_COLORS, PLOTLY_LAYOUT, BRAND_PRIMARY, COLOR_BLUE, compact_num,
)
from modules.data_processing import calc_ctr, render_ref_period_selector
from modules.insights import detect_simulator, render_insights


# ──────────────────────────────────────────────
# 수확체감 + 포화 감지
# ──────────────────────────────────────────────

def _diminishing_factor(inc_ratio: float, elasticity: float = 2.0) -> float:
    """
    수확체감 계수 — 증액 비율이 클수록 실효 비율이 감소합니다.
    inc_ratio: 증액분 / 현재예산 (0.3 = 30% 증액)
    returns: 0~1 실효 계수 (1에 가까울수록 선형에 가까움)

    예시) elasticity=2.0 기준
      10% 증액 → 91% 효율 (거의 선형)
      30% 증액 → 78% 효율
      50% 증액 → 69% 효율
     100% 증액 → 55% 효율
     200% 증액 → 40% 효율
    """
    if inc_ratio <= 0:
        return 1.0
    x = elasticity * inc_ratio
    return float(np.log1p(x) / x)  # ln(1+x) / x


def _detect_saturation(df: pd.DataFrame) -> list[dict]:
    """매체별 포화 신호 감지 — 최근 3개월 vs 이전 3개월 CTR·CPC 추이 비교"""
    results = []
    for media in df['매체명'].unique():
        m_df = df[df['매체명'] == media]
        months = (
            m_df.groupby('년월')
            .agg({'집행금액': 'sum', '발송량': 'sum', '클릭수': 'sum'})
            .reset_index()
            .sort_values('년월')
        )
        if len(months) < 6:
            continue

        months['CTR'] = np.where(
            months['발송량'] > 0, months['클릭수'] / months['발송량'] * 100, 0
        )
        months['CPC'] = np.where(
            months['클릭수'] > 0, months['집행금액'] / months['클릭수'], 0
        )

        recent_ctr = float(months['CTR'].tail(3).mean())
        prev_ctr = float(months['CTR'].iloc[-6:-3].mean())
        recent_cpc = float(months['CPC'].tail(3).mean())
        prev_cpc = float(months['CPC'].iloc[-6:-3].mean())

        score = 0
        signals: list[str] = []

        if prev_ctr > 0:
            ctr_chg = (recent_ctr - prev_ctr) / prev_ctr * 100
            if ctr_chg < -10:
                score += 40
                signals.append(f"CTR {ctr_chg:+.1f}%")
            elif ctr_chg < -5:
                score += 20
                signals.append(f"CTR {ctr_chg:+.1f}%")

        if prev_cpc > 0:
            cpc_chg = (recent_cpc - prev_cpc) / prev_cpc * 100
            if cpc_chg > 15:
                score += 40
                signals.append(f"CPC {cpc_chg:+.1f}%")
            elif cpc_chg > 8:
                score += 20
                signals.append(f"CPC {cpc_chg:+.1f}%")

        if score > 0:
            results.append(dict(media=media, score=min(score, 100), signals=signals))

    return sorted(results, key=lambda x: x['score'], reverse=True)


# ──────────────────────────────────────────────
# 기준치 계산
# ──────────────────────────────────────────────

def _calc_baseline(df: pd.DataFrame) -> pd.DataFrame:
    """매체별 월평균 기준치 + CPS/CTR/CPC 산출"""
    stats = df.groupby('매체명').agg({
        '집행금액': 'sum', '발송량': 'sum', '클릭수': 'sum',
    }).reset_index()

    stats = stats[(stats['발송량'] > 0) & (stats['클릭수'] > 0)].copy()
    if stats.empty:
        return stats

    months = df.groupby('매체명')['년월'].nunique()
    stats['월수'] = months.reindex(stats['매체명']).values.clip(min=1)

    stats['월예산'] = stats['집행금액'] / stats['월수']
    stats['월발송'] = stats['발송량'] / stats['월수']
    stats['월클릭'] = stats['클릭수'] / stats['월수']

    stats['CPS'] = stats['집행금액'] / stats['발송량']     # 건당 발송 단가
    stats['CTR'] = calc_ctr(stats)                         # 클릭률 (%)
    stats['CPC'] = stats['집행금액'] / stats['클릭수']     # 클릭당 비용

    # ── 🔒 내부: Firebase 시장 표준 단가로 CPS 보정 ──
    from modules._analytics_helpers import apply_market_price_correction
    stats = apply_market_price_correction(stats, media_col='매체명', cps_col='CPS')

    return stats.sort_values('월예산', ascending=False)


# ──────────────────────────────────────────────
# 시뮬레이션 엔진
# ──────────────────────────────────────────────

def _simulate(baseline: pd.DataFrame, increase: float, mode: str) -> pd.DataFrame:
    """증액분을 배분하고, 추가 발송·클릭을 계산"""
    result = baseline.copy()

    if mode == "현재 비율대로":
        total_budget = result['월예산'].sum()
        if total_budget <= 0:
            result['배분비율'] = 1 / len(result)
        else:
            result['배분비율'] = result['월예산'] / total_budget
    else:
        ctr_max = result['CTR'].max()
        cpc_max = result['CPC'].max()
        ctr_s = result['CTR'] / ctr_max if ctr_max > 0 else 0
        cpc_s = 1 - (result['CPC'] / cpc_max) if cpc_max > 0 else 0
        score = ctr_s * 0.6 + cpc_s * 0.4
        total = score.sum()
        result['배분비율'] = (score / total) if total > 0 else (1 / len(result))

    result['증액분'] = result['배분비율'] * increase

    # 선형 추정 — 시장 단가 보정된 CPS 사용 (내부)
    cps_col = 'CPS_보정' if 'CPS_보정' in result.columns else 'CPS'
    result['추가발송_선형'] = (result['증액분'] / result[cps_col]).astype(int)
    result['추가클릭_선형'] = (result['추가발송_선형'] * result['CTR'] / 100).astype(int)

    # 수확체감 반영
    inc_ratio = result['증액분'] / result['월예산'].clip(lower=1)
    result['체감계수'] = inc_ratio.apply(_diminishing_factor)
    result['추가발송'] = (result['추가발송_선형'] * result['체감계수']).astype(int)
    result['추가클릭'] = (result['추가발송'] * result['CTR'] / 100).astype(int)

    result['시뮬_예산'] = result['월예산'] + result['증액분']
    result['시뮬_발송'] = (result['월발송'] + result['추가발송']).astype(int)
    result['시뮬_클릭'] = (result['월클릭'] + result['추가클릭']).astype(int)
    result['증액CPC'] = np.where(
        result['추가클릭'] > 0,
        result['증액분'] / result['추가클릭'],
        0,
    )

    return result


# ──────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────

_compact = compact_num  # 후방호환 alias


def _hex_to_rgba(hex_color: str, alpha: float = 0.4) -> str:
    """#FF6B6B → rgba(255,107,107,0.4)"""
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ──────────────────────────────────────────────
# 전환 체인 요약 (HTML)
# ──────────────────────────────────────────────

def _flow_summary_html(total_budget: float, total_sends: int, total_clicks: int,
                       avg_cps: float, avg_ctr: float) -> str:
    """예산→발송→클릭 3단계 전환 카드"""
    return (
        '<div style="display:flex;align-items:center;justify-content:center;gap:0;'
        'margin:12px 0 4px;flex-wrap:wrap;">'
        # ── 예산 ──
        '<div style="background:#FFF5EB;border:1.5px solid #FFD19A;border-radius:14px;'
        'padding:14px 24px;text-align:center;min-width:130px;">'
        '<div style="font-size:0.72rem;color:#8B95A1;margin-bottom:3px;">증액 예산</div>'
        f'<div style="font-size:1.2rem;font-weight:700;color:{BRAND_PRIMARY};">'
        f'+{_compact(total_budget, "원")}</div></div>'
        # ── 화살표 + CPS ──
        '<div style="padding:0 8px;text-align:center;line-height:1;">'
        '<div style="color:#B0B8C1;font-size:1.4rem;">→</div>'
        f'<div style="font-size:0.62rem;color:#8B95A1;margin-top:1px;">CPS {avg_cps:,.0f}원</div></div>'
        # ── 발송 ──
        '<div style="background:#EFF6FF;border:1.5px solid #AAD4FF;border-radius:14px;'
        'padding:14px 24px;text-align:center;min-width:130px;">'
        '<div style="font-size:0.72rem;color:#8B95A1;margin-bottom:3px;">추가 발송</div>'
        f'<div style="font-size:1.2rem;font-weight:700;color:#3182F6;">'
        f'+{_compact(total_sends, "건")}</div></div>'
        # ── 화살표 + CTR ──
        '<div style="padding:0 8px;text-align:center;line-height:1;">'
        '<div style="color:#B0B8C1;font-size:1.4rem;">→</div>'
        f'<div style="font-size:0.62rem;color:#8B95A1;margin-top:1px;">CTR {avg_ctr:.2f}%</div></div>'
        # ── 클릭 ──
        '<div style="background:#EAFAF1;border:1.5px solid #A3E4BC;border-radius:14px;'
        'padding:14px 24px;text-align:center;min-width:130px;">'
        '<div style="font-size:0.72rem;color:#8B95A1;margin-bottom:3px;">추가 클릭</div>'
        f'<div style="font-size:1.2rem;font-weight:700;color:#20C997;">'
        f'+{total_clicks:,}회</div></div>'
        '</div>'
    )


# ──────────────────────────────────────────────
# Sankey 흐름도 (예산→매체→클릭)
# ──────────────────────────────────────────────

def _chain_flow_chart(result: pd.DataFrame) -> go.Figure:
    """예산→매체→클릭 Sankey 흐름도.
    좌측 폭 = 예산 배분 비중, 우측 폭 = 클릭 기여 비중.
    흐름이 넓어지는 매체 = 예산 대비 클릭 전환 효율이 높음.
    """
    top = result.nlargest(7, '추가클릭').sort_values('추가클릭', ascending=False)
    n = len(top)
    if n == 0:
        return go.Figure()

    total_budget = top['증액분'].sum()
    total_clicks = top['추가클릭'].sum()
    if total_budget <= 0 or total_clicks <= 0:
        return go.Figure()

    # ── Nodes: [0] 총 증액  |  [1..n] 매체  |  [n+1] 총 클릭 ──
    node_labels = [f"총 증액\n{_compact(total_budget, '원')}"]
    for _, r in top.iterrows():
        node_labels.append(f"{r['매체명']}\n📨 +{_compact(r['추가발송'], '건')}")
    node_labels.append(f"추가 클릭\n+{_compact(total_clicks, '회')}")

    n_colors = len(CHART_COLORS)
    media_colors = [CHART_COLORS[i % n_colors] for i in range(n)]
    node_colors = [BRAND_PRIMARY] + media_colors + ['#20C997']

    # ── Links ──
    sources, targets, values, link_colors, link_cdata = [], [], [], [], []

    # Stage 1: 총 예산 → 각 매체 (예산 배분)
    for i, (_, r) in enumerate(top.iterrows()):
        sources.append(0)
        targets.append(i + 1)
        values.append(r['증액분'])
        link_colors.append(_hex_to_rgba(media_colors[i], 0.45))
        pct = r['증액분'] / total_budget * 100
        link_cdata.append(f"{r['매체명']}: +{_compact(r['증액분'], '원')} ({pct:.0f}%)")

    # Stage 2: 각 매체 → 클릭 전환 (클릭을 예산 단위로 스케일링)
    click_scale = total_budget / total_clicks
    for i, (_, r) in enumerate(top.iterrows()):
        scaled = max(r['추가클릭'] * click_scale, total_budget * 0.005)
        sources.append(i + 1)
        targets.append(n + 1)
        values.append(scaled)
        link_colors.append(_hex_to_rgba(media_colors[i], 0.28))
        cpc_txt = f"{r['증액CPC']:,.0f}원" if r['증액CPC'] > 0 else "-"
        link_cdata.append(f"{r['매체명']}: +{r['추가클릭']:,}회 · CPC {cpc_txt}")

    fig = go.Figure(go.Sankey(
        arrangement='snap',
        node=dict(
            label=node_labels,
            pad=18,
            thickness=26,
            color=node_colors,
            line=dict(width=0),
            hovertemplate='%{label}<extra></extra>',
        ),
        link=dict(
            source=sources,
            target=targets,
            value=values,
            color=link_colors,
            customdata=link_cdata,
            hovertemplate='%{customdata}<extra></extra>',
        ),
    ))

    layout = {**PLOTLY_LAYOUT}
    layout.update(
        title=dict(text=""),
        height=max(380, n * 52 + 130),
        margin=dict(t=36, l=5, r=5, b=25),
        font=dict(size=11, family='Pretendard, sans-serif', color='#4E5968'),
    )
    fig.update_layout(**layout)

    # 좌우 컬럼 라벨
    fig.add_annotation(
        x=0.01, y=1.07, text="<b>💰 예산 배분</b>", showarrow=False,
        xref='paper', yref='paper',
        font=dict(size=12, color='#4E5968', family='Pretendard, sans-serif'),
    )
    fig.add_annotation(
        x=0.99, y=1.07, text="<b>🖱️ 클릭 전환</b>", showarrow=False,
        xref='paper', yref='paper', xanchor='right',
        font=dict(size=12, color='#4E5968', family='Pretendard, sans-serif'),
    )

    return fig


# ──────────────────────────────────────────────
# 비교 차트 (현재 vs 증액 후)
# ──────────────────────────────────────────────

def _compare_chart(result: pd.DataFrame, period_label: str = "") -> go.Figure:
    """현재 월 클릭 vs 시뮬레이션 클릭 그룹 바"""
    chart_df = result.sort_values('시뮬_클릭', ascending=True)

    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=chart_df['매체명'], x=chart_df['월클릭'].astype(int),
        name=f'기준 ({period_label})' if period_label else '현재',
        orientation='h',
        marker_color='#D4D9E1',
        marker_cornerradius=6,
        texttemplate='%{x:,.0f}',
        textposition='inside', insidetextanchor='end',
        textfont=dict(size=11, color='#8B95A1'),
    ))
    fig.add_trace(go.Bar(
        y=chart_df['매체명'], x=chart_df['시뮬_클릭'],
        name='증액 후 예상',
        orientation='h',
        marker_color=BRAND_PRIMARY,
        marker_cornerradius=6,
        texttemplate='%{x:,.0f}',
        textposition='inside', insidetextanchor='end',
        textfont=dict(size=11, color='#FFFFFF'),
    ))

    bar_h = max(350, len(chart_df) * 55 + 80)
    layout = {**PLOTLY_LAYOUT}
    layout.update(
        height=bar_h,
        title={**PLOTLY_LAYOUT['title'], 'text': f"매체별 월 클릭수: 기준({period_label}) vs 증액 후" if period_label else "매체별 월 클릭수: 현재 vs 증액 후"},
        barmode='group',
        xaxis=dict(showgrid=False, showticklabels=False, title=""),
        yaxis=dict(title="", tickfont=dict(size=12, color='#4E5968')),
        margin=dict(t=48, l=120, r=20, b=30),
        legend=dict(
            orientation='h', y=1.06, x=0.5, xanchor='center',
            font=dict(size=11),
        ),
    )
    fig.update_layout(**layout)
    return fig


# ──────────────────────────────────────────────
# 매체별 상세 리스트
# ──────────────────────────────────────────────

def _render_detail_list(result: pd.DataFrame):
    """매체별 증액 체인 카드 리스트"""
    st.markdown(
        '<div style="font-size:0.82rem; color:#8B95A1; margin-bottom:12px; margin-top:8px; font-weight:600; letter-spacing:-0.01em;">매체별 증액 상세</div>',
        unsafe_allow_html=True,
    )
    for idx, (_, row) in enumerate(result.iterrows()):
        color = CHART_COLORS[idx % len(CHART_COLORS)]
        add_budget_txt = f"{row['증액분'] / 10000:,.0f}만원" if row['증액분'] >= 10000 else f"{row['증액분']:,.0f}원"
        cpc_txt = f"{row['증액CPC']:,.0f}원" if row['증액CPC'] > 0 else "-"
        pct = row['배분비율'] * 100
        bar_w = max(pct, 2)

        st.markdown(
            f'<div class="budget-card">'
            f'<div class="budget-dot" style="background:{color}"></div>'
            f'<div class="budget-info">'
            f'<div class="budget-name">{row["매체명"]}</div>'
            f'<div class="budget-bar-bg"><div class="budget-bar-fill" style="background:{color}; width:{bar_w}%"></div></div>'
            f'</div>'
            f'<div class="budget-stats">'
            f'<div class="budget-pct">+{add_budget_txt}</div>'
            f'<div class="budget-detail">발송 +{row["추가발송"]:,}건 · 클릭 +{row["추가클릭"]:,}회 · CPC {cpc_txt}</div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────

def render(df: pd.DataFrame):
    from modules.ui_helpers import render_page_header
    render_page_header(
        title="예산 증액 시뮬레이터",
        kicker="내부 도구",
        subtitle=(
            "예산을 더 쓰면 발송량이 늘고, 클릭수도 따라 올라갑니다. "
            "수확체감 효과가 자동 반영된 현실적 시뮬레이션입니다."
        ),
    )

    # ── 면책 안내 ──
    st.markdown(alert_card_html(
        "info",
        "시뮬레이션 가정 안내",
        "매체별 과거 CPS·CTR 기반 추정이며, <b>수확체감(증액 폭 ↑ → 단위 효율 ↓)</b>이 반영됩니다. "
        "자사 단가가 시장 표준과 크게 다를 경우 <b>업계 중앙값 단가</b>를 자동 적용하여 예측 정확도를 높입니다. "
        "실제 집행 시 매체사 단가 변동·타겟 소진 등에 따라 결과가 달라질 수 있습니다.",
    ), unsafe_allow_html=True)

    # ── 최소 데이터 검증 ──
    if '년월' not in df.columns or df['년월'].nunique() < 2:
        st.info("📊 예산 시뮬레이터는 **2개월 이상**의 데이터가 필요합니다. 데이터가 누적되면 자동으로 활성화됩니다.")
        st.divider()
        return

    # ── 기준 기간 선택 ──
    ref_df, period_label = render_ref_period_selector(df, "sim")
    baseline = _calc_baseline(ref_df)

    # 시장 단가 보정 적용 매체 수 표시 (내부 — 수치는 숨김)
    from modules._analytics_helpers import count_adjusted_rows
    _n_adjusted = count_adjusted_rows(baseline)
    if _n_adjusted > 0:
        st.caption(
            f'🎯 <b>{_n_adjusted}개 매체</b>에 업계 표준 단가 기준 보정 적용됨 '
            f'(자사 실적 단가가 시장 중앙값 ±50% 범위를 벗어나는 경우).',
            unsafe_allow_html=True,
        )
    if baseline.empty:
        st.caption("선택 기간에 발송 및 클릭 데이터가 있는 매체가 필요합니다.")
        st.divider()
        return

    # 현재 월평균 총 예산
    current_monthly = int(baseline['월예산'].sum())
    current_man = max(int(current_monthly / 10_000), 100)

    # ── 컨트롤 ──
    ctrl1, ctrl2 = st.columns([2, 1], gap="large")
    with ctrl1:
        increase_man = st.slider(
            "월 예산 증액 (만 원)",
            min_value=50, max_value=max(current_man * 2, 5000),
            value=max(int(current_man * 0.3), 50),
            step=50,
            key="sim_budget",
        )
    with ctrl2:
        mode = st.radio(
            "증액분 배분 방식",
            ["현재 비율대로", "효율 기반"],
            horizontal=True,
            key="sim_mode",
            help="현재 비율: 기존 매체별 예산 비율 유지  |  효율 기반: CTR·CPC 효율이 좋은 매체에 집중",
        )

    increase = increase_man * 10_000

    result = _simulate(baseline, increase, mode)

    # ── 포화 매체 경고 (전체 기간 추이 기반) ──
    saturated = _detect_saturation(df)
    if saturated:
        high_risk = [s for s in saturated if s['score'] >= 40]
        if high_risk:
            sat_names = ", ".join(
                f"<b>{s['media']}</b> ({' · '.join(s['signals'])})"
                for s in high_risk[:3]
            )
            st.markdown(alert_card_html(
                "warning",
                "효율 하락 감지 매체",
                f"최근 3개월 기준 효율이 하락 중인 매체: {sat_names}. "
                f"해당 매체 증액 시 기대 효과가 낮을 수 있습니다.",
            ), unsafe_allow_html=True)

    # ── 인사이트 칩 ──
    render_insights(detect_simulator(result, increase, current_monthly))

    # ── Summary KPIs ──
    total_add_click = result['추가클릭'].sum()
    total_add_send = result['추가발송'].sum()
    add_cpc = increase / total_add_click if total_add_click > 0 else 0
    current_cpc = baseline['집행금액'].sum() / baseline['클릭수'].sum() if baseline['클릭수'].sum() > 0 else 0
    click_inc_pct = (total_add_click / baseline['월클릭'].sum() * 100) if baseline['월클릭'].sum() > 0 else 0

    kpi_cols = st.columns(4, gap="medium")
    with kpi_cols[0]:
        budget_pct = (increase_man / current_man * 100) if current_man > 0 else 0
        st.markdown(kpi_card_html(
            "증액 예산",
            f"+{increase_man:,}만원",
            f"기준 월 {current_man:,}만원 대비 +{budget_pct:.0f}%",
        ), unsafe_allow_html=True)
    with kpi_cols[1]:
        st.markdown(kpi_card_html(
            "추가 발송 기대",
            f"+{total_add_send:,}건",
            f"기준 월 {int(baseline['월발송'].sum()):,}건",
        ), unsafe_allow_html=True)
    with kpi_cols[2]:
        linear_click = int(result['추가클릭_선형'].sum()) if '추가클릭_선형' in result.columns else total_add_click
        dim_note = ""
        if linear_click > 0 and total_add_click < linear_click:
            dim_pct = (1 - total_add_click / linear_click) * 100
            dim_note = f" (선형 {linear_click:,} → 체감 -{dim_pct:.0f}%)"
        st.markdown(kpi_card_html(
            "추가 클릭 기대",
            f"+{total_add_click:,}회",
            f"클릭 +{click_inc_pct:.1f}% 증가{dim_note}",
            delta_type="up",
        ), unsafe_allow_html=True)
    with kpi_cols[3]:
        cpc_delta = ""
        if current_cpc > 0 and add_cpc > 0:
            cpc_delta = f"기준 CPC {current_cpc:,.0f}원" if add_cpc <= current_cpc else f"기준 {current_cpc:,.0f}원 대비 ↑"
        st.markdown(kpi_card_html(
            "증액분 CPC",
            f"{add_cpc:,.0f}원",
            cpc_delta,
        ), unsafe_allow_html=True)

    st.markdown('<div style="margin-top:20px"></div>', unsafe_allow_html=True)

    # ── 차트 (탭) ──
    tab_chain, tab_compare = st.tabs(["🔗 증액 흐름 (예산→발송→클릭)", "📊 현재 vs 증액 비교"])
    with tab_chain:
        # 전환 체인 요약 카드
        avg_cps = increase / total_add_send if total_add_send > 0 else 0
        avg_ctr = total_add_click / total_add_send * 100 if total_add_send > 0 else 0
        st.markdown(
            _flow_summary_html(increase, total_add_send, total_add_click, avg_cps, avg_ctr),
            unsafe_allow_html=True,
        )
        # Sankey 흐름도
        st.plotly_chart(_chain_flow_chart(result), width='stretch')
        st.caption("💡 흐름이 넓어지는 매체 = 예산 대비 클릭 전환 효율이 높은 매체입니다. 마우스를 올려 상세 수치를 확인하세요.")
    with tab_compare:
        st.plotly_chart(_compare_chart(result, period_label), width='stretch')

    # ── 매체별 상세 ──
    _render_detail_list(result)

    # ── ROI 종합 판단 ──
    st.markdown("<br>", unsafe_allow_html=True)
    if add_cpc > 0 and current_cpc > 0:
        if add_cpc <= current_cpc:
            saving_pct = (1 - add_cpc / current_cpc) * 100
            st.markdown(alert_card_html(
                "success",
                "증액 효율 양호",
                f"증액분 CPC <b>{add_cpc:,.0f}원</b>이 현재 CPC <b>{current_cpc:,.0f}원</b>보다 "
                f"<b>{saving_pct:.0f}% 저렴</b>합니다. "
                f"월 <b>{increase_man:,}만원</b> 증액 시 <b>+{total_add_click:,}회 클릭</b>이 추가로 기대됩니다.",
            ), unsafe_allow_html=True)
        elif add_cpc <= current_cpc * 1.2:
            st.markdown(alert_card_html(
                "info",
                "증액 효율 보통",
                f"증액분 CPC <b>{add_cpc:,.0f}원</b>이 현재 CPC와 유사한 수준입니다. "
                f"추가 <b>+{total_add_click:,}회 클릭</b> 확보 가능하지만, 효율 개선 여지를 검토하세요.",
            ), unsafe_allow_html=True)
        else:
            excess_pct = (add_cpc / current_cpc - 1) * 100
            st.markdown(alert_card_html(
                "warning",
                "증액 효율 주의",
                f"증액분 CPC <b>{add_cpc:,.0f}원</b>이 현재 CPC 대비 <b>{excess_pct:.0f}% 높습니다</b>. "
                f"'효율 기반' 배분으로 전환하거나, 증액 규모를 조정해 보세요.",
            ), unsafe_allow_html=True)

    st.divider()
