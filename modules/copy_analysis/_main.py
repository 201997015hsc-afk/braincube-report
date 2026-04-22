"""
메인 렌더: 업종/광고주 필터 → KPI → 각 섹션 순차 렌더링.
"""
import streamlit as st
import pandas as pd

from modules.config import BRAND_PRIMARY, section_header, kpi_card_html
from modules.firebase_connector import load_benchmark

from ._data_prep import _prepare_msg_data, _group_by_message
from ._ranking import _render_copy_ranking
from ._patterns import _render_pattern_analysis
from ._length import _render_length_analysis
from ._media_copy import _render_media_copy
from ._insights import _render_sales_insights


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────

def render(df: pd.DataFrame):
    # ── 역할 기반 접근 제어 ──
    from modules.auth import get_role, get_user, ROLE_INTERNAL, ROLE_CLIENT
    from modules.client_manager import load_profile as _load_profile, list_clients as _list_clients
    _role = get_role()
    _user = get_user()
    _is_client = (_role == ROLE_CLIENT)

    # 클라이언트가 등록된 자사 브랜드 식별 (client_select 세션에서)
    _own_brand = None
    if _is_client:
        # 선택된 클라이언트의 firebase_advertiser (자사 브랜드)
        _sel = st.session_state.get('client_select')
        _clients = _list_clients()
        _client_ids = [None] + [c['id'] for c in _clients]
        if _sel is not None and 0 < _sel < len(_client_ids):
            _cid = _client_ids[_sel]
            _prof = _load_profile(_cid) if _cid else None
            if _prof:
                _own_brand = _prof.get('firebase_advertiser', '')

    st.markdown(section_header(
        '문구(카피) 성과 분석',
        '어떤 메시지가 클릭을 이끌어내는지 데이터로 확인합니다. '
        '금액/CTA/이모지 등 패턴별 성과 · 최적 문구 길이 · 매체별 효과적인 문구를 분석합니다.'
        + ('' if _role == ROLE_INTERNAL else
           ' <span style="color:#8B95A1">(⚠ 타 광고주 문구는 개인정보·기밀 보호를 위해 익명화됩니다)</span>'),
    ), unsafe_allow_html=True)

    # ── Firebase 데이터 로드 ──
    try:
        bench = load_benchmark()
    except Exception:
        bench = None

    from modules.ui_helpers import render_empty_state
    if bench is None or bench.empty or '메시지' not in bench.columns:
        render_empty_state(
            "Firebase 메시지 데이터가 없습니다",
            "문구 성과 분석은 Firebase의 msg 필드 데이터를 기반으로 동작합니다.",
            icon="🔥",
            actions=[
                ("Firebase 브랜드 연결", "사이드바 → 클라이언트 관리에서 Firebase 브랜드를 연결하세요"),
                ("관리자 문의", "msg 필드 스키마 확인이 필요한 경우"),
            ],
        )
        st.divider()
        return

    # 메시지가 있는 데이터 존재 확인
    has_msg = bench['메시지'].notna() & (bench['메시지'].astype(str).str.strip() != '')
    if has_msg.sum() == 0:
        render_empty_state(
            "메시지 데이터가 비어 있습니다",
            "Firebase는 연결됐으나, msg 필드에 유효한 데이터가 없습니다.",
            icon="⚠️",
            actions=[
                ("Firebase 스키마 확인", "msg 필드가 올바르게 채워져 있는지 확인하세요"),
            ],
        )
        st.divider()
        return

    # ── 업종 선택 ──
    industries = sorted([
        ind for ind in bench[has_msg]['분야'].dropna().unique().tolist()
        if str(ind).strip() and not str(ind).strip().isdigit()
    ])
    if not industries:
        st.warning('업종 정보가 없는 데이터입니다.')
        st.divider()
        return

    # 클라이언트는 자사 브랜드의 업종으로 기본 설정
    _default_ind_idx = 0
    if _is_client and _own_brand:
        _own_rows = bench[bench['_브랜드'] == _own_brand] if '_브랜드' in bench.columns else pd.DataFrame()
        if not _own_rows.empty:
            _own_ind = _own_rows['분야'].dropna().value_counts()
            if len(_own_ind) > 0:
                _own_industry = _own_ind.index[0]
                if _own_industry in industries:
                    _default_ind_idx = industries.index(_own_industry)

    from modules.ui_helpers import help_text as _help_text
    col_sel, col_info = st.columns([1, 2])
    with col_sel:
        selected_ind = st.selectbox(
            '📂 업종 선택', industries, index=_default_ind_idx, key='copy_industry',
            help=_help_text("industry"),
        )
    with col_info:
        ind_msgs = bench[has_msg & (bench['분야'] == selected_ind)]
        n_msgs = len(ind_msgs)
        n_advs = ind_msgs['광고주'].nunique()
        st.markdown(
            f'<div style="padding:10px 0 0;font-size:0.85rem;color:#4E5968;">'
            f'<b style="color:{BRAND_PRIMARY}">{selected_ind}</b> 업종 — '
            f'광고주 <b>{n_advs}</b>개 · 메시지 <b>{n_msgs:,}</b>건</div>',
            unsafe_allow_html=True,
        )

    # ── 광고주 필터 ──
    advertisers = sorted([
        a for a in ind_msgs['광고주'].dropna().unique().tolist()
        if str(a).strip() and not str(a).strip().isdigit()
    ])
    if _is_client:
        # 클라이언트: 자사 브랜드 기준 자동 필터 (선택 불가)
        # _own_brand는 대행사 필드(실 브랜드)이므로, 업종 내 동일 브랜드를 가진 광고주들 모두 포함
        selected_adv = None  # 광고주명 필터 대신 브랜드 기준 필터 사용 (아래 _prepare에서 처리)
        if _own_brand:
            st.markdown(
                f'<div style="background:rgba(49,130,246,0.08);border-radius:8px;'
                f'padding:8px 14px;font-size:0.82rem;color:#3182F6;">'
                f'👤 자사 브랜드: <b>{_own_brand}</b> · 타 광고주 문구는 익명화되어 표시됩니다.</div>',
                unsafe_allow_html=True,
            )
        else:
            st.info('ℹ 클라이언트 브랜드가 설정되지 않았습니다. 업종 전체 데이터 기반 익명 분석이 제공됩니다.')
    else:
        # 내부 관리자: 기존과 동일
        adv_options = ['전체 (업종 내 모든 광고주)'] + advertisers
        selected_adv_idx = st.selectbox(
            '👤 광고주 필터',
            range(len(adv_options)),
            format_func=lambda i: adv_options[i],
            key='copy_advertiser',
            help=_help_text("advertiser"),
        )
        selected_adv = advertisers[selected_adv_idx - 1] if selected_adv_idx > 0 else None

    st.divider()

    # ── 데이터 준비 ──
    raw_df = _prepare_msg_data(bench, selected_ind, selected_adv)
    if raw_df.empty:
        st.info('선택한 조건에 해당하는 메시지 데이터가 없습니다.')
        st.divider()
        return

    msg_df = _group_by_message(raw_df, min_sends=50)

    # KPI 카드 (NaN 방어: 컬럼이 전부 NaN이면 .mean()/.max()도 NaN 반환 → 'nan%' 표시 방지)
    total_msgs = len(msg_df) if not msg_df.empty else 0
    _ctr_vals = raw_df['CTR'].dropna()
    _len_vals = raw_df['문구길이'].dropna()
    avg_ctr = float(_ctr_vals.mean()) if not _ctr_vals.empty else 0.0
    avg_len = float(_len_vals.mean()) if not _len_vals.empty else 0.0
    max_ctr = float(_ctr_vals.max()) if not _ctr_vals.empty else 0.0

    k1, k2, k3, k4 = st.columns(4)
    with k1:
        st.markdown(kpi_card_html('분석 문구 수', f'{total_msgs:,}개'), unsafe_allow_html=True)
    with k2:
        st.markdown(kpi_card_html('평균 CTR', f'{avg_ctr:.2f}%'), unsafe_allow_html=True)
    with k3:
        st.markdown(kpi_card_html('평균 문구 길이', f'{avg_len:.0f}자'), unsafe_allow_html=True)
    with k4:
        st.markdown(kpi_card_html('최고 CTR', f'{max_ctr:.2f}%'), unsafe_allow_html=True)

    st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)

    # ── 각 섹션 렌더링 ──
    # 클라이언트면 own_brand 전달해서 타 광고주 문구 익명화
    _anon_brand = _own_brand if _is_client else None

    # 1. 문구 성과 랭킹
    _render_copy_ranking(msg_df, own_brand=_anon_brand)
    st.markdown('<div style="height:20px"></div>', unsafe_allow_html=True)

    # 2. 문구 패턴 분석 (금액/CTA/이모지/첫 문장 유형별 CTR)
    _render_pattern_analysis(raw_df)
    st.markdown('<div style="height:20px"></div>', unsafe_allow_html=True)

    # 3. 문구 길이 분석
    _render_length_analysis(raw_df)
    st.markdown('<div style="height:20px"></div>', unsafe_allow_html=True)

    # 4. 매체별 문구 성과
    _render_media_copy(raw_df, own_brand=_anon_brand)
    st.markdown('<div style="height:20px"></div>', unsafe_allow_html=True)

    # 5. 인사이트 (내부 관리자만 노출 — 경쟁사 토킹포인트 포함 가능)
    if _role == ROLE_INTERNAL:
        _render_sales_insights(msg_df, raw_df, selected_ind, selected_adv)

    st.divider()
