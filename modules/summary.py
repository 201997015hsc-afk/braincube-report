"""
월간 요약 (Executive Summary) — Linear/Notion 프리뷰 스타일
데이터 로드 직후 가장 먼저 표시되는 페이지.
"""
import streamlit as st
import pandas as pd

from modules.config import (
    kpi_card_html, alert_card_html,
    BRAND_PRIMARY, COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER,
    COLOR_BORDER, COLOR_BORDER_SUBTLE, COLOR_CARD,
    COLOR_SUCCESS, COLOR_DANGER, COLOR_BG,
    MIN_SENDS_FOR_CTR_CLAIM, CTR_CHANGE_THRESHOLD, compact_num,
)
from modules.data_processing import calc_ctr_scalar, media_month_stats
from modules.insights import detect_summary, render_insights
from modules.ui_helpers import render_page_header, render_empty_state, esc_html_safe


def _format_delta(current: float, previous: float, suffix: str = "") -> tuple[str, str]:
    """변화량 문자열과 방향(up/down) 반환"""
    if previous == 0 or abs(previous) < 1e-6:
        return "전월 데이터 부족", ""
    change = ((current - previous) / previous) * 100
    direction = "up" if change > 0 else "down" if change < 0 else ""
    if abs(change) > 999:
        sign = "+" if change > 0 else "-"
        return f"{sign}999%+{suffix}", direction
    return f"{change:+.1f}%{suffix}", direction


def _render_media_table(merged: pd.DataFrame, prev_month: str, latest_month: str):
    """매체별 당월 vs 전월 비교 테이블 — Linear 스타일"""
    if merged.empty:
        st.markdown(
            f'<div style="color:{COLOR_TEXT_SEC};font-size:0.85rem;'
            f'padding:12px 0;">매체별 데이터가 없습니다.</div>',
            unsafe_allow_html=True,
        )
        return

    # 당월 발송량 내림차순
    merged = merged.sort_values('발송량_당월', ascending=False).copy()
    merged['CTR변화'] = merged['CTR_당월'] - merged['CTR_전월']

    rows_html = []
    for _, row in merged.iterrows():
        ctr_ch = float(row['CTR변화'])
        if abs(ctr_ch) < 0.005:
            ctr_color = COLOR_TEXT_SEC
            ctr_symbol = "—"
        elif ctr_ch > 0:
            ctr_color = COLOR_SUCCESS
            ctr_symbol = "↑"
        else:
            ctr_color = COLOR_DANGER
            ctr_symbol = "↓"

        rows_html.append(
            f'<tr>'
            f'  <td style="font-weight:500;color:{COLOR_TEXT};">{esc_html_safe(row["매체명"])}</td>'
            f'  <td style="text-align:right;color:{COLOR_TEXT};">{int(row["발송량_당월"]):,}</td>'
            f'  <td style="text-align:right;color:{COLOR_TEXT};">{int(row["클릭수_당월"]):,}</td>'
            f'  <td style="text-align:right;font-weight:600;color:{COLOR_TEXT};">{row["CTR_당월"]:.2f}%</td>'
            f'  <td style="text-align:right;color:{ctr_color};font-weight:500;">'
            f'    {ctr_symbol} {abs(ctr_ch):.2f}%p'
            f'  </td>'
            f'</tr>'
        )

    table_html = (
        f'<table style="width:100%;border-collapse:collapse;font-size:0.82rem;">'
        f'  <thead>'
        f'    <tr style="background:{COLOR_BG};border-bottom:1px solid {COLOR_BORDER};">'
        f'      <th style="padding:8px 12px;text-align:left;font-weight:500;'
        f'color:{COLOR_TEXT_SEC};font-size:0.72rem;">매체</th>'
        f'      <th style="padding:8px 12px;text-align:right;font-weight:500;'
        f'color:{COLOR_TEXT_SEC};font-size:0.72rem;">발송량</th>'
        f'      <th style="padding:8px 12px;text-align:right;font-weight:500;'
        f'color:{COLOR_TEXT_SEC};font-size:0.72rem;">클릭수</th>'
        f'      <th style="padding:8px 12px;text-align:right;font-weight:500;'
        f'color:{COLOR_TEXT_SEC};font-size:0.72rem;">CTR</th>'
        f'      <th style="padding:8px 12px;text-align:right;font-weight:500;'
        f'color:{COLOR_TEXT_SEC};font-size:0.72rem;">전월비</th>'
        f'    </tr>'
        f'  </thead>'
        f'  <tbody>{"".join(rows_html)}</tbody>'
        f'</table>'
    )
    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER};border-radius:10px;overflow:hidden;">'
        f'{table_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_highlight_card(kind: str, title: str, body: str):
    """Best/Watch/Info 공통 카드 — Linear 스타일 (흰 배경 + 얇은 보더 + 좌측 tone 3px 바).
    모든 tone이 동일한 흰 배경을 사용해 페이지 전체 카드 시각언어 통일.
    """
    accent = (
        COLOR_SUCCESS if kind == "best"
        else COLOR_DANGER if kind == "watch"
        else COLOR_TEXT_SEC  # info: 중립 회색
    )
    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {accent};'
        f'border-radius:8px;padding:14px 18px;background:{COLOR_CARD};margin-bottom:8px;">'
        f'<div style="font-size:0.72rem;color:{accent};font-weight:600;'
        f'letter-spacing:0.04em;text-transform:uppercase;margin-bottom:4px;">{esc_html_safe(title.split("·")[0].strip())}</div>'
        f'<div style="font-size:0.92rem;color:{COLOR_TEXT};font-weight:500;margin-bottom:6px;">'
        f'{esc_html_safe(title.split("·", 1)[1].strip()) if "·" in title else ""}</div>'
        f'<div style="font-size:0.82rem;color:{COLOR_TEXT_TER};line-height:1.55;">{body}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def render(df: pd.DataFrame):
    # ── 데이터 준비 ──
    months = sorted(df['년월'].unique())

    if len(months) < 2:
        render_empty_state(
            "월간 비교에 데이터가 부족합니다",
            f"월간 요약은 최소 2개월 이상의 데이터가 필요합니다. "
            f"현재 {len(months)}개월만 업로드되어 있어요.",
            icon="📊",
            actions=[
                ("추가 데이터 업로드", "사이드바 → 데이터 업로드에서 이전 월 데이터를 추가해 주세요"),
                ("Firebase 연동", "자사 브랜드 연동 시 누적 데이터가 자동 로드됩니다"),
            ],
        )
        return

    latest_month, prev_month = months[-1], months[-2]
    cur = df[df['년월'] == latest_month]
    prev = df[df['년월'] == prev_month]

    cur_cost, prev_cost = cur['집행금액'].sum(), prev['집행금액'].sum()
    cur_send, prev_send = cur['발송량'].sum(), prev['발송량'].sum()
    cur_click, prev_click = cur['클릭수'].sum(), prev['클릭수'].sum()
    cur_ctr = calc_ctr_scalar(cur_click, cur_send)
    prev_ctr = calc_ctr_scalar(prev_click, prev_send)

    n_media = cur['매체명'].nunique() if '매체명' in cur.columns else 0

    # ── 페이지 헤더 (Linear 스타일: kicker + 큰 타이틀 + 서브타이틀) ──
    render_page_header(
        title=f"{latest_month} 월간 요약",
        kicker="성과 분석",
        subtitle=(
            f"{prev_month} 대비 주요 지표 변화를 한눈에 확인합니다. "
            f"매체 <b>{n_media}개</b> · 집행 <b>{compact_num(cur_cost, '원')}</b>"
        ),
    )

    # ── 4-KPI 그리드 ──
    d_cost, d_cost_dir = _format_delta(cur_cost, prev_cost)
    d_send, d_send_dir = _format_delta(cur_send, prev_send)
    d_click, d_click_dir = _format_delta(cur_click, prev_click)
    ctr_change = cur_ctr - prev_ctr
    d_ctr = f"{ctr_change:+.2f}%p"
    d_ctr_dir = "up" if ctr_change > 0 else ("down" if ctr_change < 0 else "")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(kpi_card_html("집행금액", f"{cur_cost:,.0f}원",
                                   f"전월비 {d_cost}", d_cost_dir), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card_html("발송량", f"{cur_send:,.0f}건",
                                   f"전월비 {d_send}", d_send_dir), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card_html("클릭수", f"{cur_click:,.0f}회",
                                   f"전월비 {d_click}", d_click_dir), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card_html("CTR", f"{cur_ctr:.2f}%",
                                   f"전월비 {d_ctr}", d_ctr_dir), unsafe_allow_html=True)

    st.markdown('<div class="space-lg"></div>', unsafe_allow_html=True)

    # ── 매체별 비교 + 인사이트 (2-column 그리드) ──
    cur_media = media_month_stats(df, latest_month)
    prev_media = media_month_stats(df, prev_month)
    merged = pd.merge(
        cur_media, prev_media,
        on='매체명', suffixes=('_당월', '_전월'), how='left',
    ).fillna(0)

    col_table, col_insights = st.columns([1.6, 1])

    with col_table:
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:10px;letter-spacing:-0.01em;">매체별 성과</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
            f'{latest_month} 기준 · {prev_month}과 비교</div>',
            unsafe_allow_html=True,
        )
        _render_media_table(merged, prev_month, latest_month)

    with col_insights:
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:10px;letter-spacing:-0.01em;">핵심 인사이트</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
            f'자동 감지된 변화 포인트</div>',
            unsafe_allow_html=True,
        )
        _insights = detect_summary(
            cur_cost, prev_cost, cur_send, prev_send,
            cur_click, prev_click, cur_ctr, prev_ctr, merged,
        )
        if _insights:
            # 좁은 컬럼 안에서는 1열(수직 스택)로 — Streamlit nested columns 제약 회피
            render_insights(_insights, cols=1)
        else:
            # 유의미한 변화가 감지되지 않은 경우 — 간단한 현황 요약으로 빈칸 방지
            _n_tot = len(merged) if not merged.empty else 0
            _n_up = 0
            _top_media = ""
            if not merged.empty:
                # CTR변화 컬럼이 없으면 안전하게 계산
                if 'CTR변화' not in merged.columns \
                        and 'CTR_당월' in merged.columns \
                        and 'CTR_전월' in merged.columns:
                    merged['CTR변화'] = merged['CTR_당월'] - merged['CTR_전월']
                if 'CTR변화' in merged.columns:
                    _n_up = int((merged['CTR변화'] > 0).sum())
                if '클릭수_당월' in merged.columns:
                    _top_rows = merged.nlargest(1, '클릭수_당월')
                    if not _top_rows.empty:
                        _top_media = str(_top_rows.iloc[0].get('매체명', ''))

            st.markdown(
                f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {COLOR_TEXT_SEC};'
                f'border-radius:8px;padding:14px 16px;background:{COLOR_CARD};">'
                f'<div style="font-size:0.82rem;color:{COLOR_TEXT};font-weight:500;margin-bottom:6px;">'
                f'이번 달은 안정적인 흐름입니다.</div>'
                f'<div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};line-height:1.55;">'
                f'• 매체 <b style="color:{COLOR_TEXT};">{_n_tot}개</b> 중 <b style="color:{COLOR_SUCCESS};">{_n_up}개</b>가 CTR 상승<br>'
                + (f'• 클릭 1위 매체: <b style="color:{COLOR_TEXT};">{esc_html_safe(_top_media)}</b><br>' if _top_media else '')
                + '• 유의미한 변화 임계치 미달 — 이상치 없음'
                f'</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown('<div class="space-lg"></div>', unsafe_allow_html=True)

    # ── Best/Watch 하이라이트 ──
    if not merged.empty and len(merged) >= 2:
        merged['CTR변화'] = merged['CTR_당월'] - merged['CTR_전월']
        qualified = merged[
            (merged['발송량_당월'] >= MIN_SENDS_FOR_CTR_CLAIM)
            & (merged['발송량_전월'] >= MIN_SENDS_FOR_CTR_CLAIM)
        ]

        best = qualified.loc[qualified['CTR변화'].idxmax()] if not qualified.empty else None
        worst = qualified.loc[qualified['CTR변화'].idxmin()] if not qualified.empty else None

        best_ok = best is not None and abs(best['CTR변화']) >= CTR_CHANGE_THRESHOLD and best['CTR변화'] > 0
        worst_ok = worst is not None and abs(worst['CTR변화']) >= CTR_CHANGE_THRESHOLD and worst['CTR변화'] < 0

        # 동일 매체가 Best + Watch 동시 조건일 경우(드뭄)
        if best is not None and worst is not None and best['매체명'] == worst['매체명'] and (best_ok or worst_ok):
            _render_highlight_card(
                "info", f"매체 요약 · {best['매체명']}",
                f"CTR {best['CTR_당월']:.2f}% (전월비 <b>{best['CTR변화']:+.2f}%p</b>) · 클릭 {best['클릭수_당월']:,.0f}회",
            )
        elif best_ok or worst_ok:
            col_a, col_b = st.columns(2)
            with col_a:
                if best_ok:
                    _render_highlight_card(
                        "best",
                        f"Best · {best['매체명']}",
                        f"CTR {best['CTR_당월']:.2f}% (전월비 <b>{best['CTR변화']:+.2f}%p</b>) · "
                        f"클릭 {best['클릭수_당월']:,.0f}회",
                    )
                else:
                    _render_highlight_card(
                        "info", "Best · 해당 없음",
                        "유의미하게 상승한 매체가 없습니다.",
                    )
            with col_b:
                if worst_ok:
                    _render_highlight_card(
                        "watch",
                        f"Watch · {worst['매체명']}",
                        f"CTR {worst['CTR_당월']:.2f}% (전월비 <b>{worst['CTR변화']:+.2f}%p</b>) · "
                        f"클릭 {worst['클릭수_당월']:,.0f}회",
                    )
                else:
                    _render_highlight_card(
                        "info", "Watch · 해당 없음",
                        "유의미하게 하락한 매체가 없습니다.",
                    )
        else:
            # Best/Watch 모두 조건 미달 — 카드 스타일 통일 (Linear 중립 인포)
            _render_highlight_card(
                "info", "Best/Watch · 해당 없음",
                f"매체별 변동이 유의미하지 않습니다 "
                f"(양월 발송 ≥ <b>{MIN_SENDS_FOR_CTR_CLAIM:,}건</b> 및 CTR 변화 ≥ "
                f"<b>{CTR_CHANGE_THRESHOLD}%p</b> 기준 미달).",
            )
    elif len(merged) == 1:
        merged['CTR변화'] = merged['CTR_당월'] - merged['CTR_전월']
        row = merged.iloc[0]
        _render_highlight_card(
            "info", f"매체 1개 · {row['매체명']}",
            f"CTR {row['CTR_당월']:.2f}% (전월비 <b>{row['CTR변화']:+.2f}%p</b>) · "
            f"클릭 {row['클릭수_당월']:,.0f}회",
        )
