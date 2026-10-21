"""
발송 타이밍 최적화 (Send Timing)
────────────────────────────────
"다음 캠페인 언제 쏠까?"에 데이터로 답한다.

구성:
  1) 다음 발송 추천 슬롯 — 업종 검증 + 자사 미시도 구간 우선
  2) 자사 발송 패턴 진단 — 특정 요일·시간에 쏠려 있는지
  3) 요일 × 시간대 CTR 히트맵 (업종 기준, 자사 집행 위치 표시)
  4) 매체별 최적 시간대

LMS는 발송 시각을 운영자가 직접 정하므로, 이 분석이 곧 실행 가능한 액션이다.
"""
import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_BG, COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING, COLOR_BLUE,
    BRAND_PRIMARY, compact_num, MIN_SENDS_FOR_CTR_CLAIM,
)
from modules.ui_helpers import render_page_header, render_empty_state, esc_html_safe

_DOW = ['월', '화', '수', '목', '금', '토', '일']
_MIN_SLOT_N = 5   # 슬롯 신뢰 최소 캠페인 수


def _prep(df: pd.DataFrame, date_col: str, send_col: str, click_col: str) -> pd.DataFrame:
    """메시징 + qualified 행에 요일·시간 파생 추가."""
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if '광고상품' in out.columns:
        out = out[~out['광고상품'].astype(str).str.upper().isin(['DA', 'CPA'])]
    out['_d'] = pd.to_datetime(out[date_col], errors='coerce')
    out = out.dropna(subset=['_d'])
    out['_send'] = pd.to_numeric(out[send_col], errors='coerce').fillna(0)
    out = out[out['_send'] >= MIN_SENDS_FOR_CTR_CLAIM]
    if out.empty:
        return out
    out['_click'] = pd.to_numeric(out[click_col], errors='coerce').fillna(0)
    out['_hour'] = out['_d'].dt.hour
    out['_dow'] = out['_d'].dt.dayofweek
    return out


def _agg_ctr(g) -> pd.DataFrame:
    r = g.agg(n=('_send', 'size'), 발송=('_send', 'sum'), 클릭=('_click', 'sum')).reset_index()
    r['CTR'] = np.where(r['발송'] > 0, r['클릭'] / r['발송'] * 100, 0)
    return r


def _card(title: str, value: str, sub: str, *, color: str = None) -> str:
    color = color or COLOR_TEXT
    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;padding:12px 14px;'
        f'background:{COLOR_CARD};">'
        f'<div style="font-size:0.65rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;margin-bottom:4px;">{esc_html_safe(title)}</div>'
        f'<div style="font-size:1.05rem;color:{color};font-weight:700;line-height:1.2;">'
        f'{esc_html_safe(value)}</div>'
        f'<div style="font-size:0.68rem;color:{COLOR_TEXT_TER};margin-top:3px;line-height:1.4;">'
        f'{esc_html_safe(sub)}</div></div>'
    )


def render(df: pd.DataFrame):
    render_page_header(
        title='발송 타이밍',
        kicker='운영',
        subtitle=(
            '언제 보내야 가장 잘 열리는지 — 자사 발송 패턴을 진단하고 '
            '업종 데이터로 검증된 <b>다음 발송 시점</b>을 제안합니다.'
        ),
    )

    mine = _prep(df, '날짜', '발송량', '클릭수')
    if mine.empty:
        render_empty_state(
            '분석할 발송 데이터가 부족합니다',
            f'발송 {MIN_SENDS_FOR_CTR_CLAIM:,}건 이상인 메시징 캠페인이 필요합니다.',
            icon='⏰',
        )
        return

    brand = ''
    industry = ''
    for col in ('_브랜드', '광고주'):
        if col in df.columns:
            v = df[col].dropna().astype(str)
            if not v.empty:
                brand = str(v.iloc[0]); break
    if '분야' in df.columns:
        c = df['분야'].dropna().astype(str).value_counts()
        if not c.empty:
            industry = str(c.index[0])

    # 업종 벤치마크 + 전 업종(크로스) 벤치마크
    # 같은 업종이 특정 시간대에만 몰려 있으면 비교군 자체가 없으므로,
    # 전 업종 데이터를 보조로 써서 "아직 아무도 안 쓰는 좋은 시간대"를 찾는다.
    bench = pd.DataFrame()
    cross = pd.DataFrame()
    try:
        from modules.firebase_connector import load_from_firestore
        full = load_from_firestore()
        if full is not None and not full.empty:
            cross = _prep(full, '_date', '발송건', '클릭수')
            if industry and '분야' in full.columns:
                bench = _prep(full[full['분야'] == industry], '_date', '발송건', '클릭수')
    except Exception:
        bench = pd.DataFrame()
    if bench.empty:
        bench = cross

    # ── 자사 현황 ──
    my_hours = _agg_ctr(mine.groupby('_hour'))
    my_dows = _agg_ctr(mine.groupby('_dow'))
    top_hour = int(my_hours.loc[my_hours['n'].idxmax(), '_hour'])
    conc = float(my_hours['n'].max() / my_hours['n'].sum() * 100)
    my_ctr = float(mine['_click'].sum() / mine['_send'].sum() * 100)

    best_my_dow = my_dows.loc[my_dows['CTR'].idxmax()] if not my_dows.empty else None

    # ── 슬롯(요일×시간): 업종 우선, 표본 없는 슬롯은 전 업종으로 보완 ──
    slots = pd.DataFrame()
    if not bench.empty:
        slots = _agg_ctr(bench.groupby(['_dow', '_hour']))
        slots = slots[slots['n'] >= _MIN_SLOT_N]
        slots['출처'] = f'{industry} 업종' if industry else '업종'
    if not cross.empty:
        cs = _agg_ctr(cross.groupby(['_dow', '_hour']))
        cs = cs[cs['n'] >= _MIN_SLOT_N]
        cs['출처'] = '전 업종'
        if slots.empty:
            slots = cs
        else:
            known = set(zip(slots['_dow'], slots['_hour']))
            extra = cs[~cs.apply(lambda r: (r['_dow'], r['_hour']) in known, axis=1)]
            slots = pd.concat([slots, extra], ignore_index=True)
    if not slots.empty:
        slots = slots.sort_values('CTR', ascending=False)

    # 자사가 이미 쓰는 슬롯
    my_slots = set(zip(mine['_dow'], mine['_hour']))

    # ── 요약 4카드 ──
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(_card(
            '주력 발송 시간', f'{top_hour}시',
            f'전체 캠페인의 {conc:.0f}% 집중',
            color=COLOR_WARNING if conc >= 80 else COLOR_TEXT,
        ), unsafe_allow_html=True)
    with c2:
        st.markdown(_card(
            '자사 평균 CTR', f'{my_ctr:.2f}%',
            f'qualified {len(mine)}건 기준',
        ), unsafe_allow_html=True)
    with c3:
        if best_my_dow is not None:
            st.markdown(_card(
                '자사 최고 요일', f'{_DOW[int(best_my_dow["_dow"])]}요일',
                f'CTR {best_my_dow["CTR"]:.2f}% · {int(best_my_dow["n"])}건',
                color=COLOR_SUCCESS,
            ), unsafe_allow_html=True)
        else:
            st.markdown(_card('자사 최고 요일', '—', '데이터 부족'), unsafe_allow_html=True)
    with c4:
        n_tried = mine['_hour'].nunique()
        st.markdown(_card(
            '시도한 시간대', f'{n_tried}개',
            '다양할수록 최적점을 찾기 쉬움' if n_tried > 1 else '⚠ 단일 시간대만 운영 중',
            color=COLOR_DANGER if n_tried == 1 else COLOR_TEXT,
        ), unsafe_allow_html=True)

    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    # ══════════════════════════════════════════
    # 다음 발송 추천 슬롯
    # ══════════════════════════════════════════
    st.markdown(
        f'<div style="font-size:1rem;font-weight:600;color:{COLOR_TEXT};'
        f'margin-bottom:6px;letter-spacing:-0.01em;">🎯 다음 발송 추천 시점</div>'
        f'<div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};margin-bottom:12px;">'
        f'{esc_html_safe(industry or "업종")} 업종에서 검증된 슬롯 중 자사 성과·시도 여부를 반영해 추천합니다.</div>',
        unsafe_allow_html=True,
    )

    if slots.empty:
        st.markdown(
            f'<div style="border:1px dashed {COLOR_BORDER};border-radius:8px;padding:14px;'
            f'color:{COLOR_TEXT_TER};font-size:0.8rem;text-align:center;">'
            f'업종 벤치마크 데이터가 부족해 추천을 생성할 수 없습니다.</div>',
            unsafe_allow_html=True,
        )
    else:
        bench_avg = float(bench['_click'].sum() / bench['_send'].sum() * 100)
        shown = 0
        for _, s in slots.iterrows():
            if shown >= 4:
                break
            dow, hour = int(s['_dow']), int(s['_hour'])
            if dow >= 5:          # 주말은 LMS 성과가 급락 — 추천 제외
                continue
            if s['CTR'] < bench_avg:
                continue
            is_new = (dow, hour) not in my_slots
            # 자사 해당 슬롯 성과
            my_slot = mine[(mine['_dow'] == dow) & (mine['_hour'] == hour)]
            if not my_slot.empty:
                mine_ctr = float(my_slot['_click'].sum() / my_slot['_send'].sum() * 100)
                my_txt = f'자사 {len(my_slot)}건 CTR {mine_ctr:.2f}%'
                tone = COLOR_SUCCESS if mine_ctr >= my_ctr else COLOR_BLUE
                badge = '검증됨 · 유지'
            else:
                my_txt = '자사 미시도 구간'
                tone = BRAND_PRIMARY
                badge = '신규 테스트 권장'
            lift = (s['CTR'] / bench_avg - 1) * 100
            src = str(s.get('출처', '업종'))
            st.markdown(
                f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {tone};'
                f'border-radius:8px;padding:14px 18px;background:{COLOR_CARD};margin-bottom:8px;">'
                f'  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;">'
                f'    <div>'
                f'      <div style="display:flex;align-items:center;gap:8px;">'
                f'        <span style="font-size:1.05rem;color:{COLOR_TEXT};font-weight:700;">'
                f'{_DOW[dow]}요일 {hour}시</span>'
                f'        <span style="font-size:0.62rem;color:{tone};background:{COLOR_BG};'
                f'padding:2px 8px;border-radius:999px;font-weight:700;">{badge}</span>'
                f'      </div>'
                f'      <div style="font-size:0.74rem;color:{COLOR_TEXT_SEC};margin-top:4px;">'
                f'{esc_html_safe(src)} {int(s["n"])}건 평균 CTR {s["CTR"]:.2f}% '
                f'(기준 대비 {lift:+.0f}%) · {esc_html_safe(my_txt)}</div>'
                f'    </div>'
                f'    <div style="text-align:right;">'
                f'      <div style="font-size:1.3rem;color:{tone};font-weight:700;line-height:1;">'
                f'{s["CTR"]:.2f}%</div>'
                f'      <div style="font-size:0.6rem;color:{COLOR_TEXT_TER};margin-top:2px;">업종 기준</div>'
                f'    </div>'
                f'  </div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            shown += 1

        # 쏠림 경고
        if conc >= 80 and len(my_hours) <= 2:
            st.markdown(
                f'<div style="border:1px solid #FECACA;border-radius:8px;padding:12px 16px;'
                f'background:#FFF8F8;margin-top:4px;">'
                f'<div style="font-size:0.85rem;color:{COLOR_DANGER};font-weight:700;margin-bottom:4px;">'
                f'⚠ 발송 시간이 {top_hour}시에 {conc:.0f}% 집중되어 있습니다</div>'
                f'<div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};line-height:1.55;">'
                f'다른 시간대를 시도한 적이 거의 없어 더 나은 슬롯이 있는지 확인할 수 없습니다. '
                f'위 추천 중 <b>신규 테스트 권장</b> 슬롯에 1회만 집행해 보면 비교 데이터가 쌓입니다.</div>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ══════════════════════════════════════════
    # 요일 × 시간 히트맵
    # ══════════════════════════════════════════
    if not slots.empty:
        st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:6px;letter-spacing:-0.01em;">요일 × 시간대 CTR ({esc_html_safe(industry)} 업종)</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:10px;">'
            f'● 표시는 자사가 집행한 적 있는 슬롯입니다. 진할수록 CTR이 높아요.</div>',
            unsafe_allow_html=True,
        )
        hours = sorted(slots['_hour'].unique())
        vmax = float(slots['CTR'].max()) or 1.0
        head = '<div style="width:38px;"></div>' + ''.join(
            f'<div style="text-align:center;font-size:0.64rem;color:{COLOR_TEXT_SEC};'
            f'font-weight:700;">{h}시</div>' for h in hours
        )
        rows = []
        for d in range(5):  # 평일만
            cells = [
                f'<div style="font-size:0.68rem;color:{COLOR_TEXT_SEC};font-weight:700;'
                f'display:flex;align-items:center;">{_DOW[d]}</div>'
            ]
            for h in hours:
                m = slots[(slots['_dow'] == d) & (slots['_hour'] == h)]
                if m.empty:
                    cells.append(
                        f'<div style="height:34px;border:1px dashed {COLOR_BORDER_SUBTLE};'
                        f'border-radius:5px;"></div>'
                    )
                    continue
                v = float(m.iloc[0]['CTR'])
                alpha = 0.08 + min(v / vmax, 1.0) * 0.55
                dot = ' ●' if (d, h) in my_slots else ''
                cells.append(
                    f'<div style="height:34px;border-radius:5px;display:flex;align-items:center;'
                    f'justify-content:center;background:rgba(247,147,29,{alpha:.2f});'
                    f'font-size:0.64rem;color:{COLOR_TEXT};font-weight:600;" '
                    f'title="{_DOW[d]} {h}시 · {int(m.iloc[0]["n"])}건">{v:.1f}{dot}</div>'
                )
            rows.append(
                f'<div style="display:grid;grid-template-columns:38px repeat({len(hours)},1fr);'
                f'gap:4px;margin-bottom:4px;">' + ''.join(cells) + '</div>'
            )
        st.markdown(
            f'<div style="border:1px solid {COLOR_BORDER};border-radius:10px;padding:12px 14px;'
            f'background:{COLOR_CARD};">'
            f'<div style="display:grid;grid-template-columns:38px repeat({len(hours)},1fr);'
            f'gap:4px;margin-bottom:6px;">{head}</div>'
            + ''.join(rows) + '</div>',
            unsafe_allow_html=True,
        )

    # ══════════════════════════════════════════
    # 매체별 최적 시간
    # ══════════════════════════════════════════
    if not bench.empty and '매체' in bench.columns:
        st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
        st.markdown(
            f'<div style="font-size:0.95rem;font-weight:600;color:{COLOR_TEXT};'
            f'margin-bottom:6px;letter-spacing:-0.01em;">매체별 최적 시간대</div>'
            f'<div style="font-size:0.75rem;color:{COLOR_TEXT_SEC};margin-bottom:10px;">'
            f'업종 전체 데이터 기준. 매체마다 수신자 활동 시간이 다릅니다.</div>',
            unsafe_allow_html=True,
        )
        media_col = '매체명' if '매체명' in mine.columns else '매체'
        my_media = set(mine[media_col].dropna().astype(str)) if media_col in mine.columns else set()
        rows = []
        for media in sorted(my_media):
            sub = bench[bench['매체'].astype(str) == media]
            if sub.empty:
                continue
            g = _agg_ctr(sub.groupby('_hour'))
            g = g[g['n'] >= 3]
            if g.empty:
                continue
            best = g.loc[g['CTR'].idxmax()]
            cur = _agg_ctr(mine[mine[media_col].astype(str) == media].groupby('_hour'))
            cur_h = int(cur.loc[cur['n'].idxmax(), '_hour']) if not cur.empty else None
            rows.append({
                '매체': media,
                '현재 발송': f'{cur_h}시' if cur_h is not None else '—',
                '업종 최적': f'{int(best["_hour"])}시',
                '최적 CTR(%)': round(float(best['CTR']), 2),
                '표본': int(best['n']),
                '조정 필요': '✅ 일치' if cur_h == int(best['_hour']) else '🔄 변경 검토',
            })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True,
                         height=min(360, 56 + len(rows) * 36))
        else:
            st.caption('매체별 시간대 표본이 부족합니다.')

    st.markdown('<div class="space-sm"></div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="border:1px solid {COLOR_BORDER_SUBTLE};border-radius:8px;'
        f'padding:10px 14px;background:{COLOR_BG};font-size:0.72rem;color:{COLOR_TEXT_SEC};'
        f'line-height:1.55;">'
        f'💡 슬롯은 캠페인 {_MIN_SLOT_N}건 이상 집행된 구간만 집계합니다. '
        f'주말은 LMS 성과가 평일 대비 크게 낮아 추천에서 제외합니다.'
        f'</div>',
        unsafe_allow_html=True,
    )
