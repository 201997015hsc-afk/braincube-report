"""
영업용 원페이저 PDF 모듈
A4 한 장에 핵심 KPI · MoM 변화 · TOP 매체 · 액션 아이템을 담는다.
fpdf2 + Malgun Gothic (Windows 기본 한글 폰트)
"""
import os
from datetime import datetime
from io import BytesIO

import numpy as np
import pandas as pd
from fpdf import FPDF

from modules.config import BRAND_PRIMARY, COLOR_BLUE, COLOR_TEXT, COLOR_TEXT_SEC, compact_num
from modules.data_processing import calc_ctr_scalar
from modules.firebase_connector import (
    get_benchmark_stats, calc_percentile, calc_percentile_lower, percentile_letter,
)

# ──────────────────────────────────────────────
# 폰트 경로 (크로스플랫폼)
# ──────────────────────────────────────────────
import platform as _platform

def _build_font_candidates() -> list[tuple[str, str, str]]:
    """OS별 한글 폰트 경로 탐색"""
    candidates = []
    _sys = _platform.system()
    if _sys == "Windows":
        base = "C:/Windows/Fonts"
        candidates = [
            ("MalgunGothic", f"{base}/malgun.ttf", f"{base}/malgunbd.ttf"),
            ("NanumGothic", f"{base}/NanumGothic.ttf", f"{base}/NanumGothicBold.ttf"),
        ]
    elif _sys == "Darwin":  # macOS
        candidates = [
            ("AppleGothic", "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
             "/System/Library/Fonts/Supplemental/AppleGothic.ttf"),
            ("NanumGothic", "/Library/Fonts/NanumGothic.ttf",
             "/Library/Fonts/NanumGothicBold.ttf"),
        ]
    else:  # Linux
        candidates = [
            ("NanumGothic", "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
             "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
            ("NotoSansKR", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
        ]
    return candidates

_FONT_CANDIDATES = _build_font_candidates()

# ──────────────────────────────────────────────
# 색상 상수 (RGB 튜플)
# ──────────────────────────────────────────────
_C_PRIMARY = (247, 147, 29)   # 오렌지
_C_BLUE = (49, 130, 246)
_C_TEXT = (25, 31, 40)
_C_TEXT_SEC = (139, 149, 161)
_C_TEXT_TER = (78, 89, 104)
_C_BG = (247, 248, 250)
_C_WHITE = (255, 255, 255)
_C_SUCCESS = (0, 200, 83)
_C_DANGER = (244, 67, 54)
_C_BORDER = (235, 238, 242)
_C_CARD_BG = (250, 251, 252)


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


# ──────────────────────────────────────────────
# OnePagerPDF 클래스
# ──────────────────────────────────────────────

class OnePagerPDF(FPDF):
    """A4 세로 1페이지 한글 PDF"""

    def __init__(self):
        super().__init__(orientation='P', unit='mm', format='A4')
        self._register_font()
        self.set_auto_page_break(auto=False)
        self.add_page()
        self.set_margins(12, 8, 12)

    def _register_font(self):
        """한글 TTF 폰트 등록"""
        for name, regular, bold in _FONT_CANDIDATES:
            if os.path.isfile(regular):
                self.add_font(name, '', regular, uni=True)
                if os.path.isfile(bold):
                    self.add_font(name, 'B', bold, uni=True)
                else:
                    self.add_font(name, 'B', regular, uni=True)
                self._font_family = name
                return
        # fallback: 기본 Helvetica (한글 깨짐 가능)
        self._font_family = 'Helvetica'

    def _set_font(self, style: str = '', size: int = 10):
        self.set_font(self._font_family, style, size)

    # ── Drawing Helpers ──

    def draw_header(self, company_name: str, period: str, service_name: str = ""):
        """상단 헤더: 회사명 + 기간 + 서비스명"""
        x0 = self.l_margin
        y0 = 10

        # 오렌지 악센트 바
        self.set_fill_color(*_C_PRIMARY)
        self.rect(x0, y0, 3, 14, 'F')

        # 회사명
        self.set_xy(x0 + 6, y0)
        self._set_font('B', 16)
        self.set_text_color(*_C_TEXT)
        self.cell(0, 8, f"{company_name} LMS 성과 리포트", ln=True)

        # 기간 + 생성일
        self.set_x(x0 + 6)
        self._set_font('', 8)
        self.set_text_color(*_C_TEXT_SEC)
        gen_date = datetime.now().strftime('%Y년 %m월 %d일')
        self.cell(0, 5, f"{period}  |  생성: {gen_date}", ln=True)

        # 서비스명
        if service_name:
            self.set_x(x0 + 6)
            self._set_font('', 7)
            self.set_text_color(*_C_TEXT_SEC)
            self.cell(0, 4, f"Powered by {service_name}", ln=True)

        # 구분선
        self.set_draw_color(*_C_BORDER)
        self.line(x0, self.get_y() + 3, 210 - self.r_margin, self.get_y() + 3)
        self.set_y(self.get_y() + 6)

    def draw_kpi_cards(self, kpis: list[dict]):
        """
        KPI 카드 행. kpis = [{label, value, delta, delta_type}, ...]
        최대 4개 카드를 가로로 배치
        """
        card_w = (210 - self.l_margin - self.r_margin - (len(kpis) - 1) * 3) / len(kpis)
        card_h = 22
        y0 = self.get_y()

        for i, kp in enumerate(kpis):
            x = self.l_margin + i * (card_w + 3)

            # 카드 배경
            self.set_fill_color(*_C_CARD_BG)
            self.set_draw_color(*_C_BORDER)
            self.rect(x, y0, card_w, card_h, 'DF', round_corners=True, corner_radius=3)

            # 상단 악센트 라인
            self.set_fill_color(*_C_PRIMARY)
            self.rect(x, y0, card_w, 1.2, 'F')

            # 라벨
            self._set_font('', 7)
            self.set_text_color(*_C_TEXT_SEC)
            self.set_xy(x + 3, y0 + 3)
            self.cell(card_w - 6, 4, kp['label'])

            # 값
            self._set_font('B', 13)
            self.set_text_color(*_C_TEXT)
            self.set_xy(x + 3, y0 + 8)
            self.cell(card_w - 6, 7, kp['value'])

            # 변화율
            if kp.get('delta'):
                self._set_font('', 7)
                dt = kp.get('delta_type', '')
                if dt == 'up':
                    self.set_text_color(*_C_DANGER)
                    arrow = chr(0x2191) + ' '  # ↑
                elif dt == 'down':
                    self.set_text_color(*_C_BLUE)
                    arrow = chr(0x2193) + ' '  # ↓
                else:
                    self.set_text_color(*_C_TEXT_SEC)
                    arrow = ''
                self.set_xy(x + 3, y0 + 16)
                self.cell(card_w - 6, 4, f"{arrow}{kp['delta']}")

        self.set_y(y0 + card_h + 4)

    def draw_section_title(self, title: str, color: tuple = _C_PRIMARY):
        """섹션 소제목"""
        y0 = self.get_y()
        self.set_fill_color(*color)
        self.rect(self.l_margin, y0, 2.5, 5, 'F')
        self._set_font('B', 10)
        self.set_text_color(*_C_TEXT)
        self.set_xy(self.l_margin + 5, y0)
        self.cell(0, 5, title, ln=True)
        self.set_y(self.get_y() + 2)

    def draw_table(self, headers: list[str], rows: list[list[str]], col_widths: list[float]):
        """테이블 그리기"""
        row_h = 6
        y0 = self.get_y()

        # 헤더
        self.set_fill_color(*_C_BG)
        self._set_font('B', 7)
        self.set_text_color(*_C_TEXT_TER)
        x = self.l_margin
        for w, h_text in zip(col_widths, headers):
            self.set_xy(x, y0)
            self.cell(w, row_h, h_text, border=0, fill=True, align='C')
            x += w
        self.set_y(y0 + row_h)

        # 데이터 행
        self._set_font('', 7)
        self.set_text_color(*_C_TEXT)
        for row_idx, row in enumerate(rows):
            y = self.get_y()
            x = self.l_margin
            # 교대 배경
            if row_idx % 2 == 1:
                self.set_fill_color(252, 253, 254)
            else:
                self.set_fill_color(*_C_WHITE)
            for w, cell_text in zip(col_widths, row):
                self.set_xy(x, y)
                align = 'R' if any(c.isdigit() for c in cell_text) else 'L'
                self.cell(w, row_h, cell_text, border=0, fill=True, align=align)
                x += w
            self.set_y(y + row_h)

        # 하단 구분선
        self.set_draw_color(*_C_BORDER)
        self.line(self.l_margin, self.get_y(), 210 - self.r_margin, self.get_y())
        self.set_y(self.get_y() + 2)

    def draw_insight_bullets(self, items: list[str], color: tuple = _C_BLUE):
        """불릿 포인트 리스트"""
        self._set_font('', 7.5)
        self.set_text_color(*_C_TEXT)
        for item in items:
            y = self.get_y()
            # 불릿 점
            self.set_fill_color(*color)
            self.ellipse(self.l_margin + 2, y + 1.5, 2, 2, 'F')
            self.set_xy(self.l_margin + 6, y)
            self.multi_cell(210 - self.l_margin - self.r_margin - 6, 4.5, item)
            self.set_y(self.get_y() + 1)

    def draw_footer(self, service_name: str = ""):
        """하단 푸터"""
        y = 285
        self.set_draw_color(*_C_BORDER)
        self.line(self.l_margin, y, 210 - self.r_margin, y)
        self._set_font('', 6)
        self.set_text_color(*_C_TEXT_SEC)
        self.set_xy(self.l_margin, y + 1)
        footer_txt = f"Generated by {service_name}" if service_name else "LMS Analytics Report"
        self.cell(0, 4, f"{footer_txt}  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}", align='C')

    def draw_benchmark_scorecard(self, grades: list[dict]):
        """벤치마크 스코어카드. grades=[{metric, value, industry, grade, pct}, ...]"""
        card_w = (210 - self.l_margin - self.r_margin - (len(grades) - 1) * 3) / len(grades)
        card_h = 24
        y0 = self.get_y()

        grade_colors = {
            'A': (46, 125, 50),    # green
            'B': (21, 101, 192),   # blue
            'C': (245, 127, 23),   # amber
            'D': (229, 57, 53),    # red
        }

        for i, g in enumerate(grades):
            x = self.l_margin + i * (card_w + 3)
            gc = grade_colors.get(g['grade'], _C_TEXT_SEC)

            # 카드 배경
            self.set_fill_color(*_C_CARD_BG)
            self.set_draw_color(*_C_BORDER)
            self.rect(x, y0, card_w, card_h, 'DF', round_corners=True, corner_radius=3)

            # 등급 원
            self.set_fill_color(*gc)
            cx = x + card_w - 10
            cy = y0 + 6
            self.ellipse(cx - 5, cy - 5, 10, 10, 'F')
            self._set_font('B', 9)
            self.set_text_color(*_C_WHITE)
            self.set_xy(cx - 5, cy - 3.5)
            self.cell(10, 7, g['grade'], align='C')

            # 지표명
            self._set_font('B', 8)
            self.set_text_color(*_C_TEXT)
            self.set_xy(x + 3, y0 + 3)
            self.cell(card_w - 16, 4, g['metric'])

            # 자사 값
            self._set_font('', 7)
            self.set_text_color(*gc)
            self.set_xy(x + 3, y0 + 8)
            self.cell(card_w - 16, 4, f"자사: {g['value']}")

            # 업종 평균
            self._set_font('', 6.5)
            self.set_text_color(*_C_TEXT_SEC)
            self.set_xy(x + 3, y0 + 13)
            self.cell(card_w - 16, 4, f"업종: {g['industry']}")

            # 백분위
            self._set_font('', 6)
            self.set_text_color(*_C_TEXT_TER)
            self.set_xy(x + 3, y0 + 18)
            self.cell(card_w - 16, 4, f"상위 {100 - g['pct']:.0f}%")

        self.set_y(y0 + card_h + 4)

    def draw_mom_bars(self, label: str, current: float, previous: float, unit: str = ""):
        """MoM 미니 비교 바: [라벨] [이전▓▓░░░ 현재▓▓▓▓░]"""
        y0 = self.get_y()
        bar_max_w = 60
        max_val = max(current, previous, 1)

        # 라벨
        self._set_font('', 7)
        self.set_text_color(*_C_TEXT_TER)
        self.set_xy(self.l_margin, y0)
        self.cell(28, 4.5, label)

        # 이전 월 바
        prev_w = max((previous / max_val) * bar_max_w, 1) if max_val > 0 else 1
        self.set_fill_color(220, 225, 230)
        self.rect(self.l_margin + 28, y0, prev_w, 4, 'F', round_corners=True, corner_radius=1.5)
        self._set_font('', 6)
        self.set_text_color(*_C_TEXT_SEC)
        self.set_xy(self.l_margin + 28 + prev_w + 1, y0)
        self.cell(30, 4, compact_num(previous, unit))

        # 현재 월 바
        y0 += 5
        curr_w = max((current / max_val) * bar_max_w, 1) if max_val > 0 else 1
        self.set_fill_color(*_C_PRIMARY)
        self.rect(self.l_margin + 28, y0, curr_w, 4, 'F', round_corners=True, corner_radius=1.5)
        self._set_font('B', 6)
        self.set_text_color(*_C_TEXT)
        self.set_xy(self.l_margin + 28 + curr_w + 1, y0)
        self.cell(30, 4, compact_num(current, unit))

        # 변화율
        if previous > 0:
            change = (current - previous) / previous * 100
            sign = "+" if change >= 0 else ""
            self._set_font('B', 7)
            c = _C_DANGER if change > 0 else _C_BLUE
            self.set_text_color(*c)
            self.set_xy(self.l_margin + 130, y0 - 2.5)
            self.cell(30, 5, f"{sign}{change:.1f}%", align='R')

        self.set_y(y0 + 6)


# ──────────────────────────────────────────────
# 분석 & PDF 생성
# ──────────────────────────────────────────────

def _compute_kpis(df: pd.DataFrame) -> list[dict]:
    """전체 KPI 계산"""
    total_cost = df['집행금액'].sum()
    total_send = df['발송량'].sum()
    total_click = df['클릭수'].sum()
    ctr = calc_ctr_scalar(total_click, total_send)

    # MoM 변화 (최근 2개월)
    months = sorted(df['년월'].unique())
    kpis = [
        {"label": "총 집행금액", "value": compact_num(total_cost, "원"), "delta": "", "delta_type": ""},
        {"label": "총 발송량", "value": compact_num(total_send, "건"), "delta": "", "delta_type": ""},
        {"label": "총 클릭수", "value": compact_num(total_click, "회"), "delta": "", "delta_type": ""},
        {"label": "평균 CTR", "value": f"{ctr:.2f}%", "delta": "", "delta_type": ""},
    ]

    if len(months) >= 2:
        curr_m = months[-1]
        prev_m = months[-2]
        curr = df[df['년월'] == curr_m]
        prev = df[df['년월'] == prev_m]

        def _delta(cur_val, pre_val):
            if pre_val > 0:
                pct = (cur_val - pre_val) / pre_val * 100
                sign = "+" if pct >= 0 else ""
                dt = "up" if pct > 0 else ("down" if pct < 0 else "")
                return f"전월 대비 {sign}{pct:.1f}%", dt
            return "", ""

        for i, col in enumerate(['집행금액', '발송량', '클릭수']):
            d, t = _delta(curr[col].sum(), prev[col].sum())
            kpis[i]['delta'] = d
            kpis[i]['delta_type'] = t

        # CTR 변화
        curr_ctr = calc_ctr_scalar(curr['클릭수'].sum(), curr['발송량'].sum())
        prev_ctr = calc_ctr_scalar(prev['클릭수'].sum(), prev['발송량'].sum())
        diff = curr_ctr - prev_ctr
        if abs(diff) > 0.001:
            sign = "+" if diff > 0 else ""
            kpis[3]['delta'] = f"전월 대비 {sign}{diff:.2f}%p"
            kpis[3]['delta_type'] = "up" if diff > 0 else "down"

    return kpis


def _top_media(df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """CTR 기준 TOP N 매체"""
    media = df.groupby('매체명').agg({
        '집행금액': 'sum', '발송량': 'sum', '클릭수': 'sum',
    }).reset_index()
    media['CTR'] = np.where(media['발송량'] > 0, media['클릭수'] / media['발송량'] * 100, 0)
    media['CPC'] = np.where(media['클릭수'] > 0, media['집행금액'] / media['클릭수'], 0)
    media = media.sort_values('클릭수', ascending=False).head(n)
    return media


def _generate_insights(df: pd.DataFrame) -> list[str]:
    """핵심 인사이트 자동 생성"""
    insights = []
    months = sorted(df['년월'].unique())

    # 1. 전체 요약
    total_send = df['발송량'].sum()
    total_click = df['클릭수'].sum()
    ctr = calc_ctr_scalar(total_click, total_send)
    insights.append(f"전체 기간 평균 CTR {ctr:.2f}%, 총 {compact_num(total_click, '회')} 클릭 달성")

    # 2. 최고 CTR 매체
    media = df.groupby('매체명').agg({'발송량': 'sum', '클릭수': 'sum'}).reset_index()
    media['CTR'] = np.where(media['발송량'] > 0, media['클릭수'] / media['발송량'] * 100, 0)
    media = media[media['발송량'] >= 5000]  # 최소 발송량 필터
    if not media.empty:
        best = media.loc[media['CTR'].idxmax()]
        insights.append(f"최고 효율 매체: {best['매체명']} (CTR {best['CTR']:.2f}%, {compact_num(best['발송량'], '건')} 발송)")

    # 3. MoM 트렌드
    if len(months) >= 2:
        curr = df[df['년월'] == months[-1]]
        prev = df[df['년월'] == months[-2]]
        curr_ctr = calc_ctr_scalar(curr['클릭수'].sum(), curr['발송량'].sum())
        prev_ctr = calc_ctr_scalar(prev['클릭수'].sum(), prev['발송량'].sum())
        diff = curr_ctr - prev_ctr
        if abs(diff) > 0.01:
            direction = "상승" if diff > 0 else "하락"
            insights.append(f"전월 대비 CTR {abs(diff):.2f}%p {direction} ({months[-2]} → {months[-1]})")

        cost_change = (curr['집행금액'].sum() - prev['집행금액'].sum()) / max(prev['집행금액'].sum(), 1) * 100
        if abs(cost_change) > 3:
            direction = "증가" if cost_change > 0 else "감소"
            insights.append(f"집행금액 전월 대비 {abs(cost_change):.0f}% {direction}")

    # 4. 요일 분석
    if '요일번호' in df.columns and not df.empty:
        by_dow = df.groupby('요일번호').agg({'발송량': 'sum', '클릭수': 'sum'}).reset_index()
        by_dow['CTR'] = np.where(by_dow['발송량'] > 0, by_dow['클릭수'] / by_dow['발송량'] * 100, 0)
        if not by_dow.empty and by_dow['CTR'].max() > 0:
            best_dow = by_dow.loc[by_dow['CTR'].idxmax()]
            dow_names = {0: '월', 1: '화', 2: '수', 3: '목', 4: '금', 5: '토', 6: '일'}
            insights.append(f"요일별 최고 효율: {dow_names.get(int(best_dow['요일번호']), '?')}요일 (CTR {best_dow['CTR']:.2f}%)")

    return insights[:5]  # 최대 5개


def _generate_actions(df: pd.DataFrame) -> list[str]:
    """액션 아이템 자동 생성"""
    actions = []
    months = sorted(df['년월'].unique())

    # TOP 매체에 예산 집중 제안
    media = df.groupby('매체명').agg({'발송량': 'sum', '클릭수': 'sum', '집행금액': 'sum'}).reset_index()
    media['CTR'] = np.where(media['발송량'] > 0, media['클릭수'] / media['발송량'] * 100, 0)
    media['CPC'] = np.where(media['클릭수'] > 0, media['집행금액'] / media['클릭수'], 0)
    media = media[media['발송량'] >= 5000]

    if len(media) >= 2:
        best = media.loc[media['CTR'].idxmax()]
        worst = media.loc[media['CTR'].idxmin()]
        if best['CTR'] > worst['CTR'] * 1.5:
            actions.append(
                f"'{best['매체명']}' 매체에 예산 확대를 권장합니다. "
                f"(CTR {best['CTR']:.2f}% vs '{worst['매체명']}' {worst['CTR']:.2f}%)"
            )

    # CPC 효율 제안
    if not media.empty:
        cpc_positive = media[media['CPC'] > 0]
        if not cpc_positive.empty:
            low_cpc = cpc_positive.loc[cpc_positive['CPC'].idxmin()]
            actions.append(f"CPC 최저 매체: '{low_cpc['매체명']}' ({low_cpc['CPC']:,.0f}원/클릭) — 비용 효율 극대화 가능")

    # MoM 하락 매체 경고
    if len(months) >= 2:
        for _, row in media.iterrows():
            name = row['매체명']
            curr_d = df[(df['년월'] == months[-1]) & (df['매체명'] == name)]
            prev_d = df[(df['년월'] == months[-2]) & (df['매체명'] == name)]
            if curr_d['발송량'].sum() >= 5000 and prev_d['발송량'].sum() >= 5000:
                curr_ctr = calc_ctr_scalar(curr_d['클릭수'].sum(), curr_d['발송량'].sum())
                prev_ctr = calc_ctr_scalar(prev_d['클릭수'].sum(), prev_d['발송량'].sum())
                if prev_ctr > 0 and (prev_ctr - curr_ctr) / prev_ctr > 0.2:
                    actions.append(f"'{name}' CTR 20% 이상 하락 — 소재 교체 또는 타겟 재설정 필요")

    if not actions:
        actions.append("현재 매체별 성과가 안정적입니다. 기존 운영 전략을 유지하세요.")

    return actions[:4]


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def generate_onepager(
    df: pd.DataFrame,
    company_name: str = "",
    service_name: str = "",
) -> bytes:
    """
    A4 원페이저 PDF 바이트 생성.
    Returns: PDF bytes
    """
    if df.empty:
        raise ValueError("데이터가 비어 있어 PDF를 생성할 수 없습니다.")
    company = company_name or "LMS Analytics"
    period = f"{df['날짜'].min().strftime('%Y.%m.%d')} — {df['날짜'].max().strftime('%Y.%m.%d')}"
    months = sorted(df['년월'].unique())
    if not months:
        raise ValueError("날짜 정보가 없어 PDF를 생성할 수 없습니다.")

    pdf = OnePagerPDF()

    # ── 1. 헤더 ──
    pdf.draw_header(company, period, service_name)

    # ── 2. KPI 카드 ──
    kpis = _compute_kpis(df)
    pdf.draw_kpi_cards(kpis)

    # ── 3. MoM 비교 바 (2개월 이상인 경우) ──
    if len(months) >= 2:
        pdf.draw_section_title("MoM 변화", _C_PRIMARY)
        curr_m, prev_m = months[-1], months[-2]
        curr = df[df['년월'] == curr_m]
        prev = df[df['년월'] == prev_m]

        pdf._set_font('', 6)
        pdf.set_text_color(*_C_TEXT_SEC)
        pdf.set_x(pdf.l_margin)
        pdf.cell(0, 3.5, f"{prev_m} vs {curr_m}", ln=True)
        pdf.set_y(pdf.get_y() + 1)

        for label, col, unit in [
            ("집행금액", "집행금액", "원"),
            ("발송량", "발송량", "건"),
            ("클릭수", "클릭수", "회"),
        ]:
            pdf.draw_mom_bars(label, curr[col].sum(), prev[col].sum(), unit)

    # ── 4. TOP 매체 테이블 ──
    pdf.draw_section_title("TOP 매체 성과", _C_BLUE)
    top = _top_media(df, n=5)
    headers = ["매체명", "발송량", "클릭수", "CTR", "CPC"]
    col_widths = [44, 36, 36, 26, 44]
    rows = []
    for _, r in top.iterrows():
        rows.append([
            str(r['매체명']),
            compact_num(r['발송량'], "건"),
            compact_num(r['클릭수'], "회"),
            f"{r['CTR']:.2f}%",
            f"{r['CPC']:,.0f}원" if r['CPC'] > 0 else "-",
        ])
    pdf.draw_table(headers, rows, col_widths)

    # ── 5. 벤치마크 스코어카드 ──
    try:
        bench = get_benchmark_stats()
    except Exception:
        bench = None
    if bench and bench['avg_ctr'] > 0:
        pdf.draw_section_title("벤치마크 포지셔닝", (108, 99, 255))

        total_send = df['발송량'].sum()
        total_click = df['클릭수'].sum()
        total_cost = df['집행금액'].sum()
        client_ctr = (total_click / total_send * 100) if total_send > 0 else 0
        client_cpc = (total_cost / total_click) if total_click > 0 else 0

        grades = []
        if client_ctr > 0:
            ctr_pct = calc_percentile(client_ctr, bench['ctr_values'])
            grades.append({
                'metric': 'CTR', 'value': f'{client_ctr:.2f}%',
                'industry': f'{bench["avg_ctr"]:.2f}%',
                'grade': percentile_letter(ctr_pct), 'pct': ctr_pct,
            })
        if client_cpc > 0 and bench['avg_cpc'] > 0:
            cpc_pct = calc_percentile_lower(client_cpc, bench['cpc_values'])
            grades.append({
                'metric': 'CPC', 'value': f'{client_cpc:,.0f}원',
                'industry': f'{bench["avg_cpc"]:,.0f}원',
                'grade': percentile_letter(cpc_pct), 'pct': cpc_pct,
            })
        # 종합 점수
        if len(grades) >= 2:
            overall_pct = (grades[0]['pct'] + grades[1]['pct']) / 2
            grades.append({
                'metric': '종합', 'value': f'상위 {100 - overall_pct:.0f}%',
                'industry': f'{bench["total_campaigns"]}건 대비',
                'grade': percentile_letter(overall_pct), 'pct': overall_pct,
            })

        if grades:
            pdf.draw_benchmark_scorecard(grades)

    # ── 6. 핵심 인사이트 ──
    pdf.draw_section_title("핵심 인사이트", _C_PRIMARY)
    insights = _generate_insights(df)
    pdf.draw_insight_bullets(insights, _C_PRIMARY)

    # ── 7. 액션 아이템 ──
    if pdf.get_y() < 258:
        pdf.set_y(pdf.get_y() + 2)
        pdf.draw_section_title("Action Items", _C_BLUE)
        actions = _generate_actions(df)
        pdf.draw_insight_bullets(actions, _C_BLUE)

    # ── 8. 푸터 ──
    pdf.draw_footer(service_name)

    # 출력
    buf = BytesIO()
    pdf.output(buf)
    return buf.getvalue()
