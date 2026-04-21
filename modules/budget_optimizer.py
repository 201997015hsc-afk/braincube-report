"""
매체 간 예산 최적 배분 모듈
예산 방향(증/감)에 따라 자동 추천 + 사용자 선택식 배분.
카드사·통신사 최소 발송량(5만건) / 최소 예산(500만원) 제약 자동 체크.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from modules.config import (
    section_header, alert_card_html, kpi_card_html,
    CHART_COLORS, PLOTLY_LAYOUT, compact_num,
    is_card_or_telecom, MIN_SEND_CARD_TELECOM, MIN_BUDGET_CARD_TELECOM,
)
from modules.data_processing import calc_ctr, render_ref_period_selector, reliability_weight
from modules.insights import detect_budget, render_insights
from modules.firebase_connector import get_benchmark_stats


_compact = compact_num  # 후방호환 alias


# ──────────────────────────────────────────────
# 효율 계산
# ──────────────────────────────────────────────

def _calc_media_efficiency(df: pd.DataFrame) -> pd.DataFrame:
    stats = df.groupby('매체명').agg({
        '집행금액': 'sum', '발송량': 'sum', '클릭수': 'sum',
    }).reset_index()

    stats = stats[stats['클릭수'] > 0].copy()
    if stats.empty:
        return stats

    stats['CTR'] = calc_ctr(stats)
    stats['CPC'] = stats['집행금액'] / stats['클릭수']
    stats['CPS'] = np.where(stats['발송량'] > 0, stats['집행금액'] / stats['발송량'], 0)
    stats['월수'] = df.groupby('매체명')['년월'].nunique().reindex(stats['매체명']).values
    stats['월평균비용'] = stats['집행금액'] / stats['월수'].clip(lower=1)

    # ── 🔒 내부: Firebase 시장 표준 단가로 CPS 보정 ──
    from modules._analytics_helpers import apply_market_price_correction
    stats = apply_market_price_correction(stats, media_col='매체명', cps_col='CPS')

    ctr_norm = stats['CTR'] / stats['CTR'].max() if stats['CTR'].max() > 0 else pd.Series(0, index=stats.index)
    cpc_norm = 1 - (stats['CPC'] / stats['CPC'].max()) if stats['CPC'].max() > 0 else pd.Series(0, index=stats.index)

    # ── 단가 효율 점수 추가 ──
    # CPS가 낮을수록(= 발송 단가가 싸서 같은 예산으로 더 많이 보낼 수 있음) 점수↑
    cps_ref = stats['CPS_보정'].replace(0, np.nan)
    cps_min = cps_ref.min()
    if pd.notna(cps_min) and cps_min > 0:
        cps_efficiency = cps_min / cps_ref  # 0~1 (가장 싼 매체가 1)
        cps_efficiency = cps_efficiency.fillna(0)
    else:
        cps_efficiency = pd.Series(0, index=stats.index)

    # 발송량 신뢰도 가중: 소량 매체의 효율점수 자동 하락
    vol_weight = stats['발송량'].apply(reliability_weight)
    # 효율점수 = CTR(50%) + CPC(25%) + CPS(25%) — CPS 반영으로 단가 낮은 매체도 우대
    stats['효율점수'] = (ctr_norm * 0.5 + cpc_norm * 0.25 + cps_efficiency * 0.25) * vol_weight * 100
    stats['신뢰도'] = stats['발송량'].apply(
        lambda s: '신뢰' if s >= 30_000 else ('참고' if s >= 5_000 else '부족')
    )
    stats['카드통신'] = stats['매체명'].apply(is_card_or_telecom)

    return stats.sort_values('효율점수', ascending=False)


# ──────────────────────────────────────────────
# 예산 배분
# ──────────────────────────────────────────────

def _optimize_budget(stats: pd.DataFrame, total_budget: float) -> pd.DataFrame:
    if stats.empty or stats['효율점수'].sum() == 0:
        return stats

    result = stats.copy()
    total_score = result['효율점수'].sum()

    # 1단계: 효율 비례로 먼저 배분
    result['배분예산'] = (result['효율점수'] / total_score) * total_budget

    # 2단계: 카드사·통신사 중 500만원 미달인 곳만 500만원으로 올림
    card_under = result['카드통신'] & (result['배분예산'] < MIN_BUDGET_CARD_TELECOM)
    if card_under.any():
        deficit = (MIN_BUDGET_CARD_TELECOM - result.loc[card_under, '배분예산']).sum()
        result.loc[card_under, '배분예산'] = MIN_BUDGET_CARD_TELECOM
        others = ~card_under
        others_total = result.loc[others, '배분예산'].sum()
        if others_total > deficit:
            ratio = (others_total - deficit) / others_total
            result.loc[others, '배분예산'] *= ratio

    # 3단계: 비율·발송·클릭 재계산 (보정된 CPS 사용)
    budget_sum = result['배분예산'].sum()
    result['배분비율'] = result['배분예산'] / budget_sum if budget_sum > 0 else 0
    cps_for_calc = result['CPS_보정'] if 'CPS_보정' in result.columns else result['CPS']
    result['예상발송'] = np.where(cps_for_calc > 0, result['배분예산'] / cps_for_calc, 0).astype(int)
    # 예상클릭 = 예상발송 × CTR (CPC 직접 사용보다 안정적)
    result['예상클릭'] = (result['예상발송'] * result['CTR'] / 100).astype(int)

    # 4단계: 최소 미달 체크
    result['최소미달'] = False
    mask = result['카드통신'] & (
        (result['예상발송'] < MIN_SEND_CARD_TELECOM) |
        (result['배분예산'] < MIN_BUDGET_CARD_TELECOM)
    )
    result.loc[mask, '최소미달'] = True

    return result


# ──────────────────────────────────────────────
# 추천 로직
# ──────────────────────────────────────────────

def _min_budget_per_media(row: pd.Series) -> float:
    if row['카드통신']:
        cps_min = row['CPS'] * MIN_SEND_CARD_TELECOM if row['CPS'] > 0 else 0
        return max(cps_min, MIN_BUDGET_CARD_TELECOM)
    elif row['CPS'] > 0:
        return max(row['CPS'] * 10_000, 500_000)
    return 500_000


def _affordable_media(stats: pd.DataFrame, total_budget: float) -> list[str]:
    if stats.empty:
        return []
    selected = []
    remaining = total_budget
    for _, row in stats.iterrows():
        cost = _min_budget_per_media(row)
        if remaining >= cost:
            selected.append(row['매체명'])
            remaining -= cost
        if remaining <= 0:
            break
    if not selected and not stats.empty:
        selected = [stats.iloc[0]['매체명']]
    return selected


def _recommend_media(
    stats: pd.DataFrame, budget_ratio: float, total_budget: float,
) -> tuple[list[str], list[str], str, str]:
    test_candidates: list[str] = []
    affordable = _affordable_media(stats, total_budget)
    max_n = len(affordable)

    if budget_ratio < 0.9:
        recommended = affordable
        n = len(recommended)
        mode = "🎯 효율 집중"
        if n == 1:
            desc = (
                f"예산 규모상 <b>효율 1위 매체에만 집중</b>하는 것이 현실적입니다. "
                f"한 곳에 몰아서 확실한 성과를 내세요."
            )
        elif n <= 2:
            desc = (
                f"예산이 적습니다. <b>효율 최상위 {n}개 매체</b>에만 집중하여 "
                f"한정된 예산으로 최대 효과를 끌어내세요."
            )
        else:
            desc = (
                f"예산이 줄었습니다. <b>효율 상위 {n}개 매체</b>에 집중하여 "
                f"동일 예산으로 최대 성과를 확보하세요. 하위 매체는 과감히 제외를 권장합니다."
            )
    elif budget_ratio > 1.1:
        top_n = min(max_n, 5)
        top_media = affordable[:top_n]
        rest = stats[~stats['매체명'].isin(top_media)]
        used = sum(
            _min_budget_per_media(stats[stats['매체명'] == m].iloc[0])
            for m in top_media
        )
        remaining_budget = total_budget - used
        if not rest.empty and remaining_budget > 0:
            for _, row in rest.nlargest(2, 'CTR').iterrows():
                cost = _min_budget_per_media(row)
                if remaining_budget >= cost:
                    test_candidates.append(row['매체명'])
                    remaining_budget -= cost
            recommended = top_media + test_candidates
        else:
            recommended = top_media
        mode = "🚀 확장 + 신규 테스트"
        desc = (
            f"예산이 늘었습니다. <b>기존 TOP {top_n}를 유지</b>하면서 "
            f"<b>신규 매체 테스트</b>를 병행하세요."
        )
    else:
        n = min(max_n, 5)
        recommended = affordable[:n]
        mode = "⚖️ 현상 유지"
        desc = (
            f"예산이 비슷한 수준입니다. <b>효율 상위 {n}개 매체</b> 중심으로 "
            f"안정적인 배분을 유지하세요."
        )

    return recommended, test_candidates, mode, desc


# ──────────────────────────────────────────────
# 도넛 차트
# ──────────────────────────────────────────────

def _build_color_map(result: pd.DataFrame) -> dict[str, str]:
    """매체명 → 색상 매핑 (효율점수 순서 기준, 모든 차트에서 공유)"""
    return {name: CHART_COLORS[i % len(CHART_COLORS)]
            for i, name in enumerate(result['매체명'])}


def _donut_chart(result: pd.DataFrame, mode: str, color_map: dict[str, str]) -> go.Figure:
    labels = result['매체명'].tolist()
    values = result['배분예산'].tolist()
    colors = [color_map[n] for n in labels]

    fig = go.Figure(data=[go.Pie(
        labels=labels,
        values=values,
        hole=0.62,
        marker=dict(colors=colors, line=dict(color='#FFFFFF', width=2.5)),
        textinfo='label+percent',
        textposition='outside',
        textfont=dict(size=11, family='Pretendard, sans-serif', color='#4E5968'),
        hovertemplate='%{label}<br>%{value:,.0f}원 (%{percent})<extra></extra>',
        pull=[0.03 if i == 0 else 0 for i in range(len(result))],
    )])

    layout = {**PLOTLY_LAYOUT}
    layout.update(
        title=dict(text=""),
        height=340,
        showlegend=False,
        margin=dict(t=40, l=60, r=60, b=40),
        annotations=[dict(
            text=f'<b>{mode.split(" ", 1)[-1] if " " in mode else mode}</b>',
            x=0.5, y=0.5, font_size=14, font_color='#4E5968',
            font_family='Pretendard, sans-serif',
            showarrow=False,
        )],
    )
    fig.update_layout(**layout)
    return fig


# ──────────────────────────────────────────────
# 바 차트 (수평)
# ──────────────────────────────────────────────

def _bar_chart(result: pd.DataFrame, total_man: float, mode: str, color_map: dict[str, str]) -> go.Figure:
    chart_df = result.sort_values('배분예산', ascending=True)
    colors = [color_map[n] for n in chart_df['매체명']]

    def _fmt(r):
        b = r['배분예산']
        pct = r['배분비율'] * 100
        return f"{pct:.0f}% · {_compact(b)}"

    fig = go.Figure(data=[go.Bar(
        y=chart_df['매체명'],
        x=chart_df['배분예산'],
        orientation='h',
        marker_color=colors,
        marker_cornerradius=6,
        text=chart_df.apply(_fmt, axis=1),
        textposition='auto', insidetextanchor='end', cliponaxis=False,
        textfont=dict(size=11, color='#4E5968', family='Pretendard, sans-serif'),
        hovertemplate='%{y}<br>배분: %{x:,.0f}원<extra></extra>',
    )])

    layout = {**PLOTLY_LAYOUT}
    layout.update(
        height=max(280, len(chart_df) * 52 + 70),
        title={**PLOTLY_LAYOUT['title'], 'text': f"총 {total_man:,.0f}만원 배분"},
        xaxis=dict(showgrid=False, showticklabels=False, title=""),
        yaxis=dict(title="", tickfont=dict(size=12, color='#4E5968')),
        margin=dict(t=48, l=110, r=120, b=20),
    )
    fig.update_layout(**layout)
    return fig


# ──────────────────────────────────────────────
# 매체 카드 렌더
# ──────────────────────────────────────────────

def _render_media_card(row: pd.Series, idx: int, max_budget: float, color_map: dict[str, str]):
    color = color_map.get(row['매체명'], CHART_COLORS[idx % len(CHART_COLORS)])
    pct = row['배분비율'] * 100
    bar_width = max(row['배분예산'] / max_budget * 100, 3) if max_budget > 0 else 3

    # 태그
    tags = ""
    if row['카드통신']:
        if row['최소미달']:
            tags = '<span class="bo-tag bo-tag-danger">5만건 미달</span>'
        else:
            tags = '<span class="bo-tag bo-tag-muted">카드/통신</span>'

    st.markdown(
        f'<div class="bo-card">'
        f'  <div class="bo-left">'
        f'    <div class="bo-dot" style="background:{color}"></div>'
        f'    <div class="bo-name">{row["매체명"]} {tags}</div>'
        f'  </div>'
        f'  <div class="bo-center">'
        f'    <div class="bo-bar-bg"><div class="bo-bar-fill" style="background:{color}; width:{bar_width}%"></div></div>'
        f'  </div>'
        f'  <div class="bo-right">'
        f'    <div class="bo-pct">{pct:.1f}%</div>'
        f'    <div class="bo-amount">{_compact(row["배분예산"])}</div>'
        f'  </div>'
        f'  <div class="bo-metrics">'
        f'    <span>{row["예상발송"]:,}건</span>'
        f'    <span>{row["예상클릭"]:,}클릭</span>'
        f'    <span>CPC {row["CPC"]:,.0f}원</span>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────
# 렌더
# ──────────────────────────────────────────────

def render(df: pd.DataFrame):
    # ── 커스텀 CSS ──
    st.markdown("""
    <style>
    .bo-card {
        display: grid;
        grid-template-columns: 200px 1fr 90px;
        grid-template-rows: auto auto;
        align-items: center;
        gap: 0 16px;
        padding: 16px 20px;
        margin-bottom: 6px;
        background: #FFFFFF;
        border-radius: 14px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.03);
        transition: all 0.2s ease;
    }
    .bo-card:hover {
        box-shadow: 0 4px 16px rgba(0,0,0,0.06);
        transform: translateY(-1px);
    }
    .bo-left {
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .bo-dot {
        width: 10px;
        height: 10px;
        border-radius: 3px;
        flex-shrink: 0;
    }
    .bo-name {
        font-weight: 700;
        font-size: 0.88rem;
        color: #191F28;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .bo-center {
        width: 100%;
    }
    .bo-bar-bg {
        background: #F2F4F6;
        border-radius: 4px;
        height: 8px;
        width: 100%;
        overflow: hidden;
    }
    .bo-bar-fill {
        height: 8px;
        border-radius: 4px;
        transition: width 0.5s cubic-bezier(0.4,0,0.2,1);
    }
    .bo-right {
        text-align: right;
    }
    .bo-pct {
        font-weight: 800;
        font-size: 1rem;
        color: #191F28;
    }
    .bo-amount {
        font-size: 0.78rem;
        color: #8B95A1;
        margin-top: 1px;
    }
    .bo-metrics {
        grid-column: 1 / -1;
        display: flex;
        gap: 16px;
        margin-top: 6px;
        padding-top: 8px;
        border-top: 1px solid #F2F4F6;
    }
    .bo-metrics span {
        font-size: 0.75rem;
        color: #8B95A1;
        font-weight: 500;
    }
    .bo-tag {
        display: inline-block;
        font-size: 0.68rem;
        padding: 1px 8px;
        border-radius: 10px;
        margin-left: 4px;
        font-weight: 600;
        vertical-align: middle;
    }
    .bo-tag-muted {
        background: #F2F4F6;
        color: #8B95A1;
    }
    .bo-tag-danger {
        background: #FFF3F0;
        color: #F44336;
    }
    .bo-rank-card {
        background: #FFFFFF;
        border-radius: 14px;
        padding: 18px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.03);
        text-align: center;
        transition: all 0.2s ease;
    }
    .bo-rank-card:hover {
        box-shadow: 0 4px 16px rgba(0,0,0,0.06);
        transform: translateY(-2px);
    }
    .bo-rank-medal { font-size: 1.5rem; }
    .bo-rank-name {
        font-weight: 700;
        font-size: 0.85rem;
        color: #191F28;
        margin-top: 6px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .bo-rank-score {
        font-weight: 800;
        font-size: 1.3rem;
        color: #F7931D;
        margin-top: 4px;
    }
    .bo-rank-bar {
        margin-top: 8px;
        background: #F2F4F6;
        border-radius: 4px;
        height: 6px;
        overflow: hidden;
    }
    .bo-rank-bar-fill {
        height: 6px;
        border-radius: 4px;
        transition: width 0.5s ease;
    }
    .bo-rank-detail {
        font-size: 0.73rem;
        color: #8B95A1;
        margin-top: 8px;
    }
    .bo-summary {
        display: flex;
        gap: 12px;
        margin: 12px 0 8px 0;
    }
    .bo-summary-item {
        flex: 1;
        background: #FFFFFF;
        border-radius: 14px;
        padding: 18px 16px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.03);
        text-align: center;
    }
    .bo-summary-label {
        font-size: 0.75rem;
        color: #8B95A1;
        font-weight: 600;
        margin-bottom: 4px;
    }
    .bo-summary-value {
        font-weight: 800;
        font-size: 1.2rem;
        color: #191F28;
    }
    .bo-summary-sub {
        font-size: 0.72rem;
        color: #8B95A1;
        margin-top: 2px;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown(section_header(
        "매체 간 예산 최적 배분",
        "예산 방향에 따라 매체를 자동 추천하고, 효율 기반으로 최적 배분합니다.",
    ), unsafe_allow_html=True)

    # ── 최소 데이터 검증 ──
    if '년월' not in df.columns or df['년월'].nunique() < 2:
        st.info("📊 예산 최적 배분은 **2개월 이상**의 데이터가 필요합니다. 데이터가 누적되면 자동으로 활성화됩니다.")
        st.divider()
        return

    ref_df, period_label = render_ref_period_selector(df, "budget")
    stats = _calc_media_efficiency(ref_df)
    if stats.empty or len(stats) < 2:
        st.caption("2개 이상의 매체 데이터가 있어야 최적 배분을 계산할 수 있습니다.")
        st.divider()
        return

    # ── 현재 예산 ──
    current_total = int(stats['월평균비용'].sum())
    default_man = max(int(current_total / 10_000), 100)

    total_man = st.slider(
        "총 월 예산 설정 (만 원)",
        min_value=100, max_value=max(default_man * 3, 10000),
        value=default_man, step=100,
    )
    total_budget = total_man * 10_000
    budget_ratio = total_budget / current_total if current_total > 0 else 1.0

    # ── 자동 추천 ──
    recommended, test_candidates, mode, mode_desc = _recommend_media(stats, budget_ratio, total_budget)
    all_media = stats['매체명'].tolist()

    # 모드 안내
    st.markdown(alert_card_html(
        "info" if "유지" in mode else ("success" if "확장" in mode else "warning"),
        f"{mode} 모드",
        mode_desc,
    ), unsafe_allow_html=True)

    # ── 테스트 후보 (증가 모드) ──
    if test_candidates:
        cand_chips = " ".join(
            f'<span style="display:inline-block; background:#E8F5E9; color:#2E7D32; '
            f'padding:4px 12px; border-radius:20px; font-size:0.82rem; font-weight:600; '
            f'margin-right:6px;">🧪 {name}</span>'
            for name in test_candidates
        )
        cand_details = []
        for name in test_candidates:
            row = stats[stats['매체명'] == name]
            if not row.empty:
                cand_details.append(f"{name} (CTR {row.iloc[0]['CTR']:.2f}%)")
        detail_txt = ", ".join(cand_details) if cand_details else ", ".join(test_candidates)

        st.markdown(
            f'<div style="background:#F1F8E9; border-left:4px solid #66BB6A; '
            f'border-radius:8px; padding:14px 18px; margin-top:10px;">'
            f'<div style="font-weight:700; color:#2E7D32; font-size:0.88rem; margin-bottom:6px;">'
            f'🧪 신규 테스트 추천 매체</div>'
            f'<div style="font-size:0.82rem; color:#4E5968; margin-bottom:8px;">'
            f'현재 예산 비중은 적지만 CTR이 양호한 매체입니다. 소규모 테스트를 권장합니다.</div>'
            f'{cand_chips}'
            f'<div style="font-size:0.75rem; color:#8B95A1; margin-top:8px;">{detail_txt}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── 매체 선택 ──
    _rec_sig = f"{total_man}_{'_'.join(recommended)}"
    if st.session_state.get('_budget_rec_sig') != _rec_sig:
        st.session_state['_budget_rec_sig'] = _rec_sig
        st.session_state['budget_media'] = recommended

    st.markdown(
        '<div style="font-size:0.82rem; color:#8B95A1; margin-top:12px; font-weight:600;">'
        '배분 매체 선택 (추천 기반 — 자유롭게 조정 가능)</div>',
        unsafe_allow_html=True,
    )
    selected = st.multiselect(
        "매체 선택", options=all_media,
        label_visibility="collapsed", key="budget_media",
    )

    if not selected:
        st.caption("배분할 매체를 하나 이상 선택하세요.")
        st.divider()
        return

    # ── 배분 실행 ──
    selected_stats = stats[stats['매체명'].isin(selected)].copy()
    result = _optimize_budget(selected_stats, total_budget)

    # ── 인사이트 칩 ──
    render_insights(detect_budget(result))

    # ── 요약 KPI ──
    total_sends = result['예상발송'].sum()
    total_clicks = result['예상클릭'].sum()
    avg_cpc = total_budget / total_clicks if total_clicks > 0 else 0

    st.markdown(
        f'<div class="bo-summary">'
        f'  <div class="bo-summary-item">'
        f'    <div class="bo-summary-label">총 배분 예산</div>'
        f'    <div class="bo-summary-value">{_compact(total_budget)}</div>'
        f'    <div class="bo-summary-sub">{len(result)}개 매체</div>'
        f'  </div>'
        f'  <div class="bo-summary-item">'
        f'    <div class="bo-summary-label">예상 총 발송</div>'
        f'    <div class="bo-summary-value">{_compact(total_sends, "건")}</div>'
        f'    <div class="bo-summary-sub">월간 예측</div>'
        f'  </div>'
        f'  <div class="bo-summary-item">'
        f'    <div class="bo-summary-label">예상 총 클릭</div>'
        f'    <div class="bo-summary-value">{_compact(total_clicks, "회")}</div>'
        f'    <div class="bo-summary-sub">평균 CPC {avg_cpc:,.0f}원</div>'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 카드사·통신사 최소 경고 ──
    violated = result[result['최소미달']]
    if not violated.empty:
        for _, row in violated.iterrows():
            min_budget = max(row['CPS'] * MIN_SEND_CARD_TELECOM, MIN_BUDGET_CARD_TELECOM)
            st.markdown(alert_card_html(
                "warning",
                f"⚠️ {row['매체명']} — 최소 운영 기준 미달",
                f"카드사·통신사는 최소 <b>{MIN_SEND_CARD_TELECOM:,}건</b> 발송 "
                f"(약 <b>{min_budget / 10000:,.0f}만원</b>)이 필요합니다. "
                f"매체를 줄이거나 예산을 늘려주세요.",
            ), unsafe_allow_html=True)

    # ── 차트: 도넛 + 바 나란히 ──
    chart_df = result.head(5)
    color_map = _build_color_map(result)

    col_donut, col_bar = st.columns([1, 1.3], gap="medium")
    with col_donut:
        fig_donut = _donut_chart(chart_df, mode, color_map)
        st.plotly_chart(fig_donut, width='stretch')
    with col_bar:
        fig_bar = _bar_chart(chart_df, total_man, mode, color_map)
        st.plotly_chart(fig_bar, width='stretch')

    # ── 전체 매체 상세 카드 ──
    st.markdown(
        f'<div style="font-size:0.82rem; color:#8B95A1; margin-bottom:8px; '
        f'margin-top:8px; font-weight:600;">매체별 배분 상세 ({len(result)}개)</div>',
        unsafe_allow_html=True,
    )
    max_budget = result['배분예산'].max()
    for idx, (_, row) in enumerate(result.iterrows()):
        _render_media_card(row, idx, max_budget, color_map)

    # ── 현재 vs 최적 비교 ──
    months = max(stats['월수'].max(), 1)
    current_monthly_click = stats['클릭수'].sum() / months
    click_diff = total_clicks - current_monthly_click
    click_pct = (click_diff / current_monthly_click * 100) if current_monthly_click > 0 else 0

    if click_pct > 5:
        st.markdown(alert_card_html(
            "success", "최적 배분 효과",
            f"현재 대비 월간 예상 클릭수가 "
            f"<b>{_compact(current_monthly_click, '회')} → {_compact(total_clicks, '회')}</b>로 "
            f"약 <b>{click_pct:.1f}% 증가</b>할 것으로 예측됩니다.",
        ), unsafe_allow_html=True)

    # ── 매체별 효율 랭킹 ──
    st.markdown("<br>", unsafe_allow_html=True)
    st.markdown(section_header("매체별 효율 랭킹", "CTR(60%) + CPC(40%) 가중 기반 종합 효율 점수"), unsafe_allow_html=True)

    # ── 업종 벤치마크 로드 ──
    try:
        bench = get_benchmark_stats()
    except Exception:
        bench = None
    ind_cpc = bench['avg_cpc'] if bench and bench['avg_cpc'] > 0 else 0
    ind_ctr = bench['avg_ctr'] if bench and bench['avg_ctr'] > 0 else 0

    top4 = result.head(min(len(result), 4))
    max_score = top4['효율점수'].max() if not top4.empty else 100
    medals = ["🥇", "🥈", "🥉", "4️⃣"]
    rank_cols = st.columns(len(top4), gap="medium")

    for idx, (_, row) in enumerate(top4.iterrows()):
        color = color_map.get(row['매체명'], CHART_COLORS[idx % len(CHART_COLORS)])
        bar_pct = row['효율점수'] / max_score * 100 if max_score > 0 else 0

        # 업종 대비 CPC 비교 뱃지
        cpc_badge = ""
        if ind_cpc > 0 and row['CPC'] > 0:
            cpc_diff_pct = (row['CPC'] - ind_cpc) / ind_cpc * 100
            if cpc_diff_pct <= -20:
                cpc_badge = (f'<div style="font-size:.65rem;font-weight:700;color:#2E7D32;'
                             f'background:#E8F5E9;padding:2px 8px;border-radius:10px;'
                             f'margin-top:4px;display:inline-block;">업종 대비 {cpc_diff_pct:.0f}%</div>')
            elif cpc_diff_pct >= 20:
                cpc_badge = (f'<div style="font-size:.65rem;font-weight:700;color:#E53935;'
                             f'background:#FFF3F0;padding:2px 8px;border-radius:10px;'
                             f'margin-top:4px;display:inline-block;">업종 대비 +{cpc_diff_pct:.0f}%</div>')

        with rank_cols[idx]:
            st.markdown(
                f'<div class="bo-rank-card">'
                f'  <div class="bo-rank-medal">{medals[idx]}</div>'
                f'  <div class="bo-rank-name">{row["매체명"]}</div>'
                f'  <div class="bo-rank-score">{row["효율점수"]:.0f}점</div>'
                f'  <div class="bo-rank-bar">'
                f'    <div class="bo-rank-bar-fill" style="background:{color}; width:{bar_pct}%"></div>'
                f'  </div>'
                f'  <div class="bo-rank-detail">'
                f'    CPC {row["CPC"]:,.0f}원 · CTR {row["CTR"]:.2f}%'
                f'  </div>'
                f'  {cpc_badge}'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ── 업종 대비 종합 효율 안내 ──
    if ind_cpc > 0:
        avg_client_cpc = total_budget / total_clicks if total_clicks > 0 else 0
        if avg_client_cpc > 0:
            overall_diff = (avg_client_cpc - ind_cpc) / ind_cpc * 100
            if overall_diff < -10:
                st.markdown(alert_card_html(
                    "success", "🔥 업종 대비 CPC 효율 우수",
                    f"최적 배분 기준 평균 CPC <b>{avg_client_cpc:,.0f}원</b>은 "
                    f"업종 평균 <b>{ind_cpc:,.0f}원</b> 대비 <b>{abs(overall_diff):.0f}% 저렴</b>합니다.",
                ), unsafe_allow_html=True)
            elif overall_diff > 20:
                st.markdown(alert_card_html(
                    "warning", "⚠️ 업종 대비 CPC 높음",
                    f"최적 배분 기준 평균 CPC <b>{avg_client_cpc:,.0f}원</b>은 "
                    f"업종 평균 <b>{ind_cpc:,.0f}원</b> 대비 <b>{overall_diff:.0f}% 높습니다</b>. "
                    f"효율이 낮은 매체의 비중을 줄여보세요.",
                ), unsafe_allow_html=True)

    st.divider()
