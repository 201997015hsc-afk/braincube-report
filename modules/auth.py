"""
인증 · 역할 관리 모듈
─────────────────────
로그인 UI + 세션 기반 인증 + 역할(internal / client) 분기.
계정 정보는 Firestore(`app_users` 컬렉션)에 저장 — 한 문서당 한 사용자
(문서 ID = username). Firestore 연결 실패 시 로컬 `users.json`으로
폴백하여 로컬 개발 환경을 보호합니다.
"""
import os
import json
import hashlib
import streamlit as st

from modules.config import BRAND_PRIMARY, COLOR_TEXT, COLOR_TEXT_SEC, COLOR_BG

_USERS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "users.json",
)

# Firestore 컬렉션명 (한 문서당 한 사용자, doc ID = username)
_USERS_COLLECTION = "app_users"

# ──────────────────────────────────────────────
# 역할 상수
# ──────────────────────────────────────────────
ROLE_INTERNAL = "internal"
ROLE_CLIENT = "client"


# ──────────────────────────────────────────────
# 계정 관리 (비밀번호 해시)
# ──────────────────────────────────────────────

def _hash_pw(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# ──────────────────────────────────────────────
# Firestore 헬퍼 (try/except로 연결 실패 보호)
# ──────────────────────────────────────────────

def _get_db():
    """Firestore 클라이언트 반환. 실패 시 None."""
    try:
        from modules.firebase_connector import _get_firestore_client
        return _get_firestore_client()
    except Exception:
        return None


def _firestore_load_users(db) -> dict | None:
    """Firestore `app_users` 컬렉션 전체 → dict. 실패 시 None."""
    try:
        docs = list(db.collection(_USERS_COLLECTION).stream())
        users = {}
        for doc in docs:
            data = doc.to_dict() or {}
            users[doc.id] = {
                "password": data.get("password", ""),
                "role": data.get("role", ROLE_CLIENT),
                "name": data.get("name", doc.id),
                "allowed_clients": data.get("allowed_clients", []) or [],
            }
        return users
    except Exception:
        return None


def _firestore_save_users(db, users: dict) -> bool:
    """Firestore에 사용자 dict 전체 저장 (upsert + 제거된 사용자 삭제). 실패 시 False."""
    try:
        coll = db.collection(_USERS_COLLECTION)
        # 현재 Firestore에 있는 문서 ID 목록
        existing_ids = {d.id for d in coll.stream()}
        new_ids = set(users.keys())

        # 1) upsert: 신규 + 변경
        for uid, info in users.items():
            coll.document(uid).set({
                "password": info.get("password", ""),
                "role": info.get("role", ROLE_CLIENT),
                "name": info.get("name", uid),
                "allowed_clients": info.get("allowed_clients", []) or [],
            })

        # 2) 삭제: Firestore에만 있고 new dict에 없는 문서
        for uid in existing_ids - new_ids:
            coll.document(uid).delete()

        return True
    except Exception:
        return False


# ──────────────────────────────────────────────
# 로컬 JSON 폴백 헬퍼
# ──────────────────────────────────────────────

def _local_load_users() -> dict | None:
    """로컬 users.json 읽기. 없거나 실패 시 None."""
    if not os.path.exists(_USERS_PATH):
        return None
    try:
        with open(_USERS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _local_save_users(users: dict) -> bool:
    """로컬 users.json 쓰기."""
    try:
        with open(_USERS_PATH, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────
# 기본 계정 (Firestore/로컬 모두 비었을 때 생성)
# ──────────────────────────────────────────────

def _default_users() -> dict:
    return {
        "admin": {
            "password": _hash_pw("admin1234"),
            "role": ROLE_INTERNAL,
            "name": "관리자",
            "allowed_clients": [],
        },
        "client": {
            "password": _hash_pw("client1234"),
            "role": ROLE_CLIENT,
            "name": "클라이언트",
            "allowed_clients": [],
        },
    }


# ──────────────────────────────────────────────
# 마이그레이션: 로컬 users.json → Firestore (최초 1회)
# ──────────────────────────────────────────────

def _migrate_from_local_users_json(db) -> bool:
    """로컬 users.json이 있고 Firestore 컬렉션이 비어있으면 전체 복사.

    - Firestore가 이미 채워져 있으면 no-op
    - 로컬 파일이 없으면 no-op
    - 성공 시 True, 할 일 없거나 실패 시 False
    """
    if db is None:
        return False
    try:
        # 이미 Firestore에 데이터가 있으면 skip
        existing = list(db.collection(_USERS_COLLECTION).limit(1).stream())
        if existing:
            return False
        # 로컬 파일 없으면 skip
        local = _local_load_users()
        if not local:
            return False
        # 복사
        return _firestore_save_users(db, local)
    except Exception:
        return False


# ──────────────────────────────────────────────
# Public: 사용자 로드 / 저장 (캐싱 적용)
# ──────────────────────────────────────────────

# Firestore 접근 비용 절감을 위해 10분 캐시 (할당량 이슈 대응)
@st.cache_data(ttl=600, show_spinner=False)
def _load_users_cached() -> dict:
    """Firestore에서 전체 사용자 로드 (10분 캐시).

    우선순위:
      1. Firestore (`app_users` 컬렉션)
      2. 로컬 users.json (Firestore 연결 실패 시 폴백)
      3. 기본 계정 (두 경로 모두 실패 시 메모리 반환)

    * 마이그레이션 및 기본 계정 초기화는 별도 함수로 분리
    """
    db = _get_db()

    # 1. Firestore 경로
    if db is not None:
        users = _firestore_load_users(db)
        if users is not None and users:
            try:
                st.session_state['_firestore_healthy'] = True
            except Exception:
                pass
            return users
        # Firestore 조회는 성공했으나 비어있음 → 초기화 경로로 이관
        if users is not None:
            # 빈 컬렉션이라 보일 수 있음. 세션 초기화 함수가 처리.
            pass
        else:
            try:
                st.session_state['_firestore_healthy'] = False
            except Exception:
                pass

    # 2. 로컬 폴백
    local = _local_load_users()
    if local:
        return local

    # 3. 기본 계정 (메모리 — 실패 시 최소한 로그인 가능 유지)
    return _default_users()


def _ensure_user_migration_done():
    """세션 최초 1회만 사용자 마이그레이션 + 기본 계정 시드 수행.

    매 rerun마다 Firestore 스트림 쿼리가 발생하지 않도록,
    세션 상태에 플래그를 저장해 중복 호출 방지.
    """
    try:
        if st.session_state.get('_user_migration_checked'):
            return
        st.session_state['_user_migration_checked'] = True
    except Exception:
        return

    db = _get_db()
    if db is None:
        return
    try:
        _migrate_from_local_users_json(db)
        # 마이그레이션 후 여전히 비어있으면 기본 계정 시드
        users = _firestore_load_users(db)
        if users is not None and not users:
            defaults = _default_users()
            _firestore_save_users(db, defaults)
            try:
                _load_users_cached.clear()
            except Exception:
                pass
    except Exception as e:
        _log_mod = __import__('logging').getLogger(__name__)
        _log_mod.warning(f"사용자 마이그레이션 실패 (무시): {e}")


def _load_users() -> dict:
    """사용자 dict 반환 (캐시 적용). 공개 API 유지."""
    _ensure_user_migration_done()
    return _load_users_cached()


def _save_users(users: dict):
    """사용자 dict 저장 → Firestore + 로컬 백업. 캐시 무효화."""
    db = _get_db()
    fs_ok = False
    if db is not None:
        fs_ok = _firestore_save_users(db, users)

    # 로컬 파일에도 백업 (Firestore 성공 여부 무관하게 로컬 사본 유지)
    # ⚠ Firestore가 실패했는데 로컬도 실패하면 예외 없이 조용히 넘어갈 수 있으므로
    #   Firestore 실패 시에는 로컬 저장 성공 여부를 반드시 남긴다.
    _local_save_users(users)

    # 캐시 무효화
    try:
        _load_users_cached.clear()
    except Exception:
        try:
            st.cache_data.clear()
        except Exception:
            pass


def _authenticate(username: str, password: str) -> dict | None:
    """인증 성공 → 유저 dict, 실패 → None"""
    users = _load_users()
    user = users.get(username)
    if user and user["password"] == _hash_pw(password):
        return {
            "username": username,
            "role": user["role"],
            "name": user["name"],
            # 클라이언트 계정만 해당: 접근 가능한 클라이언트 ID 목록
            # 빈 리스트 = 내부 계정(전체 접근) 또는 미지정
            "allowed_clients": user.get("allowed_clients", []),
        }
    return None


def get_allowed_clients() -> list[str]:
    """현재 로그인 사용자의 접근 가능 클라이언트 ID 목록.
    내부 관리자는 [] (무제한 — 전체 접근).
    """
    user = get_user()
    if not user:
        return []
    if user.get("role") == ROLE_INTERNAL:
        return []  # 무제한
    return user.get("allowed_clients", []) or []


def can_access_client(client_id: str | None) -> bool:
    """특정 클라이언트 ID에 접근 권한이 있는지 확인.
    내부 관리자 → 항상 True
    클라이언트 → allowed_clients 에 포함되어야 True
    client_id=None → 로그인 상태면 True (클라이언트 미선택)
    """
    user = get_user()
    if not user:
        return False
    if user.get("role") == ROLE_INTERNAL:
        return True
    if client_id is None:
        return True  # 클라이언트 미선택 상태는 허용
    allowed = user.get("allowed_clients", []) or []
    return client_id in allowed


# ──────────────────────────────────────────────
# 세션 헬퍼
# ──────────────────────────────────────────────

def is_logged_in() -> bool:
    return st.session_state.get("_auth_user") is not None


def get_user() -> dict | None:
    return st.session_state.get("_auth_user")


def get_role() -> str:
    user = get_user()
    return user["role"] if user else ROLE_CLIENT


def logout():
    for key in list(st.session_state.keys()):
        del st.session_state[key]


# ──────────────────────────────────────────────
# 계정 관리 (내부 관리자 전용)
# ──────────────────────────────────────────────

def render_account_manager():
    """사이드바 expander로 계정 관리 UI"""
    if get_role() != ROLE_INTERNAL:
        return

    from modules.client_manager import list_clients

    with st.expander("👥 계정 관리", expanded=False):
        users = _load_users()
        clients = list_clients()
        client_map = {c['id']: c.get('name', c['id']) for c in clients}

        # 현재 로그인한 관리자 (본인 삭제 방지용)
        _me = get_user()
        _me_uid = _me.get('username') if _me else None

        # ── 기존 계정 목록 ──
        for uid, info in users.items():
            role_badge = "🔧 내부" if info["role"] == ROLE_INTERNAL else "👤 클라이언트"
            allowed = info.get("allowed_clients", []) or []
            if info["role"] == ROLE_CLIENT:
                if allowed:
                    names = [client_map.get(cid, cid) for cid in allowed]
                    brand_badge = f' · 🔗 {", ".join(names)}'
                else:
                    brand_badge = ' · <span style="color:#FF9100;">⚠ 광고주 미할당</span>'
            else:
                brand_badge = ''
            is_me = (uid == _me_uid)
            me_tag = ' <span style="color:#3182F6;font-size:.7rem;">(본인)</span>' if is_me else ''
            st.markdown(
                f'<div style="background:#F8F9FA;border-radius:8px;padding:8px 12px;'
                f'margin-bottom:4px;font-size:0.82rem;">'
                f'<b>{info["name"]}</b>{me_tag} ({uid}) · {role_badge}{brand_badge}</div>',
                unsafe_allow_html=True,
            )

            # 편집 / 삭제 버튼
            _ebtn_key = f'_edit_acc_{uid}'
            _dbtn_key = f'_del_acc_{uid}'
            _bc1, _bc2 = st.columns(2)
            with _bc1:
                if st.button("✏️ 수정", key=f'btn_{_ebtn_key}', use_container_width=True):
                    st.session_state[_ebtn_key] = True
                    st.session_state.pop(_dbtn_key, None)
            with _bc2:
                if is_me:
                    st.button("🗑️ 삭제", key=f'btn_{_dbtn_key}',
                              use_container_width=True, disabled=True,
                              help="본인 계정은 삭제할 수 없습니다.")
                else:
                    if st.button("🗑️ 삭제", key=f'btn_{_dbtn_key}', use_container_width=True):
                        st.session_state[_dbtn_key] = True
                        st.session_state.pop(_ebtn_key, None)

            # ── 수정 폼 ──
            if st.session_state.get(_ebtn_key):
                _new_name = st.text_input(
                    "이름", value=info.get("name", ""), key=f'name_{_ebtn_key}',
                )
                _new_pw = st.text_input(
                    "새 비밀번호 (변경 시에만 입력)", type="password",
                    key=f'pw_{_ebtn_key}', placeholder="변경하지 않으려면 비워두세요",
                )
                # 역할 선택 (본인 제외)
                if is_me:
                    _new_role = info["role"]
                    st.caption(f"역할: {role_badge} (본인 계정 역할은 변경 불가)")
                else:
                    _role_idx = 0 if info["role"] == ROLE_CLIENT else 1
                    _new_role = st.selectbox(
                        "역할",
                        [ROLE_CLIENT, ROLE_INTERNAL],
                        index=_role_idx,
                        format_func=lambda r: "👤 클라이언트" if r == ROLE_CLIENT else "🔧 내부 운영",
                        key=f'role_{_ebtn_key}',
                    )
                # 클라이언트 역할일 때만 광고주 선택
                _new_allowed = allowed
                if _new_role == ROLE_CLIENT:
                    _new_allowed = st.multiselect(
                        "🔗 접근 가능 광고주",
                        options=[c['id'] for c in clients],
                        default=[a for a in allowed if a in client_map],
                        format_func=lambda cid: client_map.get(cid, cid),
                        key=f'ms_{_ebtn_key}',
                    )

                _c1, _c2 = st.columns(2)
                with _c1:
                    if st.button("저장", key=f'save_{_ebtn_key}', type="primary",
                                 use_container_width=True):
                        if _new_role == ROLE_CLIENT and not _new_allowed:
                            st.warning("클라이언트 계정은 최소 1개 광고주 할당이 필요합니다.")
                        else:
                            users[uid]['name'] = _new_name.strip() or uid
                            users[uid]['role'] = _new_role
                            users[uid]['allowed_clients'] = (
                                _new_allowed if _new_role == ROLE_CLIENT else []
                            )
                            if _new_pw.strip():
                                users[uid]['password'] = _hash_pw(_new_pw)
                            _save_users(users)
                            st.session_state.pop(_ebtn_key, None)
                            st.session_state['_toast_msg'] = (
                                f"💾 '{_new_name or uid}' 계정 수정됨", "success"
                            )
                            st.rerun()
                with _c2:
                    if st.button("취소", key=f'cancel_{_ebtn_key}',
                                 use_container_width=True):
                        st.session_state.pop(_ebtn_key, None)
                        st.rerun()

            # ── 삭제 확인 ──
            if st.session_state.get(_dbtn_key):
                st.warning(f"'{info['name']}' ({uid}) 계정을 삭제하시겠어요?")
                _dc1, _dc2 = st.columns(2)
                with _dc1:
                    if st.button("예, 삭제", key=f'confirm_{_dbtn_key}',
                                 type="primary", use_container_width=True):
                        del users[uid]
                        _save_users(users)
                        st.session_state.pop(_dbtn_key, None)
                        st.session_state['_toast_msg'] = (
                            f"🗑️ '{uid}' 계정 삭제됨", "warning"
                        )
                        st.rerun()
                with _dc2:
                    if st.button("취소", key=f'cancel_{_dbtn_key}',
                                 use_container_width=True):
                        st.session_state.pop(_dbtn_key, None)
                        st.rerun()

        st.markdown("---")
        st.markdown(
            '<div style="font-size:0.78rem;font-weight:600;color:#4E5968;'
            'margin-bottom:6px;">새 계정 추가</div>',
            unsafe_allow_html=True,
        )
        new_id = st.text_input("아이디", key="_new_user_id", placeholder="예: wink_user")
        new_name = st.text_input("이름", key="_new_user_name", placeholder="예: 윙크 담당자")
        new_pw = st.text_input("비밀번호", key="_new_user_pw", type="password")
        new_role = st.selectbox(
            "역할",
            [ROLE_CLIENT, ROLE_INTERNAL],
            format_func=lambda r: "👤 클라이언트" if r == ROLE_CLIENT else "🔧 내부 운영",
            key="_new_user_role",
        )
        # 클라이언트 역할일 때만 광고주 선택
        new_allowed: list[str] = []
        if new_role == ROLE_CLIENT:
            if clients:
                new_allowed = st.multiselect(
                    "🔗 접근 가능 광고주",
                    options=[c['id'] for c in clients],
                    format_func=lambda cid: client_map.get(cid, cid),
                    key="_new_user_allowed",
                    help="이 계정으로 로그인하면 선택한 광고주 데이터만 볼 수 있습니다.",
                )
            else:
                st.caption("⚠ 먼저 클라이언트를 등록해 주세요.")

        if st.button("계정 추가", key="_btn_add_user", type="primary"):
            if not new_id.strip() or not new_pw.strip():
                st.warning("아이디와 비밀번호를 입력해 주세요.")
            elif new_role == ROLE_CLIENT and not new_allowed:
                st.warning("클라이언트 계정은 최소 하나의 광고주를 할당해야 합니다.")
            else:
                users[new_id.strip()] = {
                    "password": _hash_pw(new_pw),
                    "role": new_role,
                    "name": new_name.strip() or new_id.strip(),
                    "allowed_clients": new_allowed,
                }
                _save_users(users)
                st.session_state['_toast_msg'] = (
                    f"✅ '{new_name or new_id}' 계정이 추가됨", "success"
                )
                st.rerun()


# ──────────────────────────────────────────────
# 로그인 페이지
# ──────────────────────────────────────────────

_LOGIN_CSS = f"""
<style>
/* ═══════════════════════════════════════════════════
   로그인 페이지 — Linear/Notion 감성 (밝고 깔끔)
   ═══════════════════════════════════════════════════ */

/* 전체 배경: 매우 옅은 회백색 */
.stApp {{
    background: #FAFBFC !important;
}}

/* 사이드바 숨김 */
section[data-testid='stSidebar'] {{ display: none !important; }}

/* Streamlit 기본 헤더 투명 */
header[data-testid="stHeader"] {{
    background: transparent !important;
}}

/* 메인 블록 중앙 정렬 + 좁은 폭 */
.stMainBlockContainer {{
    max-width: 400px !important;
    margin: 0 auto !important;
    padding-top: 56px !important;
}}

/* 상단 로고·타이틀 영역 */
.login-card {{
    padding: 0 4px 0 4px;
    text-align: center;
}}

/* 폼 컨테이너 — 얇은 보더 플랫 카드 */
[data-testid="stForm"] {{
    background: #FFFFFF !important;
    backdrop-filter: none !important;
    -webkit-backdrop-filter: none !important;
    border-radius: 12px !important;
    border: 1px solid #E5E7EB !important;
    box-shadow: none !important;
    padding: 32px 28px 24px 28px !important;
}}

/* 로고 아이콘 — 플랫 브랜드 오렌지 */
.login-logo {{
    width: 52px; height: 52px;
    margin: 0 auto 18px auto;
    border-radius: 12px;
    background: {BRAND_PRIMARY};
    display: flex; align-items: center; justify-content: center;
    box-shadow: none;
    animation: none;
}}
.login-logo svg {{
    width: 28px; height: 28px;
}}

/* 타이틀 — 단색 · 세미볼드 */
.login-title {{
    text-align: center;
    margin-bottom: 6px;
    font-size: 1.4rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: #111827;
    background: none;
    -webkit-text-fill-color: #111827;
}}
.login-subtitle {{
    text-align: center;
    font-size: 0.82rem;
    color: #6B7280;
    margin-bottom: 28px;
    letter-spacing: -0.005em;
}}

/* 구분선 — 연한 회색 */
.login-divider {{
    display: flex; align-items: center; gap: 12px;
    margin: 0 0 20px 0;
}}
.login-divider .line {{
    flex: 1; height: 1px;
    background: #E5E7EB;
}}
.login-divider .text {{
    font-size: 0.72rem; color: #9CA3AF;
    white-space: nowrap;
    font-weight: 500;
}}

/* 인풋 — 얇은 보더, 흰 배경 */
.stTextInput > div > div {{
    background: #FFFFFF !important;
    border: 1px solid #E5E7EB !important;
    border-radius: 8px !important;
    color: #111827 !important;
    transition: border-color 0.15s, box-shadow 0.15s;
}}
.stTextInput > div > div:focus-within {{
    border-color: {BRAND_PRIMARY} !important;
    box-shadow: 0 0 0 3px rgba(247,147,29,0.10) !important;
    background: #FFFFFF !important;
}}
.stTextInput input {{
    color: #111827 !important;
    font-size: 0.9rem !important;
    font-weight: 500 !important;
    caret-color: {BRAND_PRIMARY} !important;
    -webkit-text-fill-color: #111827 !important;
}}
.stTextInput input::placeholder {{
    color: #9CA3AF !important;
    -webkit-text-fill-color: #9CA3AF !important;
}}
.stTextInput label {{
    color: #4B5563 !important;
    font-weight: 500 !important;
    font-size: 0.82rem !important;
}}
/* Streamlit 힌트 텍스트 숨김 */
.stTextInput [data-testid="InputInstructions"] {{
    display: none !important;
}}

/* 버튼 — 플랫 솔리드 */
.stFormSubmitButton button {{
    background: {BRAND_PRIMARY} !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 8px !important;
    padding: 11px 0 !important;
    font-size: 0.9rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.005em;
    box-shadow: none !important;
    transition: background 0.15s !important;
}}
.stFormSubmitButton button:hover {{
    background: #E67E00 !important;
    transform: none !important;
    box-shadow: none !important;
}}
.stFormSubmitButton button:active {{
    transform: none !important;
}}

/* 푸터 */
.login-footer {{
    text-align: center;
    margin-top: 24px;
    font-size: 0.72rem;
    color: #9CA3AF;
    letter-spacing: 0;
}}
.login-footer a {{
    color: #6B7280;
    text-decoration: none;
}}

/* 에러 메시지 */
.stAlert {{
    background: rgba(239,68,68,0.05) !important;
    border: 1px solid rgba(239,68,68,0.25) !important;
    border-radius: 8px !important;
    color: #DC2626 !important;
}}
</style>
"""

_LOGIN_LOGO_SVG = (
    '<svg viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">'
    '<rect x="4" y="14" width="6" height="14" rx="2" fill="white" opacity="0.85"/>'
    '<rect x="13" y="8" width="6" height="20" rx="2" fill="white"/>'
    '<rect x="22" y="4" width="6" height="24" rx="2" fill="white" opacity="0.85"/>'
    '</svg>'
)


# ──────────────────────────────────────────────
# 로그인 Rate Limiting (브루트포스 방지)
# ──────────────────────────────────────────────
_MAX_LOGIN_ATTEMPTS = 5
_LOCKOUT_DURATION_SEC = 300  # 5분


def _check_rate_limit() -> tuple[bool, int]:
    """로그인 시도 제한 확인.

    반환: (허용 여부, 남은 잠금 시간[초])
      - 허용: (True, 0)
      - 잠김: (False, 남은 초)
    """
    import time
    lockout_until = st.session_state.get('_login_lockout_until', 0)
    now = time.time()
    if lockout_until > now:
        return False, int(lockout_until - now)
    return True, 0


def _record_failed_login():
    """로그인 실패 기록. N회 초과 시 잠금."""
    import time
    attempts = st.session_state.get('_login_failed_attempts', 0) + 1
    st.session_state['_login_failed_attempts'] = attempts
    if attempts >= _MAX_LOGIN_ATTEMPTS:
        st.session_state['_login_lockout_until'] = time.time() + _LOCKOUT_DURATION_SEC
        st.session_state['_login_failed_attempts'] = 0  # 리셋


def _clear_failed_logins():
    """로그인 성공 시 실패 카운터 리셋."""
    st.session_state.pop('_login_failed_attempts', None)
    st.session_state.pop('_login_lockout_until', None)


def render_login_page():
    """로그인 페이지 렌더링. 인증 성공 시 True 반환."""
    st.markdown(_LOGIN_CSS, unsafe_allow_html=True)

    st.markdown(
        '<div class="login-card">'
        f'<div class="login-logo">{_LOGIN_LOGO_SVG}</div>'
        '<div class="login-title">Braincube AI Report</div>'
        '<div class="login-subtitle">데이터 기반 성과 분석 · 인사이트 · 전략 제안</div>'
        '<div class="login-divider">'
        '<div class="line"></div>'
        '<div class="text">계정으로 로그인</div>'
        '<div class="line"></div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    # 로그인 폼
    with st.form("login_form"):
        username = st.text_input("아이디", placeholder="아이디를 입력하세요")
        password = st.text_input("비밀번호", type="password", placeholder="비밀번호를 입력하세요")
        submitted = st.form_submit_button("로그인", use_container_width=True, type="primary")

    if submitted:
        # Rate limit 확인
        allowed, remaining = _check_rate_limit()
        if not allowed:
            mins = remaining // 60
            secs = remaining % 60
            st.error(
                f"⛔ 로그인 시도 횟수를 초과했습니다. "
                f"{mins}분 {secs}초 후 다시 시도해 주세요."
            )
            return False

        if not username or not password:
            st.error("아이디와 비밀번호를 입력해 주세요.")
            return False

        user = _authenticate(username.strip(), password)
        if user:
            _clear_failed_logins()
            st.session_state["_auth_user"] = user
            st.rerun()
        else:
            _record_failed_login()
            remaining_attempts = _MAX_LOGIN_ATTEMPTS - st.session_state.get('_login_failed_attempts', 0)
            if remaining_attempts > 0:
                st.error(
                    f"아이디 또는 비밀번호가 올바르지 않습니다. "
                    f"(남은 시도: {remaining_attempts}회)"
                )
            else:
                st.error(
                    "⛔ 로그인 시도 횟수를 초과했습니다. "
                    "5분간 로그인이 제한됩니다."
                )
            return False

    st.markdown(
        '<div class="login-footer">'
        f'© {__import__("datetime").datetime.now().year} Braincube · AI Report System'
        '</div>',
        unsafe_allow_html=True,
    )

    return False
