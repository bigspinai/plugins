#!/usr/bin/env python3
"""charts.py — stdlib-only inline-SVG builders for the /skill-usage report.

Three static <svg> fragments (no <script>, no external deps) suitable for
inlining into a self-contained HTML report. Shares the visual chrome (brand
colors, DM Sans font binding, legend/empty-state primitives) with the
token-roi renderer so the two reports read as one design system.

The three figures:
  - ranking_svg      : horizontal stacked bars (reads + invocations) per
                       skill, most-used at top, never-used skills muted at the
                       bottom.
  - weekly_trend_svg : multi-line weekly event counts (total / reads /
                       invocations), low-N weeks shaded pink.
  - split_svg        : 100%-stacked reads-vs-invocations summary bar plus
                       per-skill mini splits for the top skills.
"""
from __future__ import annotations

import html

LOW_N_SHADE = "#FFCDD2"      # pink low-confidence week fill (semantic — keep)
LOW_N_OUTLINE = "#D32F2F"    # red note text
# Neutral chart chrome, aligned to the Bigspin brand palette (the same
# :root tokens the report shell uses) so axes/labels read as one design.
AXIS = "#4A554F"             # --text-2
GRID = "#E3E1DB"             # warm light rule
TEXT = "#1F2E24"             # --ink-soft
MUTED = "#7A8581"            # --muted
FONT = "font-family=\"'DM Sans',-apple-system,BlinkMacSystemFont,sans-serif\""

# Series colors. Reads dominate the corpus, so they get the brand accent
# green; invocations get a warm orange; the trend total gets accent purple.
READ_COLOR = "#3F784E"       # --accent (brand green)
INVOKE_COLOR = "#E65100"     # warm orange
TOTAL_COLOR = "#7760FB"      # accent purple (matches token-roi's emphasis line)
ZERO_COLOR = "#C9C6BF"       # muted gray for never-used skills

LOW_N_THRESHOLD_DEFAULT = 3


def _esc(s) -> str:
    return html.escape(str(s), quote=True)


def _fmt_week(week: str) -> str:
    """`2026-04-01` -> `Apr 01` (matches the token-roi renderer)."""
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    try:
        y, m, d = week.split("-")
        return f"{months[int(m) - 1]} {int(d):02d}"
    except (ValueError, IndexError):
        return week


def _truncate(s: str, n: int) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _nice_ceiling(v: float) -> int:
    """Smallest 'nice' integer >= v for a linear axis top."""
    if v <= 0:
        return 1
    for step in (1, 2, 3, 4, 5, 8, 10, 15, 20, 25, 30, 40, 50, 75,
                 100, 150, 200, 250, 300, 400, 500, 750, 1000):
        if v <= step:
            return step
    return int(((v // 1000) + 1) * 1000)


def _hlegend(items: list, x: float, y: float) -> str:
    """Horizontal legend with square swatches. items = [(label, color)]."""
    parts = []
    cx = x
    for label, color in items:
        parts.append(f'<rect x="{cx}" y="{y - 9}" width="12" height="12" '
                     f'rx="2" fill="{color}"/>')
        tx = cx + 17
        parts.append(f'<text x="{tx}" y="{y + 1}" {FONT} font-size="12" '
                     f'fill="{TEXT}">{_esc(label)}</text>')
        cx = tx + 8 + len(label) * 7.0 + 22
    return "".join(parts)


def _vlegend(items: list, x: float, y: float) -> str:
    """Vertical legend with line swatches. items = [(label, color)]."""
    parts = []
    row_h = 20
    for i, (label, color) in enumerate(items):
        cy = y + i * row_h
        parts.append(f'<line x1="{x}" y1="{cy - 2}" x2="{x + 22}" y2="{cy - 2}" '
                     f'stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text x="{x + 30}" y="{cy + 2}" {FONT} font-size="12" '
                     f'fill="{TEXT}">{_esc(label)}</text>')
    return "".join(parts)


def _empty_svg(message: str) -> str:
    return (f'<svg viewBox="0 0 900 120" xmlns="http://www.w3.org/2000/svg" '
            f'width="100%" role="img" aria-label="{_esc(message)}">'
            f'<rect x="0" y="0" width="900" height="120" fill="#FAFAFA" '
            f'stroke="{GRID}"/>'
            f'<text x="450" y="64" {FONT} font-size="13" fill="{MUTED}" '
            f'text-anchor="middle">{_esc(message)}</text></svg>')


# ----------------------------------------------------------------------------
# 1. Ranking chart — horizontal stacked bars
# ----------------------------------------------------------------------------

def ranking_svg(data: dict, *, n_used: int = 18, n_zero: int = 6) -> str:
    ranking = data.get("ranking", [])
    if not ranking:
        return _empty_svg("No skill-usage data for the ranking chart.")

    used = [r for r in ranking if r["total"] > 0]
    zero = [r for r in ranking if r["total"] == 0 and r.get("in_inventory")]
    shown_used = used[:n_used]
    shown_zero = zero[:n_zero]
    if not shown_used and not shown_zero:
        return _empty_svg("No skills to rank.")

    max_total = max((r["total"] for r in shown_used), default=1) or 1

    W = 900
    ml = 248          # label column width
    mr = 60           # value column
    mt = 52           # legend headroom
    row_h = 26
    bar_h = 13
    plot_w = W - ml - mr

    zero_block = (24 + len(shown_zero) * row_h) if shown_zero else 0
    H = mt + len(shown_used) * row_h + zero_block + 24

    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
         f'width="100%" role="img" aria-label="Skills ranked by usage, '
         f'reads plus invocations">']
    p.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>')

    # Legend
    p.append(_hlegend([("SKILL.md reads", READ_COLOR),
                       ("Skill invocations", INVOKE_COLOR)], ml, 26))

    # Used rows
    for i, r in enumerate(shown_used):
        y = mt + i * row_h
        cy = y + row_h / 2
        rank = i + 1
        label = f"{rank}. {_truncate(r['base_name'], 30)}"
        p.append(f'<text x="{ml - 10}" y="{cy + 4:.1f}" {FONT} font-size="12" '
                 f'fill="{TEXT}" text-anchor="end">{_esc(label)}</text>')
        read_w = r["reads"] / max_total * plot_w
        inv_w = r["invokes"] / max_total * plot_w
        bar_y = cy - bar_h / 2
        if read_w > 0:
            p.append(f'<rect x="{ml:.1f}" y="{bar_y:.1f}" width="{read_w:.1f}" '
                     f'height="{bar_h}" fill="{READ_COLOR}" rx="2"/>')
        if inv_w > 0:
            p.append(f'<rect x="{ml + read_w:.1f}" y="{bar_y:.1f}" '
                     f'width="{inv_w:.1f}" height="{bar_h}" '
                     f'fill="{INVOKE_COLOR}" rx="2"/>')
        end_x = ml + read_w + inv_w + 6
        p.append(f'<text x="{end_x:.1f}" y="{cy + 4:.1f}" {FONT} font-size="11" '
                 f'font-weight="600" fill="{TEXT}">{r["total"]}</text>')
        if not r.get("in_inventory"):
            # Skill used but not found on disk (e.g. an installed plugin skill).
            p.append(f'<text x="{end_x + 22:.1f}" y="{cy + 4:.1f}" {FONT} '
                     f'font-size="9" fill="{MUTED}">(not in repo)</text>')

    # Never-used block
    if shown_zero:
        sep_y = mt + len(shown_used) * row_h + 14
        n_total_zero = len(zero)
        more = f" (+{n_total_zero - len(shown_zero)} more)" if n_total_zero > len(shown_zero) else ""
        p.append(f'<text x="{ml - 10}" y="{sep_y:.1f}" {FONT} font-size="11" '
                 f'font-weight="700" fill="{MUTED}" text-anchor="end" '
                 f'letter-spacing="0.08em">NEVER USED{_esc(more)}</text>')
        p.append(f'<line x1="{ml}" y1="{sep_y - 4:.1f}" x2="{ml + plot_w:.1f}" '
                 f'y2="{sep_y - 4:.1f}" stroke="{GRID}" stroke-width="1"/>')
        for j, r in enumerate(shown_zero):
            y = sep_y + 8 + j * row_h
            cy = y + row_h / 2
            p.append(f'<text x="{ml - 10}" y="{cy + 4:.1f}" {FONT} '
                     f'font-size="12" fill="{MUTED}" text-anchor="end">'
                     f'{_esc(_truncate(r["base_name"], 30))}</text>')
            p.append(f'<rect x="{ml:.1f}" y="{cy - bar_h / 2:.1f}" width="6" '
                     f'height="{bar_h}" fill="{ZERO_COLOR}" rx="2"/>')
            p.append(f'<text x="{ml + 12:.1f}" y="{cy + 4:.1f}" {FONT} '
                     f'font-size="11" fill="{MUTED}">0</text>')

    p.append("</svg>")
    return "".join(p)


# ----------------------------------------------------------------------------
# 2. Weekly trend chart — linear multi-line
# ----------------------------------------------------------------------------

def weekly_trend_svg(data: dict) -> str:
    trend = data.get("trend", [])
    threshold = data.get("meta", {}).get(
        "low_n_threshold", LOW_N_THRESHOLD_DEFAULT)
    if not trend:
        return _empty_svg("No skill-usage data for the trend chart.")

    W, H = 900, 430
    ml, mr, mt, mb = 48, 190, 30, 58
    pw = W - ml - mr
    ph = H - mt - mb
    n = len(trend)
    xs = [ml + (pw * (i + 0.5) / n) for i in range(n)]

    vmax = max((int(r.get("total") or 0) for r in trend), default=0)
    ymax = _nice_ceiling(vmax)

    def ymap(v: float) -> float:
        return mt + ph * (1.0 - (v / ymax if ymax else 0.0))

    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
         f'width="100%" role="img" aria-label="Weekly skill-usage events">']
    p.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>')

    # Low-N shaded weeks
    for i, r in enumerate(trend):
        if not r.get("confident"):
            x0 = ml + pw * i / n
            x1 = ml + pw * (i + 1) / n
            p.append(f'<rect x="{x0:.1f}" y="{mt:.1f}" width="{x1 - x0:.1f}" '
                     f'height="{ph:.1f}" fill="{LOW_N_SHADE}" opacity="0.55"/>')

    # Y grid + ticks (linear)
    n_ticks = 4
    for t in range(n_ticks + 1):
        val = ymax * t / n_ticks
        y = ymap(val)
        p.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{ml + pw}" y2="{y:.1f}" '
                 f'stroke="{GRID}" stroke-width="1"/>')
        p.append(f'<text x="{ml - 6}" y="{y + 3:.1f}" {FONT} font-size="10" '
                 f'fill="{MUTED}" text-anchor="end">{int(round(val))}</text>')

    # Series
    series = [
        ("total", "events / week", TOTAL_COLOR, 3.5, 4),
        ("reads", "SKILL.md reads", READ_COLOR, 2.0, 3),
        ("invokes", "Skill invocations", INVOKE_COLOR, 2.0, 3),
    ]
    for col, _label, color, lw, r_ in series:
        pts = [(xs[i], ymap(int(rrow.get(col) or 0)))
               for i, rrow in enumerate(trend)]
        if len(pts) >= 2:
            d = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            p.append(f'<path d="{d}" fill="none" stroke="{color}" '
                     f'stroke-width="{lw}"/>')
        for x, y in pts:
            p.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r_}" '
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
                 f'fill="{TEXT}" text-anchor="middle">'
                 f'{_esc(_fmt_week(r["week"]))}</text>')
        p.append(f'<text x="{x:.1f}" y="{mt + ph + 28}" {FONT} font-size="8" '
                 f'fill="{MUTED}" text-anchor="middle">n={int(r["n"])}</text>')
    p.append(f'<text x="{ml + pw / 2:.1f}" y="{H - 6}" {FONT} font-size="10" '
             f'fill="{MUTED}" text-anchor="middle">week (Wed start)</text>')

    # Y label
    p.append(f'<text x="13" y="{mt + ph / 2:.1f}" {FONT} font-size="10" '
             f'fill="{MUTED}" text-anchor="middle" '
             f'transform="rotate(-90 13 {mt + ph / 2:.1f})">'
             f'skill-usage events / week</text>')

    # Legend
    p.append(_vlegend([(label, color) for col, label, color, _, _ in series],
                      ml + pw + 16, mt + 14))

    # Low-N note
    if any(not r.get("confident") for r in trend):
        p.append(f'<text x="{ml + pw + 16}" y="{mt + 14 + 3 * 20 + 12}" {FONT} '
                 f'font-size="10" fill="{LOW_N_OUTLINE}">pink weeks: &lt; '
                 f'{threshold} events</text>')

    p.append("</svg>")
    return "".join(p)


# ----------------------------------------------------------------------------
# 3. Reads-vs-invocations split
# ----------------------------------------------------------------------------

def split_svg(data: dict) -> str:
    split = data.get("split", {})
    ti = int(split.get("total_invokes", 0))
    tr = int(split.get("total_reads", 0))
    total = ti + tr
    if total == 0:
        return _empty_svg("No skill-usage data for the split chart.")

    sidechain = int(split.get("sidechain_reads", 0))
    per_skill = split.get("per_skill", [])

    W = 900
    ml, mr = 16, 16
    bar_w = W - ml - mr

    # Build the body first so we can size the viewBox to the content height.
    body = []
    # ---- Main 100%-stacked bar ----
    y = 8
    main_h = 50
    read_w = tr / total * bar_w
    inv_w = ti / total * bar_w
    if read_w > 0:
        body.append(f'<rect x="{ml}" y="{y}" width="{read_w:.1f}" '
                    f'height="{main_h}" fill="{READ_COLOR}" rx="6"/>')
    if inv_w > 0:
        body.append(f'<rect x="{ml + read_w:.1f}" y="{y}" width="{inv_w:.1f}" '
                    f'height="{main_h}" fill="{INVOKE_COLOR}" rx="6"/>')
    # In-bar labels
    read_pct = tr / total * 100
    inv_pct = ti / total * 100
    if read_w > 90:
        body.append(f'<text x="{ml + 14}" y="{y + 22}" {FONT} font-size="15" '
                    f'font-weight="700" fill="#FFFFFF">{tr} reads</text>')
        body.append(f'<text x="{ml + 14}" y="{y + 40}" {FONT} font-size="12" '
                    f'fill="#FFFFFF" opacity="0.9">{read_pct:.0f}%</text>')
    if inv_w > 90:
        body.append(f'<text x="{ml + read_w + 14:.1f}" y="{y + 22}" {FONT} '
                    f'font-size="15" font-weight="700" fill="#FFFFFF">'
                    f'{ti} invocations</text>')
        body.append(f'<text x="{ml + read_w + 14:.1f}" y="{y + 40}" {FONT} '
                    f'font-size="12" fill="#FFFFFF" opacity="0.9">'
                    f'{inv_pct:.0f}%</text>')

    # ---- Sidechain note ----
    y = 8 + main_h + 22
    if tr > 0:
        sc_pct = sidechain / tr * 100 if tr else 0
        body.append(f'<text x="{ml}" y="{y}" {FONT} font-size="12" '
                    f'fill="{MUTED}">Of {tr} reads, {sidechain} '
                    f'({sc_pct:.0f}%) came from subagents (sidechain).</text>')

    # ---- Per-skill mini splits ----
    y += 26
    if per_skill:
        body.append(f'<text x="{ml}" y="{y}" {FONT} font-size="12" '
                    f'font-weight="700" fill="{TEXT}" letter-spacing="0.06em">'
                    f'TOP SKILLS — reads vs invocations</text>')
        y += 14
        label_w = 200
        row_h = 24
        mini_bar_w = bar_w - label_w - 60
        for r in per_skill:
            st = r["reads"] + r["invokes"]
            if st <= 0:
                continue
            cy = y + row_h / 2
            body.append(f'<text x="{ml + label_w - 8}" y="{cy + 4:.1f}" {FONT} '
                        f'font-size="12" fill="{TEXT}" text-anchor="end">'
                        f'{_esc(_truncate(r["base_name"], 26))}</text>')
            rx = ml + label_w
            rw = r["reads"] / st * mini_bar_w
            iw = r["invokes"] / st * mini_bar_w
            if rw > 0:
                body.append(f'<rect x="{rx:.1f}" y="{cy - 7:.1f}" '
                            f'width="{rw:.1f}" height="14" '
                            f'fill="{READ_COLOR}" rx="2"/>')
            if iw > 0:
                body.append(f'<rect x="{rx + rw:.1f}" y="{cy - 7:.1f}" '
                            f'width="{iw:.1f}" height="14" '
                            f'fill="{INVOKE_COLOR}" rx="2"/>')
            body.append(f'<text x="{rx + mini_bar_w + 8:.1f}" y="{cy + 4:.1f}" '
                        f'{FONT} font-size="11" fill="{MUTED}">{st}</text>')
            y += row_h

    H = int(y + 16)
    p = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
         f'width="100%" role="img" '
         f'aria-label="Reads versus invocations split">']
    p.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="#FFFFFF"/>')
    p.extend(body)
    p.append("</svg>")
    return "".join(p)
