"""
전월 대비 요약 원페이저 섹션 (Toss-style)
데이터 로드 직후 가장 먼저 표시되는 Executive Summary
"""
import streamlit as st
import pandas as pd

from modules.config import (
    section_header, kpi_card_html, alert_card_html,
    COLOR_DANGER, COLOR_SUCCESS,
    MIN_SENDS_FOR_CTR_CLAIM, CTR_CHANGE_THRESHOLD,
)
from modules.data_processing import calc_ctr, calc_ctr_scalar, media_month_stats
from modules.insights import detect_summary, render_insights


def _format_delta(current: float, previous: float, fmt: str = ",.0f", suffix: str = "") -> tuple[str, str]:
    """변화량 문자열과 방향(up/down) 반환"""
    # BUG S2: 전월 값이 사실상 0에 가까우면 비율이 폭발 → 표시 보정
    if previous == 0 or abs(previous) < 1e-6:
        return "전월 데이터 부족", ""
    change = ((current - previous) / previous) * 100
    direction = "up" if change > 0 else "down" if change < 0 else ""
    # 상대변화 999% 초과 시 캡
    if abs(change) > 999:
        sign = "+" if change > 0 else "-"
        return f"{sign}999%+{suffix}", direction
    return f"{change:+.1f}%{suffix}", direction


def render(df: pd.DataFrame):
    months = sorted(df['년월'].unique())
    if len(months) < 2:
        st.markdown(alert_card_html(
            "info",
            "데이터 부족",
            f"월간 요약에는 최소 <b>2개월</b> 이상의 데이터가 필요합니다. "
            f"현재 <b>{len(months)}개월</b> 데이터가 업로드되었습니다.",
        ), unsafe_allow_html=True)
        return

    latest_month = months[-1]
    prev_month = months[-2]

    cur = df[df['년월'] == latest_month]
    prev = df[df['년월'] == prev_month]

    cur_cost, prev_cost = cur['집행금액'].sum(), prev['집행금액'].sum()
    cur_send, prev_send = cur['발송량'].sum(), prev['발송량'].sum()
    cur_click, prev_click = cur['클릭수'].sum(), prev['클릭수'].sum()
    cur_ctr = calc_ctr_scalar(cur_click, cur_send)
    prev_ctr = calc_ctr_scalar(prev_click, prev_send)

    st.markdown(section_header(
        f"{latest_month} 월간 요약",
        f"{prev_month} 대비 주요 지표 변화를 한눈에 확인합니다.",
    ), unsafe_allow_html=True)

    d_cost, d_cost_dir = _format_delta(cur_cost, prev_cost)
    d_send, d_send_dir = _format_delta(cur_send, prev_send)
    d_click, d_click_dir = _format_delta(cur_click, prev_click)
    ctr_change = cur_ctr - prev_ctr
    d_ctr = f"{ctr_change:+.2f}%p"
    d_ctr_dir = "up" if ctr_change > 0 else ("down" if ctr_change < 0 else "")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(kpi_card_html("집행금액", f"{cur_cost:,.0f}원", f"전월비 {d_cost}", d_cost_dir), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card_html("발송량", f"{cur_send:,.0f}건", f"전월비 {d_send}", d_send_dir), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card_html("클릭수", f"{cur_click:,.0f}회", f"전월비 {d_click}", d_click_dir), unsafe_allow_html=True)
    with c4:
        st.markdown(kpi_card_html("CTR", f"{cur_ctr:.2f}%", f"전월비 {d_ctr}", d_ctr_dir), unsafe_allow_html=True)

    # 매체별 요약 테이블
    st.markdown('<div style="margin-top:20px"></div>', unsafe_allow_html=True)

    cur_media = media_month_stats(df, latest_month)
    prev_media = media_month_stats(df, prev_month)

    merged = pd.merge(
        cur_media, prev_media,
        on='매체명', suffixes=('_당월', '_전월'), how='left',
    ).fillna(0)

    # ── 인사이트 칩 ──
    render_insights(detect_summary(
        cur_cost, prev_cost, cur_send, prev_send,
        cur_click, prev_click, cur_ctr, prev_ctr, merged,
    ))

    if not merged.empty and len(merged) >= 2:
        merged['CTR변화'] = merged['CTR_당월'] - merged['CTR_전월']
        # BUG S1: 양월 발송량 ≥ MIN_SENDS_FOR_CTR_CLAIM 매체만 Best/Watch 후보
        qualified = merged[
            (merged['발송량_당월'] >= MIN_SENDS_FOR_CTR_CLAIM)
            & (merged['발송량_전월'] >= MIN_SENDS_FOR_CTR_CLAIM)
        ]

        best = qualified.loc[qualified['CTR변화'].idxmax()] if not qualified.empty else None
        worst = qualified.loc[qualified['CTR변화'].idxmin()] if not qualified.empty else None

        best_ok = best is not None and abs(best['CTR변화']) >= CTR_CHANGE_THRESHOLD and best['CTR변화'] > 0
        worst_ok = worst is not None and abs(worst['CTR변화']) >= CTR_CHANGE_THRESHOLD and worst['CTR변화'] < 0

        if best is not None and worst is not None and best['매체명'] == worst['매체명'] and (best_ok or worst_ok):
            st.markdown(alert_card_html(
                "info",
                f"매체 요약 · {best['매체명']}",
                f"CTR {best['CTR_당월']:.2f}% (전월비 <b>{best['CTR변화']:+.2f}%p</b>) · 클릭 {best['클릭수_당월']:,.0f}회",
            ), unsafe_allow_html=True)
        elif best_ok or worst_ok:
            col_a, col_b = st.columns(2)
            with col_a:
                if best_ok:
                    st.markdown(alert_card_html(
                        "success",
                        f"Best · {best['매체명']}",
                        f"CTR {best['CTR_당월']:.2f}% (전월비 <b>{best['CTR변화']:+.2f}%p</b>) · 클릭 {best['클릭수_당월']:,.0f}회",
                    ), unsafe_allow_html=True)
                else:
                    st.markdown(alert_card_html(
                        "info",
                        "Best · 해당 없음",
                        "유의미하게 상승한 매체가 없습니다.",
                    ), unsafe_allow_html=True)
            with col_b:
                if worst_ok:
                    st.markdown(alert_card_html(
                        "danger",
                        f"Watch · {worst['매체명']}",
                        f"CTR {worst['CTR_당월']:.2f}% (전월비 <b>{worst['CTR변화']:+.2f}%p</b>) · 클릭 {worst['클릭수_당월']:,.0f}회",
                    ), unsafe_allow_html=True)
                else:
                    st.markdown(alert_card_html(
                        "info",
                        "Watch · 해당 없음",
                        "유의미하게 하락한 매체가 없습니다.",
                    ), unsafe_allow_html=True)
        else:
            st.markdown(alert_card_html(
                "info",
                "매체별 변동",
                "매체별 변동이 유의미하지 않습니다 "
                f"(양월 발송 ≥ {MIN_SENDS_FOR_CTR_CLAIM:,}건 및 CTR 변화 ≥ {CTR_CHANGE_THRESHOLD}%p 기준 미달).",
            ), unsafe_allow_html=True)
    elif len(merged) == 1:
        merged['CTR변화'] = merged['CTR_당월'] - merged['CTR_전월']
        row = merged.iloc[0]
        st.markdown(alert_card_html(
            "info",
            f"매체 1개 · {row['매체명']}",
            f"CTR {row['CTR_당월']:.2f}% (전월비 <b>{row['CTR변화']:+.2f}%p</b>) · 클릭 {row['클릭수_당월']:,.0f}회",
        ), unsafe_allow_html=True)

    st.divider()
