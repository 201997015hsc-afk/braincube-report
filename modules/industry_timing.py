"""
업종별 최적 발송 타이밍 분석 모듈
──────────────────────────────
Firebase Firestore(campaigns) 실시간 크로스 클라이언트 데이터를 활용하여:
  1) 업종별 x 요일별 CTR 히트맵 — 클라이언트가 혼자는 못 보는 업계 전체 패턴
  2) 업종별 최적 발송일 추천 (TOP 3 요일 + 메달 랭킹)
  3) 자사 발송 패턴 vs 업종 최적 패턴 비교 → 개선 포인트
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from modules.config import (
    section_header, alert_card_html, kpi_card_html,
    CHART_COLORS, PLOTLY_LAYOUT, COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER,
    BRAND_PRIMARY, COLOR_BLUE, COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING,
    compact_num, WEEKDAY_MAP,
)
from modules.data_processing import calc_ctr_scalar
from modules.firebase_connector import load_benchmark as _fb_load_benchmark, get_data_source

_DOW_KR = ['월', '화', '수', '목', '금', '토', '일']
_DOW_FULL = ['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일']

# ══════════════════════════════════════════════
# CSS
# ══════════════════════════════════════════════

_IT_CSS = """<style>
.it-summary{display:flex;gap:12px;margin:12px 0 20px 0}
.it-summary-item{flex:1;background:#FFF;border-radius:14px;padding:18px 16px;
  box-shadow:0 1px 4px rgba(0,0,0,.03);text-align:center;transition:all .2s ease}
.it-summary-item:hover{box-shadow:0 4px 16px rgba(0,0,0,.06);transform:translateY(-1px)}
.it-s-label{font-size:.75rem;color:#8B95A1;font-weight:600;margin-bottom:4px}
.it-s-value{font-weight:800;font-size:1.2rem;color:#191F28}
.it-s-sub{font-size:.72rem;color:#8B95A1;margin-top:2px}
.it-day-card{background:#FFF;border-radius:14px;padding:18px;
  box-shadow:0 1px 4px rgba(0,0,0,.03);text-align:center;transition:all .2s ease}
.it-day-card:hover{box-shadow:0 4px 16px rgba(0,0,0,.06);transform:translateY(-2px)}
.it-day-medal{font-size:1.5rem}
.it-day-name{font-weight:700;font-size:1rem;color:#191F28;margin-top:4px}
.it-day-ctr{font-weight:800;font-size:1.3rem;color:#F7931D;margin-top:4px}
.it-day-detail{font-size:.73rem;color:#8B95A1;margin-top:6px}
.it-day-bar{margin-top:8px;background:#F2F4F6;border-radius:4px;height:6px;overflow:hidden}
.it-day-bar-fill{height:6px;border-radius:4px;transition:width .5s ease}
.it-gap-card{display:grid;grid-template-columns:1fr 80px 1fr;align-items:center;
  padding:14px 20px;margin-bottom:6px;background:#FFF;border-radius:14px;
  box-shadow:0 1px 4px rgba(0,0,0,.03);transition:all .2s ease}
.it-gap-card:hover{box-shadow:0 4px 16px rgba(0,0,0,.06);transform:translateY(-1px)}
.it-gap-left{text-align:left}
.it-gap-right{text-align:right}
.it-gap-mid{text-align:center;font-weight:700;font-size:.82rem;color:#8B95A1;white-space:nowrap}
.it-gap-mid > div{white-space:nowrap}
.it-gap-label{font-size:.70rem;color:#8B95A1;font-weight:600}
.it-gap-val{font-weight:800;font-size:1rem;color:#191F28}
</style>"""


# ══════════════════════════════════════════════
# 데이터 로더
# ══════════════════════════════════════════════

def _load_bench_with_weekday() -> pd.DataFrame | None:
    """Firebase Firestore 벤치마크 데이터 로드 + 요일 파생"""
    df = _fb_load_benchmark()

    if df is None or df.empty:
        return None

    # ── 날짜 → 요일 파생 ──
    # firebase_connector가 이미 _date 컬럼을 생성
    if '_date' not in df.columns and '일자' in df.columns:
        df['_date'] = pd.to_datetime(df['일자'], errors='coerce')

    if '_date' not in df.columns:
        return None

    df = df.dropna(subset=['_date'])
    if df.empty:
        return None

    df['_요일번호'] = df['_date'].dt.dayofweek  # 0=월 .. 6=일
    df['_요일'] = df['_요일번호'].map(dict(enumerate(_DOW_KR)))

    # 숫자 컬럼은 firebase_connector에서 이미 정리됨. CSV 폴백 보정만.
    for col in ['발송건', '광고비']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    if '클릭수' in df.columns:
        df['클릭수'] = pd.to_numeric(df['클릭수'], errors='coerce')
        if '_has_click' not in df.columns:
            df['_has_click'] = df['클릭수'].notna() & (df['클릭수'] > 0)
    else:
        df['_has_click'] = False
        df['클릭수'] = np.nan

    # LMS 필터
    if '광고상품' in df.columns:
        df = df[df['광고상품'] == 'LMS'].copy()

    return df


def _build_industry_weekday(bench: pd.DataFrame) -> pd.DataFrame:
    """업종 x 요일 피벗 집계 (트래킹 행 기준 CTR)"""
    tracked = bench[bench['_has_click']].copy()
    if tracked.empty:
        return pd.DataFrame()

    agg = tracked.groupby(['분야', '_요일', '_요일번호']).agg(
        발송건=('발송건', 'sum'),
        클릭수=('클릭수', lambda x: x.fillna(0).sum()),
        캠페인수=('분야', 'count'),
    ).reset_index()
    agg['CTR'] = np.where(agg['발송건'] > 0, agg['클릭수'] / agg['발송건'] * 100, 0)
    return agg


def _build_my_weekday(df: pd.DataFrame) -> pd.DataFrame:
    """자사 데이터의 요일별 집계"""
    if '요일번호' not in df.columns:
        return pd.DataFrame()
    agg = df.groupby('요일번호').agg(
        발송량=('발송량', 'sum'),
        클릭수=('클릭수', 'sum'),
    ).reset_index()
    agg['CTR'] = np.where(agg['발송량'] > 0, agg['클릭수'] / agg['발송량'] * 100, 0)
    agg['_요일'] = agg['요일번호'].map(dict(enumerate(_DOW_KR)))
    return agg


# ══════════════════════════════════════════════
# 렌더
# ══════════════════════════════════════════════

def render(df: pd.DataFrame):
    st.markdown(_IT_CSS, unsafe_allow_html=True)

    st.markdown(section_header(
        "업종별 최적 발송 타이밍",
        "크로스 클라이언트 데이터 기반 — 업종별로 어떤 요일에 발송해야 CTR이 높은지 분석합니다.",
    ), unsafe_allow_html=True)

    bench = _load_bench_with_weekday()

    # 데이터 소스 뱃지
    st.caption(f"데이터 소스: <b>{get_data_source()}</b>", unsafe_allow_html=True)

    if bench is None or bench.empty:
        st.info("📊 Firebase Firestore 연동이 필요합니다. 서비스 계정 키를 확인해 주세요.")
        st.divider()
        return

    # 분야 필터 (빈 문자열/숫자만 있는 값 제외)
    industries = sorted([
        ind for ind in bench['분야'].dropna().unique().tolist()
        if str(ind).strip() and not str(ind).strip().isdigit()
    ])
    if not industries:
        st.warning("벤치마크 데이터에 업종(분야) 정보가 없습니다.")
        st.divider()
        return

    from modules.ui_helpers import help_text as _help_text
    selected = st.selectbox(
        "🏢 업종 선택", ["전체"] + industries,
        index=0, key="it_industry",
        help=_help_text("industry"),
    )

    if selected != "전체":
        bench_f = bench[bench['분야'] == selected].copy()
    else:
        bench_f = bench.copy()

    iw = _build_industry_weekday(bench_f)
    if iw.empty:
        st.info("선택한 업종에 클릭 트래킹 데이터가 부족합니다.")
        st.divider()
        return

    # ── 전체 요약 ──
    total_campaigns = iw['캠페인수'].sum()
    total_sends = iw['발송건'].sum()
    total_clicks = iw['클릭수'].sum()
    overall_ctr = calc_ctr_scalar(total_clicks, total_sends)

    st.markdown(
        f'<div class="it-summary">'
        f'  <div class="it-summary-item">'
        f'    <div class="it-s-label">분석 캠페인</div>'
        f'    <div class="it-s-value">{total_campaigns:,.0f}건</div>'
        f'    <div class="it-s-sub">{len(iw["분야"].unique())}개 업종</div>'
        f'  </div>'
        f'  <div class="it-summary-item">'
        f'    <div class="it-s-label">총 발송</div>'
        f'    <div class="it-s-value">{compact_num(total_sends, "건")}</div>'
        f'    <div class="it-s-sub">클릭 트래킹 기준</div>'
        f'  </div>'
        f'  <div class="it-summary-item">'
        f'    <div class="it-s-label">평균 CTR</div>'
        f'    <div class="it-s-value">{overall_ctr:.2f}%</div>'
        f'    <div class="it-s-sub">전 요일 평균</div>'
        f'  </div>'
        f'</div>', unsafe_allow_html=True)

    # ══════════════════════════════════════
    # TAB 구성
    # ══════════════════════════════════════
    tab1, tab2, tab3 = st.tabs([
        "📊 요일별 CTR 히트맵", "🏆 최적 발송일 추천", "🔄 자사 패턴 비교"
    ])

    # ── TAB 1: 히트맵 ──
    with tab1:
        _render_heatmap(iw, selected)

    # ── TAB 2: 최적 발송일 ──
    with tab2:
        _render_best_days(iw, selected)

    # ── TAB 3: 자사 비교 ──
    with tab3:
        _render_my_comparison(df, iw, selected)

    st.divider()


def _render_heatmap(iw: pd.DataFrame, industry_label: str):
    """업종 x 요일 CTR 히트맵"""
    # 업종별 요일 피벗
    if industry_label == "전체":
        pivot_data = iw.groupby(['분야', '_요일번호', '_요일']).agg(
            발송건=('발송건', 'sum'),
            클릭수=('클릭수', 'sum'),
        ).reset_index()
        pivot_data['CTR'] = np.where(
            pivot_data['발송건'] > 0, pivot_data['클릭수'] / pivot_data['발송건'] * 100, 0
        )

        pivot = pivot_data.pivot_table(index='분야', columns='_요일번호', values='CTR', fill_value=0)
        pivot.columns = [_DOW_KR[c] for c in pivot.columns]

        # 행 정렬: 평균 CTR 높은 순
        pivot['_avg'] = pivot.mean(axis=1)
        pivot = pivot.sort_values('_avg', ascending=False).drop(columns='_avg')

        z_vals = pivot.values
        y_labels = list(pivot.index)
        x_labels = list(pivot.columns)

        # Toss-style 히트맵 색상
        cs = [
            [0, '#FFF8F0'], [0.15, '#FFE8CC'], [0.35, '#FFD19A'],
            [0.55, '#FFB74D'], [0.75, '#F7931D'], [1, '#D47700'],
        ]

        max_val = z_vals.max() if z_vals.size > 0 else 1
        min_val = z_vals[z_vals > 0].min() if (z_vals > 0).any() else 0

        # 셀 텍스트
        text_matrix = []
        font_colors = []
        threshold = max_val * 0.55
        for row in z_vals:
            texts = []
            colors = []
            for v in row:
                if v == 0:
                    texts.append("")
                    colors.append('#8B95A1')
                else:
                    txt = f"{v:.2f}%"
                    if v == max_val:
                        txt = f"★ {txt}"
                    texts.append(txt)
                    colors.append('#FFFFFF' if v > threshold else '#4E5968')
            text_matrix.append(texts)
            font_colors.append(colors)

        n_rows = len(y_labels)
        cell_h = 48 if n_rows <= 6 else (40 if n_rows <= 10 else 34)
        fig_h = max(350, n_rows * cell_h + 100)

        fig = go.Figure(data=go.Heatmap(
            z=z_vals, x=x_labels, y=y_labels,
            colorscale=cs, xgap=4, ygap=4,
            hovertemplate='<b>%{y}</b> %{x}요일<br>CTR: %{z:.2f}%<extra></extra>',
            colorbar=dict(
                thickness=12, outlinewidth=0,
                tickfont=dict(size=10, color='#8B95A1'),
                title=dict(text="CTR (%)", font=dict(size=10, color='#8B95A1')),
                lenmode='fraction', len=0.5,
            ),
        ))

        # 어노테이션
        annotations = [
            dict(
                x=x_labels[j], y=y_labels[i],
                text=text_matrix[i][j], showarrow=False,
                font=dict(size=11, color=font_colors[i][j], family='Pretendard, sans-serif'),
            )
            for i in range(len(z_vals))
            for j in range(len(z_vals[i]))
            if text_matrix[i][j]
        ]
        fig.update_layout(annotations=annotations)

        layout = {**PLOTLY_LAYOUT}
        layout.update(
            height=fig_h,
            title={**PLOTLY_LAYOUT['title'], 'text': '업종별 x 요일별 CTR 히트맵'},
            margin=dict(t=48, l=120, r=30, b=50),
            xaxis=dict(side='bottom', showgrid=False, title=''),
            yaxis=dict(showgrid=False, title='', autorange='reversed'),
        )
        fig.update_layout(**layout)
        st.plotly_chart(fig, key="it_heatmap", width="stretch")

    else:
        # 단일 업종: 매체별 x 요일별 히트맵
        bench_ind = iw.copy()
        # 요일별 집계
        dow_agg = bench_ind.groupby(['_요일번호', '_요일']).agg(
            발송건=('발송건', 'sum'),
            클릭수=('클릭수', 'sum'),
            캠페인수=('캠페인수', 'sum'),
        ).reset_index().sort_values('_요일번호')
        dow_agg['CTR'] = np.where(dow_agg['발송건'] > 0, dow_agg['클릭수'] / dow_agg['발송건'] * 100, 0)

        # 바 차트로 요일별 CTR
        colors = [BRAND_PRIMARY if ctr == dow_agg['CTR'].max() else CHART_COLORS[1] for ctr in dow_agg['CTR']]
        _ymax = float(dow_agg['CTR'].max()) * 1.25 if not dow_agg.empty else 1.0
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=dow_agg['_요일'], y=dow_agg['CTR'],
            marker_color=colors, marker_cornerradius=6, opacity=0.88,
            text=dow_agg['CTR'].apply(lambda x: f"{x:.2f}%"),
            textposition='outside',
            cliponaxis=False,
            textfont=dict(size=12, family='Pretendard, sans-serif'),
            hovertemplate='%{x}요일<br>CTR: %{y:.2f}%<br>캠페인: %{customdata}건<extra></extra>',
            customdata=dow_agg['캠페인수'],
        ))
        layout = {**PLOTLY_LAYOUT}
        layout.update(
            height=380,
            title={**PLOTLY_LAYOUT['title'], 'text': f'{industry_label} 업종 — 요일별 CTR'},
            xaxis_title="", yaxis_title="CTR (%)",
            margin=dict(t=70, l=48, r=24, b=48),
            yaxis=dict(title="CTR (%)", range=[0, _ymax]),
        )
        fig.update_layout(**layout)
        st.plotly_chart(fig, key="it_dow_bar", width="stretch")

        # 발송량 vs CTR 듀얼 축
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(
            x=dow_agg['_요일'], y=dow_agg['발송건'], name='발송건',
            marker_color=CHART_COLORS[1], marker_cornerradius=6, opacity=0.7,
        ))
        fig2.add_trace(go.Scatter(
            x=dow_agg['_요일'], y=dow_agg['CTR'], name='CTR',
            mode='lines+markers', marker_color=BRAND_PRIMARY,
            line_width=2.5, marker_size=8, yaxis='y2', line_shape='spline',
        ))
        layout2 = {**PLOTLY_LAYOUT}
        layout2.update(
            height=350,
            title={**PLOTLY_LAYOUT['title'], 'text': f'{industry_label} — 발송량 vs CTR'},
            yaxis2=dict(overlaying='y', side='right', showgrid=False,
                        tickfont=dict(size=11, color='#8B95A1')),
            margin=dict(t=48, l=48, r=60, b=48),
        )
        fig2.update_layout(**layout2)
        st.plotly_chart(fig2, key="it_dual", width="stretch")


def _render_best_days(iw: pd.DataFrame, industry_label: str):
    """업종별 최적 발송일 추천"""
    if industry_label == "전체":
        # 각 업종의 Best Day 계산
        industries = iw['분야'].unique()
        best_days = []
        for ind in industries:
            ind_data = iw[iw['분야'] == ind]
            # 요일별 합산
            dow = ind_data.groupby(['_요일번호', '_요일']).agg(
                발송건=('발송건', 'sum'), 클릭수=('클릭수', 'sum'),
            ).reset_index()
            dow['CTR'] = np.where(dow['발송건'] > 0, dow['클릭수'] / dow['발송건'] * 100, 0)
            if dow['CTR'].max() > 0:
                best = dow.loc[dow['CTR'].idxmax()]
                worst = dow.loc[dow['CTR'].idxmin()]
                best_days.append({
                    '분야': ind,
                    '최적요일': best['_요일'],
                    '최적CTR': best['CTR'],
                    '최저요일': worst['_요일'],
                    '최저CTR': worst['CTR'],
                    '격차': best['CTR'] - worst['CTR'],
                    '총발송': dow['발송건'].sum(),
                })

        if not best_days:
            st.info("분석할 데이터가 부족합니다.")
            return

        best_df = pd.DataFrame(best_days).sort_values('격차', ascending=False)

        st.markdown(
            '<div style="font-size:.82rem;color:#8B95A1;margin-bottom:12px;font-weight:600;">'
            '업종별 최적 발송일 (CTR 기준)</div>', unsafe_allow_html=True)

        # 격차 큰 순으로 카드 표시
        for _, row in best_df.iterrows():
            gap_color = COLOR_SUCCESS if row['격차'] > 0.5 else COLOR_WARNING
            st.markdown(
                f'<div class="it-gap-card">'
                f'  <div class="it-gap-left">'
                f'    <div class="it-gap-label">{row["분야"]}</div>'
                f'    <div class="it-gap-val" style="color:{BRAND_PRIMARY}">'
                f'      {row["최적요일"]}요일 {row["최적CTR"]:.2f}%</div>'
                f'  </div>'
                f'  <div class="it-gap-mid">vs</div>'
                f'  <div class="it-gap-right">'
                f'    <div class="it-gap-label">최저 요일</div>'
                f'    <div class="it-gap-val" style="color:{COLOR_TEXT_SEC}">'
                f'      {row["최저요일"]}요일 {row["최저CTR"]:.2f}%</div>'
                f'    <div style="font-size:.70rem;color:{gap_color};font-weight:700;margin-top:2px">'
                f'      격차 {row["격차"]:.2f}%p</div>'
                f'  </div>'
                f'</div>', unsafe_allow_html=True)

        # 인사이트
        top3 = best_df.head(3)
        if not top3.empty:
            biggest = top3.iloc[0]
            st.markdown(alert_card_html(
                "success",
                f"요일 격차가 가장 큰 업종: {biggest['분야']}",
                f"<b>{biggest['분야']}</b> 업종은 <b>{biggest['최적요일']}요일</b>에 발송하면 "
                f"<b>{biggest['최저요일']}요일</b> 대비 CTR이 <b>{biggest['격차']:.2f}%p</b> 높습니다. "
                f"발송 요일만 바꿔도 클릭이 크게 달라집니다.",
            ), unsafe_allow_html=True)

    else:
        # 단일 업종: TOP 3 요일 메달 카드
        dow_agg = iw.groupby(['_요일번호', '_요일']).agg(
            발송건=('발송건', 'sum'), 클릭수=('클릭수', 'sum'),
            캠페인수=('캠페인수', 'sum'),
        ).reset_index()
        dow_agg['CTR'] = np.where(dow_agg['발송건'] > 0, dow_agg['클릭수'] / dow_agg['발송건'] * 100, 0)
        dow_agg = dow_agg.sort_values('CTR', ascending=False)

        top_n = min(len(dow_agg), 4)
        top = dow_agg.head(top_n)
        if top.empty:
            st.info("분석 데이터가 부족합니다.")
            return

        medals = ["🥇", "🥈", "🥉", "4️⃣"]
        max_ctr = top['CTR'].max()
        cols = st.columns(top_n, gap="medium")
        for idx, (_, row) in enumerate(top.iterrows()):
            color = CHART_COLORS[idx % len(CHART_COLORS)]
            bar_pct = row['CTR'] / max_ctr * 100 if max_ctr > 0 else 0
            with cols[idx]:
                st.markdown(
                    f'<div class="it-day-card">'
                    f'  <div class="it-day-medal">{medals[idx]}</div>'
                    f'  <div class="it-day-name">{row["_요일"]}요일</div>'
                    f'  <div class="it-day-ctr">{row["CTR"]:.2f}%</div>'
                    f'  <div class="it-day-bar">'
                    f'    <div class="it-day-bar-fill" style="background:{color};width:{bar_pct:.0f}%"></div>'
                    f'  </div>'
                    f'  <div class="it-day-detail">'
                    f'    {row["캠페인수"]:.0f}건 · {compact_num(row["발송건"], "건")}'
                    f'  </div>'
                    f'</div>', unsafe_allow_html=True)

        if top_n >= 2:
            best = top.iloc[0]
            second = top.iloc[1]
            st.markdown(alert_card_html(
                "success",
                f"{industry_label} 업종 최적 발송일: {best['_요일']}요일",
                f"<b>{best['_요일']}요일</b>의 CTR이 <b>{best['CTR']:.2f}%</b>로 가장 높습니다. "
                f"2위 <b>{second['_요일']}요일</b>({second['CTR']:.2f}%) 대비 "
                f"<b>+{best['CTR'] - second['CTR']:.2f}%p</b> 우위입니다.",
            ), unsafe_allow_html=True)


def _render_my_comparison(df: pd.DataFrame, iw: pd.DataFrame, industry_label: str):
    """자사 발송 패턴 vs 업종 최적 패턴 비교"""
    my_dow = _build_my_weekday(df)

    if my_dow.empty or '요일번호' not in df.columns:
        st.info("자사 데이터에 요일 정보가 없습니다. 날짜 컬럼이 필요합니다.")
        return

    # 업종 요일별 집계
    bench_dow = iw.groupby(['_요일번호', '_요일']).agg(
        발송건=('발송건', 'sum'), 클릭수=('클릭수', 'sum'),
    ).reset_index()
    bench_dow['CTR'] = np.where(bench_dow['발송건'] > 0, bench_dow['클릭수'] / bench_dow['발송건'] * 100, 0)

    # 매칭
    comparison = []
    for dow_num in range(7):
        dow_kr = _DOW_KR[dow_num]
        my_row = my_dow[my_dow['요일번호'] == dow_num]
        bench_row = bench_dow[bench_dow['_요일번호'] == dow_num]

        my_ctr = my_row['CTR'].values[0] if not my_row.empty else 0
        my_send = my_row['발송량'].values[0] if not my_row.empty else 0
        bench_ctr = bench_row['CTR'].values[0] if not bench_row.empty else 0
        bench_send = bench_row['발송건'].values[0] if not bench_row.empty else 0

        comparison.append({
            '요일': dow_kr, '요일번호': dow_num,
            '자사_CTR': my_ctr, '업종_CTR': bench_ctr,
            '자사_발송': my_send, '업종_발송': bench_send,
            'CTR_격차': my_ctr - bench_ctr,
        })

    comp_df = pd.DataFrame(comparison)

    # ── 오버레이 차트: 자사 vs 업종 ──
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=comp_df['요일'], y=comp_df['자사_발송'], name='자사 발송량',
        marker_color=BRAND_PRIMARY, marker_cornerradius=6, opacity=0.7,
    ))
    fig.add_trace(go.Scatter(
        x=comp_df['요일'], y=comp_df['자사_CTR'], name='자사 CTR',
        mode='lines+markers', marker=dict(color=BRAND_PRIMARY, size=10),
        line=dict(width=3, dash='solid'), yaxis='y2', line_shape='spline',
    ))
    fig.add_trace(go.Scatter(
        x=comp_df['요일'], y=comp_df['업종_CTR'], name='업종 평균 CTR',
        mode='lines+markers', marker=dict(color=CHART_COLORS[1], size=10),
        line=dict(width=3, dash='dash'), yaxis='y2', line_shape='spline',
    ))
    ind_tag = f" ({industry_label})" if industry_label != "전체" else ""
    layout = {**PLOTLY_LAYOUT}
    layout.update(
        height=420,
        title={**PLOTLY_LAYOUT['title'], 'text': f'자사 발송 패턴 vs 업종 CTR 패턴{ind_tag}'},
        yaxis=dict(title="발송량", gridcolor='#F0F1F3'),
        yaxis2=dict(title="CTR (%)", overlaying='y', side='right', showgrid=False,
                    tickfont=dict(size=11, color='#8B95A1')),
        margin=dict(t=48, l=60, r=60, b=48),
        legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='center', x=0.5),
    )
    fig.update_layout(**layout)
    st.plotly_chart(fig, key="it_compare_chart", width="stretch")

    # ── 요일별 VS 카드 ──
    st.markdown(
        '<div style="font-size:.82rem;color:#8B95A1;margin:12px 0 8px 0;font-weight:600;">'
        '요일별 자사 vs 업종 비교</div>', unsafe_allow_html=True)

    for _, row in comp_df.iterrows():
        gap = row['CTR_격차']
        gap_color = COLOR_SUCCESS if gap >= 0 else COLOR_DANGER
        st.markdown(
            f'<div class="it-gap-card">'
            f'  <div class="it-gap-left">'
            f'    <div class="it-gap-label">자사</div>'
            f'    <div class="it-gap-val">{row["자사_CTR"]:.2f}%</div>'
            f'    <div class="it-gap-label">{compact_num(row["자사_발송"], "건")}</div>'
            f'  </div>'
            f'  <div class="it-gap-mid">'
            f'    <div>{row["요일"]}요일</div>'
            f'    <div style="font-size:.70rem;color:{gap_color};font-weight:700">{gap:+.2f}%p</div>'
            f'  </div>'
            f'  <div class="it-gap-right">'
            f'    <div class="it-gap-label">업종 평균</div>'
            f'    <div class="it-gap-val">{row["업종_CTR"]:.2f}%</div>'
            f'    <div class="it-gap-label">{compact_num(row["업종_발송"], "건")}</div>'
            f'  </div>'
            f'</div>', unsafe_allow_html=True)

    # ── 인사이트 ──
    # 자사가 업종보다 낮은 요일 중 자사 발송량이 많은 것 = 개선 기회
    underperform = comp_df[(comp_df['CTR_격차'] < -0.1) & (comp_df['자사_발송'] > 0)]
    if not underperform.empty:
        worst_gap = underperform.loc[underperform['CTR_격차'].idxmin()]
        st.markdown(alert_card_html(
            "warning",
            f"⚠️ {worst_gap['요일']}요일 개선 기회",
            f"자사 CTR <b>{worst_gap['자사_CTR']:.2f}%</b>가 업종 평균 <b>{worst_gap['업종_CTR']:.2f}%</b>보다 "
            f"<b>{abs(worst_gap['CTR_격차']):.2f}%p</b> 낮습니다. "
            f"이 요일의 소재 점검 또는 발송 시간 조정을 검토하세요.",
        ), unsafe_allow_html=True)

    outperform = comp_df[(comp_df['CTR_격차'] > 0.1) & (comp_df['자사_발송'] > 0)]
    if not outperform.empty:
        best_gap = outperform.loc[outperform['CTR_격차'].idxmax()]
        st.markdown(alert_card_html(
            "success",
            f"{best_gap['요일']}요일 강점",
            f"자사 CTR <b>{best_gap['자사_CTR']:.2f}%</b>가 업종 평균보다 "
            f"<b>+{best_gap['CTR_격차']:.2f}%p</b> 높습니다. 이 요일에 발송량을 늘리는 것을 권장합니다.",
        ), unsafe_allow_html=True)

    # 최적 요일 제안
    bench_best = comp_df.loc[comp_df['업종_CTR'].idxmax()]
    my_best = comp_df.loc[comp_df['자사_CTR'].idxmax()]
    if bench_best['요일'] != my_best['요일'] and bench_best['업종_CTR'] > my_best['자사_CTR']:
        st.markdown(alert_card_html(
            "info",
            "발송 요일 변경 제안",
            f"업종 최적 발송일은 <b>{bench_best['요일']}요일</b>(CTR {bench_best['업종_CTR']:.2f}%)이지만, "
            f"자사 주력 발송일은 <b>{my_best['요일']}요일</b>입니다. "
            f"<b>{bench_best['요일']}요일</b>로 발송을 이동하면 CTR 개선이 기대됩니다.",
        ), unsafe_allow_html=True)
