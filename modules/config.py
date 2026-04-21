"""
Toss-style 디자인 시스템 + 상수 + UI 헬퍼 모듈
"""
import streamlit as st

# ──────────────────────────────────────────────
# 브랜드 & 색상
# ──────────────────────────────────────────────
BRAND_PRIMARY = "#F7931D"
CHART_COLORS = ['#F7931D', '#3182F6', '#20C997', '#FF6B6B', '#845EF7', '#FFA726', '#42A5F5', '#66BB6A']

COLOR_TEXT = "#191F28"
COLOR_TEXT_SEC = "#8B95A1"
COLOR_TEXT_TER = "#4E5968"
COLOR_BG = "#F7F8FA"
COLOR_CARD = "#FFFFFF"
COLOR_BORDER = "#EBEEF2"
COLOR_DANGER = "#F44336"
COLOR_SUCCESS = "#00C853"
COLOR_WARNING = "#FF9100"
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
CARD_TELECOM_KEYWORDS = [
    '카드', '신한', '삼성카드', 'KB', '현대카드', '롯데카드', 'BC', '하나카드',
    'NH', '우리카드', '비씨',
    'SKT', 'SK텔레콤', 'KT', 'LG', 'LGU', '통신', '알뜰',
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
    font=dict(family='Pretendard, -apple-system, sans-serif', size=12, color=COLOR_TEXT_TER),
    paper_bgcolor='rgba(0,0,0,0)',
    plot_bgcolor='rgba(0,0,0,0)',
    margin=dict(t=56, l=48, r=24, b=48),
    title=dict(text="", font=dict(size=15, color=COLOR_TEXT, family='Pretendard, sans-serif'), x=0, xanchor='left'),
    xaxis=dict(showgrid=False, linecolor='#EBEEF2', tickfont=dict(size=11, color=COLOR_TEXT_SEC), title=""),
    yaxis=dict(gridcolor='#F0F1F3', gridwidth=1, showline=False, tickfont=dict(size=11, color=COLOR_TEXT_SEC), title=""),
    legend=dict(font=dict(size=11), bgcolor='rgba(0,0,0,0)'),
    hoverlabel=dict(bgcolor='#191F28', font_color='white', font_size=12, bordercolor='#191F28'),
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
h1 {{
    color: {COLOR_TEXT} !important;
    font-weight: 800 !important;
    font-size: 1.75rem !important;
    letter-spacing: -0.03em;
    line-height: 1.35 !important;
}}
h2 {{
    color: {COLOR_TEXT} !important;
    font-weight: 700 !important;
    font-size: 1.3rem !important;
    letter-spacing: -0.02em;
}}
h3 {{
    color: {COLOR_TEXT} !important;
    font-weight: 700 !important;
    font-size: 1.1rem !important;
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
    background: linear-gradient(180deg, #FFFFFF 0%, #F8F9FB 100%);
    border-right: 1px solid {COLOR_BORDER};
}}
section[data-testid="stSidebar"] .stMarkdown p {{
    color: {COLOR_TEXT_TER};
    font-size: 0.88rem;
}}
.sidebar-brand {{
    text-align: center;
    padding: 24px 16px 28px 16px;
    border-bottom: 1px solid {COLOR_BORDER};
    margin-bottom: 24px;
    background: linear-gradient(135deg, rgba(247,147,29,0.04) 0%, rgba(49,130,246,0.04) 100%);
    border-radius: 0 0 20px 20px;
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
    border: none;
    border-radius: 20px;
    padding: 22px 24px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04), 0 0.5px 1px rgba(0,0,0,0.02);
    transition: transform 0.25s cubic-bezier(0.4,0,0.2,1), box-shadow 0.25s cubic-bezier(0.4,0,0.2,1);
}}
[data-testid="metric-container"]:hover {{
    transform: translateY(-2px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.06), 0 2px 4px rgba(0,0,0,0.03);
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

/* ===== Custom KPI Cards ===== */
.toss-kpi {{
    background: {COLOR_CARD};
    border-radius: 20px;
    padding: 32px 20px 28px 20px;
    text-align: center;
    border: none;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04), 0 0.5px 1px rgba(0,0,0,0.02);
    position: relative;
    overflow: hidden;
    transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
}}
.toss-kpi:hover {{
    transform: translateY(-4px);
    box-shadow: 0 16px 32px rgba(0,0,0,0.08), 0 4px 8px rgba(0,0,0,0.04);
}}
.toss-kpi::before {{
    content: '';
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 3px;
    background: linear-gradient(90deg, {BRAND_PRIMARY}, #FF6B6B, {COLOR_BLUE});
    opacity: 0.85;
}}
.toss-kpi .label {{
    font-size: 0.76rem;
    color: {COLOR_TEXT_SEC};
    font-weight: 600;
    margin-bottom: 14px;
    letter-spacing: -0.01em;
}}
.toss-kpi .value {{
    font-size: 1.85rem;
    font-weight: 800;
    color: {COLOR_TEXT};
    letter-spacing: -0.04em;
    line-height: 1.15;
}}
.toss-kpi .delta {{
    display: inline-block;
    font-size: 0.78rem;
    font-weight: 600;
    margin-top: 10px;
    color: {COLOR_TEXT_SEC};
    padding: 3px 12px;
    border-radius: 10px;
    background: {COLOR_BG};
}}
.toss-kpi .delta.up {{
    color: #E53935;
    background: #FFF5F5;
}}
.toss-kpi .delta.down {{
    color: {COLOR_BLUE};
    background: #F0F7FF;
}}

/* ===== Insight Cards ===== */
.toss-insight {{
    background: {COLOR_CARD};
    border-radius: 20px;
    padding: 32px 20px 28px 20px;
    text-align: center;
    border: none;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04), 0 0.5px 1px rgba(0,0,0,0.02);
    min-height: 160px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
    position: relative;
}}
.toss-insight:hover {{
    transform: translateY(-4px);
    box-shadow: 0 16px 32px rgba(0,0,0,0.08), 0 4px 8px rgba(0,0,0,0.04);
}}
.toss-insight .icon-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-bottom: 14px;
    opacity: 0.8;
}}
.toss-insight .title {{
    font-size: 0.76rem;
    color: {COLOR_TEXT_SEC};
    font-weight: 600;
    margin-bottom: 12px;
}}
.toss-insight .highlight {{
    font-size: 1.15rem;
    font-weight: 700;
    letter-spacing: -0.02em;
    margin-bottom: 10px;
    line-height: 1.3;
}}
.toss-insight .desc {{
    font-size: 0.82rem;
    color: {COLOR_TEXT_TER};
    line-height: 1.55;
}}

/* ===== Alert Cards ===== */
.toss-alert {{
    border-radius: 16px;
    padding: 22px 26px;
    margin-bottom: 12px;
    background: {COLOR_CARD};
    border: none;
    box-shadow: 0 2px 8px rgba(0,0,0,0.03);
    border-left: 4px solid {COLOR_BORDER};
    transition: all 0.2s ease;
}}
.toss-alert:hover {{
    box-shadow: 0 6px 20px rgba(0,0,0,0.06);
}}
.toss-alert.danger {{
    background: linear-gradient(135deg, #FFF5F5 0%, #FFFFFF 100%);
    border-left-color: {COLOR_DANGER};
}}
.toss-alert.success {{
    background: linear-gradient(135deg, #F0FFF4 0%, #FFFFFF 100%);
    border-left-color: {COLOR_SUCCESS};
}}
.toss-alert.warning {{
    background: linear-gradient(135deg, #FFFBF0 0%, #FFFFFF 100%);
    border-left-color: {COLOR_WARNING};
}}
.toss-alert.info {{
    background: linear-gradient(135deg, #F0F7FF 0%, #FFFFFF 100%);
    border-left-color: {COLOR_BLUE};
}}
.toss-alert .alert-title {{
    font-weight: 700;
    font-size: 0.95rem;
    color: {COLOR_TEXT};
    margin-bottom: 8px;
    letter-spacing: -0.01em;
}}
.toss-alert .alert-body {{
    font-size: 0.85rem;
    color: {COLOR_TEXT_TER};
    line-height: 1.75;
}}

/* ===== Section Header ===== */
.section-header {{
    margin-bottom: 24px;
    padding: 14px 0 14px 22px;
    position: relative;
}}
.section-header::before {{
    content: '';
    position: absolute;
    left: 0;
    top: 10px;
    bottom: 10px;
    width: 4px;
    background: linear-gradient(180deg, {BRAND_PRIMARY}, {COLOR_BLUE});
    border-radius: 2px;
}}
.section-header .title {{
    font-size: 1.25rem;
    font-weight: 700;
    color: {COLOR_TEXT};
    letter-spacing: -0.02em;
    margin-bottom: 6px;
}}
.section-header .desc {{
    font-size: 0.84rem;
    color: {COLOR_TEXT_SEC};
    line-height: 1.55;
}}

/* ===== Tabs ===== */
.stTabs [data-baseweb="tab-list"] {{
    gap: 0;
    background-color: transparent;
    border-bottom: 2px solid {COLOR_BORDER};
    padding-bottom: 0;
}}
.stTabs [data-baseweb="tab"] {{
    border-radius: 12px 12px 0 0;
    padding: 12px 24px;
    color: {COLOR_TEXT_SEC};
    font-weight: 600;
    font-size: 0.88rem;
    border-bottom: 2px solid transparent;
    margin-bottom: -2px;
    transition: all 0.2s ease;
}}
.stTabs [data-baseweb="tab"]:hover {{
    color: {COLOR_TEXT_TER};
    background: rgba(247,147,29,0.03);
}}
.stTabs [aria-selected="true"] {{
    color: {BRAND_PRIMARY} !important;
    border-bottom: 2.5px solid {BRAND_PRIMARY};
    background: transparent;
    font-weight: 700;
}}

/* ===== Buttons ===== */
.stDownloadButton > button {{
    background: {COLOR_CARD} !important;
    border: 1px solid {COLOR_BORDER} !important;
    border-radius: 12px !important;
    color: {COLOR_TEXT} !important;
    font-weight: 600 !important;
    font-size: 0.88rem !important;
    padding: 10px 20px !important;
    transition: all 0.25s cubic-bezier(0.4,0,0.2,1);
    box-shadow: 0 1px 3px rgba(0,0,0,0.02);
}}
.stDownloadButton > button:hover {{
    background: #FFF8F0 !important;
    border-color: {BRAND_PRIMARY} !important;
    color: {BRAND_PRIMARY} !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(247,147,29,0.10);
}}

/* ===== Divider ===== */
hr {{
    border: none !important;
    border-top: 1px solid {COLOR_BORDER} !important;
    margin: 40px 0 !important;
}}

/* ===== Radio/Select ===== */
.stRadio > div {{ gap: 8px; }}

/* ===== Plotly chart container ===== */
[data-testid="stPlotlyChart"] {{
    background: {COLOR_CARD};
    border: none;
    border-radius: 20px;
    padding: 12px 8px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.04), 0 0.5px 1px rgba(0,0,0,0.02);
    transition: box-shadow 0.25s ease;
}}
[data-testid="stPlotlyChart"]:hover {{
    box-shadow: 0 6px 20px rgba(0,0,0,0.06), 0 2px 4px rgba(0,0,0,0.03);
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

/* ── selectbox 드롭다운: 검색창을 맨 위로 올리고 스타일링 ── */
/* 팝오버 내부를 flex 컬럼으로 만들어 순서 재배치 가능하게 */
div[data-baseweb="popover"] > div,
div[data-baseweb="popover"] > div > div {{
    display: flex !important;
    flex-direction: column !important;
}}

/* 검색 input이 들어있는 컨테이너(주로 e1d7a4qp0 클래스)를 맨 위로 */
div[data-baseweb="popover"] div:has(> input[type="text"]),
div[data-baseweb="popover"] div:has(> input[aria-autocomplete]) {{
    order: -999 !important;
    margin-bottom: 4px !important;
}}

/* 검색창 스타일링 (깔끔한 검색 UI) */
div[data-baseweb="popover"] input[type="text"],
div[data-baseweb="popover"] input[aria-autocomplete] {{
    background: #F7F8FA !important;
    border: 1.5px solid #E5E8EB !important;
    border-radius: 10px !important;
    padding: 8px 14px 8px 36px !important;
    margin: 6px 8px 4px 8px !important;
    width: calc(100% - 16px) !important;
    font-size: 0.88rem !important;
    color: #191F28 !important;
    transition: border-color 0.15s, background 0.15s !important;
    box-sizing: border-box !important;
}}
div[data-baseweb="popover"] input[type="text"]:focus,
div[data-baseweb="popover"] input[aria-autocomplete]:focus {{
    background: #FFFFFF !important;
    border-color: {BRAND_PRIMARY} !important;
    outline: none !important;
}}

/* 검색 아이콘 추가 (input 컨테이너에) */
div[data-baseweb="popover"] div:has(> input[type="text"]) {{
    position: relative !important;
}}
div[data-baseweb="popover"] div:has(> input[type="text"])::before {{
    content: "🔍";
    position: absolute;
    left: 20px;
    top: 50%;
    transform: translateY(-50%);
    font-size: 0.92rem;
    pointer-events: none;
    z-index: 10;
    opacity: 0.55;
}}

/* 플레이스홀더 색상 */
div[data-baseweb="popover"] input::placeholder {{
    color: #8B95A1 !important;
    opacity: 1 !important;
}}
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
