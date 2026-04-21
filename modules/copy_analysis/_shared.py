"""
공용 헬퍼: HTML 이스케이프 · 메시지 정리 · Plotly 레이아웃 병합 · 기본 정규식.
"""
import re
import html as _html

from modules.config import PLOTLY_LAYOUT


_HTML_TAG_RE = re.compile(r'<[^>]+>')
_URL_RE = re.compile(r'https?://\S+')


def _esc(text: str) -> str:
    """HTML 특수문자 이스케이프"""
    return _html.escape(str(text)) if text else ""


def _clean_msg(text: str, max_len: int = 50) -> str:
    """메시지 텍스트 정리: HTML 태그 제거 → URL 축약 → 길이 제한 → 이스케이프"""
    if not isinstance(text, str) or not text.strip():
        return ""
    s = _HTML_TAG_RE.sub('', text)           # HTML 태그 제거
    s = _URL_RE.sub('[링크]', s)              # URL → [링크]
    s = re.sub(r'\s+', ' ', s).strip()        # 공백 정리
    if len(s) > max_len:
        s = s[:max_len] + '…'
    return _html.escape(s)


def _layout(**overrides) -> dict:
    """PLOTLY_LAYOUT 기반 + 오버라이드 (title 등 중복 키 안전 병합)"""
    base = dict(PLOTLY_LAYOUT)
    base.update(overrides)
    return base


# ──────────────────────────────────────────────
# 메시지 전처리 정규식 (패턴 분석·문구 정리용)
# ──────────────────────────────────────────────
# (광고), [광고], 〔광고〕 등 광고 마커
_AD_MARKER_RE = re.compile(r'[\(\[〔【《<「『]광고[\)\]〕】》>」』]')
# URL
_URL_TOKEN_RE = re.compile(r'https?://\S+|www\.\S+|bit\.ly/\S+')


def _clean_msg_raw(text: str) -> str:
    """HTML 태그 · 광고 마커 · URL만 제거 (길이 유지)"""
    if not isinstance(text, str):
        return ''
    s = re.sub(r'<[^>]+>', ' ', text)
    s = _AD_MARKER_RE.sub(' ', s)
    s = _URL_TOKEN_RE.sub(' ', s)
    return s
