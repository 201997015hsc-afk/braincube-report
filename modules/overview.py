"""
한눈에 보기 (Overview)
────────────────────
진입 즉시 30초 안에 답을 주는 페이지.

구성:
  1) 페이지 헤더 (광고주명 · 데이터 신선도)
  2) 4 KPI 카드 — 발송량 / 클릭수 / CTR / 광고비
       각 카드: 당월값 + 전월 대비 + 업종 평균 대비 (가능 시)
  3) 이번 달 한 줄 요약 (자동 생성)
  4) 주요 변화 카드 (자동 감지 — 최대 3개)
  5) 다음 액션 추천 (3개 — 페이지 이동 안내)
"""
import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_BG, COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING, COLOR_BLUE,
    BRAND_PRIMARY, compact_num,
)
from modules.data_processing import calc_ctr_scalar, media_month_stats
from modules.ui_helpers import render_page_header, render_empty_state, esc_html_safe
from modules.firebase_connector import get_benchmark_stats


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
def _detect_industry(df: pd.DataFrame) -> str:
    """현재 데이터의 대표 업종."""
    if df is None or df.empty or '분야' not in df.columns:
        return ''
    counts = df['분야'].dropna().astype(str).str.strip().value_counts()
    counts = counts[counts.index != '']
    return str(counts.index[0]) if not counts.empty else ''


def _detect_advertiser(df: pd.DataFrame) -> str:
    """현재 데이터의 대표 광고주(브랜드)."""
    if df is None or df.empty:
        return ''
    for col in ('_브랜드', '광고주', '대행사'):
        if col in df.columns:
            vals = df[col].dropna().astype(str)
            vals = vals[vals.str.strip() != '']
            if not vals.empty:
                return str(vals.iloc[0])
    return ''


def _fmt_delta_pct(cur: float, prev: float) -> tuple[str, str]:
    """변화량 텍스트 + 방향(up/down/flat) 반환."""
    if prev == 0 and cur == 0:
        return '변화 없음', 'flat'
    if prev == 0:
        return '신규', 'up'
    pct = (cur - prev) / prev * 100
    if abs(pct) < 0.5:
        return '변화 거의 없음', 'flat'
    direction = 'up' if pct > 0 else 'down'
    return f'{pct:+.1f}%', direction


def _arrow(direction: str) -> str:
    return {'up': '↑', 'down': '↓', 'flat': '·'}.get(direction, '·')


def _color_for(direction: str, *, lower_is_better: bool = False) -> str:
    """방향 → 색상. lower_is_better=True면 down이 좋음(녹색)."""
    if direction == 'flat':
        return COLOR_TEXT_SEC
    is_good = (direction == 'down') if lower_is_better else (direction == 'up')
    return COLOR_SUCCESS if is_good else COLOR_DANGER


def _kpi_card_overview(
    label: str,
    value: str,
    *,
    mom_text: str = '',
    mom_dir: str = 'flat',
    bench_text: str = '',
    bench_dir: str = 'flat',
    lower_is_better: bool = False,
) -> str:
    """한눈에 보기 페이지용 4분할 KPI 카드. 비교축 2개(전월·업종) 동시 표시."""
    mom_html = ''
    if mom_text:
        col = _color_for(mom_dir, lower_is_better=lower_is_better)
        mom_html = (
            f'<div style="display:flex;align-items:center;gap:4px;'
            f'font-size:0.72rem;color:{col};font-weight:600;">'
            f'<span>{_arrow(mom_dir)}</span><span>전월 {esc_html_safe(mom_text)}</span>'
            f'</div>'
        )
    bench_html = ''
    if bench_text:
        col = _color_for(bench_dir, lower_is_better=lower_is_better)
        bench_html = (
            f'<div style="display:flex;align-items:center;gap:4px;'
            f'font-size:0.72rem;color:{col};font-weight:600;">'
            f'<span>{_arrow(bench_dir)}</span><span>업종 {esc_html_safe(bench_text)}</span>'
            f'</div>'
        )
    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-radius:10px;'
        f'padding:16px 18px;background:{COLOR_CARD};">'
        f'  <div style="font-size:0.7rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;margin-bottom:8px;">{esc_html_safe(label)}</div>'
        f'  <div style="font-size:1.4rem;color:{COLOR_TEXT};font-weight:700;'
        f'line-height:1.2;letter-spacing:-0.01em;margin-bottom:8px;">{esc_html_safe(value)}</div>'
        f'  <div style="display:flex;flex-direction:column;gap:3px;">'
        f'  {mom_html}{bench_html}'
        f'  </div>'
        f'</div>'
    )


def _change_card(
    title: str,
    body: str,
    *,
    tone: str = 'info',
) -> str:
    """주요 변화 카드 — 흰 배경 + 좌측 3px 액센트."""
    tones = {
        'success': COLOR_SUCCESS,
        'danger':  COLOR_DANGER,
        'warning': COLOR_WARNING,
        'info':    COLOR_BLUE,
    }
    accent = tones.get(tone, COLOR_BLUE)
    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {accent};'
        f'border-radius:8px;padding:14px 16px;background:{COLOR_CARD};margin-bottom:10px;">'
        f'  <div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:600;'
        f'margin-bottom:4px;letter-spacing:-0.01em;">{esc_html_safe(title)}</div>'
        f'  <div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};line-height:1.55;">{body}</div>'
        f'</div>'
    )


def _action_card(
    title: str,
    body: str,
    *,
    nav_target: str = '',
) -> str:
    """다음 액션 추천 카드."""
    target_html = (
        f'<div style="font-size:0.7rem;color:{BRAND_PRIMARY};font-weight:600;margin-top:8px;">'
        f'→ {esc_html_safe(nav_target)}</div>'
        if nav_target else ''
    )
    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;'
        f'padding:14px 16px;background:{COLOR_CARD};height:100%;">'
        f'  <div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:600;'
        f'margin-bottom:4px;letter-spacing:-0.01em;">{esc_html_safe(title)}</div>'
        f'  <div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};line-height:1.55;">{esc_html_safe(body)}</div>'
        f'  {target_html}'
        f'</div>'
    )


# ──────────────────────────────────────────────
# 자동 인사이트 생성
# ──────────────────────────────────────────────
def _build_one_liner(
    cur_cost, prev_cost, cur_send, prev_send, cur_ctr, prev_ctr,
    n_media: int, top_media: str, top_ctr: float, *, cur_month: str, prev_month: str,
) -> str:
    """이번 달 한 줄 요약 자동 생성."""
    # 핵심 한 줄 — "5월은 4월 대비 X 했고, 가장 좋은 매체는 Y(CTR Z%)"
    parts = []
    # 1) 발송 변화
    if prev_send > 0:
        send_pct = (cur_send - prev_send) / prev_send * 100
        if abs(send_pct) >= 5:
            dir_word = '증가' if send_pct > 0 else '감소'
            parts.append(f'발송 {abs(send_pct):.0f}% {dir_word}')
    # 2) CTR 변화
    ctr_change = cur_ctr - prev_ctr
    if abs(ctr_change) >= 0.1:
        ctr_dir = '상승' if ctr_change > 0 else '하락'
        parts.append(f'CTR {ctr_change:+.2f}%p {ctr_dir}')
    # 3) 광고비 변화
    if prev_cost > 0:
        cost_pct = (cur_cost - prev_cost) / prev_cost * 100
        if abs(cost_pct) >= 5:
            dir_word = '증가' if cost_pct > 0 else '절감'
            parts.append(f'광고비 {abs(cost_pct):.0f}% {dir_word}')

    if parts:
        change_text = ', '.join(parts[:2])  # 최대 2개
        main = f'{cur_month}은 {prev_month} 대비 {change_text}했습니다.'
    else:
        main = f'{cur_month}은 {prev_month}과 비슷한 흐름입니다.'

    extras = []
    if n_media > 0:
        extras.append(f'매체 {n_media}개 운영 중')
    if top_media and top_ctr > 0:
        extras.append(f'최고 효율 매체는 <b>{esc_html_safe(top_media)}</b> (CTR {top_ctr:.2f}%)')

    extra_text = ' · '.join(extras) if extras else ''
    return f'{main} {extra_text}' if extra_text else main


def _detect_top_changes(df: pd.DataFrame, cur_month: str, prev_month: str) -> list[dict]:
    """전월 대비 가장 큰 변화 매체 자동 감지. 최대 3개 반환."""
    if df.empty:
        return []
    cur = media_month_stats(df, cur_month)
    prev = media_month_stats(df, prev_month)
    if cur.empty:
        return []
    merged = pd.merge(cur, prev, on='매체명', suffixes=('_당월', '_전월'), how='left').fillna(0)
    if merged.empty:
        return []

    changes = []
    # 1) CTR 가장 많이 오른 매체 (발송 5천+ 양월 모두 충족)
    qualified = merged[
        (merged['발송량_당월'] >= 5000) & (merged['발송량_전월'] >= 5000)
    ].copy()
    if not qualified.empty:
        qualified['CTR_변화'] = qualified['CTR_당월'] - qualified['CTR_전월']
        best_ctr = qualified.nlargest(1, 'CTR_변화')
        if not best_ctr.empty:
            r = best_ctr.iloc[0]
            if r['CTR_변화'] > 0.3:  # 0.3%p 이상 의미 있는 변화
                changes.append({
                    'tone': 'success',
                    'title': f'{r["매체명"]} — CTR 급등',
                    'body': f'{r["CTR_전월"]:.2f}% → <b style="color:{COLOR_TEXT};">{r["CTR_당월"]:.2f}%</b> '
                            f'({r["CTR_변화"]:+.2f}%p). 발송 {compact_num(r["발송량_당월"], "건")} 기준.',
                })
        worst_ctr = qualified.nsmallest(1, 'CTR_변화')
        if not worst_ctr.empty:
            r = worst_ctr.iloc[0]
            if r['CTR_변화'] < -0.3:
                changes.append({
                    'tone': 'danger',
                    'title': f'{r["매체명"]} — CTR 하락',
                    'body': f'{r["CTR_전월"]:.2f}% → <b style="color:{COLOR_TEXT};">{r["CTR_당월"]:.2f}%</b> '
                            f'({r["CTR_변화"]:+.2f}%p). 카피·타이밍 점검 권장.',
                })

    # 2) 신규 매체 (당월에 처음 등장)
    new_media = merged[(merged['발송량_전월'] == 0) & (merged['발송량_당월'] > 0)]
    if not new_media.empty:
        top_new = new_media.nlargest(1, '발송량_당월').iloc[0]
        changes.append({
            'tone': 'info',
            'title': f'{top_new["매체명"]} — 신규 매체 진입',
            'body': f'이번 달 처음 집행, 발송 <b style="color:{COLOR_TEXT};">{compact_num(top_new["발송량_당월"], "건")}</b>. '
                    f'CTR {top_new["CTR_당월"]:.2f}%.',
        })

    # 3) 발송량 급증/급감 매체 (50% 이상)
    qualified['발송_변화율'] = np.where(
        qualified['발송량_전월'] > 0,
        (qualified['발송량_당월'] - qualified['발송량_전월']) / qualified['발송량_전월'] * 100,
        0,
    )
    big_vol = qualified[abs(qualified['발송_변화율']) >= 50].sort_values(
        '발송_변화율', key=abs, ascending=False,
    )
    if not big_vol.empty and len(changes) < 3:
        r = big_vol.iloc[0]
        dir_word = '급증' if r['발송_변화율'] > 0 else '급감'
        tone = 'warning' if r['발송_변화율'] < 0 else 'info'
        changes.append({
            'tone': tone,
            'title': f'{r["매체명"]} — 발송량 {dir_word}',
            'body': f'{compact_num(r["발송량_전월"], "건")} → <b style="color:{COLOR_TEXT};">{compact_num(r["발송량_당월"], "건")}</b> '
                    f'({r["발송_변화율"]:+.0f}%).',
        })

    return changes[:3]


def _build_action_recs(df: pd.DataFrame, cur_month: str, industry: str) -> list[dict]:
    """다음 액션 추천 3개 생성."""
    recs = []

    # 1) 매체 성과 점검
    n_media = df[df['년월'] == cur_month]['매체명'].nunique() if '매체명' in df.columns else 0
    if n_media >= 2:
        recs.append({
            'title': '매체별 효율 점검',
            'body': f'운영 중인 {n_media}개 매체의 CTR·CPC를 비교해 어디에 더 투자할지 검토하세요.',
            'nav_target': '매체 성과 페이지로',
        })

    # 2) 업종 비교 / 기회 매체
    if industry:
        recs.append({
            'title': f'{industry} 업종 내 위치 확인',
            'body': '동종업계 평균 대비 우리 성과는 어떤지, 아직 시도하지 않은 매체는 무엇인지 확인하세요.',
            'nav_target': '업종 · 경쟁 페이지로',
        })

    # 3) 카피·타이밍
    recs.append({
        'title': '카피·발송 타이밍 점검',
        'body': '메시지 카피와 발송 시간대가 이번 달 성과에 어떤 영향을 줬는지 살펴보세요.',
        'nav_target': '카피 · 타이밍 페이지로',
    })

    return recs[:3]


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────
def render(df: pd.DataFrame):
    if df is None or df.empty:
        render_empty_state(
            '표시할 데이터가 없습니다',
            '사이드바에서 데이터를 업로드하거나 클라이언트(Firebase 연동)를 선택해 주세요.',
            icon='📭',
        )
        return

    if '년월' not in df.columns:
        render_empty_state(
            '데이터 전처리가 필요합니다',
            '날짜 컬럼이 정상적으로 파싱되지 않았습니다.',
            icon='⚠️',
        )
        return

    months = sorted([m for m in df['년월'].dropna().unique() if m])
    if not months:
        render_empty_state('월 데이터가 없습니다', icon='📭')
        return

    cur_month = months[-1]
    prev_month = months[-2] if len(months) >= 2 else None

    cur = df[df['년월'] == cur_month]
    prev = df[df['년월'] == prev_month] if prev_month else df.iloc[0:0]

    # ── 핵심 지표 계산 ──
    cur_cost = float(cur['집행금액'].sum())
    prev_cost = float(prev['집행금액'].sum()) if prev_month else 0
    cur_send = float(cur['발송량'].sum())
    prev_send = float(prev['발송량'].sum()) if prev_month else 0
    cur_click = float(cur['클릭수'].fillna(0).sum())
    prev_click = float(prev['클릭수'].fillna(0).sum()) if prev_month else 0
    cur_ctr = calc_ctr_scalar(cur_click, cur_send)
    prev_ctr = calc_ctr_scalar(prev_click, prev_send) if prev_month else 0

    adv_name = _detect_advertiser(df)
    industry = _detect_industry(df)
    n_media = cur['매체명'].nunique() if '매체명' in cur.columns else 0

    # ── 페이지 헤더 ──
    render_page_header(
        title='한눈에 보기',
        kicker='분석',
        subtitle=(
            (f'<b>{esc_html_safe(adv_name)}</b> · ' if adv_name else '')
            + f'<b>{cur_month}</b> 기준 · 매체 {n_media}개 운영 중'
            + (f' · 업종 <b>{esc_html_safe(industry)}</b>' if industry else '')
        ),
    )

    # ── 업종 벤치마크 (있으면) ──
    bench_ctr_avg = None
    if industry:
        try:
            stats = get_benchmark_stats(industry)
            if stats and stats.get('avg_ctr', 0) > 0:
                bench_ctr_avg = float(stats['avg_ctr'])
        except Exception:
            bench_ctr_avg = None

    # ── 4 KPI 카드 ──
    mom_send_txt, mom_send_dir = _fmt_delta_pct(cur_send, prev_send)
    mom_click_txt, mom_click_dir = _fmt_delta_pct(cur_click, prev_click)
    mom_cost_txt, mom_cost_dir = _fmt_delta_pct(cur_cost, prev_cost)
    if prev_ctr > 0 or cur_ctr > 0:
        ctr_diff = cur_ctr - prev_ctr
        mom_ctr_txt = f'{ctr_diff:+.2f}%p' if abs(ctr_diff) >= 0.01 else '변화 거의 없음'
        mom_ctr_dir = 'up' if ctr_diff > 0 else ('down' if ctr_diff < 0 else 'flat')
    else:
        mom_ctr_txt, mom_ctr_dir = '', 'flat'

    # CTR 업종 비교
    bench_ctr_txt, bench_ctr_dir = '', 'flat'
    if bench_ctr_avg is not None and cur_ctr > 0:
        diff = cur_ctr - bench_ctr_avg
        bench_ctr_txt = f'{diff:+.2f}%p'
        bench_ctr_dir = 'up' if diff > 0 else ('down' if diff < 0 else 'flat')

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_kpi_card_overview(
            '발송량', f'{cur_send:,.0f}건',
            mom_text=mom_send_txt, mom_dir=mom_send_dir,
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_kpi_card_overview(
            '클릭수', f'{cur_click:,.0f}회',
            mom_text=mom_click_txt, mom_dir=mom_click_dir,
        ), unsafe_allow_html=True)
    with c3:
        st.markdown(_kpi_card_overview(
            'CTR', f'{cur_ctr:.2f}%',
            mom_text=mom_ctr_txt, mom_dir=mom_ctr_dir,
            bench_text=bench_ctr_txt, bench_dir=bench_ctr_dir,
        ), unsafe_allow_html=True)
    with c4:
        st.markdown(_kpi_card_overview(
            '광고비', compact_num(cur_cost, '원'),
            mom_text=mom_cost_txt, mom_dir=mom_cost_dir,
            lower_is_better=False,
        ), unsafe_allow_html=True)

    st.markdown('<div class="space-lg"></div>', unsafe_allow_html=True)

    # ── 한 줄 요약 ──
    top_media, top_ctr = '', 0.0
    if not cur.empty and '매체명' in cur.columns:
        media_stats = media_month_stats(df, cur_month)
        # 발송 5천 이상 매체 중 CTR 1위
        big = media_stats[media_stats['발송량'] >= 5000]
        if not big.empty:
            top_row = big.nlargest(1, 'CTR').iloc[0]
            top_media = str(top_row['매체명'])
            top_ctr = float(top_row['CTR'])

    if prev_month:
        one_liner = _build_one_liner(
            cur_cost, prev_cost, cur_send, prev_send, cur_ctr, prev_ctr,
            n_media, top_media, top_ctr,
            cur_month=cur_month, prev_month=prev_month,
        )
    else:
        one_liner = (
            f'{cur_month} 첫 데이터입니다. 매체 {n_media}개 운영, '
            + (f'최고 효율 매체는 <b>{esc_html_safe(top_media)}</b> (CTR {top_ctr:.2f}%).'
               if top_media else '')
        )

    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {BRAND_PRIMARY};'
        f'border-radius:10px;padding:16px 20px;background:{COLOR_CARD};margin-bottom:24px;">'
        f'  <div style="font-size:0.7rem;color:{BRAND_PRIMARY};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;margin-bottom:6px;">이번 달 한 줄 요약</div>'
        f'  <div style="font-size:0.95rem;color:{COLOR_TEXT};line-height:1.55;'
        f'letter-spacing:-0.005em;">{one_liner}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 주요 변화 + 다음 액션 (2열) ──
    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:6px;letter-spacing:-0.01em;">주요 변화</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
            f'{esc_html_safe(prev_month) + " → " + esc_html_safe(cur_month) if prev_month else "전월 데이터 없음"}</div>',
            unsafe_allow_html=True,
        )
        if prev_month:
            changes = _detect_top_changes(df, cur_month, prev_month)
            if changes:
                for c in changes:
                    st.markdown(_change_card(c['title'], c['body'], tone=c['tone']),
                                unsafe_allow_html=True)
            else:
                st.markdown(_change_card(
                    '안정적인 흐름',
                    '전월 대비 의미 있는 변화(CTR ±0.3%p, 발송 ±50% 등)가 감지되지 않았습니다.',
                    tone='info',
                ), unsafe_allow_html=True)
        else:
            st.markdown(_change_card(
                '비교 데이터 부족',
                '전월 데이터가 있으면 자동으로 변화 포인트를 감지해 알려드립니다.',
                tone='info',
            ), unsafe_allow_html=True)

    with col_right:
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:6px;letter-spacing:-0.01em;">다음에 살펴볼 것</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
            f'추천 분석 흐름</div>',
            unsafe_allow_html=True,
        )
        recs = _build_action_recs(df, cur_month, industry)
        for r in recs:
            st.markdown(_action_card(r['title'], r['body'], nav_target=r.get('nav_target', '')),
                        unsafe_allow_html=True)
            st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)

    # ── 하단 안내 ──
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER_SUBTLE};border-radius:8px;'
        f'padding:10px 14px;background:{COLOR_BG};font-size:0.74rem;color:{COLOR_TEXT_SEC};'
        f'line-height:1.55;">'
        f'💡 더 자세한 월간 요약은 위 <b>월간 요약</b> 탭, KPI 백분위·등급은 <b>KPI · 인사이트</b> 탭을 확인하세요.'
        f'</div>',
        unsafe_allow_html=True,
    )
