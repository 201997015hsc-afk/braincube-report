"""
Section 5: 영업 인사이트 — 자동 생성 토킹포인트.
"""
import streamlit as st
import pandas as pd

from modules.config import BRAND_PRIMARY

from ._classifiers import (
    _classify_money_amount,
    _CTA_PATTERNS,
    _EMOJI_RE,
    _NUMBER_RE,
)


# ──────────────────────────────────────────────
# Section 5: 영업 인사이트 — 자동 생성 토킹포인트
# ──────────────────────────────────────────────

def _render_sales_insights(msg_df: pd.DataFrame, raw_df: pd.DataFrame,
                           industry: str, advertiser: str = None):
    """데이터 기반 문구 최적화 인사이트 + 영업 토킹포인트.

    ⚠ 신뢰도 원칙:
    - 카테고리별 최소 표본 수(n≥5) 검증
    - 명확한 효과 크기(+15% 이상)만 보고
    - 상관관계/인과관계 구분 표기
    - 키워드 기반 추천 제거 (단어 수준은 의미 없음)
    """
    st.markdown(
        '<div style="font-size:0.92rem;font-weight:700;color:#191F28;'
        'margin-bottom:8px;">💬 문구 최적화 인사이트</div>',
        unsafe_allow_html=True,
    )

    points: list[str] = []

    if msg_df.empty or raw_df.empty:
        st.caption('인사이트 생성을 위한 데이터가 부족합니다.')
        return

    # 컬럼명 호환 + 가중 CTR 공용 헬퍼
    from modules._analytics_helpers import weighted_ctr as _wctr, get_sends_col
    sends_col = get_sends_col(raw_df)
    overall_ctr = _wctr(raw_df)
    n_msgs = len(msg_df)

    # ── 1. Top vs Bottom 격차 (n≥8 필요) ──
    if n_msgs >= 8:
        q = max(n_msgs // 4, 2)
        top_ctr = float(msg_df.head(q)['CTR'].mean())
        bot_ctr = float(msg_df.tail(q)['CTR'].mean())
        if bot_ctr > 0 and top_ctr > bot_ctr * 1.3:
            ratio = top_ctr / bot_ctr
            points.append(
                f"상위 25% 문구 CTR(<b>{top_ctr:.2f}%</b>)이 하위 25%(<b>{bot_ctr:.2f}%</b>) 대비 "
                f"<b>{ratio:.1f}배</b> — 동일 조건에서도 문구 차이만으로 성과 격차 큼."
            )

    # ── 2. 혜택 금액대별 최적 구간 (카테고리당 n≥3) ──
    if '메시지' in raw_df.columns:
        df_m = raw_df.copy()
        df_m['_money'] = df_m['메시지'].apply(_classify_money_amount)
        money_df = df_m[df_m['_money'].notna()]
        if len(money_df) >= 10:
            order = ['~1만원', '1~3만원', '3~5만원', '5~10만원', '10만원+']
            bucket_rows = []
            for b in order:
                sub = money_df[money_df['_money'] == b]
                if len(sub) >= 3:
                    bucket_rows.append({'b': b, 'ctr': _wctr(sub), 'n': len(sub)})
            if len(bucket_rows) >= 2:
                bkt = pd.DataFrame(bucket_rows).sort_values('ctr', ascending=False)
                best, worst = bkt.iloc[0], bkt.iloc[-1]
                if worst['ctr'] > 0 and best['ctr'] / worst['ctr'] > 1.15:
                    lift = ((best['ctr'] / worst['ctr']) - 1) * 100
                    points.append(
                        f"혜택 <b>{best['b']}</b> 구간 CTR <b>{best['ctr']:.2f}%</b>가 "
                        f"<b>{worst['b']}</b>({worst['ctr']:.2f}%) 대비 <b>+{lift:.0f}%</b> 높음 "
                        f"(표본 {int(best['n'])}건)."
                    )

    # ── 3. CTA 유형별 최적 (카테고리당 n≥5) ──
    cta_rows = []
    for cta_name, keywords in _CTA_PATTERNS.items():
        mask = raw_df['메시지'].apply(
            lambda t: any(kw.lower() in str(t).lower() for kw in keywords)
        )
        sub = raw_df[mask]
        if len(sub) >= 5:
            cta_rows.append({'cta': cta_name, 'ctr': _wctr(sub), 'n': len(sub)})
    if len(cta_rows) >= 2:
        cta_df = pd.DataFrame(cta_rows).sort_values('ctr', ascending=False)
        best_cta = cta_df.iloc[0]
        others_mean = cta_df.iloc[1:]['ctr'].mean()
        if others_mean > 0 and best_cta['ctr'] / others_mean > 1.15:
            lift = ((best_cta['ctr'] / others_mean) - 1) * 100
            points.append(
                f"<b>{best_cta['cta']}</b> CTA가 CTR <b>{best_cta['ctr']:.2f}%</b>로 "
                f"다른 CTA 평균 대비 <b>+{lift:.0f}%</b> 높음 "
                f"(표본 {int(best_cta['n'])}건)."
            )

    # ── 4. 이모지/숫자 효과 (유의한 것만 ±15%) ──
    for name, check_fn in [
        ('이모지', lambda t: bool(_EMOJI_RE.search(t)) if isinstance(t, str) else False),
        ('숫자', lambda t: bool(_NUMBER_RE.search(t)) if isinstance(t, str) else False),
    ]:
        with_mask = raw_df['메시지'].apply(check_fn)
        with_sub = raw_df[with_mask]
        wo_sub = raw_df[~with_mask]
        if len(with_sub) >= 5 and len(wo_sub) >= 5:
            ctr_w = _wctr(with_sub)
            ctr_wo = _wctr(wo_sub)
            if ctr_wo > 0:
                diff_pct = ((ctr_w / ctr_wo) - 1) * 100
                if abs(diff_pct) >= 15:
                    direction = '높음' if diff_pct > 0 else '낮음'
                    sign = '+' if diff_pct > 0 else ''
                    points.append(
                        f"<b>{name}</b> 포함 문구가 미포함 대비 CTR <b>{sign}{diff_pct:.0f}%</b> {direction} "
                        f"({ctr_w:.2f}% vs {ctr_wo:.2f}%, 표본 {len(with_sub)}/{len(wo_sub)}건)."
                    )

    # ── 5. 문구 길이 (상관관계 주의 표기) ──
    if '문구길이' in raw_df.columns:
        lens = raw_df[raw_df['문구길이'] > 0].copy()
        if len(lens) >= 10:
            bins = [0, 30, 60, 100, 200, float('inf')]
            labels_len = ['~30자', '31~60자', '61~100자', '101~200자', '200자+']
            lens['_b'] = pd.cut(lens['문구길이'], bins=bins, labels=labels_len, right=True)
            bucket_rows = []
            for b in labels_len:
                sub = lens[lens['_b'] == b]
                if len(sub) >= 3:
                    bucket_rows.append({'b': b, 'ctr': _wctr(sub), 'n': len(sub)})
            if len(bucket_rows) >= 2:
                bkt = pd.DataFrame(bucket_rows).sort_values('ctr', ascending=False)
                best = bkt.iloc[0]
                if best['ctr'] > overall_ctr * 1.1:
                    points.append(
                        f"<b>{best['b']}</b> 길이 문구가 CTR <b>{best['ctr']:.2f}%</b>로 "
                        f"전체 평균({overall_ctr:.2f}%) 대비 우수 "
                        f"<span style='color:#8B95A1;font-size:0.78rem;'>"
                        f"(※ 상관관계일 수 있음 — 긴 문구는 혜택·CTA 설명이 풍부해서일 가능성)</span>"
                    )

    # ── 6. 업종 평균 대비 자사 (클라이언트 선택 시, n≥3) ──
    if advertiser and not raw_df.empty:
        brand_col = '_브랜드' if '_브랜드' in raw_df.columns else '광고주'
        if brand_col in raw_df.columns:
            adv_data = raw_df[raw_df[brand_col] == advertiser]
        else:
            adv_data = pd.DataFrame()
        if not adv_data.empty and len(adv_data) >= 3 and len(raw_df) > len(adv_data):
            adv_ctr = _wctr(adv_data)
            ind_ctr = overall_ctr
            if adv_ctr > ind_ctr * 1.1:
                points.append(
                    f"귀사 문구 CTR <b>{adv_ctr:.2f}%</b>가 {industry} 업종 평균 "
                    f"<b>{ind_ctr:.2f}%</b> 대비 <b>+{adv_ctr - ind_ctr:.2f}%p</b> 우수."
                )
            elif ind_ctr > adv_ctr * 1.1:
                gap = ind_ctr - adv_ctr
                points.append(
                    f"귀사 문구 CTR이 업종 평균 대비 <b>-{gap:.2f}%p</b>. "
                    f"위 패턴(금액대·CTA·이모지)을 참고해 문구 재설계 검토."
                )

    # ── 7. 문구 변동성 → A/B 테스트 권장 ──
    if n_msgs >= 5:
        ctr_std = float(msg_df['CTR'].std())
        if ctr_std > overall_ctr * 0.5:
            points.append(
                f"문구 간 CTR 편차 큼 (표준편차 {ctr_std:.2f}%p) — "
                f"같은 매체·타겟이라도 문구 차이로 성과 크게 갈림. A/B 테스트 권장."
            )

    if not points:
        st.caption('유의미한 인사이트를 도출할 만큼 표본이 충분하지 않습니다. (카테고리당 최소 5건 필요)')
        return

    for p in points:
        st.markdown(
            f'<div style="background:#FFF8F0;border-left:3px solid {BRAND_PRIMARY};'
            f'border-radius:0 10px 10px 0;padding:12px 16px;margin-bottom:8px;'
            f'font-size:0.84rem;color:#4E5968;line-height:1.6;">{p}</div>',
            unsafe_allow_html=True,
        )
