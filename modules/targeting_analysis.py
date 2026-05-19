"""
타겟팅 성과 분석 (Targeting Analysis)
─────────────────────────────────────
키워드 분류 없음 — 실제 타겟 원문을 그대로 보여주고
사용자가 직접 어떤 타겟이 잘 터졌는지 한눈에 파악.

답하는 질문:
  - 어떤 타겟 설정이 가장 잘 터졌나? (베스트 캠페인 Top 10)
  - 특정 매체에서 어떤 타겟이 효과적인가? (매체별 베스트)
  - 통신사·카드사별로 어떤 타겟을 새로 만들면 좋을까? (자동 추천)
  - 우리 캠페인 중 검색하고 싶은 게 있나? (전체 테이블)

설계 원칙:
  - 원문 데이터 그대로 노출 (추상적 분류 X)
  - 베스트 카드는 시각적 (큰 CTR 숫자 + 타겟 원문)
  - 추천 타겟은 운영팀이 복사해 바로 쓸 수 있는 텍스트로
"""
import re
import streamlit as st
import pandas as pd
import numpy as np
from collections import Counter

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_BG, COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING, COLOR_BLUE,
    BRAND_PRIMARY, compact_num, MIN_SENDS_FOR_CTR_CLAIM,
)
from modules.ui_helpers import render_page_header, render_empty_state, esc_html_safe


# ──────────────────────────────────────────────
# 매체 카테고리 분류 (추천 엔진용)
# ──────────────────────────────────────────────
_CARD_KEYWORDS = [
    '카드', 'KB', '신한', '국민', '삼성카드', '현대카드', '롯데카드',
    'BC', '하나카드', 'NH', '우리카드', '비씨', '농협', '카카오뱅크',
]
_TELECOM_KEYWORDS = [
    'KT', 'SKT', 'SK텔레콤', 'LG U', 'LGU', '알뜰', '비즈챗', 'TMAP', 'T MAP',
]


def _classify_for_recommend(media_name: str) -> str:
    """매체명 → '통신사'/'카드사'/'플랫폼·기타'.
    카드 먼저 매칭 (KT가 롯데카드 같은 곳에 잘못 잡히지 않게).
    """
    if not media_name:
        return '플랫폼·기타'
    s = str(media_name).upper()
    for kw in _CARD_KEYWORDS:
        if kw.upper() in s:
            return '카드사'
    for kw in _TELECOM_KEYWORDS:
        if kw.upper() in s:
            return '통신사'
    return '플랫폼·기타'


# ──────────────────────────────────────────────
# 추천 엔진 — 베스트 캠페인 타겟 텍스트에서 공통 패턴 추출
# ──────────────────────────────────────────────
def _strip_prefix(line: str) -> str:
    """앞쪽 번호·기호 prefix만 제거. 본문 숫자는 보존.

    제거 대상:
      - "1. X", "1) X", "1: X"  (숫자 + 구두점 + 공백)
      - "* X", "- X", "ㄴ X", "▶ X", "> X", "• X"  (기호 + 공백)
    제거 안 함:
      - "24-25년..."  (본문 시작 숫자)
    """
    line = line.strip()
    # 번호 prefix
    line = re.sub(r'^\s*\d+\s*[\.\)\:]\s+', '', line)
    # 기호 prefix
    line = re.sub(r'^\s*[\*\-ㄴ▶▷>•]\s+', '', line)
    return line.strip()


def _normalize_for_grouping(line: str) -> str:
    """그룹핑(빈도 카운팅)용 정규화 — 연도/날짜를 와일드카드로 치환.
    표시할 때는 원본 라인 사용."""
    norm = re.sub(r'\b\d{2,4}\s*년', '[연도]', line)
    norm = re.sub(r'\b\d{1,2}\s*월', '[월]', norm)
    norm = re.sub(r'\s+', ' ', norm).strip()
    return norm


def _is_exclusion_line(raw: str) -> bool:
    """라인이 명백히 제외 조건인지 — 단순 단어 포함보다 엄격."""
    s = raw.strip()
    # 빈 라인
    if not s:
        return False
    # 명백한 패턴: '* X 제외', '- X 제외', 'ㄴ X 제외', '제외:' 시작
    if re.search(r'(?:^|\s)(?:\*|\-|ㄴ)\s*.{2,}\s*제외', s):
        return True
    # 라인 끝이 '제외' 또는 '제외함' / '제외함니다' 등
    if re.search(r'제외(?:함|합니다|\.|$|\))', s.rstrip()):
        return True
    # '디타겟팅' 키워드
    if '디타겟팅' in s or '미사용자' in s or '미가입자' in s:
        return True
    return False


def _recommend_targeting(qualified: pd.DataFrame, category: str) -> dict | None:
    """카테고리(통신사/카드사)별 베스트 캠페인에서 추천 타겟 추출.

    Returns dict with:
      n_base: 베스트 캠페인 수
      avg_ctr / min_ctr / max_ctr
      common_includes: [(line, count), ...]  자주 등장하는 포함 조건
      common_excludes: [(line, count), ...]  자주 등장하는 제외 조건
      age_range: (min, max) 권장 연령
      gender: 권장 성별
      recommended_text: 복사용 추천 타겟 텍스트
    """
    sub = qualified[qualified['_recommend_cat'] == category].copy()
    if len(sub) < 5:
        return None

    # 상위 30% (또는 평균의 1.5배 이상)
    threshold = max(sub['CTR_calc'].quantile(0.7), sub['CTR_calc'].mean() * 1.0)
    top = sub[sub['CTR_calc'] >= threshold].copy()
    if len(top) < 3:
        return None

    # (정규화 키 → 대표 원본 라인) 매핑으로 빈도 카운팅
    # 표시할 때는 원본 라인 그대로 사용 (정규화로 인한 텍스트 손실 방지)
    include_norm_to_rep: dict[str, str] = {}
    include_counter: Counter = Counter()
    exclude_norm_to_rep: dict[str, str] = {}
    exclude_counter: Counter = Counter()
    age_ranges = []
    genders = []

    for tgt in top['타겟'].astype(str):
        # 연령 추출
        for m in re.finditer(r'(\d{2})\s*[-~]\s*(\d{2})\s*세', tgt):
            age_ranges.append((int(m.group(1)), int(m.group(2))))
        # 성별 추출 (캠페인 단위 한 번만)
        if re.search(r'남녀|남.*여중|여.*남중', tgt):
            genders.append('남녀')
        elif re.search(r'남(?:성|자)', tgt):
            genders.append('남성')
        elif re.search(r'여(?:성|자)', tgt):
            genders.append('여성')
        # 라인별 분류
        for line in tgt.split('\n'):
            raw_stripped = _strip_prefix(line)
            if not raw_stripped or not (6 < len(raw_stripped) < 100):
                continue
            # 데모 단독 라인(35-45 남성중 등) 스킵 — 연령·성별은 별도 집계
            if re.fullmatch(r'\d{2}\s*[-~]\s*\d{2}\s*세\s*[남녀]+\s*(중|우선|만)?', raw_stripped):
                continue
            if re.fullmatch(r'[남녀]+\s*\d{2}\s*[-~]\s*\d{2}\s*세\s*(중|우선|만)?', raw_stripped):
                continue

            norm_key = _normalize_for_grouping(raw_stripped)

            if _is_exclusion_line(raw_stripped):
                exclude_counter[norm_key] += 1
                if norm_key not in exclude_norm_to_rep:
                    exclude_norm_to_rep[norm_key] = raw_stripped
            else:
                include_counter[norm_key] += 1
                if norm_key not in include_norm_to_rep:
                    include_norm_to_rep[norm_key] = raw_stripped

    # 가장 흔한 연령 범위
    age_pref = None
    if age_ranges:
        most_common_age = Counter(age_ranges).most_common(1)[0][0]
        age_pref = most_common_age

    # 가장 흔한 성별
    gender_pref = Counter(genders).most_common(1)[0][0] if genders else None

    # 복사용 추천 텍스트 생성
    rec_lines = []
    if age_pref and gender_pref:
        rec_lines.append(f'{age_pref[0]}-{age_pref[1]}세 {gender_pref}')
    elif age_pref:
        rec_lines.append(f'{age_pref[0]}-{age_pref[1]}세')
    elif gender_pref:
        rec_lines.append(gender_pref)

    # 포함 조건 (2건 이상 반복된 것 Top 5) — 표시는 원본 라인
    top_includes = [
        (include_norm_to_rep[k], n)
        for k, n in include_counter.most_common(20) if n >= 2
    ][:5]
    if top_includes:
        rec_lines.append('')
        rec_lines.append('# 포함 조건 (베스트 캠페인 반복)')
        for line, n in top_includes:
            rec_lines.append(f'- {line}')

    # 제외 조건 (1건 이상) — 표시는 원본 라인
    top_excludes = [
        (exclude_norm_to_rep[k], n)
        for k, n in exclude_counter.most_common(10) if n >= 1
    ][:5]
    if top_excludes:
        rec_lines.append('')
        rec_lines.append('# 제외 조건 (디타겟팅)')
        for line, n in top_excludes:
            rec_lines.append(f'* {line}')

    return {
        'n_base': len(top),
        'avg_ctr': float(top['CTR_calc'].mean()),
        'min_ctr': float(top['CTR_calc'].min()),
        'max_ctr': float(top['CTR_calc'].max()),
        'common_includes': top_includes,
        'common_excludes': top_excludes,
        'age_pref': age_pref,
        'gender_pref': gender_pref,
        'recommended_text': '\n'.join(rec_lines) if rec_lines else '',
    }


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
def _ctr_color(ctr: float, avg: float) -> str:
    """평균 대비 CTR 색상."""
    if ctr >= avg * 1.5:
        return COLOR_SUCCESS
    if ctr >= avg:
        return COLOR_BLUE
    if ctr >= avg * 0.5:
        return COLOR_TEXT_SEC
    return COLOR_DANGER


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


def _format_target_text(text: str, *, max_lines: int | None = None) -> str:
    """타겟 텍스트 정리 — 전체 줄 표시 (축약 없음), 줄바꿈 보존.
    제외 조건('제외' 포함 줄)은 빨간색으로 강조.
    """
    if not text or pd.isna(text):
        return '<span style="color:#9CA3AF;">타겟 정보 없음</span>'
    lines = str(text).strip().split('\n')
    cleaned = []
    for line in lines:
        line = line.rstrip()
        if not line:
            continue
        # 제외 조건 = 디타겟팅 → 빨간 강조
        is_exclusion = '제외' in line or '미사용' in line or '미가입' in line
        escaped = esc_html_safe(line)
        if is_exclusion:
            cleaned.append(
                f'<span style="color:{COLOR_DANGER};font-weight:600;">'
                f'<span style="background:#FFF0EF;padding:1px 6px;border-radius:4px;'
                f'border:1px solid #FECACA;font-size:0.66rem;margin-right:6px;'
                f'text-transform:uppercase;letter-spacing:0.02em;">제외</span>'
                f'{escaped}</span>'
            )
        else:
            cleaned.append(escaped)
    # max_lines가 명시되면 그때만 잘라낸다 (기본은 전체)
    if max_lines is not None and len(cleaned) > max_lines:
        cleaned = cleaned[:max_lines] + [
            f'<span style="color:{COLOR_TEXT_TER};">… 외 {len(lines) - max_lines}줄</span>'
        ]
    return '<br>'.join(cleaned)


def _format_message_preview(text: str, max_chars: int = 60) -> str:
    """메시지 첫 줄(또는 제목) 미리보기."""
    if not text or pd.isna(text):
        return ''
    s = str(text).strip()
    # 첫 줄만
    first = s.split('\n')[0].strip()
    if len(first) > max_chars:
        first = first[:max_chars] + '…'
    return esc_html_safe(first)


def _best_target_card(row: pd.Series, *, rank: int, avg_ctr: float) -> str:
    """베스트 타겟 카드 — CTR 큰 숫자 + 매체 + 타겟 원문 + 메시지 미리보기."""
    ctr = float(row.get('CTR_calc', 0) or 0)
    sends = float(row.get('발송량', 0) or 0)
    clicks = float(row.get('클릭수', 0) or 0)
    media = str(row.get('매체명', row.get('매체', '—')))
    product = str(row.get('광고상품', ''))
    target = str(row.get('타겟', '') or '')
    message = str(row.get('메시지', '') or '')

    # 일자 — '년월' 또는 '날짜' 사용
    date_str = ''
    for col in ('년월', '날짜', '일자'):
        if col in row.index and pd.notna(row.get(col)):
            v = row[col]
            if hasattr(v, 'strftime'):
                date_str = v.strftime('%Y-%m-%d')
            else:
                date_str = str(v)
            break

    ctr_color = _ctr_color(ctr, avg_ctr)
    pct_vs_avg = f'평균 대비 +{(ctr/max(avg_ctr,0.01) - 1) * 100:.0f}%' if avg_ctr > 0 else ''

    rank_badge = (
        f'<span style="display:inline-flex;align-items:center;justify-content:center;'
        f'width:24px;height:24px;background:{BRAND_PRIMARY};color:white;border-radius:50%;'
        f'font-size:0.7rem;font-weight:700;">{rank}</span>'
    )
    product_chip = (
        f'<span style="font-size:0.65rem;color:{COLOR_TEXT_SEC};background:{COLOR_BG};'
        f'padding:2px 8px;border-radius:999px;font-weight:600;">{esc_html_safe(product)}</span>'
        if product else ''
    )
    msg_html = ''
    if message:
        msg_preview = _format_message_preview(message, max_chars=80)
        msg_html = (
            f'<div style="margin-top:10px;padding-top:10px;border-top:1px dashed {COLOR_BORDER_SUBTLE};'
            f'font-size:0.72rem;color:{COLOR_TEXT_TER};">'
            f'<span style="color:{COLOR_TEXT_SEC};font-weight:600;">메시지: </span>{msg_preview}'
            f'</div>'
        )

    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {ctr_color};'
        f'border-radius:8px;padding:16px 18px;background:{COLOR_CARD};margin-bottom:12px;">'
        # 상단: 순위 + 매체 + CTR
        f'  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:10px;">'
        f'    <div style="display:flex;align-items:center;gap:10px;">'
        f'      {rank_badge}'
        f'      <div>'
        f'        <div style="display:flex;align-items:center;gap:6px;">'
        f'          <span style="font-size:0.95rem;color:{COLOR_TEXT};font-weight:700;letter-spacing:-0.01em;">'
        f'{esc_html_safe(media)}</span>'
        f'          {product_chip}'
        f'        </div>'
        f'        <div style="font-size:0.68rem;color:{COLOR_TEXT_TER};margin-top:2px;">'
        f'{esc_html_safe(date_str)} · 발송 {compact_num(sends, "건")} · 클릭 {compact_num(clicks, "회")}</div>'
        f'      </div>'
        f'    </div>'
        f'    <div style="text-align:right;">'
        f'      <div style="font-size:1.6rem;color:{ctr_color};font-weight:700;line-height:1;letter-spacing:-0.01em;">'
        f'{ctr:.2f}%</div>'
        f'      <div style="font-size:0.62rem;color:{COLOR_TEXT_TER};margin-top:2px;">{pct_vs_avg or "CTR"}</div>'
        f'    </div>'
        f'  </div>'
        # 타겟 원문 박스
        f'  <div style="background:{COLOR_BG};border-radius:6px;padding:10px 14px;'
        f'font-size:0.78rem;color:{COLOR_TEXT};line-height:1.6;'
        f'border-left:2px solid {COLOR_BORDER};">'
        f'    <div style="font-size:0.62rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;margin-bottom:6px;">타겟 설정</div>'
        f'    {_format_target_text(target)}'
        f'  </div>'
        # 메시지 미리보기 (있으면)
        f'  {msg_html}'
        f'</div>'
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
            '타겟팅 분석은 Firebase 연동된 데이터에서만 동작합니다. '
            '엑셀 업로드 데이터는 메시지·발송·클릭만 있어 타겟 분석이 불가합니다.',
            icon='🎯',
        )
        return

    # 메시징 캠페인만
    if '광고상품' in df.columns:
        msg = df[~df['광고상품'].astype(str).str.upper().isin(['DA', 'CPA'])].copy()
    else:
        msg = df.copy()

    # 타겟 정보 있는 행만
    if '타겟' in msg.columns:
        msg = msg[msg['타겟'].notna() & (msg['타겟'].astype(str).str.strip() != '')].copy()

    # qualified
    qualified = msg[msg['발송량'] >= MIN_SENDS_FOR_CTR_CLAIM].copy()
    qualified['CTR_calc'] = np.where(
        qualified['발송량'] > 0,
        qualified['클릭수'].fillna(0) / qualified['발송량'] * 100,
        0,
    )
    # 추천 엔진용 매체 카테고리
    if '매체명' in qualified.columns:
        qualified['_recommend_cat'] = qualified['매체명'].astype(str).apply(_classify_for_recommend)
    elif '매체' in qualified.columns:
        qualified['_recommend_cat'] = qualified['매체'].astype(str).apply(_classify_for_recommend)
    else:
        qualified['_recommend_cat'] = '플랫폼·기타'

    n_total = len(df)
    n_with_target = len(msg)
    n_qualified = len(qualified)

    render_page_header(
        title='타겟팅 성과',
        kicker='운영',
        subtitle=(
            f'실제 캠페인의 <b>타겟 설정 원문</b>을 CTR 높은 순으로 표시합니다. '
            f'발송 <b>{MIN_SENDS_FOR_CTR_CLAIM:,}건</b> 이상 메시징 캠페인 기준.'
        ),
    )

    # ── 4 summary strip ──
    if n_qualified > 0:
        avg_ctr = qualified['클릭수'].fillna(0).sum() / qualified['발송량'].sum() * 100
        best_ctr = float(qualified['CTR_calc'].max())
        median_ctr = float(qualified['CTR_calc'].median())
    else:
        avg_ctr = 0
        best_ctr = 0
        median_ctr = 0

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_summary_card(
            '분석 대상 캠페인', f'{n_qualified}건',
            f'타겟 정보 보유 {n_with_target}건 / 전체 {n_total}건',
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_summary_card(
            '평균 CTR', f'{avg_ctr:.2f}%',
            f'중앙값 {median_ctr:.2f}%',
        ), unsafe_allow_html=True)
    with c3:
        st.markdown(_summary_card(
            '최고 CTR', f'{best_ctr:.2f}%',
            (f'평균 대비 {best_ctr/max(avg_ctr,0.01):.1f}배' if avg_ctr > 0 else '—'),
            color=COLOR_SUCCESS,
        ), unsafe_allow_html=True)
    with c4:
        # 타겟 텍스트 평균 길이 (정교한 타겟일수록 길다는 경향)
        avg_target_len = int(qualified['타겟'].astype(str).str.len().mean()) if n_qualified > 0 else 0
        st.markdown(_summary_card(
            '평균 타겟 길이', f'{avg_target_len}자',
            ('정교한 SEG일수록 길어지는 경향' if avg_target_len > 50 else '단순 인구통계 위주'),
        ), unsafe_allow_html=True)

    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    if n_qualified < 1:
        st.markdown(
            f'<div style="border:1px dashed {COLOR_BORDER};border-radius:8px;'
            f'padding:14px 16px;color:{COLOR_TEXT_TER};font-size:0.8rem;text-align:center;">'
            f'분석 가능한 캠페인이 부족합니다. 발송 {MIN_SENDS_FOR_CTR_CLAIM:,}건 이상 메시징 캠페인이 없습니다.</div>',
            unsafe_allow_html=True,
        )
        return

    # ═══════════════════════════════════════════════
    # 베스트 타겟 Top 10
    # ═══════════════════════════════════════════════
    st.markdown(
        f'<div style="font-size:1rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:6px;letter-spacing:-0.01em;">🏆 베스트 타겟 Top 10</div>'
        f'<div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};margin-bottom:14px;">'
        f'CTR 높은 캠페인의 실제 타겟 설정을 그대로 표시합니다. '
        f'평균 대비 좋은 정도에 따라 색이 달라집니다.</div>',
        unsafe_allow_html=True,
    )

    top_n = qualified.nlargest(10, 'CTR_calc')
    for rank, (_, row) in enumerate(top_n.iterrows(), start=1):
        st.markdown(_best_target_card(row, rank=rank, avg_ctr=avg_ctr), unsafe_allow_html=True)

    # ═══════════════════════════════════════════════
    # 매체별 베스트 1건
    # ═══════════════════════════════════════════════
    if '매체명' in qualified.columns and qualified['매체명'].nunique() >= 3:
        st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-size:1rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:6px;letter-spacing:-0.01em;">📺 매체별 베스트 타겟</div>'
            f'<div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};margin-bottom:14px;">'
            f'각 매체에서 가장 좋은 성과를 낸 캠페인의 타겟 — 매체별 패턴 비교용.</div>',
            unsafe_allow_html=True,
        )

        # 매체별 베스트 (CTR 최고, 발송 5천+)
        media_best = (
            qualified.sort_values('CTR_calc', ascending=False)
            .drop_duplicates(subset='매체명', keep='first')
        )

        # 매체별 캠페인 수 (1건만 있는 매체는 비교 가치 낮으니 제외)
        media_counts = qualified.groupby('매체명').size()
        valid_medias = media_counts[media_counts >= 1].index
        media_best = media_best[media_best['매체명'].isin(valid_medias)].head(8)

        # 2열 컴팩트 카드
        rows = list(media_best.iterrows())
        for i in range(0, len(rows), 2):
            cols = st.columns(2)
            for j, col in enumerate(cols):
                if i + j >= len(rows):
                    break
                _, row = rows[i + j]
                with col:
                    ctr = float(row['CTR_calc'])
                    media = str(row.get('매체명', ''))
                    target = str(row.get('타겟', '') or '')
                    sends = float(row.get('발송량', 0))

                    ctr_color = _ctr_color(ctr, avg_ctr)

                    st.markdown(
                        f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {ctr_color};'
                        f'border-radius:8px;padding:12px 14px;background:{COLOR_CARD};margin-bottom:8px;">'
                        f'  <div style="display:flex;justify-content:space-between;align-items:flex-start;'
                        f'margin-bottom:6px;">'
                        f'    <div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:700;'
                        f'letter-spacing:-0.005em;">{esc_html_safe(media)}</div>'
                        f'    <div style="text-align:right;">'
                        f'      <div style="font-size:1rem;color:{ctr_color};font-weight:700;line-height:1;">'
                        f'{ctr:.2f}%</div>'
                        f'      <div style="font-size:0.6rem;color:{COLOR_TEXT_TER};margin-top:1px;">'
                        f'{compact_num(sends, "건")}</div>'
                        f'    </div>'
                        f'  </div>'
                        f'  <div style="font-size:0.72rem;color:{COLOR_TEXT_SEC};line-height:1.5;'
                        f'background:{COLOR_BG};padding:8px 10px;border-radius:6px;'
                        f'white-space:normal;">'
                        f'{_format_target_text(target)}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

    # ═══════════════════════════════════════════════
    # 🎁 추천 타겟 (통신사 / 카드사 별)
    # ═══════════════════════════════════════════════
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:1rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:6px;letter-spacing:-0.01em;">🎁 추천 타겟 (자동 생성)</div>'
        f'<div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};margin-bottom:14px;">'
        f'각 카테고리에서 <b>CTR 상위 30%</b> 캠페인의 타겟 텍스트를 분석해 '
        f'반복되는 패턴을 추출했습니다. 운영팀이 그대로 복사해 새 캠페인에 활용할 수 있어요.</div>',
        unsafe_allow_html=True,
    )

    rec_telecom = _recommend_targeting(qualified, '통신사')
    rec_card = _recommend_targeting(qualified, '카드사')

    rec_col1, rec_col2 = st.columns(2)
    for col, rec, cat_label, cat_color in [
        (rec_col1, rec_telecom, '통신사', COLOR_BLUE),
        (rec_col2, rec_card, '카드사', BRAND_PRIMARY),
    ]:
        with col:
            if rec is None:
                st.markdown(
                    f'<div style="border:1px dashed {COLOR_BORDER};border-radius:8px;'
                    f'padding:14px 16px;background:{COLOR_CARD};margin-bottom:12px;height:100%;">'
                    f'  <div style="font-size:0.85rem;font-weight:700;color:{cat_color};margin-bottom:6px;">'
                    f'{cat_label} 추천 타겟</div>'
                    f'  <div style="font-size:0.78rem;color:{COLOR_TEXT_TER};line-height:1.55;">'
                    f'{cat_label} 캠페인이 5건 미만이라 추천을 생성할 수 없습니다. '
                    f'더 많은 데이터가 쌓이면 자동으로 인사이트가 생성됩니다.</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                continue

            # 헤더 + 메트릭
            age_str = (
                f'{rec["age_pref"][0]}-{rec["age_pref"][1]}세' if rec['age_pref'] else '연령 다양'
            )
            gender_str = rec['gender_pref'] or '성별 다양'

            st.markdown(
                f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {cat_color};'
                f'border-radius:8px;padding:14px 16px;background:{COLOR_CARD};margin-bottom:10px;">'
                # 헤더
                f'  <div style="display:flex;align-items:center;justify-content:space-between;'
                f'gap:10px;margin-bottom:10px;">'
                f'    <div style="font-size:0.95rem;font-weight:700;color:{cat_color};'
                f'letter-spacing:-0.01em;">{cat_label} 추천 타겟</div>'
                f'    <div style="font-size:0.62rem;color:{COLOR_TEXT_TER};">'
                f'{rec["n_base"]}개 베스트 캠페인 기반</div>'
                f'  </div>'
                # 기대 CTR
                f'  <div style="background:{COLOR_BG};border-radius:6px;padding:10px 12px;'
                f'margin-bottom:12px;display:flex;gap:14px;align-items:baseline;">'
                f'    <div>'
                f'      <div style="font-size:0.62rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
                f'letter-spacing:0.04em;font-weight:600;">기대 CTR</div>'
                f'      <div style="font-size:1.1rem;color:{COLOR_TEXT};font-weight:700;line-height:1.2;">'
                f'{rec["avg_ctr"]:.2f}%</div>'
                f'    </div>'
                f'    <div style="font-size:0.7rem;color:{COLOR_TEXT_TER};line-height:1.4;">'
                f'베스트 범위: {rec["min_ctr"]:.2f}% ~ {rec["max_ctr"]:.2f}%'
                f'</div>'
                f'  </div>'
                # 기본 타겟 (연령·성별)
                f'  <div style="margin-bottom:8px;">'
                f'    <div style="font-size:0.65rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
                f'letter-spacing:0.04em;font-weight:600;margin-bottom:4px;">권장 모수</div>'
                f'    <div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:600;">'
                f'{esc_html_safe(age_str)} · {esc_html_safe(gender_str)}</div>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True,
            )

            # 포함 조건 리스트
            if rec['common_includes']:
                items_html = ''.join(
                    f'<li style="margin-bottom:4px;">'
                    f'<span style="color:{COLOR_TEXT};">{esc_html_safe(line)}</span>'
                    f' <span style="font-size:0.62rem;color:{COLOR_TEXT_TER};">×{n}회</span>'
                    f'</li>'
                    for line, n in rec['common_includes']
                )
                st.markdown(
                    f'<div style="border:1px solid {COLOR_BORDER_SUBTLE};border-radius:8px;'
                    f'padding:10px 14px;background:{COLOR_CARD};margin-bottom:8px;">'
                    f'  <div style="font-size:0.65rem;color:{COLOR_SUCCESS};text-transform:uppercase;'
                    f'letter-spacing:0.04em;font-weight:600;margin-bottom:6px;">✓ 추천 포함 조건</div>'
                    f'  <ul style="margin:0;padding-left:18px;font-size:0.78rem;line-height:1.6;">'
                    f'{items_html}'
                    f'  </ul>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # 제외 조건 리스트
            if rec['common_excludes']:
                items_html = ''.join(
                    f'<li style="margin-bottom:4px;color:{COLOR_DANGER};">'
                    f'{esc_html_safe(line)}'
                    f' <span style="font-size:0.62rem;color:{COLOR_TEXT_TER};">×{n}회</span>'
                    f'</li>'
                    for line, n in rec['common_excludes']
                )
                st.markdown(
                    f'<div style="border:1px solid #FECACA;border-radius:8px;'
                    f'padding:10px 14px;background:#FFF8F8;margin-bottom:8px;">'
                    f'  <div style="font-size:0.65rem;color:{COLOR_DANGER};text-transform:uppercase;'
                    f'letter-spacing:0.04em;font-weight:600;margin-bottom:6px;">⛔ 추천 제외 조건 (디타겟팅)</div>'
                    f'  <ul style="margin:0;padding-left:18px;font-size:0.78rem;line-height:1.6;">'
                    f'{items_html}'
                    f'  </ul>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div style="border:1px dashed {COLOR_BORDER_SUBTLE};border-radius:8px;'
                    f'padding:8px 14px;font-size:0.7rem;color:{COLOR_TEXT_TER};margin-bottom:8px;">'
                    f'⛔ 제외 조건 데이터가 부족합니다 (운영 시스템 동기화 확인 필요)'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # 복사용 텍스트
            if rec['recommended_text']:
                st.caption('📋 복사용 추천 타겟 텍스트')
                st.code(rec['recommended_text'], language='text')

    # ═══════════════════════════════════════════════
    # 전체 캠페인 검색 가능 테이블
    # ═══════════════════════════════════════════════
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:1rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:6px;letter-spacing:-0.01em;">🔍 전체 캠페인 검색</div>'
        f'<div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};margin-bottom:14px;">'
        f'타겟 텍스트로 검색하거나 컬럼 헤더를 클릭해 정렬하세요.</div>',
        unsafe_allow_html=True,
    )

    # 검색 입력
    search_q = st.text_input(
        '타겟 텍스트 검색',
        placeholder='예: "1년내", "수발신", "남성", "유치원"',
        key='tgt_search',
        label_visibility='collapsed',
    )

    table_df = qualified.copy()
    if search_q:
        mask = table_df['타겟'].astype(str).str.contains(search_q, case=False, na=False, regex=False)
        table_df = table_df[mask]
        st.caption(f'검색 결과: {len(table_df)}건 / 전체 {n_qualified}건')

    if table_df.empty:
        st.info('검색 조건에 맞는 캠페인이 없습니다.')
        return

    # 표시용 컬럼 선택 + 정리
    display_cols = []
    rename_map = {}

    for src, dst in [
        ('년월', '월'),
        ('날짜', '일자'),
        ('매체명', '매체'),
        ('광고상품', '상품'),
        ('타겟', '타겟 설정'),
        ('발송량', '발송'),
        ('클릭수', '클릭'),
        ('CTR_calc', 'CTR(%)'),
    ]:
        if src in table_df.columns:
            display_cols.append(src)
            rename_map[src] = dst

    table_view = table_df[display_cols].rename(columns=rename_map).copy()
    if 'CTR(%)' in table_view.columns:
        table_view['CTR(%)'] = table_view['CTR(%)'].round(2)
    if '발송' in table_view.columns:
        table_view['발송'] = table_view['발송'].astype(int)
    if '클릭' in table_view.columns:
        table_view['클릭'] = pd.to_numeric(table_view['클릭'], errors='coerce').fillna(0).astype(int)
    if '일자' in table_view.columns:
        table_view['일자'] = pd.to_datetime(table_view['일자'], errors='coerce').dt.strftime('%Y-%m-%d')

    # CTR 높은 순 기본 정렬
    if 'CTR(%)' in table_view.columns:
        table_view = table_view.sort_values('CTR(%)', ascending=False)

    st.dataframe(
        table_view,
        use_container_width=True,
        hide_index=True,
        height=min(420, 56 + len(table_view) * 36),
        column_config={
            'CTR(%)': st.column_config.NumberColumn('CTR(%)', format='%.2f'),
            '발송': st.column_config.NumberColumn('발송', format='%d'),
            '클릭': st.column_config.NumberColumn('클릭', format='%d'),
            '타겟 설정': st.column_config.TextColumn('타겟 설정', width='large'),
        },
    )

    # ── 하단 안내 ──
    st.markdown('<div class="space-sm"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER_SUBTLE};border-radius:8px;'
        f'padding:10px 14px;background:{COLOR_BG};font-size:0.72rem;color:{COLOR_TEXT_SEC};'
        f'line-height:1.55;">'
        f'💡 키워드 분류 없이 실제 타겟 설정을 그대로 보여드립니다. '
        f'<span style="color:{COLOR_DANGER};font-weight:600;">'
        f'<span style="background:#FFF0EF;padding:1px 6px;border-radius:4px;'
        f'border:1px solid #FECACA;font-size:0.62rem;margin:0 3px;'
        f'text-transform:uppercase;letter-spacing:0.02em;">제외</span></span> '
        f'배지는 디타겟팅 조건(제외/미사용/미가입)을 표시합니다. '
        f'검색창에 "1년내", "수발신", "제외" 등을 입력해 비슷한 타겟끼리 비교해보세요.'
        f'</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────
# (보존) 키워드 파서 — 추후 자동 분류 필요 시 재활용
# ──────────────────────────────────────────────
# 이전 버전의 행동·시간·정밀도 파서는 외부 호출이 없으므로 제거.
# 필요 시 git history에서 복원 가능 (커밋 ae55681 이전).
