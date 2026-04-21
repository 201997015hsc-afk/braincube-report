"""
Toss-style 차트 빌더 모듈
모든 차트에 공통 레이아웃을 적용하여 일관된 디자인 유지
"""
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

from modules.config import PLOTLY_LAYOUT, COLOR_BORDER, TARGET_METRICS, compact_num


def _apply_layout(fig: go.Figure, **overrides) -> go.Figure:
    """공통 Toss 레이아웃 적용 + 개별 오버라이드"""
    layout = {**PLOTLY_LAYOUT, **overrides}
    fig.update_layout(**layout)
    return fig


def _title(text: str) -> dict:
    """PLOTLY_LAYOUT 타이틀 스타일 상속 + text 오버라이드 (중복 키 방지)"""
    d = dict(PLOTLY_LAYOUT['title'])
    d['text'] = text
    return d


def bar_chart(df: pd.DataFrame, x: str, y: str, title: str, color: str, text_fmt: str = '.2s') -> go.Figure:
    fig = px.bar(df, x=x, y=y, text_auto=text_fmt, color_discrete_sequence=[color])
    fig.update_traces(marker_line_width=0, opacity=0.88, marker_cornerradius=6)
    return _apply_layout(fig, title=_title(title), bargap=0.35)


def line_chart(df: pd.DataFrame, x: str, y: str, title: str, color: str) -> go.Figure:
    fig = px.line(df, x=x, y=y, markers=True, color_discrete_sequence=[color])
    fig.update_traces(line_width=2.5, marker_size=8, line_shape='spline')
    return _apply_layout(fig, title=_title(title))


def dual_axis_bar_line(
    df: pd.DataFrame,
    x: str,
    bar_y: str,
    line_y: str,
    bar_name: str = "발송량 (건)",
    line_name: str = "클릭수 (회)",
    bar_color: str = '#3182F6',
    line_color: str = '#FF6B6B',
    title: str = "",
) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df[x], y=df[bar_y], name=bar_name,
        marker_color=bar_color, marker_line_width=0, opacity=0.85,
        marker_cornerradius=6,
    ))
    fig.add_trace(go.Scatter(
        x=df[x], y=df[line_y], name=line_name,
        mode='lines+markers', marker_color=line_color,
        line_width=2.5, marker_size=8, yaxis='y2',
        line_shape='spline',
    ))
    return _apply_layout(
        fig,
        title=_title(title),
        yaxis2=dict(overlaying='y', side='right', showgrid=False,
                    tickfont=dict(size=11, color='#8B95A1')),
    )


# ──────────────────────────────────────────────
# Toss-style 히트맵 (커스텀 컬러 + 적응형 텍스트)
# ──────────────────────────────────────────────

_TOSS_HEAT_ORANGE = [
    [0, '#FFF8F0'], [0.15, '#FFE8CC'], [0.35, '#FFD19A'],
    [0.55, '#FFB74D'], [0.75, '#F7931D'], [1, '#D47700'],
]
_TOSS_HEAT_BLUE = [
    [0, '#F0F6FF'], [0.15, '#D6EAFF'], [0.35, '#AAD4FF'],
    [0.55, '#64B5F6'], [0.75, '#3182F6'], [1, '#1565C0'],
]


_compact_num = compact_num  # 후방호환 alias


def heatmap(
    df: pd.DataFrame,
    x: str, y: str, z: str,
    title: str,
    color_scale: str = "Oranges",
    category_order: list[str] | None = None,
    height: int = 700,
    is_pct: bool = False,
) -> go.Figure:

    # 집계 피벗
    pivot = df.pivot_table(index=y, columns=x, values=z, aggfunc='sum', fill_value=0)
    if category_order:
        valid = [c for c in category_order if c in pivot.columns]
        pivot = pivot.reindex(columns=valid, fill_value=0)

    z_vals = pivot.values
    y_labels = list(pivot.index)
    x_labels = list(pivot.columns)

    # 컬러스케일 선택
    cs = _TOSS_HEAT_BLUE if color_scale == "Blues" else _TOSS_HEAT_ORANGE

    # 최대/최소 위치 찾기
    flat = z_vals.flatten()
    max_val = flat.max() if flat.size > 0 else 0
    min_val = flat[flat > 0].min() if (flat > 0).any() else 0

    # 셀 텍스트 생성 (축약 숫자 + 최대/최소 마킹)
    def _cell_text(v):
        if v == 0:
            return ""
        txt = f"{v:.2f}%" if is_pct else _compact_num(v)
        if v == max_val and max_val > 0:
            return f"★ {txt}"
        if v == min_val and min_val > 0 and min_val != max_val:
            return f"▾ {txt}"
        return txt

    text_matrix = [[_cell_text(v) for v in row] for row in z_vals]

    # 적응형 크기 계산
    n_rows = len(y_labels)
    n_cols = len(x_labels)

    # 셀당 높이 — 행이 많으면 줄임, 적으면 넉넉히
    cell_h = 52 if n_rows <= 5 else (44 if n_rows <= 10 else 36)
    auto_height = max(350, n_rows * cell_h + 100)
    final_height = auto_height if height == 700 else max(height, auto_height)

    # 폰트 크기 — 셀 수에 맞게 조절
    if n_cols <= 7:
        base_font = 12
    elif n_cols <= 12:
        base_font = 11
    else:
        base_font = 9

    # 적응형 텍스트 색상 + 굵기 (최대/최소 강조)
    threshold = max_val * 0.55 if max_val > 0 else 1
    font_colors = []
    font_sizes = []
    for row in z_vals:
        row_colors = []
        row_sizes = []
        for v in row:
            if v == max_val and max_val > 0:
                row_colors.append('#FFFFFF')
                row_sizes.append(base_font + 1)
            elif v == min_val and min_val > 0 and min_val != max_val:
                row_colors.append('#D32F2F')
                row_sizes.append(base_font)
            elif v > threshold:
                row_colors.append('#FFFFFF')
                row_sizes.append(base_font)
            else:
                row_colors.append('#4E5968')
                row_sizes.append(base_font)
        font_colors.append(row_colors)
        font_sizes.append(row_sizes)

    # 갭 — 셀이 많으면 좁게
    gap = 4 if n_cols <= 7 else (3 if n_cols <= 12 else 2)

    fig = go.Figure(data=go.Heatmap(
        z=z_vals,
        x=x_labels,
        y=y_labels,
        text=text_matrix,
        texttemplate="%{text}",
        textfont=dict(size=base_font, family='Pretendard, sans-serif'),
        colorscale=cs,
        xgap=gap,
        ygap=gap,
        hovertemplate=(
            '<b>%{y}</b><br>%{x}<br>'
            + (z + ': %{z:.2f}%' if is_pct else z + ': %{z:,.0f}')
            + '<extra></extra>'
        ),
        colorbar=dict(
            thickness=12,
            outlinewidth=0,
            tickfont=dict(size=10, color='#8B95A1'),
            title=dict(text="CTR (%)" if is_pct else "", font=dict(size=10, color='#8B95A1')),
            lenmode='fraction', len=0.5,
            yanchor='middle', y=0.5,
        ),
    ))

    # 적응형 어노테이션 (색상 + 사이즈 개별 적용)
    annotations = [
        dict(
            x=x_labels[j], y=y_labels[i],
            text=text_matrix[i][j], showarrow=False,
            font=dict(
                size=font_sizes[i][j],
                color=font_colors[i][j],
                family='Pretendard, sans-serif',
            ),
        )
        for i, row in enumerate(z_vals)
        for j, v in enumerate(row)
        if text_matrix[i][j]
    ]
    fig.update_layout(annotations=annotations)
    fig.update_traces(texttemplate="")

    # 왼쪽 마진 — 매체명 길이에 맞게
    max_label_len = max((len(str(y)) for y in y_labels), default=4)
    left_margin = min(max(max_label_len * 9, 80), 180)
    tick_font = min(base_font, 12)

    return _apply_layout(
        fig,
        height=final_height,
        margin=dict(t=10, l=left_margin, r=30, b=50),
        xaxis=dict(
            side='bottom',
            tickfont=dict(size=tick_font, color='#8B95A1', family='Pretendard, sans-serif'),
            showgrid=False, title='',
        ),
        yaxis=dict(
            tickfont=dict(size=tick_font, color='#4E5968', family='Pretendard, sans-serif'),
            showgrid=False, title='', autorange='reversed',
        ),
    )
