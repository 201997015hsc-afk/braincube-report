"""
Firebase Firestore 연동 모듈
────────────────────────────
campaigns 컬렉션에서 데이터를 읽어 DataFrame 형식으로 변환.
모든 벤치마크 데이터는 Firebase Firestore에서 실시간 로딩.

⚠ 인증 우선순위:
  1. Streamlit secrets (st.secrets["firebase"]) — 클라우드 배포용
  2. 로컬 JSON 파일 (dashboard-*-firebase-adminsdk-*.json) — 로컬 개발용
  3. 환경변수 GOOGLE_APPLICATION_CREDENTIALS — 기타 배포 환경
"""
import os
import glob
import streamlit as st
import pandas as pd
import numpy as np

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_COLLECTION = "campaigns"


def _find_local_credentials() -> str | None:
    """로컬 Firebase Admin SDK JSON 파일 자동 탐색."""
    # firebase-adminsdk 패턴의 JSON 파일 찾기
    patterns = [
        os.path.join(_BASE_DIR, "*firebase-adminsdk*.json"),
        os.path.join(_BASE_DIR, "*firebase_adminsdk*.json"),
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None


_KEY_PATH = _find_local_credentials()


def _load_credentials_dict() -> dict | None:
    """Streamlit secrets에서 Firebase 자격증명 로드 (클라우드 배포용)."""
    try:
        if hasattr(st, 'secrets') and 'firebase' in st.secrets:
            # Streamlit secrets는 AttrDict이므로 일반 dict로 변환
            return dict(st.secrets['firebase'])
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# Firebase 싱글턴 초기화
# ──────────────────────────────────────────────

def _get_firestore_client():
    """firebase_admin 앱을 한 번만 초기화하고 Firestore 클라이언트 반환.

    우선순위:
      1. Streamlit secrets (st.secrets["firebase"])
      2. 로컬 JSON 파일
    """
    import firebase_admin
    from firebase_admin import credentials, firestore

    if not firebase_admin._apps:
        # 1. Streamlit secrets 우선
        cred_dict = _load_credentials_dict()
        if cred_dict:
            cred = credentials.Certificate(cred_dict)
        # 2. 로컬 파일 fallback
        elif _KEY_PATH and os.path.exists(_KEY_PATH):
            cred = credentials.Certificate(_KEY_PATH)
        else:
            raise FileNotFoundError(
                "Firebase 자격증명을 찾을 수 없습니다. "
                "로컬: firebase-adminsdk JSON 파일을 프로젝트 루트에 두거나, "
                "Streamlit Cloud: secrets.toml에 [firebase] 섹션 추가 필요."
            )
        firebase_admin.initialize_app(cred)
    return firestore.client()


# ──────────────────────────────────────────────
# Firestore → DataFrame 변환
# ──────────────────────────────────────────────

_FIELD_MAP = {
    # Firebase field → DataFrame column
    'date':     '일자',
    'cat':      '분야',
    'adv':      '광고주',
    'content':  '대행사',     # 실제로는 브랜드/서비스명
    'media':    '매체',
    'product':  '광고상품',
    'actual':   '발송건',
    'clicks':   '클릭수',
    'db':       'DB',
    'sellUnit': '단가',
    'dept':     '부서명',
    'sales':    '영업담당',
    'ops':      '운영담당',
    'id':       '캠페인ID',
    'target':   '타겟',
    'status':   '상태',
    'sent':     '발송완료',
    'qty':      '요청수량',
    'buyUnit':  '매입단가',
    'comm':     '수수료율',
    'agrate':   '대행%',
    'disc':     '할인',
    'svc':      '서비스',
    'ctr':      '클릭율_원본',
    'dbr':      'DB율',
    'msg':      '메시지',
    'promo':    '프로모',
    'note':     '비고',
    'seller':   '셀러',
}


def _docs_to_dataframe(docs: list) -> pd.DataFrame:
    """Firestore 문서 리스트 → 정규화된 DataFrame"""
    rows = []
    for doc in docs:
        d = doc.to_dict()
        row = {}
        for fb_key, col_name in _FIELD_MAP.items():
            row[col_name] = d.get(fb_key)
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # ── 숫자 정리 ──
    for col in ['발송건', '요청수량', '단가', '매입단가', '수수료율', '대행%', '할인', '서비스']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    # 클릭수: None → NaN (트래킹 미설정)
    if '클릭수' in df.columns:
        df['클릭수'] = pd.to_numeric(df['클릭수'], errors='coerce')

    # DB: None → NaN
    if 'DB' in df.columns:
        df['DB'] = pd.to_numeric(df['DB'], errors='coerce')

    # ── 광고비 계산: actual × sellUnit ──
    df['광고비'] = df['발송건'] * df['단가']
    df['금액'] = df['요청수량'] * df['단가']

    # ── 날짜 파싱 ("2026-02-04 11:00" → datetime) ──
    if '일자' in df.columns:
        df['_date'] = pd.to_datetime(df['일자'], errors='coerce')
        # 년월 파생 (Windows 호환: %-m 대신 수동 조합)
        df['년월'] = (
            df['_date'].dt.year.astype(str) + '년' +
            df['_date'].dt.month.astype(str) + '월'
        ).where(df['_date'].notna(), '')
        # 주차
        df['주차'] = (
            df['_date'].dt.strftime('%Y년 %m월 ') +
            ((df['_date'].dt.day - 1) // 7 + 1).astype(str) + '주차'
        ).where(df['_date'].notna(), '')

    # ── _has_click 플래그 (benchmark 호환) ──
    df['_has_click'] = df['클릭수'].notna() & (df['클릭수'] > 0)

    # ── _브랜드 (benchmark 호환: 대행사 = 브랜드/서비스명) ──
    df['_브랜드'] = df['대행사'].fillna(df['광고주'].fillna(''))

    # ── CTR / CPC / CPM 파생 ──
    sends = df['발송건'].fillna(0)
    clicks = df['클릭수'].fillna(0)
    cost = df['광고비'].fillna(0)

    df['_CTR'] = np.where(
        df['_has_click'] & (sends > 0),
        clicks / sends * 100,
        np.nan,
    )
    df['_CPC'] = np.where(
        df['_has_click'] & (clicks > 0),
        cost / clicks,
        np.nan,
    )
    df['_CPM'] = np.where(sends > 0, cost / sends * 1000, 0)

    # 클릭율 숫자 (원본에서 가져오거나 계산)
    df['클릭율_num'] = np.where(
        df['_has_click'] & (sends > 0),
        clicks / sends * 100,
        0,
    )

    return df


# ──────────────────────────────────────────────
# 캐시된 데이터 로더 (Public API)
# ──────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner="Firebase에서 데이터 로딩 중...")
def load_from_firestore() -> pd.DataFrame | None:
    """Firestore campaigns 컬렉션 전체 → DataFrame (5분 캐시)

    인증 우선순위:
      1. Streamlit secrets (클라우드 배포)
      2. 로컬 JSON 파일 (로컬 개발)
    둘 다 없으면 None.
    """
    # 자격증명 확보 가능 여부 확인 (로컬 파일 OR Streamlit secrets)
    has_local_key = _KEY_PATH is not None and os.path.exists(_KEY_PATH)
    has_secrets = _load_credentials_dict() is not None
    if not has_local_key and not has_secrets:
        return None
    try:
        db = _get_firestore_client()
        docs = list(db.collection(_COLLECTION).stream())
        if not docs:
            return None
        df = _docs_to_dataframe(docs)
        return df if not df.empty else None
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None


def load_benchmark() -> pd.DataFrame | None:
    """Firebase Firestore 벤치마크 데이터 로더."""
    return load_from_firestore()


# ──────────────────────────────────────────────
# 광고주별 데이터 조회 (클라이언트 리포트용)
# ──────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def list_advertisers() -> list[dict]:
    """
    Firebase에 등록된 전체 브랜드(실 광고주) 목록 반환 (5분 캐시).
    ⚠ 직접계약과 대대행을 모두 포함 — `_브랜드` 컬럼 기준 (대행사 필드가 실제 브랜드명).
    Returns: [{name, industry, campaign_count, last_date}, ...]
    """
    df = load_from_firestore()
    if df is None or df.empty:
        return []

    brands = []
    for brand_name, group in df.groupby('_브랜드'):
        # 빈 문자열 / NaN 제외
        if pd.isna(brand_name) or not str(brand_name).strip():
            continue
        # 가장 자주 등장하는 분야 선택 (브랜드가 여러 분야 걸쳐있을 때)
        industry_counts = group['분야'].dropna().value_counts()
        main_industry = industry_counts.index[0] if len(industry_counts) > 0 else ''
        last_date = group['_date'].max() if '_date' in group.columns else None
        brands.append({
            'name': str(brand_name),
            'industry': main_industry,
            'campaign_count': len(group),
            'last_date': last_date.strftime('%Y-%m-%d') if last_date and pd.notna(last_date) else '',
        })

    # 캠페인 수 많은 순 → 이름순
    brands.sort(key=lambda x: (-x['campaign_count'], x['name']))
    return brands


@st.cache_data(ttl=300, show_spinner="광고주 데이터 로딩 중...")
def load_advertiser_data(advertiser_name: str) -> pd.DataFrame | None:
    """
    특정 브랜드(실 광고주)의 전체 데이터 반환 (5분 캐시).
    ⚠ `_브랜드` 컬럼 기준 — 직접계약(`광고주=브랜드`)과 대대행(`대행사=브랜드`) 모두 포함.
    표준 분석 컬럼(날짜/매체명/집행금액/발송량/클릭수) 포함.
    """
    df = load_from_firestore()
    if df is None or df.empty:
        return None

    # 브랜드 기준 필터 (직접 + 대대행 모두 통합)
    adv_df = df[df['_브랜드'] == advertiser_name].copy()
    if adv_df.empty:
        return None

    # Bug 4 fix: '금액' 컬럼 제거 (COL_MAPPING['집행금액']에 '금액' alias 있어 충돌)
    if '금액' in adv_df.columns:
        adv_df = adv_df.drop(columns=['금액'])

    # 표준 분석 컬럼으로 매핑 (lms_app의 REQUIRED_COLS 맞춤)
    # ⚠ rename으로 처리하여 중복 컬럼 방지 (_normalize_columns와 충돌 방지)
    adv_df['날짜'] = adv_df['_date']
    _rename_map = {}
    if '매체' in adv_df.columns:
        _rename_map['매체'] = '매체명'
    if '광고비' in adv_df.columns:
        _rename_map['광고비'] = '집행금액'
    if '발송건' in adv_df.columns:
        _rename_map['발송건'] = '발송량'
    if _rename_map:
        adv_df = adv_df.rename(columns=_rename_map)

    # 혹시 남아있는 '일자' 컬럼 제거 (날짜와 중복 방지)
    if '일자' in adv_df.columns and '날짜' in adv_df.columns:
        adv_df = adv_df.drop(columns=['일자'])

    # '년월' 제거 — data_processing 표준 형식 ("YYYY년 MM월")으로 재생성되도록
    for c in ('년월', '주차'):
        if c in adv_df.columns:
            adv_df = adv_df.drop(columns=[c])

    # 결측치 제거 (날짜/매체 없는 행)
    adv_df = adv_df.dropna(subset=['날짜', '매체명'])
    # 발송량 0 제거
    adv_df = adv_df[adv_df['발송량'] > 0].copy()

    return adv_df if not adv_df.empty else None


# ──────────────────────────────────────────────
# 벤치마크 분석 헬퍼 (타 모듈 활용)
# ──────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def get_benchmark_stats(industry: str = None) -> dict | None:
    """업종별 벤치마크 통계 요약 반환

    Returns: dict(avg_ctr, med_ctr, avg_cpc, med_cpc,
                  ctr_values, cpc_values, total_campaigns, industries)
    """
    df = load_from_firestore()
    if df is None or df.empty:
        return None
    if industry and industry != "전체":
        df = df[df['분야'] == industry]
    if df.empty:
        return None

    has_click = df[df['_has_click']].copy()
    ctr_vals = has_click['_CTR'].dropna()
    cpc_vals = has_click['_CPC'].dropna()

    return {
        'avg_ctr': float(ctr_vals.mean()) if len(ctr_vals) > 0 else 0,
        'med_ctr': float(ctr_vals.median()) if len(ctr_vals) > 0 else 0,
        'avg_cpc': float(cpc_vals.mean()) if len(cpc_vals) > 0 else 0,
        'med_cpc': float(cpc_vals.median()) if len(cpc_vals) > 0 else 0,
        'total_campaigns': len(df),
        'click_campaigns': len(has_click),
        'ctr_values': ctr_vals.tolist(),
        'cpc_values': cpc_vals.tolist(),
        'industries': sorted(df['분야'].dropna().unique().tolist()),
    }


def calc_percentile(value: float, distribution: list) -> float:
    """값의 백분위 (높을수록 좋음 — CTR 등). 상위 %로 반환."""
    if not distribution or value is None:
        return 50.0
    count_below = sum(1 for v in distribution if v <= value)
    return round((count_below / len(distribution)) * 100, 1)


def calc_percentile_lower(value: float, distribution: list) -> float:
    """값의 백분위 (낮을수록 좋음 — CPC 등). 상위 %로 반환."""
    if not distribution or value is None:
        return 50.0
    count_above = sum(1 for v in distribution if v >= value)
    return round((count_above / len(distribution)) * 100, 1)


def percentile_grade(pct: float) -> tuple[str, str, str]:
    """백분위 → (등급텍스트, 배경색, 글자색)"""
    if pct >= 80:
        return (f"상위 {100 - pct:.0f}%", "#E8F5E9", "#2E7D32")
    elif pct >= 60:
        return (f"상위 {100 - pct:.0f}%", "#E3F2FD", "#1565C0")
    elif pct >= 40:
        return ("중위권", "#FFF8E1", "#F57F17")
    else:
        return (f"하위 {100 - pct:.0f}%", "#FFF3F0", "#E53935")


def percentile_letter(pct: float) -> str:
    """백분위 → A/B/C/D 등급"""
    if pct >= 80:
        return "A"
    elif pct >= 60:
        return "B"
    elif pct >= 40:
        return "C"
    else:
        return "D"


@st.cache_data(ttl=300, show_spinner=False)
def get_benchmark_monthly(industry: str = None) -> pd.DataFrame | None:
    """업종 월별 벤치마크 트렌드 (CTR, CPC).

    ⚠ 년월 포맷 통일: "2026년 03월" (zero-padded, with space)
    — data_processing._cast_and_enrich와 동일한 형식이라
    자사 데이터와 X축 정렬 가능.
    """
    df = load_from_firestore()
    if df is None or df.empty:
        return None
    if industry and industry != "전체":
        df = df[df['분야'] == industry]

    has_click = df[df['_has_click']].copy()
    if has_click.empty or '_date' not in has_click.columns:
        return None

    # ⚠ 원본 '년월' 대신 '_date'에서 재생성 (표준 포맷으로 강제)
    has_click = has_click.dropna(subset=['_date']).copy()
    if has_click.empty:
        return None
    has_click['년월'] = has_click['_date'].dt.strftime('%Y년 %m월')

    monthly = has_click.groupby('년월').agg(
        발송건=('발송건', 'sum'),
        클릭수=('클릭수', 'sum'),
        광고비=('광고비', 'sum'),
    ).reset_index()
    monthly['CTR'] = np.where(
        monthly['발송건'] > 0, monthly['클릭수'] / monthly['발송건'] * 100, 0,
    )
    monthly['CPC'] = np.where(
        monthly['클릭수'] > 0, monthly['광고비'] / monthly['클릭수'], 0,
    )
    return monthly.sort_values('년월').reset_index(drop=True)


@st.cache_data(ttl=300, show_spinner=False)
def get_benchmark_weekday(industry: str = None) -> pd.DataFrame | None:
    """업종 요일별 벤치마크 (CTR 기준)"""
    df = load_from_firestore()
    if df is None or df.empty:
        return None
    if industry and industry != "전체":
        df = df[df['분야'] == industry]

    has_click = df[df['_has_click']].copy()
    if has_click.empty or '_date' not in has_click.columns:
        return None

    has_click['요일번호'] = has_click['_date'].dt.dayofweek
    _dow = {0: '월요일', 1: '화요일', 2: '수요일', 3: '목요일',
            4: '금요일', 5: '토요일', 6: '일요일'}
    has_click['요일'] = has_click['요일번호'].map(_dow)

    wd = has_click.groupby(['요일번호', '요일']).agg(
        발송건=('발송건', 'sum'), 클릭수=('클릭수', 'sum'),
    ).reset_index().sort_values('요일번호')
    wd['CTR'] = np.where(wd['발송건'] > 0, wd['클릭수'] / wd['발송건'] * 100, 0)
    return wd


def is_firebase_available() -> bool:
    """Firebase 자격증명이 있는지 빠르게 확인.

    로컬 JSON 파일 또는 Streamlit secrets 둘 중 하나라도 있으면 True.
    (실제 Firestore 접속 시도는 하지 않음 — 빠른 가용성 체크)
    """
    has_local_key = _KEY_PATH is not None and os.path.exists(_KEY_PATH)
    has_secrets = _load_credentials_dict() is not None
    return has_local_key or has_secrets


def get_data_source() -> str:
    """현재 데이터 소스 확인 (UI 표시용)

    로컬 JSON 또는 Streamlit secrets 둘 중 하나라도 있으면 Firebase 사용 가능.
    """
    if not is_firebase_available():
        return "연결 안됨"
    try:
        fb = load_from_firestore()
        return "🔥 Firebase" if fb is not None else "연결 안됨"
    except Exception:
        return "연결 안됨"


# ──────────────────────────────────────────────
# 🔒 내부 전용: 매체별 표준 단가 조회 (예측/시뮬레이션 계산용)
# ──────────────────────────────────────────────
# ⚠ 반환값은 UI에 직접 노출하지 말 것 — 클릭수/발송량 등 파생 결과만 표시.

@st.cache_data(ttl=600, show_spinner=False)
def _build_price_table() -> pd.DataFrame | None:
    """매체 × 광고상품 × 업종별 판매단가 중앙값 테이블 (10분 캐시)."""
    df = load_from_firestore()
    if df is None or df.empty or '단가' not in df.columns:
        return None
    df = df[df['단가'].notna() & (df['단가'] > 0)].copy()
    if df.empty:
        return None

    # 매체 × 광고상품 × 업종별 중앙값 (outlier에 강건)
    group_cols = []
    for c in ('매체', '광고상품', '분야'):
        if c in df.columns:
            group_cols.append(c)
    if not group_cols:
        return None

    table = df.groupby(group_cols, dropna=False).agg(
        단가_중앙값=('단가', 'median'),
        단가_평균=('단가', 'mean'),
        캠페인수=('단가', 'size'),
    ).reset_index()
    return table


def get_media_price(media: str, product: str = 'LMS',
                    industry: str = None) -> float | None:
    """
    매체의 표준 판매단가(원/발송건) 반환.

    ⚠ 내부 계산 전용 — 이 함수의 반환값을 직접 UI에 표시하지 마세요.
    예측/시뮬레이션의 "발송 가능 수량" 계산에만 사용.

    검색 우선순위:
    1. (매체, 광고상품, 업종) 정확 매치
    2. (매체, 광고상품) 매치 (업종 무관)
    3. (매체, 광고상품 무관) 매치
    4. (광고상품) 전체 업계 평균
    5. None (단가 추정 불가)
    """
    table = _build_price_table()
    if table is None or table.empty:
        return None

    def _lookup(filters: dict) -> float | None:
        sub = table.copy()
        for col, val in filters.items():
            if val is None or col not in sub.columns:
                continue
            sub = sub[sub[col] == val]
        # 신뢰도: 최소 2건 이상이어야 중앙값 채택
        sub = sub[sub['캠페인수'] >= 2]
        if sub.empty:
            return None
        # 캠페인 수 가중 중앙값 (캠페인 많은 세그먼트 우선)
        sub = sub.sort_values('캠페인수', ascending=False)
        return float(sub['단가_중앙값'].iloc[0])

    # 1. 정확 매치
    price = _lookup({'매체': media, '광고상품': product, '분야': industry})
    if price:
        return price
    # 2. 매체 + 상품
    price = _lookup({'매체': media, '광고상품': product})
    if price:
        return price
    # 3. 매체만
    price = _lookup({'매체': media})
    if price:
        return price
    # 4. 상품 전체 평균
    price = _lookup({'광고상품': product})
    if price:
        return price
    return None


def estimate_sends_from_budget(media: str, budget: float,
                                product: str = 'LMS',
                                industry: str = None) -> int | None:
    """예산으로 발송 가능한 건수 추정 — 단가 기반.

    ⚠ 내부 계산 전용.
    Returns: 예상 발송량 (int) or None (단가 조회 불가 시)
    """
    if budget is None or budget <= 0:
        return 0
    price = get_media_price(media, product=product, industry=industry)
    if price is None or price <= 0:
        return None
    return int(budget / price)
