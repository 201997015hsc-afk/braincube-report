"""
Toss-style 디자인 시스템 + 상수 + UI 헬퍼 모듈
"""
import html as _html
import re as _re

import streamlit as st


# ──────────────────────────────────────────────
# 보안 유틸 — 사용자 입력 HTML 이스케이프
# ──────────────────────────────────────────────
def esc_html(text) -> str:
    """사용자 입력을 HTML 이스케이프. None/비문자열도 안전 처리.

    st.markdown(..., unsafe_allow_html=True)에 사용자 입력을 삽입할 때
    필수로 거쳐야 하는 함수. XSS (<script>, <img onerror=>) 등 차단.
    """
    if text is None:
        return ""
    return _html.escape(str(text), quote=True)


def sanitize_input(text, max_len: int = 200) -> str:
    """사용자 입력 정제 — 이스케이프 + 길이 제한 + 제어 문자 제거.

    회원가입·클라이언트 등록 등 저장 전 입력 검증에 사용.
    """
    if text is None:
        return ""
    s = str(text).strip()
    # 제어 문자 제거 (\x00-\x1f 중 \t\n 제외)
    s = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', s)
    # 길이 제한
    if len(s) > max_len:
        s = s[:max_len]
    return s

# ──────────────────────────────────────────────
# 브랜드 & 색상
# ──────────────────────────────────────────────
BRAND_PRIMARY = "#F7931D"
CHART_COLORS = ['#F7931D', '#3182F6', '#20C997', '#FF6B6B', '#845EF7', '#FFA726', '#42A5F5', '#66BB6A']

COLOR_TEXT = "#111827"
COLOR_TEXT_SEC = "#6B7280"
COLOR_TEXT_TER = "#4B5563"
COLOR_BG = "#FAFBFC"          # Linear 느낌의 아주 옅은 배경
COLOR_CARD = "#FFFFFF"
COLOR_BORDER = "#E5E7EB"      # Linear/Notion 표준 보더
COLOR_BORDER_SUBTLE = "#F3F4F6"
COLOR_DANGER = "#EF4444"
COLOR_SUCCESS = "#10B981"
COLOR_WARNING = "#F59E0B"
COLOR_BLUE = "#3182F6"

# ──────────────────────────────────────────────
# 데이터 컬럼 정의
# ──────────────────────────────────────────────
REQUIRED_COLS = ['날짜', '집행금액', '발송량', '클릭수']
TARGET_METRICS = ['집행금액', '발송량', '클릭수']

COL_MAPPING = {
    '날짜': ['발송일', '날짜', '일자', 'date', 'Date', '발송일자', '발송일시', '기준일'],
    '매체명': ['발송구분', '매체', '매체명', '채널', 'Media', '구분', '발송매체'],
    '집행금액': ['비용', '집행금액', '소진액', '광고비', 'Cost', '사용금액', '총비용', '금액'],
    '발송량': ['발송건수', '발송량', '노출수', '성공건수', '발송', '성공', '총발송건수', '발송수'],
    '클릭수': ['클릭', '클릭수', 'Click', '유입수', '클릭건수'],
}

WEEKDAY_MAP = {0: '월', 1: '화', 2: '수', 3: '목', 4: '금', 5: '토', 6: '일'}
WEEKDAY_ORDER_FULL = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
WMA_WEIGHTS = [0.2, 0.3, 0.5]

# ──────────────────────────────────────────────
# 분석 임계값 (매직넘버 → 상수)
# ──────────────────────────────────────────────
CTR_CHANGE_THRESHOLD = 0.3       # %p — CTR 변동 유의 기준
VOLUME_CHANGE_THRESHOLD = 10     # %  — 발송량 변동 유의 기준
ANOMALY_Z_BINS = [2.0, 3.0, 4.0, float('inf')]
ANOMALY_Z_LABELS = ['주의', '경고', '심각']
BUDGET_TOP_N = 8                 # 예산 배분 차트 최대 매체 수
PREDICTION_MIN_MONTHS = 3        # 예측 최소 데이터 개월 수
BACKTEST_MIN_MONTHS = 4          # 백테스트 최소 개월 수

# ── 통계적 신뢰도 기준 (인사이트 생성 시 필수 검증) ──
MIN_SAMPLES_PER_CATEGORY = 8     # 카테고리(금액대/CTA/매체 등)당 최소 캠페인 수
MIN_SAMPLES_FOR_QUARTILE = 12    # 사분위 분석 최소 표본 (q1 ≥ 3 확보)
MIN_SAMPLES_FEATURE_SPLIT = 10   # 이모지/숫자 포함 vs 미포함 분리 시 각 그룹 최소
MIN_SENDS_FOR_CTR_CLAIM = 5000   # CTR 기반 주장(우수/저조) 최소 발송량
MIN_LIFT_RELATIVE = 0.15         # 상대 효과 크기 최소 (15% lift)
MIN_PEERS_FOR_RANKING = 10       # 업종 내 순위·등급 산정 최소 경쟁사 수
CTR_MIN_FLOOR = 0.1              # %  — CTR 비율 계산 시 분모 하한 (0 근처 폭발 방지)

# 카드사·통신사 매체 식별 (예산 배분 제약 조건용)
# ⚠ 리스트 확장 시 주의: is_card_or_telecom()은 부분 매칭(substring)이므로
#   너무 일반적인 키워드(예: '포인트')는 의도치 않은 매칭을 일으킬 수 있음.
CARD_TELECOM_KEYWORDS = [
    '카드', '신한', '삼성카드', 'KB', '현대카드', '롯데카드', 'BC', '하나카드',
    'NH', '우리카드', '비씨',
    'SKT', 'SK텔레콤', 'KT', 'LG', 'LGU', '통신', '알뜰',
    '신세계',  # 신세계포인트·신세계상품권 등 (최소 5만건 / 500만원 제약)
]
MIN_SEND_CARD_TELECOM = 50_000       # 카드사·통신사 최소 발송 수량
MIN_BUDGET_CARD_TELECOM = 5_000_000  # 카드사·통신사 최소 예산 (500만원)


def is_card_or_telecom(media_name: str) -> bool:
    """매체명이 카드사·통신사에 해당하는지 판별"""
    name = media_name.upper()
    return any(kw.upper() in name for kw in CARD_TELECOM_KEYWORDS)


# ──────────────────────────────────────────────
# Plotly 차트 공통 레이아웃 (Toss-style)
# ──────────────────────────────────────────────
PLOTLY_LAYOUT = dict(
    template='plotly_white',
    font=dict(family='Pretendard, Inter, -apple-system, sans-serif', size=12, color=COLOR_TEXT_TER),
    paper_bgcolor='rgba(0,0,0,0)',
    plot_bgcolor='rgba(0,0,0,0)',
    margin=dict(t=48, l=44, r=20, b=44),
    title=dict(text="", font=dict(size=14, color=COLOR_TEXT, family='Pretendard, Inter, sans-serif'), x=0, xanchor='left'),
    xaxis=dict(showgrid=False, linecolor=COLOR_BORDER, tickfont=dict(size=11, color=COLOR_TEXT_SEC), title=""),
    yaxis=dict(gridcolor=COLOR_BORDER_SUBTLE, gridwidth=1, showline=False, tickfont=dict(size=11, color=COLOR_TEXT_SEC), title=""),
    legend=dict(font=dict(size=11), bgcolor='rgba(0,0,0,0)'),
    hoverlabel=dict(bgcolor=COLOR_TEXT, font_color='white', font_size=12, bordercolor=COLOR_TEXT),
)


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
def get_media_color(media_name: str, all_media: list[str]) -> str:
    return CHART_COLORS[all_media.index(media_name) % len(CHART_COLORS)]


def compact_num(v: float, unit: str = "") -> str:
    """큰 숫자 축약: 123,456,789 → 1.2억원 / 12,345 → 1만건"""
    if abs(v) >= 1_0000_0000:
        return f"{v / 1_0000_0000:,.1f}억{unit}"
    if abs(v) >= 1_0000:
        return f"{v / 1_0000:,.0f}만{unit}"
    return f"{v:,.0f}{unit}"


# ──────────────────────────────────────────────
# HTML UI 컴포넌트 (Toss-style)
# ──────────────────────────────────────────────
def section_header(title: str, desc: str = "") -> str:
    desc_html = f'<div class="desc">{desc}</div>' if desc else ""
    return f'<div class="section-header"><div class="title">{title}</div>{desc_html}</div>'


def kpi_card_html(label: str, value: str, delta: str = "", delta_type: str = "") -> str:
    delta_html = ""
    if delta:
        cls = "up" if delta_type == "up" else ("down" if delta_type == "down" else "")
        arrow = "↑ " if delta_type == "up" else ("↓ " if delta_type == "down" else "")
        delta_html = f'<div class="delta {cls}">{arrow}{delta}</div>'
    return f'<div class="toss-kpi"><div class="label">{label}</div><div class="value">{value}</div>{delta_html}</div>'


def insight_card_html(title: str, value: str, desc: str, color: str) -> str:
    return f'<div class="toss-insight"><div class="icon-dot" style="background:{color}"></div><div class="title">{title}</div><div class="highlight" style="color:{color}">{value}</div><div class="desc">{desc}</div></div>'


def alert_card_html(alert_type: str, title: str, body: str) -> str:
    return f'<div class="toss-alert {alert_type}"><div class="alert-title">{title}</div><div class="alert-body">{body}</div></div>'


# ──────────────────────────────────────────────
# CSS (Toss-style Design System v2)
# ──────────────────────────────────────────────
_TOSS_CSS = f"""
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css');

/* ===== Foundation ===== */
.stApp {{
    background-color: {COLOR_BG};
    font-family: 'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, system-ui, Roboto, sans-serif;
}}
::selection {{
    background: rgba(247, 147, 29, 0.12);
    color: {COLOR_TEXT};
}}
/* Linear/Notion 감성 — 볼드 줄이고 letter-spacing 차분하게 */
h1 {{
    color: {COLOR_TEXT} !important;
    font-weight: 600 !important;
    font-size: 1.6rem !important;
    letter-spacing: -0.02em;
    line-height: 1.3 !important;
}}
h2 {{
    color: {COLOR_TEXT} !important;
    font-weight: 600 !important;
    font-size: 1.2rem !important;
    letter-spacing: -0.015em;
}}
h3 {{
    color: {COLOR_TEXT} !important;
    font-weight: 600 !important;
    font-size: 1.05rem !important;
    letter-spacing: -0.01em;
}}

/* ===== Custom Scrollbar ===== */
::-webkit-scrollbar {{
    width: 6px;
    height: 6px;
}}
::-webkit-scrollbar-track {{
    background: transparent;
}}
::-webkit-scrollbar-thumb {{
    background: rgba(0,0,0,0.08);
    border-radius: 3px;
}}
::-webkit-scrollbar-thumb:hover {{
    background: rgba(0,0,0,0.15);
}}

/* ===== Sidebar ===== */
section[data-testid="stSidebar"] {{
    background: #F8F9FB;
    border-right: 1px solid {COLOR_BORDER};
}}
section[data-testid="stSidebar"] .stMarkdown p {{
    color: {COLOR_TEXT_TER};
    font-size: 0.88rem;
}}
.sidebar-brand {{
    text-align: center;
    padding: 20px 16px 20px 16px;
    border-bottom: 1px solid {COLOR_BORDER};
    margin-bottom: 20px;
    background: transparent;
}}
.sidebar-brand .name {{
    font-size: 1.2rem;
    font-weight: 800;
    color: {COLOR_TEXT};
    letter-spacing: -0.02em;
}}
.sidebar-brand .sub {{
    font-size: 0.72rem;
    color: {COLOR_TEXT_SEC};
    margin-top: 4px;
    letter-spacing: 0.03em;
    text-transform: uppercase;
}}
.sidebar-label {{
    font-size: 0.68rem;
    font-weight: 700;
    color: {COLOR_TEXT_SEC};
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-bottom: 10px;
    margin-top: 28px;
    padding-left: 2px;
}}

/* ===== Sidebar Navigation ===== */
section[data-testid="stSidebar"] .stRadio > div {{
    gap: 1px !important;
}}
section[data-testid="stSidebar"] .stRadio label {{
    border-radius: 10px !important;
    padding: 7px 12px !important;
    font-size: 0.88rem !important;
    transition: background 0.15s ease !important;
}}
section[data-testid="stSidebar"] .stRadio label:hover {{
    background: rgba(247, 147, 29, 0.06) !important;
}}
section[data-testid="stSidebar"] .stRadio label[data-baseweb="radio"]:has(input:checked) {{
    background: rgba(247, 147, 29, 0.10) !important;
    font-weight: 700 !important;
}}

/* ===== Metric Cards (st.metric) ===== */
[data-testid="metric-container"] {{
    background: {COLOR_CARD};
    border: 1px solid {COLOR_BORDER};
    border-radius: 10px;
    padding: 18px 20px;
    box-shadow: none;
    transition: border-color 0.15s ease;
}}
[data-testid="metric-container"]:hover {{
    border-color: #D1D5DB;
}}
[data-testid="stMetricLabel"] {{
    color: {COLOR_TEXT_SEC} !important;
    font-size: 0.8rem !important;
}}
[data-testid="stMetricValue"] {{
    font-weight: 800 !important;
    color: {COLOR_TEXT} !important;
    letter-spacing: -0.03em;
}}

/* ===== Custom KPI Cards — Linear/Notion 스타일 (플랫·섬세 보더) ===== */
.toss-kpi {{
    background: {COLOR_CARD};
    border: 1px solid {COLOR_BORDER};
    border-radius: 10px;
    padding: 20px 20px 18px 20px;
    text-align: left;
    box-shadow: none;
    transition: border-color 0.15s ease;
    position: relative;
}}
.toss-kpi:hover {{
    border-color: #D1D5DB;
}}
.toss-kpi .label {{
    font-size: 0.78rem;
    color: {COLOR_TEXT_SEC};
    font-weight: 500;
    margin-bottom: 10px;
    letter-spacing: -0.005em;
}}
.toss-kpi .value {{
    font-size: 1.75rem;
    font-weight: 600;
    color: {COLOR_TEXT};
    letter-spacing: -0.02em;
    line-height: 1.15;
    font-feature-settings: "tnum";
}}
.toss-kpi .delta {{
    display: inline-flex;
    align-items: center;
    gap: 4px;
    font-size: 0.76rem;
    font-weight: 500;
    margin-top: 10px;
    color: {COLOR_TEXT_SEC};
    padding: 2px 8px;
    border-radius: 6px;
    background: {COLOR_BG};
}}
.toss-kpi .delta.up {{
    color: {COLOR_SUCCESS};
    background: rgba(16,185,129,0.10);
}}
.toss-kpi .delta.down {{
    color: {COLOR_DANGER};
    background: rgba(239,68,68,0.10);
}}

/* ===== Insight Cards — Linear 감성 ===== */
.toss-insight {{
    background: {COLOR_CARD};
    border: 1px solid {COLOR_BORDER};
    border-radius: 10px;
    padding: 22px 20px;
    text-align: left;
    box-shadow: none;
    min-height: 140px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    transition: border-color 0.15s ease;
    position: relative;
}}
.toss-insight:hover {{
    border-color: #D1D5DB;
}}
.toss-insight .icon-dot {{
    width: 6px;
    height: 6px;
    border-radius: 50%;
    margin-bottom: 10px;
    opacity: 0.9;
}}
.toss-insight .title {{
    font-size: 0.78rem;
    color: {COLOR_TEXT_SEC};
    font-weight: 500;
    margin-bottom: 8px;
}}
.toss-insight .highlight {{
    font-size: 1.1rem;
    font-weight: 600;
    letter-spacing: -0.015em;
    margin-bottom: 8px;
    line-height: 1.35;
}}
.toss-insight .desc {{
    font-size: 0.82rem;
    color: {COLOR_TEXT_TER};
    line-height: 1.55;
}}

/* ===== Alert Cards — Linear 감성 (플랫·얇은 보더·은은한 배경) ===== */
.toss-alert {{
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 12px;
    background: {COLOR_CARD};
    border: 1px solid {COLOR_BORDER};
    border-left: 3px solid {COLOR_BORDER};
    box-shadow: none;
    transition: border-color 0.15s ease;
}}
.toss-alert:hover {{
    border-color: #D1D5DB;
}}
.toss-alert.danger {{
    background: rgba(239,68,68,0.04);
    border-left-color: {COLOR_DANGER};
}}
.toss-alert.success {{
    background: rgba(16,185,129,0.04);
    border-left-color: {COLOR_SUCCESS};
}}
.toss-alert.warning {{
    background: rgba(245,158,11,0.05);
    border-left-color: {COLOR_WARNING};
}}
.toss-alert.info {{
    background: rgba(49,130,246,0.04);
    border-left-color: {COLOR_BLUE};
}}
.toss-alert .alert-title {{
    font-weight: 600;
    font-size: 0.92rem;
    color: {COLOR_TEXT};
    margin-bottom: 6px;
    letter-spacing: -0.005em;
}}
.toss-alert .alert-body {{
    font-size: 0.84rem;
    color: {COLOR_TEXT_TER};
    line-height: 1.65;
}}

/* ===== Section Header — Linear 감성 (깔끔 · 세로 바 제거) ===== */
.section-header {{
    margin-bottom: 20px;
    padding: 6px 0 8px 0;
    border-bottom: 1px solid {COLOR_BORDER_SUBTLE};
}}
.section-header .title {{
    font-size: 1.2rem;
    font-weight: 600;
    color: {COLOR_TEXT};
    letter-spacing: -0.015em;
    margin-bottom: 4px;
}}
.section-header .desc {{
    font-size: 0.82rem;
    color: {COLOR_TEXT_SEC};
    line-height: 1.55;
    margin-bottom: 4px;
}}

/* ===== Tabs ===== */
.stTabs [data-baseweb="tab-list"] {{
    gap: 0;
    background-color: transparent;
    border-bottom: 2px solid {COLOR_BORDER};
    padding-bottom: 0;
}}
.stTabs [data-baseweb="tab"] {{
    border-radius: 0;
    padding: 10px 18px;
    color: {COLOR_TEXT_SEC};
    font-weight: 500;
    font-size: 0.85rem;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    transition: color 0.15s ease, border-color 0.15s ease;
}}
.stTabs [data-baseweb="tab"]:hover {{
    color: {COLOR_TEXT};
    background: transparent;
}}
.stTabs [aria-selected="true"] {{
    color: {COLOR_TEXT} !important;
    border-bottom: 2px solid {BRAND_PRIMARY};
    background: transparent;
    font-weight: 600;
}}

/* ===== Buttons — Linear 감성 ===== */
.stDownloadButton > button {{
    background: {COLOR_CARD} !important;
    border: 1px solid {COLOR_BORDER} !important;
    border-radius: 8px !important;
    color: {COLOR_TEXT} !important;
    font-weight: 500 !important;
    font-size: 0.85rem !important;
    padding: 8px 16px !important;
    transition: border-color 0.15s ease, background 0.15s ease;
    box-shadow: none;
}}
.stDownloadButton > button:hover {{
    background: #FAFBFC !important;
    border-color: #D1D5DB !important;
    color: {COLOR_TEXT} !important;
}}

/* ===== Divider ===== */
hr {{
    border: none !important;
    border-top: 1px solid {COLOR_BORDER_SUBTLE} !important;
    margin: 32px 0 !important;
}}

/* ===== Radio/Select ===== */
.stRadio > div {{ gap: 6px; }}

/* ===== Plotly chart container — Linear 감성 (얇은 보더) ===== */
[data-testid="stPlotlyChart"] {{
    background: {COLOR_CARD};
    border: 1px solid {COLOR_BORDER};
    border-radius: 10px;
    padding: 10px 8px;
    box-shadow: none;
    transition: border-color 0.15s ease;
}}
[data-testid="stPlotlyChart"]:hover {{
    border-color: #D1D5DB;
}}

/* ===== Slider ===== */
.stSlider [data-baseweb="slider"] [role="slider"] {{
    background-color: {BRAND_PRIMARY};
    border: 2px solid white;
    box-shadow: 0 2px 6px rgba(247,147,29,0.25);
}}
.stSlider [data-baseweb="slider"] div[data-testid="stTickBarMin"],
.stSlider [data-baseweb="slider"] div[data-testid="stTickBarMax"] {{
    font-size: 0.78rem;
    color: {COLOR_TEXT_SEC};
}}

/* ===== Selectbox / Input ===== */
.stSelectbox [data-baseweb="select"] > div,
.stTextInput > div > div > input,
.stNumberInput > div > div > input {{
    border-radius: 12px !important;
    border-color: {COLOR_BORDER} !important;
    transition: border-color 0.2s ease, box-shadow 0.2s ease;
}}
.stSelectbox [data-baseweb="select"] > div:focus-within,
.stTextInput > div > div > input:focus,
.stNumberInput > div > div > input:focus {{
    border-color: {BRAND_PRIMARY} !important;
    box-shadow: 0 0 0 3px rgba(247,147,29,0.08) !important;
}}
/* 타이핑 차단 시각 신호 (JS readonly 주입의 CSS 폴백) */
.stSelectbox [data-baseweb="select"] input,
.stMultiSelect [data-baseweb="select"] input {{
    caret-color: transparent !important;
    cursor: pointer !important;
}}
/* "Press Enter to apply" 힌트 숨김 — 입력 텍스트와 겹침 방지 */
[data-testid="InputInstructions"],
[data-testid="stWidgetInstructions"] {{
    display: none !important;
}}

/* ===== Multiselect ===== */
.stMultiSelect [data-baseweb="tag"] {{
    border-radius: 8px;
    background: rgba(247,147,29,0.08);
    color: #E67E00;
    font-weight: 600;
}}

/* ===== Expander ===== */
[data-testid="stExpander"] {{
    border-radius: 16px !important;
    border: none !important;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04) !important;
    background: {COLOR_CARD};
    overflow: hidden;
}}
[data-testid="stExpander"] summary {{
    font-weight: 600;
}}

/* ===== Alert overrides ===== */
[data-testid="stAlert"] {{
    border-radius: 14px;
    border: none;
    box-shadow: 0 1px 4px rgba(0,0,0,0.04);
}}

/* ===== Data Banner ===== */
.data-banner {{
    background: linear-gradient(135deg, #F0FFF4 0%, #F0F7FF 100%);
    border: none;
    border-radius: 16px;
    padding: 16px 24px;
    margin-bottom: 28px;
    display: flex;
    align-items: center;
    gap: 14px;
    box-shadow: 0 2px 8px rgba(0,200,83,0.08);
}}
.data-banner .badge {{
    background: linear-gradient(135deg, {COLOR_SUCCESS}, #00E676);
    color: white;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 4px 12px;
    border-radius: 8px;
    letter-spacing: 0.02em;
    flex-shrink: 0;
}}
.data-banner .info {{
    color: {COLOR_TEXT_TER};
    font-size: 0.88rem;
    font-weight: 500;
}}
.data-banner .info b {{
    color: {COLOR_TEXT};
}}

/* ===== Budget List Cards ===== */
.budget-card {{
    display: flex;
    align-items: center;
    padding: 14px 18px;
    margin-bottom: 8px;
    background: {COLOR_CARD};
    border: none;
    border-radius: 14px;
    box-shadow: 0 1px 4px rgba(0,0,0,0.03);
    transition: all 0.25s cubic-bezier(0.4,0,0.2,1);
}}
.budget-card:hover {{
    box-shadow: 0 6px 20px rgba(0,0,0,0.06);
    transform: translateX(4px);
}}
.budget-dot {{
    width: 12px;
    height: 12px;
    border-radius: 4px;
    margin-right: 14px;
    flex-shrink: 0;
}}
.budget-info {{
    flex: 1;
    min-width: 0;
}}
.budget-name {{
    font-weight: 700;
    font-size: 0.9rem;
    color: {COLOR_TEXT};
    margin-bottom: 6px;
}}
.budget-bar-bg {{
    background: {COLOR_BG};
    border-radius: 4px;
    height: 6px;
    width: 100%;
    overflow: hidden;
}}
.budget-bar-fill {{
    border-radius: 4px;
    height: 6px;
    transition: width 0.4s ease;
}}
.budget-stats {{
    text-align: right;
    margin-left: 18px;
    flex-shrink: 0;
}}
.budget-pct {{
    font-weight: 800;
    font-size: 0.95rem;
    color: {COLOR_TEXT};
}}
.budget-detail {{
    font-size: 0.76rem;
    color: {COLOR_TEXT_TER};
    margin-top: 2px;
}}

/* ===== Anomaly Count Badge ===== */
.anomaly-badge {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 14px;
    border-radius: 10px;
    font-size: 0.82rem;
    font-weight: 700;
    margin-bottom: 12px;
}}
.anomaly-badge.found {{
    background: #FFF5F5;
    color: {COLOR_DANGER};
}}
.anomaly-badge.clear {{
    background: #F0FFF4;
    color: {COLOR_SUCCESS};
}}

/* ===== Hero Header ===== */
.hero-header {{
    padding: 8px 0 4px 0;
}}
.hero-header .title {{
    font-size: 1.75rem;
    font-weight: 800;
    color: {COLOR_TEXT};
    letter-spacing: -0.03em;
    line-height: 1.3;
    margin-bottom: 6px;
}}
.hero-header .title .accent {{
    background: linear-gradient(90deg, {BRAND_PRIMARY}, #FF6B6B);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}}
.hero-header .subtitle {{
    font-size: 0.88rem;
    color: {COLOR_TEXT_SEC};
    font-weight: 500;
    line-height: 1.5;
}}

/* ===== Empty State ===== */
.empty-state {{
    text-align: center;
    padding: 80px 20px;
    color: {COLOR_TEXT_SEC};
}}
.empty-state .icon {{
    font-size: 3rem;
    margin-bottom: 16px;
    opacity: 0.6;
}}
.empty-state .msg {{
    font-size: 1.05rem;
    font-weight: 600;
    color: {COLOR_TEXT_TER};
    margin-bottom: 8px;
}}
.empty-state .sub {{
    font-size: 0.85rem;
    color: {COLOR_TEXT_SEC};
}}

/* ===== Insight Chips ===== */
.insight-chip {{
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 6px;
    transition: all 0.25s ease;
}}
.insight-chip:hover {{
    transform: translateY(-2px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.06);
}}
.insight-chip .ic-fact {{
    font-size: 0.82rem;
    font-weight: 700;
    color: #191F28;
    line-height: 1.4;
}}
.insight-chip .ic-detail {{
    font-size: 0.72rem;
    color: {COLOR_TEXT_TER};
    margin-bottom: 4px;
    padding-left: 2px;
}}
.insight-chip .ic-action {{
    font-size: 0.78rem;
    color: {COLOR_TEXT_SEC};
    margin-top: 8px;
    padding-left: 10px;
    border-left-width: 2px;
    border-left-style: solid;
    font-weight: 600;
}}

/* ===== Fade-in animation ===== */
@keyframes fadeInUp {{
    from {{ opacity: 0; transform: translateY(12px); }}
    to   {{ opacity: 1; transform: translateY(0); }}
}}
.toss-kpi, .toss-insight, .toss-alert {{
    animation: fadeInUp 0.45s cubic-bezier(0.4,0,0.2,1) both;
}}
.toss-kpi:nth-child(2) {{ animation-delay: 0.05s; }}
.toss-kpi:nth-child(3) {{ animation-delay: 0.1s; }}
.toss-kpi:nth-child(4) {{ animation-delay: 0.15s; }}

/* ===== Subtle section spacing ===== */
[data-testid="stVerticalBlock"] > [data-testid="stVerticalBlockBorderWrapper"] {{
    margin-bottom: 4px;
}}

/* ===== Prediction card spacing ===== */
[data-testid="stHorizontalBlock"] [data-testid="stPlotlyChart"] {{
    margin-bottom: 6px;
}}
[data-testid="stHorizontalBlock"] .toss-kpi {{
    margin-top: 4px;
    padding: 22px 16px 20px 16px;
}}

/* ===== Client Selector Badge ===== */
.client-badge {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 5px 12px;
    border-radius: 10px;
    font-size: 0.76rem;
    font-weight: 700;
    margin-bottom: 4px;
}}
.client-badge.active {{
    background: rgba(247, 147, 29, 0.10);
    color: #E67E00;
}}
.client-badge.none {{
    background: {COLOR_BG};
    color: {COLOR_TEXT_SEC};
}}

/* ===== Sidebar Client Section ===== */
section[data-testid="stSidebar"] [data-testid="stExpander"] {{
    border-radius: 12px !important;
    margin-bottom: 8px;
}}
section[data-testid="stSidebar"] [data-testid="stExpander"] summary {{
    font-size: 0.84rem;
    padding: 8px 12px;
}}

/* selectbox 드롭다운 커스텀 CSS는 Streamlit 내부 구조와 충돌이 많아 제거.
   검색창 위치/스타일은 기본 동작 유지. */
</style>
"""


def apply_page_style(skip_page_config: bool = False):
    if not skip_page_config:
        st.set_page_config(
            page_title="LMS Analytics Dashboard",
            page_icon="📊",
            layout="wide",
            initial_sidebar_state="expanded",
        )
    st.markdown(_TOSS_CSS, unsafe_allow_html=True)
