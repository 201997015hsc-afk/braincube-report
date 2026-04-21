"""
Section 4: 매체별 문구 성과 차이.
"""
import streamlit as st
import pandas as pd

from modules.config import BRAND_PRIMARY

from ._shared import _clean_msg, _esc


# ──────────────────────────────────────────────
# Section 4: 매체별 문구 성과 차이
# ──────────────────────────────────────────────

def _render_media_copy(raw_df: pd.DataFrame, own_brand: str = None):
    """매체별로 어떤 유형의 문구가 효과적인지 분석.

    own_brand가 주어지면 자사 브랜드 외 최고 성과 문구는 익명화.
    """
    st.markdown(
        '<div style="font-size:0.92rem;font-weight:700;color:#191F28;'
        'margin-bottom:8px;">📡 매체별 문구 성과</div>',
        unsafe_allow_html=True,
    )

    if raw_df.empty or '매체' not in raw_df.columns:
        st.caption('매체별 분석 데이터가 부족합니다.')
        return

    # 매체별 집계 (빈 문자열/숫자만 있는 값 제외)
    medias = [
        m for m in raw_df['매체'].dropna().unique().tolist()
        if str(m).strip() and not str(m).strip().isdigit()
    ]
    if len(medias) < 2:
        st.caption('2개 이상의 매체 데이터가 필요합니다.')
        return

    brand_col = '_브랜드' if '_브랜드' in raw_df.columns else '광고주'
    media_stats = []
    for m in medias:
        sub = raw_df[raw_df['매체'] == m]
        if len(sub) < 3:
            continue
        sends = sub['발송건'].sum()
        clicks = sub['클릭수'].sum()
        ctr = clicks / sends * 100 if sends > 0 else 0
        avg_len = sub['문구길이'].mean()
        # 단일 소발송 아웃라이어 방지: 발송건 ≥ 500인 행 중 최고 CTR
        eligible = sub[sub['발송건'] >= 500]
        best_row = None
        best_label = '최다 발송 문구'
        # CTR 전부 NaN이면 idxmax 결과도 NaN → KeyError. 방어적 체크.
        if len(eligible) > 0 and eligible['CTR'].notna().any():
            _idx = eligible['CTR'].idxmax()
            if pd.notna(_idx):
                best_row = eligible.loc[_idx]
                best_label = '최고성과 문구'
        if best_row is None:
            # 폴백: 최다 발송 문구. 발송건 유효성도 체크.
            if sub['발송건'].notna().any():
                _idx = sub['발송건'].idxmax()
                if pd.notna(_idx):
                    best_row = sub.loc[_idx]
        if best_row is None:
            continue  # 유효한 최고 문구를 찾지 못하면 스킵
        best_brand = best_row.get(brand_col, '')
        is_own = (own_brand and best_brand == own_brand)
        if own_brand and not is_own:
            # 익명화: 길이만 노출
            best_msg_clean = f'<span style="color:#8B95A1;font-style:italic;">[익명 문구 · {int(len(str(best_row["메시지"])))}자]</span>'
        else:
            best_msg_clean = _clean_msg(str(best_row['메시지']), 35)
            if is_own:
                best_msg_clean = f'🟢 {best_msg_clean}'
        best_ctr = float(best_row['CTR'])
        best_sends = int(best_row['발송건'])
        media_stats.append({
            '매체': _esc(str(m)),
            '캠페인수': len(sub),
            '평균CTR': ctr,
            '평균문구길이': avg_len,
            '최고성과문구': best_msg_clean,
            '최고CTR': best_ctr,
            '최고발송': best_sends,
            '라벨': best_label,
        })

    if not media_stats:
        st.caption('매체별 분석 데이터가 부족합니다.')
        return

    mdf = pd.DataFrame(media_stats).sort_values('평균CTR', ascending=False)

    # HTML 테이블
    html = (
        '<table style="width:100%;border-collapse:collapse;font-size:0.78rem;">'
        '<tr style="background:#F7F8FA;">'
        '<th style="padding:8px;text-align:left;color:#4E5968;">매체</th>'
        '<th style="padding:8px;text-align:right;color:#4E5968;">캠페인</th>'
        '<th style="padding:8px;text-align:right;color:#4E5968;">평균 CTR</th>'
        '<th style="padding:8px;text-align:right;color:#4E5968;">평균 길이</th>'
        '<th style="padding:8px;text-align:left;color:#4E5968;">대표 문구</th>'
        '</tr>'
    )
    for _, r in mdf.iterrows():
        label_color = '#2E7D32' if r['라벨'] == '최고성과 문구' else '#4E5968'
        html += (
            f'<tr style="border-bottom:1px solid #F0F1F3;">'
            f'<td style="padding:8px;font-weight:600;color:#191F28;">{r["매체"]}</td>'
            f'<td style="padding:8px;text-align:right;color:#4E5968;">{int(r["캠페인수"])}건</td>'
            f'<td style="padding:8px;text-align:right;color:{BRAND_PRIMARY};font-weight:700;">'
            f'{r["평균CTR"]:.2f}%</td>'
            f'<td style="padding:8px;text-align:right;color:#4E5968;">{r["평균문구길이"]:.0f}자</td>'
            f'<td style="padding:8px;color:#4E5968;">'
            f'<div style="font-size:0.70rem;color:#8B95A1;margin-bottom:2px;">{r["라벨"]}</div>'
            f'{r["최고성과문구"]} <span style="color:{label_color};font-weight:600;">'
            f'(CTR {r["최고CTR"]:.2f}% · 발송 {int(r["최고발송"]):,}건)</span></td>'
            f'</tr>'
        )
    html += '</table>'
    st.markdown(html, unsafe_allow_html=True)
    st.caption('※ 최고성과 문구는 발송건 500 이상 문구 중 선정합니다. 기준 미달 시 최다 발송 문구로 대체됩니다.')
