"""Minimal `chart` block for canvas-content/v0: bar and line, values + labels only.

The model writes categories, series names and numbers. This module validates them
and draws an SVG; scales, ticks and positions are computed here, never in the JSON.
"""

from __future__ import annotations

import math

TYPES = ("bar", "line")
W, H = 640, 340
PAD_L, PAD_R, PAD_T, PAD_B = 58, 18, 24, 52


def _esc(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def validate_chart(chart) -> dict:
    if not isinstance(chart, dict):
        raise ValueError("chart must be an object")
    kind = str(chart.get("type") or "bar").lower()
    if kind not in TYPES:
        raise ValueError(f"chart.type must be one of {TYPES}")
    for bad in ("x", "y", "width", "height", "points", "scale", "ticks"):
        if bad in chart:
            raise ValueError(f"chart must not include {bad}: canvas computes geometry")
    cats = chart.get("categories")
    if not isinstance(cats, list) or not cats:
        raise ValueError("chart.categories must be a non-empty array of labels")
    cats = [str(c) for c in cats]
    series = chart.get("series")
    if not isinstance(series, list) or not series:
        raise ValueError("chart.series must be a non-empty array")
    clean = []
    for s in series:
        if not isinstance(s, dict) or not isinstance(s.get("values"), list):
            raise ValueError("each series needs values[]")
        vals = s["values"]
        if len(vals) != len(cats):
            raise ValueError(f"series {s.get('name')!r}: {len(vals)} values for {len(cats)} categories")
        nums = []
        for v in vals:
            if v is None:
                nums.append(None)
            elif isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                raise ValueError(f"series {s.get('name')!r}: values must be numbers or null")
            else:
                nums.append(float(v))
        clean.append({"name": str(s.get("name") or f"series {len(clean) + 1}"), "values": nums})
    return {
        "type": kind,
        "title": str(chart.get("title") or ""),
        "unit": str(chart.get("unit") or ""),
        "x_label": str(chart.get("x_label") or ""),
        "y_label": str(chart.get("y_label") or ""),
        "categories": cats,
        "series": clean,
        "sample": bool(chart.get("sample")),
        "source": str(chart.get("source") or ""),
    }


def nice_ticks(lo: float, hi: float, count: int = 5) -> list:
    if hi <= lo:
        hi = lo + 1
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks = []
    t = start
    while t <= hi + step * 1e-9:
        ticks.append(round(t, 10))
        t += step
    if ticks[-1] < hi:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def fmt(v: float) -> str:
    return f"{v:.0f}" if float(v).is_integer() else f"{v:.2f}".rstrip("0").rstrip(".")


def category_index(chart: dict, ref) -> int | None:
    if ref is None or ref == "":
        return None
    if isinstance(ref, int) and not isinstance(ref, bool):
        return ref if 0 <= ref < len(chart["categories"]) else None
    try:
        return chart["categories"].index(str(ref))
    except ValueError:
        raise ValueError(f"chart highlight not a category: {ref!r}") from None


def chart_svg(chart: dict) -> str:
    cats, series = chart["categories"], chart["series"]
    vals = [v for s in series for v in s["values"] if v is not None]
    lo = min(0.0, min(vals, default=0.0))
    hi = max(vals, default=1.0)
    ticks = nice_ticks(lo, hi)
    y0, y1 = ticks[0], ticks[-1]
    pw, ph = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def ys(v: float) -> float:
        return PAD_T + ph - (v - y0) / (y1 - y0) * ph

    band = pw / len(cats)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" class="cv-chart-svg" role="img" '
           f'aria-label="{_esc(chart["title"] or "chart")}">']
    for t in ticks:
        y = ys(t)
        out.append(f'<line class="cv-grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="cv-tick" x="{PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end">{fmt(t)}</text>')
    out.append(f'<line class="cv-axis" x1="{PAD_L}" x2="{W - PAD_R}" y1="{ys(max(y0, 0) if y0 <= 0 <= y1 else y0):.1f}" '
               f'y2="{ys(max(y0, 0) if y0 <= 0 <= y1 else y0):.1f}"/>')
    for i, c in enumerate(cats):
        cx = PAD_L + band * (i + 0.5)
        out.append(f'<text class="cv-cat" data-cat="{i}" x="{cx:.1f}" y="{H - PAD_B + 18}" text-anchor="middle">{_esc(c)}</text>')
    if chart["y_label"]:
        out.append(f'<text class="cv-axis-label" x="14" y="{PAD_T + ph / 2:.1f}" text-anchor="middle" '
                   f'transform="rotate(-90 14 {PAD_T + ph / 2:.1f})">{_esc(chart["y_label"])}</text>')
    if chart["x_label"]:
        out.append(f'<text class="cv-axis-label" x="{PAD_L + pw / 2:.1f}" y="{H - 8}" text-anchor="middle">{_esc(chart["x_label"])}</text>')
    zero = ys(0 if y0 <= 0 <= y1 else y0)
    if chart["type"] == "bar":
        n = len(series)
        inner = band * 0.72
        bw = inner / n
        for si, s in enumerate(series):
            for i, v in enumerate(s["values"]):
                if v is None:
                    continue
                x = PAD_L + band * i + (band - inner) / 2 + si * bw
                top, bottom = sorted((ys(v), zero))
                out.append(
                    f'<rect class="cv-mark cv-bar cv-s{si}" data-cat="{i}" data-series="{si}" data-value="{fmt(v)}" '
                    f'x="{x + 1:.1f}" y="{top:.1f}" width="{bw - 2:.1f}" height="{max(bottom - top, 0.5):.1f}" '
                    f'style="transform-origin: {x + bw / 2:.1f}px {zero:.1f}px"/>'
                )
    else:
        for si, s in enumerate(series):
            pts = [(PAD_L + band * (i + 0.5), ys(v)) if v is not None else None for i, v in enumerate(s["values"])]
            for i in range(1, len(pts)):
                a, b = pts[i - 1], pts[i]
                if a and b:
                    seg = math.hypot(b[0] - a[0], b[1] - a[1])
                    out.append(
                        f'<line class="cv-seg cv-s{si}" data-cat="{i}" data-series="{si}" x1="{a[0]:.1f}" y1="{a[1]:.1f}" '
                        f'x2="{b[0]:.1f}" y2="{b[1]:.1f}" style="stroke-dasharray:{seg:.1f};--len:{seg:.1f}"/>'
                    )
            for i, p in enumerate(pts):
                if p:
                    out.append(
                        f'<circle class="cv-mark cv-dot cv-s{si}" data-cat="{i}" data-series="{si}" '
                        f'data-value="{fmt(s["values"][i])}" cx="{p[0]:.1f}" cy="{p[1]:.1f}" r="5"/>'
                    )
    # invisible hit columns so hovering anywhere over a category works
    for i in range(len(cats)):
        out.append(f'<rect class="cv-hitcol" data-cat="{i}" x="{PAD_L + band * i:.1f}" y="{PAD_T}" '
                   f'width="{band:.1f}" height="{ph + 26:.1f}"/>')
    out.append("</svg>")
    return "".join(out)


def chart_html(chart: dict) -> str:
    legend = "".join(
        f'<span class="cv-legend-item"><i class="cv-swatch cv-s{si}"></i>{_esc(s["name"])}</span>'
        for si, s in enumerate(chart["series"])
    )
    badge = '<span class="cv-sample">示例数据</span>' if chart["sample"] else ""
    source = f'<div class="cv-chart-source">{_esc(chart["source"])}</div>' if chart["source"] else ""
    title = f'<div class="cv-chart-title">{_esc(chart["title"])}{badge}</div>' if (chart["title"] or badge) else ""
    return (
        f'<figure class="cv-chart cv-chart-{chart["type"]}" id="chart">{title}'
        f'<div class="cv-legend">{legend}</div>{chart_svg(chart)}'
        f'<div class="cv-readout" id="chart-readout">把鼠标移到柱子上能看到数值。</div>{source}</figure>'
    )
