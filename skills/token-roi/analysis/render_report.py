#!/usr/bin/env python3
"""Render the token-roi report from a single roi_data.json.

Reads:
  - roi_data.json   (emitted by preprocessing/compute_roi.py, schema-validated)

Writes (to --out):
  - report.html     self-contained HTML with three inline-SVG charts
  - hero.md         a tight summary for inline chat paste

The data JSON is validated against roi_data.schema.json before rendering;
a schema failure aborts with a clear diff so the orchestrator can fix and
retry. No network calls. The CSV is produced by compute_roi.py, not here.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import jsonschema
from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "roi_data.schema.json"
TEMPLATES_DIR = HERE / "templates"

# Make charts.py importable whether invoked as a script or a module.
sys.path.insert(0, str(HERE))
import charts  # noqa: E402


def load_and_validate(data_path: Path) -> dict:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    data = json.loads(data_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    if errors:
        msgs = []
        for e in errors:
            where = ".".join(str(p) for p in e.path) or "<root>"
            msgs.append(f"  at {where}: {e.message}")
        print(f"\nschema validation failed for {data_path}:\n"
              + "\n".join(msgs), file=sys.stderr)
        raise SystemExit(2)
    return data


def _fmt_int(v) -> str:
    if v is None:
        return "—"
    return f"{int(round(float(v))):,}"


def _trend_direction(roi_data: dict) -> str:
    """Headline direction of output_tokens/session across confident weeks."""
    # Only weeks with a positive median count: a trailing in-progress week
    # (or any week with a degenerate 0 median) must not anchor the headline,
    # or the ratio collapses to ~0.0× and reads as a nonsense "falling" call.
    confident = [r for r in roi_data.get("trend", [])
                 if r.get("confident") and (r.get("output_tokens") or 0) > 0]
    if len(confident) < 2:
        return "flat (not enough confident weeks to call a trend)"
    first, last = confident[0]["output_tokens"], confident[-1]["output_tokens"]
    ratio = last / first
    if ratio >= 1.15:
        return f"rising (~{ratio:.1f}× output tokens/session vs. your first weeks)"
    if ratio <= 0.87:
        return f"falling (~{ratio:.1f}× output tokens/session vs. your first weeks)"
    return "roughly flat (output tokens/session steady)"


def _tokens_per_pr(roi_data: dict):
    """Aggregate tokens / PR across the whole window, or None."""
    pr = next((r for r in roi_data.get("cost_ratios", [])
               if r["den"] == "pr_count"), None)
    if not pr:
        return None
    num = sum(wk.get("num_sum", 0) for wk in pr["weeks"])
    den = sum(wk.get("den_sum", 0) for wk in pr["weeks"])
    return (num / den) if den else None


def build_context(roi_data: dict) -> dict:
    meta = roi_data["meta"]
    summary = roi_data["summary"]
    dr = summary["date_range"]
    n = meta["n_sessions"]

    summary_line = (
        f"{n} session{'s' if n != 1 else ''} across "
        f"{dr.get('earliest') or '?'} → {dr.get('latest') or '?'}, "
        f"last {meta['days']} days. "
        f"Output tokens/session are {_trend_direction(roi_data)}."
    )

    tpr = _tokens_per_pr(roi_data)
    stat_cards = [
        {"label": "sessions", "value": _fmt_int(n)},
        {"label": "median output tokens", "value": _fmt_int(summary.get("median_output_tokens"))},
        {"label": "median turns", "value": _fmt_int(summary.get("median_turns"))},
    ]
    if tpr is not None:
        stat_cards.append({"label": "tokens / PR", "value": _fmt_int(tpr)})

    return {
        "summary_line": summary_line,
        "stat_cards": stat_cards,
        "reference": roi_data["reference"],
        "trend_svg": charts.trend_svg(roi_data),
        "buckets_svg": charts.buckets_svg(roi_data),
        "cost_ratios_svg": charts.cost_ratios_svg(roi_data),
    }


def render_html(roi_data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    template = env.get_template("roi_report.html.j2")
    return template.render(**build_context(roi_data))


def render_hero(roi_data: dict) -> str:
    meta = roi_data["meta"]
    summary = roi_data["summary"]
    dr = summary["date_range"]
    n = meta["n_sessions"]
    tpr = _tokens_per_pr(roi_data)

    lines = [
        "# Your Claude Code — token ROI",
        "",
        f"- **Window:** {dr.get('earliest') or '?'} → {dr.get('latest') or '?'} "
        f"(last {meta['days']} days)",
        f"- **Sessions:** {n}",
        f"- **Median output tokens / session:** {_fmt_int(summary.get('median_output_tokens'))}",
        f"- **Median turns / session:** {_fmt_int(summary.get('median_turns'))}",
    ]
    if tpr is not None:
        lines.append(f"- **Tokens / PR (window):** {_fmt_int(tpr)}")
    else:
        lines.append("- **Tokens / PR:** n/a (no PRs detected"
                     + ("" if meta.get("git_enabled") else " / git disabled")
                     + ")")
    lines.append(f"- **Trend:** output tokens/session are "
                 f"{_trend_direction(roi_data)}")
    lines.append("")
    lines.append("_Everything runs locally. No upload, no telemetry._")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Render roi_data.json into report.html + hero.md.")
    parser.add_argument("--data", type=Path, required=True,
                        help="Path to roi_data.json.")
    parser.add_argument("--out", type=Path, required=True,
                        help="Output directory.")
    args = parser.parse_args(argv)

    if not args.data.exists():
        print(f"data JSON not found: {args.data}", file=sys.stderr)
        return 2

    roi_data = load_and_validate(args.data)
    args.out.mkdir(parents=True, exist_ok=True)

    html_path = args.out / "report.html"
    html_path.write_text(render_html(roi_data), encoding="utf-8")

    hero_path = args.out / "hero.md"
    hero_path.write_text(render_hero(roi_data), encoding="utf-8")

    print("=" * 64)
    print("Rendered:")
    print(f"  {html_path}")
    print(f"  {hero_path}")
    print("=" * 64)
    print(f"  open {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
