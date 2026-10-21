"""
발송 스케줄 캘린더 (Schedule Calendar)
──────────────────────────────────────
광고주 선택 직후 가장 먼저 보이는 화면.

답하는 질문:
  - 이번 달 언제 발송했나? (달력 한눈에)
  - 그날 누구(매체)한테 얼마나(발송량) 보냈고 광고비는 얼마였나?
  - 그 내용의 상세는? (캘린더 아래 발송 리스트)

설계:
  - 월 단위 7열 캘린더 그리드 (일요일 시작)
  - 발송일 셀: 매체 칩 + 발송량 + 광고비, 발송량에 따라 배경 농도
  - 캘린더 아래: 같은 월 발송 상세 표 (일자·매체·상품·발송·클릭·CTR·광고비·DB)
"""
import calendar
import re
from datetime import date

import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_BG, COLOR_SUCCESS, COLOR_DANGER, BRAND_PRIMARY,
    compact_num,
)
from modules.ui_helpers import render_empty_state, esc_html_safe

_WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토']

# 타겟 요약용 — 연령·성별과 대표 시그널 키워드
_RE_AGE = re.compile(r'(\d{2})\s*[-~]\s*(\d{2})\s*세?')
_SIGNAL_KEYWORDS = [
    '유치원', '어린이집', '초등', '중등', '중학', '고등', '스쿨뱅킹', '산후조리원',
    '국민행복카드', '이유식', '기저귀', '학원', '수발신', '통화', '교복', '키즈',
    '소아과', '치과', '아동복', '도서', '참고서', '캠핑', '여행', '보험', '카드',
]


def _summarize_target(raw) -> str:
    """타겟 원문 → 캘린더 셀에 들어갈 한 줄 요약.

    예) "35-54세 여성중 / 1년내 초등학교 수발신대상자" → "35-54세 여성·초등·수발신"
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return ''
    s = str(raw).strip()
    if not s:
        return ''
    flat = re.sub(r'\s+', ' ', s.replace('\n', ' '))

    parts = []
    m = _RE_AGE.search(flat)
    if m:
        parts.append(f'{m.group(1)}-{m.group(2)}세')
    if re.search(r'남녀|남성.*여성|여성.*남성', flat):
        parts.append('남녀')
    elif re.search(r'여성|여자', flat):
        parts.append('여성')
    elif re.search(r'남성|남자', flat):
        parts.append('남성')

    sigs = [k for k in _SIGNAL_KEYWORDS if k in flat][:2]
    parts.extend(sigs)

    if not parts:
        return flat[:18] + ('…' if len(flat) > 18 else '')
    return '·'.join(parts)


def _cell_bg(ratio: float) -> str:
    """발송량 비중(0~1) → 브랜드 컬러 농도 배경."""
    if ratio <= 0:
        return COLOR_CARD
    # 0.06 ~ 0.30 알파 범위로 매핑 (너무 진하지 않게)
    alpha = 0.06 + min(ratio, 1.0) * 0.24
    return f'rgba(247, 147, 29, {alpha:.2f})'


def _day_cell_html(
    day: int | None,
    info: dict | None,
    *,
    max_send: float,
    is_weekend: bool,
    is_today: bool,
) -> str:
    """캘린더 한 칸 HTML."""
    if day is None:
        return f'<div style="min-height:88px;background:transparent;"></div>'

    has = info is not None
    ratio = (info['발송'] / max_send) if (has and max_send > 0) else 0
    bg = _cell_bg(ratio)
    border = BRAND_PRIMARY if is_today else COLOR_BORDER_SUBTLE
    border_w = '2px' if is_today else '1px'
    num_color = COLOR_DANGER if is_weekend else COLOR_TEXT_SEC

    body = ''
    if has:
        # 그날의 모든 캠페인을 한 줄씩 — 생략(+N) 없음
        lines = []
        for c in info['캠페인목록']:
            tgt = c.get('타겟요약', '')
            tgt_html = (
                f'<div style="font-size:0.56rem;color:{COLOR_TEXT_TER};line-height:1.3;'
                f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                f'{esc_html_safe(tgt)}</div>'
                if tgt else ''
            )
            qty_cost = compact_num(c['발송'], '건') if c['발송'] > 0 else ''
            if c['광고비'] > 0:
                qty_cost = (
                    f'{qty_cost} · {compact_num(c["광고비"], "원")}'
                    if qty_cost else compact_num(c['광고비'], '원')
                )
            lines.append(
                f'<div style="border-top:1px dotted {COLOR_BORDER_SUBTLE};padding-top:2px;'
                f'margin-top:2px;">'
                f'  <div style="font-size:0.6rem;color:{COLOR_TEXT};font-weight:700;'
                f'line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                f'{esc_html_safe(c["매체"])}'
                + (f'<span style="color:{COLOR_TEXT_TER};font-weight:500;"> {esc_html_safe(c["상품"])}</span>'
                   if c.get('상품') else '')
                + f'</div>'
                f'  {tgt_html}'
                f'  <div style="font-size:0.58rem;color:{BRAND_PRIMARY};font-weight:600;'
                f'line-height:1.3;">{qty_cost}</div>'
                f'</div>'
            )
        body = f'<div style="margin-top:2px;">{"".join(lines)}</div>'

    return (
        f'<div style="min-height:88px;border:{border_w} solid {border};border-radius:8px;'
        f'background:{bg};padding:5px 6px;">'
        f'  <div style="font-size:0.68rem;color:{num_color};font-weight:600;">{day}</div>'
        f'  {body}'
        f'</div>'
    )


def _summary_card(title: str, value: str, sub: str) -> str:
    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;padding:12px 14px;'
        f'background:{COLOR_CARD};">'
        f'  <div style="font-size:0.65rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;margin-bottom:4px;">{esc_html_safe(title)}</div>'
        f'  <div style="font-size:1.05rem;color:{COLOR_TEXT};font-weight:700;line-height:1.2;">'
        f'{esc_html_safe(value)}</div>'
        f'  <div style="font-size:0.7rem;color:{COLOR_TEXT_TER};margin-top:3px;">'
        f'{esc_html_safe(sub)}</div>'
        f'</div>'
    )


def render_calendar(df: pd.DataFrame, *, key_prefix: str = 'sched') -> None:
    """발송 스케줄 캘린더 + 해당 월 발송 상세.

    df: 표준 컬럼(날짜·매체명·발송량·클릭수·집행금액) 보유 광고주 데이터
    """
    if df is None or df.empty or '날짜' not in df.columns:
        render_empty_state('발송 데이터가 없습니다', icon='📅')
        return

    work = df.dropna(subset=['날짜']).copy()
    if work.empty:
        render_empty_state('발송 일자 데이터가 없습니다', icon='📅')
        return
    work['_d'] = pd.to_datetime(work['날짜'], errors='coerce')
    work = work.dropna(subset=['_d'])
    work['_ym'] = work['_d'].dt.strftime('%Y년 %m월')

    months = sorted(work['_ym'].unique())
    if not months:
        render_empty_state('발송 일자 데이터가 없습니다', icon='📅')
        return

    # ── 월 선택 ──
    col_sel, col_meta = st.columns([1.1, 3])
    with col_sel:
        sel_month = st.selectbox(
            '발송 월', months, index=len(months) - 1,
            key=f'{key_prefix}_cal_month', label_visibility='collapsed',
        )
    month_df = work[work['_ym'] == sel_month].copy()
    year, mon = int(sel_month[:4]), int(sel_month[6:8])

    # ── 일자별 집계 ──
    daily = month_df.groupby(month_df['_d'].dt.day).agg(
        캠페인=('매체명', 'size'),
        발송=('발송량', 'sum'),
        클릭=('클릭수', lambda x: x.fillna(0).sum()),
        광고비=('집행금액', 'sum'),
    )
    # 날짜별 캠페인 목록 (매체·상품·발송·광고비·타겟요약)
    camps_by_day: dict[int, list[dict]] = {}
    for _, r in month_df.iterrows():
        d = int(r['_d'].day)
        camps_by_day.setdefault(d, []).append({
            '매체': str(r.get('매체명', '') or '—'),
            '상품': str(r.get('광고상품', '') or ''),
            '발송': float(r.get('발송량', 0) or 0),
            '광고비': float(r.get('집행금액', 0) or 0),
            '타겟요약': _summarize_target(r.get('타겟')),
        })
    for d in camps_by_day:
        camps_by_day[d].sort(key=lambda c: -c['광고비'])

    day_map = {}
    for d, row in daily.iterrows():
        day_map[int(d)] = {
            '캠페인': int(row['캠페인']),
            '발송': float(row['발송']),
            '클릭': float(row['클릭']),
            '광고비': float(row['광고비']),
            '캠페인목록': camps_by_day.get(int(d), []),
        }

    total_send = float(month_df['발송량'].sum())
    total_cost = float(month_df['집행금액'].sum())
    n_days = len(day_map)
    n_camp = len(month_df)
    n_media = month_df['매체명'].nunique() if '매체명' in month_df.columns else 0
    max_send = max((v['발송'] for v in day_map.values()), default=0)

    with col_meta:
        st.markdown(
            f'<div style="font-size:0.8rem;color:{COLOR_TEXT_SEC};padding-top:8px;">'
            f'<b style="color:{COLOR_TEXT};">{esc_html_safe(sel_month)}</b> · '
            f'발송일 <b style="color:{COLOR_TEXT};">{n_days}일</b> · '
            f'캠페인 <b style="color:{COLOR_TEXT};">{n_camp}건</b> · '
            f'매체 <b style="color:{COLOR_TEXT};">{n_media}개</b></div>',
            unsafe_allow_html=True,
        )

    # ── 요약 4카드 ──
    st.markdown('<div class="space-sm"></div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_summary_card(
            '발송일', f'{n_days}일',
            f'캠페인 {n_camp}건' + (f' · 최다 {int(daily["캠페인"].max())}건/일' if n_days else ''),
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_summary_card(
            '총 발송량', f'{total_send:,.0f}건',
            f'일평균 {total_send / n_days:,.0f}건' if n_days else '—',
        ), unsafe_allow_html=True)
    with c3:
        st.markdown(_summary_card(
            '총 광고비', compact_num(total_cost, '원'),
            f'일평균 {compact_num(total_cost / n_days, "원")}' if n_days else '—',
        ), unsafe_allow_html=True)
    with c4:
        _clicks = float(month_df['클릭수'].fillna(0).sum())
        _ctr = (_clicks / total_send * 100) if total_send > 0 else 0
        st.markdown(_summary_card(
            '클릭 · CTR', f'{_clicks:,.0f}회',
            f'CTR {_ctr:.2f}%',
        ), unsafe_allow_html=True)

    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    # ── 캘린더 그리드 ──
    cal = calendar.Calendar(firstweekday=6)  # 6 = 일요일 시작
    weeks = cal.monthdayscalendar(year, mon)
    today = date.today()

    head = ''.join(
        f'<div style="text-align:center;font-size:0.68rem;font-weight:700;'
        f'color:{COLOR_DANGER if i in (0, 6) else COLOR_TEXT_SEC};padding:4px 0;">'
        f'{w}</div>'
        for i, w in enumerate(_WEEKDAYS)
    )
    cells = []
    for week in weeks:
        for i, d in enumerate(week):
            if d == 0:
                cells.append(_day_cell_html(None, None, max_send=max_send,
                                            is_weekend=False, is_today=False))
            else:
                cells.append(_day_cell_html(
                    d, day_map.get(d),
                    max_send=max_send,
                    is_weekend=(i in (0, 6)),
                    is_today=(today.year == year and today.month == mon and today.day == d),
                ))

    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER};border-radius:12px;'
        f'padding:12px 14px;background:{COLOR_CARD};">'
        f'  <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:6px;'
        f'margin-bottom:4px;">{head}</div>'
        f'  <div style="display:grid;grid-template-columns:repeat(7,1fr);gap:6px;">'
        + ''.join(cells) +
        f'  </div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    st.caption('셀 배경이 진할수록 그날 발송량이 많습니다. 매체는 2개까지 표시되며 나머지는 +N으로 묶입니다.')

    # ── 발송 상세 리스트 ──
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:6px;letter-spacing:-0.01em;">{esc_html_safe(sel_month)} 발송 상세</div>'
        f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:10px;">'
        f'위 캘린더에 표시된 발송 내역입니다. 컬럼 헤더를 클릭해 정렬할 수 있어요.</div>',
        unsafe_allow_html=True,
    )

    detail = month_df.sort_values('_d').copy()
    detail['일자'] = detail['_d'].dt.strftime('%m/%d')
    detail['요일'] = detail['_d'].dt.weekday.map(
        {0: '월', 1: '화', 2: '수', 3: '목', 4: '금', 5: '토', 6: '일'}
    )
    detail['CTR(%)'] = np.where(
        detail['발송량'] > 0,
        detail['클릭수'].fillna(0) / detail['발송량'] * 100,
        np.nan,
    ).round(2)

    cols = ['일자', '요일', '매체명']
    if '광고상품' in detail.columns:
        cols.append('광고상품')
    cols += ['발송량', '클릭수', 'CTR(%)', '집행금액']
    if 'DB' in detail.columns:
        detail['DB'] = pd.to_numeric(detail['DB'], errors='coerce').fillna(0)
        if (detail['DB'] > 0).any():
            detail['CPA단가(원)'] = np.where(
                detail['DB'] > 0, detail['집행금액'] / detail['DB'], np.nan
            ).round(0)
            cols += ['DB', 'CPA단가(원)']

    view = detail[[c for c in cols if c in detail.columns]].copy()
    for c in ('발송량', '클릭수', '집행금액', 'DB'):
        if c in view.columns:
            view[c] = pd.to_numeric(view[c], errors='coerce').fillna(0).astype(int)
    view = view.rename(columns={'매체명': '매체', '광고상품': '상품', '집행금액': '광고비(원)'})

    st.dataframe(
        view, use_container_width=True, hide_index=True,
        height=min(420, 56 + len(view) * 36),
    )
