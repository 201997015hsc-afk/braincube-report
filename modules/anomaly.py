"""
이상치(Anomaly) 자동 감지 모듈
Z-score 기반으로 일별 지표의 급등/급락을 감지하고
자연어 알림 카드로 표시합니다.
"""
import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    section_header, alert_card_html, TARGET_METRICS,
    COLOR_DANGER, COLOR_SUCCESS, COLOR_WARNING,
    ANOMALY_Z_BINS, ANOMALY_Z_LABELS,
)
from modules.data_processing import aggregate_metrics, format_metric, render_ref_period_selector, MIN_RELIABLE_SENDS
from modules.insights import detect_anomaly_summary, render_insights
from modules.firebase_connector import (
    get_benchmark_stats, calc_percentile, calc_percentile_lower, percentile_grade,
)


def _detect_anomalies(daily: pd.DataFrame, metric: str, threshold: float = 2.0) -> pd.DataFrame:
    """Z-score 기반 이상치 감지. threshold 이상이면 이상치로 판정."""
    series = daily[metric].dropna()
    if len(series) < 7 or series.std() == 0 or series.mean() == 0:
        return pd.DataFrame()

    daily = daily.copy()
    mean = series.mean()
    std = series.std()
    daily['z_score'] = (series - mean) / std
    daily['배수'] = series / mean
    daily['기준평균'] = mean
    daily['방향'] = np.where(daily['z_score'] > 0, '급등', '급감')

    anomalies = daily[daily['z_score'].abs() >= threshold].copy()
    anomalies['심각도'] = pd.cut(
        anomalies['z_score'].abs(),
        bins=[threshold] + ANOMALY_Z_BINS[1:],
        labels=ANOMALY_Z_LABELS,
    )
    return anomalies.sort_values('z_score', key=abs, ascending=False)


_ACTION_MSG = {
    '급등': {
        '집행금액': '예산 초과 집행 또는 단가 급등 여부를 확인하세요.',
        '발송량':  '대량 발송 캠페인 또는 리스트 중복 여부를 점검하세요.',
        '클릭수':  '바이럴 효과 또는 봇 트래픽 가능성을 확인하세요.',
    },
    '급감': {
        '집행금액': '예산 소진, 캠페인 중단, 또는 정산 지연 가능성을 확인하세요.',
        '발송량':  '발송 장애, 리스트 축소, 또는 수신 거부 급증 여부를 점검하세요.',
        '클릭수':  '소재 피로도, 타겟 이탈, 또는 랜딩 페이지 오류 가능성을 확인하세요.',
    },
}


def _anomaly_card(row: pd.Series, metric: str, period_label: str = "") -> str:
    """이상치 행 데이터를 알림 카드 HTML로 변환"""
    date_str = row['일자']
    direction = row['방향']
    multiple = row['배수']
    severity = row['심각도']
    value = row[metric]

    if direction == '급등':
        alert_type = "danger" if severity in ['경고', '심각'] else "warning"
        icon = "🔺"
    else:
        alert_type = "danger" if severity in ['경고', '심각'] else "info"
        icon = "🔻"

    value_str = format_metric(metric, value)
    base_mean = row.get('기준평균', 0)
    base_str = format_metric(metric, base_mean)
    action = _ACTION_MSG.get(direction, {}).get(metric, '해당 캠페인의 변동사항을 확인하세요.')
    prd = f" ({period_label})" if period_label else ""

    if direction == '급감':
        ratio_text = f"일평균{prd} {base_str}의 <b>{multiple:.1f}배</b> 수준 (<b>{(1 - multiple) * 100:.0f}% 감소</b>)"
    else:
        ratio_text = f"일평균{prd} <b>{base_str}</b> 대비 <b>{multiple:.1f}배</b>"

    title = f"{icon} {date_str} · {metric} {direction} 감지 [{severity}]"
    body = (
        f"해당일 {metric}: <b>{value_str}</b> ({ratio_text})<br>"
        f"→ {action}"
    )
    return alert_card_html(alert_type, title, body)


def _anomaly_by_media(df: pd.DataFrame, threshold: float, period_label: str = "") -> list[str]:
    """매체별 이상치도 탐지하여 카드 리스트 반환 (발송량 부족 매체는 스킵)"""
    cards = []
    # 사전 집계: 매체별 총 발송량으로 신뢰도 판별 (루프 내 중복 연산 제거)
    media_sends = df.groupby('매체명')['발송량'].sum()
    reliable_media = media_sends[media_sends >= MIN_RELIABLE_SENDS].index

    # 전체 데이터를 매체+일별로 한 번에 집계
    daily_all = aggregate_metrics(df[df['매체명'].isin(reliable_media)], ['매체명', '날짜', '일자', '짧은_요일'])

    for media in reliable_media:
        daily = daily_all[daily_all['매체명'] == media]
        for metric in TARGET_METRICS:
            anomalies = _detect_anomalies(daily, metric, threshold)
            if anomalies.empty:
                continue
            top = anomalies.iloc[0]
            date_str = top['일자']
            direction = top['방향']
            multiple = top['배수']
            base_mean = top.get('기준평균', 0)
            value_str = format_metric(metric, top[metric])
            base_str = format_metric(metric, base_mean)
            action = _ACTION_MSG.get(direction, {}).get(metric, '해당 매체의 캠페인 변동사항을 점검하세요.')
            prd = f" ({period_label})" if period_label else ""

            icon = "🔺" if direction == '급등' else "🔻"
            alert_type = "warning" if direction == '급등' else "info"

            if direction == '급감':
                ratio_text = f"일평균{prd} {base_str}의 <b>{multiple:.1f}배</b> ({(1 - multiple) * 100:.0f}% 감소)"
            else:
                ratio_text = f"일평균{prd} <b>{base_str}</b> 대비 <b>{multiple:.1f}배</b>"

            cards.append(alert_card_html(
                alert_type,
                f"{icon} {media} · {date_str} · {metric} {direction}",
                f"<b>{value_str}</b> ({ratio_text})<br>→ {action}",
            ))
    return cards


def render(df: pd.DataFrame):
    from modules.ui_helpers import render_page_header
    render_page_header(
        title="이상치 감지",
        kicker="내부 도구",
        subtitle="일별 지표가 평소 대비 비정상적으로 급등/급락한 날을 Z-score 기반으로 자동 탐지합니다.",
    )

    # ── 기준 기간 + 민감도 ──
    ref_df, period_label = render_ref_period_selector(df, "anomaly")

    col1, col2 = st.columns([1, 3])
    with col1:
        sensitivity = st.select_slider(
            "감지 민감도",
            options=["높음 (1.5σ)", "보통 (2.0σ)", "낮음 (2.5σ)"],
            value="보통 (2.0σ)",
        )
    threshold_map = {"높음 (1.5σ)": 1.5, "보통 (2.0σ)": 2.0, "낮음 (2.5σ)": 2.5}
    threshold = threshold_map[sensitivity]

    # 전체 일별 이상치
    daily = aggregate_metrics(ref_df, ['날짜', '일자', '짧은_요일'])
    spike_cards = []   # 급등
    drop_cards = []    # 급감

    for metric in TARGET_METRICS:
        anomalies = _detect_anomalies(daily, metric, threshold)
        for _, row in anomalies.head(3).iterrows():
            card = _anomaly_card(row, metric, period_label)
            if row['방향'] == '급감':
                drop_cards.append(card)
            else:
                spike_cards.append(card)

    # 매체별 이상치 (상위만)
    media_cards = _anomaly_by_media(ref_df, threshold, period_label)
    total_count = len(spike_cards) + len(drop_cards) + len(media_cards)

    # ── 인사이트 칩 ──
    render_insights(detect_anomaly_summary(
        len(spike_cards) + len(drop_cards), len(media_cards), threshold,
    ))

    if total_count == 0:
        st.markdown(alert_card_html(
            "success",
            "이상치 없음",
            f"현재 민감도({sensitivity}) 기준으로 비정상적인 급등/급감이 감지되지 않았습니다. 모든 지표가 안정 범위 내에 있습니다.",
        ), unsafe_allow_html=True)
    else:
        # ── 급감 (먼저 — 더 중요) ──
        if drop_cards:
            st.markdown(
                f'<div class="anomaly-badge found">🔻 급감 감지 · {len(drop_cards)}건</div>',
                unsafe_allow_html=True,
            )
            for card in drop_cards:
                st.markdown(card, unsafe_allow_html=True)

        # ── 급등 ──
        if spike_cards:
            st.markdown(
                f'<div class="anomaly-badge found">🔺 급등 감지 · {len(spike_cards)}건</div>',
                unsafe_allow_html=True,
            )
            for card in spike_cards:
                st.markdown(card, unsafe_allow_html=True)

        # ── 매체별 ──
        if media_cards:
            st.markdown(
                f'<br><div class="anomaly-badge found">매체별 기준 · {len(media_cards)}건 감지</div>',
                unsafe_allow_html=True,
            )
            for card in media_cards[:8]:
                st.markdown(card, unsafe_allow_html=True)

    # ── 업종 기준 비교 ──────────────────────────
    try:
        bench = get_benchmark_stats()
    except Exception:
        bench = None
    if bench and bench['avg_ctr'] > 0:
        st.markdown(section_header(
            "업종 벤치마크 대비 포지션",
            "Firebase 업종 데이터 기준으로 자사 지표가 어디에 위치하는지 보여줍니다.",
        ), unsafe_allow_html=True)

        # 자사 지표 계산
        total_send = ref_df['발송량'].sum()
        total_click = ref_df['클릭수'].sum()
        total_cost = ref_df['집행금액'].sum()
        client_ctr = (total_click / total_send * 100) if total_send > 0 else 0
        client_cpc = (total_cost / total_click) if total_click > 0 else 0

        bench_items = []
        if client_ctr > 0:
            ctr_pct = calc_percentile(client_ctr, bench['ctr_values'])
            ctr_label, ctr_bg, ctr_color = percentile_grade(ctr_pct)
            ctr_ratio = client_ctr / bench['avg_ctr'] if bench['avg_ctr'] > 0 else 1
            bench_items.append(('CTR', f'{client_ctr:.2f}%', f'{bench["avg_ctr"]:.2f}%',
                                ctr_ratio, ctr_label, ctr_bg, ctr_color))

        if client_cpc > 0 and bench['avg_cpc'] > 0:
            cpc_pct = calc_percentile_lower(client_cpc, bench['cpc_values'])
            cpc_label, cpc_bg, cpc_color = percentile_grade(cpc_pct)
            cpc_ratio = client_cpc / bench['avg_cpc']
            bench_items.append(('CPC', f'{client_cpc:,.0f}원', f'{bench["avg_cpc"]:,.0f}원',
                                cpc_ratio, cpc_label, cpc_bg, cpc_color))

        if bench_items:
            cards_html = ""
            for metric_name, my_val, ind_val, ratio, label, bg, color in bench_items:
                ratio_pct = (ratio - 1) * 100
                sign = "+" if ratio_pct >= 0 else ""
                # CPC는 낮을수록 좋음, CTR은 높을수록 좋음
                if metric_name == 'CPC':
                    good = ratio_pct <= 0
                else:
                    good = ratio_pct >= 0
                ratio_color = "#2E7D32" if good else "#E53935"

                cards_html += (
                    f'<div style="flex:1;background:#FFF;border-radius:14px;padding:16px 20px;'
                    f'box-shadow:0 1px 4px rgba(0,0,0,.03);">'
                    f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">'
                    f'<span style="font-weight:700;font-size:.9rem;color:#191F28;">{metric_name}</span>'
                    f'<span style="background:{bg};color:{color};font-size:.72rem;'
                    f'font-weight:700;padding:3px 10px;border-radius:16px;">{label}</span></div>'
                    f'<div style="display:flex;gap:20px;">'
                    f'<div><div style="font-size:.68rem;color:#8B95A1;font-weight:600;">자사</div>'
                    f'<div style="font-size:1.1rem;font-weight:800;color:#191F28;">{my_val}</div></div>'
                    f'<div><div style="font-size:.68rem;color:#8B95A1;font-weight:600;">업종 평균</div>'
                    f'<div style="font-size:1.1rem;font-weight:800;color:#8B95A1;">{ind_val}</div></div>'
                    f'<div><div style="font-size:.68rem;color:#8B95A1;font-weight:600;">차이</div>'
                    f'<div style="font-size:1.1rem;font-weight:800;color:{ratio_color};">'
                    f'{sign}{ratio_pct:.1f}%</div></div>'
                    f'</div></div>'
                )

            st.markdown(
                f'<div style="display:flex;gap:10px;margin-bottom:12px;">{cards_html}</div>',
                unsafe_allow_html=True,
            )

            # 업종 대비 이상치 알림
            alerts = []
            if client_cpc > 0 and bench['avg_cpc'] > 0:
                cpc_ratio = client_cpc / bench['avg_cpc']
                if cpc_ratio > 1.5:
                    alerts.append(alert_card_html(
                        "danger", "🔴 CPC 업종 대비 과다",
                        f"자사 CPC <b>{client_cpc:,.0f}원</b>은 업종 평균 <b>{bench['avg_cpc']:,.0f}원</b>의 "
                        f"<b>{cpc_ratio:.1f}배</b>입니다. 소재 최적화 또는 타겟 재설정을 검토하세요.",
                    ))
                elif cpc_ratio < 0.5:
                    alerts.append(alert_card_html(
                        "success", "🟢 CPC 업종 대비 우수",
                        f"자사 CPC <b>{client_cpc:,.0f}원</b>은 업종 평균의 <b>{cpc_ratio:.1f}배</b> 수준으로 "
                        f"매우 효율적입니다. 현재 전략을 유지하세요.",
                    ))

            if client_ctr > 0 and bench['avg_ctr'] > 0:
                ctr_ratio = client_ctr / bench['avg_ctr']
                if ctr_ratio < 0.5:
                    alerts.append(alert_card_html(
                        "danger", "🔴 CTR 업종 대비 저조",
                        f"자사 CTR <b>{client_ctr:.2f}%</b>는 업종 평균 <b>{bench['avg_ctr']:.2f}%</b>의 "
                        f"절반 이하입니다. 소재·타겟·발송 시간대를 점검하세요.",
                    ))
                elif ctr_ratio > 1.5:
                    alerts.append(alert_card_html(
                        "success", "🟢 CTR 업종 대비 우수",
                        f"자사 CTR <b>{client_ctr:.2f}%</b>는 업종 평균 대비 "
                        f"<b>{ctr_ratio:.1f}배</b>로 매우 우수합니다.",
                    ))

            for a in alerts:
                st.markdown(a, unsafe_allow_html=True)

    st.divider()
