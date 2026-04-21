"""
Section 1: 문구 성과 Top / Bottom 랭킹 테이블.
"""
import streamlit as st
import pandas as pd

from modules.config import BRAND_PRIMARY, CTR_MIN_FLOOR

from ._shared import _clean_msg


# ──────────────────────────────────────────────
# Section 1: 문구 성과 Top / Bottom
# ──────────────────────────────────────────────

def _render_copy_ranking(msg_df: pd.DataFrame, own_brand: str = None):
    """고성과 / 저성과 문구 랭킹 테이블.

    own_brand가 주어지면 자사 브랜드 외 메시지는 익명화 표시.
    """
    st.markdown(
        '<div style="font-size:0.92rem;font-weight:700;color:#191F28;'
        'margin-bottom:8px;">🏆 문구별 성과 랭킹</div>',
        unsafe_allow_html=True,
    )

    if msg_df.empty or len(msg_df) < 2:
        st.caption('충분한 문구 데이터가 없습니다.')
        return

    avg_ctr = float(msg_df['CTR'].mean())

    def _build_table(sub: pd.DataFrame, title: str, bg_color: str, header_color: str):
        html = (
            f'<div style="margin-bottom:16px;">'
            f'<div style="font-size:0.82rem;font-weight:600;color:{header_color};'
            f'margin-bottom:6px;">{title}</div>'
            f'<table style="width:100%;border-collapse:collapse;font-size:0.78rem;">'
            f'<tr style="background:{bg_color};border-radius:8px;">'
            f'<th style="padding:8px;text-align:left;color:#4E5968;">문구 (요약)</th>'
            f'<th style="padding:8px;text-align:right;color:#4E5968;">발송</th>'
            f'<th style="padding:8px;text-align:right;color:#4E5968;">클릭</th>'
            f'<th style="padding:8px;text-align:right;color:#4E5968;">CTR</th>'
            f'<th style="padding:8px;text-align:right;color:#4E5968;">vs 평균</th>'
            f'</tr>'
        )
        for idx, (_, row) in enumerate(sub.iterrows(), start=1):
            row_brand = row.get('_브랜드', '')
            is_own = (own_brand and row_brand == own_brand)
            # 익명화 대상: own_brand 설정됐고 자사 브랜드가 아닌 경우
            if own_brand and not is_own:
                short = f'[익명 문구 #{idx}]'
                row_style = 'color:#8B95A1;font-style:italic;'
            else:
                short = _clean_msg(str(row['메시지']), 40)
                if is_own:
                    short = f'🟢 {short}'  # 자사 표시
                row_style = 'color:#191F28;'
            ctr_val = float(row['CTR'])
            diff = ctr_val - avg_ctr
            diff_color = '#2E7D32' if diff > 0 else '#E53935'
            diff_sign = '+' if diff > 0 else ''
            html += (
                f'<tr style="border-bottom:1px solid #F0F1F3;">'
                f'<td style="padding:8px;{row_style}">{short}</td>'
                f'<td style="padding:8px;text-align:right;color:#4E5968;">{int(row["발송건"]):,}</td>'
                f'<td style="padding:8px;text-align:right;color:#4E5968;">{int(row["클릭수"]):,}</td>'
                f'<td style="padding:8px;text-align:right;font-weight:700;color:{header_color};">'
                f'{ctr_val:.2f}%</td>'
                f'<td style="padding:8px;text-align:right;color:{diff_color};font-weight:600;">'
                f'{diff_sign}{diff:.2f}%p</td>'
                f'</tr>'
            )
        html += '</table></div>'
        return html

    n_show = min(5, len(msg_df) // 2)
    # 상·하위 각각 최소 3건 이상일 때만 랭킹 노출
    if n_show < 3:
        st.caption(f'상·하위 비교에는 각 그룹 최소 3건 이상이 필요합니다 (현재 {n_show}건).')
        return

    top = msg_df.head(n_show)
    bottom = msg_df.tail(n_show)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown(
            _build_table(top, f'🔥 Top {n_show} — 평균 CTR {avg_ctr:.2f}%', '#EAFAF1', '#2E7D32'),
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            _build_table(bottom, f'⚠️ Bottom {n_show}', '#FFF3E0', '#E53935'),
            unsafe_allow_html=True,
        )

    # 성과 격차 요약 — 분모 폭발 방지 + 상한 캡
    top_ctr = float(top['CTR'].mean())
    bot_ctr = float(bottom['CTR'].mean())
    if bot_ctr >= CTR_MIN_FLOOR:
        ratio = top_ctr / bot_ctr
        ratio_text = '10배 이상' if ratio > 10 else f'{ratio:.1f}배'
        st.markdown(
            f'<div style="background:#F8F9FA;border-radius:12px;padding:12px 16px;'
            f'margin-top:4px;font-size:0.82rem;color:#4E5968;">'
            f'💡 상위 {n_show}건의 평균 CTR은 하위 {n_show}건 대비 '
            f'<b style="color:{BRAND_PRIMARY}">{ratio_text}</b> 수준입니다 '
            f'(상위 {top_ctr:.2f}% / 하위 {bot_ctr:.2f}%).'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="background:#F8F9FA;border-radius:12px;padding:12px 16px;'
            f'margin-top:4px;font-size:0.82rem;color:#8B95A1;">'
            f'하위 문구의 CTR이 매우 낮아(&lt;{CTR_MIN_FLOOR}%) 배수 비교는 생략합니다.'
            f'</div>',
            unsafe_allow_html=True,
        )
