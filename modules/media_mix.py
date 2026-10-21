"""
미디어 믹스 제안 (Media Mix Recommendation)
───────────────────────────────────────────
다음 달 어느 매체에 얼마를, 어떤 타겟으로 집행할지 제안한다.

제안 4유형:
  🔁 휴면 복귀  — 과거 집행했으나 최근 90일 미집행 (성과 있었던 매체 우선)
  🆕 신규 진입  — 동종업계 타사가 검증했으나 자사 미집행
  📈 증액       — 전월·전전월 성과가 좋았던 자사 매체
  ✨ 신규 매체  — 업종 내 최근 등장한 매체 (선점 기회)

각 제안에 포함:
  - 예상 CTR (자사 실적 우선, 없으면 동종업계 레퍼런스)
  - 추천 예산 (최근 집행 단가 × 권장 물량)
  - 추천 타겟팅 (자사 베스트 타겟 또는 동종업계 검증 타겟)
"""
import re

import streamlit as st
import pandas as pd
import numpy as np

from modules.config import (
    COLOR_TEXT, COLOR_TEXT_SEC, COLOR_TEXT_TER, COLOR_BORDER, COLOR_BORDER_SUBTLE,
    COLOR_CARD, COLOR_BG, COLOR_SUCCESS, COLOR_DANGER, COLOR_WARNING, COLOR_BLUE,
    BRAND_PRIMARY, compact_num, MIN_SENDS_FOR_CTR_CLAIM,
    GOOD_CPA, REF_DB_FOR_CPA, PROMO_ONLY_REFERENCES,
    CPA_PROPOSE_MULT, PRODUCT_DEFAULT_QTY, MEDIA_VARIANT_SUFFIXES,
)
from modules.ui_helpers import render_page_header, render_empty_state, esc_html_safe
from modules.data_processing import is_db_driven as _shared_is_db_driven

_DORMANT_DAYS = 90      # 휴면 판정 기준
_NEW_MEDIA_DAYS = 60    # 업종 내 신규 매체 판정 기준
_REF_WINDOW_DAYS = 180  # 동종업계 레퍼런스 조회 기간
_DEFAULT_QTY = 50_000   # 기본 권장 발송량
# DB가 1건만 나온 매체도 CPA로 판정한다.
#   DB 1~2건은 "표본 부족"이 아니라 대부분 **실제로 효율이 나쁜 것**이다.
#   (실사례: 밀크T 출결선생 DB 2건/550만원 = CPA 275만원,
#            아이스크림홈런 하나카드 DB 2건/500만원 = CPA 250만원)
#   기존 기준(3건)에서는 이들이 CTR 판정으로 넘어가 증액 후보가 될 수 있었다.
_MIN_DB_FOR_JUDGE = 1

# 전환(DB)이 핵심 KPI인 광고주인지 판정하는 기준
_DB_DRIVEN_MIN_CAMPAIGNS = 10   # DB>0 캠페인 최소 건수
_DB_DRIVEN_MIN_RATIO = 0.20     # 전체 캠페인 대비 비율

# 예상 CPA 등급은 **업종 공통 기준 하나로** 전 브랜드에 동일 적용한다.
#   교육 = "500만원에 DB 30건" → 166,667원.
#   기준 이하 = 양호 / 1.5배 이내 = 보통 / 초과 = 부진(제안 제외).
#   브랜드별 자사 평균을 기준선으로 쓰면 CPA가 비싼 브랜드는 비싼 매체가
#   그대로 통과해 '잘 나왔다'의 정의가 브랜드마다 달라진다.
_CPA_POOR_MULT = CPA_PROPOSE_MULT

# 메시징 최소 유효 단가(원). 이보다 낮으면 단가 미입력·패키지 정산 등
# 데이터 공백으로 보고 예산·CPA 산출에 쓰지 않는다.
#   (실사례: 온리원 ASUM 패키지 단가 1원 → 예상 CPA 2,667원으로 1순위 추천됨.
#            교육 LMS 실단가는 5퍼센타일이 90원, 중앙값 100원)
_MIN_UNIT_PRICE = 10.0

# 실측 CPA로 믿을 최소선(원). 이보다 낮으면 광고비가 비어 있는 행으로 본다.
# (교육 성과형 최저 단가가 25,300원 — 1만원 미만 CPA는 데이터 공백이다)
_MIN_CREDIBLE_CPA = 10_000.0

# 유형별 제안 상한. 한 유형이 목록을 다 차지하면 믹스가 아니다.
#   (휴면 복귀만 9개 나오던 상태 → 실제로 돌릴 수 있는 조합이 아니다)
_TYPE_CAP = {'📈 증액': 4, '🔁 휴면 복귀': 4, '🆕 신규 진입': 5, '✨ 신규 매체': 2}

# 제안 총액을 최근 월 집행액의 이 배수 안으로 맞춘다. 넘는 제안은
# '추가 후보'로 내린다. 월 879만원을 쓰는 광고주에게 4,300만원짜리
# 믹스를 내밀면 제안서로 쓸 수 없다.
_BUDGET_FIT_MULT = 1.1

# 광고상품을 실제로 사는 단위 4개로 묶는다. 데이터에는 LMS·MMS·실시간 발송처럼
# 같은 채널이 다른 이름으로 들어와 있어서, 그대로 두면 매체×상품 전환율이
# 쪼개져 표본이 1건씩 흩어진다.
#   LMS   — 문자(LMS/MMS/실시간 발송)
#   카플친 — 카카오 플러스친구 메시지
#   앱푸시 — 매체 앱 푸시
#   DA    — 디스플레이 배너
#   성과형 — DB 건당 과금(CPA·CPS·퍼미션콜). 채널이 아니라 과금 방식이라
#            4개로 묶을 수 없다. 물량·단가 의미가 달라 따로 다룬다.
_PRODUCT_GROUPS = {
    'LMS': 'LMS', 'MMS': 'LMS', '실시간발송': 'LMS', 'SMS': 'LMS',
    '카톡MSG': '카플친', '카카오MSG': '카플친', '플친': '카플친',
    '카카오플러스친구': '카플친', '카톡': '카플친',
    'PUSH': '앱푸시', '앱푸시': '앱푸시', '푸시': '앱푸시',
    'DA': 'DA', 'IPTV': 'DA', '배너': 'DA',
    'CPA': '성과형', 'CPS': '성과형', '퍼미션콜': '성과형',
}
_PRODUCT_PERF = '성과형'


def _media_key(name) -> str:
    """같은 매체의 표기 변형을 하나로 묶는 키.

    'BC카드'와 'BC카드 CNCAD'는 같은 매체다(운영팀 확인). 따로 두면
    레퍼런스 표본이 쪼개지고, 같은 매체가 두 번 제안된다.
    """
    s = str(name or '').strip()
    low = s.upper()
    for suf in MEDIA_VARIANT_SUFFIXES:
        if low.endswith(suf.upper()):
            return s[: -len(suf)].strip()
    return s


def _promo_reason(media: str, brand: str = '') -> str:
    """프로모션 단가로 집행해 레퍼런스로 쓸 수 없는 매체면 사유를 돌려준다."""
    m = str(media or '').strip()
    if brand and f'{brand}|{m}' in PROMO_ONLY_REFERENCES:
        return PROMO_ONLY_REFERENCES[f'{brand}|{m}']
    return PROMO_ONLY_REFERENCES.get(m, '')


def _drop_promo(df: pd.DataFrame) -> pd.DataFrame:
    """프로모션 집행 행을 레퍼런스 모집단에서 제거."""
    if df is None or df.empty or '매체' not in df.columns:
        return df
    keys = set(PROMO_ONLY_REFERENCES)
    plain = {k for k in keys if '|' not in k}
    mask = ~df['매체'].astype(str).str.strip().isin(plain)
    if '_브랜드' in df.columns:
        pairs = {k for k in keys if '|' in k}
        if pairs:
            bm = (df['_브랜드'].astype(str).str.strip() + '|'
                  + df['매체'].astype(str).str.strip())
            mask &= ~bm.isin(pairs)
    return df[mask]


def _product_label(raw) -> str:
    """원본 광고상품명 → 구매 단위 라벨. 모르는 값은 원문을 그대로 쓴다."""
    s = str(raw or '').strip()
    if not s:
        return 'LMS'
    return _PRODUCT_GROUPS.get(s.upper().replace(' ', ''), s)


# 자녀 연령 스테이지 매칭과 고정 CPA 기준(500만원/30건)은 **교육 업종 전용**이다.
# 보험·수송 등은 KPI 정의와 전환 단가 수준이 완전히 달라 같은 기준을 쓰면 안 된다.
_STAGE_INDUSTRIES = {'교육'}


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
def _ctr(clicks, sends) -> float:
    return float(clicks) / float(sends) * 100 if sends else 0.0


# 학령 명시 키워드 — 연도 추론보다 우선한다
_RE_STAGE_ELEM = re.compile(r'초등|스쿨뱅킹\s*\(?\s*초|초등학교|초등지수|실내화|골드키즈')
_RE_STAGE_MID = re.compile(r'중학|스쿨뱅킹\s*\(?\s*중|교복|중등')
_RE_STAGE_HIGH = re.compile(r'고등|수능|모의고사|입시')
# "20-23년 유치원/어린이집 결제자" → 끝 연도로 자녀 현재 나이를 역산
_RE_KINDER_YEAR = re.compile(r'(\d{2})\s*[-~]\s*(\d{2})\s*년?\s*도?\s*[^\n]{0,8}?(유치원|어린이집)')
_RE_KINDER_PLAIN = re.compile(r'유치원|어린이집|키즈카페')
_RE_INFANT = re.compile(r'이유식|기저귀|분유|산후조리원|태교|임신|출산|신생아|국민행복카드')
_RE_BIRTH_YEAR = re.compile(r'(20\d{2})\s*년\s*생')

_CUR_YEAR = 2026  # 데이터 기준 연도 (연도 역산용)


def _stage_of_text(text) -> str:
    """타겟 원문 → 자녀 연령 스테이지. 매칭 없으면 '미상'.

    ⚠ 단순 키워드 매칭은 틀린다.
      "20-23년 유치원 결제자"는 **과거 이력**이라 자녀가 지금은 초등이다.
      실제로 아이스크림홈런·밀크T(초등 학습지)가 '영유아'로 잘못 분류됐다.
    판정 순서:
      1) 학령 명시(초등/중학/고등) — 연도와 무관하게 확정
      2) 자녀 출생연도 명시 → 나이 계산
      3) 유치원·어린이집 결제 '종료 연도'로 현재 나이 역산
         (유치원 재원 ≈ 5세 기준 + 경과 연수)
      4) 영유아 전용 시그널(이유식·기저귀·산후조리원 등)
    """
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return '미상'
    s = str(text)
    if not s.strip():
        return '미상'

    # 1) 학령 명시가 가장 강한 신호
    if _RE_STAGE_HIGH.search(s):
        return '고등'
    if _RE_STAGE_MID.search(s):
        return '중등'
    if _RE_STAGE_ELEM.search(s):
        return '초등'

    def _age_to_stage(age: float) -> str:
        if age < 7:
            return '영유아'
        if age < 13:
            return '초등'
        if age < 16:
            return '중등'
        return '고등'

    # 2) 자녀 출생연도 명시 (예: "2022년생")
    m = _RE_BIRTH_YEAR.search(s)
    if m:
        return _age_to_stage(_CUR_YEAR - int(m.group(1)))

    # 3) 유치원·어린이집 결제 연도로 역산
    yrs = [int(mm.group(2)) for mm in _RE_KINDER_YEAR.finditer(s)]
    if yrs:
        end_yy = max(yrs)                      # 가장 최근 재원 연도
        end_year = 2000 + end_yy if end_yy < 100 else end_yy
        # 유치원 재원 시점 약 5세 → 현재 나이 = 5 + 경과 연수
        return _age_to_stage(5 + (_CUR_YEAR - end_year))

    # 4) 영유아 전용 시그널 (연도 없음)
    if _RE_INFANT.search(s) or _RE_KINDER_PLAIN.search(s):
        return '영유아'
    return '미상'


def _add_stage(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out['_stage'] = out['타겟'].apply(_stage_of_text) if '타겟' in out.columns else '미상'
    return out


def _main_stage(df_brand: pd.DataFrame) -> tuple[str, dict]:
    """브랜드의 주력 자녀 연령 스테이지와 분포(%)."""
    if df_brand.empty or '_stage' not in df_brand.columns:
        return '미상', {}
    vc = df_brand[df_brand['_stage'] != '미상']['_stage'].value_counts()
    if vc.empty:
        return '미상', {}
    total = int(vc.sum())
    dist = {k: round(v / total * 100) for k, v in vc.items()}
    return str(vc.index[0]), dist


def _cpa_grade(cpa: float, good: float = GOOD_CPA) -> tuple[str, str]:
    """CPA → (등급 문구, 색상).

    good: 업종 공통 기준 CPA. 교육은 500만원/30건 = 166,667원 고정.
    """
    if cpa <= 0:
        return '—', COLOR_TEXT_SEC
    if cpa <= good:
        return '양호', COLOR_SUCCESS
    if cpa <= good * _CPA_POOR_MULT:
        return '보통', COLOR_WARNING
    return '부진', COLOR_DANGER


def _db_rate_table(df: pd.DataFrame) -> dict:
    """매체·상품별 '발송 1만건당 DB 인입수'와 상품별/전체 평균을 계산.

    DB 예상치의 근거가 되는 전환율 테이블.
    CPA 상품은 발송 개념이 없어(물량=DB 과금 단위) 별도로 다룬다.

    매체 단위만으로 묶으면 같은 매체의 서로 다른 상품이 섞인다.
    상품 간 전환율 차이가 매체 간 차이보다 훨씬 커서 그대로 쓰면 어긋난다.
      (온리원 경기지역화폐: LMS 4.00건/만 vs PUSH 0.57건/만 — 7배)
    """
    out = {'media': {}, 'media_prod': {}, 'product': {}, 'overall': None}
    if df is None or df.empty or 'DB' not in df.columns:
        return out
    d = df.copy()
    d['_db'] = pd.to_numeric(d['DB'], errors='coerce')
    d = d[d['_db'].notna()]
    if d.empty:
        return out
    d['_snd'] = pd.to_numeric(d.get('발송건'), errors='coerce').fillna(0)
    d['_prod'] = d['광고상품'].apply(_product_label)
    if '_mk' not in d.columns:
        d['_mk'] = d['매체'].apply(_media_key)
    msg = d[~d['_prod'].isin(['DA', _PRODUCT_PERF])]
    msg = msg[msg['_snd'] > 0]
    if not msg.empty:
        for media, g in msg.groupby('_mk'):
            snd = float(g['_snd'].sum())
            if snd <= 0:
                continue
            out['media'][str(media)] = {
                'rate': float(g['_db'].sum()) / snd * 10000,
                'n': len(g), 'db': float(g['_db'].sum()),
            }
        for (media, prod), g in msg.groupby(['_mk', '_prod']):
            snd = float(g['_snd'].sum())
            if snd <= 0:
                continue
            out['media_prod'][(str(media), str(prod))] = {
                'rate': float(g['_db'].sum()) / snd * 10000,
                'n': len(g), 'db': float(g['_db'].sum()),
            }
        for prod, g in msg.groupby('_prod'):
            snd = float(g['_snd'].sum())
            if snd > 0:
                out['product'][str(prod)] = float(g['_db'].sum()) / snd * 10000
        tot_snd = float(msg['_snd'].sum())
        if tot_snd > 0:
            out['overall'] = float(msg['_db'].sum()) / tot_snd * 10000
    return out


def _expect_db(media: str, qty: int, *, my_tbl: dict, ref_tbl: dict,
               product: str = 'LMS', label: str = '') -> tuple[float | None, str]:
    """추천 물량에 대한 예상 DB 인입수와 그 근거. **실측만 사용한다.**

    우선순위 (제안할 상품과 같은 상품의 실적):
      1) 자사 해당 매체 + 해당 상품
      2) 동종업계(같은 학령, DB 수신 브랜드) 해당 매체 + 해당 상품
      3) 같은 매체의 다른 상품 실측 — 자사 → 타사 (상품 라벨이 갈린 경우)

    평균(상품 평균·전체 평균)은 쓰지 않는다. 매체마다 DB 인입 수가
    워낙 달라서 평균을 입히면 추천이 뒤집힌다.
      (온리원 실측: 현대카드 CNCAD 5.86 · SKT비즈챗 4.86 · 신한카드 4.27
                   vs CJONE 0.59건/만 — 10배. 자사 LMS 평균은 3.84건/만이라
                   CJONE에 평균을 입히면 6배 과대추정이 된다)
    실측이 없으면 None을 돌려주고, 호출부는 그 매체를 CTR 기준으로 제안한다.
    근거에 표본 수를 함께 적어 신뢰도를 판단할 수 있게 한다.
    """
    name = label or media
    mp = my_tbl.get('media_prod', {}).get((media, product))
    if mp and mp['n'] >= 1:
        return qty * mp['rate'] / 10000, f'자사 {name} {product} 실적 {mp["n"]}건'
    rp = ref_tbl.get('media_prod', {}).get((media, product))
    if rp and rp['n'] >= 1:
        return qty * rp['rate'] / 10000, f'동종업계 {name} {product} {rp["n"]}건'
    m = my_tbl.get('media', {}).get(media)
    if m and m['n'] >= 1:
        return qty * m['rate'] / 10000, f'자사 {name} 실적 {m["n"]}건'
    r = ref_tbl.get('media', {}).get(media)
    if r and r['n'] >= 1:
        return qty * r['rate'] / 10000, f'동종업계 {name} {r["n"]}건'
    return None, ''


def _is_db_driven(me: pd.DataFrame) -> bool:
    """이 광고주에게 DB(전환)가 핵심 KPI인지.

    DB를 실제로 받아내는 광고주(온리원·밀크T·아이스크림홈런 등)와,
    DB 칸은 있지만 전환이 거의 없는 CTR 중심 광고주(윙크·캐츠잉글리시)를 구분한다.
    DB 중심 광고주는 '전환 0건' 매체를 증액 후보에서 제외해야 한다.
    """
    if me is None or me.empty or 'DB' not in me.columns:
        return False
    db = pd.to_numeric(me['DB'], errors='coerce').fillna(0)
    pos = int((db > 0).sum())
    return pos >= _DB_DRIVEN_MIN_CAMPAIGNS and pos / max(len(me), 1) >= _DB_DRIVEN_MIN_RATIO


def _db_perf(rows: pd.DataFrame, *, good_cpa: float = GOOD_CPA) -> dict | None:
    """DB(전환) 기준 성과. DB가 충분히 쌓인 경우에만 반환.

    good_cpa: 업종별 양호 기준. 교육은 고정값(500만원/30건),
              그 외 업종은 호출부에서 업종 중앙 CPA를 넘겨준다.
    """
    if rows.empty or 'DB' not in rows.columns:
        return None
    # DB가 아직 집계되지 않은(NaN) 캠페인은 분자·분모 모두에서 뺀다.
    #   광고비만 더해지면 최근 집행이 실제보다 나쁘게 찍힌다 —
    #   실사례: 온리원 SKT비즈챗 705,882원 → 집계분만 352,941원,
    #           아이스크림홈런 롯데카드 384,615원 → 128,205원(양호).
    #   DB 0건으로 '보고된' 행은 실제 실패이므로 그대로 포함한다.
    _prod = rows['광고상품'].apply(_product_label)
    rep = rows[(_prod != 'DA') & pd.to_numeric(rows['DB'], errors='coerce').notna()]
    if rep.empty:
        return None
    db = pd.to_numeric(rep['DB'], errors='coerce').fillna(0).sum()
    if db < _MIN_DB_FOR_JUDGE:
        return None
    # DA(배너)는 원래 DB가 찍히지 않는 상품이라 '미집계'가 아니라
    # 같은 매체를 사면서 함께 낸 **패키지 비용**이다. 분모에 포함해야
    # 광고주가 체감하는 CPA와 같아진다.
    #   (실사례: 온리원 KB페이 = LMS 500만 + DA 배너 200만 패키지, DB 17건.
    #            LMS만 세면 294,118원이지만 실제로는 411,765원 — 광고주는
    #            "매우 안 좋았다"고 평가했다. 타겟 원문에도 "KB Pay + LMS
    #            배너 패키지"로 적혀 있다)
    pkg = pd.to_numeric(rows[_prod == 'DA']['광고비'], errors='coerce').fillna(0).sum()
    cost = pd.to_numeric(rep['광고비'], errors='coerce').fillna(0).sum() + float(pkg)
    if cost <= 0:
        return None
    rows = rep
    cpa = float(cost) / float(db)
    if cpa <= good_cpa:
        grade, color = '양호', COLOR_SUCCESS
    elif cpa <= good_cpa * _CPA_POOR_MULT:
        grade, color = '보통', COLOR_WARNING
    else:
        grade, color = '부진', COLOR_DANGER
    return {'db': float(db), 'cost': float(cost), 'cpa': cpa,
            'grade': grade, 'color': color, 'n': len(rows), 'good_cpa': good_cpa}


def _summarize_target(raw) -> str:
    """타겟 원문 → 한 줄 요약 (캘린더와 동일 규칙)."""
    try:
        from modules.schedule_calendar import _summarize_target as _s
        return _s(raw)
    except Exception:
        return str(raw)[:30] if raw else ''


def _best_target_for(df: pd.DataFrame, media: str) -> tuple[str, str]:
    """해당 매체에서 가장 성과 좋았던 캠페인의 타겟 (요약, 원문)."""
    if df.empty or '타겟' not in df.columns:
        return '', ''
    col = '_mk' if '_mk' in df.columns else '매체'
    sub = df[df[col].astype(str) == media].copy()
    sub = sub[sub['타겟'].notna() & (sub['타겟'].astype(str).str.strip() != '')]
    sub = sub[pd.to_numeric(sub['발송건'], errors='coerce').fillna(0) >= MIN_SENDS_FOR_CTR_CLAIM]
    if sub.empty:
        return '', ''
    sub['_ctr'] = (
        pd.to_numeric(sub['클릭수'], errors='coerce').fillna(0)
        / pd.to_numeric(sub['발송건'], errors='coerce').clip(lower=1) * 100
    )
    best = sub.nlargest(1, '_ctr').iloc[0]
    raw = str(best['타겟'])
    return _summarize_target(raw), raw


@st.cache_data(ttl=1800, show_spinner=False)
def build_recommendations(
    _full: pd.DataFrame, brand: str, industry: str, cache_key: str
) -> list[dict]:
    """4유형 제안 생성. (cache_key는 캐시 무효화용 — 데이터 크기·최신일자)

    DB 광고주의 제안은 매체마다 **근거의 종류가 다르다**.
      · 그 매체에서 전환 실측(자사 또는 DB 수신 타사)이 있으면 → 예상 DB·CPA
      · 전환 실측이 없고 CTR 레퍼런스만 있으면 → 예상 CTR·클릭
    전환 실측이 없는 매체에 업종·상품 평균 전환율을 입히지 않는다.
    매체별 전환율 차이가 워낙 커서(온리원: 현대카드 CNCAD 5.86 vs
    CJONE 0.59건/만 — 10배) 평균을 입히면 추천 순위가 뒤집힌다.
    """
    if _full is None or _full.empty:
        return []
    full = _full.copy()
    full['_d'] = pd.to_datetime(full['_date'], errors='coerce')
    full = full.dropna(subset=['_d'])
    if full.empty:
        return []
    latest = full['_d'].max()
    # 같은 매체의 표기 변형(BC카드 / BC카드 CNCAD)을 하나로 묶는다.
    #   이후 모든 매체 단위 집계·중복 판정은 _mk 로 한다.
    full['_mk'] = full['매체'].apply(_media_key)

    # 자녀 연령 스테이지는 교육 업종에서만 의미가 있다
    use_stage = industry in _STAGE_INDUSTRIES
    if use_stage:
        full = _add_stage(full)
    me = full[full['_브랜드'] == brand].copy()
    if me.empty:
        return []

    # 이 광고주에게 DB(전환)가 핵심 KPI인지 — 레퍼런스를 고르기 전에 판정한다.
    db_driven = _shared_is_db_driven(me)

    # 자사 주력 자녀 연령 스테이지 — 레퍼런스를 고를 때의 기준
    my_stage, my_dist = _main_stage(me) if use_stage else ('미상', {})

    # 업종별 양호 CPA 기준: 교육은 고정(500만원/30건), 그 외는 업종 중앙값
    good_cpa = GOOD_CPA
    if not use_stage:
        ind_rows = full[(full['분야'] == industry)
                        & (pd.to_numeric(full['DB'], errors='coerce').fillna(0) > 0)]
        if not ind_rows.empty:
            _db = pd.to_numeric(ind_rows['DB'], errors='coerce')
            _cp = (pd.to_numeric(ind_rows['광고비'], errors='coerce') / _db).replace(
                [np.inf, -np.inf], np.nan).dropna()
            if len(_cp):
                good_cpa = float(_cp.median())

    # 메시징 qualified (CTR 신뢰 가능)
    def _qual(d):
        q = d[~d['광고상품'].astype(str).str.upper().isin(['DA', 'CPA'])].copy()
        q = q[pd.to_numeric(q['발송건'], errors='coerce').fillna(0) >= MIN_SENDS_FOR_CTR_CLAIM]
        q['_ctr'] = (
            pd.to_numeric(q['클릭수'], errors='coerce').fillna(0)
            / pd.to_numeric(q['발송건'], errors='coerce').clip(lower=1) * 100
        )
        return q

    me_q = _qual(me)
    my_media_all = set(me['_mk'].dropna().astype(str))
    recent_media = set(me[me['_d'] >= latest - pd.Timedelta(days=_DORMANT_DAYS)]['_mk']
                       .dropna().astype(str))

    # 동종업계 타사 (최근 180일)
    others = full[
        (full['분야'] == industry)
        & (full['_브랜드'] != brand)
        & (full['_d'] >= latest - pd.Timedelta(days=_REF_WINDOW_DAYS))
    ].copy()

    # ⚠ 같은 업종이라도 자녀 연령대가 다르면 모수가 전혀 다르다.
    #   자사 주력 스테이지와 같은 타겟의 캠페인만 레퍼런스로 삼는다.
    #   (예: 윙크=영유아 / 온리원=초등 → 서로 레퍼런스가 될 수 없음)
    stage_filtered = False
    if use_stage and my_stage != '미상':
        same_stage = others[others['_stage'] == my_stage]
        if len(same_stage) >= 5:
            others = same_stage
            stage_filtered = True

    # ⚠ **전환(DB) 레퍼런스는 DB를 전달받는 브랜드에서만 가져온다.**
    #   윙크·캐츠잉글리시·웅진스마트올은 광고주에게서 DB를 못 받아
    #   그 매체에서 전환이 됐는지 자체를 알 수 없다.
    #   그렇다고 이들의 실적을 버리지는 않는다 — CTR은 유효한 정보다.
    #   전환 실측이 없는 매체는 'CTR 기준' 제안으로 넘긴다.
    #   (실사례: 온리원 KT·BC카드 CNCAD는 캐츠잉글리시 CTR만 있는 매체 →
    #            DB 추정을 붙이지 않고 예상 CTR로 제시한다)
    db_brands: set[str] = set()
    others_db = others.iloc[0:0]
    if db_driven and '_브랜드' in others.columns:
        db_brands = {b for b in others['_브랜드'].dropna().astype(str).unique()
                     if _shared_is_db_driven(full[full['_브랜드'].astype(str) == b])}
        others_db = others[others['_브랜드'].astype(str).isin(db_brands)]

    # 타사 레퍼런스 모집단 — DB를 전달받는 브랜드의 **전 기간** 실적.
    #   기간·학령을 좁히면 표본이 2건씩으로 쪼개져, 같은 매체의 예상치와
    #   실측치가 어긋난다. 어떤 매체를 볼지는 학령 필터로 이미 걸렀으므로
    #   전환율·CPA는 그 매체의 전체 실적으로 계산하는 게 정확하다.
    #     (실사례: 온리원 키즈노트 — 180일·초등 2건 기준으로는 예상 CPA
    #      294,118원이라 제외됐지만, 아이스크림홈런 전 기간 실측은 681건에
    #      175,184원이었다. 같은 매체를 두고 두 숫자가 엇갈렸다)
    ind_db_rows = full.iloc[0:0]
    if db_driven and db_brands:
        ind_db_rows = full[(full['분야'] == industry)
                           & (full['_브랜드'].astype(str).isin(db_brands))]

    # 예상 DB 산출용 전환율 테이블 — 자사 우선, 타사는 DB 수신 브랜드만
    _empty_tbl = {'media': {}, 'media_prod': {}, 'product': {}, 'overall': None}
    my_tbl = _db_rate_table(_drop_promo(me)) if db_driven else _empty_tbl
    ref_tbl = _db_rate_table(_drop_promo(ind_db_rows)) if db_driven else _empty_tbl

    def _measured_cpa(media: str, product: str) -> tuple[dict | None, str]:
        """그 매체에서 실제로 나온 CPA (자사 전체 기간 → DB 수신 타사).

        추정치가 아무리 좋게 나와도 **실측이 부진했던 매체는 제안하지 않는다.**
        전환율(발송 1만건당 DB)만 보면 패키지로 함께 낸 배너 비용이나
        매체 전체 지출이 빠져서 실제보다 좋게 보일 수 있다.
          (실사례: 온리원 KB페이 — 전환율로는 294,118원이지만 DA 배너
                   200만원을 합치면 411,765원. 광고주 평가는 "매우 안 좋았다")
        """
        if not db_driven:
            return None, ''
        # 타사 쪽은 학령·기간 필터를 걸지 않은 전체 이력을 본다.
        #   이 값은 '이 매체가 실제로 나빴나'를 거르는 용도라 증거를 넓게 모은다.
        #   학령 필터를 그대로 걸면 패키지 배너 행(타겟 문구에 학령 신호가 없어
        #   '미상'으로 빠짐)이 떨어져 나가 비용이 과소집계된다.
        #   (실사례: 온리원 KB페이 DA 200만원이 빠져 294,118원으로 보였고,
        #            그 값으로 밀크T·엘리하이에 KB페이가 다시 추천됐다)
        for src, label in ((me, '자사'), (ind_db_rows, '타사')):
            rows = src[src['_mk'].astype(str) == media]
            if rows.empty:
                continue
            # 제안할 상품과 같은 상품만 본다 (+ 함께 낸 DA 배너 비용).
            # 성과형 CPA 행과 메시징 행이 섞이면 전혀 다른 단가가 합쳐진다.
            _pl = rows['광고상품'].apply(_product_label)
            rows = rows[_pl.isin([product, 'DA'])]
            p = _db_perf(rows, good_cpa=good_cpa)
            # 단가 미입력·패키지 정산으로 광고비가 비어 있으면 CPA가
            # 비현실적으로 낮게 찍힌다. 실측으로 믿지 않는다.
            #   (온리원 ASUM 패키지: 단가 1원으로 기록돼 실측 CPA 2,667원)
            if p is not None and p['cpa'] >= _MIN_CREDIBLE_CPA:
                return p, label
        return None, ''

    def _ref_table(q: pd.DataFrame) -> pd.DataFrame:
        """동종업계 매체별 CTR·단가 집계. 레퍼런스가 비면 빈 테이블."""
        cols = ['_mk', '브랜드수', '캠페인수', '발송', '클릭', '단가', 'CTR']
        if q.empty:
            return pd.DataFrame(columns=cols)
        t = q.groupby('_mk').agg(
            브랜드수=('_브랜드', 'nunique'), 캠페인수=('_mk', 'size'),
            발송=('발송건', 'sum'), 클릭=('클릭수', lambda x: x.fillna(0).sum()),
            단가=('실단가', 'median') if '실단가' in q.columns else ('단가', 'median'),
        ).reset_index()
        t['CTR'] = t.apply(lambda r: _ctr(r['클릭'], r['발송']), axis=1)
        return t

    others_q = _qual(others)
    ref = _ref_table(others_q)
    ref_map = {r['_mk']: r for _, r in ref.iterrows()}

    _ucol = '실단가' if '실단가' in full.columns else '단가'
    ind_rows_all = full[full['분야'] == industry]

    # 표기 변형을 합쳐 집계하므로, 화면에는 실제로 많이 쓰는 이름을 쓴다.
    #   ('현대카드 CNCAD'를 '현대카드'로 줄여 보여주면 운영팀이 못 찾는다)
    def _mode_name(g) -> str:
        v = g['매체'].dropna().astype(str)
        return str(v.mode().iloc[0]) if len(v) else ''

    _disp = {str(k): _mode_name(g) for k, g in ind_rows_all.groupby('_mk')
             if not g.empty}
    # 자사가 실제로 쓰는 표기가 있으면 그것을 보여준다 (발주 시 혼동 방지)
    _disp.update({str(k): _mode_name(g) for k, g in me.groupby('_mk')
                  if not g.empty and _mode_name(g)})

    def _disp_name(key) -> str:
        return _disp.get(str(key), str(key))

    def _plan_qty(media_key: str, product: str) -> int:
        """추천 물량 — 그 매체에서 실제로 돌린 1회 물량을 따른다.

        자사 매체×상품 중앙값 → 업종 매체×상품 중앙값 → 상품 기본값.
        상품 기본값만 쓰면 실제 집행 규모와 어긋난다 (교육 실측:
        LMS 5만건 / 카플친 5만건 / 앱푸시 16.7만건).
        """
        for src in (me, ind_rows_all):
            if src.empty or '정산수량' not in src.columns:
                continue
            rows = src[(src['_mk'].astype(str) == media_key)
                       & (src['광고상품'].apply(_product_label) == product)]
            q = pd.to_numeric(rows['정산수량'], errors='coerce')
            q = q[q > 0]
            if len(q):
                v = float(q.median())
                if product == _PRODUCT_PERF:
                    return int(max(round(v), 1))
                return int(max(round(v / 1000) * 1000, 1000))
        return int(PRODUCT_DEFAULT_QTY.get(product, _DEFAULT_QTY))

    # 최근 6개월 월 집행액 중앙값 — 제안 총액을 현실 예산에 맞추는 기준
    monthly_budget = 0.0
    _recent6 = me[me['_d'] >= latest - pd.Timedelta(days=186)]
    if not _recent6.empty:
        _ms = _recent6.groupby(_recent6['_d'].dt.to_period('M'))['광고비'].apply(
            lambda x: float(pd.to_numeric(x, errors='coerce').fillna(0).sum()))
        _ms = _ms[_ms > 0]
        if len(_ms):
            monthly_budget = float(_ms.median())

    def _media_plan(media: str) -> tuple[str, float]:
        """이 매체를 제안할 때의 (주력 광고상품, 유효 단가).

        단가와 상품을 **같은 출처에서 함께** 가져온다. 따로 가져오면
        '카톡MSG 단가(40원) × LMS 전환율'처럼 상품이 어긋난 추정이 만들어진다.
        (실사례: 아이쿠카는 카톡MSG 40원과 성과형 CPA 60,000원 두 가지로 팔린다)

        단가가 비정상적으로 낮은 행(패키지 정산·단가 미입력)은 건너뛴다.
        그대로 쓰면 예산과 예상 CPA가 동시에 허수가 된다.
        """
        for src, newest in ((me, True), (others, False), (ind_rows_all, False)):
            rows = src[src['_mk'].astype(str) == media].copy()
            if rows.empty:
                continue
            rows['_u'] = pd.to_numeric(rows[_ucol], errors='coerce')
            rows = rows[rows['_u'] >= _MIN_UNIT_PRICE]
            if rows.empty:
                continue
            rows['_prod'] = rows['광고상품'].apply(_product_label)
            # 메시징 상품을 먼저 본다. CTR·전환 근거가 메시징 행에서 나오므로
            # 같은 매체에 DA가 더 많다고 DA로 잡으면 근거와 어긋난다.
            #   (실사례: 밀크T 도와줘 — CTR 2.12%는 카플친 실적인데
            #            DA가 더 많아 DA로 제안되고 물량이 0으로 찍혔다)
            _msg = rows[rows['_prod'] != 'DA']
            prod = str((_msg if not _msg.empty else rows)['_prod'].mode().iloc[0])
            pr = rows[rows['_prod'] == prod].sort_values('_d')
            unit = float(pr['_u'].iloc[-1]) if newest else float(pr['_u'].median())
            return prod, unit
        # 업종 전체 메시징 중앙 단가로 마지막 보정 (그마저 없으면 제안 제외)
        allp = pd.to_numeric(others_q[_ucol], errors='coerce').dropna() \
            if _ucol in others_q.columns else pd.Series(dtype=float)
        allp = allp[allp >= _MIN_UNIT_PRICE]
        return 'LMS', (float(allp.median()) if len(allp) else 0.0)

    def _unit_price(media: str) -> float:
        return _media_plan(media)[1]

    recs: list[dict] = []

    # ── 🔁 휴면 복귀 ──
    for media in sorted(my_media_all - recent_media):
        hist = me_q[me_q['_mk'].astype(str) == media]
        if hist.empty:
            continue
        my_ctr = _ctr(hist['클릭수'].fillna(0).sum(), hist['발송건'].sum())
        last_d = me[me['_mk'].astype(str) == media]['_d'].max()
        days = int((latest - last_d).days)
        tgt_s, tgt_raw = _best_target_for(me, media)
        recs.append({
            'type': '🔁 휴면 복귀', 'tone': COLOR_WARNING, 'media': media,
            'ctr': my_ctr, 'ctr_src': f'자사 실적 {len(hist)}건',
            'qty': _DEFAULT_QTY, 'unit': _unit_price(media),
            'target': tgt_s, 'target_raw': tgt_raw,
            'why': f'{days}일간 미집행. 과거 CTR {my_ctr:.2f}% 기록',
            'score': my_ctr * 1.0,
            'evidence': f'자사 {len(hist)}건 · 최종 집행 {last_d:%Y-%m-%d}',
        })

    # ── 🆕 신규 진입 (동종업계 검증, 자사 미집행) ──
    for _, r in ref.iterrows():
        media = str(r['_mk'])
        if media in my_media_all or r['발송'] < MIN_SENDS_FOR_CTR_CLAIM:
            continue
        tgt_s, tgt_raw = _best_target_for(others, media)
        brands = sorted(set(
            others_q[others_q['_mk'].astype(str) == media]['_브랜드'].dropna().astype(str)
        ))[:3]
        recs.append({
            'type': '🆕 신규 진입', 'tone': BRAND_PRIMARY, 'media': media,
            'ctr': float(r['CTR']), 'ctr_src': f'동종업계 {int(r["캠페인수"])}건',
            'qty': _DEFAULT_QTY if r['발송'] >= 100_000 else 30_000,
            'unit': _unit_price(media),
            'target': tgt_s, 'target_raw': tgt_raw,
            'why': f'{industry} 업종 {int(r["브랜드수"])}개사 검증 · 자사 미집행',
            'score': float(r['CTR']) * (1.0 + 0.1 * int(r['브랜드수'])),
            'evidence': f'활용: {", ".join(brands)}' + (' 외' if int(r['브랜드수']) > 3 else ''),
        })

    # ── 🆕 신규 진입 (타사 CPA 검증, 자사 미집행) ──
    # 자사 집행 이력이 없어도 **타사가 DB를 잘 뽑은 매체**는 추천한다.
    #   CTR만 보는 위 블록과 달리 전환 실측으로 고르므로 DB 광고주에게
    #   훨씬 actionable하다. CTR이 낮아도 DB가 나오는 매체가 실제로 있다.
    for media in sorted(set(others_db['_mk'].dropna().astype(str)) - my_media_all):
        rows = others_db[others_db['_mk'].astype(str) == media]
        perf = _db_perf(rows, good_cpa=good_cpa)
        if perf is None or perf['grade'] == '부진':
            continue
        tgt_s, tgt_raw = _best_target_for(others_db, media)
        pb = sorted(set(rows['_브랜드'].dropna().astype(str)))[:3]
        _rq = _qual(rows)
        recs.append({
            'type': '🆕 신규 진입', 'tone': BRAND_PRIMARY, 'media': media,
            'ctr': _ctr(_rq['클릭수'].fillna(0).sum(), _rq['발송건'].sum()) if not _rq.empty else 0,
            'ctr_src': f'동종업계 {len(rows)}건',
            'qty': _DEFAULT_QTY, 'unit': _unit_price(media),
            'target': tgt_s, 'target_raw': tgt_raw,
            'why': (f'타사 CPA {perf["cpa"]:,.0f}원 ({perf["grade"]}) — '
                    f'기준 {good_cpa:,.0f}원 · 자사 미집행'),
            # 전환으로 검증된 매체는 CTR 기반 제안보다 항상 위로
            'score': (good_cpa / perf['cpa']) * 5.0,
            'evidence': (f'타사 DB {perf["db"]:.0f}건 / 광고비 '
                         f'{compact_num(perf["cost"], "원")} · {perf["n"]}회 — '
                         f'{", ".join(pb)}'),
        })

    # ── 📈 증액 (최근 2개월 자사 성과 우수) ──
    # ⚠ DB(전환) 추적 매체는 CTR이 아니라 **CPA 단가**로 판정한다.
    #   CTR이 높아도 전환이 안 되면 광고주 입장에선 실패한 캠페인이다.
    # 자사 기준선: 메시징 평균 CPA (참고 표기용)
    my_cpa_base = None
    if db_driven:
        _m = me[~me['광고상품'].astype(str).str.upper().isin(['DA', 'CPA'])].copy()
        _m['_db'] = pd.to_numeric(_m['DB'], errors='coerce')
        _m = _m[_m['_db'].fillna(0) > 0]
        if not _m.empty:
            _c = pd.to_numeric(_m['광고비'], errors='coerce').fillna(0).sum()
            _d = float(_m['_db'].sum())
            if _d > 0:
                my_cpa_base = _c / _d
    recent_all = me[me['_d'] >= latest - pd.Timedelta(days=62)]
    recent_q = me_q[me_q['_d'] >= latest - pd.Timedelta(days=62)]
    if not recent_q.empty or not recent_all.empty:
        base_ctr = _ctr(recent_q['클릭수'].fillna(0).sum(), recent_q['발송건'].sum()) \
            if not recent_q.empty else 0
        cands = []
        for media in sorted(set(recent_all['_mk'].dropna().astype(str))):
            m_all = recent_all[recent_all['_mk'].astype(str) == media]
            m_q = recent_q[recent_q['_mk'].astype(str) == media]
            # DB를 전달받는 광고주일 때만 CPA로 판정한다.
            # (미수신 광고주는 우연히 기록된 1~2건으로 CPA가 계산돼 왜곡된다 —
            #  예: 윙크 헬로100 DB 2건/4만원 → CPA 2만원으로 '양호' 오판)
            perf = _db_perf(m_all, good_cpa=good_cpa) if db_driven else None

            # DB가 핵심 KPI인 광고주인데 이 매체에서 전환이 0건이면
            # CTR이 아무리 좋아도 증액 대상이 아니다 (광고주 기준 실패)
            if perf is None and db_driven:
                _tracked = pd.to_numeric(m_all.get('DB'), errors='coerce')
                _spend = pd.to_numeric(m_all['광고비'], errors='coerce').fillna(0).sum()
                if _tracked is not None and _tracked.notna().any() and _spend > 0:
                    continue

            if perf is not None:
                # 전환 기준 평가 — 기준 CPA 초과면 증액 대상에서 제외
                if perf['cpa'] > good_cpa:
                    continue
                qty_base = float(m_all['정산수량'].sum()) if '정산수량' in m_all.columns else 0
                # 1회분 평균 물량 = 전체 정산수량 / 집행 횟수.
                # DB가 집계된 건수(perf['n'])로 나누면 아직 DB가 안 들어온
                # 캠페인 몫까지 1회분에 더해져 물량이 몇 배로 불어난다.
                #   (실사례: 아이스크림홈런 롯데카드 3회 15만건 → 15만건 증액 제안)
                n_run = max(len(m_all), 1)
                n = perf['n']
                ctr_val = _ctr(m_q['클릭수'].fillna(0).sum(), m_q['발송건'].sum()) if not m_q.empty else 0
                cands.append({
                    'media': media, 'ctr': ctr_val,
                    'ctr_src': f'자사 최근 {n}건 · DB {perf["db"]:.0f}건',
                    'qty': int(min(max(qty_base / n_run * 1.3 // 1000 * 1000, 10_000), 150_000)),
                    'why': (f'CPA {perf["cpa"]:,.0f}원 ({perf["grade"]}) — '
                            f'목표 {good_cpa:,.0f}원 이내'),
                    'evidence': (f'최근 DB {perf["db"]:.0f}건 / 광고비 '
                                 f'{compact_num(perf["cost"], "원")} · {n}회 집행'),
                    'score': (good_cpa / perf['cpa']) * 3.0,   # CPA가 좋을수록 높은 점수
                    'cpa': perf['cpa'], 'db': perf['db'],
                })
                continue

            # DB 추적이 없는 매체만 CTR로 판정
            if m_q.empty:
                continue
            c = _ctr(m_q['클릭수'].fillna(0).sum(), m_q['발송건'].sum())
            if c < base_ctr or base_ctr <= 0:
                continue
            n = len(m_q)
            lift = (c / base_ctr - 1) * 100
            cands.append({
                'media': media, 'ctr': c, 'ctr_src': f'자사 최근 {n}건',
                'qty': int(min(m_q['발송건'].sum() / max(n, 1) * 1.3 // 1000 * 1000, 150_000)),
                'why': f'최근 2개월 CTR {c:.2f}% — 자사 평균 대비 +{lift:.0f}%',
                'evidence': f'최근 발송 {compact_num(float(m_q["발송건"].sum()), "건")} · {n}회',
                'score': c * 1.2, 'cpa': None, 'db': None,
            })

        for c in sorted(cands, key=lambda x: -x['score'])[:4]:
            tgt_s, tgt_raw = _best_target_for(me, c['media'])
            recs.append({
                'type': '📈 증액', 'tone': COLOR_SUCCESS, 'media': c['media'],
                'ctr': c['ctr'], 'ctr_src': c['ctr_src'],
                'qty': c['qty'], 'unit': _unit_price(c['media']),
                'target': tgt_s, 'target_raw': tgt_raw,
                'why': c['why'], 'score': c['score'], 'evidence': c['evidence'],
                'cpa': c.get('cpa'), 'db': c.get('db'), 'qty_fixed': True,
            })

    # ── ✨ 업종 신규 매체 (최근 등장, 선점 기회) ──
    ind_all = full[full['분야'] == industry]
    cut = latest - pd.Timedelta(days=_NEW_MEDIA_DAYS)
    newly = set(ind_all[ind_all['_d'] >= cut]['_mk'].dropna().astype(str)) - \
        set(ind_all[ind_all['_d'] < cut]['_mk'].dropna().astype(str))
    for media in sorted(newly):
        if media in my_media_all:
            continue
        sub = others_q[others_q['_mk'].astype(str) == media]
        c = _ctr(sub['클릭수'].fillna(0).sum(), sub['발송건'].sum()) if not sub.empty else 0
        if c <= 0:
            continue  # 성과 레퍼런스가 전혀 없으면 추천 근거가 없다
        tgt_s, tgt_raw = _best_target_for(others, media)
        recs.append({
            'type': '✨ 신규 매체', 'tone': COLOR_BLUE, 'media': media,
            'ctr': c, 'ctr_src': f'동종업계 {len(sub)}건' if len(sub) else '레퍼런스 없음',
            'qty': 30_000, 'unit': _unit_price(media),
            'target': tgt_s, 'target_raw': tgt_raw,
            'why': f'최근 {_NEW_MEDIA_DAYS}일 내 {industry} 업종에 처음 등장 — 선점 기회',
            'score': (c if c > 0 else 1.0) * 0.9,
            'evidence': '업종 내 신규 매체',
        })

    # 중복 매체 제거 (점수 높은 제안 우선) + 상위 12개만
    # — 실제로 한 달에 돌릴 수 있는 규모로 추려야 제안서로 쓸 수 있다.
    recs.sort(key=lambda x: -x['score'])
    seen, out = set(), []
    dropped: list[dict] = []
    type_n: dict[str, int] = {}
    _meta = {
        'my_stage': my_stage, 'my_dist': my_dist,
        'stage_filtered': stage_filtered, 'good_cpa': good_cpa,
        'db_driven': db_driven, 'my_cpa_base': my_cpa_base,
        'use_stage': use_stage, 'monthly_budget': monthly_budget,
        'dropped': dropped,
    }

    def _drop(rec, reason: str, cpa=None):
        dropped.append({'media': _disp_name(rec['media']), 'type': rec['type'],
                        'product': rec.get('product', ''), 'reason': reason,
                        'exp_cpa': cpa})

    for r in recs:
        if r['media'] in seen:
            continue
        # 프로모션·특가로 집행했던 매체는 제안하지 않는다.
        # 정상 단가로 다시 돌리면 같은 효율이 나오지 않는다 (운영팀 확인).
        _pr = _promo_reason(r['media'], brand)
        if _pr:
            seen.add(r['media'])
            _drop(r, f'프로모션 집행 이력 — {_pr}')
            continue
        # 유형별 상한 (믹스 균형)
        if type_n.get(r['type'], 0) >= _TYPE_CAP.get(r['type'], 4):
            continue
        # CTR·CPA 어느 쪽으로도 근거가 없으면 제안하지 않는다
        if float(r.get('ctr') or 0) <= 0 and not r.get('cpa'):
            continue
        _prod, _ = _media_plan(r['media'])
        r['product'] = _prod
        r['media_disp'] = _disp_name(r['media'])
        # 추천 물량을 그 매체의 실제 집행 규모로 맞춘다 (증액은 자체 산출 유지)
        if not r.get('qty_fixed') and _prod != 'DA':
            r['qty'] = _plan_qty(r['media'], _prod)
        # 성과형(CPA) 상품은 단가 자체가 DB 단가다. 발송 전환율을 적용하면
        # '물량 1만건 × 6만원 = 6억원' 같은 허수가 만들어진다.
        # 물량은 발송이 아니라 DB 건수로 제안한다. (기준과 같은 30건)
        r['cpa_product'] = _prod == _PRODUCT_PERF
        if _prod == 'DA':
            # DA는 발송 단가 과금이 아니라 노출·기간 단위 집행이다.
            # 물량 × 단가로 예산을 만들면 실제 청구액과 무관한 값이 된다.
            # 과거 집행 광고비 중앙값을 추천 예산으로 쓴다.
            _da = ind_rows_all[
                (ind_rows_all['_mk'].astype(str) == r['media'])
                & (ind_rows_all['광고상품'].apply(_product_label) == 'DA')
            ]
            _c = pd.to_numeric(_da['광고비'], errors='coerce')
            _c = _c[_c > 0]
            if _c.empty:
                continue        # 실제 청구액을 모르면 제안하지 않는다
            _imp = pd.to_numeric(_da['발송건'], errors='coerce').fillna(0)
            _imp = _imp[_imp > 0]
            r['budget'] = int(_c.median())
            r['qty'] = int(_imp.median()) if len(_imp) else 0
        else:
            # 신뢰할 단가가 없으면 예산·예상 CPA를 만들 수 없다 → 제안 제외
            if float(r.get('unit') or 0) < _MIN_UNIT_PRICE:
                continue
            if r['cpa_product']:
                r['qty'] = REF_DB_FOR_CPA
            r['budget'] = int(r['qty'] * r['unit'])
        r['exp_clicks'] = int(r['qty'] * r['ctr'] / 100)

        # ── DB 광고주: 매체별 실측이 있으면 DB·CPA, 없으면 CTR 기준 ──
        r['basis'] = 'CTR'
        if db_driven:
            # 실측이 부진했던 매체는 추정치가 좋아도 제안하지 않는다
            _hist, _hsrc = _measured_cpa(r['media'], _prod)
            if _hist is not None:
                if _hist['grade'] == '부진':
                    seen.add(r['media'])
                    _drop(r, f'{_hsrc} {_prod} 실측 CPA {_hist["cpa"]:,.0f}원 '
                             f'({_hist["n"]}회 · DB {_hist["db"]:.0f}건) — 기준 '
                             f'{good_cpa * _CPA_POOR_MULT:,.0f}원 초과', _hist['cpa'])
                    continue
                r['hist_cpa'] = _hist['cpa']
                r['hist_src'] = (f'{_hsrc} {_prod} 실측 CPA {_hist["cpa"]:,.0f}원 '
                                 f'({_hist["grade"]}) · DB {_hist["db"]:.0f}건')
            if r['cpa_product']:
                # 성과형은 단가가 곧 DB 단가 — 추정이 아니라 계약 조건이다
                r['basis'] = 'CPA'
                r['exp_db'] = float(r['qty'])
                r['exp_cpa'] = float(r['unit'])
                r['exp_db_src'] = f'성과형 단가 {float(r["unit"]):,.0f}원 — DB 건당 과금'
            else:
                exp_db, db_src = _expect_db(
                    r['media'], r['qty'], my_tbl=my_tbl, ref_tbl=ref_tbl,
                    product=_prod, label=r['media_disp'])
                if exp_db and exp_db > 0:
                    r['basis'] = 'CPA'
                    r['exp_db'] = exp_db
                    r['exp_db_src'] = db_src
                    r['exp_cpa'] = r['budget'] / exp_db
                elif r.get('cpa'):
                    # 자사 실측 CPA가 있으면(증액 후보) 그 값을 그대로 쓴다
                    r['basis'] = 'CPA'
                    r['exp_cpa'] = float(r['cpa'])
                    r['exp_db'] = r['budget'] / float(r['cpa'])
                    r['exp_db_src'] = f'자사 {r["media_disp"]} 실측 CPA'

            if r['basis'] == 'CPA':
                # 선별: 업종 공통 기준 CPA 하나로 전 브랜드를 동일하게 본다.
                #   기준 이하 = 양호 / 1.5배 이내 = 보통 / 초과 = 부진(제외)
                r['exp_grade'], r['exp_grade_color'] = _cpa_grade(r['exp_cpa'], good_cpa)
                if r['exp_grade'] == '부진':
                    seen.add(r['media'])
                    _drop(r, f'예상 CPA {r["exp_cpa"]:,.0f}원 — 기준 '
                             f'{good_cpa * _CPA_POOR_MULT:,.0f}원 초과', r['exp_cpa'])
                    continue
            else:
                # 전환 실측이 없는 매체 — 평균을 입히지 않고 CTR로만 제시한다.
                # (CTR 레퍼런스가 DB 미수신 광고주뿐인 경우가 대표적)
                r['ctr_only'] = True
        seen.add(r['media'])
        type_n[r['type']] = type_n.get(r['type'], 0) + 1
        out.append(r)
        if not db_driven and len(out) >= 12:
            break

    # DB 광고주 정렬: 전환 실측으로 기준 충족(양호) → 보통 → CTR 기준(전환 미확인).
    # — CTR 점수로 미리 정렬된 순서는 CPA 기준과 무관하므로 다시 세운다.
    if db_driven:
        def _order(r: dict) -> tuple:
            if r.get('basis') == 'CPA':
                return (0 if r.get('exp_grade') == '양호' else 1, float(r['exp_cpa']))
            return (2, -float(r.get('ctr') or 0))
        out.sort(key=_order)
    out = out[:12]

    # 제안 총액을 최근 월 집행액에 맞춘다. 넘어가는 제안은 '추가 후보'로.
    acc = 0.0
    limit = monthly_budget * _BUDGET_FIT_MULT if monthly_budget else 0.0
    for i, r in enumerate(out):
        acc += float(r['budget'])
        r['overflow'] = bool(limit and i > 0 and acc > limit)
    for r in out:
        r['_meta'] = _meta
    return out


# ──────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────
_PROD_COLOR = {
    'LMS': COLOR_BLUE, '카플친': COLOR_WARNING,
    '앱푸시': COLOR_SUCCESS, 'DA': BRAND_PRIMARY, '성과형': COLOR_DANGER,
}


def _rec_card(r: dict, rank: int) -> str:
    tone = r['tone']
    _gcpa = float(r.get('_meta', {}).get('good_cpa') or GOOD_CPA)
    cpa_html = ''
    if r.get('cpa'):
        _g, _c = _cpa_grade(float(r['cpa']), _gcpa)
        cpa_html = (
            f'<div style="margin-top:6px;display:inline-flex;align-items:center;gap:6px;'
            f'background:{COLOR_BG};border-radius:6px;padding:4px 10px;">'
            f'<span style="font-size:0.62rem;color:{COLOR_TEXT_SEC};font-weight:600;">CPA</span>'
            f'<span style="font-size:0.8rem;color:{_c};font-weight:700;">'
            f'{float(r["cpa"]):,.0f}원</span>'
            f'<span style="font-size:0.62rem;color:{_c};font-weight:600;">{_g}</span>'
            f'<span style="font-size:0.62rem;color:{COLOR_TEXT_TER};">· DB {float(r.get("db") or 0):.0f}건</span>'
            f'</div>'
        )
    target_html = (
        f'<div style="background:{COLOR_BG};border-radius:6px;padding:8px 12px;'
        f'margin-top:8px;border-left:2px solid {COLOR_BORDER};">'
        f'  <div style="font-size:0.62rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
        f'letter-spacing:0.04em;font-weight:600;margin-bottom:3px;">추천 타겟</div>'
        f'  <div style="font-size:0.78rem;color:{COLOR_TEXT};font-weight:600;">'
        f'{esc_html_safe(r["target"])}</div>'
        f'</div>'
        if r.get('target') else ''
    )
    return (
        f'<div style="border:1px solid {COLOR_BORDER};border-left:3px solid {tone};'
        f'border-radius:8px;padding:14px 18px;background:{COLOR_CARD};margin-bottom:10px;">'
        # 헤더
        f'  <div style="display:flex;align-items:center;justify-content:space-between;'
        f'gap:12px;margin-bottom:8px;flex-wrap:wrap;">'
        f'    <div style="display:flex;align-items:center;gap:8px;">'
        f'      <span style="font-size:0.62rem;color:{tone};background:{COLOR_BG};'
        f'padding:2px 8px;border-radius:999px;font-weight:700;">{r["type"]}</span>'
        f'      <span style="font-size:1rem;color:{COLOR_TEXT};font-weight:700;">'
        f'{esc_html_safe(r.get("media_disp") or r["media"])}</span>'
        + (f'      <span style="font-size:0.62rem;color:{_PROD_COLOR.get(r["product"], COLOR_TEXT_SEC)};'
           f'background:{COLOR_BG};border:1px solid {COLOR_BORDER};padding:2px 7px;'
           f'border-radius:6px;font-weight:700;">{esc_html_safe(r["product"])}</span>'
           if r.get('product') else '')
        + (f'      <span style="font-size:0.6rem;color:{COLOR_TEXT_SEC};'
           f'border:1px solid {COLOR_BORDER};padding:1px 6px;border-radius:999px;'
           f'font-weight:700;" title="이 매체는 전환(DB) 실측 레퍼런스가 없어 CTR로만 추정합니다">'
           f'CTR 기준</span>' if r.get('ctr_only') else '')
        + (f'      <span style="font-size:0.6rem;color:{COLOR_TEXT_TER};'
           f'border:1px dashed {COLOR_BORDER};padding:1px 6px;border-radius:999px;"'
           f' title="최근 월 집행액을 넘는 추가 후보입니다">추가 후보</span>'
           if r.get('overflow') else '')
        + f'    </div>'
        f'    <div style="text-align:right;">'
        + (
            f'      <div style="font-size:1.25rem;color:{r.get("exp_grade_color") or tone};'
            f'font-weight:700;line-height:1;">{float(r["exp_cpa"]):,.0f}원</div>'
            f'      <div style="font-size:0.6rem;color:{COLOR_TEXT_TER};margin-top:2px;">'
            f'예상 CPA 단가'
            + (f' · <span style="color:{r["exp_grade_color"]};font-weight:700;">'
               f'{r["exp_grade"]}</span>' if r.get('exp_grade') else '')
            + f' (기준 {_gcpa:,.0f}원)</div>'
            if r.get('exp_cpa') else
            f'      <div style="font-size:1.25rem;color:{tone};font-weight:700;line-height:1;">'
            f'{r["ctr"]:.2f}%</div>'
            f'      <div style="font-size:0.6rem;color:{COLOR_TEXT_TER};margin-top:2px;">'
            f'예상 CTR · {esc_html_safe(r["ctr_src"])}</div>'
        )
        + ('' if not r.get('ctr_only') else
           f'      <div style="font-size:0.57rem;color:{COLOR_TEXT_TER};margin-top:1px;">'
           f'전환 실측 없음</div>')
        + f'    </div>'
        f'  </div>'
        # 근거
        f'  <div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};line-height:1.5;">'
        f'{esc_html_safe(r["why"])}</div>'
        f'  <div style="font-size:0.68rem;color:{COLOR_TEXT_TER};margin-top:2px;">'
        f'{esc_html_safe(r["evidence"])}'
        + (f' · 전환율 근거: {esc_html_safe(r["exp_db_src"])}' if r.get('exp_db_src') else '')
        + (f' · {esc_html_safe(r["hist_src"])}' if r.get('hist_src') else '')
        + f'</div>'
        f'  {cpa_html}'
        # 제안 수치
        f'  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;'
        f'margin-top:10px;padding-top:10px;border-top:1px dashed {COLOR_BORDER_SUBTLE};">'
        f'    <div><div style="font-size:0.6rem;color:{COLOR_TEXT_SEC};font-weight:600;">'
        + ('추천 DB 물량' if r.get('cpa_product')
           else '예상 노출' if r.get('product') == 'DA' else '추천 물량') + '</div>'
        f'<div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:700;">{r["qty"]:,}건</div></div>'
        f'    <div><div style="font-size:0.6rem;color:{COLOR_TEXT_SEC};font-weight:600;">추천 예산</div>'
        f'<div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:700;">{compact_num(r["budget"], "원")}</div></div>'
        + (
            f'    <div><div style="font-size:0.6rem;color:{COLOR_TEXT_SEC};font-weight:600;">예상 DB 인입</div>'
            f'<div style="font-size:0.85rem;color:{COLOR_SUCCESS};font-weight:700;">'
            f'{float(r["exp_db"]):.0f}건</div></div>'
            if r.get('exp_db') else
            f'    <div><div style="font-size:0.6rem;color:{COLOR_TEXT_SEC};font-weight:600;">예상 클릭</div>'
            f'<div style="font-size:0.85rem;color:{COLOR_TEXT};font-weight:700;">{r["exp_clicks"]:,}회</div></div>'
        )
        +
        f'  </div>'
        f'  {target_html}'
        f'</div>'
    )


def render(df: pd.DataFrame):
    render_page_header(
        title='미디어 믹스 제안',
        kicker='운영',
        subtitle=(
            '휴면 매체 복귀 · 동종업계 검증 신규 매체 · 성과 우수 매체 증액 · '
            '업종 신규 매체를 자동으로 추려 <b>예상 CTR · 예산 · 타겟</b>까지 제안합니다.'
        ),
    )

    if df is None or df.empty:
        render_empty_state('데이터가 없습니다', icon='📭')
        return

    # 광고주·업종 식별
    brand = ''
    for col in ('_브랜드', '광고주', '대행사'):
        if col in df.columns:
            v = df[col].dropna().astype(str)
            v = v[v.str.strip() != '']
            if not v.empty:
                brand = str(v.iloc[0])
                break
    industry = ''
    if '분야' in df.columns:
        c = df['분야'].dropna().astype(str).str.strip().value_counts()
        c = c[c.index != '']
        if not c.empty:
            industry = str(c.index[0])

    if not brand or not industry:
        render_empty_state(
            '광고주·업종 정보가 필요합니다',
            'Firebase 연동 클라이언트에서만 미디어 믹스 제안이 동작합니다.',
            icon='🎯',
        )
        return

    from modules.firebase_connector import load_from_firestore
    full = load_from_firestore()
    if full is None or full.empty:
        render_empty_state('Firebase 데이터를 불러올 수 없습니다', icon='🔌')
        return

    cache_key = f'{brand}|{industry}|{len(full)}'
    recs = build_recommendations(full, brand, industry, cache_key)
    if not recs:
        render_empty_state(
            '제안할 매체가 없습니다',
            '집행 이력과 동종업계 데이터가 더 쌓이면 자동으로 제안이 생성됩니다.',
            icon='📭',
        )
        return

    # ── 요약 + 필터 ──
    types = ['전체'] + [t for t in ['🆕 신규 진입', '📈 증액', '🔁 휴면 복귀', '✨ 신규 매체']
                        if any(r['type'] == t for r in recs)]
    col_f, col_m = st.columns([2.2, 1])
    with col_f:
        sel = st.radio('제안 유형', types, horizontal=True,
                       key='mix_type', label_visibility='collapsed')
    view = recs if sel == '전체' else [r for r in recs if r['type'] == sel]
    with col_m:
        st.markdown(
            f'<div style="text-align:right;font-size:0.75rem;color:{COLOR_TEXT_SEC};'
            f'padding-top:8px;">{esc_html_safe(brand)} · {esc_html_safe(industry)} 업종 기준</div>',
            unsafe_allow_html=True,
        )

    # 레퍼런스 범위 안내 — 어떤 기준으로 비교했는지 투명하게
    meta = recs[0].get('_meta', {}) if recs else {}
    if meta.get('use_stage'):
        stg = meta.get('my_stage', '미상')
        dist = meta.get('my_dist', {})
        dist_txt = ' · '.join(f'{k} {v}%' for k, v in list(dist.items())[:3])
        if meta.get('stage_filtered'):
            note = (f'자녀 <b>{esc_html_safe(stg)}</b> 타겟 캠페인만 레퍼런스로 비교했습니다 '
                    f'({dist_txt}). 학령이 다른 광고주 성과는 제외됩니다.')
            tone, icon = COLOR_SUCCESS, '✅'
        else:
            note = (f'자사 주력은 <b>{esc_html_safe(stg)}</b>({dist_txt})이지만 같은 학령의 '
                    f'타사 캠페인이 부족해 업종 전체를 참고했습니다. '
                    f'학령이 다른 매체가 섞일 수 있으니 타겟을 꼭 확인하세요.')
            tone, icon = COLOR_WARNING, '⚠'
        st.markdown(
            f'<div style="border:1px solid {COLOR_BORDER_SUBTLE};border-left:3px solid {tone};'
            f'border-radius:8px;padding:10px 14px;background:{COLOR_BG};margin-bottom:12px;'
            f'font-size:0.76rem;color:{COLOR_TEXT_SEC};line-height:1.5;">'
            f'{icon} {note}</div>',
            unsafe_allow_html=True,
        )
    gcpa = float(meta.get('good_cpa') or GOOD_CPA)
    base = meta.get('my_cpa_base')
    if meta.get('db_driven'):
        std_txt = ('500만원 / DB 30건' if meta.get('use_stage')
                   else '업종 중앙 CPA')
        base_txt = (f' · 참고: 이 광고주 최근 실측 평균 CPA <b>{base:,.0f}원</b>'
                    if base else '')
        n_ctr = sum(1 for r in view if r.get('ctr_only'))
        criteria = (
            f'<b>① 전환 실측이 있는 매체</b> → 예상 DB·CPA로 선별. '
            f'기준 CPA <b>{gcpa:,.0f}원</b>({std_txt}) — <b>{industry} 전체 동일 기준</b>, '
            f'브랜드별 자사 평균은 쓰지 않습니다. '
            f'{gcpa:,.0f}원 이하 <b>양호</b> / {gcpa * _CPA_POOR_MULT:,.0f}원 이내 보통 / '
            f'초과는 제외.<br>'
            f'예상 DB = 추천 물량 × <b>그 매체의 전환율 실측</b> '
            f'(자사 해당 매체·상품 → DB 수신 타사 해당 매체·상품 순). '
            f'매체마다 DB 인입 수가 10배까지 차이 나므로 상품·업종 평균은 쓰지 않습니다.<br>'
            f'<b>② 전환 실측이 없는 매체</b> → <b>예상 CTR</b>로 제시하고 '
            f'&lsquo;CTR 기준&rsquo; 표시를 답니다 (윙크·캐츠잉글리시처럼 DB를 못 받는 '
            f'광고주의 레퍼런스뿐인 경우). DB 추정치를 만들지 않고 목록 아래에 배치'
            + (f' — 현재 {n_ctr}개' if n_ctr else '')
            + base_txt
            + (f'<br>프로모션·특가로 집행했던 매체는 제안·레퍼런스에서 제외합니다 '
               f'— {", ".join(sorted(k.split("|")[-1] for k in PROMO_ONLY_REFERENCES))}'
               if PROMO_ONLY_REFERENCES else '')
        )
    else:
        criteria = (
            'DB를 전달받지 못하는 광고주라 <b>예상 CTR</b>로 선별합니다. '
            '기준: 자사 평균 CTR 이상(증액) · 동종업계 검증 CTR(신규) · '
            '과거 자사 실적 CTR(휴면 복귀)'
        )
    with st.expander('📐 선별 기준 — 이 숫자들을 어떻게 뽑았나', expanded=False):
        st.markdown(
            f'<div style="font-size:0.78rem;color:{COLOR_TEXT_SEC};line-height:1.6;">'
            f'{criteria}</div>',
            unsafe_allow_html=True,
        )

    # 추가 후보(월 집행액 초과분)는 합계에서 분리한다 — 바로 돌릴 수 있는
    # 조합이 얼마인지가 제안서의 핵심 숫자다.
    core = [r for r in view if not r.get('overflow')]
    extra = [r for r in view if r.get('overflow')]
    total_budget = sum(r['budget'] for r in core)
    total_clicks = sum(r['exp_clicks'] for r in core)
    _qsum = sum(r['qty'] for r in core)
    avg_ctr = (sum(r['ctr'] * r['qty'] for r in core) / _qsum) if _qsum else 0
    # 평균 CPA는 **전환 실측이 있는 제안끼리만** 계산한다.
    # CTR 기준 제안의 예산을 분자에 넣으면 CPA가 부풀려진다.
    db_view = [r for r in core if r.get('exp_db')]
    ctr_view = [r for r in core if not r.get('exp_db')]
    db_budget = sum(r['budget'] for r in db_view)
    db_total = sum(float(r['exp_db']) for r in db_view)
    n_good = sum(1 for r in core if r.get('exp_grade') == '양호')
    monthly = float(meta.get('monthly_budget') or 0)
    top = core[0] if core else None

    c1, c2, c3, c4 = st.columns(4)
    for col, (lab, val, sub) in zip(
        [c1, c2, c3, c4],
        [
            ('제안 매체', f'{len(core)}개',
             (f'전환 기준 {len(db_view)}개 · CTR 기준 {len(ctr_view)}개'
              + (f' · 추가 후보 {len(extra)}개' if extra else ''))
             if meta.get('db_driven') else
             (f'전체 {len(recs)}개 중' + (f' · 추가 후보 {len(extra)}개' if extra else ''))),
            ('실행 예산', compact_num(total_budget, '원'),
             (f'최근 월 집행 {compact_num(monthly, "원")} 대비 '
              f'{total_budget / monthly * 100:.0f}%') if monthly else '제안 합계'),
            (('예상 총 DB', f'{db_total:,.0f}건',
              f'평균 CPA {db_budget / max(db_total, 1):,.0f}원 · '
              f'기준 충족 {n_good}개')
             if db_view else
             ('예상 총 클릭', f'{total_clicks:,}회', f'가중 CTR {avg_ctr:.2f}%')),
            ('최우선 매체', (top.get('media_disp') or top['media']) if top else '—',
             ('' if not top else
              (f'{top.get("product", "")} · 예상 CPA {float(top["exp_cpa"]):,.0f}원'
               if top.get('exp_cpa')
               else f'{top.get("product", "")} · CTR {top["ctr"]:.2f}%'))),
        ],
    ):
        with col:
            st.markdown(
                f'<div style="border:1px solid {COLOR_BORDER};border-radius:8px;'
                f'padding:12px 14px;background:{COLOR_CARD};">'
                f'<div style="font-size:0.65rem;color:{COLOR_TEXT_SEC};text-transform:uppercase;'
                f'letter-spacing:0.04em;font-weight:600;margin-bottom:4px;">{esc_html_safe(lab)}</div>'
                f'<div style="font-size:1.05rem;color:{COLOR_TEXT};font-weight:700;line-height:1.2;">'
                f'{esc_html_safe(str(val))}</div>'
                f'<div style="font-size:0.68rem;color:{COLOR_TEXT_TER};margin-top:3px;">'
                f'{esc_html_safe(sub)}</div></div>',
                unsafe_allow_html=True,
            )

    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    # ── 한눈에 보기 ──
    def _perf_txt(r: dict) -> str:
        if r.get('exp_db'):
            return (f'DB {float(r["exp_db"]):.0f}건 · '
                    f'CPA {float(r["exp_cpa"]):,.0f}원')
        return f'CTR {r["ctr"]:.2f}% · 클릭 {r["exp_clicks"]:,}회'

    def _qty_txt(r: dict) -> str:
        if r.get('product') == 'DA':
            return f'노출 {r["qty"]:,}' if r['qty'] else '—'
        if r.get('cpa_product'):
            return f'DB {r["qty"]:,}건'
        return f'{r["qty"]:,}건'

    if view:
        st.dataframe(
            pd.DataFrame([{
                '유형': r['type'],
                '매체': r.get('media_disp') or r['media'],
                '상품': r.get('product', ''),
                '물량': _qty_txt(r),
                '예산': f'{r["budget"]:,}원',
                '예상 성과': _perf_txt(r),
                '등급': r.get('exp_grade') or ('CTR 기준' if r.get('ctr_only') else '—'),
                '비고': '추가 후보' if r.get('overflow') else '',
                '근거': r.get('exp_db_src') or r.get('ctr_src', ''),
            } for r in view]),
            hide_index=True, use_container_width=True,
        )
        st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)

    # ── 제안 카드 (섹션 분리) ──
    def _section(title: str, rows: list, desc: str = ''):
        if not rows:
            return
        st.markdown(
            f'<div style="font-size:0.9rem;font-weight:700;color:{COLOR_TEXT};'
            f'margin:4px 0 2px;">{title} <span style="font-weight:500;'
            f'color:{COLOR_TEXT_SEC};font-size:0.78rem;">{len(rows)}개</span></div>'
            + (f'<div style="font-size:0.72rem;color:{COLOR_TEXT_TER};'
               f'margin-bottom:8px;">{desc}</div>' if desc else ''),
            unsafe_allow_html=True,
        )
        for i, r in enumerate(rows, 1):
            st.markdown(_rec_card(r, i), unsafe_allow_html=True)

    if meta.get('db_driven'):
        _section('🎯 전환 기준 제안', db_view,
                 '그 매체의 DB 실측이 있어 예상 DB·CPA로 판단한 제안입니다.')
        _section('👆 CTR 기준 제안', ctr_view,
                 '전환 실측이 없는 매체입니다. DB 추정치를 만들지 않고 CTR로만 제시합니다.')
    else:
        _section('👆 제안 매체', core)

    if extra:
        with st.expander(f'➕ 추가 후보 {len(extra)}개 — 최근 월 집행액을 넘는 제안',
                         expanded=False):
            st.caption(
                f'위 실행 예산({compact_num(total_budget, "원")})에 더해 집행할 경우의 후보입니다.'
                + (f' 최근 월 집행액은 {compact_num(monthly, "원")}입니다.' if monthly else '')
            )
            for i, r in enumerate(extra, 1):
                st.markdown(_rec_card(r, i), unsafe_allow_html=True)

    _dropped = meta.get('dropped') or []
    if _dropped:
        with st.expander(f'🚫 제외한 매체 {len(_dropped)}개 — 왜 빠졌는지', expanded=False):
            st.dataframe(
                pd.DataFrame([{
                    '매체': d['media'], '유형': d['type'],
                    '상품': d.get('product') or '',
                    '예상/실측 CPA': (f'{float(d["exp_cpa"]):,.0f}원'
                                 if d.get('exp_cpa') else '—'),
                    '제외 사유': d['reason'],
                } for d in _dropped]),
                hide_index=True, use_container_width=True,
            )

    # ── 운영팀 전달용 텍스트 ──
    st.markdown('<div class="space-md"></div>', unsafe_allow_html=True)
    lines = [f'# {brand} 미디어 믹스 제안 ({industry} 업종)']
    if meta.get('db_driven'):
        lines.append(f'# 기준 CPA {gcpa:,.0f}원 이하 양호 · '
                     f'{gcpa * _CPA_POOR_MULT:,.0f}원 초과 제외 ({industry} 공통 기준)')
    lines.append('')
    for r in core + extra:
        lines.append(('[추가 후보] ' if r.get('overflow') else '')
                     + f'[{r["type"]}] {r.get("media_disp") or r["media"]}'
                     + (f' / {r["product"]}' if r.get('product') else '')
                     + (f' — {r["exp_grade"]}' if r.get('exp_grade') else '')
                     + (' (CTR 기준 — 전환 실측 없음)' if r.get('ctr_only') else ''))
        if r.get('exp_db'):
            _ql = ('DB 물량' if r.get('cpa_product')
                   else '예상 노출' if r.get('product') == 'DA' else '물량')
            lines.append(f'  - {_ql} {r["qty"]:,}건 / 예산 {r["budget"]:,}원 / '
                         f'예상 DB {float(r["exp_db"]):.0f}건 / 예상 CPA {float(r["exp_cpa"]):,.0f}원')
            if r.get('exp_db_src'):
                lines.append(f'    (전환율 근거: {r["exp_db_src"]}'
                             + (f' · {r["hist_src"]}' if r.get('hist_src') else '') + ')')
        else:
            lines.append(f'  - 물량 {r["qty"]:,}건 / 예산 {r["budget"]:,}원 / '
                         f'예상 CTR {r["ctr"]:.2f}% (예상 클릭 {r["exp_clicks"]:,}회)')
        lines.append(f'  - 근거: {r["why"]} ({r["evidence"]})')
        if r.get('target_raw'):
            tg = re.sub(r'\s*\n\s*', ' / ', str(r['target_raw']).strip())
            lines.append(f'  - 추천 타겟: {tg[:150]}')
        lines.append('')
    if db_view:
        lines.append(f'합계(실행): {len(core)}개 매체 / 예산 {total_budget:,}원 / '
                     f'예상 DB {db_total:,.0f}건 / 평균 CPA '
                     f'{db_budget / max(db_total, 1):,.0f}원')
    else:
        lines.append(f'합계(실행): {len(core)}개 매체 / 예산 {total_budget:,}원 / '
                     f'예상 클릭 {total_clicks:,}회')
    if extra:
        lines.append(f'추가 후보 {len(extra)}개 / 예산 '
                     f'{sum(r["budget"] for r in extra):,}원')
    st.caption('📋 복사용 제안서')
    st.code('\n'.join(lines), language='text')
