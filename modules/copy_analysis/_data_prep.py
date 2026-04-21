"""
데이터 준비: Firebase 벤치마크 → 메시지 분석용 서브셋 · 메시지별 집계.
"""
import pandas as pd
import numpy as np


# ──────────────────────────────────────────────
# 데이터 준비
# ──────────────────────────────────────────────

def _prepare_msg_data(bench: pd.DataFrame, industry: str = None,
                      advertiser: str = None) -> pd.DataFrame:
    """Firebase 벤치마크 데이터에서 메시지 분석용 서브셋 추출.

    - 메시지 비어있지 않은 캠페인만
    - 클릭 트래킹이 있는 캠페인만 (CTR 계산 가능)
    """
    df = bench.copy()

    # 업종 필터
    if industry and industry != '전체':
        df = df[df['분야'] == industry]

    # 광고주 필터
    if advertiser:
        df = df[df['광고주'] == advertiser]

    # 메시지 존재 + 클릭 트래킹
    df = df[df['메시지'].notna() & (df['메시지'].astype(str).str.strip() != '')]
    df = df[df['_has_click'] & (df['발송건'] > 0)]

    if df.empty:
        return pd.DataFrame()

    # 파생 컬럼
    df['CTR'] = df['클릭수'] / df['발송건'] * 100
    df['CPC'] = np.where(df['클릭수'] > 0, df['광고비'] / df['클릭수'], 0)
    df['문구길이'] = df['메시지'].astype(str).str.len()

    return df


def _group_by_message(df: pd.DataFrame, min_sends: int = 100) -> pd.DataFrame:
    """메시지 텍스트별 성과 집계.

    동일 문구로 여러 캠페인 진행 시 합산.
    min_sends 이상 발송된 문구만 통계적으로 유의.
    브랜드 정보 보존 (익명화 로직에서 활용).
    """
    if df.empty:
        return pd.DataFrame()

    # 메시지별 브랜드 정보 (가장 많이 사용한 브랜드 기록)
    brand_col = '_브랜드' if '_브랜드' in df.columns else '광고주'
    brand_map = {}
    for msg, sub in df.groupby('메시지'):
        if brand_col in sub.columns:
            top_brand = sub[brand_col].dropna().value_counts()
            brand_map[msg] = top_brand.index[0] if len(top_brand) > 0 else ''
        else:
            brand_map[msg] = ''

    grp = df.groupby('메시지', as_index=False).agg(
        캠페인수=('메시지', 'size'),
        발송건=('발송건', 'sum'),
        클릭수=('클릭수', 'sum'),
        광고비=('광고비', 'sum'),
    )
    grp = grp[grp['발송건'] >= min_sends]
    grp['CTR'] = np.where(grp['발송건'] > 0, grp['클릭수'] / grp['발송건'] * 100, 0)
    grp['CPC'] = np.where(grp['클릭수'] > 0, grp['광고비'] / grp['클릭수'], 0)
    grp['문구길이'] = grp['메시지'].str.len()
    grp['_브랜드'] = grp['메시지'].map(brand_map).fillna('')
    grp = grp.sort_values('CTR', ascending=False).reset_index(drop=True)
    return grp
