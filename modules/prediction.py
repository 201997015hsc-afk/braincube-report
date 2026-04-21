"""
앙상블 기반 매체별 클릭 예측 섹션 (Toss-style)
─────────────────────────────────────────────
- WMA + 선형회귀 + 전년동월 앙상블 모델
- 잔차 기반 80%/95% 신뢰구간 (부채꼴 콘)
- 과거 예측 적중률 백테스팅 (WMA vs 앙상블 비교)
- 발송량 변동 시뮬레이션
- 모델 신뢰도 등급 · 구성 표시
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go

from modules.config import (
    WMA_WEIGHTS, PLOTLY_LAYOUT, section_header, get_media_color,
    kpi_card_html, alert_card_html,
    COLOR_SUCCESS, COLOR_WARNING, COLOR_DANGER,
    PREDICTION_MIN_MONTHS, BACKTEST_MIN_MONTHS,
)
from modules.data_processing import calc_ctr
from modules.insights import detect_prediction, render_insights


# ──────────────────────────────────────────────
# 개별 모델
# ──────────────────────────────────────────────

def _wma(series: np.ndarray, weights: np.ndarray) -> float:
    """Weighted Moving Average — 최근 N개 값에 가중치 적용"""
    n = len(weights)
    return float(np.dot(series[-n:], weights))


def _linear(series: np.ndarray) -> float | None:
    """선형 회귀 외삽 — 추세선을 다음 시점으로 연장"""
    n = len(series)
    if n < 3:
        return None
    x = np.arange(n, dtype=float)
    try:
        coeffs = np.polyfit(x, series, 1)
        pred = float(np.polyval(coeffs, n))
        return max(pred, 0)
    except (np.linalg.LinAlgError, ValueError):
        return None


def _yoy(series: np.ndarray) -> float | None:
    """전년 동월 기반 예측 — 12개월 전 값에 최근 트렌드 보정"""
    n = len(series)
    if n < 12:
        return None
    yoy_base = series[-12]
    if yoy_base <= 0:
        return None
    recent = float(np.mean(series[-3:])) if n >= 3 else float(series[-1])
    older = float(np.mean(series[-6:-3])) if n >= 6 else recent
    trend = float(np.clip(recent / older if older > 0 else 1.0, 0.5, 2.0))
    return float(yoy_base * trend)


def _reliability_grade(accuracy: float) -> tuple[str, str]:
    """적중률 → (등급텍스트, 색상)"""
    if accuracy >= 85:
        return '높음', '#2E7D32'
    if accuracy >= 70:
        return '양호', '#3182F6'
    if accuracy >= 55:
        return '보통', '#F59E0B'
    return '참고용', '#E53935'


# ──────────────────────────────────────────────
# 앙상블 예측
# ──────────────────────────────────────────────

def _predict_media(m_df: pd.DataFrame, vol_adjust: float = 0.0) -> dict | None:
    """
    앙상블 예측: WMA + 선형회귀 + 전년동월.
    가용 모델만 자동 조합하고, 잔차 기반 80%/95% 신뢰구간을 산출합니다.
    """
    if len(m_df) < PREDICTION_MIN_MONTHS:
        return None

    clicks = m_df['클릭수'].fillna(0).values.astype(float)
    n = len(clicks)
    w = np.array(WMA_WEIGHTS, dtype=float)

    # ── 개별 모델 예측 ──
    wma_pred = _wma(clicks, w)
    linear_pred = _linear(clicks)
    yoy_pred = _yoy(clicks)

    # ── 가중치 결정 (가용 모델에 따라 동적 조정) ──
    models: dict[str, float] = {}
    weights: dict[str, float] = {}

    models['WMA'] = wma_pred
    weights['WMA'] = 0.50

    if linear_pred is not None and np.isfinite(linear_pred):
        models['선형회귀'] = linear_pred
        weights['선형회귀'] = 0.25

    if yoy_pred is not None and np.isfinite(yoy_pred):
        models['전년동월'] = yoy_pred
        weights['전년동월'] = 0.30
        # 전년 데이터 있으면 WMA 비중 축소
        weights['WMA'] = 0.35
        if '선형회귀' in weights:
            weights['선형회귀'] = 0.20

    total_w = sum(weights.values())
    weights = {k: v / total_w for k, v in weights.items()}

    pred_click = sum(models[k] * weights[k] for k in models)

    # ── 발송량 변동 보정 ──
    if vol_adjust != 0:
        pred_click *= (1 + vol_adjust / 100)

    if not np.isfinite(pred_click) or pred_click < 0:
        return None

    # ── 잔차 기반 신뢰구간 (개별 잔차 ±100% 캡핑) ──
    residual_pcts: list[float] = []
    for i in range(3, n):
        bt_pred = float(np.dot(clicks[i - 3:i], w))
        actual = clicks[i]
        if actual > 0 and np.isfinite(bt_pred):
            res = (bt_pred - actual) / actual
            residual_pcts.append(max(min(res, 1.0), -1.0))

    if len(residual_pcts) >= 3:
        std_pct = float(np.std(residual_pcts))
    else:
        std_pct = 0.20
    # 전체 불확실성 상한 50%
    std_pct = min(std_pct, 0.50)

    # 발송량 변동 시 불확실성 증가
    if vol_adjust != 0:
        std_pct *= (1 + abs(vol_adjust) / 100 * 0.3)

    ci80 = pred_click * std_pct * 1.28   # 80% CI z-score
    ci95 = pred_click * std_pct * 1.96   # 95% CI z-score

    last_ym = m_df['년월'].iloc[-1]
    last_date = pd.to_datetime(f"{int(last_ym[:4])}-{int(last_ym[6:8]):02d}-01")
    next_label = (last_date + pd.DateOffset(months=1)).strftime('%Y년 %m월')

    # ── 발송량 예측 (WMA + 보조) ──
    pred_sends = None
    if '발송량' in m_df.columns:
        sends_arr = m_df['발송량'].fillna(0).values.astype(float)
        if len(sends_arr) >= 3:
            sends_wma = _wma(sends_arr, w)
            if sends_wma is not None and np.isfinite(sends_wma) and sends_wma > 0:
                pred_sends = float(sends_wma)
                if vol_adjust != 0:
                    pred_sends *= (1 + vol_adjust / 100)

    last_sends = int(m_df['발송량'].iloc[-1]) if '발송량' in m_df.columns else 0

    return dict(
        click=int(pred_click),
        sends=int(pred_sends) if pred_sends else None,
        last_sends=last_sends,
        # 신뢰구간
        ci80_upper=int(pred_click + ci80),
        ci80_lower=int(max(pred_click - ci80, 0)),
        ci95_upper=int(pred_click + ci95),
        ci95_lower=int(max(pred_click - ci95, 0)),
        # 레거시 호환 (insights.py 등)
        optimistic=int(pred_click + ci80),
        conservative=int(max(pred_click - ci80, 0)),
        # 메타
        next_label=next_label,
        last_ym=last_ym,
        last_click=int(m_df['클릭수'].iloc[-1]),
        models={k: int(v) for k, v in models.items()},
        weights={k: round(v * 100) for k, v in weights.items()},
        n_months=n,
        std_pct=round(std_pct * 100, 1),
    )


# ──────────────────────────────────────────────
# 백테스팅 (WMA vs 앙상블 비교)
# ──────────────────────────────────────────────

def _backtest(m_df: pd.DataFrame) -> dict | None:
    """WMA vs 앙상블 백테스팅 — 시점별로 과거 예측 정확도를 비교합니다."""
    if len(m_df) < BACKTEST_MIN_MONTHS:
        return None

    clicks = m_df['클릭수'].fillna(0).values.astype(float)
    w = np.array(WMA_WEIGHTS, dtype=float)
    n = len(clicks)
    records: list[dict] = []

    for i in range(3, n):
        actual = clicks[i]
        if actual <= 0:
            continue

        # ── WMA 예측 ──
        wma_p = float(np.dot(clicks[i - 3:i], w))

        # ── 앙상블 예측 (해당 시점까지 데이터만 사용) ──
        ens_preds: list[float] = [wma_p]
        ens_wgts: list[float] = [0.50]

        lin_p = _linear(clicks[:i])
        if lin_p is not None and np.isfinite(lin_p):
            ens_preds.append(lin_p)
            ens_wgts.append(0.25)

        if i >= 12:
            yoy_base = clicks[i - 12]
            if yoy_base > 0:
                rc = float(np.mean(clicks[i - 3:i]))
                ol = float(np.mean(clicks[i - 6:i - 3])) if i >= 6 else rc
                tr = float(np.clip(rc / ol if ol > 0 else 1.0, 0.5, 2.0))
                ens_preds.append(yoy_base * tr)
                ens_wgts.append(0.30)
                ens_wgts[0] = 0.35
                if len(ens_wgts) > 2:
                    ens_wgts[1] = 0.20

        tw = sum(ens_wgts)
        ens_wgts = [ew / tw for ew in ens_wgts]
        ens_p = sum(m * ew for m, ew in zip(ens_preds, ens_wgts))

        wma_err = min(abs(wma_p - actual) / actual * 100, 100)
        ens_err = min(abs(ens_p - actual) / actual * 100, 100)

        records.append({
            '월': m_df['년월'].iloc[i],
            'WMA': f"{int(wma_p):,}",
            '앙상블': f"{int(ens_p):,}",
            '실제': f"{int(actual):,}",
            'WMA오차': f"{wma_err:.1f}%",
            '앙상블오차': f"{ens_err:.1f}%",
            '_wma_e': wma_err,
            '_ens_e': ens_err,
        })

    if not records:
        return None

    wma_mape = float(np.mean([r['_wma_e'] for r in records]))
    ens_mape = float(np.mean([r['_ens_e'] for r in records]))
    display = [{k: v for k, v in r.items() if not k.startswith('_')} for r in records]

    return dict(
        mape=round(ens_mape, 1),
        accuracy=round(max(100 - ens_mape, 0), 1),
        wma_mape=round(wma_mape, 1),
        wma_accuracy=round(max(100 - wma_mape, 0), 1),
        improvement=round(wma_mape - ens_mape, 1),
        n=len(records),
        details=display,
    )


# ──────────────────────────────────────────────
# 매체 차트 카드
# ──────────────────────────────────────────────

def _render_media_card(media, m_df, pred, acc, all_media):
    """매체 1개의 차트 + KPI + 모델 구성 정보를 렌더"""
    # ── 데이터 부족 → 예측 불가 안내 카드로 대체 ──
    if pred is None and len(m_df) < 2:
        st.markdown(
            f'<div style="background:#F8F9FA;border-radius:14px;padding:20px 22px;'
            f'border:1px solid #EBEEF2;height:100%;">'
            f'<div style="font-size:0.98rem;font-weight:700;color:#191F28;margin-bottom:8px;">{media}</div>'
            f'<div style="background:#FFF8F0;border-left:3px solid #FF9100;'
            f'border-radius:0 8px 8px 0;padding:12px 14px;margin-top:8px;">'
            f'<div style="font-size:0.84rem;color:#4E5968;line-height:1.6;">'
            f'📉 <b>예측을 위한 데이터 부족</b><br>'
            f'<span style="font-size:0.76rem;color:#8B95A1;">'
            f'현재 {len(m_df)}개월 데이터만 존재 — 최소 2개월 이상 필요합니다.</span>'
            f'</div></div></div>',
            unsafe_allow_html=True,
        )
        return

    color = get_media_color(media, all_media)
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)

    fig = go.Figure()

    # ── 실적 라인 + 영역 ──
    fig.add_trace(go.Scatter(
        x=m_df['년월'], y=m_df['클릭수'],
        mode='lines+markers',
        line=dict(color=color, width=2.5, shape='spline'),
        marker=dict(size=6, color=color, line=dict(width=1.5, color='white')),
        fill='tozeroy',
        fillcolor=f'rgba({r},{g},{b},0.06)',
        hovertemplate='%{x}<br>%{y:,.0f}건<extra></extra>',
        showlegend=False,
    ))

    if pred:
        # ── 95% 신뢰구간 (넓은 밴드 · 연한 색) ──
        fig.add_trace(go.Scatter(
            x=[pred['last_ym'], pred['next_label']],
            y=[pred['last_click'], pred['ci95_lower']],
            mode='lines', line=dict(width=0),
            showlegend=False, hoverinfo='skip',
        ))
        fig.add_trace(go.Scatter(
            x=[pred['last_ym'], pred['next_label']],
            y=[pred['last_click'], pred['ci95_upper']],
            mode='lines', line=dict(width=0),
            fill='tonexty',
            fillcolor=f'rgba({r},{g},{b},0.07)',
            showlegend=False, hoverinfo='skip',
        ))

        # ── 80% 신뢰구간 (좁은 밴드 · 진한 색) ──
        fig.add_trace(go.Scatter(
            x=[pred['last_ym'], pred['next_label']],
            y=[pred['last_click'], pred['ci80_lower']],
            mode='lines', line=dict(width=0),
            showlegend=False, hoverinfo='skip',
        ))
        fig.add_trace(go.Scatter(
            x=[pred['last_ym'], pred['next_label']],
            y=[pred['last_click'], pred['ci80_upper']],
            mode='lines', line=dict(width=0),
            fill='tonexty',
            fillcolor=f'rgba({r},{g},{b},0.15)',
            showlegend=False, hoverinfo='skip',
        ))

        # ── 예측 점선 ──
        fig.add_trace(go.Scatter(
            x=[pred['last_ym'], pred['next_label']],
            y=[pred['last_click'], pred['click']],
            mode='lines',
            line=dict(color=color, width=2, dash='dot'),
            showlegend=False, hoverinfo='skip',
        ))

        # ── ◆ 예측 포인트 ──
        fig.add_trace(go.Scatter(
            x=[pred['next_label']], y=[pred['click']],
            mode='markers',
            marker=dict(size=11, color=color, symbol='diamond',
                        line=dict(width=2, color='white')),
            showlegend=False,
            hovertemplate=(
                f"예측: {pred['click']:,}건<br>"
                f"80%: {pred['ci80_lower']:,} ~ {pred['ci80_upper']:,}<br>"
                f"95%: {pred['ci95_lower']:,} ~ {pred['ci95_upper']:,}"
                f"<extra></extra>"
            ),
        ))

        # ── 예측값 텍스트 라벨 ──
        fig.add_annotation(
            x=pred['next_label'], y=pred['click'],
            text=f"<b>{pred['click']:,}</b>",
            showarrow=False, yshift=20,
            font=dict(size=12, color=color, family='Pretendard'),
        )

    # ── 레이아웃 ──
    acc_tag = ""
    if acc:
        if acc['accuracy'] >= 10:
            grade_txt, _ = _reliability_grade(acc['accuracy'])
            acc_tag = f"  ·  적중률 {acc['accuracy']:.0f}% ({grade_txt})"
        elif acc['n'] <= 1:
            acc_tag = "  ·  데이터 부족"

    yoy_tag = ""
    if pred and pred.get('n_months', 0) >= 12:
        yoy_tag = "  ·  📅전년동월"

    layout = {**PLOTLY_LAYOUT}
    layout.update(
        height=340,
        title=dict(text=f"{media}{acc_tag}{yoy_tag}",
                   font=dict(size=14, color='#191F28'), x=0, xanchor='left'),
        margin=dict(t=48, l=52, r=24, b=36),
        xaxis=dict(showgrid=False, tickfont=dict(size=11, color='#8B95A1'), title=""),
        yaxis=dict(gridcolor='#F0F1F3', gridwidth=1, showline=False,
                   tickfont=dict(size=11, color='#8B95A1'), title=""),
        showlegend=False,
    )
    fig.update_layout(**layout)
    st.plotly_chart(fig, width='stretch')

    # ── KPI + 모델 구성 ──
    st.markdown('<div style="margin-top:8px"></div>', unsafe_allow_html=True)
    if pred:
        st.markdown(kpi_card_html(
            "다음 달 예측",
            f"{pred['click']:,}건",
            f"80% 구간 {pred['ci80_lower']:,} ~ {pred['ci80_upper']:,}  |  "
            f"95% 구간 {pred['ci95_lower']:,} ~ {pred['ci95_upper']:,}",
        ), unsafe_allow_html=True)

        # 예상 발송량 (서브 정보)
        if pred.get('sends'):
            delta_sends = pred['sends'] - pred['last_sends']
            sign = '+' if delta_sends >= 0 else ''
            _implied_ctr = (
                pred['click'] / pred['sends'] * 100 if pred['sends'] > 0 else 0
            )
            st.markdown(
                f'<div style="background:#F8F9FA;border-radius:8px;padding:10px 14px;'
                f'margin-top:6px;font-size:0.82rem;color:#4E5968;">'
                f'📮 <b>예상 발송량 {pred["sends"]:,}건</b> '
                f'(전월 {pred["last_sends"]:,}건, {sign}{delta_sends:,}) · '
                f'예측 CTR {_implied_ctr:.2f}%'
                f'</div>',
                unsafe_allow_html=True,
            )

        # 모델 구성 바
        if pred.get('models'):
            model_tags = " · ".join(
                f"{name} {pred['weights'][name]}%"
                for name in pred['models']
            )
            st.caption(
                f"🧮 모델: {model_tags}  |  "
                f"불확실성 ±{pred.get('std_pct', 20):.0f}%"
            )
    else:
        st.caption("3개월 이상 데이터가 필요합니다.")


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────

def render(df: pd.DataFrame):
    st.markdown(section_header(
        "매체별 클릭 예측",
        "WMA + 선형회귀 + 전년동월 앙상블 모델로 예측합니다. "
        "80%/95% 신뢰구간을 함께 제공합니다.",
    ), unsafe_allow_html=True)

    # ── 면책 안내 ──
    st.markdown(alert_card_html(
        "info",
        "예측 모델 안내",
        "본 예측은 과거 데이터 기반 <b>통계적 추정</b>이며, 시장 변화·이벤트 등 "
        "외부 요인을 반영하지 않습니다. 신뢰구간이 넓을수록 불확실성이 높으므로 "
        "<b>의사결정 참고용</b>으로 활용해 주세요.",
    ), unsafe_allow_html=True)

    all_media = sorted(df['매체명'].unique())
    top5 = df.groupby('매체명')['발송량'].sum().nlargest(5).index.tolist()

    # ── 컨트롤 ──
    ctrl1, ctrl2 = st.columns([3, 1])
    with ctrl1:
        selected = st.multiselect("매체 선택", options=all_media, default=top5)
    with ctrl2:
        vol_adjust = st.slider(
            "발송량 변동 (%)", min_value=-30, max_value=50, value=0, step=5,
            help="다음 달 발송량을 최근 대비 몇 % 변동시킬지 시뮬레이션",
        )

    if not selected:
        st.caption("분석할 매체를 하나 이상 선택해 주세요.")
        st.divider()
        return

    # ── 시뮬레이션 안내 ──
    if vol_adjust != 0:
        st.markdown(alert_card_html(
            "info",
            f"발송량 {vol_adjust:+d}% 시뮬레이션 적용 중",
            f"최근 가중 평균 발송량 대비 <b>{vol_adjust:+d}%</b> 변동을 가정한 예측입니다. "
            f"슬라이더를 0%로 되돌리면 기본 예측으로 복귀합니다.",
        ), unsafe_allow_html=True)

    # ── 월별 집계 ──
    filtered = df[df['매체명'].isin(selected)]
    monthly = (
        filtered.groupby(['년월', '매체명'])
        .agg({'발송량': 'sum', '클릭수': 'sum'})
        .reset_index()
    )
    monthly['CTR'] = calc_ctr(monthly)

    # ── 예측 + 백테스팅 ──
    results = []
    for media in selected:
        m_df = monthly[monthly['매체명'] == media].sort_values('년월').reset_index(drop=True)
        if m_df.empty:
            continue
        results.append((media, m_df, _predict_media(m_df, vol_adjust), _backtest(m_df)))

    if not results:
        st.caption("선택된 매체에 데이터가 없습니다.")
        st.divider()
        return

    # ── 인사이트 칩 ──
    render_insights(detect_prediction(results))

    # ── 모델 신뢰도 요약 ──
    accs_summary = [(m, a) for m, _, _, a in results if a is not None]
    if accs_summary:
        avg_acc = sum(a['accuracy'] for _, a in accs_summary) / len(accs_summary)
        avg_imp = sum(a['improvement'] for _, a in accs_summary) / len(accs_summary)
        grade_txt, grade_clr = _reliability_grade(avg_acc)

        parts = [f"평균 적중률 <b>{avg_acc:.0f}%</b> ({grade_txt})"]
        parts.append(f"검증 매체 {len(accs_summary)}개")

        if avg_imp > 1:
            parts.append(f"앙상블이 WMA 대비 <b>+{avg_imp:.1f}%p</b> 정확")

        n_with_yoy = sum(1 for _, _, p, _ in results if p and p.get('n_months', 0) >= 12)
        if n_with_yoy > 0:
            parts.append(f"전년동월 반영 {n_with_yoy}개 매체")

        st.markdown(
            f'<div style="background:#F8F9FA;border-radius:12px;padding:12px 18px;'
            f'margin:4px 0 16px;font-size:0.82rem;color:#4E5968;">'
            f'🎯 <span style="color:{grade_clr};font-weight:600;">'
            f'모델 신뢰도: {grade_txt}</span>'
            f'  —  {" · ".join(parts)}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── 2열 그리드: 매체별 개별 차트 ──
    for row_start in range(0, len(results), 2):
        row = results[row_start:row_start + 2]
        cols = st.columns(2, gap="large")
        for col_idx, (media, m_df, pred, acc) in enumerate(row):
            with cols[col_idx]:
                _render_media_card(media, m_df, pred, acc, all_media)
        st.markdown('<div style="margin-bottom:18px"></div>', unsafe_allow_html=True)

    # ── 적중률 상세 (접기) ──
    accs = [(m, a) for m, _, _, a in results if a is not None]
    if accs:
        st.markdown('<div style="margin-top:16px"></div>', unsafe_allow_html=True)
        with st.expander("📊 예측 적중률 상세 — WMA vs 앙상블 비교표"):
            for media, a in accs:
                grade = (
                    "우수" if a['mape'] < 15
                    else ("양호" if a['mape'] < 25
                          else ("보통" if a['mape'] < 40 else "참고용"))
                )
                imp_txt = ""
                if a['improvement'] > 0:
                    imp_txt = f" · 앙상블 +{a['improvement']:.1f}%p↑"
                elif a['improvement'] < -1:
                    imp_txt = f" · WMA가 {abs(a['improvement']):.1f}%p↑ 우세"

                st.markdown(
                    f"<b>{media}</b> — 앙상블 적중률 <b>{a['accuracy']:.0f}%</b> ({grade}) · "
                    f"WMA 적중률 {a['wma_accuracy']:.0f}% · "
                    f"검증 {a['n']}건{imp_txt}",
                    unsafe_allow_html=True,
                )
                st.dataframe(
                    pd.DataFrame(a['details']),
                    width='stretch',
                    hide_index=True,
                )

    st.divider()
