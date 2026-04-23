"""
LMS 고도화 분석 및 예측 대시보드 — 메인 오케스트레이터
Toss-style UI · 사이드바(브랜딩, 네비게이션, 필터, 다운로드) · PPTX 리포트 생성
멀티 클라이언트 관리 · 원페이저 PDF 다운로드 · 역할 기반 접근 제어
"""
import io
import os
from datetime import datetime

import streamlit as st
import pandas as pd

# 로깅 초기화 (Sentry 연동 포함) — 다른 모듈 import 전에 실행
from modules.log_setup import get_logger, cleanup_session_state
_log = get_logger(__name__)

from modules.config import apply_page_style, section_header, alert_card_html, kpi_card_html, esc_html, sanitize_input
from modules.data_processing import (
    load_and_preprocess, get_sheet_names, find_default_sheet, filter_by_date_range,
    ensure_derived_columns, _cast_and_enrich, _normalize_columns, _merge_duplicate_metric_cols,
)
from modules.client_manager import (
    list_clients, load_profile, create_client, delete_client, update_client,
    load_client_data, save_uploaded_data, merge_data, get_client_stats,
    archive_report, list_archived_reports,
)
from modules.auth import (
    is_logged_in, get_user, get_role, logout, render_login_page,
    render_account_manager, ROLE_INTERNAL, ROLE_CLIENT,
)
from modules import (
    kpi, trend, prediction, heatmap, business, summary,
    anomaly, budget_optimizer, budget_simulator, competitor, email_report,
    industry_timing, industry_intel, copy_analysis, monthly_pdf,
    media_trend,
)

# ──────────────────────────────────────────────
# 섹션 네비게이션 정의 (Linear/Notion 스타일 — 플랫 리스트 + 카테고리)
# ──────────────────────────────────────────────
# 각 아이템: {"cat": 카테고리, "label": 표시 이름, "icon": 기하학 아이콘,
#            "render": 렌더 함수, "scope": "client"|"internal"}

# 카테고리별 컬러 점 (Option 3 스타일 — 카테고리 그룹화 시각화)
_NAV_CAT_DOT = {
    "성과 분석":       "🔵",
    "업종 인사이트":   "🟠",
    "액션":           "🟢",
    "내부 도구":       "⚫",
}


def _dot(cat: str) -> str:
    return _NAV_CAT_DOT.get(cat, "⚪")


_NAV_ITEMS = [
    # ── 성과 분석 (파랑) ──
    {"cat": "성과 분석", "label": "월간 요약",      "icon": _NAV_CAT_DOT["성과 분석"], "render": summary.render, "scope": "client"},
    {"cat": "성과 분석", "label": "KPI 및 인사이트", "icon": _NAV_CAT_DOT["성과 분석"], "render": kpi.render,     "scope": "client"},
    {"cat": "성과 분석", "label": "트렌드 분석",    "icon": _NAV_CAT_DOT["성과 분석"], "render": trend.render,   "scope": "client"},

    # ── 업종 인사이트 (오렌지) ──
    {"cat": "업종 인사이트", "label": "경쟁사 분석",             "icon": _NAV_CAT_DOT["업종 인사이트"], "render": competitor.render,      "scope": "client"},
    {"cat": "업종 인사이트", "label": "업종 내 경쟁 인텔리전스", "icon": _NAV_CAT_DOT["업종 인사이트"], "render": industry_intel.render,  "scope": "client"},
    {"cat": "업종 인사이트", "label": "매체 트렌드",             "icon": _NAV_CAT_DOT["업종 인사이트"], "render": media_trend.render,     "scope": "client"},
    {"cat": "업종 인사이트", "label": "문구 성과 분석",          "icon": _NAV_CAT_DOT["업종 인사이트"], "render": copy_analysis.render,   "scope": "client"},
    {"cat": "업종 인사이트", "label": "업종별 최적 발송 타이밍", "icon": _NAV_CAT_DOT["업종 인사이트"], "render": industry_timing.render, "scope": "client"},

    # ── 액션 (초록) ──
    {"cat": "액션", "label": "전략 제안",       "icon": _NAV_CAT_DOT["액션"], "render": business.render,    "scope": "client"},
    {"cat": "액션", "label": "매체 히트맵",     "icon": _NAV_CAT_DOT["액션"], "render": heatmap.render,     "scope": "client"},
    {"cat": "액션", "label": "월간 PDF 리포트", "icon": _NAV_CAT_DOT["액션"], "render": monthly_pdf.render, "scope": "client"},

    # ── 내부 도구 (어두운 회색) — 관리자 전용 ──
    {"cat": "내부 도구", "label": "이상치 감지",         "icon": _NAV_CAT_DOT["내부 도구"], "render": anomaly.render,         "scope": "internal"},
    {"cat": "내부 도구", "label": "클릭 예측",           "icon": _NAV_CAT_DOT["내부 도구"], "render": prediction.render,      "scope": "internal"},
    {"cat": "내부 도구", "label": "예산 최적 배분",       "icon": _NAV_CAT_DOT["내부 도구"], "render": budget_optimizer.render, "scope": "internal"},
    {"cat": "내부 도구", "label": "예산 증액 시뮬레이터", "icon": _NAV_CAT_DOT["내부 도구"], "render": budget_simulator.render, "scope": "internal"},
    {"cat": "내부 도구", "label": "상세 히트맵",         "icon": _NAV_CAT_DOT["내부 도구"], "render": heatmap.render,         "scope": "internal"},
    {"cat": "내부 도구", "label": "이메일 발송",          "icon": _NAV_CAT_DOT["내부 도구"], "render": email_report.render,    "scope": "internal"},
]


def _build_nav(role: str):
    """역할에 따라 네비게이션 아이템 반환 (flat list)."""
    if role == ROLE_INTERNAL:
        items = list(_NAV_ITEMS)
    else:
        items = [i for i in _NAV_ITEMS if i["scope"] == "client"]
    return items


def _nav_grouped(items: list[dict]) -> list[tuple[str, list[dict]]]:
    """카테고리별로 그룹핑 (순서 보존)."""
    groups: dict[str, list[dict]] = {}
    order: list[str] = []
    for it in items:
        c = it["cat"]
        if c not in groups:
            groups[c] = []
            order.append(c)
        groups[c].append(it)
    return [(c, groups[c]) for c in order]


def _find_nav_item(items: list[dict], label: str) -> dict | None:
    """라벨로 네비 아이템 찾기."""
    for it in items:
        if it["label"] == label:
            return it
    return None


# ──────────────────────────────────────────────
# 섹션 실행기 (오류 격리)
# ──────────────────────────────────────────────
def _run_section(name: str, render_fn, df):
    try:
        render_fn(df)
    except Exception as e:
        st.error(f"'{name}' 섹션 오류: {type(e).__name__}: {e}")
        import traceback
        with st.expander("상세 오류 정보", expanded=False):
            st.code(traceback.format_exc())


# ──────────────────────────────────────────────
# 다운로드 바이트 캐시
# ──────────────────────────────────────────────
def _build_download_cache(df: pd.DataFrame):
    """데이터가 바뀔 때만 CSV·Excel 바이트를 재생성하고, PPTX 캐시를 무효화한다."""
    sig = f"{len(df)}_{df['날짜'].min()}_{df['날짜'].max()}"
    if st.session_state.get('_dl_sig') == sig:
        return  # 이미 캐시됨 — 스킵
    st.session_state['_dl_sig'] = sig
    st.session_state['_csv'] = df.to_csv(index=False).encode('utf-8-sig')
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='분석데이터')
    st.session_state['_xlsx'] = buf.getvalue()
    st.session_state.pop('_pptx', None)  # 데이터 변경 → PPTX 무효화
    st.session_state.pop('_pdf', None)   # 데이터 변경 → PDF 무효화


# ──────────────────────────────────────────────
# 사이드바
# ──────────────────────────────────────────────
def _render_client_manager():
    """클라이언트 관리 팝업 UI (expander 내부)."""
    from modules.firebase_connector import list_advertisers
    clients = list_clients()

    with st.expander("➕ 새 클라이언트 등록", expanded=False):
        new_name = st.text_input("클라이언트명", key="new_client_name", placeholder="예: 삼성카드")
        new_domain = st.text_input("도메인 (선택)", key="new_client_domain", placeholder="samsung.com")

        # Firebase 브랜드 연결 (드롭다운) — 직접계약/대대행 통합
        advs = list_advertisers()
        if advs:
            _options = ["(연결 안 함 — 엑셀 업로드만 사용)"] + [
                f"{a['name']} · {a['industry']} · {a['campaign_count']}건" for a in advs
            ]
            _adv_names = [""] + [a['name'] for a in advs]
            _sel_idx = st.selectbox(
                "📊 Firebase 브랜드 연결",
                range(len(_options)),
                format_func=lambda i: _options[i],
                key="new_client_fbadv",
                help="회사 대시보드 데이터와 자동 연동합니다. 직접계약·대대행 모두 통합 집계됩니다. 비워두면 엑셀 업로드만 가능합니다.",
            )
            new_fb_adv = _adv_names[_sel_idx]
        else:
            st.caption("⚠ Firebase 연결 실패 — 엑셀 업로드 전용으로 생성됩니다.")
            new_fb_adv = ""

        new_email = st.text_input("이메일 (선택)", key="new_client_email", placeholder="pm@client.com")
        new_memo = st.text_input("메모 (선택)", key="new_client_memo", placeholder="2026 상반기 캠페인")
        if st.button("등록", key="btn_create_client", type="primary"):
            # 입력 정제 (제어문자 제거 + 길이 제한)
            _name = sanitize_input(new_name, max_len=60)
            _domain = sanitize_input(new_domain, max_len=100)
            _email = sanitize_input(new_email, max_len=100)
            _memo = sanitize_input(new_memo, max_len=200)
            if _name:
                with st.spinner(f"'{_name}' 등록 중..."):
                    cid = create_client(
                        _name, _domain, _email, _memo,
                        firebase_advertiser=new_fb_adv,
                    )
                # 페이지 리렌더 후에도 뜨도록 토스트 메시지 예약
                st.session_state['_toast_msg'] = (
                    f"✅ '{_name}' 등록 완료", "success"
                )
                st.rerun()
            else:
                st.warning("⚠ 클라이언트명을 입력해 주세요.")

    # 기존 클라이언트 관리
    if clients:
        with st.expander("🗂️ 클라이언트 관리", expanded=False):
            for c in clients:
                stats = get_client_stats(c['id'])
                date_str = esc_html(stats['date_range']) if stats['date_range'] else ""
                fb_adv = c.get('firebase_advertiser', '')
                fb_badge = (
                    f"<span style='background:#3182F6;color:white;border-radius:4px;"
                    f"padding:2px 6px;font-size:.65rem;margin-left:4px;'>📊 {esc_html(fb_adv)}</span>"
                    if fb_adv else ""
                )
                _name_disp = esc_html(c.get('name', c['id']))
                st.markdown(
                    f"<div style='background:#F8F9FA;border-radius:10px;padding:12px 14px;margin-bottom:8px'>"
                    f"<div style='font-weight:700;font-size:.95rem;color:#191F28;margin-bottom:4px'>{_name_disp}{fb_badge}</div>"
                    f"<div style='font-size:.75rem;color:#8B95A1'>"
                    f"{stats['rows']:,}행 · 매체 {stats['media_count']}개"
                    + (f" · {date_str}" if date_str else "")
                    + "</div></div>",
                    unsafe_allow_html=True,
                )
                _e, _d = st.columns(2)
                with _e:
                    if st.button("✏️ 편집", key=f"edit_{c['id']}", use_container_width=True):
                        st.session_state[f'_editing_{c["id"]}'] = True
                with _d:
                    if st.button("🗑️ 삭제", key=f"del_{c['id']}", use_container_width=True):
                        _deleted_name = c.get('name', c['id'])
                        with st.spinner(f"'{_deleted_name}' 삭제 중..."):
                            delete_client(c['id'])
                        st.session_state['_toast_msg'] = (
                            f"🗑️ '{_deleted_name}' 삭제됨", "warning"
                        )
                        st.rerun()

                # 인라인 편집 폼
                if st.session_state.get(f'_editing_{c["id"]}'):
                    with st.container():
                        e1, e2 = st.columns(2)
                        with e1:
                            new_name = st.text_input("이름", value=c.get('name', ''), key=f"en_{c['id']}")
                        with e2:
                            new_domain = st.text_input("도메인", value=c.get('domain', ''), key=f"ed_{c['id']}")
                        new_memo = st.text_input("메모", value=c.get('memo', ''), key=f"em_{c['id']}")

                        # Firebase 브랜드 연결 (직접계약/대대행 통합)
                        _advs = list_advertisers()
                        if _advs:
                            _e_opts = ["(연결 안 함)"] + [
                                f"{a['name']} · {a['industry']} · {a['campaign_count']}건" for a in _advs
                            ]
                            _e_names = [""] + [a['name'] for a in _advs]
                            _cur_fb = c.get('firebase_advertiser', '')
                            _cur_idx = _e_names.index(_cur_fb) if _cur_fb in _e_names else 0
                            _e_sel = st.selectbox(
                                "📊 Firebase 브랜드",
                                range(len(_e_opts)),
                                format_func=lambda i: _e_opts[i],
                                index=_cur_idx,
                                key=f"efb_{c['id']}",
                            )
                            new_fb = _e_names[_e_sel]
                        else:
                            new_fb = c.get('firebase_advertiser', '')

                        eb1, eb2 = st.columns(2)
                        with eb1:
                            if st.button("저장", key=f"esave_{c['id']}", type="primary"):
                                update_client(
                                    c['id'], name=new_name, domain=new_domain,
                                    memo=new_memo, firebase_advertiser=new_fb,
                                )
                                st.session_state.pop(f'_editing_{c["id"]}', None)
                                st.session_state['_toast_msg'] = (
                                    f"💾 '{new_name}' 수정 저장됨", "success"
                                )
                                st.rerun()
                        with eb2:
                            if st.button("취소", key=f"ecancel_{c['id']}"):
                                st.session_state.pop(f'_editing_{c["id"]}', None)
                                st.rerun()

                # 아카이브 리포트 목록
                reports = list_archived_reports(c['id'])
                if reports:
                    st.caption(f"📁 아카이브 ({len(reports)}개)")
                    for r in reports[:5]:
                        try:
                            with open(r['path'], 'rb') as _rf:
                                _rbytes = _rf.read()
                        except OSError:
                            continue
                        st.download_button(
                            f"📥 {r['filename']} ({r['size_kb']}KB)",
                            data=_rbytes,
                            file_name=r['filename'],
                            key=f"dl_{c['id']}_{r['filename']}",
                        )


def _render_sidebar() -> tuple:
    """사이드바 렌더링. (uploaded, sheet_name, company_name, date_range, nav_choice, client_id) 반환"""
    from modules.company_lookup import fetch_company_info

    user = get_user()
    role = get_role()
    nav_items = _build_nav(role)

    with st.sidebar:
        # ── 사용자 정보 + 로그아웃 (Linear 감성: 플랫 · 얇은 보더) ──
        role_badge = "내부" if role == ROLE_INTERNAL else "클라이언트"
        _user_name = user["name"] if user else "Guest"
        _user_id = user["username"] if user else ""
        st.markdown(
            f'<div style="background:transparent;border:1px solid #E5E7EB;'
            f'border-radius:8px;padding:10px 12px;margin-bottom:10px;">'
            f'<div style="font-size:0.86rem;font-weight:600;color:#111827;">'
            f'{esc_html(_user_name)}</div>'
            f'<div style="font-size:0.72rem;color:#6B7280;margin-top:2px;">'
            f'{role_badge} · {esc_html(_user_id)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if st.button("로그아웃", key="btn_logout", use_container_width=True):
            logout()
            st.rerun()

        # ── 클라이언트 선택 (역할 기반 제한) ──
        st.markdown('<div class="sidebar-label">클라이언트</div>', unsafe_allow_html=True)
        all_clients = list_clients()

        if role == ROLE_INTERNAL:
            # 내부 관리자: 전체 클라이언트 선택 가능
            clients = all_clients
            client_options = ["새 업로드 (클라이언트 미선택)"] + [
                f"{c.get('name', c['id'])}" for c in clients
            ]
            client_ids_list = [None] + [c['id'] for c in clients]
            # 방어: 세션에 저장된 인덱스가 유효 범위를 벗어나면(삭제 등) 0으로 리셋
            _cur = st.session_state.get("client_select")
            if _cur is None or not isinstance(_cur, int) or _cur < 0 or _cur >= len(client_options):
                st.session_state["client_select"] = 0
            from modules.ui_helpers import help_text as _help_text
            selected_idx = st.selectbox(
                "클라이언트 선택",
                range(len(client_options)),
                format_func=lambda i: client_options[i],
                label_visibility="collapsed",
                key="client_select",
                help=_help_text("client"),
            )
            # 방어: selectbox가 None을 반환하는 극단적 케이스(검색 텍스트 클리어 등) 대비
            if selected_idx is None or not isinstance(selected_idx, int) or selected_idx >= len(client_ids_list):
                selected_idx = 0
            client_id = client_ids_list[selected_idx]
        else:
            # 클라이언트 역할: 본인 allowed_clients만 접근 가능
            allowed = user.get("allowed_clients", []) if user else []
            clients = [c for c in all_clients if c['id'] in allowed]

            if not clients:
                st.error(
                    "⚠ 접근 가능한 광고주가 없습니다. 관리자에게 문의해 주세요."
                )
                client_id = None
            elif len(clients) == 1:
                # 단일 광고주 → 자동 선택, 드롭다운 숨김
                client_id = clients[0]['id']
                st.markdown(
                    f'<div style="background:rgba(49,130,246,0.08);border-radius:8px;'
                    f'padding:8px 12px;font-size:0.82rem;color:#3182F6;">'
                    f'🔗 <b>{clients[0].get("name", clients[0]["id"])}</b>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                # client_select 세션 상태도 동기화 (다른 모듈 참조용)
                _idx_in_all = next(
                    (i + 1 for i, c in enumerate(all_clients) if c['id'] == client_id), 0,
                )
                st.session_state['client_select'] = _idx_in_all
            else:
                # 여러 광고주 담당 → 본인 것 중에서만 선택
                client_options = [f"{c.get('name', c['id'])}" for c in clients]
                client_ids_list = [c['id'] for c in clients]
                # 방어: 세션에 저장된 인덱스가 유효 범위를 벗어나면 0으로 리셋
                _cur = st.session_state.get("client_select_limited")
                if _cur is None or not isinstance(_cur, int) or _cur < 0 or _cur >= len(client_options):
                    st.session_state["client_select_limited"] = 0
                selected_idx = st.selectbox(
                    "담당 광고주 선택",
                    range(len(client_options)),
                    format_func=lambda i: client_options[i],
                    label_visibility="collapsed",
                    key="client_select_limited",
                )
                # 방어: None 대비
                if selected_idx is None or not isinstance(selected_idx, int) or selected_idx >= len(client_ids_list):
                    selected_idx = 0
                client_id = client_ids_list[selected_idx]
                # 전역 client_select 키도 동기화
                _idx_in_all = next(
                    (i + 1 for i, c in enumerate(all_clients) if c['id'] == client_id), 0,
                )
                st.session_state['client_select'] = _idx_in_all

        # 현재 클라이언트 뱃지
        if client_id and role == ROLE_INTERNAL:
            profile = load_profile(client_id)
            badge_name = profile.get('name', client_id) if profile else client_id
            st.markdown(
                f'<div class="client-badge active">✅ {badge_name}</div>',
                unsafe_allow_html=True,
            )

        # 클라이언트 전환 감지 → 캐시 초기화
        # Bug 7+13: data_mode도 초기화해야 이전 클라이언트 모드가 새 클라이언트에 잔존 안 함
        if st.session_state.get('_prev_client_id') != client_id:
            st.session_state['_prev_client_id'] = client_id
            for k in ('_data_key', '_df_raw', '_display_sheet', '_dl_sig',
                       '_pptx', '_pdf', '_csv', '_xlsx', 'df_loaded', 'data_mode'):
                st.session_state.pop(k, None)

        # 클라이언트 관리 (내부 사용자만)
        if role == ROLE_INTERNAL:
            _render_client_manager()

        # ── 브랜딩 ──
        default_name = ""
        default_domain = ""
        if client_id:
            profile = load_profile(client_id)
            if profile:
                default_name = profile.get("name", "")
                default_domain = profile.get("domain", "")

        # 클라이언트 선택 시 프로필의 이름/도메인을 자동 사용 (UI 입력 제거)
        company_name = default_name or "LMS Analytics"
        domain = default_domain

        # 도메인이 있으면 로고·회사 정보 자동 조회 (백그라운드)
        auto_logo_url = ""
        auto_desc = ""
        if domain:
            info = fetch_company_info(domain)
            if info['success']:
                auto_logo_url = info.get('logo_url', '')
                auto_desc = info.get('description', '')

        # 브랜드 표시 — Linear 감성: 작은 이니셜 아이콘 + 이름 (로고 이미지는 생략)
        display_name = company_name or "LMS Analytics"
        _initial = display_name.strip()[:1] if display_name else "B"
        st.markdown(
            f'<div class="sidebar-brand">'
            f'  <div class="brand-icon">{esc_html(_initial)}</div>'
            f'  <div class="name">{esc_html(display_name)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # ── 데이터 소스 ──
        uploaded = None
        sheet_name = None
        # Firebase 광고주 연결 클라이언트는 기본값이 firebase, 아니면 upload
        _profile = load_profile(client_id) if client_id else None
        _has_fb_setting = bool(_profile and _profile.get('firebase_advertiser'))

        # Bug 6: Firebase 연결 가능 여부 확인 (로컬 JSON OR Streamlit secrets)
        if _has_fb_setting:
            from modules.firebase_connector import is_firebase_available
            _fb_available = is_firebase_available()
        else:
            _fb_available = False
        _has_fb = _has_fb_setting and _fb_available
        data_mode = "firebase" if _has_fb else "upload"

        if client_id:
            stats = get_client_stats(client_id)
            has_stored = stats['rows'] > 0

            # 선택 가능한 모드 목록
            _modes = []
            _fmts = {}
            if _has_fb:
                _modes.append("firebase")
                _fmts["firebase"] = f"📊 Firebase 실시간 ({_profile['firebase_advertiser']})"
            if has_stored:
                _modes.append("stored")
                _fmts["stored"] = f"📂 저장된 데이터 ({stats['rows']:,}행)"
            # Bug 6: upload는 항상 fallback으로 포함
            _modes.append("upload")
            _fmts["upload"] = "📤 새 파일 업로드"

            if len(_modes) > 1:
                st.markdown('<div class="sidebar-label">데이터 소스</div>', unsafe_allow_html=True)
                data_mode = st.radio(
                    "데이터 소스",
                    _modes,
                    format_func=lambda x: _fmts.get(x, x),
                    label_visibility="collapsed",
                    key="data_mode",
                )

            # Firebase 설정은 있는데 연결 불가한 경우 경고
            if _has_fb_setting and not _fb_available:
                st.warning("⚠ Firebase 키 파일을 찾을 수 없습니다. 업로드 모드를 사용해 주세요.")

        # Firebase 모드: 새로고침 버튼
        if data_mode == "firebase" and _has_fb:
            st.markdown(
                f'<div style="background:transparent;border:1px solid #E5E7EB;border-radius:8px;'
                f'padding:10px 12px;margin:6px 0 8px 0;">'
                f'<div style="font-size:.76rem;font-weight:500;color:#4B5563;">'
                f'<span style="color:#3182F6;">●</span> Firebase 실시간 연동</div>'
                f'<div style="font-size:.70rem;color:#9CA3AF;margin-top:3px;">'
                f'{esc_html(_profile["firebase_advertiser"])} · 5분 자동 갱신</div>'
                f'</div>',
                unsafe_allow_html=True,
            )
            if st.button("🔄 지금 새로고침", key="fb_refresh", use_container_width=True):
                # Bug 11: 모든 Firebase 캐시 전체 무효화
                from modules.firebase_connector import (
                    load_from_firestore, list_advertisers, load_advertiser_data,
                    get_benchmark_stats, get_benchmark_monthly, get_benchmark_weekday,
                )
                for _fn in (load_from_firestore, list_advertisers, load_advertiser_data,
                            get_benchmark_stats, get_benchmark_monthly, get_benchmark_weekday):
                    try:
                        _fn.clear()
                    except Exception:
                        pass
                for k in ('_data_key', '_df_raw', '_display_sheet', '_dl_sig',
                           '_pptx', '_pdf', '_csv', '_xlsx', 'df_loaded'):
                    st.session_state.pop(k, None)
                st.rerun()

        if data_mode == "upload":
            st.markdown('<div class="sidebar-label">데이터 업로드</div>', unsafe_allow_html=True)
            uploaded = st.file_uploader("엑셀/CSV 파일", label_visibility="collapsed")
            if uploaded:
                sheets = get_sheet_names(uploaded)
                if sheets:
                    default = find_default_sheet(sheets)
                    sheet_name = st.selectbox("시트 선택", sheets, index=sheets.index(default))
                if client_id and uploaded:
                    merge_opt = st.radio(
                        "데이터 저장 방식",
                        ["merge", "replace"],
                        format_func=lambda x: "🔄 기존 데이터에 누적" if x == "merge" else "🔁 새 데이터로 교체",
                        key="merge_mode",
                        horizontal=True,
                    )
                    st.session_state['_merge_mode'] = merge_opt

        # ── 섹션 네비게이션 (Linear/Notion 스타일 — 카테고리별 플랫 네비) ──
        has_data = uploaded or (data_mode == "stored" and client_id) or (data_mode == "firebase" and _has_fb)
        nav_choice = nav_items[0]["label"] if nav_items else ""
        if has_data:
            _labels = [it["label"] for it in nav_items]
            # 초기값 · 유효성 검증
            _cur = st.session_state.get("_nav_active")
            if _cur not in _labels:
                _cur = nav_items[0]["label"] if nav_items else ""
                st.session_state["_nav_active"] = _cur

            # 카테고리별로 버튼 렌더 (헤더에 카테고리 색 점 포함)
            for _cat, _cat_items in _nav_grouped(nav_items):
                st.markdown(
                    f'<div class="nav-cat-header">'
                    f'<span class="nav-cat-dot">{_dot(_cat)}</span> {_cat}'
                    f'</div>',
                    unsafe_allow_html=True,
                )
                for _it in _cat_items:
                    _is_active = (_it["label"] == _cur)
                    if st.button(
                        f"{_it['icon']}  {_it['label']}",
                        key=f"nav_btn_{_it['label']}",
                        use_container_width=True,
                        type=("primary" if _is_active else "secondary"),
                    ):
                        st.session_state["_nav_active"] = _it["label"]
                        st.rerun()
            nav_choice = st.session_state["_nav_active"]

        # ── 날짜 필터 ──
        date_range = None
        if "df_loaded" in st.session_state and st.session_state.df_loaded is not None:
            df_temp = st.session_state.df_loaded
            st.markdown('<div class="sidebar-label">기간 필터</div>', unsafe_allow_html=True)
            min_date = df_temp['날짜'].min().date()
            max_date = df_temp['날짜'].max().date()
            date_range = st.date_input(
                "분석 기간",
                value=(min_date, max_date),
                min_value=min_date,
                max_value=max_date,
                key="date_filter",
            )

        # ── 계정 관리 (내부 관리자만) ──
        if role == ROLE_INTERNAL:
            render_account_manager()

        # ── 데이터 신선도 배지 (Firebase 최신 일자) ──
        try:
            from modules.firebase_connector import get_latest_data_timestamp
            from modules.ui_helpers import render_freshness_badge
            _latest = get_latest_data_timestamp()
            render_freshness_badge(_latest, source="Firebase", show_absolute=False)
        except Exception:
            pass

        st.markdown("---")
        st.caption(f"© {datetime.now().year} {company_name or 'LMS Analytics'}")

        # ── 버전 · 배포 정보 푸터 ──
        try:
            from modules.version import get_footer_text
            st.caption(f"🔖 {get_footer_text()}")
        except Exception:
            pass

    return uploaded, sheet_name, company_name, date_range, nav_choice, client_id, nav_items


def _render_download_buttons(df: pd.DataFrame, company_name: str, client_id: str | None = None):
    """사이드바에 다운로드 버튼 렌더링 (캐시된 바이트 사용)"""
    _build_download_cache(df)

    with st.sidebar:
        st.markdown('<div class="sidebar-label">리포트 다운로드</div>', unsafe_allow_html=True)

        # CSV (캐시)
        st.download_button(
            "📥 CSV 다운로드",
            data=st.session_state['_csv'],
            file_name=f"lms_report_{datetime.now():%Y%m%d}.csv",
            mime="text/csv",
        )

        # Excel (캐시)
        st.download_button(
            "📥 Excel 다운로드",
            data=st.session_state['_xlsx'],
            file_name=f"lms_report_{datetime.now():%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        # PPTX — 클릭 시에만 생성 (kaleido 렌더링이 무거우므로)
        if '_pptx' not in st.session_state:
            if st.button("📄 PPTX 리포트 생성", key="gen_pptx"):
                with st.spinner("PPTX 생성 중..."):
                    try:
                        from modules.report import generate_pptx
                        from modules.email_report import get_service_name
                        st.session_state['_pptx'] = generate_pptx(df, company_name, service_name=get_service_name())
                    except Exception as e:
                        st.caption(f"PPTX 생성 불가: {e}")

        if '_pptx' in st.session_state:
            st.download_button(
                "📥 PPTX 리포트 다운로드",
                data=st.session_state['_pptx'],
                file_name=f"lms_report_{datetime.now():%Y%m%d}.pptx",
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )

        # PDF 원페이저 — 클릭 시 생성
        if '_pdf' not in st.session_state:
            if st.button("📋 영업용 원페이저 PDF", key="gen_pdf"):
                with st.spinner("PDF 생성 중..."):
                    try:
                        from modules.onepager_pdf import generate_onepager
                        from modules.email_report import get_service_name
                        st.session_state['_pdf'] = generate_onepager(
                            df, company_name, service_name=get_service_name(),
                        )
                    except Exception as e:
                        st.caption(f"PDF 생성 불가: {e}")

        if '_pdf' in st.session_state:
            pdf_fname = f"{company_name or 'LMS'}_원페이저_{datetime.now():%Y%m%d}.pdf"
            st.download_button(
                "📥 원페이저 PDF 다운로드",
                data=st.session_state['_pdf'],
                file_name=pdf_fname,
                mime="application/pdf",
            )
            # 클라이언트 선택 시 아카이브 옵션
            if client_id and st.button("💾 리포트 아카이브 저장", key="archive_pdf"):
                archive_report(client_id, pdf_fname, st.session_state['_pdf'])
                st.success("아카이브에 저장되었습니다.")


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def _load_client_stored_data(client_id: str):
    """클라이언트 저장 데이터를 로드하고 전처리 적용."""
    from modules.config import REQUIRED_COLS
    _fkey = f"client_{client_id}"
    if st.session_state.get('_data_key') == _fkey:
        return st.session_state['_df_raw'], st.session_state['_display_sheet']

    df = load_client_data(client_id)
    if df is None or df.empty:
        return None, None

    # 전처리 파이프라인 적용
    df = _normalize_columns(df)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        st.error(f"저장된 데이터에 필수 항목이 없습니다: {', '.join(missing)}")
        return None, None
    df = _merge_duplicate_metric_cols(df)
    df = ensure_derived_columns(df)
    if df.empty:
        return None, None

    profile = load_profile(client_id)
    display_sheet = profile.get('name', client_id) if profile else client_id
    st.session_state['_data_key'] = _fkey
    st.session_state['_df_raw'] = df
    st.session_state['_display_sheet'] = display_sheet
    st.session_state.df_loaded = df
    return df, display_sheet


def _load_firebase_data(client_id: str):
    """Firebase에서 광고주 실시간 데이터를 로드하고 전처리 적용."""
    from modules.config import REQUIRED_COLS
    from modules.firebase_connector import load_advertiser_data

    profile = load_profile(client_id)
    if not profile or not profile.get('firebase_advertiser'):
        return None, None

    adv_name = profile['firebase_advertiser']
    _fkey = f"firebase_{client_id}_{adv_name}"
    if st.session_state.get('_data_key') == _fkey:
        return st.session_state['_df_raw'], st.session_state['_display_sheet']

    df = load_advertiser_data(adv_name)
    if df is None or df.empty:
        return None, None

    # Bug 1+2 fix: NaN 클릭수 보존 (추적 설정 안 된 캠페인과 0 클릭 구분)
    # _cast_and_enrich가 클릭수.fillna(0)을 하므로, 추적 플래그 백업 후 복원
    _has_click_backup = df['_has_click'].copy() if '_has_click' in df.columns else None

    # 전처리 파이프라인 (이미 표준 컬럼은 있지만 파생 컬럼 보장)
    df = _normalize_columns(df)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        st.error(f"Firebase 데이터에 필수 항목이 없습니다: {', '.join(missing)}")
        return None, None
    df = _merge_duplicate_metric_cols(df)
    df = ensure_derived_columns(df)
    if df.empty:
        return None, None

    # _cast_and_enrich가 날짜 NaT 드롭해서 인덱스 달라졌을 수 있으니 인덱스로 복원
    if _has_click_backup is not None:
        # 공통 인덱스만 복원
        common_idx = df.index.intersection(_has_click_backup.index)
        df.loc[common_idx, '_has_click'] = _has_click_backup.loc[common_idx]
        # 추적 없는 행의 클릭수는 NaN으로 되돌리기
        untracked = df['_has_click'] == False
        df.loc[untracked, '클릭수'] = pd.NA

    display_sheet = profile.get('name', client_id)
    st.session_state['_data_key'] = _fkey
    st.session_state['_df_raw'] = df
    st.session_state['_display_sheet'] = display_sheet
    st.session_state.df_loaded = df
    return df, display_sheet


def _render_health_check():
    """헬스체크 엔드포인트 — ?health=1 쿼리 시 간단 상태 반환.

    UptimeRobot 등 외부 모니터링 서비스가 5분마다 접근해
    서비스 가용성 확인하는 용도. 인증 없이 공개.
    """
    st.set_page_config(page_title="Health", page_icon="✅", layout="centered")
    import datetime as _dt
    # Firebase 연결 상태 간단 체크
    fb_ok = False
    try:
        from modules.firebase_connector import is_firebase_available
        fb_ok = is_firebase_available()
    except Exception:
        fb_ok = False
    status = {
        "status": "ok" if fb_ok else "degraded",
        "firebase": "ok" if fb_ok else "unavailable",
        "time": _dt.datetime.utcnow().isoformat() + "Z",
    }
    # 최소한의 JSON-like 출력 (UptimeRobot은 HTTP 200과 키워드 매칭으로 판정)
    st.code(str(status), language="json")
    st.caption("Health check endpoint — do not index.")


def main():
    # ── 헬스체크 분기 (로그인 없이 공개) ──
    try:
        _qp = st.query_params
        if 'health' in _qp:
            _render_health_check()
            return
    except Exception:
        # 구버전 Streamlit 호환
        try:
            if 'health' in st.experimental_get_query_params():
                _render_health_check()
                return
        except Exception:
            pass

    # ── 로그인 게이트 ──
    if not is_logged_in():
        st.set_page_config(
            page_title="Braincube AI Report · 로그인",
            page_icon="📊",
            layout="centered",
            initial_sidebar_state="collapsed",
        )
        render_login_page()
        return

    # ── 세션 상태 정리 (임시 플래그 제거 · 메모리 누수 방지) ──
    cleanup_session_state()

    # ── 대시보드 (로그인 완료) ──
    apply_page_style()  # wide layout + CSS

    # ── selectbox 타이핑 차단 (readonly 속성 주입) ──
    # Streamlit selectbox는 내부에 검색 input이 있어 백스페이스로 텍스트가 지워짐.
    # 클릭/스크롤 선택은 유지하고 키보드 타이핑만 차단.
    try:
        from streamlit.components.v1 import html as _html_comp
        _html_comp(
            """
            <script>
            (function(){
              try {
                var doc = window.parent.document;
                if (!doc) return;
                // 중복 옵저버 방지 플래그
                if (!window.parent.__selectboxReadOnlyPatched) {
                  window.parent.__selectboxReadOnlyPatched = true;
                  var patch = function(){
                    var inputs = doc.querySelectorAll('[data-baseweb="select"] input');
                    inputs.forEach(function(el){
                      if (!el.hasAttribute('readonly')) {
                        el.setAttribute('readonly', '');
                        el.style.caretColor = 'transparent';
                        el.style.cursor = 'pointer';
                      }
                    });
                  };
                  patch();
                  var observer = new MutationObserver(patch);
                  observer.observe(doc.body, {childList:true, subtree:true});
                } else {
                  // 재실행 시에도 새 요소 즉시 패치
                  var inputs = doc.querySelectorAll('[data-baseweb="select"] input:not([readonly])');
                  inputs.forEach(function(el){
                    el.setAttribute('readonly','');
                    el.style.caretColor='transparent';
                    el.style.cursor='pointer';
                  });
                }
              } catch(e) {}
            })();
            </script>
            """,
            height=0,
        )
    except Exception:
        pass  # 환경에 따라 components 미지원 시 조용히 스킵

    # 예약된 토스트 메시지 표시 (등록/수정/삭제 알림)
    _pending_toast = st.session_state.pop('_toast_msg', None)
    if _pending_toast:
        _msg, _kind = _pending_toast
        _icon = {'success': '✅', 'warning': '⚠️', 'info': 'ℹ️'}.get(_kind, '')
        st.toast(_msg, icon=_icon)

    # ── Firestore 상태 경고 배너 ──
    # 할당량 초과/연결 실패 시 사용자에게 명확히 알림 (데이터 사라진 것처럼 보이는 혼란 방지)
    if st.session_state.get('_firestore_healthy') is False:
        st.warning(
            "⚠️ Firebase 연결 일시 불안정 — 클라이언트/사용자 목록이 불완전할 수 있습니다. "
            "데이터는 안전하게 보관되어 있으며, 잠시 후 자동 복구됩니다. "
            "지속되면 관리자에게 문의해 주세요.",
            icon="⚠️",
        )

    uploaded, sheet_name, company_name, date_range, nav_choice, client_id, nav_items = _render_sidebar()

    # ── 권한 차단: 클라이언트 역할이 권한 없는 광고주에 접근 시 강제 차단 ──
    from modules.auth import can_access_client
    if not can_access_client(client_id):
        st.error(
            "⛔ 접근 권한이 없습니다. 본인에게 할당된 광고주만 열람할 수 있습니다."
        )
        return

    # 데이터 모드 판별
    data_mode = st.session_state.get('data_mode', 'upload')

    # Bug 13: firebase 모드인데 firebase_advertiser 없으면 upload로 강제 전환
    if data_mode == "firebase" and client_id:
        _prof_check = load_profile(client_id)
        if not (_prof_check and _prof_check.get('firebase_advertiser')):
            data_mode = "upload"
            st.session_state['data_mode'] = "upload"

    # ── 상단 Breadcrumb 바 (Linear/Notion 스타일) ──
    # client / current_page  +  우측 메타 (Firebase · 신선도 · 유저)
    try:
        from modules.ui_helpers import render_topbar
        from modules.firebase_connector import get_latest_data_timestamp
        from modules.ui_helpers import _format_relative_time

        _ctx = company_name or "대시보드"
        _page = nav_choice if nav_choice else None

        # 우측 메타: 데이터 소스 + 최신 + 유저
        _meta_parts = []
        try:
            if data_mode == "firebase":
                _lt = get_latest_data_timestamp()
                _rel = _format_relative_time(_lt) if _lt else None
                _meta_parts.append(f"Firebase · {_rel or '실시간'}")
            elif data_mode == "stored":
                _meta_parts.append("저장된 데이터")
            elif data_mode == "upload":
                _meta_parts.append("업로드 파일")
        except Exception:
            pass
        _user = get_user()
        if _user:
            _meta_parts.append(_user.get("name") or _user.get("username") or "")

        render_topbar(
            context=_ctx,
            current_page=_page,
            meta_right=" · ".join([p for p in _meta_parts if p]) or None,
        )
    except Exception:
        # 어떤 이유로 실패해도 대시보드 렌더는 계속
        pass

    # ── 데이터 로딩: 클라이언트 저장 데이터 또는 새 업로드 ──
    df_raw = None
    display_sheet = None

    # 경로 1: Firebase 실시간 연동 (광고주 연결된 클라이언트)
    if client_id and data_mode == "firebase":
        df_raw, display_sheet = _load_firebase_data(client_id)
        if df_raw is None:
            st.markdown(alert_card_html(
                "warning", "Firebase 데이터 없음",
                "연결된 광고주의 데이터를 찾을 수 없습니다. 광고주 이름을 확인하거나 '새 파일 업로드'를 이용해 주세요.",
            ), unsafe_allow_html=True)
            return

    # 경로 2: 클라이언트 저장 데이터
    elif client_id and data_mode == "stored":
        df_raw, display_sheet = _load_client_stored_data(client_id)
        if df_raw is None:
            st.markdown(alert_card_html(
                "info", "저장된 데이터 없음",
                "이 클라이언트에 아직 저장된 데이터가 없습니다. '새 파일 업로드'로 전환해 주세요.",
            ), unsafe_allow_html=True)
            return

    # 경로 3: 새 파일 업로드
    elif uploaded:
        _allowed_ext = ('.xlsx', '.xls', '.csv')
        if not uploaded.name.lower().endswith(_allowed_ext):
            st.warning("지원하지 않는 파일 형식입니다. 엑셀(.xlsx, .xls) 또는 CSV 파일을 업로드해 주세요.")
            return
        _MAX_FILE_MB = 100
        if uploaded.size > _MAX_FILE_MB * 1024 * 1024:
            st.error(f"파일이 너무 큽니다 ({uploaded.size / 1024 / 1024:.0f}MB). {_MAX_FILE_MB}MB 이하로 업로드해 주세요.")
            return

        _fkey = f"{uploaded.name}_{uploaded.size}_{sheet_name}"
        if st.session_state.get('_data_key') != _fkey:
            df_raw, display_sheet = load_and_preprocess(uploaded, sheet_name)
            if df_raw is None or df_raw.empty:
                return
            st.session_state['_data_key'] = _fkey
            st.session_state['_df_raw'] = df_raw
            st.session_state['_display_sheet'] = display_sheet
            st.session_state.df_loaded = df_raw

            # 클라이언트 선택 시 → 자동 저장/병합
            if client_id:
                merge_mode = st.session_state.get('_merge_mode', 'merge')
                if merge_mode == "merge":
                    df_raw = merge_data(client_id, df_raw)
                    # 병합 후 파생 컬럼 보장
                    df_raw = ensure_derived_columns(_normalize_columns(_merge_duplicate_metric_cols(df_raw)))
                    st.session_state['_df_raw'] = df_raw
                    st.session_state.df_loaded = df_raw
                else:
                    save_uploaded_data(client_id, df_raw)
                st.toast(f"'{company_name or client_id}' 데이터 저장 완료", icon="✅")
        else:
            df_raw = st.session_state['_df_raw']
            display_sheet = st.session_state['_display_sheet']

    # 경로 4: 아무것도 없음
    else:
        from modules.ui_helpers import render_empty_state
        render_empty_state(
            "분석할 데이터가 없습니다",
            "클라이언트를 선택하거나 파일을 업로드해 분석을 시작하세요. "
            "클라이언트를 등록하면 데이터를 누적 관리할 수 있습니다.",
            icon="📊",
            actions=[
                ("사이드바 → 클라이언트 선택", "이미 등록된 광고주면 저장 데이터 자동 로드"),
                ("사이드바 → 데이터 업로드", "엑셀(.xlsx) 또는 CSV 파일 업로드"),
                ("➕ 새 클라이언트 등록", "장기 관리를 위해 사이드바 '🗂️ 클라이언트 관리'에서"),
            ],
        )

        with st.expander("📋 파일 형식 가이드 & 샘플 데이터", expanded=False):
            st.markdown("""
**필수 컬럼** (자동 매핑 지원):
| 표준명 | 인식 가능한 이름 |
|--------|-----------------|
| 날짜 | 발송일, 일자, date, 기준일 |
| 매체명 | 발송구분, 매체, 채널, Media |
| 집행금액 | 비용, 소진액, 광고비, Cost |
| 발송량 | 발송건수, 노출수, 성공건수 |
| 클릭수 | 클릭, Click, 유입수 |
""")
            sample_csv = (
                "날짜,매체명,집행금액,발송량,클릭수\n"
                "2026-01-02,국민카드,5000000,50000,1500\n"
                "2026-01-02,신세계포인트,3000000,30000,900\n"
                "2026-01-15,국민카드,5500000,55000,1650\n"
                "2026-01-15,신세계포인트,3200000,32000,960\n"
                "2026-02-01,국민카드,6000000,60000,1800\n"
                "2026-02-01,신세계포인트,3500000,35000,1050\n"
                "2026-02-15,국민카드,6200000,62000,1860\n"
                "2026-02-15,신세계포인트,3800000,38000,1140\n"
                "2026-03-01,국민카드,7000000,70000,2100\n"
                "2026-03-01,신세계포인트,4000000,40000,1200\n"
                "2026-03-15,국민카드,7200000,72000,2160\n"
                "2026-03-15,신세계포인트,4200000,42000,1260\n"
            )
            st.download_button(
                "📥 샘플 CSV 다운로드",
                data=sample_csv.encode('utf-8-sig'),
                file_name="sample_lms_data.csv",
                mime="text/csv",
            )
        return

    # ── 이하 공통 (데이터 로드 성공) ──
    # 날짜 필터 적용
    df = df_raw
    if date_range and len(date_range) == 2:
        df = filter_by_date_range(df_raw, date_range[0], date_range[1])
        if df.empty:
            st.warning("선택한 기간에 해당하는 데이터가 없습니다.")
            return

    # 다운로드 버튼 렌더링
    _render_download_buttons(df, company_name, client_id)

    # ── 데이터 로드 성공 배너 ──
    period = f"{df['날짜'].min().strftime('%Y.%m.%d')} – {df['날짜'].max().strftime('%Y.%m.%d')}"
    if client_id and data_mode == "firebase":
        source_badge = "🔴 LIVE"
    elif client_id and data_mode == "stored":
        source_badge = "CLIENT"
    else:
        source_badge = "LOADED"
    st.markdown(
        f'<div class="data-banner"><div class="badge">{source_badge}</div><div class="info"><b>{display_sheet}</b> · {len(df):,}행 · {period}</div></div>',
        unsafe_allow_html=True,
    )

    # ── 데이터 품질 안내 ──
    n_months = df['년월'].nunique() if '년월' in df.columns else 0
    n_media = df['매체명'].nunique() if '매체명' in df.columns else 0
    warnings = []
    if n_months < 2:
        warnings.append("1개월 데이터 — 예산 배분·시뮬레이터·트렌드 비교가 제한됩니다.")
    if n_months < 3:
        warnings.append("3개월 미만 — 클릭 예측 정확도가 낮을 수 있습니다.")
    if n_media < 2:
        warnings.append("매체가 1개 — 매체 간 비교 분석이 불가합니다.")
    if warnings and n_months < 3:
        st.markdown(alert_card_html(
            "warning", f"데이터 품질 안내 ({n_months}개월 · {n_media}개 매체)",
            "<br>".join(f"• {w}" for w in warnings),
        ), unsafe_allow_html=True)

    # ── 선택된 섹션만 렌더링 (Linear 스타일: 단일 페이지) ──
    _active = _find_nav_item(nav_items, nav_choice)
    if _active is not None:
        _run_section(_active["label"], _active["render"], df)


if __name__ == "__main__":
    main()
