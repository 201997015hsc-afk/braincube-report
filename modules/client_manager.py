"""
멀티 클라이언트 관리 모듈
─────────────────────────
클라이언트 프로필은 Firestore(`client_profiles` 컬렉션)에 저장 — 한 문서당 한
클라이언트(문서 ID = client_id 슬러그). Firestore 연결 실패 시 로컬
`.clients/{client_id}/profile.json`으로 폴백하여 로컬 개발 환경을 보호합니다.

데이터 관련 함수(merged.csv, 리포트 아카이브)는 여전히 로컬 파일시스템을
사용합니다. 클라우드 배포 환경(Streamlit Cloud)에서는 영속 스토리지가
없으므로 세션 단위의 no-op 동작이 되며, 예외 대신 경고로 처리됩니다.
"""
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st

_log = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
_CLIENTS_ROOT = os.path.join(_BASE_DIR, ".clients")

# Firestore 컬렉션명 (한 문서당 한 클라이언트, doc ID = client_id 슬러그)
_PROFILES_COLLECTION = "client_profiles"

# 프로필 스키마 키 (Firestore <-> 로컬 변환 시 동일 구조 유지)
_PROFILE_FIELDS = (
    "name",
    "domain",
    "contact_email",
    "memo",
    "firebase_advertiser",
    "gsheet_url",
    "gsheet_worksheet",
    "created",
    "updated",
)


# ──────────────────────────────────────────────
# 경로 헬퍼 (로컬 파일 시스템 — 폴백 및 데이터/리포트)
# ──────────────────────────────────────────────

def _client_dir(client_id: str) -> str:
    return os.path.join(_CLIENTS_ROOT, client_id)


def _profile_path(client_id: str) -> str:
    return os.path.join(_client_dir(client_id), "profile.json")


def _data_dir(client_id: str) -> str:
    return os.path.join(_client_dir(client_id), "data")


def _merged_csv_path(client_id: str) -> str:
    return os.path.join(_data_dir(client_id), "merged.csv")


def _reports_dir(client_id: str) -> str:
    return os.path.join(_client_dir(client_id), "reports")


def _slugify(name: str) -> str:
    """한글·영문·숫자만 남겨 안전한 폴더명/문서 ID 생성"""
    slug = re.sub(r'[^\w가-힣]', '_', name.strip())
    slug = re.sub(r'_+', '_', slug).strip('_')
    return slug.lower() or "client"


def _safe_makedirs(path: str) -> bool:
    """디렉토리 생성 시도. 실패 시(클라우드 읽기 전용 FS 등) 경고 후 False."""
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception as e:
        _log.warning(f"디렉토리 생성 실패 ({path}): {e}")
        return False


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


def _firestore_load_profiles(db) -> dict | None:
    """Firestore `client_profiles` 컬렉션 전체 → {client_id: profile_dict}.
    실패 시 None."""
    try:
        docs = list(db.collection(_PROFILES_COLLECTION).stream())
        profiles: dict[str, dict] = {}
        for doc in docs:
            data = doc.to_dict() or {}
            # 스키마 표준화 — 누락 필드는 빈 문자열로 채움
            profile = {k: data.get(k, "") for k in _PROFILE_FIELDS}
            profiles[doc.id] = profile
        return profiles
    except Exception as e:
        _log.warning(f"Firestore 프로필 로드 실패: {e}")
        return None


def _firestore_load_profile(db, client_id: str) -> dict | None:
    """Firestore에서 단일 프로필 조회. 없거나 실패 시 None."""
    try:
        doc = db.collection(_PROFILES_COLLECTION).document(client_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict() or {}
        return {k: data.get(k, "") for k in _PROFILE_FIELDS}
    except Exception as e:
        _log.warning(f"Firestore 프로필 조회 실패 ({client_id}): {e}")
        return None


def _firestore_save_profile(db, client_id: str, profile: dict) -> bool:
    """Firestore 프로필 upsert. 성공 시 True."""
    try:
        payload = {k: profile.get(k, "") for k in _PROFILE_FIELDS}
        db.collection(_PROFILES_COLLECTION).document(client_id).set(payload)
        return True
    except Exception as e:
        _log.warning(f"Firestore 프로필 저장 실패 ({client_id}): {e}")
        return False


def _firestore_delete_profile(db, client_id: str) -> bool:
    """Firestore 프로필 삭제. 성공 시 True."""
    try:
        db.collection(_PROFILES_COLLECTION).document(client_id).delete()
        return True
    except Exception as e:
        _log.warning(f"Firestore 프로필 삭제 실패 ({client_id}): {e}")
        return False


def _firestore_doc_exists(db, client_id: str) -> bool:
    """Firestore 문서 존재 여부."""
    try:
        doc = db.collection(_PROFILES_COLLECTION).document(client_id).get()
        return doc.exists
    except Exception:
        return False


# ──────────────────────────────────────────────
# 로컬 JSON 폴백 헬퍼
# ──────────────────────────────────────────────

def _local_load_profile(client_id: str) -> dict | None:
    """로컬 profile.json 읽기. 없거나 실패 시 None."""
    path = _profile_path(client_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        _log.warning(f"프로필 JSON 손상 ({client_id}): {e}")
        return None
    except Exception as e:
        _log.warning(f"프로필 로드 실패 ({client_id}): {e}")
        return None


def _local_save_profile(client_id: str, profile: dict) -> bool:
    """로컬 profile.json 쓰기. 실패 시 False (클라우드 읽기 전용 FS 대응)."""
    if not _safe_makedirs(_client_dir(client_id)):
        return False
    path = _profile_path(client_id)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        _log.warning(f"로컬 프로필 저장 실패 ({client_id}): {e}")
        return False


def _local_list_profiles() -> dict:
    """로컬 .clients/ 전체 스캔 → {client_id: profile_dict}."""
    profiles: dict[str, dict] = {}
    if not os.path.isdir(_CLIENTS_ROOT):
        return profiles
    try:
        for cid in sorted(os.listdir(_CLIENTS_ROOT)):
            profile = _local_load_profile(cid)
            if profile:
                profiles[cid] = profile
    except Exception as e:
        _log.warning(f"로컬 프로필 목록 스캔 실패: {e}")
    return profiles


def _local_delete_profile(client_id: str) -> bool:
    """로컬 클라이언트 디렉토리 전체 삭제. 성공 시 True."""
    path = _client_dir(client_id)
    if not os.path.isdir(path):
        return False
    try:
        shutil.rmtree(path, ignore_errors=True)
        return True
    except Exception as e:
        _log.warning(f"로컬 디렉토리 삭제 실패 ({client_id}): {e}")
        return False


# ──────────────────────────────────────────────
# 마이그레이션: 로컬 .clients/*/profile.json → Firestore (최초 1회)
# ──────────────────────────────────────────────

def _migrate_local_profiles_to_firestore(db) -> bool:
    """로컬 프로필이 있고 Firestore 컬렉션이 비어있으면 전체 복사.

    - Firestore가 이미 채워져 있으면 no-op
    - 로컬 프로필이 없으면 no-op
    - 성공 시 True, 할 일 없거나 실패 시 False
    """
    if db is None:
        return False
    try:
        # 이미 Firestore에 데이터가 있으면 skip
        existing = list(db.collection(_PROFILES_COLLECTION).limit(1).stream())
        if existing:
            return False
        # 로컬 프로필 없으면 skip
        local = _local_list_profiles()
        if not local:
            return False
        # 복사
        ok = True
        for cid, profile in local.items():
            if not _firestore_save_profile(db, cid, profile):
                ok = False
        return ok
    except Exception as e:
        _log.warning(f"로컬 → Firestore 프로필 마이그레이션 실패: {e}")
        return False


# ──────────────────────────────────────────────
# 캐시된 프로필 로더 (Firestore 비용 절감)
# ──────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _load_all_profiles_cached() -> dict:
    """전체 클라이언트 프로필 로드 (60초 캐시).

    우선순위:
      1. Firestore (`client_profiles` 컬렉션)
         - 최초 호출 시 로컬 프로필이 있으면 마이그레이션 시도
      2. 로컬 `.clients/*/profile.json` (Firestore 연결 실패 시 폴백)
    """
    db = _get_db()
    if db is not None:
        # 최초 1회: 로컬 → Firestore 마이그레이션 (멱등)
        try:
            _migrate_local_profiles_to_firestore(db)
        except Exception:
            pass

        profiles = _firestore_load_profiles(db)
        if profiles is not None:
            return profiles

    # 폴백: 로컬 파일 시스템
    return _local_list_profiles()


def _invalidate_profile_cache():
    """프로필 캐시 무효화 (create/update/delete 시 호출)."""
    try:
        _load_all_profiles_cached.clear()
    except Exception:
        try:
            st.cache_data.clear()
        except Exception:
            pass


# ──────────────────────────────────────────────
# 클라이언트 CRUD (공개 API)
# ──────────────────────────────────────────────

def list_clients() -> list[dict]:
    """등록된 전체 클라이언트 목록 반환. [{id, name, created, ...}, ...]"""
    profiles = _load_all_profiles_cached()
    clients: list[dict] = []
    for cid in sorted(profiles.keys()):
        clients.append({"id": cid, **profiles[cid]})
    return clients


def load_profile(client_id: str) -> Optional[dict]:
    """프로필 로드. 없으면 None.

    Firestore 우선, 실패 시 로컬 폴백.
    """
    # 캐시된 dict에서 우선 조회 (list_clients 호출 직후 연속 호출 최적화)
    profiles = _load_all_profiles_cached()
    if client_id in profiles:
        return profiles[client_id]

    # 캐시 미스 — 직접 조회 (드문 경우: 캐시 갱신 전 새 ID)
    db = _get_db()
    if db is not None:
        profile = _firestore_load_profile(db, client_id)
        if profile is not None:
            return profile

    # 로컬 폴백
    return _local_load_profile(client_id)


def save_profile(client_id: str, profile: dict):
    """프로필 저장 — Firestore + 로컬 백업. 캐시 무효화."""
    db = _get_db()
    fs_ok = False
    if db is not None:
        fs_ok = _firestore_save_profile(db, client_id, profile)

    # 로컬 백업 (실패해도 Firestore 성공 시 무시 — 클라우드 읽기 전용 FS 대응)
    local_ok = _local_save_profile(client_id, profile)

    if not fs_ok and not local_ok:
        _log.error(f"프로필 저장 실패 ({client_id}): Firestore/로컬 모두 실패")

    _invalidate_profile_cache()


def create_client(
    name: str,
    domain: str = "",
    contact_email: str = "",
    memo: str = "",
    firebase_advertiser: str = "",
) -> str:
    """새 클라이언트 생성 → client_id 반환."""
    db = _get_db()
    base_slug = _slugify(name)
    client_id = base_slug

    # 중복 방지: 숫자 서픽스 (Firestore + 로컬 모두 고려)
    existing_profiles = _load_all_profiles_cached()
    counter = 1
    while (
        client_id in existing_profiles
        or os.path.isdir(_client_dir(client_id))
        or (db is not None and _firestore_doc_exists(db, client_id))
    ):
        client_id = f"{base_slug}_{counter}"
        counter += 1

    now = datetime.now().isoformat()
    profile = {
        "name": name,
        "domain": domain,
        "contact_email": contact_email,
        "memo": memo,
        "firebase_advertiser": firebase_advertiser,
        "gsheet_url": "",
        "gsheet_worksheet": "",
        "created": now,
        "updated": now,
    }

    # 로컬 디렉토리 구조 생성 (실패해도 치명적이지 않음 — 클라우드 대응)
    _safe_makedirs(_data_dir(client_id))
    _safe_makedirs(_reports_dir(client_id))

    save_profile(client_id, profile)
    return client_id


def delete_client(client_id: str) -> bool:
    """클라이언트 삭제 (Firestore + 로컬). 둘 중 하나라도 성공하면 True."""
    db = _get_db()
    fs_ok = False
    if db is not None:
        fs_ok = _firestore_delete_profile(db, client_id)

    local_ok = _local_delete_profile(client_id)

    _invalidate_profile_cache()
    return fs_ok or local_ok


def update_client(client_id: str, **kwargs) -> bool:
    """기존 프로필 필드 업데이트. 성공이면 True."""
    profile = load_profile(client_id)
    if not profile:
        return False
    profile.update(kwargs)
    profile["updated"] = datetime.now().isoformat()
    save_profile(client_id, profile)
    return True


# ──────────────────────────────────────────────
# 클라이언트 데이터 관리 (로컬 파일 시스템 — 클라우드에서는 no-op/세션 단위)
# ──────────────────────────────────────────────

def load_client_data(client_id: str) -> Optional[pd.DataFrame]:
    """병합된 CSV 로드. 없거나 실패 시 None (클라우드 안전)."""
    try:
        path = _merged_csv_path(client_id)
        if not os.path.isfile(path):
            return None
        df = pd.read_csv(path, encoding="utf-8-sig")
        if '날짜' in df.columns:
            df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
        return df
    except Exception as e:
        _log.warning(f"클라이언트 데이터 로드 실패 ({client_id}): {e}")
        return None


def save_uploaded_data(client_id: str, df: pd.DataFrame):
    """업로드 데이터를 merged.csv로 저장 (원자적 쓰기).

    클라우드 환경에서 파일 시스템 쓰기가 실패할 수 있으므로
    예외를 삼키고 경고만 남긴다 (세션 단위 메모리 데이터는 유지됨).
    """
    try:
        data_dir = _data_dir(client_id)
        if not _safe_makedirs(data_dir):
            _log.warning(
                f"데이터 디렉토리 생성 실패 ({client_id}): "
                "클라우드 환경이면 세션 데이터만 유지됩니다."
            )
            return

        dest = _merged_csv_path(client_id)
        # 임시 파일 → 이름 변경 (atomic write)
        fd, tmp = tempfile.mkstemp(suffix='.csv', dir=data_dir)
        try:
            os.close(fd)
            df.to_csv(tmp, index=False, encoding="utf-8-sig")
            # Windows: 기존 파일 삭제 후 rename
            if os.path.exists(dest):
                os.replace(tmp, dest)
            else:
                os.rename(tmp, dest)
        except Exception:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            raise
    except Exception as e:
        _log.warning(
            f"업로드 데이터 저장 실패 ({client_id}): {e} "
            "(클라우드 환경에서는 정상입니다 — 세션 데이터만 유지됨)"
        )
        return

    # 저장 성공 시에만 updated 타임스탬프 반영
    try:
        update_client(client_id, updated=datetime.now().isoformat())
    except Exception as e:
        _log.warning(f"updated 타임스탬프 업데이트 실패 ({client_id}): {e}")


def merge_data(client_id: str, new_df: pd.DataFrame) -> pd.DataFrame:
    """
    새 데이터를 기존 데이터에 누적 병합.
    - 동일 (날짜, 매체명) 행은 새 데이터로 교체
    - 새로운 행은 추가
    반환: 병합 후 전체 DataFrame
    """
    existing = load_client_data(client_id)

    if existing is None or existing.empty:
        save_uploaded_data(client_id, new_df)
        return new_df

    # 날짜 타입 통일
    for d in [existing, new_df]:
        if '날짜' in d.columns:
            d['날짜'] = pd.to_datetime(d['날짜'], errors='coerce')

    # 매체명 없으면 기본값
    for d in [existing, new_df]:
        if '매체명' not in d.columns:
            d['매체명'] = 'LMS 전체'

    # concat + drop_duplicates (새 데이터 우선)
    merged = pd.concat([existing, new_df], ignore_index=True)

    # 날짜 문자열 키 생성하여 중복 제거
    merged['_merge_key'] = merged['날짜'].dt.strftime('%Y-%m-%d') + '||' + merged['매체명'].astype(str)
    merged = merged.drop_duplicates(subset=['_merge_key'], keep='last')
    merged = merged.drop(columns=['_merge_key'])
    merged = merged.sort_values('날짜').reset_index(drop=True)

    save_uploaded_data(client_id, merged)
    return merged


# ──────────────────────────────────────────────
# 리포트 아카이브 (로컬 파일 시스템 — 클라우드에서는 no-op)
# ──────────────────────────────────────────────

def archive_report(client_id: str, filename: str, data: bytes) -> str:
    """리포트 파일 아카이브 → 저장된 경로 반환 (실패 시 빈 문자열)."""
    try:
        reports = _reports_dir(client_id)
        if not _safe_makedirs(reports):
            _log.warning(
                f"리포트 디렉토리 생성 실패 ({client_id}): "
                "클라우드 환경에서는 사용자가 직접 다운로드해야 합니다."
            )
            return ""
        # 파일명에 날짜 프리픽스 추가
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        safe_name = f"{ts}_{filename}"
        path = os.path.join(reports, safe_name)
        with open(path, "wb") as f:
            f.write(data)
        return path
    except Exception as e:
        _log.warning(
            f"리포트 아카이브 실패 ({client_id}): {e} "
            "(클라우드 환경에서는 정상입니다)"
        )
        return ""


def list_archived_reports(client_id: str) -> list[dict]:
    """아카이브된 리포트 목록. [{filename, path, size_kb, modified}, ...].
    디렉토리 없거나 실패 시 빈 리스트."""
    try:
        reports = _reports_dir(client_id)
        if not os.path.isdir(reports):
            return []
        items = []
        for fname in sorted(os.listdir(reports), reverse=True):
            fpath = os.path.join(reports, fname)
            if os.path.isfile(fpath):
                stat = os.stat(fpath)
                items.append({
                    "filename": fname,
                    "path": fpath,
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                })
        return items
    except Exception as e:
        _log.warning(f"리포트 목록 조회 실패 ({client_id}): {e}")
        return []


def get_client_stats(client_id: str) -> dict:
    """클라이언트 데이터 통계 요약.

    우선순위:
      1. Firebase 연동 클라이언트 → Firestore에서 실시간 집계
      2. 로컬 CSV 업로드 데이터 → merged.csv 읽기
      3. 둘 다 없으면 빈 통계 (0건)

    실패 시 예외 없이 0 반환.
    """
    reports = list_archived_reports(client_id)
    empty = {"rows": 0, "date_range": "", "media_count": 0, "report_count": len(reports)}

    # 1. Firebase 연동 브랜드가 있으면 Firestore에서 통계 계산
    profile = load_profile(client_id)
    fb_brand = profile.get('firebase_advertiser', '') if profile else ''
    if fb_brand:
        try:
            from modules.firebase_connector import load_advertiser_data
            fb_df = load_advertiser_data(fb_brand)
            if fb_df is not None and not fb_df.empty:
                date_range = ""
                if '날짜' in fb_df.columns:
                    dmin, dmax = fb_df['날짜'].min(), fb_df['날짜'].max()
                    if pd.notna(dmin) and pd.notna(dmax):
                        date_range = f"{dmin:%Y.%m.%d} ~ {dmax:%Y.%m.%d}"
                return {
                    "rows": len(fb_df),
                    "date_range": date_range,
                    "media_count": fb_df['매체명'].nunique() if '매체명' in fb_df.columns else 0,
                    "report_count": len(reports),
                }
        except Exception as e:
            _log.warning(f"Firebase 통계 조회 실패 ({client_id}): {e}")

    # 2. 로컬 CSV 데이터 (업로드 방식)
    df = load_client_data(client_id)
    if df is None or df.empty:
        return empty
    try:
        date_range = ""
        if '날짜' in df.columns:
            date_min = df['날짜'].min()
            date_max = df['날짜'].max()
            if pd.notna(date_min) and pd.notna(date_max):
                date_range = f"{date_min:%Y.%m.%d} ~ {date_max:%Y.%m.%d}"
        return {
            "rows": len(df),
            "date_range": date_range,
            "media_count": df['매체명'].nunique() if '매체명' in df.columns else 0,
            "report_count": len(reports),
        }
    except Exception:
        return {**empty, "rows": len(df)}
