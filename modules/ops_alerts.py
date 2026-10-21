"""
운영 체크 (Operational Alerts)
──────────────────────────────
퍼포먼스 마케터가 출근해서 가장 먼저 확인하는 것들만 모았다.
"지금 조치가 필요한 캠페인이 있는가?"

감지 항목:
  🔴 성과 급락   — 같은 매체 직전 회차 대비 CTR 30%+ 하락
  🟠 발송 미달   — 요청 수량 대비 실발송 95% 미만 (매체사 클레임 대상)
  🟡 트래킹 누락 — 발송은 됐는데 클릭이 0 또는 미입력 (측정 불가)
  🟢 성과 급등   — 직전 대비 30%+ 상승 (성공 요인 복제 대상)

설계:
  - 최근 N일만 본다 (기본 45일). 오래된 건 이미 지나간 일.
  - 카드 하나 = 조치 하나. 근거 수치와 날짜를 함께 보여준다.
  - 이상 없으면 "확인 필요 없음"을 명시해 안심시킨다.
"""
import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_BG, COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING, COLOR_BLUE,
    compact_num, MIN_SENDS_FOR_CTR_CLAIM,
)
from modules.ui_helpers import esc_html_safe

_WINDOW_DAYS = 45       # 최근 며칠까지 볼 것인가
_DROP_PCT = 30.0        # 급락 판정 기준 (%)
_SURGE_PCT = 30.0       # 급등 판정 기준 (%)
_DELIVERY_MIN = 95.0    # 발송 이행률 하한 (%)
_MAX_COMPARE_GAP_DAYS = 60   # 직전 회차와 이 이상 벌어지면 비교하지 않음


def detect(df: pd.DataFrame, *, window_days: int = _WINDOW_DAYS) -> list[dict]:
    """조치가 필요한 항목 목록 반환 (심각도 순)."""
    if df is None or df.empty or '날짜' not in df.columns:
        return []
    d = df.copy()
    d['_d'] = pd.to_datetime(d['날짜'], errors='coerce')
    d = d.dropna(subset=['_d'])
    if d.empty:
        return []
    latest = d['_d'].max()
    recent = d[d['_d'] >= latest - pd.Timedelta(days=window_days)]
    if recent.empty:
        return []

    # 메시징만 (DA/CPA는 발송·클릭 의미가 달라 제외)
    if '광고상품' in d.columns:
        msg_all = d[~d['광고상품'].astype(str).str.upper().isin(['DA', 'CPA'])].copy()
        msg_recent = recent[~recent['광고상품'].astype(str).str.upper().isin(['DA', 'CPA'])].copy()
    else:
        msg_all, msg_recent = d.copy(), recent.copy()

    for frame in (msg_all, msg_recent):
        if not frame.empty:
            frame['_send'] = pd.to_numeric(frame['발송량'], errors='coerce').fillna(0)
            frame['_click_raw'] = pd.to_numeric(frame['클릭수'], errors='coerce')
            frame['_ctr'] = np.where(
                frame['_send'] > 0,
                frame['_click_raw'].fillna(0) / frame['_send'].clip(lower=1) * 100,
                np.nan,
            )

    alerts: list[dict] = []
    media_col = '매체명' if '매체명' in msg_all.columns else '매체'

    # ── 성과 급락 / 급등 (같은 매체 직전 회차 대비) ──
    if media_col in msg_all.columns and not msg_recent.empty:
        qual_all = msg_all[msg_all['_send'] >= MIN_SENDS_FOR_CTR_CLAIM]
        for media, g in qual_all.groupby(media_col):
            g = g.sort_values('_d')
            if len(g) < 2:
                continue
            last, prev = g.iloc[-1], g.iloc[-2]
            if last['_d'] < latest - pd.Timedelta(days=window_days):
                continue  # 최근 집행이 아니면 지나간 일
            if not (prev['_ctr'] > 0) or pd.isna(last['_ctr']):
                continue
            # 직전 회차가 너무 오래됐으면 비교 자체가 무의미 (시즌·타겟·시장이 달라짐)
            gap_days = int((last['_d'] - prev['_d']).days)
            if gap_days > _MAX_COMPARE_GAP_DAYS:
                continue
            change = (last['_ctr'] / prev['_ctr'] - 1) * 100
            if change <= -_DROP_PCT:
                alerts.append({
                    'sev': 0, 'tone': COLOR_DANGER, 'icon': '🔴', 'kind': '성과 급락',
                    'title': f'{media} — CTR {change:+.0f}%',
                    'body': (f'{prev["_ctr"]:.2f}% → <b>{last["_ctr"]:.2f}%</b> '
                             f'(직전 {prev["_d"]:%m/%d} → 최근 {last["_d"]:%m/%d}, {gap_days}일 간격) · '
                             f'발송 {compact_num(float(last["_send"]), "건")}'),
                    'action': '타겟·소재가 직전과 달라졌는지 확인하고, 동일 조건으로 재집행 검토',
                })
            elif change >= _SURGE_PCT:
                alerts.append({
                    'sev': 3, 'tone': COLOR_SUCCESS, 'icon': '🟢', 'kind': '성과 급등',
                    'title': f'{media} — CTR {change:+.0f}%',
                    'body': (f'{prev["_ctr"]:.2f}% → <b>{last["_ctr"]:.2f}%</b> '
                             f'({last["_d"]:%m/%d}) · 발송 {compact_num(float(last["_send"]), "건")}'),
                    'action': '이번 회차 타겟·소재를 다른 매체에도 확장 적용',
                })

    # ── 발송 미달 (요청 대비 실발송) ──
    if '요청수량' in msg_recent.columns and '발송건' in msg_recent.columns:
        s = msg_recent.copy()
        s['_req'] = pd.to_numeric(s['요청수량'], errors='coerce').fillna(0)
        s['_act'] = pd.to_numeric(s['발송건'], errors='coerce').fillna(0)
        s = s[(s['_req'] > 0) & (s['_act'] > 0)]
        s['_rate'] = s['_act'] / s['_req'] * 100
        for _, r in s[s['_rate'] < _DELIVERY_MIN].sort_values('_d', ascending=False).head(5).iterrows():
            alerts.append({
                'sev': 1, 'tone': COLOR_WARNING, 'icon': '🟠', 'kind': '발송 미달',
                'title': f'{r.get(media_col, "—")} — 이행률 {r["_rate"]:.0f}%',
                'body': (f'요청 {int(r["_req"]):,}건 → 실발송 <b>{int(r["_act"]):,}건</b> '
                         f'({int(r["_req"] - r["_act"]):,}건 부족) · {r["_d"]:%m/%d}'),
                'action': '매체사에 미발송 사유 확인 및 잔여 물량 보전 요청',
            })

    # ── 트래킹 누락 (발송됐는데 클릭 0/미입력) ──
    if not msg_recent.empty:
        t = msg_recent[msg_recent['_send'] >= MIN_SENDS_FOR_CTR_CLAIM]
        miss = t[t['_click_raw'].isna() | (t['_click_raw'] == 0)]
        if not miss.empty:
            by_media = miss.groupby(media_col).agg(
                n=('_send', 'size'), send=('_send', 'sum'),
                last=('_d', 'max'),
            ).reset_index().sort_values('send', ascending=False)
            for _, r in by_media.head(3).iterrows():
                alerts.append({
                    'sev': 2, 'tone': COLOR_BLUE, 'icon': '🟡', 'kind': '트래킹 누락',
                    'title': f'{r[media_col]} — 클릭 0건 {int(r["n"])}건',
                    'body': (f'발송 {compact_num(float(r["send"]), "건")}인데 클릭 데이터가 없음 '
                             f'(최근 {r["last"]:%m/%d}) · 성과 측정 불가'),
                    'action': '단축 URL 트래킹 설정 여부 확인 — 미설정이면 해당 매체 성과 평가 제외',
                })

    alerts.sort(key=lambda a: a['sev'])
    return alerts


def render_inline(df: pd.DataFrame, *, max_items: int = 5) -> None:
    """한눈에 보기 상단에 삽입하는 축약 뷰."""
    try:
        alerts = detect(df)
    except Exception:
        return

    st.markdown(
        f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:6px;letter-spacing:-0.01em;">🚨 운영 체크'
        f'<span style="font-weight:400;color:{COLOR_TEXT_SEC};font-size:0.75rem;"> — '
        f'최근 {_WINDOW_DAYS}일 내 조치가 필요한 항목</span></div>',
        unsafe_allow_html=True,
    )

    if not alerts:
        st.markdown(
            f'<div style="border:1px solid {COLOR_BORDER_SUBTLE};border-left:3px solid {COLOR_SUCCESS};'
            f'border-radius:8px;padding:12px 16px;background:{COLOR_CARD};margin-bottom:16px;">'
            f'<div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:600;">'
            f'✅ 조치가 필요한 항목이 없습니다</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-top:3px;">'
            f'성과 급락·발송 미달·트래킹 누락 모두 정상 범위입니다.</div></div>',
            unsafe_allow_html=True,
        )
        return

    # 심각도별 카운트 배지
    n_crit = sum(1 for a in alerts if a['sev'] == 0)
    n_warn = sum(1 for a in alerts if a['sev'] in (1, 2))
    n_good = sum(1 for a in alerts if a['sev'] == 3)
    chips = []
    if n_crit:
        chips.append(f'<span style="color:{COLOR_DANGER};font-weight:700;">급락 {n_crit}</span>')
    if n_warn:
        chips.append(f'<span style="color:{COLOR_WARNING};font-weight:700;">점검 {n_warn}</span>')
    if n_good:
        chips.append(f'<span style="color:{COLOR_SUCCESS};font-weight:700;">호조 {n_good}</span>')
    st.markdown(
        f'<div style="font-size:0.72rem;color:{COLOR_TEXT_SEC};margin-bottom:8px;">'
        + ' · '.join(chips) + '</div>',
        unsafe_allow_html=True,
    )

    for a in alerts[:max_items]:
        st.markdown(
            f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {a["tone"]};'
            f'border-radius:8px;padding:12px 16px;background:{COLOR_CARD};margin-bottom:8px;">'
            f'  <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px;">'
            f'    <span style="font-size:0.62rem;color:{a["tone"]};background:{COLOR_BG};'
            f'padding:2px 8px;border-radius:999px;font-weight:700;">{a["icon"]} {a["kind"]}</span>'
            f'    <span style="font-size:0.88rem;color:{COLOR_TEXT};font-weight:700;">'
            f'{esc_html_safe(a["title"])}</span>'
            f'  </div>'
            f'  <div style="font-size:0.76rem;color:{COLOR_TEXT_SEC};line-height:1.5;">{a["body"]}</div>'
            f'  <div style="font-size:0.72rem;color:{a["tone"]};margin-top:5px;font-weight:600;">'
            f'→ {esc_html_safe(a["action"])}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    if len(alerts) > max_items:
        with st.expander(f'나머지 {len(alerts) - max_items}건 더 보기'):
            for a in alerts[max_items:]:
                st.markdown(
                    f'<div style="border-left:2px solid {a["tone"]};padding:6px 12px;'
                    f'margin-bottom:6px;">'
                    f'<b style="font-size:0.8rem;color:{COLOR_TEXT};">{a["icon"]} '
                    f'{esc_html_safe(a["title"])}</b>'
                    f'<div style="font-size:0.74rem;color:{COLOR_TEXT_SEC};">{a["body"]}</div></div>',
                    unsafe_allow_html=True,
                )
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
