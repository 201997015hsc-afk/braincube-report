"""
타겟팅 성과 분석 (Targeting Analysis)
─────────────────────────────────────
캠페인 타겟 필드(자유 텍스트)를 자동 파싱해 차원별 성과 비교.

답하는 질문:
  - 어떤 성별/연령대에 보냈을 때 잘 반응하나?
  - 행동 시그널(SEG/통화이력) 사용 시 vs 단순 인구통계 차이는?
  - 지역 타겟 vs 전국 타겟 어느 쪽이 효율적인가?
  - 우리 베스트 타겟 조합은?

데이터 소스: df['타겟'] 자유 텍스트 필드
파싱: 정규식 + 키워드 기반 (NLP 없이)
신뢰성: 메시징 캠페인 + 발송 5,000건+ qualified만 분석
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
from modules.data_processing import calc_ctr_scalar
from modules.ui_helpers import render_page_header, render_empty_state, esc_html_safe


# ──────────────────────────────────────────────
# 타겟 텍스트 파서
# ──────────────────────────────────────────────
_RE_AGE_RANGE = re.compile(r'(\d{2})\s*[-~]\s*(\d{2})\s*세')
_RE_AGE_DECADE = re.compile(r'(\d{2})\s*대')
_RE_AGE_BIRTH = re.compile(r'(\d{4})\s*년생')
_RE_AGE_OVER = re.compile(r'(\d{2})\s*세\s*(?:이상|이후|초과)')

_RE_GENDER_F = re.compile(r'여(?:성|자)(?!.*남)')  # 여 단독 (남 안 따라옴)
_RE_GENDER_M = re.compile(r'남(?:성|자)(?!.*여)')  # 남 단독
_RE_GENDER_MIX = re.compile(r'남녀|남\s*[,/]\s*여|여\s*[,/]\s*남')

_RE_SEG_BEHAVIOR = re.compile(r'SEG|수발신|수신이력|발신이력|통화이력|문자이력|이력자|상담이력')
_RE_REGION = re.compile(r'서울|경기|인천|부산|대구|광주|대전|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주|시\b|구\b|동\b|군\b')
_RE_INCOME = re.compile(r'소득|한도|연봉|등급|결제|매출')


def _parse_age_band(text: str) -> str:
    """연령대 추출 → 표준 라벨."""
    # 1) X-Y세 (35-49세)
    m = _RE_AGE_RANGE.search(text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        mid = (lo + hi) // 2
        if mid < 25: return '20대 이하'
        if mid < 35: return '30대'
        if mid < 45: return '40대'
        if mid < 55: return '50대'
        return '60대+'
    # 2) N대 (30대)
    m = _RE_AGE_DECADE.search(text)
    if m:
        d = int(m.group(1))
        if d == 20: return '20대 이하'
        if d == 30: return '30대'
        if d == 40: return '40대'
        if d == 50: return '50대'
        return '60대+'
    # 3) N년생 (유아동 타겟)
    m = _RE_AGE_BIRTH.search(text)
    if m:
        y = int(m.group(1))
        # 2026 기준, 2015~2022년생 → 유아동
        if 2010 <= y <= 2025:
            return '유아·아동'
    # 4) N세 이상
    m = _RE_AGE_OVER.search(text)
    if m:
        d = int(m.group(1))
        if d >= 50: return '50대'
        if d >= 40: return '40대'
        if d >= 30: return '30대'
    return '미분류'


def _parse_gender(text: str) -> str:
    """성별 추출."""
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


def _has_seg(text: str) -> bool:
    return bool(_RE_SEG_BEHAVIOR.search(text))


def _has_region(text: str) -> bool:
    return bool(_RE_REGION.search(text))


def _has_income(text: str) -> bool:
    return bool(_RE_INCOME.search(text))


def parse_targets(df: pd.DataFrame) -> pd.DataFrame:
    """df에 타겟 파싱 컬럼 추가하여 반환.
    추가 컬럼: _tgt_gender, _tgt_age, _tgt_has_seg, _tgt_has_region, _tgt_has_income
    """
    out = df.copy()
    if '타겟' not in out.columns:
        for c in ('_tgt_gender', '_tgt_age', '_tgt_has_seg', '_tgt_has_region', '_tgt_has_income'):
            out[c] = None if c.endswith('_gender') or c.endswith('_age') else False
        return out
    tgt = out['타겟'].fillna('').astype(str)
    out['_tgt_gender'] = tgt.apply(_parse_gender)
    out['_tgt_age'] = tgt.apply(_parse_age_band)
    out['_tgt_has_seg'] = tgt.apply(_has_seg)
    out['_tgt_has_region'] = tgt.apply(_has_region)
    out['_tgt_has_income'] = tgt.apply(_has_income)
    return out


# ──────────────────────────────────────────────
# 분석 헬퍼
# ──────────────────────────────────────────────
def _ctr_by_dim(qualified: pd.DataFrame, dim_col: str) -> pd.DataFrame:
    """qualified 메시징 캠페인을 차원별로 집계 — 발송/클릭/CTR/캠페인수."""
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
    return g.sort_values('CTR', ascending=False)


def _ctr_by_flag(qualified: pd.DataFrame, flag_col: str, label: str) -> pd.DataFrame:
    """True/False 플래그 차원."""
    if qualified.empty or flag_col not in qualified.columns:
        return pd.DataFrame()
    out = qualified.copy()
    out['구분'] = out[flag_col].map({True: f'{label} 사용', False: f'{label} 미사용'})
    return _ctr_by_dim(out, '구분')


# ──────────────────────────────────────────────
# UI 카드
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


def _dim_bar_card(title: str, sub: str, stats: pd.DataFrame, *, max_rows: int = 6) -> None:
    """차원별 CTR 비교 — 가로 막대 카드.
    각 행: 구분 | 발송 | CTR (막대 + 수치)
    """
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

    # 미분류 행은 비교 가치 낮으니 색 옅게
    valid = stats[stats['구분'] != '미분류'].head(max_rows)
    if valid.empty:
        valid = stats.head(max_rows)
    max_ctr = float(valid['CTR'].max()) if not valid.empty else 1.0
    if max_ctr <= 0:
        max_ctr = 1.0

    rows_html = []
    for _, r in valid.iterrows():
        ctr = float(r['CTR'])
        sends = float(r['발송'])
        n = int(r['캠페인수'])
        is_top = r['구분'] == valid.iloc[0]['구분']
        bar_pct = (ctr / max_ctr) * 100
        bar_color = BRAND_PRIMARY if is_top else COLOR_BLUE

        rows_html.append(
            f'<div style="display:grid;grid-template-columns:90px 1fr 80px;'
            f'align-items:center;gap:10px;margin-bottom:6px;">'
            # 구분 라벨
            f'  <div style="font-size:0.78rem;color:{COLOR_TEXT};font-weight:{"700" if is_top else "500"};">'
            f'{esc_html_safe(str(r["구분"]))}</div>'
            # 막대
            f'  <div style="position:relative;height:18px;background:{COLOR_BORDER_SUBTLE};'
            f'border-radius:4px;overflow:hidden;">'
            f'    <div style="position:absolute;top:0;left:0;height:100%;width:{bar_pct}%;'
            f'background:{bar_color};border-radius:4px;"></div>'
            f'  </div>'
            # CTR + 보조
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


def _combo_card(combo: pd.Series) -> str:
    """베스트 타겟 조합 카드."""
    gender = str(combo.get('_tgt_gender', '—'))
    age = str(combo.get('_tgt_age', '—'))
    seg_label = 'SEG 사용' if combo.get('_tgt_has_seg', False) else '인구통계만'
    sends = float(combo.get('발송', 0))
    clicks = float(combo.get('클릭', 0))
    ctr = float(combo.get('CTR', 0))
    n = int(combo.get('캠페인수', 0))

    seg_color = COLOR_SUCCESS if combo.get('_tgt_has_seg', False) else COLOR_TEXT_SEC
    seg_bg = '#E8F7F0' if combo.get('_tgt_has_seg', False) else COLOR_BG

    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {BRAND_PRIMARY};'
        f'border-radius:8px;padding:12px 16px;background:{COLOR_CARD};margin-bottom:8px;">'
        f'  <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;">'
        # 좌: 조합 설명
        f'    <div style="flex:1;">'
        f'      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;">'
        f'        <span style="font-size:0.92rem;color:{COLOR_TEXT};font-weight:700;">'
        f'{esc_html_safe(age)} · {esc_html_safe(gender)}</span>'
        f'        <span style="font-size:0.65rem;color:{seg_color};background:{seg_bg};'
        f'padding:2px 8px;border-radius:999px;font-weight:600;">{esc_html_safe(seg_label)}</span>'
        f'      </div>'
        f'      <div style="font-size:0.7rem;color:{COLOR_TEXT_TER};">'
        f'발송 {compact_num(sends, "건")} · 클릭 {compact_num(clicks, "회")} · 캠페인 {n}회</div>'
        f'    </div>'
        # 우: CTR
        f'    <div style="text-align:right;">'
        f'      <div style="font-size:1.2rem;color:{COLOR_TEXT};font-weight:700;line-height:1;">'
        f'{ctr:.2f}%</div>'
        f'      <div style="font-size:0.62rem;color:{COLOR_TEXT_TER};margin-top:2px;">CTR</div>'
        f'    </div>'
        f'  </div>'
        f'</div>'
    )


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────
def render(df: pd.DataFrame):
    if df is None or df.empty:
        render_empty_state('표시할 데이터가 없습니다', icon='📭')
        return

    # 타겟 컬럼이 없으면 안내 (firebase에서만 들어옴)
    if '타겟' not in df.columns:
        render_empty_state(
            '타겟 정보가 없는 데이터입니다',
            '타겟팅 분석은 Firebase 연동된 데이터에서만 동작합니다. '
            '엑셀 업로드 데이터는 메시지·발송·클릭만 있어 타겟 분석이 불가합니다.',
            icon='🎯',
        )
        return

    # ── 파싱 ──
    parsed = parse_targets(df)

    # 메시징 캠페인만 (DA/CPA는 노출수 기반이라 의미 다름)
    if '광고상품' in parsed.columns:
        msg = parsed[~parsed['광고상품'].astype(str).str.upper().isin(['DA', 'CPA'])].copy()
    else:
        msg = parsed.copy()

    # qualified: 발송 5천+ AND 클릭 트래킹 있음
    qualified = msg[
        (msg['발송량'] >= MIN_SENDS_FOR_CTR_CLAIM)
        & (msg['클릭수'].notna() if '_has_click' in msg.columns or '클릭수' in msg.columns else True)
    ].copy()

    n_total = len(df)
    n_with_target = int((df['타겟'].fillna('').astype(str).str.strip() != '').sum())
    n_qualified = len(qualified)
    seg_rate = float(qualified['_tgt_has_seg'].mean() * 100) if n_qualified > 0 else 0

    # ── 페이지 헤더 ──
    render_page_header(
        title='타겟팅 성과',
        kicker='운영',
        subtitle=(
            f'타겟 정보를 자동 파싱해 성별·연령·SEG·지역별 CTR을 비교합니다. '
            f'발송 <b>{MIN_SENDS_FOR_CTR_CLAIM:,}건</b> 이상 메시징 캠페인 기준.'
        ),
    )

    # ── 4 summary strip ──
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_summary_card(
            '전체 캠페인', f'{n_total}건',
            f'타겟 정보 보유 {n_with_target}건 ({n_with_target/max(n_total,1)*100:.0f}%)',
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_summary_card(
            '분석 대상', f'{n_qualified}건',
            f'메시징 + 발송 {MIN_SENDS_FOR_CTR_CLAIM:,}건+ qualified',
        ), unsafe_allow_html=True)
    with c3:
        if n_qualified > 0:
            avg_ctr = qualified['클릭수'].fillna(0).sum() / qualified['발송량'].sum() * 100
        else:
            avg_ctr = 0
        st.markdown(_summary_card(
            '평균 CTR', f'{avg_ctr:.2f}%',
            'qualified 캠페인 가중평균',
        ), unsafe_allow_html=True)
    with c4:
        st.markdown(_summary_card(
            'SEG 활용률', f'{seg_rate:.0f}%',
            '행동 시그널(통화이력/SEG) 사용 비율',
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

    # ── 4 차원 비교 (2x2 그리드) ──
    col_a, col_b = st.columns(2)
    with col_a:
        _dim_bar_card(
            '성별별 CTR',
            '같은 매체·같은 카피라도 성별에 따라 반응이 다릅니다',
            _ctr_by_dim(qualified, '_tgt_gender'),
        )
        _dim_bar_card(
            'SEG(행동 시그널) 효과',
            '통화이력·수발신 등 행동 시그널 사용 vs 단순 인구통계',
            _ctr_by_flag(qualified, '_tgt_has_seg', '행동 시그널'),
        )
    with col_b:
        _dim_bar_card(
            '연령대별 CTR',
            'X-Y세 / N대 / N년생 패턴을 자동 분류',
            _ctr_by_dim(qualified, '_tgt_age'),
        )
        _dim_bar_card(
            '지역 타겟 효과',
            '특정 지역 명시 vs 전국 타겟',
            _ctr_by_flag(qualified, '_tgt_has_region', '지역 타겟'),
        )

    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    # ── 베스트 타겟 조합 Top 5 ──
    st.markdown(
        f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:6px;letter-spacing:-0.01em;">베스트 타겟 조합 Top 5</div>'
        f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
        f'연령 × 성별 × SEG 조합별 CTR. 분류된 캠페인 1건 이상 + 발송 5천+ 충족.</div>',
        unsafe_allow_html=True,
    )

    combos = (
        qualified.groupby(['_tgt_age', '_tgt_gender', '_tgt_has_seg'])
        .agg(
            발송=('발송량', 'sum'),
            클릭=('클릭수', lambda x: x.fillna(0).sum()),
            캠페인수=('발송량', 'size'),
        )
        .reset_index()
    )
    combos = combos[
        (combos['_tgt_age'] != '미분류')
        & (combos['_tgt_gender'] != '미분류')
        & (combos['발송'] >= MIN_SENDS_FOR_CTR_CLAIM)
    ].copy()
    combos['CTR'] = np.where(combos['발송'] > 0, combos['클릭'] / combos['발송'] * 100, 0)
    combos = combos.sort_values('CTR', ascending=False).head(5)

    if combos.empty:
        st.markdown(
            f'<div style="border:1px dashed {COLOR_BORDER};border-radius:8px;'
            f'padding:14px 16px;color:{COLOR_TEXT_TER};font-size:0.8rem;text-align:center;">'
            f'분류 가능한 타겟 조합이 부족합니다.</div>',
            unsafe_allow_html=True,
        )
    else:
        for _, c in combos.iterrows():
            st.markdown(_combo_card(c), unsafe_allow_html=True)

    # ── 추천 액션 ──
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:6px;letter-spacing:-0.01em;">추천 액션</div>',
        unsafe_allow_html=True,
    )

    recs = []

    # 1) 성별 인사이트
    gender_stats = _ctr_by_dim(qualified, '_tgt_gender')
    if not gender_stats.empty and len(gender_stats[gender_stats['구분'] != '미분류']) >= 2:
        valid_g = gender_stats[gender_stats['구분'] != '미분류']
        best_g = valid_g.iloc[0]
        worst_g = valid_g.iloc[-1]
        if float(best_g['CTR']) > float(worst_g['CTR']) * 1.3:  # 30%+ 차이
            recs.append({
                'icon': '🎯',
                'title': f'{best_g["구분"]} 타겟이 더 효과적',
                'body': (f'{best_g["구분"]} CTR {float(best_g["CTR"]):.2f}% vs '
                         f'{worst_g["구분"]} {float(worst_g["CTR"]):.2f}% '
                         f'({float(best_g["CTR"])/max(float(worst_g["CTR"]),0.01):.1f}배). '
                         f'반대 성별 모수 확장 검토 권장.'),
            })

    # 2) SEG 효과
    seg_stats = _ctr_by_flag(qualified, '_tgt_has_seg', '행동 시그널')
    if not seg_stats.empty and len(seg_stats) >= 2:
        seg_used = seg_stats[seg_stats['구분'].str.contains('사용')]
        seg_not = seg_stats[seg_stats['구분'].str.contains('미사용')]
        if not seg_used.empty and not seg_not.empty:
            ctr_used = float(seg_used.iloc[0]['CTR'])
            ctr_not = float(seg_not.iloc[0]['CTR'])
            if ctr_used > ctr_not * 1.2:
                recs.append({
                    'icon': '🔍',
                    'title': '행동 시그널(SEG) 사용이 효과적',
                    'body': (f'SEG 사용 CTR {ctr_used:.2f}% vs 미사용 {ctr_not:.2f}% '
                             f'({ctr_used/max(ctr_not,0.01):.1f}배). '
                             f'단순 인구통계 캠페인에 통화이력 등 행동 시그널 결합 검토.'),
                })

    # 3) 베스트 조합 활용
    if not combos.empty:
        top = combos.iloc[0]
        recs.append({
            'icon': '⭐',
            'title': f'베스트 조합: {top["_tgt_age"]} · {top["_tgt_gender"]} ' +
                     ('+ SEG' if top['_tgt_has_seg'] else ''),
            'body': (f'CTR {float(top["CTR"]):.2f}%, 발송 {compact_num(float(top["발송"]), "건")}. '
                     f'이 조합을 다른 매체·캠페인에도 확장 시도해 보세요.'),
        })

    if not recs:
        recs.append({
            'icon': '📊',
            'title': '의미 있는 차이 미감지',
            'body': '현재 데이터에서 타겟 차원별 큰 차이가 발견되지 않았습니다. 캠페인이 더 쌓이면 자동으로 인사이트가 생성됩니다.',
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
        f'💡 파싱 한계: 자유 텍스트 기반이라 일부 타겟은 미분류로 처리됩니다. '
        f'성별 73%, 연령 56%, 행동 시그널 26% 자동 분류 가능. '
        f'소득·한도 등 민감 정보는 의도적으로 분석에서 제외합니다.'
        f'</div>',
        unsafe_allow_html=True,
    )
