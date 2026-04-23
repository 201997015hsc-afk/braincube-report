"""
AI 전략 제안 + 예산 시뮬레이터 (Toss-style)
"""
import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    section_header, alert_card_html, kpi_card_html,
    CTR_CHANGE_THRESHOLD, VOLUME_CHANGE_THRESHOLD,
    MIN_SAMPLES_PER_CATEGORY, MIN_SENDS_FOR_CTR_CLAIM,
    COLOR_TEXT_SEC,
)
from modules.data_processing import (
    calc_ctr, calc_ctr_scalar, aggregate_by_weekday, media_month_stats,
    render_ref_period_selector,
)


def _render_strategy(df: pd.DataFrame):
    st.markdown(section_header(
        "AI 마케팅 전략 제안",
        "데이터 기반으로 자동 도출된 개선 포인트와 액션 아이템입니다.",
    ), unsafe_allow_html=True)

    # 요일별 효율
    day_stats = aggregate_by_weekday(df)
    valid_days = day_stats[day_stats['CTR'] > 0]
    if valid_days.empty:
        st.caption("요일별 효율 데이터가 부족합니다.")
        st.divider()
        return
    best_day = valid_days.loc[valid_days['CTR'].idxmax()]
    worst_day = valid_days.loc[valid_days['CTR'].idxmin()]

    # 최근 2개월 비교
    months = sorted(df['년월'].unique())
    if len(months) >= 2:
        latest = media_month_stats(df, months[-1]).set_index('매체명').rename(
            columns={'CTR': '최근CTR', '발송량': '발송량_당월'}
        )
        prev = media_month_stats(df, months[-2]).set_index('매체명').rename(
            columns={'CTR': '과거CTR', '발송량': '발송량_전월'}
        )

        trend = pd.merge(
            latest[['최근CTR', '발송량_당월']],
            prev[['과거CTR', '발송량_전월']],
            on='매체명',
        ).reset_index()
        trend['변화량'] = trend['최근CTR'] - trend['과거CTR']

        if not trend.empty:
            worst = trend.loc[trend['변화량'].idxmin()]
            best = trend.loc[trend['변화량'].idxmax()]
            # BUG B1: CTR 변동 유의성 + 최소 발송량 가드
            worst_sends_ok = (
                worst['발송량_당월'] >= MIN_SENDS_FOR_CTR_CLAIM
                and worst['발송량_전월'] >= MIN_SENDS_FOR_CTR_CLAIM
            )
            best_sends_ok = (
                best['발송량_당월'] >= MIN_SENDS_FOR_CTR_CLAIM
                and best['발송량_전월'] >= MIN_SENDS_FOR_CTR_CLAIM
            )
            if abs(worst['변화량']) >= CTR_CHANGE_THRESHOLD and worst['변화량'] < 0 and worst_sends_ok:
                st.markdown(alert_card_html(
                    "danger",
                    f"효율 저하 감지 · {worst['매체명']}",
                    f"최근 한 달간 <b>{worst['매체명']}</b>의 CTR이 이전 달 대비 <b>{abs(worst['변화량']):.2f}%p 하락</b>했습니다.<br>"
                    f"→ 타겟팅 연령대 재조정 또는 신규 문구 A/B 테스트를 권장합니다.",
                ), unsafe_allow_html=True)
            if abs(best['변화량']) >= CTR_CHANGE_THRESHOLD and best['변화량'] > 0 and best_sends_ok:
                st.markdown(alert_card_html(
                    "success",
                    f"예산 증액 추천 · {best['매체명']}",
                    f"최근 한 달간 <b>{best['매체명']}</b>의 CTR이 <b>{best['변화량']:+.2f}%p 상승</b>하며 최고 효율을 기록 중입니다.<br>"
                    f"→ 캠페인 예산 20% 우선 증액 편성으로 전환 볼륨 극대화를 제안합니다.",
                ), unsafe_allow_html=True)

    # BUG B5: 요일당 캠페인 ≥5일 때만 제안 (표본 부족 시 문구 톤다운)
    if '짧은_요일' in df.columns:
        day_camp_counts = df.groupby('짧은_요일').size()
        best_n = int(day_camp_counts.get(best_day['짧은_요일'], 0))
        worst_n = int(day_camp_counts.get(worst_day['짧은_요일'], 0))
    else:
        best_n = worst_n = 0

    if best_n >= 5 and worst_n >= 5:
        st.markdown(alert_card_html(
            "warning",
            "발송 타이밍 최적화",
            f"클릭률 최고 요일: <b>{best_day['짧은_요일']}요일 ({best_day['CTR']:.2f}%)</b> · "
            f"최저 요일: <b>{worst_day['짧은_요일']}요일 ({worst_day['CTR']:.2f}%)</b><br>"
            f"→ {worst_day['짧은_요일']}요일 발송 비중을 줄이고, <b>{best_day['짧은_요일']}요일</b> 비중 상향 검토를 권장합니다.",
        ), unsafe_allow_html=True)
    else:
        st.markdown(alert_card_html(
            "info",
            "발송 타이밍 참고",
            f"요일별 표본이 부족하여 확정 제안은 생략합니다 "
            f"({best_day['짧은_요일']}요일 n={best_n}, {worst_day['짧은_요일']}요일 n={worst_n} / 최소 5 필요).",
        ), unsafe_allow_html=True)

    st.divider()


def _analyze_media(df: pd.DataFrame, media: str) -> dict:
    """매체별 성과 데이터를 종합 분석하여 딕셔너리로 반환"""
    sim = df[df['매체명'] == media]
    months = sorted(sim['년월'].unique())
    n_months = max(len(months), 1)

    total_cost = sim['집행금액'].sum()
    total_click = sim['클릭수'].sum()
    total_send = sim['발송량'].sum()
    cpc = total_cost / total_click if total_click > 0 else 0
    avg_monthly_cost = total_cost / n_months
    overall_ctr = calc_ctr_scalar(total_click, total_send)

    # CTR 추세 (최근 2개월 비교)
    ctr_trend = 0.0
    ctr_status = "stable"
    if len(months) >= 2:
        latest_ctr = calc_ctr_scalar(sim[sim['년월'] == months[-1]]['클릭수'].sum(),
                                     sim[sim['년월'] == months[-1]]['발송량'].sum())
        prev_ctr = calc_ctr_scalar(sim[sim['년월'] == months[-2]]['클릭수'].sum(),
                                   sim[sim['년월'] == months[-2]]['발송량'].sum())
        ctr_trend = latest_ctr - prev_ctr
        if ctr_trend > CTR_CHANGE_THRESHOLD:
            ctr_status = "rising"
        elif ctr_trend < -CTR_CHANGE_THRESHOLD:
            ctr_status = "falling"

    # 볼륨 추세 (최근 2개월 발송량 비교)
    vol_trend = "stable"
    if len(months) >= 2:
        latest_vol = sim[sim['년월'] == months[-1]]['발송량'].sum()
        prev_vol = sim[sim['년월'] == months[-2]]['발송량'].sum()
        if prev_vol > 0:
            vol_change = ((latest_vol - prev_vol) / prev_vol) * 100
            if vol_change > VOLUME_CHANGE_THRESHOLD:
                vol_trend = "growing"
            elif vol_change < -VOLUME_CHANGE_THRESHOLD:
                vol_trend = "shrinking"

    # 전체 매체 대비 CPC 순위 (BUG B6: 0클릭 매체의 inf 제외)
    all_media_cpc = df.groupby('매체명').apply(
        lambda g: g['집행금액'].sum() / g['클릭수'].sum() if g['클릭수'].sum() > 0 else float('inf')
    ).sort_values()
    all_media_cpc = all_media_cpc[all_media_cpc != float('inf')]
    cpc_rank = list(all_media_cpc.index).index(media) + 1 if media in all_media_cpc.index else 0
    cpc_total = len(all_media_cpc)
    # BUG B3: 매체 수 부족 시 3등분 분류는 신뢰도 낮음 → mid로 고정 (스크립트에서 분기 톤다운)
    if cpc_total >= MIN_SAMPLES_PER_CATEGORY:
        cpc_position = "low" if cpc_rank <= cpc_total * 0.33 else ("high" if cpc_rank >= cpc_total * 0.66 else "mid")
    else:
        cpc_position = "insufficient"

    # 이 매체의 최적 요일
    day_stats = aggregate_by_weekday(sim)
    valid_d = day_stats[day_stats['CTR'] > 0]
    best_day = valid_d.loc[valid_d['CTR'].idxmax()] if not valid_d.empty else None

    return dict(
        cpc=cpc, avg_monthly_cost=avg_monthly_cost, overall_ctr=overall_ctr,
        ctr_trend=ctr_trend, ctr_status=ctr_status,
        vol_trend=vol_trend,
        cpc_position=cpc_position, cpc_rank=cpc_rank, cpc_total=cpc_total,
        best_day=best_day, n_months=n_months,
        total_cost=total_cost, total_click=total_click,
    )


def _generate_script(media: str, info: dict, add_man: int, new_click: float, increase: float) -> str:
    """매체별 실제 성과 데이터를 기반으로 맞춤형 영업 스크립트 생성"""
    lines = []

    # 1) 현황 진단 (CTR 추세에 따라 다른 오프닝)
    if info['ctr_status'] == "rising":
        lines.append(
            f"<b>{media}</b>의 CTR이 전월 대비 <b>{info['ctr_trend']:+.2f}%p 상승</b> 중이며, "
            f"현재 평균 CTR <b>{info['overall_ctr']:.2f}%</b>로 효율이 개선되고 있습니다."
        )
    elif info['ctr_status'] == "falling":
        lines.append(
            f"<b>{media}</b>의 CTR이 전월 대비 <b>{info['ctr_trend']:+.2f}%p 하락</b>한 상황이나, "
            f"평균 CTR <b>{info['overall_ctr']:.2f}%</b>는 여전히 유의미한 수준입니다. "
            f"지금 예산을 조정하면 반등 기회를 잡을 수 있습니다."
        )
    else:
        # BUG B2: n_months에 따라 문구 분기
        if info['n_months'] >= 3:
            stable_txt = f"<b>{info['n_months']}개월</b>간 안정적인 성과 유지 중입니다."
        elif info['n_months'] == 2:
            stable_txt = "최근 2개월 비슷한 수준을 유지하고 있습니다."
        else:
            stable_txt = "신규 매체 — 트렌드 판단 불가 상태입니다."
        lines.append(
            f"<b>{media}</b>는 평균 CTR <b>{info['overall_ctr']:.2f}%</b>로 "
            f"{stable_txt}"
        )

    # 2) CPC 경쟁력 언급
    if info['cpc_position'] == "insufficient":
        # BUG B3: 매체 수가 적을 때는 사실만 전달 (인과적 해석 제거)
        lines.append(
            f"CPC <b>{info['cpc']:,.0f}원</b> · <b>{info['cpc_rank']}위 / {info['cpc_total']}개 매체</b>. "
            f"(비교 매체 수가 적어 상대 효율 해석은 보류합니다.)"
        )
    elif info['cpc_position'] == "low":
        lines.append(
            f"특히 CPC <b>{info['cpc']:,.0f}원</b>으로 전체 {info['cpc_total']}개 매체 중 "
            f"<b>{info['cpc_rank']}위</b>의 비용 효율을 보여, 예산 대비 유입 효과가 큰 채널입니다."
        )
    elif info['cpc_position'] == "high":
        lines.append(
            f"CPC가 <b>{info['cpc']:,.0f}원</b>으로 다소 높은 편이지만, "
            f"예산 증액을 통해 볼륨을 확보하면 단가 협상력도 함께 올라갈 수 있습니다."
        )
    else:
        lines.append(
            f"CPC <b>{info['cpc']:,.0f}원</b>은 업계 평균 수준이며, 볼륨 확대를 통해 추가 효율 개선 여지가 있습니다."
        )

    # 3) 볼륨 추세 반영
    if info['vol_trend'] == "growing":
        lines.append("최근 발송량도 증가 추세여서, 지금이 스케일업에 적합한 타이밍입니다.")
    elif info['vol_trend'] == "shrinking":
        lines.append("최근 발송량이 감소 추세이므로, 예산 재투입으로 볼륨을 회복하는 것이 중요합니다.")

    # 4) 최적 요일 팁
    if info['best_day'] is not None:
        bd = info['best_day']
        lines.append(
            f"참고로 {media}의 <b>{bd['짧은_요일']}요일</b> CTR이 <b>{bd['CTR']:.2f}%</b>로 가장 높으니, "
            f"증액 예산은 해당 요일에 집중 편성하시면 효과가 극대화됩니다."
        )

    # 5) 결론 (증액 제안)
    lines.append(
        f"월 예산을 <b>{add_man:,.0f}만 원</b> 증액하시면 "
        f"예상 클릭수가 <b>{new_click:,.0f}회</b>(약 <b>{increase:.1f}% 증가</b>)로 예측됩니다."
    )

    return "<br>".join(lines)


def _render_simulator(df: pd.DataFrame):
    st.markdown(section_header(
        "예산 증액 시뮬레이터",
        "매체별 실제 성과 데이터를 분석하여 맞춤형 시뮬레이션과 영업 스크립트를 생성합니다.",
    ), unsafe_allow_html=True)

    media = st.selectbox("시뮬레이션 매체 선택", df['매체명'].unique())
    sim = df[df['매체명'] == media]
    total_cost, total_click = sim['집행금액'].sum(), sim['클릭수'].sum()

    if total_click <= 0 or total_cost <= 0:
        st.caption("과거 데이터가 부족하여 시뮬레이션을 실행할 수 없습니다.")
        return

    info = _analyze_media(df, media)
    avg_monthly = info['avg_monthly_cost']
    cpc = info['cpc']
    base_click = avg_monthly / cpc

    st.markdown(alert_card_html(
        "info",
        f"{media} 현황 분석",
        f"평균 CPC <b>{cpc:,.0f}원</b> · CTR <b>{info['overall_ctr']:.2f}%</b> · "
        f"CTR 추세 <b>{info['ctr_trend']:+.2f}%p</b> · 볼륨 {info['vol_trend']}",
    ), unsafe_allow_html=True)

    add_man = st.slider("추가 제안 예산 (만 원)", 0, 5000, 1000, 100)
    new_budget = avg_monthly + add_man * 10_000
    new_click = new_budget / cpc
    increase = ((new_click - base_click) / base_click) * 100 if base_click > 0 else 0

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(kpi_card_html("현재 월 평균 예산", f"{avg_monthly:,.0f}원"), unsafe_allow_html=True)
    with c2:
        st.markdown(kpi_card_html("증액 후 총 예산", f"{new_budget:,.0f}원", f"+{add_man:,.0f}만원", "up"), unsafe_allow_html=True)
    with c3:
        st.markdown(kpi_card_html("예상 클릭수", f"{new_click:,.0f}회", f"+{increase:.1f}% 증가", "up"), unsafe_allow_html=True)

    # BUG B4: 선형 가정에 대한 면책
    st.markdown(
        '<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-top:8px;padding:0 4px;">'
        '※ CPC가 일정하다는 가정 (실제는 예산 증액 시 CPC 상승 가능).'
        '</div>',
        unsafe_allow_html=True,
    )

    st.markdown('<div style="margin-top:20px"></div>', unsafe_allow_html=True)
    script = _generate_script(media, info, add_man, new_click, increase)
    st.markdown(alert_card_html("info", f"{media} 맞춤 영업 스크립트", script), unsafe_allow_html=True)


def render(df: pd.DataFrame):
    from modules.ui_helpers import render_page_header
    render_page_header(
        title="전략 제안",
        kicker="액션",
        subtitle="데이터 기반으로 자동 도출된 개선 포인트와 액션 아이템, 그리고 매체별 증액 시뮬레이터를 제공합니다.",
    )
    ref_df, period_label = render_ref_period_selector(df, "biz")
    _render_strategy(ref_df)
    _render_simulator(ref_df)
