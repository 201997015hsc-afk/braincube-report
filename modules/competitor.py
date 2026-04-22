"""
경쟁사 · 업종 벤치마크 분석 모듈
────────────────────────────────
Firebase Firestore(campaigns) 실시간 데이터를 활용하여:
  1) 업종별 벤치마크 비교  — 자사 CTR/CPC vs 업종 중앙값
  2) 매체별 벤치마크       — 채널별 성과를 업종 평균과 오버레이
  3) 광고주 비교 분석      — 업종 내 광고주별 포지셔닝
  4) 수동 경쟁사 입력 + API — 기존 SEMrush/Ahrefs 연동 유지
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from modules.config import (
    section_header, alert_card_html, kpi_card_html, insight_card_html,
    CHART_COLORS, PLOTLY_LAYOUT, COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER,
    BRAND_PRIMARY, COLOR_BLUE, COLOR_BG, COLOR_SUCCESS, COLOR_DANGER,
    COLOR_WARNING, compact_num,
    MIN_SAMPLES_PER_CATEGORY, MIN_PEERS_FOR_RANKING, MIN_SENDS_FOR_CTR_CLAIM,
    MIN_LIFT_RELATIVE, CTR_CHANGE_THRESHOLD, CTR_MIN_FLOOR,
)
from modules.data_processing import (
    calc_ctr, calc_ctr_scalar, aggregate_by_weekday, render_ref_period_selector,
)
from modules.firebase_connector import load_benchmark as _fb_load_benchmark, get_data_source


# ══════════════════════════════════════════════
# 1. Benchmark 데이터 로더 (Firebase → CSV 폴백)
# ══════════════════════════════════════════════

def _load_benchmark() -> pd.DataFrame | None:
    """Firebase Firestore에서 벤치마크 데이터 로드"""
    return _fb_load_benchmark()


def _filter_product(bench: pd.DataFrame, product: str = "LMS") -> pd.DataFrame:
    """광고상품 필터. 'LMS'(기본), '전체', 또는 특정 상품명."""
    if '광고상품' not in bench.columns or product == "전체":
        return bench
    return bench[bench['광고상품'] == product].copy()


def _ctr_safe(sends: pd.Series, clicks: pd.Series) -> pd.Series:
    """CTR 계산 — 클릭수 NaN 행은 분모에서 제외하고 유효 행만 계산"""
    valid_clicks = clicks.fillna(0)
    return np.where(sends > 0, valid_clicks / sends * 100, 0)


def _industry_agg(bench: pd.DataFrame) -> pd.DataFrame:
    """업종별 집계 — 클릭 트래킹 있는 행 기준 CTR 계산"""
    # 유효한 업종명만 필터링 (빈 문자열, 공백, 숫자만 있는 값 제외)
    if '분야' not in bench.columns:
        return pd.DataFrame()
    _ind_str = bench['분야'].astype(str).str.strip()
    _valid = (
        bench['분야'].notna()
        & (_ind_str != '')
        & (_ind_str.str.lower() != 'nan')
        & (~_ind_str.str.fullmatch(r'\d+'))  # 숫자만 있는 값 제외 (예: "7")
    )
    bench = bench[_valid].copy()
    if bench.empty:
        return pd.DataFrame()

    # 전체 볼륨 (발송건/광고비: 모든 행 포함)
    vol = bench.groupby('분야').agg(
        총발송건=('발송건', 'sum'),
        총광고비=('광고비', 'sum'),
        캠페인수=('분야', 'count'),
    ).reset_index()

    # CTR 계산용: 클릭 트래킹 있는 행만
    tracked = bench[bench['_has_click']].copy() if '_has_click' in bench.columns else bench
    tracked_agg = tracked.groupby('분야').agg(
        트래킹_발송건=('발송건', 'sum'),
        클릭수=('클릭수', lambda x: x.fillna(0).sum()),
        트래킹_건수=('분야', 'count'),
    ).reset_index()

    agg = vol.merge(tracked_agg, on='분야', how='left').fillna(0)
    agg['CTR'] = np.where(agg['트래킹_발송건'] > 0, agg['클릭수'] / agg['트래킹_발송건'] * 100, 0)
    agg['CPC'] = np.where(agg['클릭수'] > 0, agg['총광고비'] / agg['클릭수'], 0)
    agg['CPM'] = np.where(agg['총발송건'] > 0, agg['총광고비'] / agg['총발송건'] * 1000, 0)
    # 호환성: 기존 코드가 '발송건', '광고비' 참조
    agg['발송건'] = agg['총발송건']
    agg['광고비'] = agg['총광고비']
    return agg.sort_values('캠페인수', ascending=False)


def _media_agg(bench: pd.DataFrame) -> pd.DataFrame:
    """매체별 집계 — 클릭 트래킹 있는 행 기준 CTR 계산"""
    # 유효한 매체명만 필터링 (빈 문자열, 숫자만 있는 값 제외)
    if '매체' not in bench.columns:
        return pd.DataFrame()
    _med_str = bench['매체'].astype(str).str.strip()
    _valid = (
        bench['매체'].notna()
        & (_med_str != '')
        & (_med_str.str.lower() != 'nan')
        & (~_med_str.str.fullmatch(r'\d+'))
    )
    bench = bench[_valid].copy()
    if bench.empty:
        return pd.DataFrame()

    vol = bench.groupby('매체').agg(
        총발송건=('발송건', 'sum'),
        총광고비=('광고비', 'sum'),
        캠페인수=('매체', 'count'),
    ).reset_index()

    tracked = bench[bench['_has_click']].copy() if '_has_click' in bench.columns else bench
    tracked_agg = tracked.groupby('매체').agg(
        트래킹_발송건=('발송건', 'sum'),
        클릭수=('클릭수', lambda x: x.fillna(0).sum()),
    ).reset_index()

    agg = vol.merge(tracked_agg, on='매체', how='left').fillna(0)
    agg['CTR'] = np.where(agg['트래킹_발송건'] > 0, agg['클릭수'] / agg['트래킹_발송건'] * 100, 0)
    agg['CPC'] = np.where(agg['클릭수'] > 0, agg['총광고비'] / agg['클릭수'], 0)
    agg['발송건'] = agg['총발송건']
    agg['광고비'] = agg['총광고비']
    return agg.sort_values('캠페인수', ascending=False)


def _advertiser_agg(bench: pd.DataFrame) -> pd.DataFrame:
    """광고주(회사) + 브랜드별 집계 — 클릭 트래킹 행 기준"""
    # 광고주 + 브랜드 + 분야로 그룹핑
    brand_col = '_브랜드' if '_브랜드' in bench.columns else '광고주'
    group_cols = ['광고주', brand_col, '분야'] if brand_col != '광고주' else ['광고주', '분야']

    # 유효한 광고주/브랜드/업종만 필터링 (빈 문자열, 숫자만 있는 값 제외)
    if '광고주' not in bench.columns or '분야' not in bench.columns:
        return pd.DataFrame()
    bench = bench.copy()
    for _c in group_cols:
        if _c not in bench.columns:
            return pd.DataFrame()
        _s = bench[_c].astype(str).str.strip()
        _mask = (
            bench[_c].notna()
            & (_s != '')
            & (_s.str.lower() != 'nan')
            & (~_s.str.fullmatch(r'\d+'))
        )
        bench = bench[_mask]
    if bench.empty:
        return pd.DataFrame()

    vol = bench.groupby(group_cols).agg(
        총발송건=('발송건', 'sum'),
        총광고비=('광고비', 'sum'),
        캠페인수=('광고주', 'count'),
    ).reset_index()

    tracked = bench[bench['_has_click']].copy() if '_has_click' in bench.columns else bench
    tracked_agg = tracked.groupby(group_cols).agg(
        트래킹_발송건=('발송건', 'sum'),
        클릭수=('클릭수', lambda x: x.fillna(0).sum()),
    ).reset_index()

    agg = vol.merge(tracked_agg, on=group_cols, how='left').fillna(0)
    agg['CTR'] = np.where(agg['트래킹_발송건'] > 0, agg['클릭수'] / agg['트래킹_발송건'] * 100, 0)
    agg['CPC'] = np.where(agg['클릭수'] > 0, agg['총광고비'] / agg['클릭수'], 0)
    agg['발송건'] = agg['총발송건']
    agg['광고비'] = agg['총광고비']

    # 브랜드 컬럼명 통일
    if brand_col == '_브랜드' and '_브랜드' in agg.columns:
        agg = agg.rename(columns={'_브랜드': '브랜드'})

    return agg.sort_values('광고비', ascending=False)


# ══════════════════════════════════════════════
# 2. 자사 데이터 요약
# ══════════════════════════════════════════════

def _my_summary(df: pd.DataFrame) -> dict:
    """자사 LMS 데이터의 핵심 지표 딕셔너리"""
    total_cost = df['집행금액'].sum()
    total_send = df['발송량'].sum()
    total_click = df['클릭수'].sum()
    ctr = calc_ctr_scalar(total_click, total_send)
    cpc = total_cost / total_click if total_click > 0 else 0
    cpm = total_cost / total_send * 1000 if total_send > 0 else 0

    # 매체별 집계
    media = df.groupby('매체명').agg(
        {'집행금액': 'sum', '발송량': 'sum', '클릭수': 'sum'}
    ).reset_index()
    media['CTR'] = calc_ctr(media)
    media['CPC'] = np.where(media['클릭수'] > 0, media['집행금액'] / media['클릭수'], 0)

    return dict(
        cost=total_cost, send=total_send, click=total_click,
        ctr=ctr, cpc=cpc, cpm=cpm,
        media=media,
        n_media=len(media),
        months=sorted(df['년월'].unique()) if '년월' in df.columns else [],
    )



# ══════════════════════════════════════════════
# 3. 공통 CSS + 유틸리티 (Toss-style)
# ══════════════════════════════════════════════

_CB_CSS = """<style>
.cb-summary{display:flex;gap:12px;margin:12px 0 20px 0}
.cb-summary-item{flex:1;background:#FFF;border-radius:14px;padding:18px 16px;box-shadow:0 1px 4px rgba(0,0,0,.03);text-align:center;transition:all .2s ease}
.cb-summary-item:hover{box-shadow:0 4px 16px rgba(0,0,0,.06);transform:translateY(-1px)}
.cb-s-label{font-size:.75rem;color:#8B95A1;font-weight:600;margin-bottom:4px}
.cb-s-value{font-weight:800;font-size:1.2rem;color:#191F28}
.cb-s-sub{font-size:.72rem;color:#8B95A1;margin-top:2px}
.cb-rank-card{background:#FFF;border-radius:14px;padding:18px;box-shadow:0 1px 4px rgba(0,0,0,.03);text-align:center;transition:all .2s ease}
.cb-rank-card:hover{box-shadow:0 4px 16px rgba(0,0,0,.06);transform:translateY(-2px)}
.cb-rank-medal{font-size:1.5rem}
.cb-rank-name{font-weight:700;font-size:.85rem;color:#191F28;margin-top:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cb-rank-score{font-weight:800;font-size:1.3rem;color:#F7931D;margin-top:4px}
.cb-rank-bar{margin-top:8px;background:#F2F4F6;border-radius:4px;height:6px;overflow:hidden}
.cb-rank-bar-fill{height:6px;border-radius:4px;transition:width .5s ease}
.cb-rank-detail{font-size:.73rem;color:#8B95A1;margin-top:8px}
.cb-vs-card{display:grid;grid-template-columns:1fr 40px 1fr;align-items:center;padding:14px 20px;margin-bottom:6px;background:#FFF;border-radius:14px;box-shadow:0 1px 4px rgba(0,0,0,.03);transition:all .2s ease}
.cb-vs-card:hover{box-shadow:0 4px 16px rgba(0,0,0,.06);transform:translateY(-1px)}
.cb-vs-name{font-weight:700;font-size:.82rem;color:#191F28;margin-bottom:2px}
.cb-vs-label{font-size:.70rem;color:#8B95A1;font-weight:600}
.cb-vs-val{font-weight:800;font-size:1rem}
.cb-vs-mid{text-align:center;font-size:.82rem;font-weight:700;color:#8B95A1}
.cb-vs-left{text-align:left}
.cb-vs-right{text-align:right}
.cb-media-card{display:grid;grid-template-columns:200px 1fr 90px;grid-template-rows:auto auto;align-items:center;gap:0 16px;padding:16px 20px;margin-bottom:6px;background:#FFF;border-radius:14px;box-shadow:0 1px 4px rgba(0,0,0,.03);transition:all .2s ease}
.cb-media-card:hover{box-shadow:0 4px 16px rgba(0,0,0,.06);transform:translateY(-1px)}
.cb-m-left{display:flex;align-items:center;gap:10px}
.cb-m-dot{width:10px;height:10px;border-radius:3px;flex-shrink:0}
.cb-m-name{font-weight:700;font-size:.88rem;color:#191F28;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cb-m-center{width:100%}
.cb-m-bar-bg{background:#F2F4F6;border-radius:4px;height:8px;width:100%;overflow:hidden}
.cb-m-bar-fill{height:8px;border-radius:4px;transition:width .5s cubic-bezier(.4,0,.2,1)}
.cb-m-right{text-align:right}
.cb-m-pct{font-weight:800;font-size:1rem;color:#191F28}
.cb-m-sub{font-size:.78rem;color:#8B95A1;margin-top:1px}
.cb-m-metrics{grid-column:1/-1;display:flex;gap:16px;margin-top:6px;padding-top:8px;border-top:1px solid #F2F4F6}
.cb-m-metrics span{font-size:.75rem;color:#8B95A1;font-weight:500}
.cb-tag{display:inline-block;font-size:.68rem;padding:1px 8px;border-radius:10px;margin-left:4px;font-weight:600;vertical-align:middle}
.cb-tag-up{background:#F0FFF4;color:#00C853}
.cb-tag-down{background:#FFF5F5;color:#F44336}
.cb-tag-neutral{background:#F2F4F6;color:#8B95A1}
.cb-adv-card{background:#FFF;border-radius:14px;padding:16px 20px;margin-bottom:6px;box-shadow:0 1px 4px rgba(0,0,0,.03);transition:all .2s ease;display:grid;grid-template-columns:1fr auto;align-items:center;gap:8px 16px}
.cb-adv-card:hover{box-shadow:0 4px 16px rgba(0,0,0,.06);transform:translateY(-1px)}
.cb-adv-name{font-weight:700;font-size:.88rem;color:#191F28}
.cb-adv-brand{font-size:.75rem;color:#8B95A1;font-weight:500;margin-top:1px}
.cb-adv-ind{font-size:.68rem;color:#F7931D;font-weight:600;background:rgba(247,147,29,.08);padding:1px 8px;border-radius:10px;display:inline-block;margin-top:4px}
.cb-adv-stats{text-align:right}
.cb-adv-ctr{font-weight:800;font-size:1.1rem;color:#191F28}
.cb-adv-detail{font-size:.72rem;color:#8B95A1;margin-top:2px}
</style>"""

_compact = compact_num


def _cb_color_map(items: list[str]) -> dict[str, str]:
    """항목 -> 색상 매핑 (순서 기준, 모든 차트에서 공유)"""
    return {name: CHART_COLORS[i % len(CHART_COLORS)]
            for i, name in enumerate(items)}


def _cb_donut(labels: list, values: list, center_text: str,
              color_map: dict, height: int = 320) -> go.Figure:
    """Modern 도넛 차트"""
    colors = [color_map.get(l, CHART_COLORS[0]) for l in labels]
    fig = go.Figure(data=[go.Pie(
        labels=labels, values=values,
        hole=0.68,
        marker=dict(colors=colors, line=dict(color='#FFFFFF', width=3)),
        textinfo='percent',
        textposition='inside',
        textfont=dict(size=11, family='Pretendard, sans-serif', color='#FFFFFF'),
        hovertemplate='%{label}<br>%{value:,.0f} (%{percent})<extra></extra>',
    )])
    fig.update_layout(
        title=dict(text=""), height=height, showlegend=True,
        legend=dict(
            orientation='h', yanchor='top', y=-0.05, xanchor='center', x=0.5,
            font=dict(size=11, family='Pretendard, sans-serif', color='#4E5968'),
        ),
        margin=dict(t=40, l=10, r=30, b=30),
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        annotations=[dict(
            text=f'<b>{center_text}</b>', x=0.5, y=0.5,
            font_size=15, font_color='#4E5968',
            font_family='Pretendard, sans-serif', showarrow=False,
        )],
    )
    return fig


# ══════════════════════════════════════════════
# 4. TAB 1 — 업종 벤치마크 비교
# ══════════════════════════════════════════════

def _render_industry_benchmark(df: pd.DataFrame, bench: pd.DataFrame, selected_industry: str = "전체"):
    """자사 CTR/CPC를 업종별 벤치마크와 비교 — Toss-style UI"""
    my = _my_summary(df)
    ind = _industry_agg(bench)

    if ind.empty:
        st.info("벤치마크 데이터에 업종 정보가 없습니다.")
        return

    # ── 선택 업종 필터 ──
    _is_specific = bool(selected_industry) and selected_industry != "전체"

    # ── 벤치마크 평균 계산 (선택 업종이 있으면 해당 업종만, 없으면 전체) ──
    if _is_specific:
        bench_scope = bench[bench['분야'] == selected_industry] if '분야' in bench.columns else bench
    else:
        bench_scope = bench

    tracked = bench_scope[bench_scope['_has_click']] if '_has_click' in bench_scope.columns else bench_scope
    tracked_clicks = tracked['클릭수'].fillna(0).sum()
    tracked_sends = tracked['발송건'].sum()
    bench_ctr = calc_ctr_scalar(tracked_clicks, tracked_sends)
    bench_cpc = tracked['광고비'].sum() / tracked_clicks if tracked_clicks > 0 else 0

    ctr_diff = my['ctr'] - bench_ctr
    cpc_diff = my['cpc'] - bench_cpc

    # ── Summary KPI Row ──
    ctr_tag = 'cb-tag-up' if ctr_diff >= 0 else 'cb-tag-down'
    cpc_tag = 'cb-tag-up' if cpc_diff <= 0 else 'cb-tag-down'  # CPC: 낮을수록 좋음
    _avg_label_suffix = f" ({selected_industry})" if _is_specific else ""
    _avg_sub = (
        f"{selected_industry} 업종 · {int(tracked_sends):,}건 집행"
        if _is_specific else f"{len(ind)}개 업종 기준"
    )
    _cpc_sub = (
        f"{selected_industry} 업종 · {_compact(bench_scope['광고비'].sum(), '원')} 집행"
        if _is_specific else f"{_compact(bench['광고비'].sum(), '원')} 집행"
    )
    st.markdown(
        f'<div class="cb-summary">'
        f'  <div class="cb-summary-item">'
        f'    <div class="cb-s-label">자사 CTR</div>'
        f'    <div class="cb-s-value">{my["ctr"]:.2f}%</div>'
        f'    <div class="cb-s-sub"><span class="{ctr_tag} cb-tag">'
        f'      {abs(ctr_diff):.2f}%p {"높음" if ctr_diff >= 0 else "낮음"}</span></div>'
        f'  </div>'
        f'  <div class="cb-summary-item">'
        f'    <div class="cb-s-label">업종 평균 CTR{_avg_label_suffix}</div>'
        f'    <div class="cb-s-value">{bench_ctr:.2f}%</div>'
        f'    <div class="cb-s-sub">{_avg_sub}</div>'
        f'  </div>'
        f'  <div class="cb-summary-item">'
        f'    <div class="cb-s-label">자사 CPC</div>'
        f'    <div class="cb-s-value">{my["cpc"]:,.0f}원</div>'
        f'    <div class="cb-s-sub"><span class="{cpc_tag} cb-tag">'
        f'      {abs(cpc_diff):,.0f}원 {"높음" if cpc_diff > 0 else "낮음"}</span></div>'
        f'  </div>'
        f'  <div class="cb-summary-item">'
        f'    <div class="cb-s-label">업종 평균 CPC{_avg_label_suffix}</div>'
        f'    <div class="cb-s-value">{bench_cpc:,.0f}원</div>'
        f'    <div class="cb-s-sub">{_cpc_sub}</div>'
        f'  </div>'
        f'</div>', unsafe_allow_html=True)

    # ── TOP 업종 랭킹 카드 ──
    ind_ranked = ind.sort_values('CTR', ascending=False).reset_index(drop=True)
    top_ind = ind_ranked.head(4)
    if len(top_ind) >= 2:
        medals = ["🥇", "🥈", "🥉", "4️⃣"]
        max_ctr = top_ind['CTR'].max()
        cmap = _cb_color_map(top_ind['분야'].tolist())
        rank_cols = st.columns(len(top_ind), gap="medium")
        for idx, (_, row) in enumerate(top_ind.iterrows()):
            color = cmap.get(row['분야'], CHART_COLORS[0])
            bar_pct = row['CTR'] / max_ctr * 100 if max_ctr > 0 else 0
            _is_sel = _is_specific and row['분야'] == selected_industry
            _card_style = (
                ' style="border:2px solid #FF6B6B;box-shadow:0 4px 12px rgba(255,107,107,0.15)"'
                if _is_sel else ''
            )
            with rank_cols[idx]:
                st.markdown(
                    f'<div class="cb-rank-card"{_card_style}>'
                    f'  <div class="cb-rank-medal">{medals[idx]}</div>'
                    f'  <div class="cb-rank-name">{row["분야"]}{" ⭐" if _is_sel else ""}</div>'
                    f'  <div class="cb-rank-score">{row["CTR"]:.2f}%</div>'
                    f'  <div class="cb-rank-bar">'
                    f'    <div class="cb-rank-bar-fill" style="background:{color};width:{bar_pct:.0f}%"></div>'
                    f'  </div>'
                    f'  <div class="cb-rank-detail">'
                    f'    {row["캠페인수"]:.0f}건 · CPC {row["CPC"]:,.0f}원'
                    f'  </div>'
                    f'</div>', unsafe_allow_html=True)

    # 선택 업종이 TOP 4에 없으면 별도 순위 안내
    if _is_specific:
        _sel_row = ind_ranked[ind_ranked['분야'] == selected_industry]
        if not _sel_row.empty:
            _sel_idx = int(_sel_row.index[0])
            _total = len(ind_ranked)
            if _sel_idx >= 4:  # TOP 4 밖
                _r = _sel_row.iloc[0]
                st.markdown(
                    f'<div style="margin-top:8px;padding:10px 14px;background:rgba(255,107,107,0.08);'
                    f'border-left:3px solid #FF6B6B;border-radius:6px;font-size:0.85rem;color:#191F28;">'
                    f'선택 업종 <b>{selected_industry}</b> — CTR 순위 <b>{_sel_idx + 1}위</b>/{_total}개 업종 · '
                    f'CTR <b>{_r["CTR"]:.2f}%</b> · CPC <b>{_r["CPC"]:,.0f}원</b> · 캠페인 <b>{_r["캠페인수"]:.0f}건</b>'
                    f'</div>', unsafe_allow_html=True,
                )

    st.markdown("<br>", unsafe_allow_html=True)

    # ── 업종별 CTR + CPC: 도넛 + 바 나란히 ──
    col_donut, col_bar = st.columns([1, 1.3], gap="medium")

    with col_donut:
        donut_ind = ind.head(8)
        cmap_d = _cb_color_map(donut_ind['분야'].tolist())
        fig_donut = _cb_donut(
            donut_ind['분야'].tolist(),
            donut_ind['발송건'].tolist(),
            "업종별 발송 비중",
            cmap_d,
        )
        st.plotly_chart(fig_donut, key="ind_donut", width="stretch")

    with col_bar:
        ind_sorted = ind.sort_values('CTR', ascending=True)
        cmap_bar = _cb_color_map(ind_sorted['분야'].tolist())

        fig = go.Figure()
        for i, (_, row) in enumerate(ind_sorted.iterrows()):
            color = cmap_bar.get(row['분야'], CHART_COLORS[i % len(CHART_COLORS)])
            _sel_row = _is_specific and row['분야'] == selected_industry
            _line_w = 5 if _sel_row else 3
            _mk_size = 20 if _sel_row else 14
            _mk_border = '#FF6B6B' if _sel_row else 'white'
            _mk_border_w = 3 if _sel_row else 2
            _text_color = '#FF6B6B' if _sel_row else '#4E5968'
            _text_wt = 'font-weight:bold;' if _sel_row else ''
            fig.add_trace(go.Scatter(
                x=[0, row['CTR']], y=[row['분야'], row['분야']],
                mode='lines', line=dict(color=color, width=_line_w),
                showlegend=False, hoverinfo='skip',
            ))
            fig.add_trace(go.Scatter(
                x=[row['CTR']], y=[row['분야']],
                mode='markers+text',
                marker=dict(size=_mk_size, color=color, line=dict(width=_mk_border_w, color=_mk_border)),
                text=[f"<b>{row['CTR']:.2f}%</b>" if _sel_row else f"{row['CTR']:.2f}%"],
                textposition='middle right',
                textfont=dict(size=12 if _sel_row else 11, color=_text_color, family='Pretendard, sans-serif'),
                showlegend=False,
                cliponaxis=False,
                hovertemplate=f"<b>{row['분야']}</b><br>CTR: {row['CTR']:.2f}%<br>캠페인: {row['캠페인수']:.0f}건<extra></extra>",
            ))
        fig.add_vline(x=my['ctr'], line_dash="dot", line_color='#FF6B6B', line_width=1.5,
            annotation_text=f"자사 {my['ctr']:.2f}%", annotation_position="top",
            annotation_font=dict(color='#FF6B6B', size=10, family='Pretendard, sans-serif'))
        _ctr_xmax = float(max(ind_sorted['CTR'].max(), my['ctr'])) * 1.22
        fig.update_layout(
            height=max(300, len(ind_sorted) * 48 + 60),
            title=dict(text='업종별 CTR 비교', font=dict(size=14, family='Pretendard, sans-serif', color='#191F28')),
            xaxis=dict(showgrid=True, gridcolor='#F2F4F6', zeroline=False, showline=False,
                       tickfont=dict(size=11, family='Pretendard, sans-serif'),
                       range=[0, _ctr_xmax]),
            yaxis=dict(showgrid=False, showline=False,
                       tickfont=dict(size=11, family='Pretendard, sans-serif')),
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(t=40, l=100, r=120, b=20),
        )
        st.plotly_chart(fig, key="ind_ctr_bar", width="stretch")

    # ── CPC 비교 롤리팝 ──
    ind_cpc = ind[ind['CPC'] > 0].sort_values('CPC', ascending=True)
    if not ind_cpc.empty:
        cmap_cpc = _cb_color_map(ind_cpc['분야'].tolist())
        fig2 = go.Figure()
        for i, (_, row) in enumerate(ind_cpc.iterrows()):
            color = cmap_cpc.get(row['분야'], CHART_COLORS[i % len(CHART_COLORS)])
            fig2.add_trace(go.Scatter(
                x=[0, row['CPC']], y=[row['분야'], row['분야']],
                mode='lines', line=dict(color=color, width=3),
                showlegend=False, hoverinfo='skip',
            ))
            fig2.add_trace(go.Scatter(
                x=[row['CPC']], y=[row['분야']],
                mode='markers+text',
                marker=dict(size=14, color=color, line=dict(width=2, color='white')),
                text=[f"{row['CPC']:,.0f}원"], textposition='middle right',
                textfont=dict(size=11, color='#4E5968', family='Pretendard, sans-serif'),
                showlegend=False,
                cliponaxis=False,
                hovertemplate=f"<b>{row['분야']}</b><br>CPC: {row['CPC']:,.0f}원<br>캠페인: {row['캠페인수']:.0f}건<extra></extra>",
            ))
        if my['cpc'] > 0:
            fig2.add_vline(x=my['cpc'], line_dash="dot", line_color='#FF6B6B', line_width=1.5,
                annotation_text=f"자사 {my['cpc']:,.0f}원", annotation_position="top",
                annotation_font=dict(color='#FF6B6B', size=10, family='Pretendard, sans-serif'))
        _cpc_xmax = float(max(ind_cpc['CPC'].max(), my['cpc'] if my['cpc'] > 0 else 0)) * 1.25
        fig2.update_layout(
            height=max(300, len(ind_cpc) * 48 + 60),
            title=dict(text='업종별 CPC 비교 (낮을수록 효율적)', font=dict(size=14, family='Pretendard, sans-serif', color='#191F28')),
            xaxis=dict(showgrid=True, gridcolor='#F2F4F6', zeroline=False, showline=False,
                       tickfont=dict(size=11, family='Pretendard, sans-serif'),
                       range=[0, _cpc_xmax]),
            yaxis=dict(showgrid=False, showline=False,
                       tickfont=dict(size=11, family='Pretendard, sans-serif')),
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(t=40, l=100, r=140, b=20),
        )
        st.plotly_chart(fig2, key="ind_cpc_bar", width="stretch")

    # ── 포지셔닝 스캐터 (업종별 컬러 버블) ──
    ind_scatter = ind[(ind['CTR'] > 0) & (ind['CPC'] > 0)].copy()
    if len(ind_scatter) >= 2:
        fig3 = go.Figure()
        cmap_s = _cb_color_map(ind_scatter['분야'].tolist())
        for _, row in ind_scatter.iterrows():
            c = cmap_s.get(row['분야'], CHART_COLORS[0])
            bubble_size = float(np.clip(row['캠페인수'] / max(ind_scatter['캠페인수'].max(), 1) * 50, 14, 55))
            fig3.add_trace(go.Scatter(
                x=[row['CTR']], y=[row['CPC']],
                mode='markers+text',
                text=[row['분야']], textposition='top center',
                textfont=dict(size=10, color=c, family='Pretendard, sans-serif', weight=700),
                marker=dict(size=bubble_size, color=c, opacity=0.7,
                            line=dict(width=2, color='white')),
                showlegend=False,
                hovertemplate=f"<b>{row['분야']}</b><br>CTR: {row['CTR']:.2f}%<br>CPC: {row['CPC']:,.0f}원<br>캠페인: {row['캠페인수']:.0f}건<extra></extra>",
            ))
        fig3.add_trace(go.Scatter(
            x=[my['ctr']], y=[my['cpc']],
            mode='markers+text', text=['★ 자사'], textposition='bottom center',
            textfont=dict(color='#FF6B6B', size=12, family='Pretendard, sans-serif'),
            marker=dict(size=20, color='#FF6B6B', symbol='star', line=dict(width=2, color='white')),
            showlegend=False,
            hovertemplate='<b>자사</b><br>CTR: %{x:.2f}%<br>CPC: %{y:,.0f}원<extra></extra>',
        ))
        fig3.update_layout(
            height=450,
            title=dict(text='업종 포지셔닝 맵 (CTR x CPC)', font=dict(size=14, family='Pretendard, sans-serif', color='#191F28')),
            xaxis=dict(title="CTR (%) → 높을수록 효율적", showgrid=True, gridcolor='#F2F4F6',
                       zeroline=False, showline=False, tickfont=dict(size=11, family='Pretendard, sans-serif')),
            yaxis=dict(title="CPC (원) → 낮을수록 효율적", showgrid=True, gridcolor='#F2F4F6',
                       zeroline=False, showline=False, tickfont=dict(size=11, family='Pretendard, sans-serif')),
            showlegend=False,
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(t=40, l=60, r=30, b=30),
        )
        st.plotly_chart(fig3, key="ind_scatter", width="stretch")

    # ── 인사이트 카드 ──
    # BUG A fix: 캠페인수 >= 20 업종만 비교 대상. 자격 미달 시 insight 생략.
    ind_reliable = ind[ind['캠페인수'] >= 20]
    above = ind_reliable[ind_reliable['CTR'] < my['ctr']]
    below = ind_reliable[ind_reliable['CTR'] >= my['ctr']]

    if not above.empty:
        names = ', '.join(above['분야'].tolist())
        st.markdown(alert_card_html(
            "success",
            f"자사 CTR이 {len(above)}개 업종 평균을 상회",
            f"<b>{names}</b> 업종 대비 높은 CTR을 기록 중입니다 (업종별 캠페인 20건 이상). "
            f"해당 업종 광고주 영업 시 자사 CTR <b>{my['ctr']:.2f}%</b>를 경쟁력 지표로 어필할 수 있습니다.",
        ), unsafe_allow_html=True)
    if not below.empty:
        top_i = below.sort_values('CTR', ascending=False).iloc[0]
        st.markdown(alert_card_html(
            "warning",
            f"최고 효율 업종: {top_i['분야']} (CTR {top_i['CTR']:.2f}%)",
            f"<b>{top_i['분야']}</b> 업종의 평균 CTR이 자사보다 높습니다. "
            f"해당 업종의 크리에이티브 트렌드와 매체 믹스를 벤치마킹하여 자사 캠페인에 적용을 검토하세요.",
        ), unsafe_allow_html=True)

    ind_with_cpc = ind[ind['CPC'] > 0]
    if not ind_with_cpc.empty and my['cpc'] > 0:
        cheapest = ind_with_cpc.loc[ind_with_cpc['CPC'].idxmin()]
        if cheapest['CPC'] < my['cpc']:
            saving = my['cpc'] - cheapest['CPC']
            # BUG E fix: 업종 간 CPC 비교는 apples-to-oranges. 참고용 정보로만 제시.
            st.markdown(alert_card_html(
                "info",
                f"CPC 최적 업종: {cheapest['분야']} ({cheapest['CPC']:,.0f}원)",
                f"<b>{cheapest['분야']}</b>은 CPC가 낮은 경향 (업종 특성일 수 있음). "
                f"자사 대비 <b>{saving:,.0f}원</b> 저렴하나 업종별 특성 차이로 직접 비교는 제한적입니다.",
            ), unsafe_allow_html=True)


# ══════════════════════════════════════════════
# 5. TAB 2 — 매체별 벤치마크
# ══════════════════════════════════════════════

def _render_media_benchmark(df: pd.DataFrame, bench: pd.DataFrame, selected_industry: str):
    """자사 매체 성과 vs 벤치마크 매체 평균 — Toss-style UI"""
    my = _my_summary(df)

    if selected_industry and selected_industry != "전체":
        bench_filtered = bench[bench['분야'] == selected_industry]
    else:
        bench_filtered = bench

    bench_media = _media_agg(bench_filtered)
    my_media = my['media']

    if bench_media.empty:
        st.info("선택한 업종에 해당하는 벤치마크 데이터가 없습니다.")
        return

    ind_label = f" ({selected_industry})" if selected_industry and selected_industry != "전체" else ""

    # ── 자사-벤치마크 매체 매칭 ──
    # BUG B fix: 벤치마크 캠페인수 >= 20 AND 자사 발송량 >= MIN_SENDS_FOR_CTR_CLAIM 행만 포함
    matched = []
    for _, my_row in my_media.iterrows():
        my_name = my_row['매체명']
        for _, b_row in bench_media.iterrows():
            b_name = b_row['매체']
            if my_name in b_name or b_name in my_name or my_name == b_name:
                if b_row['캠페인수'] < 20 or my_row['발송량'] < MIN_SENDS_FOR_CTR_CLAIM:
                    break
                matched.append({
                    '매체': my_name,
                    '자사_CTR': my_row['CTR'],
                    '벤치마크_CTR': b_row['CTR'],
                    '자사_CPC': my_row['CPC'],
                    '벤치마크_CPC': b_row['CPC'],
                    '자사_발송': my_row['발송량'],
                    '벤치마크_발송': b_row['발송건'],
                    '벤치마크_캠페인수': b_row['캠페인수'],
                })
                break

    if matched:
        match_df = pd.DataFrame(matched)

        # ── Summary: 매칭 결과 개요 ──
        # BUG G fix: 매칭 매체가 3개 미만이면 summary 생략
        if len(match_df) < 3:
            st.info("매칭된 매체가 부족합니다 (벤치마크 비교 생략)")
        else:
            outperform_n = len(match_df[match_df['자사_CTR'] > match_df['벤치마크_CTR']])
            avg_gap = (match_df['자사_CTR'] - match_df['벤치마크_CTR']).mean()
            # BUG F fix: 3-way label — CTR_CHANGE_THRESHOLD 기준으로 양호/평균/개선
            if avg_gap >= CTR_CHANGE_THRESHOLD:
                gap_tag = 'cb-tag-up'
                gap_label = "양호"
            elif avg_gap <= -CTR_CHANGE_THRESHOLD:
                gap_tag = 'cb-tag-down'
                gap_label = "개선 필요"
            else:
                gap_tag = 'cb-tag-neutral'
                gap_label = "업종 평균 수준"
            st.markdown(
                f'<div class="cb-summary">'
                f'  <div class="cb-summary-item">'
                f'    <div class="cb-s-label">매칭 매체</div>'
                f'    <div class="cb-s-value">{len(match_df)}개</div>'
                f'    <div class="cb-s-sub">자사 {len(my_media)}개 중</div>'
                f'  </div>'
                f'  <div class="cb-summary-item">'
                f'    <div class="cb-s-label">벤치마크 상회</div>'
                f'    <div class="cb-s-value">{outperform_n}개</div>'
                f'    <div class="cb-s-sub">{outperform_n}/{len(match_df)} 매체</div>'
                f'  </div>'
                f'  <div class="cb-summary-item">'
                f'    <div class="cb-s-label">평균 CTR 격차</div>'
                f'    <div class="cb-s-value"><span class="{gap_tag} cb-tag">{avg_gap:+.2f}%p</span></div>'
                f'    <div class="cb-s-sub">{gap_label}</div>'
                f'  </div>'
                f'</div>', unsafe_allow_html=True)

        # ── CTR 비교: 덤벨 차트 ──
        fig = go.Figure()
        for _, row in match_df.iterrows():
            my_val = row['자사_CTR']
            bench_val = row['벤치마크_CTR']
            fig.add_trace(go.Scatter(
                x=[my_val, bench_val], y=[row['매체'], row['매체']],
                mode='lines', line=dict(color='#E5E8EB', width=3),
                showlegend=False, hoverinfo='skip',
            ))
        fig.add_trace(go.Scatter(
            x=match_df['자사_CTR'], y=match_df['매체'],
            mode='markers', name='자사',
            marker=dict(size=13, color=BRAND_PRIMARY, line=dict(width=2, color='white')),
            hovertemplate='<b>자사</b> %{y}<br>CTR: %{x:.2f}%<extra></extra>',
        ))
        fig.add_trace(go.Scatter(
            x=match_df['벤치마크_CTR'], y=match_df['매체'],
            mode='markers', name='벤치마크',
            marker=dict(size=13, color=CHART_COLORS[1], symbol='diamond',
                        line=dict(width=2, color='white')),
            hovertemplate='<b>벤치마크</b> %{y}<br>CTR: %{x:.2f}%<extra></extra>',
        ))
        fig.update_layout(
            height=max(300, len(match_df) * 50 + 80),
            title=dict(text=f'매체별 CTR 비교{ind_label}', font=dict(size=14, family='Pretendard, sans-serif', color='#191F28')),
            legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1,
                        font=dict(size=11, family='Pretendard, sans-serif')),
            xaxis=dict(showgrid=True, gridcolor='#F2F4F6', zeroline=False, showline=False,
                       tickfont=dict(size=11, family='Pretendard, sans-serif')),
            yaxis=dict(showgrid=False, showline=False,
                       tickfont=dict(size=11, family='Pretendard, sans-serif')),
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(t=40, l=120, r=30, b=30),
        )
        st.plotly_chart(fig, key="media_ctr_group", width="stretch")

        # ── CPC 비교: 덤벨 차트 ──
        cpc_match = match_df[(match_df['자사_CPC'] > 0) & (match_df['벤치마크_CPC'] > 0)]
        if not cpc_match.empty:
            fig2 = go.Figure()
            for _, row in cpc_match.iterrows():
                my_val = row['자사_CPC']
                bench_val = row['벤치마크_CPC']
                fig2.add_trace(go.Scatter(
                    x=[my_val, bench_val], y=[row['매체'], row['매체']],
                    mode='lines', line=dict(color='#E5E8EB', width=3),
                    showlegend=False, hoverinfo='skip',
                ))
            fig2.add_trace(go.Scatter(
                x=cpc_match['자사_CPC'], y=cpc_match['매체'],
                mode='markers', name='자사',
                marker=dict(size=13, color=BRAND_PRIMARY, line=dict(width=2, color='white')),
                hovertemplate='<b>자사</b> %{y}<br>CPC: %{x:,.0f}원<extra></extra>',
            ))
            fig2.add_trace(go.Scatter(
                x=cpc_match['벤치마크_CPC'], y=cpc_match['매체'],
                mode='markers', name='벤치마크',
                marker=dict(size=13, color=CHART_COLORS[2], symbol='diamond',
                            line=dict(width=2, color='white')),
                hovertemplate='<b>벤치마크</b> %{y}<br>CPC: %{x:,.0f}원<extra></extra>',
            ))
            fig2.update_layout(
                height=max(300, len(cpc_match) * 50 + 80),
                title=dict(text=f'매체별 CPC 비교{ind_label}', font=dict(size=14, family='Pretendard, sans-serif', color='#191F28')),
                legend=dict(orientation='h', yanchor='bottom', y=1.02, xanchor='right', x=1,
                            font=dict(size=11, family='Pretendard, sans-serif')),
                xaxis=dict(showgrid=True, gridcolor='#F2F4F6', zeroline=False, showline=False,
                           tickfont=dict(size=11, family='Pretendard, sans-serif')),
                yaxis=dict(showgrid=False, showline=False,
                           tickfont=dict(size=11, family='Pretendard, sans-serif')),
                plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                margin=dict(t=40, l=120, r=30, b=30),
            )
            st.plotly_chart(fig2, key="media_cpc_group", width="stretch")

        # ── 매체별 VS 비교 카드 (테이블 대체) ──
        st.markdown(
            '<div style="font-size:.82rem;color:#8B95A1;margin:16px 0 8px 0;font-weight:600;">'
            '매체별 상세 비교</div>', unsafe_allow_html=True)

        for _, row in match_df.iterrows():
            ctr_gap = row['자사_CTR'] - row['벤치마크_CTR']
            cpc_gap = row['자사_CPC'] - row['벤치마크_CPC']
            ctr_color = COLOR_SUCCESS if ctr_gap >= 0 else COLOR_DANGER
            cpc_color = COLOR_SUCCESS if cpc_gap <= 0 else COLOR_DANGER

            st.markdown(
                f'<div class="cb-vs-card">'
                f'  <div class="cb-vs-left">'
                f'    <div class="cb-vs-label">자사</div>'
                f'    <div class="cb-vs-val">{row["자사_CTR"]:.2f}%</div>'
                f'    <div class="cb-vs-label" style="margin-top:4px">'
                f'      CPC {row["자사_CPC"]:,.0f}원</div>'
                f'  </div>'
                f'  <div class="cb-vs-mid">'
                f'    <div class="cb-vs-name">{row["매체"]}</div>'
                f'    <div style="font-size:.70rem;color:{ctr_color};font-weight:700;margin-top:2px">'
                f'      CTR {ctr_gap:+.2f}%p</div>'
                f'  </div>'
                f'  <div class="cb-vs-right">'
                f'    <div class="cb-vs-label">벤치마크</div>'
                f'    <div class="cb-vs-val">{row["벤치마크_CTR"]:.2f}%</div>'
                f'    <div class="cb-vs-label" style="margin-top:4px">'
                f'      CPC {row["벤치마크_CPC"]:,.0f}원 · {row["벤치마크_캠페인수"]:.0f}건</div>'
                f'  </div>'
                f'</div>', unsafe_allow_html=True)

        # ── 인사이트 ──
        # BUG B fix: abs(gap) >= CTR_CHANGE_THRESHOLD 이상인 경우만 알림 카드 생성, 발송량 표시
        outperform = match_df[
            (match_df['자사_CTR'] - match_df['벤치마크_CTR']) >= CTR_CHANGE_THRESHOLD
        ]
        underperform = match_df[
            (match_df['벤치마크_CTR'] - match_df['자사_CTR']) >= CTR_CHANGE_THRESHOLD
        ]

        if not outperform.empty:
            names = ', '.join(outperform['매체'].tolist())
            best = outperform.loc[(outperform['자사_CTR'] - outperform['벤치마크_CTR']).idxmax()]
            st.markdown(alert_card_html(
                "success",
                f"{len(outperform)}개 매체에서 업종 평균 상회",
                f"<b>{names}</b>에서 벤치마크 대비 높은 CTR을 기록 중입니다. "
                f"특히 <b>{best['매체']}</b>는 업종 평균 대비 "
                f"<b>+{best['자사_CTR'] - best['벤치마크_CTR']:.2f}%p</b> 우위 "
                f"(자사 {best['자사_발송']:,.0f}건 · 벤치마크 {best['벤치마크_발송']:,.0f}건) "
                f"— 예산 확대를 검토하세요.",
            ), unsafe_allow_html=True)

        if not underperform.empty:
            worst = underperform.loc[(underperform['벤치마크_CTR'] - underperform['자사_CTR']).idxmax()]
            st.markdown(alert_card_html(
                "warning",
                f"개선 필요 매체: {worst['매체']}",
                f"<b>{worst['매체']}</b>의 자사 CTR은 <b>{worst['자사_CTR']:.2f}%</b>이나, "
                f"업종 벤치마크는 <b>{worst['벤치마크_CTR']:.2f}%</b>입니다 "
                f"(자사 {worst['자사_발송']:,.0f}건 · 벤치마크 {worst['벤치마크_발송']:,.0f}건). "
                f"크리에이티브 A/B 테스트 또는 타겟 세그먼트 변경을 권장합니다.",
            ), unsafe_allow_html=True)
    else:
        st.info("자사 매체와 벤치마크 매체 간 매칭되는 채널이 없습니다. 매체명을 확인해 주세요.")

    # ── 벤치마크 전체 매체 랭킹 ──
    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander(f"📊 벤치마크 전체 매체 CTR 랭킹{ind_label}"):
        top_media = bench_media.sort_values('CTR', ascending=False).head(15)
        cmap_top = _cb_color_map(top_media['매체'].tolist())
        max_ctr_m = top_media['CTR'].max() if not top_media.empty else 1

        # 매체 랭킹을 카드로 표시 (상위 4개 메달)
        top4 = top_media.head(min(4, len(top_media)))
        if len(top4) >= 2:
            medals = ["🥇", "🥈", "🥉", "4️⃣"]
            rcols = st.columns(len(top4), gap="medium")
            for idx, (_, row) in enumerate(top4.iterrows()):
                color = cmap_top.get(row['매체'], CHART_COLORS[0])
                bar_pct = row['CTR'] / max_ctr_m * 100 if max_ctr_m > 0 else 0
                with rcols[idx]:
                    st.markdown(
                        f'<div class="cb-rank-card">'
                        f'  <div class="cb-rank-medal">{medals[idx]}</div>'
                        f'  <div class="cb-rank-name">{row["매체"]}</div>'
                        f'  <div class="cb-rank-score">{row["CTR"]:.2f}%</div>'
                        f'  <div class="cb-rank-bar">'
                        f'    <div class="cb-rank-bar-fill" style="background:{color};width:{bar_pct:.0f}%"></div>'
                        f'  </div>'
                        f'  <div class="cb-rank-detail">'
                        f'    {row["캠페인수"]:.0f}건 · {_compact(row["발송건"], "건")}'
                        f'  </div>'
                        f'</div>', unsafe_allow_html=True)

        # 나머지는 수평 롤리팝 차트
        if len(top_media) > 4:
            rest = top_media.iloc[4:].sort_values('CTR', ascending=True)
            fig3 = go.Figure()
            for i, (_, row) in enumerate(rest.iterrows()):
                color = cmap_top.get(row['매체'], CHART_COLORS[i % len(CHART_COLORS)])
                fig3.add_trace(go.Scatter(
                    x=[0, row['CTR']], y=[row['매체'], row['매체']],
                    mode='lines', line=dict(color=color, width=3),
                    showlegend=False, hoverinfo='skip',
                ))
                fig3.add_trace(go.Scatter(
                    x=[row['CTR']], y=[row['매체']],
                    mode='markers+text',
                    marker=dict(size=14, color=color, line=dict(width=2, color='white')),
                    text=[f"{row['CTR']:.2f}%"], textposition='middle right',
                    textfont=dict(size=10, color='#4E5968', family='Pretendard, sans-serif'),
                    showlegend=False,
                    cliponaxis=False,
                    hovertemplate=f"<b>{row['매체']}</b><br>CTR: {row['CTR']:.2f}%<br>캠페인: {row['캠페인수']:.0f}건<extra></extra>",
                ))
            _rest_xmax = float(rest['CTR'].max()) * 1.22 if len(rest) else 1.0
            fig3.update_layout(
                height=max(300, len(rest) * 48 + 60),
                title=dict(text='나머지 매체 CTR', font=dict(size=14, family='Pretendard, sans-serif', color='#191F28')),
                xaxis=dict(showgrid=True, gridcolor='#F2F4F6', zeroline=False, showline=False,
                           tickfont=dict(size=11, family='Pretendard, sans-serif'),
                           range=[0, _rest_xmax]),
                yaxis=dict(showgrid=False, showline=False,
                           tickfont=dict(size=11, family='Pretendard, sans-serif')),
                plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                margin=dict(t=40, l=100, r=120, b=20),
            )
            st.plotly_chart(fig3, key="media_rest_bar", width="stretch")


# ══════════════════════════════════════════════
# 6. TAB 3 — 광고주 비교 분석
# ══════════════════════════════════════════════

def _render_advertiser_comparison(df: pd.DataFrame, bench: pd.DataFrame, selected_industry: str):
    """업종 내 광고주 성과 비교 + 자사 포지셔닝 — Toss-style UI"""
    my = _my_summary(df)

    if selected_industry and selected_industry != "전체":
        bench_filtered = bench[bench['분야'] == selected_industry]
    else:
        bench_filtered = bench

    adv = _advertiser_agg(bench_filtered)
    if adv.empty:
        st.info("선택한 업종에 해당하는 광고주 데이터가 없습니다.")
        return

    ind_label = f" ({selected_industry})" if selected_industry and selected_industry != "전체" else ""

    # 발송량 최소 기준
    # BUG C fix: 표본 부족 시 top 10 폴백 제거 - 명시적 경고 후 early return
    adv_valid = adv[adv['발송건'] >= 10000].copy()
    if adv_valid.empty:
        st.warning("표본이 부족해 상세 포지셔닝 분석을 생략합니다.")
        return

    has_brand = '브랜드' in adv_valid.columns
    if has_brand:
        adv_valid['_label'] = adv_valid.apply(
            lambda r: f"{r['광고주']} ({r['브랜드']})" if r['광고주'] != r['브랜드'] else r['광고주'],
            axis=1,
        )
    else:
        adv_valid['_label'] = adv_valid['광고주']

    # ── 자사 포지션 계산 ──
    better_than = adv_valid[adv_valid['CTR'] < my['ctr']]
    pct_rank = len(better_than) / len(adv_valid) * 100 if len(adv_valid) > 0 else 0
    top_pct = 100 - pct_rank

    # ── Summary KPI Row ──
    avg_ctr_adv = adv_valid['CTR'].mean() if len(adv_valid) > 0 else 0
    ctr_vs_avg = my['ctr'] - avg_ctr_adv
    pos_cls = 'cb-tag-up' if top_pct <= 30 else ('cb-tag-neutral' if top_pct <= 60 else 'cb-tag-down')
    st.markdown(
        f'<div class="cb-summary">'
        f'  <div class="cb-summary-item">'
        f'    <div class="cb-s-label">자사 포지션</div>'
        f'    <div class="cb-s-value"><span class="{pos_cls} cb-tag">상위 {top_pct:.0f}%</span></div>'
        f'    <div class="cb-s-sub">{len(adv_valid)}개 광고주 중</div>'
        f'  </div>'
        f'  <div class="cb-summary-item">'
        f'    <div class="cb-s-label">자사 CTR</div>'
        f'    <div class="cb-s-value">{my["ctr"]:.2f}%</div>'
        f'    <div class="cb-s-sub">평균 대비 {ctr_vs_avg:+.2f}%p</div>'
        f'  </div>'
        f'  <div class="cb-summary-item">'
        f'    <div class="cb-s-label">광고주 평균 CTR</div>'
        f'    <div class="cb-s-value">{avg_ctr_adv:.2f}%</div>'
        f'    <div class="cb-s-sub">{_compact(adv_valid["발송건"].sum(), "건")} 기준</div>'
        f'  </div>'
        f'  <div class="cb-summary-item">'
        f'    <div class="cb-s-label">벤치마크 광고주</div>'
        f'    <div class="cb-s-value">{len(adv_valid)}개</div>'
        f'    <div class="cb-s-sub">{len(adv_valid["분야"].unique())}개 업종</div>'
        f'  </div>'
        f'</div>', unsafe_allow_html=True)

    # ── TOP 광고주 랭킹 카드 ──
    top_adv = adv_valid.sort_values('CTR', ascending=False).head(15)
    top4 = top_adv.head(min(4, len(top_adv)))
    if len(top4) >= 2:
        medals = ["🥇", "🥈", "🥉", "4️⃣"]
        max_ctr_a = top4['CTR'].max()
        cmap_a = _cb_color_map(top4['_label'].tolist())
        rcols = st.columns(len(top4), gap="medium")
        for idx, (_, row) in enumerate(top4.iterrows()):
            color = cmap_a.get(row['_label'], CHART_COLORS[0])
            bar_pct = row['CTR'] / max_ctr_a * 100 if max_ctr_a > 0 else 0
            with rcols[idx]:
                brand_line = f'<div style="font-size:.70rem;color:#8B95A1;margin-top:2px">{row["브랜드"]}</div>' if has_brand and row.get('브랜드', '') != row['광고주'] else ''
                st.markdown(
                    f'<div class="cb-rank-card">'
                    f'  <div class="cb-rank-medal">{medals[idx]}</div>'
                    f'  <div class="cb-rank-name">{row["광고주"]}</div>'
                    f'  {brand_line}'
                    f'  <div class="cb-rank-score">{row["CTR"]:.2f}%</div>'
                    f'  <div class="cb-rank-bar">'
                    f'    <div class="cb-rank-bar-fill" style="background:{color};width:{bar_pct:.0f}%"></div>'
                    f'  </div>'
                    f'  <div class="cb-rank-detail">'
                    f'    {row["분야"]} · {_compact(row["광고비"], "원")}'
                    f'  </div>'
                    f'</div>', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── 광고주 CTR 랭킹 그라디언트 바 차트 + 자사 기준선 ──
    top_adv_sorted = top_adv.sort_values('CTR', ascending=True)
    max_ctr = top_adv_sorted['CTR'].max()
    min_ctr = top_adv_sorted['CTR'].min()
    gradient_colors = []
    for ctr_val in top_adv_sorted['CTR']:
        ratio = (ctr_val - min_ctr) / (max_ctr - min_ctr) if max_ctr > min_ctr else 0.5
        r = int(59 + (247 - 59) * ratio)
        g = int(130 + (121 - 130) * ratio)
        b = int(246 + (29 - 246) * ratio)
        gradient_colors.append(f'rgb({r},{g},{b})')

    _xmax_ctr = float(top_adv_sorted['CTR'].max()) * 1.25 if len(top_adv_sorted) else 1.0
    fig = go.Figure()
    fig.add_trace(go.Bar(
        y=top_adv_sorted['_label'], x=top_adv_sorted['CTR'],
        orientation='h', marker_color=gradient_colors,
        marker=dict(cornerradius=8, line=dict(width=0)),
        text=top_adv_sorted.apply(lambda r: f"{r['CTR']:.2f}%", axis=1),
        textposition='outside', cliponaxis=False,
        textfont=dict(size=10, color='#4E5968', family='Pretendard, sans-serif'),
        hovertemplate='<b>%{y}</b><br>CTR: %{x:.2f}%<extra></extra>',
    ))
    fig.add_vline(x=my['ctr'], line_dash="dot", line_color='#FF6B6B', line_width=1.5,
        annotation_text=f"자사 {my['ctr']:.2f}%", annotation_position="top",
        annotation_font=dict(color='#FF6B6B', size=10, family='Pretendard, sans-serif'))
    fig.update_layout(
        height=max(400, len(top_adv_sorted) * 35 + 100),
        title=dict(text=f'광고주 CTR 랭킹{ind_label}', font=dict(size=14, family='Pretendard, sans-serif', color='#191F28')),
        xaxis=dict(showgrid=True, gridcolor='#F2F4F6', zeroline=False, showline=False,
                   tickfont=dict(size=11, family='Pretendard, sans-serif'),
                   range=[0, _xmax_ctr]),
        yaxis=dict(showgrid=False, showline=False,
                   tickfont=dict(size=11, family='Pretendard, sans-serif')),
        plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
        margin=dict(t=40, l=180, r=120, b=30),
    )
    st.plotly_chart(fig, key="adv_ctr_bar", width="stretch")

    # ── 광고주 포지셔닝 버블 차트 (업종별 컬러) ──
    adv_bubble = adv_valid[adv_valid['CTR'] > 0].head(20)
    if len(adv_bubble) >= 3:
        fig2 = go.Figure()
        cmap_bubble = _cb_color_map(adv_bubble['분야'].unique().tolist())
        for _, row in adv_bubble.iterrows():
            c = cmap_bubble.get(row['분야'], CHART_COLORS[0])
            bubble_size = float(np.clip(row['발송건'] / max(adv_bubble['발송건'].max(), 1) * 50, 14, 55))
            label = row['_label'] if '_label' in row.index else row['광고주']
            fig2.add_trace(go.Scatter(
                x=[row['CTR']], y=[row['광고비']],
                mode='markers+text',
                text=[label], textposition='top center',
                textfont=dict(size=10, color=c, family='Pretendard, sans-serif', weight=700),
                marker=dict(size=bubble_size, color=c, opacity=0.7,
                            line=dict(width=2, color='white')),
                showlegend=False,
                hovertemplate=f"<b>{label}</b><br>CTR: {row['CTR']:.2f}%<br>광고비: {row['광고비']:,.0f}원<br>발송: {row['발송건']:,.0f}건<br>업종: {row['분야']}<extra></extra>",
            ))
        fig2.add_trace(go.Scatter(
            x=[my['ctr']], y=[my['cost']],
            mode='markers+text', text=['★ 자사'], textposition='bottom center',
            textfont=dict(color='#FF6B6B', size=12, family='Pretendard, sans-serif'),
            marker=dict(size=20, color='#FF6B6B', symbol='star', line=dict(width=2, color='white')),
            showlegend=False,
            hovertemplate='<b>자사</b><br>CTR: %{x:.2f}%<br>광고비: %{y:,.0f}원<extra></extra>',
        ))
        fig2.update_layout(
            height=480,
            title=dict(text=f'광고주 포지셔닝 맵 (CTR x 광고비){ind_label}', font=dict(size=14, family='Pretendard, sans-serif', color='#191F28')),
            xaxis=dict(title="CTR (%) → 높을수록 효율적", showgrid=True, gridcolor='#F2F4F6',
                       zeroline=False, showline=False, tickfont=dict(size=11, family='Pretendard, sans-serif')),
            yaxis=dict(title="광고비 (원)", showgrid=True, gridcolor='#F2F4F6',
                       zeroline=False, showline=False, tickfont=dict(size=11, family='Pretendard, sans-serif')),
            showlegend=False,
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(t=40, l=60, r=30, b=30),
        )
        st.plotly_chart(fig2, key="adv_bubble", width="stretch")

    # ── 광고주 상세 카드 (테이블 대체) ──
    st.markdown(
        '<div style="font-size:.82rem;color:#8B95A1;margin:16px 0 8px 0;font-weight:600;">'
        f'광고주 상세 비교 ({min(20, len(adv_valid))}개)</div>',
        unsafe_allow_html=True)

    display_adv = adv_valid.head(20)
    for _, row in display_adv.iterrows():
        brand_html = f'<div class="cb-adv-brand">{row["브랜드"]}</div>' if has_brand and row.get('브랜드', '') != row['광고주'] else ''
        cpc_str = f"CPC {row['CPC']:,.0f}원" if row['CPC'] > 0 else "CPC -"
        st.markdown(
            f'<div class="cb-adv-card">'
            f'  <div>'
            f'    <div class="cb-adv-name">{row["광고주"]}</div>'
            f'    {brand_html}'
            f'    <span class="cb-adv-ind">{row["분야"]}</span>'
            f'  </div>'
            f'  <div class="cb-adv-stats">'
            f'    <div class="cb-adv-ctr">{row["CTR"]:.2f}%</div>'
            f'    <div class="cb-adv-detail">{cpc_str} · {_compact(row["광고비"], "원")} · {row["캠페인수"]:.0f}건</div>'
            f'  </div>'
            f'</div>', unsafe_allow_html=True)

    # ── 인사이트 ──
    # BUG D fix: 경쟁사 수 >= MIN_PEERS_FOR_RANKING 확보 시에만 "핵심 셀링 포인트" 주장 허용
    if not better_than.empty:
        if len(adv_valid) >= MIN_PEERS_FOR_RANKING:
            st.markdown(alert_card_html(
                "success",
                f"광고주 {len(adv_valid)}개 중 상위 {top_pct:.0f}%",
                f"자사 CTR <b>{my['ctr']:.2f}%</b>는 벤치마크 내 <b>{len(better_than)}개 광고주</b>보다 높습니다. "
                f"경쟁 프레젠테이션 시 '상위 {top_pct:.0f}% 성과'를 핵심 셀링 포인트로 활용하세요.",
            ), unsafe_allow_html=True)
        else:
            st.markdown(alert_card_html(
                "info",
                f"참고 포지셔닝 (표본 부족, {len(adv_valid)}개 광고주)",
                f"자사 CTR <b>{my['ctr']:.2f}%</b>가 벤치마크 내 <b>{len(better_than)}개 광고주</b>보다 높으나, "
                f"경쟁사 표본이 {MIN_PEERS_FOR_RANKING}개 미만이라 순위 산정 신뢰도는 제한적입니다.",
            ), unsafe_allow_html=True)

    worse_than = adv_valid[adv_valid['CTR'] >= my['ctr']]
    if not worse_than.empty:
        top3 = worse_than.sort_values('CTR', ascending=False).head(3)
        names = ', '.join(top3['광고주'].tolist())
        st.markdown(alert_card_html(
            "info",
            "벤치마킹 대상 광고주",
            f"<b>{names}</b>이(가) 자사보다 높은 CTR을 기록 중입니다. "
            f"이들의 매체 믹스, 발송 시간대, 크리에이티브 패턴을 분석하여 자사 전략에 반영하세요.",
        ), unsafe_allow_html=True)


# ══════════════════════════════════════════════
# 7. TAB 4 — 수동 경쟁사 + API (기존 기능 유지)
# ══════════════════════════════════════════════

def _fetch_semrush(api_key: str, domain: str) -> pd.DataFrame | None:
    """SEMrush API에서 도메인 키워드 데이터 조회"""
    try:
        import requests
        url = "https://api.semrush.com/"
        params = {
            "type": "domain_organic",
            "key": api_key,
            "domain": domain,
            "database": "kr",
            "display_limit": 20,
            "export_columns": "Ph,Po,Nq,Cp,Tr",
        }
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        from io import StringIO
        result = pd.read_csv(StringIO(resp.text), sep=";")
        result.columns = ['키워드', '순위', '월간검색량', 'CPC($)', '트래픽비중(%)']
        return result
    except Exception:
        return None


def _fetch_ahrefs(api_key: str, domain: str) -> pd.DataFrame | None:
    """Ahrefs API에서 도메인 오가닉 키워드 조회"""
    try:
        import requests
        url = "https://apiv2.ahrefs.com"
        params = {
            "token": api_key,
            "from": "organic_keywords",
            "target": domain,
            "mode": "domain",
            "country": "kr",
            "limit": 20,
            "output": "json",
        }
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json().get("keywords", [])
        if not data:
            return None
        result = pd.DataFrame(data)
        result = result.rename(columns={
            "keyword": "키워드", "position": "순위",
            "volume": "월간검색량", "cpc": "CPC($)", "traffic": "트래픽비중(%)",
        })
        return result[['키워드', '순위', '월간검색량', 'CPC($)', '트래픽비중(%)']]
    except Exception:
        return None


def _render_manual_section(df: pd.DataFrame):
    """수동 경쟁사 입력 + LMS 교차 분석 + API 연동"""
    my_name = st.session_state.get("company", "자사")

    # 수동 입력 UI
    st.markdown("**경쟁사 정보 입력**")
    n_comp = st.slider("경쟁사 수", min_value=1, max_value=10, value=3, key="comp_n_manual")
    competitors = []
    cols = st.columns(min(n_comp, 5))
    for i in range(n_comp):
        col_idx = i % len(cols)
        with cols[col_idx]:
            name = st.text_input(f"경쟁사 {i+1}", key=f"comp_name_{i}", placeholder="이름")
            domain = st.text_input(f"도메인 {i+1}", key=f"comp_domain_{i}", placeholder="example.co.kr")
            if name:
                competitors.append({"이름": name, "도메인": domain or ""})

    keywords = st.text_input(
        "핵심 키워드 입력 (쉼표로 구분)",
        placeholder="예: LMS, 문자마케팅, 대량문자, SMS마케팅",
        key="comp_keywords",
    )
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []

    # API 연동
    with st.sidebar:
        st.markdown('<div class="sidebar-label">경쟁사 분석 API (선택)</div>', unsafe_allow_html=True)
        api_provider = st.selectbox("API 선택", ["사용 안 함", "SEMrush", "Ahrefs"], key="comp_api")
        api_key = ""
        if api_provider != "사용 안 함":
            api_key = st.text_input(f"{api_provider} API Key", type="password", key="comp_api_key")

    if api_key and api_provider != "사용 안 함":
        st.markdown("<br>", unsafe_allow_html=True)
        st.markdown(section_header("API 실시간 데이터"), unsafe_allow_html=True)
        my_domain = st.text_input("자사 도메인", placeholder="예: braincube.co.kr", key="my_domain")
        if my_domain:
            with st.spinner(f"{api_provider}에서 데이터 조회 중..."):
                fetch_fn = _fetch_semrush if api_provider == "SEMrush" else _fetch_ahrefs
                my_data = fetch_fn(api_key, my_domain)
                if my_data is not None and not my_data.empty:
                    _render_api_results(my_data, my_domain)
                else:
                    st.markdown(alert_card_html(
                        "warning", "데이터 조회 실패",
                        f"{api_provider} API에서 <b>{my_domain}</b>의 데이터를 가져올 수 없습니다.",
                    ), unsafe_allow_html=True)
        for comp in competitors:
            if comp['도메인']:
                with st.spinner(f"{comp['도메인']} 조회 중..."):
                    fetch_fn = _fetch_semrush if api_provider == "SEMrush" else _fetch_ahrefs
                    comp_data = fetch_fn(api_key, comp['도메인'])
                    if comp_data is not None and not comp_data.empty:
                        _render_api_results(comp_data, f"{comp['이름']} ({comp['도메인']})")

    # LMS 교차 분석 카드
    if competitors or kw_list:
        st.markdown("<br>", unsafe_allow_html=True)
        cards = _generate_competitive_analysis(my_name, competitors, kw_list, df)
        for card in cards:
            st.markdown(card, unsafe_allow_html=True)
    elif not api_key:
        st.markdown(alert_card_html(
            "info", "경쟁사 분석 시작하기",
            "위에서 경쟁사 이름과 핵심 키워드를 입력하면 자사 LMS 데이터 기반 경쟁 분석이 시작됩니다.<br>"
            "SEMrush/Ahrefs API를 연동하면 실시간 키워드 순위 데이터도 확인할 수 있습니다.",
        ), unsafe_allow_html=True)


def _render_api_results(api_data: pd.DataFrame, domain: str):
    """API 조회 결과를 테이블 + 차트로 표시"""
    st.markdown(f"<b>{domain}</b> 상위 키워드", unsafe_allow_html=True)
    top10 = api_data.head(10).sort_values('순위', ascending=True)
    _xmax_vol = float(top10['월간검색량'].max()) * 1.25 if len(top10) else 1.0
    fig = go.Figure(data=[go.Bar(
        y=top10['키워드'], x=top10['월간검색량'],
        orientation='h',
        marker_color=CHART_COLORS[1],
        text=top10['순위'].apply(lambda x: f"{x}위"),
        textposition='outside',
        cliponaxis=False,
    )])
    layout = {**PLOTLY_LAYOUT}
    layout.update(
        height=350,
        title={**PLOTLY_LAYOUT['title'], 'text': f"{domain} 오가닉 키워드 TOP 10"},
        xaxis_title="", yaxis_title="",
        xaxis=dict(range=[0, _xmax_vol]),
        yaxis=dict(autorange="reversed"),
        margin=dict(t=48, r=100, b=20, l=20),
    )
    fig.update_layout(**layout)
    st.plotly_chart(fig, width='stretch')
    st.dataframe(
        api_data.head(20).style.format({
            '월간검색량': '{:,.0f}', 'CPC($)': '{:.2f}', '트래픽비중(%)': '{:.1f}',
        }),
        width='stretch', hide_index=True,
    )


def _generate_competitive_analysis(
    my_name: str, competitors: list[dict], keywords: list[str], df: pd.DataFrame,
) -> list[str]:
    """LMS 데이터 교차 분석 → 경쟁 인사이트 카드"""
    from modules.config import CTR_CHANGE_THRESHOLD
    cards = []

    total_cost = df['집행금액'].sum()
    total_send = df['발송량'].sum()
    total_click = df['클릭수'].sum()
    ctr = calc_ctr_scalar(total_click, total_send)
    cpc = total_cost / total_click if total_click > 0 else 0
    months = sorted(df['년월'].unique()) if '년월' in df.columns else []
    n_months = max(len(months), 1)
    monthly_budget = total_cost / n_months

    # 매체 랭킹
    media_stats = df.groupby('매체명').agg({'집행금액': 'sum', '발송량': 'sum', '클릭수': 'sum'}).reset_index()
    media_stats['CTR'] = calc_ctr(media_stats)
    media_stats = media_stats.sort_values('CTR', ascending=False)
    top_media = media_stats.iloc[0] if not media_stats.empty else None

    # 추세
    ctr_trend = "stable"
    if len(months) >= 2:
        cur_m = df[df['년월'] == months[-1]]
        prv_m = df[df['년월'] == months[-2]]
        diff = (calc_ctr_scalar(cur_m['클릭수'].sum(), cur_m['발송량'].sum())
                - calc_ctr_scalar(prv_m['클릭수'].sum(), prv_m['발송량'].sum()))
        if diff > CTR_CHANGE_THRESHOLD:
            ctr_trend = "improving"
        elif diff < -CTR_CHANGE_THRESHOLD:
            ctr_trend = "declining"

    # 요일
    day_stats = aggregate_by_weekday(df) if '짧은_요일' in df.columns else pd.DataFrame()
    valid_d = day_stats[day_stats['CTR'] > 0] if not day_stats.empty else pd.DataFrame()
    best_day = valid_d.loc[valid_d['CTR'].idxmax()] if not valid_d.empty else None

    comp_names = ', '.join(c['이름'] for c in competitors) if competitors else "경쟁사"
    trend_msg = {"improving": "상승세", "declining": "하락세", "stable": "안정적"}

    # 자사 현황
    cards.append(alert_card_html(
        "info",
        f"{my_name} 현재 경쟁력 진단",
        f"총 <b>{len(media_stats)}개 매체</b>에서 월 평균 <b>{monthly_budget:,.0f}원</b> 집행 중<br>"
        f"평균 CTR <b>{ctr:.2f}%</b> · CPC <b>{cpc:,.0f}원</b> · 추세: <b>{trend_msg[ctr_trend]}</b>",
    ))

    # 키워드별 전략
    if keywords:
        top_m_name = top_media['매체명'] if top_media is not None else "주력 매체"
        top_m_ctr = top_media['CTR'] if top_media is not None else 0
        for kw in keywords[:5]:
            budget_kw = monthly_budget / max(len(keywords), 1)
            exp_clicks = budget_kw / cpc if cpc > 0 else 0
            day_str = f"<br>• 최적 발송일: <b>{best_day['짧은_요일']}요일</b> (CTR {best_day['CTR']:.2f}%)" if best_day is not None else ""
            cards.append(alert_card_html(
                "warning",
                f"키워드 전략 · \"{kw}\"",
                f"<b>{comp_names}</b> 대비 '<b>{kw}</b>' 점유율 확보 전략:<br>"
                f"• 예산 배분: 월 <b>{budget_kw:,.0f}원</b> → 약 <b>{exp_clicks:,.0f}건</b> 유입 예상<br>"
                f"• 추천 매체: <b>{top_m_name}</b> (CTR {top_m_ctr:.2f}%){day_str}",
            ))

    # 경쟁사별 대응
    if competitors:
        for comp in competitors:
            if ctr_trend == "improving":
                strategy = (
                    f"자사 CTR 상승세 → <b>{comp['이름']}</b> 고객에게 성과 데이터 기반 영업 유효<br>"
                    f"• 평균 CTR <b>{ctr:.2f}%</b> · 월 <b>{total_click / n_months:,.0f}건</b> 실 클릭수 어필"
                )
            elif ctr_trend == "declining":
                strategy = (
                    f"CTR 하락세 반전 후 <b>{comp['이름']}</b>와 경쟁 권장<br>"
                    f"• CPC <b>{cpc:,.0f}원</b> — 크리에이티브 개선 우선"
                    + (f"<br>• {best_day['짧은_요일']}요일 집중 발송으로 효율 극대화" if best_day is not None else "")
                )
            else:
                inc = monthly_budget * 0.2
                extra = inc / cpc if cpc > 0 else 0
                strategy = (
                    f"안정적 성과 → <b>{comp['이름']}</b> 대비 볼륨 확대로 차별화<br>"
                    f"• 예산 20% 증액(<b>+{inc:,.0f}원</b>) 시 <b>+{extra:,.0f}건</b> 추가 유입<br>"
                    f"• 경쟁사 약점 요일/시간대에 발송 집중"
                )
            cards.append(alert_card_html("danger", f"경쟁 대응 · {comp['이름']}", strategy))

    return cards


# ══════════════════════════════════════════════
# 8. 메인 렌더
# ══════════════════════════════════════════════

def render(df: pd.DataFrame):
    # ── 커스텀 CSS 주입 ──
    st.markdown(_CB_CSS, unsafe_allow_html=True)

    st.markdown(section_header(
        "경쟁사 · 업종 벤치마크 분석",
        "Firebase 실시간 데이터 기반 업종/매체/광고주 벤치마크와 수동 경쟁사 분석을 제공합니다.",
    ), unsafe_allow_html=True)

    from modules.data_processing import render_ref_period_selector
    ref_df, period_label = render_ref_period_selector(df, "comp")

    bench_raw = _load_benchmark()

    # 데이터 소스 뱃지 표시
    st.caption(f"데이터 소스: <b>{get_data_source()}</b>", unsafe_allow_html=True)

    if bench_raw is not None and not bench_raw.empty:
        # ── 필터 UI (업종 + 광고상품) ──
        fc1, fc2 = st.columns(2)
        with fc1:
            # 빈 문자열·공백만 있는 값 제외 (라디오 버튼이 이름 없이 뜨는 것 방지)
            industries = (
                sorted([
                    ind for ind in bench_raw['분야'].dropna().unique().tolist()
                    if str(ind).strip()
                ])
                if '분야' in bench_raw.columns else []
            )
            from modules.ui_helpers import help_text as _help_text
            selected_industry = "전체"
            if industries:
                industry_opts = ["전체"] + industries
                selected_industry = st.radio(
                    "🏢 벤치마크 업종 필터",
                    industry_opts,
                    index=0,
                    horizontal=True,
                    key="comp_industry_filter",
                    help=_help_text("industry"),
                )
        with fc2:
            # 빈 문자열·공백만 있는 값 제외
            products = (
                sorted([
                    p for p in bench_raw['광고상품'].dropna().unique().tolist()
                    if str(p).strip()
                ])
                if '광고상품' in bench_raw.columns else []
            )
            selected_product = "LMS"
            if products:
                product_opts = ["LMS", "전체"] + [p for p in products if p != "LMS"]
                selected_product = st.radio(
                    "📦 광고상품 필터",
                    product_opts,
                    index=0,
                    horizontal=True,
                    key="comp_product_filter",
                    help=_help_text("product"),
                )

        # 광고상품 필터 적용
        bench = _filter_product(bench_raw, selected_product)
        if bench.empty:
            st.warning(f"'{selected_product}' 상품에 해당하는 벤치마크 데이터가 없습니다.")
            _render_manual_section(ref_df)
            st.divider()
            return

        # 클릭 트래킹 통계 안내
        total_rows = len(bench)
        tracked_rows = bench['_has_click'].sum() if '_has_click' in bench.columns else total_rows
        if tracked_rows < total_rows:
            st.caption(
                f"📊 {selected_product} 데이터 {total_rows}건 중 "
                f"<b>클릭 트래킹 {int(tracked_rows)}건</b> ({tracked_rows / total_rows * 100:.0f}%) 기준으로 CTR을 산출합니다.",
                unsafe_allow_html=True,
            )

        tab1, tab2, tab3 = st.tabs([
            "📊 업종 벤치마크", "🏆 광고주 비교", "🔍 수동 경쟁사 · API"
        ])

        with tab1:
            _render_industry_benchmark(ref_df, bench, selected_industry)
        with tab2:
            _render_advertiser_comparison(ref_df, bench, selected_industry)
        with tab3:
            _render_manual_section(ref_df)
    else:
        # 데이터 없음 → 기존 수동 모드만
        st.markdown(alert_card_html(
            "info", "Firebase 연동 필요",
            "Firebase Firestore가 연결되면 업종/매체/광고주 벤치마크 비교가 활성화됩니다.<br>"
            "현재는 수동 경쟁사 분석 모드로 동작합니다.",
        ), unsafe_allow_html=True)
        _render_manual_section(ref_df)

    st.divider()
