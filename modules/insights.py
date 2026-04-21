"""
룰 기반 마이크로 인사이트 엔진
각 분석 모듈의 필터링된 데이터를 받아 핵심 패턴을 감지하고
Toss-style 1줄 인사이트 칩으로 렌더링합니다.

원칙:
  - 팩트(숫자/트렌드)는 100% 코드 계산 → 기간 필터 정확 반영
  - 액션 한 줄은 룰 기반 → 빠르고 짧음
  - 칩 최대 4개 → 핵심만 노출
"""
import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING,
    CTR_CHANGE_THRESHOLD,
)
from modules.data_processing import (
    aggregate_by_weekday, calc_ctr,
    is_reliable, MIN_RELIABLE_SENDS,
)

_TONE = {
    'positive': {'border': COLOR_SUCCESS, 'bg': 'rgba(29,191,115,0.06)'},
    'negative': {'border': COLOR_DANGER,  'bg': 'rgba(255,71,87,0.06)'},
    'warning':  {'border': COLOR_WARNING, 'bg': 'rgba(255,159,67,0.06)'},
    'info':     {'border': '#3182F6',     'bg': 'rgba(49,130,246,0.06)'},
}


# ──────────────────────────────────────────────
# 렌더링
# ──────────────────────────────────────────────

def _chip(ins: dict) -> str:
    t = _TONE.get(ins.get('tone', 'info'), _TONE['info'])
    icon = ins.get('icon', '💡')
    fact = ins.get('fact', '')
    detail = ins.get('detail', '')
    action = ins.get('action', '')
    det = f'<div class="ic-detail">{detail}</div>' if detail else ''
    act = f'<div class="ic-action" style="border-color:{t["border"]}">→ {action}</div>' if action else ''
    return f'<div class="insight-chip" style="background:{t["bg"]};border-left:3px solid {t["border"]}">{det}<div class="ic-fact">{icon} {fact}</div>{act}</div>'


def render_insights(insights: list[dict], max_show: int = 4):
    """인사이트 칩을 2열 그리드로 렌더링"""
    if not insights:
        return
    show = insights[:max_show]
    for i in range(0, len(show), 2):
        row = show[i:i + 2]
        cols = st.columns(2)
        for ci, ins in enumerate(row):
            with cols[ci]:
                st.markdown(_chip(ins), unsafe_allow_html=True)


# ──────────────────────────────────────────────
# 요약 (summary)
# ──────────────────────────────────────────────

def detect_summary(cur_cost, prev_cost, cur_send, prev_send,
                   cur_click, prev_click, cur_ctr, prev_ctr, merged) -> list[dict]:
    ins = []

    # CTR 변동
    d = cur_ctr - prev_ctr
    if abs(d) >= CTR_CHANGE_THRESHOLD:
        up = d > 0
        ins.append(dict(
            icon='📈' if up else '📉',
            fact=f"전체 CTR {abs(d):.2f}%p {'상승' if up else '하락'}",
            detail=f"{prev_ctr:.2f}% → {cur_ctr:.2f}%",
            action="현 전략 유지 및 스케일업 검토" if up else "소재 변경 또는 타겟 재조정 필요",
            tone='positive' if up else 'negative',
        ))

    # 발송 vs 클릭 효율 비교
    if prev_send > 0 and prev_click > 0:
        sc = (cur_send - prev_send) / prev_send * 100
        cc = (cur_click - prev_click) / prev_click * 100
        if sc > 10 and cc < sc * 0.5:
            ins.append(dict(
                icon='⚠️', tone='warning',
                fact=f"발송 +{sc:.0f}% 대비 클릭 +{cc:.0f}% — 효율 저하",
                detail="발송 증가분 대비 클릭 반응 미흡",
                action="타겟 정밀화 또는 소재 리프레시 권장",
            ))
        elif cc > sc + 15:
            ins.append(dict(
                icon='🎯', tone='positive',
                fact=f"클릭 효율 개선 (클릭 +{cc:.0f}% > 발송 +{sc:.0f}%)",
                detail="타겟·소재 조합 효과 상승 중",
                action="현 조합 유지, 예산 확대 적기",
            ))

    # 매체 집중도 경고
    if not merged.empty and '클릭수_당월' in merged.columns:
        total = merged['클릭수_당월'].sum()
        top_rows = merged.nlargest(1, '클릭수_당월')
        if total > 0 and not top_rows.empty:
            top = top_rows.iloc[0]
            share = top['클릭수_당월'] / total * 100
            if share > 60:
                ins.append(dict(
                    icon='🏢', tone='warning',
                    fact=f"{top['매체명']} 클릭 비중 {share:.0f}% — 과도 집중",
                    detail="단일 매체 의존도 높음",
                    action="리스크 분산 위해 차순위 매체 확대 권장",
                ))

    # 비용↑ 성과↓ 경고
    if prev_cost > 0 and prev_click > 0:
        cost_c = (cur_cost - prev_cost) / prev_cost * 100
        clk_c = (cur_click - prev_click) / prev_click * 100
        if cost_c > 10 and clk_c < 0:
            ins.append(dict(
                icon='💸', tone='negative',
                fact=f"비용 +{cost_c:.0f}% 증가, 클릭 {clk_c:.0f}% 감소",
                detail="예산 증가에도 성과 하락",
                action="비효율 매체 축소 및 캠페인 점검",
            ))

    return ins


# ──────────────────────────────────────────────
# 트렌드 (trend)
# ──────────────────────────────────────────────

def _streak(values, direction='down'):
    """최근부터 연속 상승/하락 횟수"""
    cnt = 0
    for i in range(len(values) - 1, 0, -1):
        if direction == 'down' and values[i] < values[i - 1]:
            cnt += 1
        elif direction == 'up' and values[i] > values[i - 1]:
            cnt += 1
        else:
            break
    return cnt


def detect_trend(monthly_df: pd.DataFrame) -> list[dict]:
    ins = []
    if len(monthly_df) < 3:
        return ins

    for metric, label in [('CTR', 'CTR'), ('클릭수', '클릭수')]:
        if metric not in monthly_df.columns:
            continue

        # CTR 분석은 발송량 충분한 월만 대상 (소량 발송 월의 CTR 왜곡 방지)
        if metric == 'CTR' and '발송량' in monthly_df.columns:
            reliable_df = monthly_df[monthly_df['발송량'] >= MIN_RELIABLE_SENDS]
            if len(reliable_df) < 3:
                continue
            vals = reliable_df[metric].values
        else:
            vals = monthly_df[metric].values

        for direction, emoji, tone_val, action_msg in [
            ('down', '📉', 'negative', '하락 원인 분석 및 소재/타겟 변경 검토'),
            ('up',   '📈', 'positive', '현 전략 유지, 스케일업 적기'),
        ]:
            s = _streak(vals, direction)
            if s < 2:
                continue
            start_v, end_v = vals[-(s + 1)], vals[-1]
            pct = min(max((end_v - start_v) / start_v * 100 if start_v != 0 else 0, -999), 999)
            det = f"{start_v:.2f}% → {end_v:.2f}%" if metric == 'CTR' else f"{start_v:,.0f} → {end_v:,.0f}"
            word = '상승' if direction == 'up' else '하락'
            ins.append(dict(
                icon=emoji, tone=tone_val,
                fact=f"{label} {s + 1}개월 연속 {word} ({pct:+.1f}%)",
                detail=det, action=action_msg,
            ))

    # 변동성 (발송량 충분한 월만 대상)
    if len(monthly_df) >= 4 and 'CTR' in monthly_df.columns:
        if '발송량' in monthly_df.columns:
            rel = monthly_df[monthly_df['발송량'] >= MIN_RELIABLE_SENDS]
        else:
            rel = monthly_df
        if len(rel) >= 4:
            v = rel['CTR'].values
            m = np.mean(v)
            if m > 0:
                cv = np.std(v) / m * 100
                if cv > 30:
                    ins.append(dict(
                        icon='🎢', tone='warning',
                        fact=f"CTR 변동성 높음 (변동계수 {cv:.0f}%)",
                        detail=f"최소 {min(v):.2f}% ~ 최대 {max(v):.2f}%",
                        action="발송 조건 표준화로 성과 안정화 필요",
                    ))

    return ins


# ──────────────────────────────────────────────
# 예측 (prediction)
# ──────────────────────────────────────────────

def detect_prediction(results: list) -> list[dict]:
    """results: [(media, m_df, pred_dict|None, acc_dict|None), ...]"""
    ins = []

    changes = []
    for media, _, pred, _ in results:
        if not pred or pred['last_click'] == 0:
            continue
        pct = (pred['click'] - pred['last_click']) / pred['last_click'] * 100
        changes.append((media, pred, pct))

    changes.sort(key=lambda x: abs(x[2]), reverse=True)
    for media, pred, pct in changes[:2]:
        if abs(pct) < 10:
            continue
        up = pct > 0
        ins.append(dict(
            icon='🔮' if up else '⚠️',
            tone='positive' if up else 'warning',
            fact=f"{media} 클릭 {abs(pct):.0f}% {'증가' if up else '감소'} 예측",
            detail=f"{pred['last_click']:,} → {pred['click']:,}건",
            action='예산 확대 검토' if up else '소재 교체 또는 타겟 재설정 권장',
        ))

    # 전체 합산
    t_cur = sum(p['last_click'] for _, _, p, _ in results if p)
    t_pred = sum(p['click'] for _, _, p, _ in results if p)
    if t_cur > 0:
        tp = (t_pred - t_cur) / t_cur * 100
        if abs(tp) > 5:
            up = tp > 0
            ins.append(dict(
                icon='📊', tone='positive' if up else 'warning',
                fact=f"전체 예상 클릭 {abs(tp):.0f}% {'증가' if up else '감소'}",
                detail=f"합계: {t_cur:,} → {t_pred:,}건",
                action='성과 상승 추세 유지' if up else '전반적 효율 점검 필요',
            ))

    # ── 모델 신뢰도 안내 ──
    accs_avail = [a for _, _, _, a in results if a is not None]
    if accs_avail:
        avg_acc = sum(a['accuracy'] for a in accs_avail) / len(accs_avail)
        if avg_acc < 55:
            ins.append(dict(
                icon='⚠️', tone='warning',
                fact=f"예측 모델 적중률 {avg_acc:.0f}% — 참고용 수준",
                detail="데이터가 축적되면 정확도가 개선됩니다",
                action="현 시점 예측은 방향성 참고만 권장",
            ))

        # 앙상블 개선 효과
        improvements = [a.get('improvement', 0) for a in accs_avail]
        avg_imp = sum(improvements) / len(improvements) if improvements else 0
        if avg_imp > 3:
            ins.append(dict(
                icon='🧮', tone='positive',
                fact=f"앙상블 모델이 단순 WMA 대비 {avg_imp:.1f}%p 정확",
                detail="선형회귀 + 전년동월 보정이 예측력 향상에 기여",
                action="모델이 유효하게 작동 중",
            ))

    return ins


# ──────────────────────────────────────────────
# 이상치 (anomaly)
# ──────────────────────────────────────────────

def detect_anomaly_summary(all_count: int, media_count: int, threshold: float) -> list[dict]:
    total = all_count + media_count
    if total == 0:
        return [dict(
            icon='✅', tone='positive',
            fact="이상치 미감지 — 모든 지표 안정",
            detail=f"감지 기준 {threshold}σ",
            action="정상 범위 내, 모니터링 유지",
        )]
    if total <= 3:
        return [dict(
            icon='🔍', tone='info',
            fact=f"이상치 {total}건 감지 (경미)",
            detail=f"전체 {all_count}건 · 매체별 {media_count}건",
            action="일회성 이벤트 가능성, 추이 관찰",
        )]
    return [dict(
        icon='🚨', tone='warning' if total < 10 else 'negative',
        fact=f"이상치 {total}건 감지 — 점검 필요",
        detail=f"전체 {all_count}건 · 매체별 {media_count}건",
        action="캠페인 세팅 또는 외부 요인 점검 필요",
    )]


# ──────────────────────────────────────────────
# 예산 배분 (budget)
# ──────────────────────────────────────────────

def detect_budget(result: pd.DataFrame) -> list[dict]:
    ins = []
    if result.empty or len(result) < 2:
        return ins

    best, worst = result.iloc[0], result.iloc[-1]
    has_volume = '발송량' in result.columns

    # 효율 격차 (신뢰도 참고 표시)
    if worst['효율점수'] > 0:
        ratio = best['효율점수'] / worst['효율점수']
        if ratio > 1.5:
            note = ""
            if has_volume:
                if not is_reliable(best['발송량']) or not is_reliable(worst['발송량']):
                    note = " ⚠️참고: 일부 매체 발송량 부족"
            ins.append(dict(
                icon='💡', tone='positive',
                fact=f"{best['매체명']} 효율 {worst['매체명']} 대비 {ratio:.1f}배",
                detail=f"CPC {best['CPC']:,.0f}원 vs {worst['CPC']:,.0f}원{note}",
                action=f"{best['매체명']} 비중 확대, {worst['매체명']} 축소 권장",
            ))

    # CPC 이상치 (발송량 충분한 매체만 경고)
    reliable_result = result[result['발송량'] >= MIN_RELIABLE_SENDS] if has_volume else result
    if not reliable_result.empty:
        mean_cpc = reliable_result['CPC'].mean()
        for _, row in reliable_result.iterrows():
            if row['CPC'] > mean_cpc * 2:
                ins.append(dict(
                    icon='💸', tone='negative',
                    fact=f"{row['매체명']} CPC 평균 대비 {row['CPC'] / mean_cpc:.1f}배 높음",
                    detail=f"CPC {row['CPC']:,.0f}원 (평균 {mean_cpc:,.0f}원)",
                    action="타겟 축소 또는 대체 매체 테스트 권장",
                ))
                break

    return ins


# ──────────────────────────────────────────────
# 예산 증액 시뮬레이터 (budget_simulator)
# ──────────────────────────────────────────────

def detect_simulator(result: pd.DataFrame, increase: float, current_monthly: float) -> list[dict]:
    ins = []
    if result.empty:
        return ins

    # 가장 효율적인 매체 (증액 CPC 최저)
    valid = result[result['증액CPC'] > 0]
    if not valid.empty:
        best = valid.loc[valid['증액CPC'].idxmin()]
        ins.append(dict(
            icon='🎯', tone='positive',
            fact=f"최고 효율 매체: {best['매체명']} (CPC {best['증액CPC']:,.0f}원)",
            detail=f"증액 {best['증액분'] / 10000:,.0f}만원 → 클릭 +{int(best['추가클릭']):,}회",
            action=f"{best['매체명']} 비중 확대 시 증액 효율 극대화",
        ))

    # 클릭 기여도 집중 경고
    total_add = result['추가클릭'].sum()
    if total_add > 0:
        top_media = result.nlargest(1, '추가클릭').iloc[0]
        share = top_media['추가클릭'] / total_add * 100
        if share > 60:
            ins.append(dict(
                icon='⚠️', tone='warning',
                fact=f"추가 클릭의 {share:.0f}%가 {top_media['매체명']}에 집중",
                detail="단일 매체 의존 리스크 존재",
                action="2~3개 매체로 분산 배분 권장",
            ))

    # 증액 규모 대비 효과
    if current_monthly > 0:
        inc_pct = increase / current_monthly * 100
        click_pct = (total_add / result['월클릭'].sum() * 100) if result['월클릭'].sum() > 0 else 0
        if click_pct > inc_pct * 0.8:
            ins.append(dict(
                icon='📈', tone='positive',
                fact=f"예산 +{inc_pct:.0f}% → 클릭 +{click_pct:.0f}% (효율적 증액)",
                detail="투입 대비 산출 비율 양호",
                action="현 수준까지는 증액 ROI 긍정적",
            ))
        elif click_pct < inc_pct * 0.5:
            ins.append(dict(
                icon='💸', tone='warning',
                fact=f"예산 +{inc_pct:.0f}% → 클릭 +{click_pct:.0f}% (체감 효율 저하)",
                detail="투입 대비 산출 비율 낮음",
                action="증액 규모 축소 또는 고효율 매체 집중 권장",
            ))

    # ── 수확체감 영향 ──
    if '추가클릭_선형' in result.columns:
        total_linear = int(result['추가클릭_선형'].sum())
        if total_linear > 0 and total_add < total_linear * 0.85:
            reduction_pct = (1 - total_add / total_linear) * 100
            ins.append(dict(
                icon='📉', tone='info',
                fact=f"수확체감 효과: 선형 대비 -{reduction_pct:.0f}% 보정 적용",
                detail=f"선형 추정 {total_linear:,}회 → 실효 추정 {int(total_add):,}회",
                action="증액 폭을 줄이면 단위당 효율이 높아집니다",
            ))

    return ins


# ──────────────────────────────────────────────
# 히트맵 (heatmap)
# ──────────────────────────────────────────────

def detect_heatmap(df: pd.DataFrame) -> list[dict]:
    ins = []
    if '짧은_요일' not in df.columns:
        return ins

    day = aggregate_by_weekday(df)
    if day.empty or day['발송량'].sum() == 0:
        return ins

    # 발송량 충분한 요일만 CTR 비교 (소량 요일의 CTR 왜곡 방지)
    day_reliable = day[day['발송량'] >= MIN_RELIABLE_SENDS]
    if len(day_reliable) < 2:
        return ins

    best = day_reliable.loc[day_reliable['CTR'].idxmax()]
    worst = day_reliable.loc[day_reliable['CTR'].idxmin()]
    gap = best['CTR'] - worst['CTR']

    if gap > CTR_CHANGE_THRESHOLD:
        ins.append(dict(
            icon='📅', tone='positive',
            fact=f"최적 요일: {best['짧은_요일']}요일 (CTR {best['CTR']:.2f}%, 발송 {best['발송량']:,.0f}건)",
            detail=f"최저 {worst['짧은_요일']}요일 ({worst['CTR']:.2f}%) 대비 +{gap:.2f}%p",
            action=f"{best['짧은_요일']}요일 발송 비중 확대 권장",
        ))

    # 매체별 최적 요일 다양성 — 발송량 충분한 데이터만 대상
    if '매체명' in df.columns:
        mg = (
            df.groupby(['매체명', '짧은_요일'], sort=False)
            .agg({'발송량': 'sum', '클릭수': 'sum'})
            .reset_index()
        )
        mg['CTR'] = calc_ctr(mg)
        mg = mg[mg['발송량'] >= MIN_RELIABLE_SENDS]

        media_best_days = {}
        for media, grp in mg.groupby('매체명'):
            if grp.empty:
                continue
            media_best_days[media] = grp.loc[grp['CTR'].idxmax(), '짧은_요일']

        days_set = set(media_best_days.values())
        if len(days_set) >= 3 and len(media_best_days) >= 3:
            ins.append(dict(
                icon='🔀', tone='info',
                fact="매체별 최적 요일이 서로 다름",
                detail=f"{len(days_set)}개 요일에 분산",
                action="매체별 발송 요일 개별 최적화 권장",
            ))

    return ins
