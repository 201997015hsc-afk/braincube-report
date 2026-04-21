"""
문구(카피) 성과 분석 모듈 (Toss-style)
────────────────────────────────────────
Firebase 메시지 데이터를 활용한 문구별 성과 분석 · 키워드 분석 · 최적 문구 전략 제안.
"어떤 문구가 효과적인가?"에 대한 데이터 기반 답변.

외부에서는 `from modules.copy_analysis import render` 형태로 사용합니다.
"""
from ._main import render

__all__ = ['render']
