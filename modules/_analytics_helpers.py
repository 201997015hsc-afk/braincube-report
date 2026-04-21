"""
공용 분석 헬퍼 (_analytics_helpers.py)
───────────────────────────────────────
여러 모듈에서 중복 사용되는 계산·검증·보정 로직을 한 곳에 모음.

- 컬럼명 자동 감지 (발송량/발송건, 매체/매체명)
- 가중 CTR 계산 (클릭 / 발송 × 100)
- 표본 신뢰도 검증 (최소 캠페인 수 + 최소 발송량)
- 매체별 CPS 시장 표준 단가 보정
"""
import numpy as np
import pandas as pd


# ──────────────────────────────────────────────
# 컬럼 네이밍 호환 헬퍼
# ──────────────────────────────────────────────

def get_sends_col(df: pd.DataFrame) -> str:
    """발송량 컬럼명 자동 감지.

    Firebase 벤치마크 데이터는 '발송건', 표준 분석 데이터는 '발송량'을 사용.
    둘 다 없으면 '발송량' 기본값 반환 (KeyError는 호출자 책임).
    """
    if '발송량' in df.columns:
        return '발송량'
    if '발송건' in df.columns:
        return '발송건'
    return '발송량'


def get_media_col(df: pd.DataFrame) -> str:
    """매체 컬럼명 자동 감지 (매체명/매체)."""
    if '매체명' in df.columns:
        return '매체명'
    if '매체' in df.columns:
        return '매체'
    return '매체명'


def get_cost_col(df: pd.DataFrame) -> str:
    """집행금액 컬럼명 자동 감지 (집행금액/광고비)."""
    if '집행금액' in df.columns:
        return '집행금액'
    if '광고비' in df.columns:
        return '광고비'
    return '집행금액'


# ──────────────────────────────────────────────
# 가중 CTR 계산
# ──────────────────────────────────────────────

def weighted_ctr(df: pd.DataFrame,
                 sends_col: str | None = None,
                 clicks_col: str = '클릭수') -> float:
    """발송량 가중 평균 CTR(%) 계산.

    단순 평균 대신 "총 클릭 / 총 발송 × 100" 을 사용해 대량 캠페인이
    소량 캠페인에 희석되지 않도록 함.

    Parameters
    ----------
    df : pd.DataFrame
    sends_col : str, optional
        발송량 컬럼. None이면 자동 감지.
    clicks_col : str
        클릭수 컬럼명.

    Returns
    -------
    float — CTR(%). 데이터가 없거나 발송량이 0이면 0.0.
    """
    if df is None or df.empty:
        return 0.0
    col = sends_col or get_sends_col(df)
    if col not in df.columns or clicks_col not in df.columns:
        return 0.0
    s = df[col].fillna(0).sum()
    c = df[clicks_col].fillna(0).sum()
    return float(c / s * 100) if s > 0 else 0.0


# ──────────────────────────────────────────────
# 표본 신뢰도 검증
# ──────────────────────────────────────────────

def is_reliable_sample(df: pd.DataFrame, min_samples: int = 8,
                       min_sends: int = 0,
                       sends_col: str | None = None) -> bool:
    """주어진 서브셋이 인사이트 도출에 충분한지 검증.

    Parameters
    ----------
    df : pd.DataFrame
    min_samples : int
        최소 행(캠페인/문구) 수.
    min_sends : int
        최소 총 발송량. 0이면 발송량 검증 스킵.
    sends_col : str, optional
        발송량 컬럼. None이면 자동 감지.
    """
    if df is None or df.empty or len(df) < min_samples:
        return False
    if min_sends > 0:
        col = sends_col or get_sends_col(df)
        if col not in df.columns:
            return False
        if float(df[col].fillna(0).sum()) < min_sends:
            return False
    return True


# ──────────────────────────────────────────────
# 매체별 CPS 시장 표준 단가 보정
# ──────────────────────────────────────────────

def apply_market_price_correction(stats: pd.DataFrame,
                                  media_col: str = '매체명',
                                  cps_col: str = 'CPS',
                                  out_col: str = 'CPS_보정',
                                  product: str = 'LMS',
                                  tolerance: float = 0.5) -> pd.DataFrame:
    """자사 CPS가 시장 표준 단가에서 크게 벗어나면 시장 단가로 치환.

    ⚠ 내부 계산 전용. 보정된 CPS는 UI에 직접 수치 노출 금지.

    Parameters
    ----------
    stats : pd.DataFrame
        매체별 집계 DataFrame.
    media_col : str
        매체명 컬럼.
    cps_col : str
        자사 CPS 컬럼.
    out_col : str
        보정된 CPS를 저장할 컬럼명.
    product : str
        광고상품 분류 (예: 'LMS').
    tolerance : float
        시장 단가 대비 ±tolerance 범위 밖이면 치환. 기본 0.5 (±50%).

    Returns
    -------
    pd.DataFrame — out_col이 추가된 stats.
    """
    if stats is None or stats.empty:
        return stats
    if cps_col not in stats.columns or media_col not in stats.columns:
        return stats

    try:
        from modules.firebase_connector import get_media_price
    except Exception:
        stats[out_col] = stats[cps_col]
        return stats

    lower = 1.0 - tolerance
    upper = 1.0 + tolerance

    def _robust_cps(row) -> float:
        own = float(row[cps_col]) if pd.notna(row[cps_col]) else 0.0
        try:
            market = get_media_price(row[media_col], product=product)
        except Exception:
            market = None
        if market is None or market <= 0:
            return own
        if own <= 0 or own < market * lower or own > market * upper:
            return float(market)
        return own

    stats = stats.copy()
    stats[out_col] = stats.apply(_robust_cps, axis=1)
    return stats


def count_adjusted_rows(stats: pd.DataFrame,
                        cps_col: str = 'CPS',
                        out_col: str = 'CPS_보정') -> int:
    """apply_market_price_correction 적용 후 보정된 행 수 반환."""
    if stats is None or stats.empty:
        return 0
    if cps_col not in stats.columns or out_col not in stats.columns:
        return 0
    return int((stats[cps_col].round(2) != stats[out_col].round(2)).sum())
