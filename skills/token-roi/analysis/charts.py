#!/usr/bin/env python3
"""charts.py — stdlib-only inline-SVG builders for the /token-roi report.

Re-implements the three matplotlib figures from my_token_roi.py as
server-side SVG string builders. No external dependencies: no matplotlib,
no numpy, no JS charting libraries. Every chart is a static <svg> fragment
(no <script>) suitable for inlining into a self-contained HTML report.

The three figures:
  - trend_svg          : multi-line, log y-axis, values indexed to a
                         baseline of 1.0x, low-N weeks shaded pink.
  - buckets_svg        : 2x3 small-multiples grid of stacked %-bars.
  - cost_ratios_svg    : multi-line log y-axis of sum(num)/sum(den) per week,
                         with a SWE-chat reference band.
"""
from __future__ import annotations

import html
import math
from typing import Optional

LOW_N_SHADE = "#FFCDD2"      # pink low-confidence week fill
LOW_N_OUTLINE = "#D32F2F"    # red dashed outline / note border
AXIS = "#444"
GRID = "#E0E0E0"
TEXT = "#222"
MUTED = "#666"
FONT = ("font-family=\"-apple-system,BlinkMacSystemFont,'Segoe UI',"
        "Roboto,Helvetica,Arial,sans-serif\"")

# Bucket colors that need white text on top (mirrors source `dark_colors`).
DARK_COLORS = {"#0D47A1", "#1976D2", "#6A1B9A", "#1B5E20",
               "#BF360C", "#F57C00", "#E65100", "#388E3C"}

LOW_N_THRESHOLD_DEFAULT = 3

# (col, label, color) — order/labels/colors must match my_token_roi.py
# TREND_LINES. Duplicated here (rather than imported) so charts.py stays a
# self-contained renderer with no dependency on the preprocessing package.
TREND_LINES = [
    ("output_tokens",          "output tokens / session",   "#D32F2F"),
    ("turn_count",             "turns / session",           "#5D4037"),
    ("duration_seconds",       "session duration (s)",      "#AD1457"),
    ("committed_lines",        "committed lines / session",  "#388E3C"),
    ("pr_count",               "PRs / session",             "#1B5E20"),
    ("agent_committed_lines",  "agent-committed lines",      "#E65100"),
    ("agent_total_edit_lines", "agent draft lines",          "#F57C00"),
]


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _fmt_week(week: str) -> str:
    """`2026-04-01` -> `Apr 01` (matches the source's `%b %d`)."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    try:
        y, m, d = week.split("-")
        return f"{months[int(m) - 1]} {int(d):02d}"
    except (ValueError, IndexError):
        return week


# ----------------------------------------------------------------------------
# Shared axis / scale helpers
# ----------------------------------------------------------------------------

class LogYMap:
    """Maps a positive value to a pixel y inside [top, top+height], with the
    top of the plot = vmax and the bottom = vmin (log scale)."""

    def __init__(self, vmin: float, vmax: float, top: float, height: float):
        self.lmin = math.log10(vmin)
        self.lmax = math.log10(vmax)
        self.top = top
        self.height = height
        if self.lmax <= self.lmin:
            self.lmax = self.lmin + 1.0

    def y(self, value: float) -> float:
        value = max(value, 1e-9)
        frac = (math.log10(value) - self.lmin) / (self.lmax - self.lmin)
        frac = min(max(frac, 0.0), 1.0)
        return self.top + (1.0 - frac) * self.height


def _nice_log_ticks(vmin: float, vmax: float) -> list:
    """Pick readable ticks (1,2,5 x powers of ten) spanning [vmin, vmax]."""
    ticks = []
    lo = math.floor(math.log10(vmin))
    hi = math.ceil(math.log10(vmax))
    for exp in range(lo, hi + 1):
        for mant in (1, 2, 5):
            t = mant * (10 ** exp)
            if vmin <= t <= vmax:
                ticks.append(t)
    if not ticks:
        ticks = [vmin, vmax]
    return ticks


def _fmt_tick(v: float) -> str:
    if v >= 1000:
        return f"{v:,.0f}"
    if v >= 1:
        return f"{v:g}"
    return f"{v:g}"


# ----------------------------------------------------------------------------
# SVG primitives
# ----------------------------------------------------------------------------

def _legend(items: list, x: float, y: float, *, swatch=True) -> str:
    """Vertical legend. items = [(label, color)]."""
    parts = []
    row_h = 18
    for i, (label, color) in enumerate(items):
        cy = y + i * row_h
        if swatch:
            parts.append(
                f'<rect x="{x}" y="{cy - 8}" width="12" height="12" '
                f'rx="2" fill="{color}"/>')
            tx = x + 18
        else:
            parts.append(
                f'<line x1="{x}" y1="{cy - 2}" x2="{x + 22}" y2="{cy - 2}" '
                f'stroke="{color}" stroke-width="3"/>')
            tx = x + 28
        parts.append(
            f'<text x="{tx}" y="{cy + 2}" {FONT} font-size="11" '
            f'fill="{TEXT}">{_esc(label)}</text>')
    return "".join(parts)


def _low_n_band(x0: float, x1: float, top: float, height: float) -> str:
    return (f'<rect x="{x0:.1f}" y="{top:.1f}" width="{max(x1 - x0, 0):.1f}" '
            f'height="{height:.1f}" fill="{LOW_N_SHADE}" opacity="0.55"/>')


# ----------------------------------------------------------------------------
# 1. Trend chart
# ----------------------------------------------------------------------------

def trend_svg(roi_data: dict, *, baseline_weeks: int = 2) -> str:
    trend = roi_data.get("trend", [])
    threshold = roi_data.get("meta", {}).get(
        "low_n_threshold", LOW_N_THRESHOLD_DEFAULT)
    if not trend:
        return _empty_svg("No session data for the trend chart.")

    # Baseline = mean of first `baseline_weeks` confident weeks (or first N).
    confident = [r for r in trend if r.get("confident")]
    base_rows = (confident[:baseline_weeks]
                 if len(confident) >= baseline_weeks
                 else trend[:baseline_weeks])

    # Build indexed series per line (skip all-null / no-baseline lines).
    series = []
    for col, label, color in TREND_LINES:
        raw = [r.get(col) for r in trend]
        if all(v is None for v in raw):
            continue
        base_vals = [r.get(col) for r in base_rows if r.get(col) is not None]
        base = sum(base_vals) / len(base_vals) if base_vals else 0.0
        if not (base and base > 0):
            non_zero = [v for v in raw if v is not None and v > 0]
            base = (sum(non_zero) / len(non_zero)) if non_zero else 1.0
        idx = [(v / base if (v is not None and base) else None) for v in raw]
        series.append((col, label, color, idx))

    if not series:
        return _empty_svg("No defined trend lines.")

    # Layout
    W, H = 900, 460
    ml, mr, mt, mb = 60, 250, 30, 60
    pw = W - ml - mr
    ph = H - mt - mb
    n = len(trend)
    xs = [ml + (pw * (i + 0.5) / n) for i in range(n)]

    # Fixed y-range matching the source (0.2x .. 20x).
    ymap = LogYMap(0.2, 20, mt, ph)
    yticks = [0.25, 0.5, 1, 2, 3, 5, 7, 10, 14]

    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
         f'width="100%" role="img" aria-label="Weekly trend, indexed to '
         f'baseline = 1.0x, log scale">']
    p.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>')

    # Low-N shaded weeks
    for i, r in enumerate(trend):
        if not r.get("confident"):
            x0 = ml + pw * i / n
            x1 = ml + pw * (i + 1) / n
            p.append(_low_n_band(x0, x1, mt, ph))

    # Y grid + ticks
    for t in yticks:
        y = ymap.y(t)
        p.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + pw}" y2="{y:.1f}" '
                 f'stroke="{GRID}" stroke-width="1"/>')
        p.append(f'<text x="{ml - 6}" y="{y + 3:.1f}" {FONT} font-size="10" '
                 f'fill="{MUTED}" text-anchor="end">{t:g}×</text>')

    # Baseline 1.0x dotted line
    y1 = ymap.y(1.0)
    p.append(f'<line x1="{ml}" y1="{y1:.1f}" x2="{ml + pw}" y2="{y1:.1f}" '
             f'stroke="#000" stroke-width="1" stroke-dasharray="2,3" '
             f'opacity="0.6"/>')

    # Lines + markers
    for col, label, color, idx in series:
        pts = [(xs[i], ymap.y(v)) for i, v in enumerate(idx) if v is not None]
        if len(pts) >= 2:
            d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            lw = 3.5 if col == "output_tokens" else 2.0
            p.append(f'<path d="{d}" fill="none" stroke="{color}" '
                     f'stroke-width="{lw}"/>')
        r = 4 if col == "output_tokens" else 3
        for x, y in pts:
            p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" '
                     f'fill="{color}"/>')

    # Axis frame
    p.append(f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" '
             f'stroke="{AXIS}" stroke-width="1"/>')
    p.append(f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" '
             f'stroke="{AXIS}" stroke-width="1"/>')

    # X labels (week + n=)
    for i, r in enumerate(trend):
        x = xs[i]
        p.append(f'<text x="{x:.1f}" y="{mt + ph + 16}" {FONT} font-size="9" '
                 f'fill="{TEXT}" text-anchor="middle">{_esc(_fmt_week(r["week"]))}</text>')
        p.append(f'<text x="{x:.1f}" y="{mt + ph + 28}" {FONT} font-size="8" '
                 f'fill="{MUTED}" text-anchor="middle">n={int(r["n"])}</text>')
    p.append(f'<text x="{ml + pw / 2:.1f}" y="{H - 6}" {FONT} font-size="10" '
             f'fill="{MUTED}" text-anchor="middle">week (Wed start)</text>')

    # Y label
    p.append(f'<text x="14" y="{mt + ph / 2:.1f}" {FONT} font-size="10" '
             f'fill="{MUTED}" text-anchor="middle" '
             f'transform="rotate(-90 14 {mt + ph / 2:.1f})">'
             f'median per session, indexed (log)</text>')

    # Legend
    legend_items = [(label, color) for _, label, color, _ in series]
    p.append(_legend(legend_items, ml + pw + 14, mt + 14, swatch=False))

    # Low-N note
    if any(not r.get("confident") for r in trend):
        p.append(f'<text x="{ml + 4}" y="{mt + 14}" {FONT} font-size="9" '
                 f'fill="{LOW_N_OUTLINE}">pink weeks: &lt; {threshold} sessions '
                 f'(low confidence)</text>')

    p.append("</svg>")
    return "".join(p)


# ----------------------------------------------------------------------------
# 2. Buckets small-multiples
# ----------------------------------------------------------------------------

def buckets_svg(roi_data: dict) -> str:
    panels = roi_data.get("buckets", [])
    threshold = roi_data.get("meta", {}).get(
        "low_n_threshold", LOW_N_THRESHOLD_DEFAULT)
    if not panels:
        return _empty_svg("No session data for the bucket chart.")

    cols, rows = 3, 2
    cell_w, cell_h = 300, 240
    pad_x, pad_y = 0, 0
    W = cols * cell_w
    H = rows * cell_h + 30  # extra row for footnote

    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
         f'width="100%" role="img" aria-label="Weekly outcome distributions">']
    p.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>')

    for idx, panel in enumerate(panels[:5]):
        cx = (idx % cols) * cell_w
        cy = (idx // cols) * cell_h
        p.append(_bucket_panel(panel, cx, cy, cell_w, cell_h, threshold))

    # 6th cell hidden (matches source 2x3 with one hidden).
    p.append(f'<text x="{W / 2:.0f}" y="{H - 8}" {FONT} font-size="10" '
             f'fill="{MUTED}" text-anchor="middle" font-style="italic">'
             f'Red dashed outline = weeks with &lt; {threshold} sessions '
             f'(distribution is noisy on small N).</text>')
    p.append("</svg>")
    return "".join(p)


def _bucket_panel(panel: dict, ox: float, oy: float, cw: float, ch: float,
                  threshold: int) -> str:
    weeks = panel.get("weeks", [])
    buckets = panel.get("buckets", [])
    labels = [b["label"] for b in buckets]
    colors = {b["label"]: b["color"] for b in buckets}

    ml, mr, mt, mb = 34, 8, 30, 40
    pw = cw - ml - mr
    ph = ch - mt - mb
    nw = max(len(weeks), 1)
    bar_w = pw / nw * 0.7
    gap = pw / nw

    p = [f'<text x="{ox + cw / 2:.0f}" y="{oy + 16}" {FONT} font-size="10" '
         f'font-weight="600" fill="{TEXT}" text-anchor="middle">'
         f'{_esc(panel["title"])}</text>']

    if not weeks:
        p.append(f'<text x="{ox + cw / 2:.0f}" y="{oy + ch / 2:.0f}" {FONT} '
                 f'font-size="10" fill="{MUTED}" text-anchor="middle">'
                 f'(no data)</text>')
        return "".join(p)

    top = oy + mt
    base_y = oy + mt + ph

    # y axis 0..100%
    for pct in (0, 25, 50, 75, 100):
        y = base_y - ph * pct / 100
        p.append(f'<line x1="{ox + ml}" y1="{y:.1f}" x2="{ox + ml + pw}" '
                 f'y2="{y:.1f}" stroke="{GRID}" stroke-width="1"/>')
        p.append(f'<text x="{ox + ml - 4}" y="{y + 3:.1f}" {FONT} '
                 f'font-size="8" fill="{MUTED}" text-anchor="end">{pct}</text>')

    for wi, wk in enumerate(weeks):
        x0 = ox + ml + gap * wi + (gap - bar_w) / 2
        shares = wk.get("shares", {})
        bottom = base_y
        for lab in labels:
            v = float(shares.get(lab, 0.0))
            if v <= 0:
                continue
            h = ph * v / 100.0
            y = bottom - h
            p.append(f'<rect x="{x0:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                     f'height="{h:.1f}" fill="{colors[lab]}" '
                     f'stroke="#FFFFFF" stroke-width="0.4"/>')
            if v >= 10:
                tc = "#FFFFFF" if colors[lab] in DARK_COLORS else "#222"
                p.append(f'<text x="{x0 + bar_w / 2:.1f}" '
                         f'y="{y + h / 2 + 3:.1f}" {FONT} font-size="7" '
                         f'fill="{tc}" text-anchor="middle">'
                         f'{int(round(v))}%</text>')
            bottom = y
        # Low-N outline
        if int(wk.get("n", 0)) < threshold:
            p.append(f'<rect x="{x0:.1f}" y="{top:.1f}" width="{bar_w:.1f}" '
                     f'height="{ph:.1f}" fill="none" stroke="{LOW_N_OUTLINE}" '
                     f'stroke-width="1.5" stroke-dasharray="3,2" '
                     f'opacity="0.7"/>')
        # x label
        cxm = x0 + bar_w / 2
        p.append(f'<text x="{cxm:.1f}" y="{base_y + 12:.1f}" {FONT} '
                 f'font-size="7" fill="{TEXT}" text-anchor="middle">'
                 f'{_esc(_fmt_week(wk["week"]))}</text>')
        p.append(f'<text x="{cxm:.1f}" y="{base_y + 21:.1f}" {FONT} '
                 f'font-size="6" fill="{MUTED}" text-anchor="middle">'
                 f'N={int(wk.get("n", 0))}</text>')

    return "".join(p)


# ----------------------------------------------------------------------------
# 3. Cost ratios chart
# ----------------------------------------------------------------------------

def cost_ratios_svg(roi_data: dict) -> str:
    ratios = roi_data.get("cost_ratios", [])
    reference = roi_data.get("reference", {})
    threshold = roi_data.get("meta", {}).get(
        "low_n_threshold", LOW_N_THRESHOLD_DEFAULT)
    if not ratios:
        return _empty_svg("No session data for the cost-ratio chart.")

    # Determine weeks from the first ratio (all share the same week list).
    weeks = ratios[0].get("weeks", [])
    n = len(weeks)
    if n == 0:
        return _empty_svg("No weeks in the cost-ratio data.")

    # Compute per-ratio per-week value = sum(num)/sum(den), None if den==0.
    series = []
    all_vals = []
    for rat in ratios:
        vals = []
        for wk in rat.get("weeks", []):
            den = wk.get("den_sum", 0)
            num = wk.get("num_sum", 0)
            v = (num / den) if den else None
            vals.append(v)
            if v is not None and v > 0:
                all_vals.append(v)
        if any(v is not None for v in vals):
            series.append((rat["label"], rat["color"], vals))

    # Include reference values in the y-range so the band is on-scale.
    for rv in (reference.get("tokens_per_pr"),
               reference.get("tokens_per_committed_line")):
        if rv:
            all_vals.append(float(rv))

    if not series or not all_vals:
        return _empty_svg("No defined cost ratios in any week.")

    vmin = max(min(all_vals) * 0.6, 0.1)
    vmax = max(all_vals) * 1.6

    W, H = 900, 470
    ml, mr, mt, mb = 70, 280, 30, 64
    pw = W - ml - mr
    ph = H - mt - mb
    xs = [ml + (pw * (i + 0.5) / n) for i in range(n)]
    ymap = LogYMap(vmin, vmax, mt, ph)

    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
         f'width="100%" role="img" aria-label="Token cost per unit of work, '
         f'weekly, log scale">']
    p.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>')

    # Low-N shading
    for i, wk in enumerate(weeks):
        if int(wk.get("n", 0)) < threshold:
            x0 = ml + pw * i / n
            x1 = ml + pw * (i + 1) / n
            p.append(_low_n_band(x0, x1, mt, ph))

    # Y grid
    for t in _nice_log_ticks(vmin, vmax):
        y = ymap.y(t)
        p.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + pw}" y2="{y:.1f}" '
                 f'stroke="{GRID}" stroke-width="1"/>')
        p.append(f'<text x="{ml - 6}" y="{y + 3:.1f}" {FONT} font-size="10" '
                 f'fill="{MUTED}" text-anchor="end">{_esc(_fmt_tick(t))}</text>')

    # Reference lines (SWE-chat band) — dashed horizontal markers.
    ref_lines = [
        ("SWE-chat tokens/PR ≈ {:,}".format(int(reference["tokens_per_pr"]))
         if reference.get("tokens_per_pr") else None,
         reference.get("tokens_per_pr"), "#1B5E20"),
        ("SWE-chat tokens/committed line ≈ {:,}".format(
            int(reference["tokens_per_committed_line"]))
         if reference.get("tokens_per_committed_line") else None,
         reference.get("tokens_per_committed_line"), "#388E3C"),
    ]
    for label, val, color in ref_lines:
        if not val:
            continue
        y = ymap.y(float(val))
        if mt <= y <= mt + ph:
            p.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + pw}" y2="{y:.1f}" '
                     f'stroke="{color}" stroke-width="1" '
                     f'stroke-dasharray="6,4" opacity="0.5"/>')

    # Lines (break on None denominators)
    for label, color, vals in series:
        seg = []
        for i, v in enumerate(vals):
            if v is None:
                if len(seg) >= 2:
                    d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in seg)
                    p.append(f'<path d="{d}" fill="none" stroke="{color}" '
                             f'stroke-width="2.2"/>')
                seg = []
                continue
            seg.append((xs[i], ymap.y(v)))
            p.append(f'<circle cx="{xs[i]:.1f}" cy="{ymap.y(v):.1f}" r="3" '
                     f'fill="{color}"/>')
        if len(seg) >= 2:
            d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in seg)
            p.append(f'<path d="{d}" fill="none" stroke="{color}" '
                     f'stroke-width="2.2"/>')

    # Axis frame
    p.append(f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" '
             f'stroke="{AXIS}" stroke-width="1"/>')
    p.append(f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" '
             f'stroke="{AXIS}" stroke-width="1"/>')

    # X labels
    for i, wk in enumerate(weeks):
        x = xs[i]
        p.append(f'<text x="{x:.1f}" y="{mt + ph + 16}" {FONT} font-size="9" '
                 f'fill="{TEXT}" text-anchor="middle">{_esc(_fmt_week(wk["week"]))}</text>')
        p.append(f'<text x="{x:.1f}" y="{mt + ph + 28}" {FONT} font-size="8" '
                 f'fill="{MUTED}" text-anchor="middle">n={int(wk.get("n", 0))}</text>')

    # Y label
    p.append(f'<text x="16" y="{mt + ph / 2:.1f}" {FONT} font-size="10" '
             f'fill="{MUTED}" text-anchor="middle" '
             f'transform="rotate(-90 16 {mt + ph / 2:.1f})">'
             f'tokens per unit — sum(tokens)/sum(denom) (log)</text>')

    # Legend
    legend_items = [(label, color) for label, color, _ in series]
    p.append(_legend(legend_items, ml + pw + 14, mt + 14, swatch=False))

    # Footnotes for missing denominators
    notes = []
    cl = next((r for r in ratios if r["den"] == "committed_lines"), None)
    if cl:
        nzero = sum(1 for wk in cl["weeks"] if not wk.get("den_sum"))
        if nzero:
            notes.append(f"committed-line ratios missing in {nzero} week(s) "
                         f"(no git data or no commits)")
    pr = next((r for r in ratios if r["den"] == "pr_count"), None)
    if pr:
        nzero = sum(1 for wk in pr["weeks"] if not wk.get("den_sum"))
        if nzero:
            notes.append(f"tokens-per-PR missing in {nzero} week(s) "
                         f"(no PRs that week)")
    ny = mt + ph + 44
    for note in notes:
        p.append(f'<text x="{ml}" y="{ny}" {FONT} font-size="8" '
                 f'fill="{MUTED}" font-style="italic">{_esc(note)}</text>')
        ny += 11

    # Reference band (labeled, right column under legend)
    rb_y = mt + 14 + len(legend_items) * 18 + 16
    rx = ml + pw + 14
    p.append(f'<text x="{rx}" y="{rb_y}" {FONT} font-size="10" '
             f'font-weight="600" fill="{TEXT}">SWE-chat reference</text>')
    rb_y += 16
    ref_rows = [
        ("tokens / PR", reference.get("tokens_per_pr")),
        ("tokens / committed line", reference.get("tokens_per_committed_line")),
        ("median output tokens", reference.get("median_session_output_tokens")),
        ("median agent committed lines", reference.get("median_agent_committed_lines")),
        ("median turns / session", reference.get("median_turns_per_session")),
    ]
    for rlabel, rval in ref_rows:
        if rval is None:
            continue
        p.append(f'<text x="{rx}" y="{rb_y}" {FONT} font-size="9" '
                 f'fill="{MUTED}">{_esc(rlabel)}: '
                 f'~{int(rval):,}</text>')
        rb_y += 13

    p.append("</svg>")
    return "".join(p)


# ----------------------------------------------------------------------------
# Empty-state placeholder
# ----------------------------------------------------------------------------

def _empty_svg(message: str) -> str:
    return (f'<svg viewBox="0 0 900 120" xmlns="http://www.w3.org/2000/svg" '
            f'width="100%" role="img" aria-label="{_esc(message)}">'
            f'<rect x="0" y="0" width="900" height="120" fill="#FAFAFA" '
            f'stroke="{GRID}"/>'
            f'<text x="450" y="64" {FONT} font-size="13" fill="{MUTED}" '
            f'text-anchor="middle">{_esc(message)}</text></svg>')
