"""
미디어 믹스 제안 (Media Mix Recommendation)
───────────────────────────────────────────
다음 달 어느 매체에 얼마를, 어떤 타겟으로 집행할지 제안한다.

제안 4유형:
  🔁 휴면 복귀  — 과거 집행했으나 최근 90일 미집행 (성과 있었던 매체 우선)
  🆕 신규 진입  — 동종업계 타사가 검증했으나 자사 미집행
  📈 증액       — 전월·전전월 성과가 좋았던 자사 매체
  ✨ 신규 매체  — 업종 내 최근 등장한 매체 (선점 기회)

각 제안에 포함:
  - 예상 CTR (자사 실적 우선, 없으면 동종업계 레퍼런스)
  - 추천 예산 (최근 집행 단가 × 권장 물량)
  - 추천 타겟팅 (자사 베스트 타겟 또는 동종업계 검증 타겟)
"""
import re

import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_BG, COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING, COLOR_BLUE,
    BRAND_PRIMARY, compact_num, MIN_SENDS_FOR_CTR_CLAIM,
)
from modules.ui_helpers import render_page_header, render_empty_state, esc_html_safe

_DORMANT_DAYS = 90      # 휴면 판정 기준
_NEW_MEDIA_DAYS = 60    # 업종 내 신규 매체 판정 기준
_REF_WINDOW_DAYS = 180  # 동종업계 레퍼런스 조회 기간
_DEFAULT_QTY = 50_000   # 기본 권장 발송량


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
def _ctr(clicks, sends) -> float:
    return float(clicks) / float(sends) * 100 if sends else 0.0


def _summarize_target(raw) -> str:
    """타겟 원문 → 한 줄 요약 (캘린더와 동일 규칙)."""
    try:
        from modules.schedule_calendar import _summarize_target as _s
        return _s(raw)
    except Exception:
        return str(raw)[:30] if raw else ''


def _best_target_for(df: pd.DataFrame, media: str) -> tuple[str, str]:
    """해당 매체에서 가장 성과 좋았던 캠페인의 타겟 (요약, 원문)."""
    if df.empty or '타겟' not in df.columns:
        return '', ''
    sub = df[df['매체'].astype(str) == media].copy()
    sub = sub[sub['타겟'].notna() & (sub['타겟'].astype(str).str.strip() != '')]
    sub = sub[pd.to_numeric(sub['발송건'], errors='coerce').fillna(0) >= MIN_SENDS_FOR_CTR_CLAIM]
    if sub.empty:
        return '', ''
    sub['_ctr'] = (
        pd.to_numeric(sub['클릭수'], errors='coerce').fillna(0)
        / pd.to_numeric(sub['발송건'], errors='coerce').clip(lower=1) * 100
    )
    best = sub.nlargest(1, '_ctr').iloc[0]
    raw = str(best['타겟'])
    return _summarize_target(raw), raw


@st.cache_data(ttl=1800, show_spinner=False)
def build_recommendations(
    _full: pd.DataFrame, brand: str, industry: str, cache_key: str
) -> list[dict]:
    """4유형 제안 생성. (cache_key는 캐시 무효화용 — 데이터 크기·최신일자)"""
    if _full is None or _full.empty:
        return []
    full = _full.copy()
    full['_d'] = pd.to_datetime(full['_date'], errors='coerce')
    full = full.dropna(subset=['_d'])
    if full.empty:
        return []
    latest = full['_d'].max()

    me = full[full['_브랜드'] == brand].copy()
    if me.empty:
        return []

    # 메시징 qualified (CTR 신뢰 가능)
    def _qual(d):
        q = d[~d['광고상품'].astype(str).str.upper().isin(['DA', 'CPA'])].copy()
        q = q[pd.to_numeric(q['발송건'], errors='coerce').fillna(0) >= MIN_SENDS_FOR_CTR_CLAIM]
        q['_ctr'] = (
            pd.to_numeric(q['클릭수'], errors='coerce').fillna(0)
            / pd.to_numeric(q['발송건'], errors='coerce').clip(lower=1) * 100
        )
        return q

    me_q = _qual(me)
    my_media_all = set(me['매체'].dropna().astype(str))
    recent_media = set(me[me['_d'] >= latest - pd.Timedelta(days=_DORMANT_DAYS)]['매체']
                       .dropna().astype(str))

    # 동종업계 타사 (최근 180일)
    others = full[
        (full['분야'] == industry)
        & (full['_브랜드'] != brand)
        & (full['_d'] >= latest - pd.Timedelta(days=_REF_WINDOW_DAYS))
    ].copy()
    others_q = _qual(others)
    ref = others_q.groupby('매체').agg(
        브랜드수=('_브랜드', 'nunique'), 캠페인수=('매체', 'size'),
        발송=('발송건', 'sum'), 클릭=('클릭수', lambda x: x.fillna(0).sum()),
        단가=('실단가', 'median') if '실단가' in others_q.columns else ('단가', 'median'),
    ).reset_index()
    ref['CTR'] = ref.apply(lambda r: _ctr(r['클릭'], r['발송']), axis=1)
    ref_map = {r['매체']: r for _, r in ref.iterrows()}

    def _unit_price(media: str) -> float:
        """최근 집행 단가 — 자사 우선, 없으면 동종업계 중앙값."""
        col = '실단가' if '실단가' in full.columns else '단가'
        mine = me[me['매체'].astype(str) == media]
        if not mine.empty:
            v = pd.to_numeric(mine.sort_values('_d')[col], errors='coerce').dropna()
            if len(v):
                return float(v.iloc[-1])
        r = ref_map.get(media)
        if r is not None and r['단가'] and not pd.isna(r['단가']):
            return float(r['단가'])
        return 100.0

    recs: list[dict] = []

    # ── 🔁 휴면 복귀 ──
    for media in sorted(my_media_all - recent_media):
        hist = me_q[me_q['매체'].astype(str) == media]
        if hist.empty:
            continue
        my_ctr = _ctr(hist['클릭수'].fillna(0).sum(), hist['발송건'].sum())
        last_d = me[me['매체'].astype(str) == media]['_d'].max()
        days = int((latest - last_d).days)
        tgt_s, tgt_raw = _best_target_for(me, media)
        recs.append({
            'type': '🔁 휴면 복귀', 'tone': COLOR_WARNING, 'media': media,
            'ctr': my_ctr, 'ctr_src': f'자사 실적 {len(hist)}건',
            'qty': _DEFAULT_QTY, 'unit': _unit_price(media),
            'target': tgt_s, 'target_raw': tgt_raw,
            'why': f'{days}일간 미집행. 과거 CTR {my_ctr:.2f}% 기록',
            'score': my_ctr * 1.0,
            'evidence': f'자사 {len(hist)}건 · 최종 집행 {last_d:%Y-%m-%d}',
        })

    # ── 🆕 신규 진입 (동종업계 검증, 자사 미집행) ──
    for _, r in ref.iterrows():
        media = str(r['매체'])
        if media in my_media_all or r['발송'] < MIN_SENDS_FOR_CTR_CLAIM:
            continue
        tgt_s, tgt_raw = _best_target_for(others, media)
        brands = sorted(set(
            others_q[others_q['매체'].astype(str) == media]['_브랜드'].dropna().astype(str)
        ))[:3]
        recs.append({
            'type': '🆕 신규 진입', 'tone': BRAND_PRIMARY, 'media': media,
            'ctr': float(r['CTR']), 'ctr_src': f'동종업계 {int(r["캠페인수"])}건',
            'qty': _DEFAULT_QTY if r['발송'] >= 100_000 else 30_000,
            'unit': _unit_price(media),
            'target': tgt_s, 'target_raw': tgt_raw,
            'why': f'{industry} 업종 {int(r["브랜드수"])}개사 검증 · 자사 미집행',
            'score': float(r['CTR']) * (1.0 + 0.1 * int(r['브랜드수'])),
            'evidence': f'활용: {", ".join(brands)}' + (' 외' if int(r['브랜드수']) > 3 else ''),
        })

    # ── 📈 증액 (전월·전전월 자사 성과 우수) ──
    recent_q = me_q[me_q['_d'] >= latest - pd.Timedelta(days=62)]
    if not recent_q.empty:
        g = recent_q.groupby('매체').agg(
            발송=('발송건', 'sum'), 클릭=('클릭수', lambda x: x.fillna(0).sum()),
            건수=('매체', 'size'),
        ).reset_index()
        g['CTR'] = g.apply(lambda r: _ctr(r['클릭'], r['발송']), axis=1)
        base_ctr = _ctr(recent_q['클릭수'].fillna(0).sum(), recent_q['발송건'].sum())
        for _, r in g.sort_values('CTR', ascending=False).head(4).iterrows():
            if r['CTR'] < base_ctr:
                continue
            media = str(r['매체'])
            tgt_s, tgt_raw = _best_target_for(me, media)
            lift = (r['CTR'] / base_ctr - 1) * 100 if base_ctr else 0
            recs.append({
                'type': '📈 증액', 'tone': COLOR_SUCCESS, 'media': media,
                'ctr': float(r['CTR']), 'ctr_src': f'자사 최근 {int(r["건수"])}건',
                'qty': int(min(r['발송'] / max(int(r['건수']), 1) * 1.3 // 1000 * 1000, 150_000)),
                'unit': _unit_price(media),
                'target': tgt_s, 'target_raw': tgt_raw,
                'why': f'최근 2개월 CTR {r["CTR"]:.2f}% — 평균 대비 +{lift:.0f}%',
                'score': float(r['CTR']) * 1.2,
                'evidence': f'최근 발송 {compact_num(float(r["발송"]), "건")} · {int(r["건수"])}회',
            })

    # ── ✨ 업종 신규 매체 (최근 등장, 선점 기회) ──
    ind_all = full[full['분야'] == industry]
    cut = latest - pd.Timedelta(days=_NEW_MEDIA_DAYS)
    newly = set(ind_all[ind_all['_d'] >= cut]['매체'].dropna().astype(str)) - \
        set(ind_all[ind_all['_d'] < cut]['매체'].dropna().astype(str))
    for media in sorted(newly):
        if media in my_media_all:
            continue
        sub = others_q[others_q['매체'].astype(str) == media]
        c = _ctr(sub['클릭수'].fillna(0).sum(), sub['발송건'].sum()) if not sub.empty else 0
        tgt_s, tgt_raw = _best_target_for(others, media)
        recs.append({
            'type': '✨ 신규 매체', 'tone': COLOR_BLUE, 'media': media,
            'ctr': c, 'ctr_src': f'동종업계 {len(sub)}건' if len(sub) else '레퍼런스 없음',
            'qty': 30_000, 'unit': _unit_price(media),
            'target': tgt_s, 'target_raw': tgt_raw,
            'why': f'최근 {_NEW_MEDIA_DAYS}일 내 {industry} 업종에 처음 등장 — 선점 기회',
            'score': (c if c > 0 else 1.0) * 0.9,
            'evidence': '업종 내 신규 매체',
        })

    # 중복 매체 제거 (점수 높은 제안 우선) + 상위 12개만
    # — 실제로 한 달에 돌릴 수 있는 규모로 추려야 제안서로 쓸 수 있다.
    recs.sort(key=lambda x: -x['score'])
    seen, out = set(), []
    for r in recs:
        if r['media'] in seen:
            continue
        seen.add(r['media'])
        r['budget'] = int(r['qty'] * r['unit'])
        r['exp_clicks'] = int(r['qty'] * r['ctr'] / 100)
        out.append(r)
        if len(out) >= 12:
            break
    return out


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────
def _rec_card(r: dict, rank: int) -> str:
    tone = r['tone']
    target_html = (
        f'<div style="background:{COLOR_BG};border-radius:6px;padding:8px 12px;'
        f'margin-top:8px;border-left:2px solid {COLOR_BORDER};">'
        f'  <div style="font-size:0.62rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;margin-bottom:3px;">추천 타겟</div>'
        f'  <div style="font-size:0.78rem;color:{COLOR_TEXT};font-weight:600;">'
        f'{esc_html_safe(r["target"])}</div>'
        f'</div>'
        if r.get('target') else ''
    )
    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {tone};'
        f'border-radius:8px;padding:14px 18px;background:{COLOR_CARD};margin-bottom:10px;">'
        # 헤더
        f'  <div style="display:flex;align-items:center;justify-content:space-between;'
        f'gap:12px;margin-bottom:8px;flex-wrap:wrap;">'
        f'    <div style="display:flex;align-items:center;gap:8px;">'
        f'      <span style="font-size:0.62rem;color:{tone};background:{COLOR_BG};'
        f'padding:2px 8px;border-radius:999px;font-weight:700;">{r["type"]}</span>'
        f'      <span style="font-size:1rem;color:{COLOR_TEXT};font-weight:700;">'
        f'{esc_html_safe(r["media"])}</span>'
        f'    </div>'
        f'    <div style="text-align:right;">'
        f'      <div style="font-size:1.25rem;color:{tone};font-weight:700;line-height:1;">'
        f'{r["ctr"]:.2f}%</div>'
        f'      <div style="font-size:0.6rem;color:{COLOR_TEXT_TER};margin-top:2px;">'
        f'예상 CTR · {esc_html_safe(r["ctr_src"])}</div>'
        f'    </div>'
        f'  </div>'
        # 근거
        f'  <div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};line-height:1.5;">'
        f'{esc_html_safe(r["why"])}</div>'
        f'  <div style="font-size:0.68rem;color:{COLOR_TEXT_TER};margin-top:2px;">'
        f'{esc_html_safe(r["evidence"])}</div>'
        # 제안 수치
        f'  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;'
        f'margin-top:10px;padding-top:10px;border-top:1px dashed {COLOR_BORDER_SUBTLE};">'
        f'    <div><div style="font-size:0.6rem;color:{COLOR_TEXT_SEC};font-weight:600;">추천 물량</div>'
        f'<div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:700;">{r["qty"]:,}건</div></div>'
        f'    <div><div style="font-size:0.6rem;color:{COLOR_TEXT_SEC};font-weight:600;">추천 예산</div>'
        f'<div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:700;">{compact_num(r["budget"], "원")}</div></div>'
        f'    <div><div style="font-size:0.6rem;color:{COLOR_TEXT_SEC};font-weight:600;">예상 클릭</div>'
        f'<div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:700;">{r["exp_clicks"]:,}회</div></div>'
        f'  </div>'
        f'  {target_html}'
        f'</div>'
    )


def render(df: pd.DataFrame):
    render_page_header(
        title='미디어 믹스 제안',
        kicker='운영',
        subtitle=(
            '휴면 매체 복귀 · 동종업계 검증 신규 매체 · 성과 우수 매체 증액 · '
            '업종 신규 매체를 자동으로 추려 <b>예상 CTR · 예산 · 타겟</b>까지 제안합니다.'
        ),
    )

    if df is None or df.empty:
        render_empty_state('데이터가 없습니다', icon='📭')
        return

    # 광고주·업종 식별
    brand = ''
    for col in ('_브랜드', '광고주', '대행사'):
        if col in df.columns:
            v = df[col].dropna().astype(str)
            v = v[v.str.strip() != '']
            if not v.empty:
                brand = str(v.iloc[0])
                break
    industry = ''
    if '분야' in df.columns:
        c = df['분야'].dropna().astype(str).str.strip().value_counts()
        c = c[c.index != '']
        if not c.empty:
            industry = str(c.index[0])

    if not brand or not industry:
        render_empty_state(
            '광고주·업종 정보가 필요합니다',
            'Firebase 연동 클라이언트에서만 미디어 믹스 제안이 동작합니다.',
            icon='🎯',
        )
        return

    from modules.firebase_connector import load_from_firestore
    full = load_from_firestore()
    if full is None or full.empty:
        render_empty_state('Firebase 데이터를 불러올 수 없습니다', icon='🔌')
        return

    cache_key = f'{brand}|{industry}|{len(full)}'
    recs = build_recommendations(full, brand, industry, cache_key)
    if not recs:
        render_empty_state(
            '제안할 매체가 없습니다',
            '집행 이력과 동종업계 데이터가 더 쌓이면 자동으로 제안이 생성됩니다.',
            icon='📭',
        )
        return

    # ── 요약 + 필터 ──
    types = ['전체'] + [t for t in ['🆕 신규 진입', '📈 증액', '🔁 휴면 복귀', '✨ 신규 매체']
                        if any(r['type'] == t for r in recs)]
    col_f, col_m = st.columns([2.2, 1])
    with col_f:
        sel = st.radio('제안 유형', types, horizontal=True,
                       key='mix_type', label_visibility='collapsed')
    view = recs if sel == '전체' else [r for r in recs if r['type'] == sel]
    with col_m:
        st.markdown(
            f'<div style="text-align:right;font-size:0.75rem;color:{COLOR_TEXT_SEC};'
            f'padding-top:8px;">{esc_html_safe(brand)} · {esc_html_safe(industry)} 업종 기준</div>',
            unsafe_allow_html=True,
        )

    total_budget = sum(r['budget'] for r in view)
    total_clicks = sum(r['exp_clicks'] for r in view)
    avg_ctr = (sum(r['ctr'] * r['qty'] for r in view) / sum(r['qty'] for r in view)) if view else 0

    c1, c2, c3, c4 = st.columns(4)
    for col, (lab, val, sub) in zip(
        [c1, c2, c3, c4],
        [
            ('제안 매체', f'{len(view)}개', f'전체 {len(recs)}개 중'),
            ('총 추천 예산', compact_num(total_budget, '원'), '전체 제안 합계'),
            ('예상 총 클릭', f'{total_clicks:,}회', f'가중 CTR {avg_ctr:.2f}%'),
            ('최고 기대 매체', view[0]['media'] if view else '—',
             f'CTR {view[0]["ctr"]:.2f}%' if view else ''),
        ],
    ):
        with col:
            st.markdown(
                f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;'
                f'padding:12px 14px;background:{COLOR_CARD};">'
                f'<div style="font-size:0.65rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
                f'letter-spacing:0.04em;font-weight:600;margin-bottom:4px;">{esc_html_safe(lab)}</div>'
                f'<div style="font-size:1.05rem;color:{COLOR_TEXT};font-weight:700;line-height:1.2;">'
                f'{esc_html_safe(str(val))}</div>'
                f'<div style="font-size:0.68rem;color:{COLOR_TEXT_TER};margin-top:3px;">'
                f'{esc_html_safe(sub)}</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    # ── 제안 카드 ──
    for i, r in enumerate(view, 1):
        st.markdown(_rec_card(r, i), unsafe_allow_html=True)

    # ── 운영팀 전달용 텍스트 ──
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
    lines = [f'# {brand} 미디어 믹스 제안 ({industry} 업종)', '']
    for r in view:
        lines.append(f'[{r["type"]}] {r["media"]}')
        lines.append(f'  - 물량 {r["qty"]:,}건 / 예산 {r["budget"]:,}원 / 예상 CTR {r["ctr"]:.2f}% (예상 클릭 {r["exp_clicks"]:,}회)')
        lines.append(f'  - 근거: {r["why"]} ({r["evidence"]})')
        if r.get('target_raw'):
            tg = re.sub(r'\s*\n\s*', ' / ', str(r['target_raw']).strip())
            lines.append(f'  - 추천 타겟: {tg[:150]}')
        lines.append('')
    lines.append(f'합계: {len(view)}개 매체 / 예산 {total_budget:,}원 / 예상 클릭 {total_clicks:,}회')
    st.caption('📋 복사용 제안서')
    st.code('\n'.join(lines), language='text')
