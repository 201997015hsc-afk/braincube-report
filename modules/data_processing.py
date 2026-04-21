"""
데이터 로딩, 전처리, 공통 집계 헬퍼 모듈
- @st.cache_data 로 파일 파싱 결과를 캐싱
- CTR, 주차, 집계 등 반복 로직을 헬퍼로 통합
"""
import streamlit as st
import pandas as pd
import numpy as np

from modules.config import COL_MAPPING, REQUIRED_COLS, TARGET_METRICS, WEEKDAY_MAP


# ──────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────

def calc_ctr(df: pd.DataFrame) -> pd.Series:
    """DataFrame 컬럼 기반 벡터 CTR 계산"""
    return np.where(df['발송량'] > 0, (df['클릭수'] / df['발송량']) * 100, 0)


def calc_ctr_scalar(clicks: float, sends: float) -> float:
    """스칼라 CTR 계산 (단일 값)"""
    return (clicks / sends * 100) if sends > 0 else 0.0


def aggregate_by_weekday(df: pd.DataFrame) -> pd.DataFrame:
    """요일별 집계 + CTR (7개 모듈에서 반복되던 패턴)"""
    return aggregate_metrics(df, ['짧은_요일'])


def media_month_stats(df: pd.DataFrame, year_month: str) -> pd.DataFrame:
    """특정 월의 매체별 집계 + CTR"""
    m = df[df['년월'] == year_month].groupby('매체명').agg(
        {k: 'sum' for k in TARGET_METRICS}
    ).reset_index()
    m['CTR'] = calc_ctr(m)
    return m


def format_metric(metric: str, value: float) -> str:
    """지표명에 맞는 단위 포맷 (anomaly 등에서 반복되던 패턴)"""
    _FMT = {'집행금액': '{:,.0f}원', '발송량': '{:,.0f}건', '클릭수': '{:,.0f}회'}
    return _FMT.get(metric, '{:,.0f}').format(value)


def add_week_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out['월내_주차'] = ((out['날짜'].dt.day - 1) // 7) + 1
    out['주차'] = out['날짜'].dt.strftime('%Y년 %m월 ') + out['월내_주차'].astype(str) + "주차"
    return out


def aggregate_metrics(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    agg = (
        df.groupby(group_cols, sort=False)
        .agg({m: 'sum' for m in TARGET_METRICS})
        .reset_index()
        .sort_values(group_cols)
    )
    agg['CTR'] = calc_ctr(agg)
    return agg


def filter_by_date_range(df: pd.DataFrame, start, end) -> pd.DataFrame:
    """날짜 범위로 DataFrame 필터링"""
    mask = (df['날짜'].dt.date >= start) & (df['날짜'].dt.date <= end)
    return df[mask].copy()


# ──────────────────────────────────────────────
# 기준 기간 유틸 (여러 모듈에서 공용)
# ──────────────────────────────────────────────

REF_RECENT_1 = "최근 1개월"
REF_RECENT_3 = "최근 3개월 평균"
REF_ALL      = "전체 기간 평균"


def ref_period_options(df: pd.DataFrame) -> list[tuple[str, str]]:
    """선택 가능한 기준 기간 [(option_label, period_display), ...]"""
    months = sorted(df['년월'].unique())
    opts = []
    if len(months) >= 1:
        opts.append((REF_RECENT_1, months[-1]))
    if len(months) >= 3:
        opts.append((REF_RECENT_3, f"{months[-3]} ~ {months[-1]}"))
    if len(months) >= 2:
        opts.append((REF_ALL, f"{months[0]} ~ {months[-1]}"))
    return opts


def filter_ref_period(df: pd.DataFrame, ref_mode: str) -> pd.DataFrame:
    """기준 기간에 맞게 DataFrame 필터링"""
    months = sorted(df['년월'].unique())
    if ref_mode == REF_RECENT_1:
        return df[df['년월'] == months[-1]]
    elif ref_mode == REF_RECENT_3:
        return df[df['년월'].isin(months[-3:])]
    return df


def render_ref_period_selector(df: pd.DataFrame, key: str) -> tuple[pd.DataFrame, str]:
    """기준 기간 셀렉터 UI 렌더 → (필터된 df, 기간 라벨) 반환"""
    opts = ref_period_options(df)
    if not opts:
        return df, ""
    labels = [o for o, _ in opts]
    period_map = {o: p for o, p in opts}

    c1, c2 = st.columns([1, 3])
    with c1:
        mode = st.selectbox("📅 기준 기간", labels, index=0, key=f"ref_{key}")
    with c2:
        period = period_map[mode]
        st.markdown(f'<div style="padding:10px 0 0 0; font-size:0.85rem; color:#4E5968;">기준: <b style="color:#F7931D">{period}</b> 데이터 기반으로 분석합니다.</div>', unsafe_allow_html=True)

    return filter_ref_period(df, mode), period


# ──────────────────────────────────────────────
# 발송량 기반 신뢰도 (전 모듈 공통)
# ──────────────────────────────────────────────
MIN_RELIABLE_SENDS = 5_000      # 이 미만 → 분석 신뢰도 부족
FULL_RELIABLE_SENDS = 30_000    # 이 이상 → 완전 신뢰

_RELIABILITY_TIERS = [
    (FULL_RELIABLE_SENDS, "신뢰"),
    (MIN_RELIABLE_SENDS, "참고"),
    (0, "부족"),
]


def send_reliability(sends: float) -> str:
    """발송량 기반 데이터 신뢰도 라벨"""
    for threshold, label in _RELIABILITY_TIERS:
        if sends >= threshold:
            return label
    return "부족"


def reliability_weight(sends: float) -> float:
    """
    발송량 기반 가중치 (0.0 ~ 1.0).
    - 0건 → 0.0
    - 5,000건 → 약 0.17
    - 30,000건 이상 → 1.0
    효율 점수 등에 곱해서 소량 데이터의 영향력을 줄임.
    """
    if sends <= 0:
        return 0.0
    return min(sends / FULL_RELIABLE_SENDS, 1.0)


def is_reliable(sends: float) -> bool:
    """분석에 포함할 최소 발송량 충족 여부"""
    return sends >= MIN_RELIABLE_SENDS


# ──────────────────────────────────────────────
# 데이터 로딩 파이프라인
# ──────────────────────────────────────────────

@st.cache_data(show_spinner="데이터를 파싱하고 있습니다...")
def _parse_file(file_bytes: bytes, file_name: str, sheet_name: str | None) -> pd.DataFrame:
    from io import BytesIO
    buf = BytesIO(file_bytes)
    if file_name.endswith('.csv'):
        return pd.read_csv(buf)
    df = pd.read_excel(buf, sheet_name=sheet_name)
    for i in range(1, 3):
        if sum('Unnamed' in str(c) for c in df.columns) > len(df.columns) / 2:
            buf.seek(0)
            df = pd.read_excel(buf, sheet_name=sheet_name, header=i)
    return df


def get_sheet_names(uploaded_file) -> list[str]:
    """엑셀 파일의 시트 목록 반환. CSV면 빈 리스트."""
    if uploaded_file.name.endswith('.csv'):
        return []
    try:
        return pd.ExcelFile(uploaded_file).sheet_names
    except Exception:
        return []


def find_default_sheet(sheet_names: list[str]) -> str:
    return next(
        (s for s in sheet_names if any(k in s.upper() for k in ['결과', '리포트', 'RAW'])),
        sheet_names[0] if sheet_names else "",
    )


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().replace('\n', '') for c in df.columns]
    for target, candidates in COL_MAPPING.items():
        for cand in candidates:
            if cand in df.columns:
                df.rename(columns={cand: target}, inplace=True)
                break
    return df


def _merge_duplicate_metric_cols(df: pd.DataFrame) -> pd.DataFrame:
    for target in TARGET_METRICS:
        dup_cols = [c for c in df.columns if str(c).startswith(target)]
        if len(dup_cols) > 1:
            merged = pd.Series(0, index=df.index)
            for c in dup_cols:
                merged += pd.to_numeric(df[c].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
            df[target] = merged
    return df


def _cast_and_enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df['날짜'] = pd.to_datetime(df['날짜'], errors='coerce')
    df = df.dropna(subset=['날짜'])
    for c in TARGET_METRICS:
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
    if '매체명' not in df.columns:
        df['매체명'] = 'LMS 전체'
    df['년월'] = df['날짜'].dt.strftime('%Y년 %m월')
    df['일자'] = df['날짜'].dt.strftime('%Y년 %m월 %d일')
    df['요일번호'] = df['날짜'].dt.dayofweek
    df['요일'] = df['요일번호'].map(
        {0: '월요일', 1: '화요일', 2: '수요일', 3: '목요일', 4: '금요일', 5: '토요일', 6: '일요일'}
    )
    df['짧은_요일'] = df['요일번호'].map(WEEKDAY_MAP)
    return df


def ensure_derived_columns(df: pd.DataFrame) -> pd.DataFrame:
    """파생 컬럼(년월, 요일 등)이 없으면 재생성. 이미 있으면 패스."""
    if df.empty:
        return df
    need_enrich = any(c not in df.columns for c in ('년월', '짧은_요일', '요일번호'))
    if need_enrich and '날짜' in df.columns:
        df = _cast_and_enrich(df)
    return df


def load_and_preprocess(uploaded_file, sheet_name: str | None = None) -> tuple[pd.DataFrame | None, str | None]:
    """메인 데이터 로드 파이프라인. (df, display_sheet) 반환."""
    try:
        display_sheet = sheet_name if sheet_name else "CSV 데이터"
        file_bytes = uploaded_file.getvalue()
        df = _parse_file(file_bytes, uploaded_file.name, sheet_name)
    except Exception as e:
        st.error(f"파일을 읽는 중 오류가 발생했습니다: {e}")
        return None, None

    df = _normalize_columns(df)

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        st.error(f"필수 항목을 찾을 수 없습니다: {', '.join(missing)}")
        st.info(f"현재 시트({display_sheet})의 항목명: {', '.join(df.columns)}")
        return None, None

    df = _merge_duplicate_metric_cols(df)
    df = _cast_and_enrich(df)

    if df.empty:
        st.warning("유효한 데이터가 없습니다. 날짜 형식을 확인해 주세요.")
        return None, None

    return df, display_sheet
