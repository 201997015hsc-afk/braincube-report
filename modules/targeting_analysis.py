"""
타겟팅 성과 분석 (Targeting Analysis)
─────────────────────────────────────
캠페인 타겟 필드(자유 텍스트)를 자동 파싱해 행동·시간·정밀도 차원별 성과 비교.

답하는 질문:
  - 어떤 행동 시그널(결제·통화·방문 등)이 가장 잘 반응하나?
  - 시간 윈도우(1년/6개월/최근)에 따라 CTR이 달라지나?
  - 타겟 조건이 정교할수록 효과가 좋은가?
  - 매체 카테고리(카드·통신사 vs 플랫폼)별 타겟 풍부도 차이는?

설계 원칙:
  - 데모(성별·연령)는 보조 정보 (한 줄 축소)
  - 행동·시간·정밀도가 메인 분석 차원
  - 자유 텍스트 → 정규식 + 키워드 (NLP 없이)
"""
import re
import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_BG, COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING, COLOR_BLUE,
    BRAND_PRIMARY, compact_num, MIN_SENDS_FOR_CTR_CLAIM, is_card_or_telecom,
)
from modules.data_processing import calc_ctr_scalar
from modules.ui_helpers import render_page_header, render_empty_state, esc_html_safe


# ──────────────────────────────────────────────
# 정규식 — 행동·시간·데모
# ──────────────────────────────────────────────
# 데모 (보조)
_RE_AGE_RANGE = re.compile(r'(\d{2})\s*[-~]\s*(\d{2})\s*세')
_RE_AGE_DECADE = re.compile(r'(\d{2})\s*대')
_RE_AGE_BIRTH = re.compile(r'(\d{4})\s*년생')
_RE_AGE_OVER = re.compile(r'(\d{2})\s*세\s*(?:이상|이후|초과)')
_RE_GENDER_F = re.compile(r'여(?:성|자)(?!.*남)')
_RE_GENDER_M = re.compile(r'남(?:성|자)(?!.*여)')
_RE_GENDER_MIX = re.compile(r'남녀|남\s*[,/]\s*여|여\s*[,/]\s*남')

# 행동 시그널 (메인)
_BEHAVIOR_PATTERNS = [
    ('결제·구매',   re.compile(r'결제|구매|결재|매입|구입|이용\s*금액|사용\s*금액')),
    ('방문·접속',   re.compile(r'방문|접속|로그인|이용|클릭|조회|페이지\s*뷰')),
    ('통화·문자',   re.compile(r'통화|전화|수발신|발신|수신|문자\s*수신|SMS\s*수신|MMS\s*수신')),
    ('가입·신청',   re.compile(r'가입|신청|등록|회원|입회')),
    ('상담·문의',   re.compile(r'상담|문의|콜센터|컨택|상담사')),
    ('관심·선호',   re.compile(r'선호|관심|취향|좋아\s*하|즐겨')),
    ('보유·소유',   re.compile(r'보유|소유|이용\s*중|가입자|등급')),
]

# 시간 윈도우
_TIME_PATTERNS = [
    ('1개월 이내',  re.compile(r'1개월\s*(?:이내|내)|최근\s*한\s*달|30일\s*(?:이내|내)|최근\s*1개월')),
    ('3개월 이내',  re.compile(r'3개월\s*(?:이내|내)|90일\s*(?:이내|내)|최근\s*3개월|분기\s*내')),
    ('6개월 이내',  re.compile(r'6개월\s*(?:이내|내)|반년\s*(?:이내|내)|최근\s*6개월|180일')),
    ('1년 이내',    re.compile(r'1년\s*(?:이내|내)|12개월\s*(?:이내|내)|최근\s*1년|365일')),
    ('1년 초과',    re.compile(r'2년\s*(?:이내|내)|3년|24개월|36개월')),
]

# SEG 명시
_RE_SEG_LABEL = re.compile(r'SEG\s*\d+|SEG[)\s]')


def _parse_age_band(text: str) -> str:
    m = _RE_AGE_RANGE.search(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        mid = (lo + hi) // 2
        if mid < 25: return '20대 이하'
        if mid < 35: return '30대'
        if mid < 45: return '40대'
        if mid < 55: return '50대'
        return '60대+'
    m = _RE_AGE_DECADE.search(text)
    if m:
        d = int(m.group(1))
        if d == 20: return '20대 이하'
        if d == 30: return '30대'
        if d == 40: return '40대'
        if d == 50: return '50대'
        return '60대+'
    m = _RE_AGE_BIRTH.search(text)
    if m:
        y = int(m.group(1))
        if 2010 <= y <= 2025:
            return '유아·아동'
    m = _RE_AGE_OVER.search(text)
    if m:
        d = int(m.group(1))
        if d >= 50: return '50대'
        if d >= 40: return '40대'
        if d >= 30: return '30대'
    return '미분류'


def _parse_gender(text: str) -> str:
    if _RE_GENDER_MIX.search(text):
        return '남녀'
    has_m = bool(_RE_GENDER_M.search(text))
    has_f = bool(_RE_GENDER_F.search(text))
    if has_m and has_f:
        return '남녀'
    if has_m:
        return '남성'
    if has_f:
        return '여성'
    return '미분류'


def _parse_behaviors(text: str) -> list[str]:
    """행동 시그널 종류 리스트 (복수 가능)."""
    out = []
    for label, pat in _BEHAVIOR_PATTERNS:
        if pat.search(text):
            out.append(label)
    return out


def _parse_time_window(text: str) -> str:
    """가장 짧은 시간 윈도우 우선 반환."""
    for label, pat in _TIME_PATTERNS:
        if pat.search(text):
            return label
    return '기간 미상'


def _precision_tier(text: str, behaviors: list[str], has_time: bool, has_seg_label: bool) -> str:
    """타겟 정밀도 등급:
       - 단일 조건: 데모만 또는 행동 0~1개
       - 복합 (2~3): 행동 2개 or 행동+시간 or 행동+SEG
       - 정밀 (4+):  행동 3개+ 또는 SEG 명시 + 시간 + 행동 등
    """
    score = 0
    score += len(behaviors)
    if has_time:
        score += 1
    if has_seg_label:
        score += 1
    # 텍스트 길이도 보조 지표 (긴 텍스트 = 복잡 조건)
    if len(text) > 100:
        score += 1
    if score >= 4:
        return '정밀 (4단계+)'
    if score >= 2:
        return '복합 (2-3단계)'
    return '단일 조건'


def parse_targets(df: pd.DataFrame) -> pd.DataFrame:
    """df에 타겟 파싱 컬럼 추가하여 반환."""
    out = df.copy()
    if '타겟' not in out.columns:
        for c in ('_tgt_gender', '_tgt_age', '_tgt_behaviors', '_tgt_n_behaviors',
                  '_tgt_time', '_tgt_has_seg_label', '_tgt_precision'):
            out[c] = None if isinstance(c, str) and 'behaviors' in c else False
        return out
    tgt = out['타겟'].fillna('').astype(str)
    out['_tgt_gender'] = tgt.apply(_parse_gender)
    out['_tgt_age'] = tgt.apply(_parse_age_band)
    out['_tgt_behaviors'] = tgt.apply(_parse_behaviors)
    out['_tgt_n_behaviors'] = out['_tgt_behaviors'].apply(len)
    out['_tgt_time'] = tgt.apply(_parse_time_window)
    out['_tgt_has_seg_label'] = tgt.apply(lambda t: bool(_RE_SEG_LABEL.search(t)))
    out['_tgt_has_time'] = out['_tgt_time'] != '기간 미상'
    out['_tgt_precision'] = out.apply(
        lambda r: _precision_tier(
            str(r.get('타겟', '')),
            r['_tgt_behaviors'],
            r['_tgt_has_time'],
            r['_tgt_has_seg_label'],
        ),
        axis=1,
    )
    # 매체 카테고리
    if '매체명' in out.columns:
        out['_media_cat'] = out['매체명'].astype(str).apply(
            lambda m: '카드·통신사' if is_card_or_telecom(m) else '플랫폼·기타'
        )
    elif '매체' in out.columns:
        out['_media_cat'] = out['매체'].astype(str).apply(
            lambda m: '카드·통신사' if is_card_or_telecom(m) else '플랫폼·기타'
        )
    else:
        out['_media_cat'] = '미상'
    return out


# ──────────────────────────────────────────────
# 분석 헬퍼
# ──────────────────────────────────────────────
def _ctr_by_dim(qualified: pd.DataFrame, dim_col: str, *, drop_uncategorized: bool = True) -> pd.DataFrame:
    if qualified.empty or dim_col not in qualified.columns:
        return pd.DataFrame()
    g = (
        qualified.groupby(dim_col)
        .agg(
            발송=('발송량', 'sum'),
            클릭=('클릭수', lambda x: x.fillna(0).sum()),
            캠페인수=('발송량', 'size'),
        )
        .reset_index()
        .rename(columns={dim_col: '구분'})
    )
    g['CTR'] = np.where(g['발송'] > 0, g['클릭'] / g['발송'] * 100, 0)
    if drop_uncategorized:
        g = g[~g['구분'].isin(['미분류', '미상'])]
    return g.sort_values('CTR', ascending=False)


def _ctr_by_behavior_explode(qualified: pd.DataFrame) -> pd.DataFrame:
    """행동 시그널은 복수 — explode 후 시그널별 집계."""
    if qualified.empty or '_tgt_behaviors' not in qualified.columns:
        return pd.DataFrame()
    base = qualified[qualified['_tgt_behaviors'].apply(len) > 0].copy()
    if base.empty:
        return pd.DataFrame()
    expl = base.explode('_tgt_behaviors')
    return _ctr_by_dim(expl, '_tgt_behaviors')


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────
def _summary_card(title: str, value: str, sub: str, *, color: str = None) -> str:
    color = color or COLOR_TEXT
    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;padding:12px 14px;'
        f'background:{COLOR_CARD};">'
        f'  <div style="font-size:0.65rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;margin-bottom:4px;">{esc_html_safe(title)}</div>'
        f'  <div style="font-size:0.95rem;color:{color};font-weight:700;line-height:1.2;">'
        f'{esc_html_safe(value)}</div>'
        f'  <div style="font-size:0.7rem;color:{COLOR_TEXT_TER};margin-top:3px;line-height:1.4;">'
        f'{esc_html_safe(sub)}</div>'
        f'</div>'
    )


def _dim_bar_card(title: str, sub: str, stats: pd.DataFrame, *, max_rows: int = 6,
                   order_preset: list[str] | None = None) -> None:
    """차원별 CTR 가로 막대."""
    st.markdown(
        f'<div style="font-size:0.9rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:4px;letter-spacing:-0.01em;">{esc_html_safe(title)}</div>'
        f'<div style="font-size:0.72rem;color:{COLOR_TEXT_SEC};margin-bottom:10px;">'
        f'{esc_html_safe(sub)}</div>',
        unsafe_allow_html=True,
    )

    if stats.empty:
        st.markdown(
            f'<div style="border:1px dashed {COLOR_BORDER};border-radius:8px;'
            f'padding:14px;color:{COLOR_TEXT_TER};font-size:0.78rem;text-align:center;">'
            f'분석 가능한 데이터가 부족합니다.</div>',
            unsafe_allow_html=True,
        )
        return

    valid = stats.head(max_rows).copy()
    # 사전 정의 순서 (예: 시간 윈도우는 짧은 것부터)
    if order_preset:
        valid['_order'] = valid['구분'].apply(
            lambda x: order_preset.index(x) if x in order_preset else 999
        )
        valid = valid.sort_values('_order').drop(columns='_order')

    max_ctr = float(valid['CTR'].max()) if not valid.empty else 1.0
    if max_ctr <= 0:
        max_ctr = 1.0
    top_idx = valid['CTR'].idxmax() if not valid.empty else None

    rows_html = []
    for idx, r in valid.iterrows():
        ctr = float(r['CTR'])
        sends = float(r['발송'])
        n = int(r['캠페인수'])
        is_top = idx == top_idx
        bar_pct = (ctr / max_ctr) * 100
        bar_color = BRAND_PRIMARY if is_top else COLOR_BLUE
        font_weight = '700' if is_top else '500'

        rows_html.append(
            f'<div style="display:grid;grid-template-columns:110px 1fr 88px;'
            f'align-items:center;gap:10px;margin-bottom:6px;">'
            f'  <div style="font-size:0.78rem;color:{COLOR_TEXT};font-weight:{font_weight};">'
            f'{esc_html_safe(str(r["구분"]))}</div>'
            f'  <div style="position:relative;height:18px;background:{COLOR_BORDER_SUBTLE};'
            f'border-radius:4px;overflow:hidden;">'
            f'    <div style="position:absolute;top:0;left:0;height:100%;width:{bar_pct}%;'
            f'background:{bar_color};border-radius:4px;"></div>'
            f'  </div>'
            f'  <div style="text-align:right;font-size:0.78rem;color:{COLOR_TEXT};font-weight:600;">'
            f'{ctr:.2f}%'
            f'    <div style="font-size:0.62rem;color:{COLOR_TEXT_TER};font-weight:400;margin-top:1px;">'
            f'{compact_num(sends, "건")} · {n}회</div>'
            f'  </div>'
            f'</div>'
        )

    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;'
        f'padding:14px 16px;background:{COLOR_CARD};margin-bottom:16px;">'
        + ''.join(rows_html) +
        f'</div>',
        unsafe_allow_html=True,
    )


def _demo_compact_card(qualified: pd.DataFrame) -> None:
    """데모 (성별·연령) 통합 한 줄 카드 — 보조 정보로 축소."""
    gender_stats = _ctr_by_dim(qualified, '_tgt_gender')
    age_stats = _ctr_by_dim(qualified, '_tgt_age')

    def _mini(stats: pd.DataFrame, label: str) -> str:
        if stats.empty:
            return f'<div style="color:{COLOR_TEXT_TER};font-size:0.75rem;">{label}: 데이터 부족</div>'
        top = stats.iloc[0]
        bottom = stats.iloc[-1] if len(stats) > 1 else None
        if bottom is not None:
            return (
                f'<div style="font-size:0.78rem;color:{COLOR_TEXT};line-height:1.6;">'
                f'<span style="color:{COLOR_TEXT_SEC};">{label}:</span> '
                f'<b style="color:{COLOR_SUCCESS};">{esc_html_safe(str(top["구분"]))}</b> '
                f'{float(top["CTR"]):.2f}% '
                f'<span style="color:{COLOR_TEXT_TER};">vs '
                f'{esc_html_safe(str(bottom["구분"]))} {float(bottom["CTR"]):.2f}%</span>'
                f'</div>'
            )
        return (
            f'<div style="font-size:0.78rem;color:{COLOR_TEXT};">'
            f'<span style="color:{COLOR_TEXT_SEC};">{label}:</span> '
            f'<b>{esc_html_safe(str(top["구분"]))}</b> {float(top["CTR"]):.2f}%'
            f'</div>'
        )

    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER_SUBTLE};border-radius:8px;'
        f'padding:12px 16px;background:{COLOR_BG};margin-bottom:16px;">'
        f'  <div style="font-size:0.7rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;margin-bottom:8px;">데모 그래픽 (보조)</div>'
        f'  <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px;">'
        f'    {_mini(gender_stats, "성별 최고")}'
        f'    {_mini(age_stats, "연령 최고")}'
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────
def render(df: pd.DataFrame):
    if df is None or df.empty:
        render_empty_state('표시할 데이터가 없습니다', icon='📭')
        return

    if '타겟' not in df.columns:
        render_empty_state(
            '타겟 정보가 없는 데이터입니다',
            '타겟팅 분석은 Firebase 연동된 데이터에서만 동작합니다.',
            icon='🎯',
        )
        return

    # ── 파싱 ──
    parsed = parse_targets(df)

    # 메시징 캠페인만 (DA/CPA 제외)
    if '광고상품' in parsed.columns:
        msg = parsed[~parsed['광고상품'].astype(str).str.upper().isin(['DA', 'CPA'])].copy()
    else:
        msg = parsed.copy()

    qualified = msg[msg['발송량'] >= MIN_SENDS_FOR_CTR_CLAIM].copy()

    n_total = len(df)
    n_with_target = int((df['타겟'].fillna('').astype(str).str.strip() != '').sum())
    n_qualified = len(qualified)

    # ── 페이지 헤더 ──
    render_page_header(
        title='타겟팅 성과',
        kicker='운영',
        subtitle=(
            f'행동 시그널·시간 윈도우·타겟 정밀도 중심으로 CTR을 비교합니다. '
            f'발송 <b>{MIN_SENDS_FOR_CTR_CLAIM:,}건</b> 이상 메시징 캠페인 기준.'
        ),
    )

    # ── 4 summary strip ──
    behavior_rate = float((qualified['_tgt_n_behaviors'] > 0).mean() * 100) if n_qualified else 0
    time_rate = float(qualified['_tgt_has_time'].mean() * 100) if n_qualified else 0
    if n_qualified > 0:
        avg_ctr = qualified['클릭수'].fillna(0).sum() / qualified['발송량'].sum() * 100
        avg_behaviors = float(qualified['_tgt_n_behaviors'].mean())
    else:
        avg_ctr = 0
        avg_behaviors = 0

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_summary_card(
            '분석 대상', f'{n_qualified}건',
            f'전체 {n_total}건 중 메시징+qualified',
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_summary_card(
            '평균 CTR', f'{avg_ctr:.2f}%',
            '가중평균 (발송량 기준)',
        ), unsafe_allow_html=True)
    with c3:
        st.markdown(_summary_card(
            '행동 시그널 활용', f'{behavior_rate:.0f}%',
            f'평균 {avg_behaviors:.1f}개 시그널/캠페인',
        ), unsafe_allow_html=True)
    with c4:
        st.markdown(_summary_card(
            '시간 윈도우 명시', f'{time_rate:.0f}%',
            '1년/6개월/3개월/1개월 등 명시',
        ), unsafe_allow_html=True)

    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    if n_qualified < 3:
        st.markdown(
            f'<div style="border:1px dashed {COLOR_BORDER};border-radius:8px;'
            f'padding:14px 16px;color:{COLOR_TEXT_TER};font-size:0.8rem;text-align:center;">'
            f'분석 가능한 캠페인이 {n_qualified}건으로 부족합니다. '
            f'더 많은 캠페인 데이터가 쌓이면 자동으로 인사이트가 생성됩니다.</div>',
            unsafe_allow_html=True,
        )
        return

    # ══════════════════════════════════════════════
    # MAIN: 행동·시간·정밀도·매체 (2x2)
    # ══════════════════════════════════════════════
    col_a, col_b = st.columns(2)

    with col_a:
        _dim_bar_card(
            '행동 시그널 종류별 CTR',
            '결제·방문·통화 등 어떤 행동 기반 타겟이 가장 잘 반응하는가',
            _ctr_by_behavior_explode(qualified),
        )
        _dim_bar_card(
            '타겟 정밀도 등급별 CTR',
            '조건이 정교할수록 효과가 좋은가 — 단일/복합/정밀 등급 비교',
            _ctr_by_dim(qualified, '_tgt_precision'),
            order_preset=['정밀 (4단계+)', '복합 (2-3단계)', '단일 조건'],
        )

    with col_b:
        _dim_bar_card(
            '시간 윈도우별 CTR',
            '최근 행동일수록 반응이 좋은가 — 1개월/3개월/6개월/1년/기간미상',
            _ctr_by_dim(qualified, '_tgt_time', drop_uncategorized=False),
            order_preset=['1개월 이내', '3개월 이내', '6개월 이내', '1년 이내', '1년 초과', '기간 미상'],
        )
        _dim_bar_card(
            '매체 카테고리별 CTR',
            '카드·통신사 vs 플랫폼·기타 — 어느 쪽이 더 정교한 타겟팅을 제공하는가',
            _ctr_by_dim(qualified, '_media_cat'),
        )

    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    # ══════════════════════════════════════════════
    # 데모 그래픽 (보조 — 한 줄 축소)
    # ══════════════════════════════════════════════
    _demo_compact_card(qualified)

    # ══════════════════════════════════════════════
    # 베스트 조합: 행동 시그널 + 시간 윈도우 + 매체 카테고리
    # ══════════════════════════════════════════════
    st.markdown(
        f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:6px;letter-spacing:-0.01em;">베스트 행동×시간 조합 Top 5</div>'
        f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
        f'행동 시그널 + 시간 윈도우 조합별 CTR. 발송 5천+ qualified.</div>',
        unsafe_allow_html=True,
    )

    # explode + groupby
    base = qualified[qualified['_tgt_behaviors'].apply(len) > 0].copy()
    if not base.empty:
        expl = base.explode('_tgt_behaviors')
        combo = (
            expl.groupby(['_tgt_behaviors', '_tgt_time'])
            .agg(
                발송=('발송량', 'sum'),
                클릭=('클릭수', lambda x: x.fillna(0).sum()),
                캠페인수=('발송량', 'size'),
            )
            .reset_index()
        )
        combo = combo[combo['발송'] >= MIN_SENDS_FOR_CTR_CLAIM].copy()
        combo['CTR'] = np.where(combo['발송'] > 0, combo['클릭'] / combo['발송'] * 100, 0)
        combo = combo.sort_values('CTR', ascending=False).head(5)
    else:
        combo = pd.DataFrame()

    if combo.empty:
        st.markdown(
            f'<div style="border:1px dashed {COLOR_BORDER};border-radius:8px;'
            f'padding:14px 16px;color:{COLOR_TEXT_TER};font-size:0.8rem;text-align:center;">'
            f'행동×시간 조합 데이터가 부족합니다.</div>',
            unsafe_allow_html=True,
        )
    else:
        for _, c in combo.iterrows():
            time_label = str(c['_tgt_time'])
            time_color = COLOR_SUCCESS if time_label != '기간 미상' else COLOR_TEXT_SEC
            time_bg = '#E8F7F0' if time_label != '기간 미상' else COLOR_BG
            st.markdown(
                f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {BRAND_PRIMARY};'
                f'border-radius:8px;padding:12px 16px;background:{COLOR_CARD};margin-bottom:8px;">'
                f'  <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;">'
                f'    <div style="flex:1;">'
                f'      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
                f'        <span style="font-size:0.92rem;color:{COLOR_TEXT};font-weight:700;">'
                f'{esc_html_safe(str(c["_tgt_behaviors"]))}</span>'
                f'        <span style="font-size:0.65rem;color:{time_color};background:{time_bg};'
                f'padding:2px 8px;border-radius:999px;font-weight:600;">{esc_html_safe(time_label)}</span>'
                f'      </div>'
                f'      <div style="font-size:0.7rem;color:{COLOR_TEXT_TER};">'
                f'발송 {compact_num(float(c["발송"]), "건")} · 클릭 {compact_num(float(c["클릭"]), "회")} · '
                f'캠페인 {int(c["캠페인수"])}회</div>'
                f'    </div>'
                f'    <div style="text-align:right;">'
                f'      <div style="font-size:1.2rem;color:{COLOR_TEXT};font-weight:700;line-height:1;">'
                f'{float(c["CTR"]):.2f}%</div>'
                f'      <div style="font-size:0.62rem;color:{COLOR_TEXT_TER};margin-top:2px;">CTR</div>'
                f'    </div>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ══════════════════════════════════════════════
    # 추천 액션
    # ══════════════════════════════════════════════
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:6px;letter-spacing:-0.01em;">추천 액션</div>',
        unsafe_allow_html=True,
    )

    recs = []

    # 1) 베스트 행동 시그널
    behavior_stats = _ctr_by_behavior_explode(qualified)
    if not behavior_stats.empty and len(behavior_stats) >= 2:
        best_b = behavior_stats.iloc[0]
        worst_b = behavior_stats.iloc[-1]
        if float(best_b['CTR']) > float(worst_b['CTR']) * 1.3:
            recs.append({
                'icon': '🎯',
                'title': f'{best_b["구분"]} 시그널이 가장 효과적',
                'body': (f'{best_b["구분"]} CTR {float(best_b["CTR"]):.2f}% '
                         f'vs {worst_b["구분"]} {float(worst_b["CTR"]):.2f}% '
                         f'({float(best_b["CTR"])/max(float(worst_b["CTR"]),0.01):.1f}배). '
                         f'다른 매체·캠페인에도 이 행동 시그널 활용 검토.'),
            })

    # 2) 정밀도 효과
    prec_stats = _ctr_by_dim(qualified, '_tgt_precision')
    if not prec_stats.empty and len(prec_stats) >= 2:
        valid_prec = prec_stats[prec_stats['구분'] != '미분류']
        if not valid_prec.empty:
            top_p = valid_prec.iloc[0]
            single = valid_prec[valid_prec['구분'] == '단일 조건']
            if not single.empty and float(top_p['CTR']) > float(single.iloc[0]['CTR']) * 1.2:
                recs.append({
                    'icon': '🔍',
                    'title': '정교한 타겟이 효과적',
                    'body': (f'{top_p["구분"]} CTR {float(top_p["CTR"]):.2f}% '
                             f'vs 단일 조건 {float(single.iloc[0]["CTR"]):.2f}%. '
                             f'단순 인구통계 캠페인에 행동 시그널·시간 조건 결합 검토.'),
                })

    # 3) 시간 윈도우 효과
    time_stats = _ctr_by_dim(qualified, '_tgt_time', drop_uncategorized=False)
    if not time_stats.empty and len(time_stats) >= 2:
        valid_t = time_stats[time_stats['구분'] != '기간 미상']
        no_time = time_stats[time_stats['구분'] == '기간 미상']
        if not valid_t.empty and not no_time.empty:
            avg_with_time = float(valid_t['CTR'].mean())
            ctr_no = float(no_time.iloc[0]['CTR'])
            if avg_with_time > ctr_no * 1.2:
                recs.append({
                    'icon': '⏰',
                    'title': '시간 윈도우 명시가 효과적',
                    'body': (f'시간 조건 있음 평균 CTR {avg_with_time:.2f}% '
                             f'vs 기간 미상 {ctr_no:.2f}%. '
                             f'"1년내", "최근 3개월" 등 명시적 시간 조건 추가 검토.'),
                })

    # 4) 베스트 조합
    if not combo.empty:
        top_combo = combo.iloc[0]
        recs.append({
            'icon': '⭐',
            'title': f'베스트 조합: {top_combo["_tgt_behaviors"]} + {top_combo["_tgt_time"]}',
            'body': (f'CTR {float(top_combo["CTR"]):.2f}%, 발송 {compact_num(float(top_combo["발송"]), "건")}. '
                     f'이 조합을 우선 확장 시도.'),
        })

    if not recs:
        recs.append({
            'icon': '📊',
            'title': '의미 있는 차이 미감지',
            'body': '캠페인이 더 쌓이면 자동으로 인사이트가 생성됩니다.',
        })

    for r in recs:
        st.markdown(
            f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {BRAND_PRIMARY};'
            f'border-radius:8px;padding:12px 16px;background:{COLOR_CARD};margin-bottom:8px;">'
            f'  <div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:600;margin-bottom:4px;">'
            f'{r["icon"]} {esc_html_safe(r["title"])}</div>'
            f'  <div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};line-height:1.55;">{r["body"]}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── 파싱 한계 안내 ──
    st.markdown('<div class="space-sm"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER_SUBTLE};border-radius:8px;'
        f'padding:10px 14px;background:{COLOR_BG};font-size:0.72rem;color:{COLOR_TEXT_SEC};'
        f'line-height:1.55;">'
        f'💡 행동 시그널은 자유 텍스트 기반 자동 파싱입니다 — 결제·구매(35%), '
        f'방문·접속(15%), 통화·문자(9%), 가입·신청(6%) 등 검출. '
        f'성별·연령 데모는 보조 정보로 축소 표시. '
        f'소득·한도 등 민감 정보는 분석에서 제외합니다.'
        f'</div>',
        unsafe_allow_html=True,
    )
