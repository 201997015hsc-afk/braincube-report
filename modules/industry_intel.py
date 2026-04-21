"""
업종 내 경쟁 인텔리전스 (Toss-style)
─────────────────────────────────────
동종 업계 광고주 대비 포지셔닝 · 순위 · 상위 패턴 · 트렌드 분석.
클라이언트에게 "다른 데는 어떤데요?"에 답할 수 있는 화면.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from modules.config import (
    BRAND_PRIMARY, COLOR_BLUE, PLOTLY_LAYOUT,
    section_header, kpi_card_html, alert_card_html, compact_num,
    MIN_SAMPLES_FOR_QUARTILE, MIN_SENDS_FOR_CTR_CLAIM,
    MIN_PEERS_FOR_RANKING, CTR_MIN_FLOOR, CTR_CHANGE_THRESHOLD,
)
from modules.firebase_connector import (
    load_benchmark, get_benchmark_monthly,
    calc_percentile, calc_percentile_lower, percentile_grade, percentile_letter,
)
from modules.data_processing import calc_ctr_scalar


# ──────────────────────────────────────────────
# 데이터 준비
# ──────────────────────────────────────────────

def _my_metrics(df: pd.DataFrame) -> dict:
    """클라이언트 핵심 지표 요약"""
    cost = df['집행금액'].sum()
    sends = df['발송량'].sum()
    clicks = df['클릭수'].sum()
    n_months = max(df['년월'].nunique(), 1) if '년월' in df.columns else 1
    # 캠페인 수: 캠페인ID 컬럼이 있으면 사용, 없으면 매체명×년월 조합수
    if '캠페인ID' in df.columns:
        n_camps = df['캠페인ID'].nunique()
    elif '매체명' in df.columns and '년월' in df.columns:
        n_camps = df.groupby(['매체명', '년월']).ngroups
    elif '매체명' in df.columns:
        n_camps = df['매체명'].nunique()
    else:
        n_camps = 0

    return dict(
        cost=cost, sends=sends, clicks=clicks,
        ctr=calc_ctr_scalar(clicks, sends),
        cpc=cost / clicks if clicks > 0 else 0,
        monthly_cost=cost / n_months,
        monthly_sends=sends / n_months,
        n_months=n_months,
        n_media=df['매체명'].nunique() if '매체명' in df.columns else 0,
        n_camps=n_camps,
    )


def _my_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """클라이언트 월별 CTR/CPC"""
    m = df.groupby('년월').agg({'발송량': 'sum', '클릭수': 'sum', '집행금액': 'sum'}).reset_index()
    m['CTR'] = np.where(m['발송량'] > 0, m['클릭수'] / m['발송량'] * 100, 0)
    m['CPC'] = np.where(m['클릭수'] > 0, m['집행금액'] / m['클릭수'], 0)
    return m.sort_values('년월')


def _peer_metrics(bench: pd.DataFrame, industry: str) -> pd.DataFrame:
    """업종 내 광고주별 집계 (익명화된 경쟁사 데이터)"""
    ind = bench[bench['분야'] == industry].copy()
    if ind.empty:
        return pd.DataFrame()

    peers = ind.groupby('광고주').agg({'발송건': 'sum', '클릭수': 'sum', '광고비': 'sum'}).reset_index()

    # CTR 계산용: 클릭 트래킹이 있는 캠페인의 발송건만
    tracked = ind[ind['_has_click']].groupby('광고주')['발송건'].sum()
    peers['트래킹_발송'] = peers['광고주'].map(tracked).fillna(0)

    # 캠페인 수 / 매체 수 / 활동 기간
    if '캠페인ID' in ind.columns:
        camps = ind.groupby('광고주')['캠페인ID'].nunique()
    else:
        camps = ind.groupby('광고주').size()
    peers['캠페인수'] = peers['광고주'].map(camps).fillna(1).astype(int)

    media_cnt = ind.groupby('광고주')['매체'].nunique()
    peers['매체수'] = peers['광고주'].map(media_cnt).fillna(1).astype(int)

    month_cnt = ind.groupby('광고주')['년월'].nunique()
    peers['활동월수'] = peers['광고주'].map(month_cnt).fillna(1).astype(int)

    # 파생 지표
    peers['CTR'] = np.where(peers['트래킹_발송'] > 0, peers['클릭수'] / peers['트래킹_발송'] * 100, 0)
    peers['CPC'] = np.where(peers['클릭수'] > 0, peers['광고비'] / peers['클릭수'], 0)
    peers['월예산'] = peers['광고비'] / peers['활동월수'].clip(lower=1)
    peers['월발송'] = peers['발송건'] / peers['활동월수'].clip(lower=1)

    return peers[peers['발송건'] > 100].sort_values('CTR', ascending=False).reset_index(drop=True)


def _rank_of(my_val: float, peer_vals, higher_is_better: bool = True) -> tuple[int, int]:
    """자사의 순위 (1-based, 자사 포함 총원)"""
    if higher_is_better:
        rank = sum(1 for v in peer_vals if v > my_val) + 1
    else:
        rank = sum(1 for v in peer_vals if v < my_val) + 1
    return rank, len(peer_vals) + 1


# ──────────────────────────────────────────────
# 차트
# ──────────────────────────────────────────────

def _rank_bar_chart(peers: pd.DataFrame, my_val: float, col: str,
                    title: str, unit: str, higher_is_better: bool = True,
                    fmt: str = '.2f') -> go.Figure:
    """
    익명 광고주 수평 바 + 자사 위치 하이라이트.
    가장 직관적인 "내 위치" 시각화.
    """
    # 자사 포함 데이터프레임 생성
    rows = []
    for i, (_, r) in enumerate(peers.iterrows()):
        rows.append(dict(name=f'광고주 {chr(65 + i)}', val=r[col], is_me=False))
    rows.append(dict(name='★ 자사', val=my_val, is_me=True))

    chart_df = pd.DataFrame(rows)
    chart_df = chart_df.sort_values('val', ascending=not higher_is_better).reset_index(drop=True)

    colors = [BRAND_PRIMARY if r['is_me'] else '#D4D9E1' for _, r in chart_df.iterrows()]
    text_colors = ['#FFFFFF' if r['is_me'] else '#8B95A1' for _, r in chart_df.iterrows()]

    fig = go.Figure(go.Bar(
        y=chart_df['name'], x=chart_df['val'],
        orientation='h',
        marker_color=colors,
        marker_cornerradius=6,
        texttemplate=f'%{{x:{fmt}}}{unit}',
        textposition='inside', insidetextanchor='end',
        textfont=dict(size=11),
        hovertemplate=f'%{{y}}: %{{x:{fmt}}}{unit}<extra></extra>',
    ))

    # 업종 평균선
    avg = float(peers[col].mean())
    fig.add_vline(
        x=avg, line_dash='dot', line_color='#8B95A1', line_width=1.5,
        annotation_text=f'업종 평균 {avg:{fmt}}{unit}',
        annotation_position='top right',
        annotation_font=dict(size=10, color='#8B95A1'),
    )

    layout = {**PLOTLY_LAYOUT}
    layout.update(
        title=dict(text=title, font=dict(size=14, color='#191F28')),
        height=max(280, (len(chart_df)) * 38 + 80),
        margin=dict(t=50, l=90, r=20, b=30),
        xaxis=dict(showgrid=False, showticklabels=False, title=''),
        yaxis=dict(title='', tickfont=dict(size=12, color='#4E5968')),
        showlegend=False,
    )
    fig.update_layout(**layout)
    return fig


def _trend_overlay_chart(bench_monthly: pd.DataFrame, my_monthly: pd.DataFrame,
                         col: str, title: str, unit: str) -> go.Figure:
    """업종 월별 트렌드 + 자사 오버레이"""
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=bench_monthly['년월'], y=bench_monthly[col],
        mode='lines+markers', name='업종 평균',
        line=dict(color='#B0B8C1', width=2, dash='dot'),
        marker=dict(size=5, color='#B0B8C1', symbol='diamond'),
        hovertemplate=f'업종 평균: %{{y:.2f}}{unit}<extra></extra>',
    ))

    if my_monthly is not None and not my_monthly.empty and col in my_monthly.columns:
        fig.add_trace(go.Scatter(
            x=my_monthly['년월'], y=my_monthly[col],
            mode='lines+markers', name='자사',
            line=dict(color=BRAND_PRIMARY, width=2.5),
            marker=dict(size=7, color=BRAND_PRIMARY),
            hovertemplate=f'자사: %{{y:.2f}}{unit}<extra></extra>',
        ))

    layout = {**PLOTLY_LAYOUT}
    layout.update(
        title=dict(text=title, font=dict(size=14, color='#191F28')),
        height=320,
        margin=dict(t=50, l=50, r=20, b=40),
        showlegend=True,
        legend=dict(orientation='h', y=1.08, x=0.5, xanchor='center',
                    font=dict(size=11, color='#8B95A1')),
    )
    fig.update_layout(**layout)
    return fig


# ──────────────────────────────────────────────
# 섹션 렌더
# ──────────────────────────────────────────────

def _render_positioning(my: dict, peers: pd.DataFrame):
    """Section 1: 업종 포지셔닝 KPI"""
    ctr_rank, total = _rank_of(my['ctr'], peers['CTR'].tolist(), higher_is_better=True)
    cpc_rank, _ = _rank_of(my['cpc'], peers[peers['CPC'] > 0]['CPC'].tolist(), higher_is_better=False)
    vol_rank, _ = _rank_of(my['monthly_sends'], peers['월발송'].tolist(), higher_is_better=True)

    # 종합 점수 (CTR 순위 40% + CPC 순위 40% + 발송규모 20%)
    ctr_pct = (1 - (ctr_rank - 1) / max(total - 1, 1)) * 100
    cpc_pct = (1 - (cpc_rank - 1) / max(total - 1, 1)) * 100
    vol_pct = (1 - (vol_rank - 1) / max(total - 1, 1)) * 100
    overall_pct = ctr_pct * 0.4 + cpc_pct * 0.4 + vol_pct * 0.2
    grade = percentile_letter(overall_pct)

    c1, c2, c3, c4 = st.columns(4, gap='medium')
    with c1:
        medal = ['🥇', '🥈', '🥉'][ctr_rank - 1] if ctr_rank <= 3 else f'{ctr_rank}위'
        st.markdown(kpi_card_html(
            'CTR 순위', f'{medal}',
            f'{total}개 광고주 중 · CTR {my["ctr"]:.2f}%',
        ), unsafe_allow_html=True)
    with c2:
        medal = ['🥇', '🥈', '🥉'][cpc_rank - 1] if cpc_rank <= 3 else f'{cpc_rank}위'
        st.markdown(kpi_card_html(
            'CPC 순위', f'{medal}',
            f'낮을수록 우수 · CPC {my["cpc"]:,.0f}원',
        ), unsafe_allow_html=True)
    with c3:
        medal = ['🥇', '🥈', '🥉'][vol_rank - 1] if vol_rank <= 3 else f'{vol_rank}위'
        st.markdown(kpi_card_html(
            '발송 규모', f'{medal}',
            f'월 {compact_num(my["monthly_sends"], "건")}',
        ), unsafe_allow_html=True)
    with c4:
        if total >= MIN_PEERS_FOR_RANKING:
            grade_clr = {'A': '#2E7D32', 'B': '#3182F6', 'C': '#F59E0B', 'D': '#E53935'}.get(grade, '#8B95A1')
            st.markdown(kpi_card_html(
                '종합 등급', f'{grade}',
                f'상위 {100 - overall_pct:.0f}% · {total}개 광고주 기준',
            ), unsafe_allow_html=True)
        else:
            st.markdown(kpi_card_html(
                '종합 등급', '–',
                f'표본 부족 ({total}/{MIN_PEERS_FOR_RANKING})',
            ), unsafe_allow_html=True)


def _render_peer_bars(my: dict, peers: pd.DataFrame):
    """Section 2: 익명 경쟁사 비교 (수평 바 차트)"""
    st.markdown(
        '<div style="font-size:0.78rem;color:#8B95A1;margin-bottom:4px;">'
        '💡 광고주명은 익명 처리됩니다. 주황색 바가 자사 위치입니다.</div>',
        unsafe_allow_html=True,
    )

    t1, t2 = st.tabs(['📊 CTR 비교 (높을수록 우수)', '💰 CPC 비교 (낮을수록 우수)'])
    with t1:
        fig = _rank_bar_chart(peers, my['ctr'], 'CTR', '업종 내 CTR 순위', '%', True, '.2f')
        st.plotly_chart(fig, width='stretch')
    with t2:
        cpc_peers = peers[peers['CPC'] > 0]
        if not cpc_peers.empty:
            fig = _rank_bar_chart(cpc_peers, my['cpc'], 'CPC', '업종 내 CPC 순위', '원', False, ',.0f')
            st.plotly_chart(fig, width='stretch')
        else:
            st.caption('CPC 데이터가 부족합니다.')


def _render_top_patterns(my: dict, peers: pd.DataFrame):
    """Section 3: 상위 광고주 패턴 분석"""
    n = len(peers)
    if n < 4:
        st.caption('상위/하위 비교를 위해 최소 4개 광고주가 필요합니다.')
        return

    q1 = max(n // 4, 1)
    top = peers.head(q1)
    bottom = peers.tail(q1)

    def _fmt(val, unit=''):
        if unit == '원':
            return f'{val:,.0f}원'
        if unit == '%':
            return f'{val:.2f}%'
        if unit == '건':
            return compact_num(val, '건')
        if unit == '개':
            return f'{val:.1f}개'
        return f'{val:,.0f}'

    metrics = [
        ('월 평균 예산', '월예산', '원'),
        ('CTR', 'CTR', '%'),
        ('CPC', 'CPC', '원'),
        ('활용 매체 수', '매체수', '개'),
        ('캠페인 수', '캠페인수', ''),
        ('월 발송량', '월발송', '건'),
    ]

    # 카드 기반 비교 테이블
    my_lookup = {
        '월예산': my['monthly_cost'], 'CTR': my['ctr'], 'CPC': my['cpc'],
        '매체수': my['n_media'], '캠페인수': my['n_camps'], '월발송': my['monthly_sends'],
    }

    html = (
        '<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">'
        '<tr style="border-bottom:2px solid #E5E8EB;">'
        '<th style="text-align:left;padding:10px 8px;color:#4E5968;">지표</th>'
        '<th style="text-align:right;padding:10px 8px;color:#2E7D32;">상위 25%</th>'
        '<th style="text-align:right;padding:10px 8px;color:#E53935;">하위 25%</th>'
        '<th style="text-align:right;padding:10px 8px;color:#F7931D;font-weight:700;">자사</th>'
        '</tr>'
    )

    for label, col, unit in metrics:
        t_val = float(top[col].mean()) if col in top.columns else 0
        b_val = float(bottom[col].mean()) if col in bottom.columns else 0
        m_val = my_lookup.get(col, 0)

        # 자사 셀 색상 (상위 근접 = 초록, 하위 근접 = 빨강)
        if col in ('CPC',):  # 낮을수록 좋은 지표
            is_good = m_val <= t_val * 1.1 if t_val > 0 else True
        else:
            is_good = m_val >= t_val * 0.9 if t_val > 0 else True

        my_bg = '#EAFAF1' if is_good else '#FFF3E0'

        html += (
            f'<tr style="border-bottom:1px solid #F0F1F3;">'
            f'<td style="padding:9px 8px;color:#4E5968;">{label}</td>'
            f'<td style="text-align:right;padding:9px 8px;color:#2E7D32;font-weight:600;">{_fmt(t_val, unit)}</td>'
            f'<td style="text-align:right;padding:9px 8px;color:#E53935;">{_fmt(b_val, unit)}</td>'
            f'<td style="text-align:right;padding:9px 8px;background:{my_bg};'
            f'color:#F7931D;font-weight:700;border-radius:6px;">{_fmt(m_val, unit)}</td>'
            f'</tr>'
        )

    html += '</table>'
    st.markdown(html, unsafe_allow_html=True)

    # 핵심 차이점 요약 — 표본 충분할 때만 (BUG I3)
    if n < MIN_SAMPLES_FOR_QUARTILE:
        st.markdown(
            f'<div style="background:#F8F9FA;border-radius:12px;padding:14px 18px;'
            f'margin-top:12px;font-size:0.82rem;color:#8B95A1;">'
            f'💡 표본 부족 — 상위 25% 분석 생략 (n={n} / 최소 {MIN_SAMPLES_FOR_QUARTILE})'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    top_ctr = float(top['CTR'].mean())
    bot_ctr = float(bottom['CTR'].mean())
    top_budget = float(top['월예산'].mean())
    bot_budget = float(bottom['월예산'].mean())

    # BUG I4: 발송량 기반 유효 세그먼트 판정 + 5배 이상 캡
    def _fmt_ratio(ratio: float) -> str:
        if ratio >= 5:
            return "5배 이상"
        return f"{ratio:.1f}배"

    top_sends_ok = True
    bot_sends_ok = True
    if '트래킹_발송' in top.columns and '트래킹_발송' in bottom.columns:
        top_sends_ok = float(top['트래킹_발송'].sum()) >= MIN_SENDS_FOR_CTR_CLAIM
        bot_sends_ok = float(bottom['트래킹_발송'].sum()) >= MIN_SENDS_FOR_CTR_CLAIM

    diff_points = []
    if bot_ctr > 0 and top_sends_ok and bot_sends_ok:
        diff_points.append(f"CTR <b>{_fmt_ratio(top_ctr / bot_ctr)}</b> 높음")
    if bot_budget > 0:
        diff_points.append(f"월 예산 <b>{_fmt_ratio(top_budget / bot_budget)}</b>")
    top_media = float(top['매체수'].mean())
    bot_media = float(bottom['매체수'].mean())
    if bot_media > 0:
        diff_points.append(f"매체 <b>{top_media:.1f}개</b> vs {bot_media:.1f}개")

    if diff_points:
        st.markdown(
            f'<div style="background:#F8F9FA;border-radius:12px;padding:14px 18px;'
            f'margin-top:12px;font-size:0.82rem;color:#4E5968;">'
            f'💡 <b>상위 25% 핵심 특징</b>: {" · ".join(diff_points)}'
            f'</div>',
            unsafe_allow_html=True,
        )


_MIN_MONTHS_FOR_TREND = 3  # 트렌드 판단 최소 개월 수


def _normalize_ym(ym: str) -> str:
    """'2026년3월' / '2026년 03월' / '2026년 3월' → 통일된 '2026년 03월' 형식"""
    if not isinstance(ym, str):
        return ''
    # 공백 제거 후 분리
    s = ym.strip().replace(' ', '')
    # '년' 과 '월' 기준 파싱
    try:
        y_part, rest = s.split('년', 1)
        m_part = rest.replace('월', '').strip()
        y, m = int(y_part), int(m_part)
        return f'{y}년 {m:02d}월'
    except (ValueError, IndexError):
        return ym  # 파싱 실패 시 원본 유지


def _render_trend(my_df: pd.DataFrame, bench: pd.DataFrame, industry: str):
    """Section 4: 업종 월별 트렌드 + 자사 오버레이"""
    try:
        bench_monthly = get_benchmark_monthly(industry)
    except Exception:
        bench_monthly = None

    if bench_monthly is None or bench_monthly.empty:
        st.caption('업종 월별 트렌드 데이터가 부족합니다.')
        return

    my_m = _my_monthly(my_df)

    # ── 년월 포맷 통일 ──
    # Firebase 벤치마크("2026년3월") vs data_processing 자사("2026년 03월") 포맷 불일치 해결
    bench_monthly = bench_monthly.copy()
    bench_monthly['년월'] = bench_monthly['년월'].astype(str).apply(_normalize_ym)
    if not my_m.empty and '년월' in my_m.columns:
        my_m['년월'] = my_m['년월'].astype(str).apply(_normalize_ym)
        my_m = my_m.sort_values('년월').reset_index(drop=True)
    bench_monthly = bench_monthly.sort_values('년월').reset_index(drop=True)

    # ── 트렌드 판단 가능한 최소 개월 수 검증 ──
    bench_months = bench_monthly['년월'].nunique() if '년월' in bench_monthly.columns else 0
    my_months = my_m['년월'].nunique() if not my_m.empty else 0
    if bench_months < _MIN_MONTHS_FOR_TREND and my_months < _MIN_MONTHS_FOR_TREND:
        st.markdown(
            f'<div style="background:#F8F9FA;border-radius:12px;padding:16px 18px;'
            f'font-size:0.85rem;color:#4E5968;line-height:1.6;">'
            f'📉 트렌드 분석은 <b>최소 {_MIN_MONTHS_FOR_TREND}개월 이상</b>의 데이터가 필요합니다. '
            f'(현재: 자사 {my_months}개월 · 업종 {bench_months}개월)<br>'
            f'<span style="color:#8B95A1;font-size:0.78rem;">'
            f'※ 2개월 이하의 비교는 일시적 변동일 가능성이 높아 추세로 해석하기 어렵습니다.'
            f'</span></div>',
            unsafe_allow_html=True,
        )
        return

    t1, t2 = st.tabs(['CTR 트렌드', 'CPC 트렌드'])
    with t1:
        fig = _trend_overlay_chart(bench_monthly, my_m, 'CTR', '월별 CTR: 자사 vs 업종 평균', '%')
        st.plotly_chart(fig, width='stretch')
    with t2:
        if 'CPC' in bench_monthly.columns:
            fig = _trend_overlay_chart(bench_monthly, my_m, 'CPC', '월별 CPC: 자사 vs 업종 평균', '원')
            st.plotly_chart(fig, width='stretch')


def _render_sales_points(my: dict, peers: pd.DataFrame, industry: str):
    """Section 5: 영업팀용 자동 생성 토킹포인트"""
    points: list[str] = []
    n = len(peers) + 1  # peers + self

    # 1. CTR 포지셔닝
    ctr_rank, total = _rank_of(my['ctr'], peers['CTR'].tolist(), True)
    avg_ctr = float(peers['CTR'].mean())
    diff_signed = my['ctr'] - avg_ctr
    # BUG I6: 미세 차이는 "유사"로 표시 (0.1%p 미만)
    if abs(diff_signed) < CTR_CHANGE_THRESHOLD / 3:
        points.append(
            f"귀사의 CTR <b>{my['ctr']:.2f}%</b>는 {industry} 업종 평균 {avg_ctr:.2f}%와 "
            f"<b>유사한 수준</b>입니다 (<b>{total}개 광고주 중 {ctr_rank}위</b>)."
        )
    elif my['ctr'] > avg_ctr:
        diff = my['ctr'] - avg_ctr
        points.append(
            f"귀사의 CTR <b>{my['ctr']:.2f}%</b>는 {industry} 업종 평균 {avg_ctr:.2f}% 대비 "
            f"<b>+{diff:.2f}%p 높은</b> 수준으로, <b>{total}개 광고주 중 {ctr_rank}위</b>입니다."
        )
    else:
        diff = avg_ctr - my['ctr']
        # BUG I1: top25 클릭 증가 시뮬레이션은 표본 충분 + CTR 하한 통과 시만
        n_peers = len(peers)
        if (
            n_peers >= MIN_SAMPLES_FOR_QUARTILE
            and my['ctr'] >= CTR_MIN_FLOOR
        ):
            top25_ctr = float(peers['CTR'].head(max(n_peers // 4, 1)).mean())
            lift_pct = top25_ctr / my['ctr'] * 100 - 100
            points.append(
                f"귀사의 CTR은 업종 평균보다 {diff:.2f}%p 낮습니다. "
                f"만약 상위 25% CTR({top25_ctr:.2f}%)에 도달한다면 "
                f"<b>동일 발송량 기준 클릭 약 {lift_pct:.0f}% 증가 예상 "
                f"(가정 시뮬레이션)</b>."
            )
        else:
            points.append(
                f"귀사의 CTR은 업종 평균보다 {diff:.2f}%p 낮습니다. "
                f"표본이 더 확보되면 상위 25% 시뮬레이션 비교가 가능합니다."
            )

    # 2. CPC 효율
    cpc_peers = peers[peers['CPC'] > 0]['CPC']
    if my['cpc'] > 0 and len(cpc_peers) > 0:
        avg_cpc = float(cpc_peers.mean())
        if my['cpc'] < avg_cpc:
            saving = (1 - my['cpc'] / avg_cpc) * 100
            points.append(
                f"CPC <b>{my['cpc']:,.0f}원</b>은 업종 평균 {avg_cpc:,.0f}원 대비 "
                f"<b>{saving:.0f}% 저렴</b>합니다. 비용 효율이 우수한 상태입니다."
            )
        else:
            excess = (my['cpc'] / avg_cpc - 1) * 100
            points.append(
                f"CPC가 업종 평균 대비 {excess:.0f}% 높습니다. "
                f"소재 최적화 또는 고효율 매체 비중 확대로 개선이 가능합니다."
            )

    # 3. 예산 규모 vs 상위 광고주 (BUG I2: 인과 단정 제거 + 표본 가드)
    if len(peers) >= MIN_SAMPLES_FOR_QUARTILE:
        q1 = max(len(peers) // 4, 1)
        top_budget = float(peers.head(q1)['월예산'].mean())
        if my['monthly_cost'] < top_budget * 0.5:
            gap = compact_num(top_budget - my['monthly_cost'], '원')
            points.append(
                f"업종 상위 25% 광고주의 월 평균 예산은 <b>{compact_num(top_budget, '원')}</b>입니다. "
                f"현재 대비 <b>{gap} 차이</b> — <b>예산 규모 참고용</b>."
            )

    # 4. 매체 다양성
    avg_media = float(peers['매체수'].mean())
    if my['n_media'] > avg_media * 1.3:
        points.append(
            f"귀사는 <b>{my['n_media']}개 매체</b>를 활용 중으로, "
            f"업종 평균({avg_media:.0f}개) 대비 다양한 채널을 운용하고 있습니다."
        )
    elif my['n_media'] < avg_media * 0.7 and avg_media > 2:
        points.append(
            f"업종 평균 {avg_media:.0f}개 매체 대비 귀사는 {my['n_media']}개로, "
            f"채널 다변화를 통해 도달률을 높일 수 있습니다."
        )

    if not points:
        return

    st.markdown(
        '<div style="font-size:0.92rem;font-weight:700;color:#191F28;'
        'margin-bottom:8px;">💬 영업 포인트</div>',
        unsafe_allow_html=True,
    )

    for p in points:
        st.markdown(
            f'<div style="background:#FFF8F0;border-left:3px solid {BRAND_PRIMARY};'
            f'border-radius:0 10px 10px 0;padding:12px 16px;margin-bottom:8px;'
            f'font-size:0.84rem;color:#4E5968;line-height:1.6;">{p}</div>',
            unsafe_allow_html=True,
        )


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────

def render(df: pd.DataFrame):
    st.markdown(section_header(
        '업종 내 경쟁 인텔리전스',
        '동종 업계 광고주 대비 포지셔닝을 확인합니다. '
        '"다른 데는 어떤데요?"에 대한 데이터 기반 답변입니다.',
    ), unsafe_allow_html=True)

    # ── Firebase 데이터 로드 ──
    try:
        bench = load_benchmark()
    except Exception:
        bench = None

    if bench is None or bench.empty:
        st.info('🔥 Firebase 벤치마크 데이터가 연결되면 업종 비교가 가능합니다.')
        st.divider()
        return

    # ── 업종 선택 ──
    industries = sorted([
        ind for ind in bench['분야'].dropna().unique().tolist()
        if str(ind).strip() and not str(ind).strip().isdigit()
    ])
    if not industries:
        st.warning('벤치마크 데이터에 업종 정보가 없습니다.')
        st.divider()
        return

    col_sel, col_info = st.columns([1, 2])
    with col_sel:
        selected = st.selectbox('📂 비교 업종 선택', industries, key='intel_industry')
    with col_info:
        ind_bench = bench[bench['분야'] == selected]
        n_camps = len(ind_bench)
        n_advs = ind_bench['광고주'].nunique()
        st.markdown(
            f'<div style="padding:10px 0 0;font-size:0.85rem;color:#4E5968;">'
            f'<b style="color:{BRAND_PRIMARY}">{selected}</b> 업종 — '
            f'광고주 <b>{n_advs}</b>개 · 캠페인 <b>{n_camps:,}</b>건</div>',
            unsafe_allow_html=True,
        )

    # ── 데이터 준비 ──
    my = _my_metrics(df)
    peers = _peer_metrics(bench, selected)

    if peers.empty or len(peers) < 2:
        st.info(f'{selected} 업종에 비교 가능한 광고주가 부족합니다 (최소 2개 필요).')
        st.divider()
        return

    # ── 면책 ──
    st.markdown(
        '<div style="font-size:0.73rem;color:#B0B8C1;margin-bottom:16px;">'
        '※ 모든 광고주 데이터는 익명 처리되며, 귀사의 데이터도 타사에 노출되지 않습니다.</div>',
        unsafe_allow_html=True,
    )

    # ── Section 1: 포지셔닝 개요 ──
    _render_positioning(my, peers)
    st.markdown('<div style="margin-top:24px"></div>', unsafe_allow_html=True)

    # ── Section 2: 경쟁사 비교 바 차트 ──
    _render_peer_bars(my, peers)
    st.markdown('<div style="margin-top:20px"></div>', unsafe_allow_html=True)

    # ── Section 3: 상위 광고주 패턴 ──
    st.markdown(
        f'<div style="font-size:0.92rem;font-weight:700;color:#191F28;'
        f'margin-bottom:8px;">🏆 상위 25% vs 하위 25% vs 자사</div>',
        unsafe_allow_html=True,
    )
    _render_top_patterns(my, peers)
    st.markdown('<div style="margin-top:20px"></div>', unsafe_allow_html=True)

    # ── Section 4: 업종 트렌드 ──
    st.markdown(
        f'<div style="font-size:0.92rem;font-weight:700;color:#191F28;'
        f'margin-bottom:8px;">📈 업종 트렌드 추이</div>',
        unsafe_allow_html=True,
    )
    _render_trend(df, bench, selected)
    st.markdown('<div style="margin-top:20px"></div>', unsafe_allow_html=True)

    # ── Section 5: 영업 포인트 ──
    _render_sales_points(my, peers, selected)

    st.divider()
