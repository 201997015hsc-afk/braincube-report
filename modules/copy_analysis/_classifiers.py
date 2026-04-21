"""
메시지 분류기: 금액대 · CTA · 첫 문장 유형.
정규식 + 키워드 기반 휴리스틱.
"""
import re

from ._shared import _clean_msg_raw


# ──────────────────────────────────────────────
# 패턴 분류 헬퍼
# ──────────────────────────────────────────────
_MONEY_RE = re.compile(r'(\d[\d,]*)\s*(원|만원|억원)')
_EMOJI_RE = re.compile(
    r'[\U0001F300-\U0001F6FF\U0001F900-\U0001F9FF\U0001FA00-\U0001FAFF'
    r'\U00002600-\U000027BF\U0001F000-\U0001F02F]'
)
_NUMBER_RE = re.compile(r'\d+')
_QUESTION_RE = re.compile(r'[?？]')
_EXCLAMATION_RE = re.compile(r'[!！]')

# CTA 유형 키워드 (여러 표현 통합)
_CTA_PATTERNS = {
    '무료체험': ['무료체험', '무료 체험', '체험하기', '체험이벤트', '체험 신청'],
    '할인': ['할인', '특가', 'OFF', '%할인', '세일', '최저가', '가격인하'],
    '상품권/증정': ['상품권', '증정', '지급', '드려요', '받아가세요', '받으세요'],
    '이벤트/추첨': ['이벤트', '추첨', '경품', '당첨', '럭키드로우'],
    '한정/마감': ['한정', '마감', '마지막', '오늘까지', '까지만', '선착순'],
    '신규/오픈': ['신규', '오픈', '런칭', 'OPEN', '신상', '새로'],
    '혜택/특별': ['혜택', '특별', '프리미엄', 'VIP', '단독', '독점'],
}


# 혜택 맥락 키워드 (이 주변의 금액만 혜택으로 인정)
_REWARD_CONTEXT_KEYWORDS = (
    '증정', '드려', '드립', '받으', '받아', '드리', '지급', '적립',
    '상품권', '포인트', '쿠폰', '경품', '선물',
    '혜택', '할인', '특가', '캐시백', '페이백',
    '감사', '축하', '리워드',
)


def _classify_money_amount(text: str) -> str | None:
    """메시지에서 **혜택** 금액 추출 → 금액대로 분류.

    보험금/상품가격/대출액 등은 혜택이 아니므로 제외.
    → 혜택 맥락 키워드 주변(±25자)의 금액만 채택.
    """
    if not isinstance(text, str):
        return None

    reward_amounts = []
    for m in _MONEY_RE.finditer(text):
        val_str, unit = m.group(1), m.group(2)
        try:
            n = int(val_str.replace(',', ''))
        except ValueError:
            continue
        if unit == '원':
            won = n
        elif unit == '만원':
            won = n * 10000
        elif unit == '억원':
            won = n * 100000000
        else:
            continue

        # 혜택 맥락 검증: 금액 주변 ±25자에 혜택 키워드가 있는지
        window_start = max(0, m.start() - 25)
        window_end = min(len(text), m.end() + 25)
        window = text[window_start:window_end]
        if any(kw in window for kw in _REWARD_CONTEXT_KEYWORDS):
            reward_amounts.append(won)

    if not reward_amounts:
        return None

    # 가장 큰 혜택 금액 기준
    max_won = max(reward_amounts)
    # 비현실적 금액 필터 (10억 초과는 오탐 가능성)
    if max_won > 1_000_000_000:
        return None

    if max_won < 10000:
        return '~1만원'
    if max_won < 30000:
        return '1~3만원'
    if max_won < 50000:
        return '3~5만원'
    if max_won < 100000:
        return '5~10만원'
    return '10만원+'


def _classify_cta(text: str) -> list[str]:
    """메시지에 포함된 CTA 유형 리스트 반환 (복수 가능)"""
    if not isinstance(text, str):
        return []
    found = []
    for cta, keywords in _CTA_PATTERNS.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                found.append(cta)
                break
    return found


def _first_sentence_type(text: str) -> str:
    """첫 문장(또는 첫 줄) 유형 분류"""
    if not isinstance(text, str) or not text.strip():
        return '기타'
    # HTML 태그 / 광고 마커 제거 후 첫 줄
    cleaned = _clean_msg_raw(text)
    # 첫 문장 = 첫 줄의 첫 . ! ? 기준
    first_line = cleaned.split('\n')[0].strip()
    # 첫 문장 추출 (마침표 등까지)
    sentence_end = min((i for i in (
        first_line.find('.'), first_line.find('!'), first_line.find('?')
    ) if i > 0), default=len(first_line))
    first = first_line[:sentence_end + 1] if sentence_end < len(first_line) else first_line
    first = first.strip()
    if not first:
        return '기타'
    # 분류 규칙 (우선순위: 질문 > 혜택강조 > 긴급 > 행동요청 > 단정)
    if '?' in first or '？' in first:
        return '질문형'
    if any(kw in first for kw in ['만원', '할인', '증정', '무료', '드려', '혜택', '%', '선물', '상품권', '원 증정']):
        return '혜택강조형'
    if any(kw in first for kw in ['지금', '오늘', '마감', '한정', '서둘러', '놓치', '마지막']):
        return '긴급형'
    # 끝맺음이 명령/권유형 어미
    if any(first.rstrip('.!? ').endswith(e) for e in ['세요', '하세요', '해보세요', '주세요', '받으세요']):
        return '행동요청형'
    # 기본: 단정형
    return '단정형'
