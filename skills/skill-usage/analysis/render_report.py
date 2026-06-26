#!/usr/bin/env python3
"""Render the skill-usage report from a single usage_data.json.

Reads:
  - usage_data.json   (emitted by preprocessing/compute_usage.py, schema-validated)

Writes (to --out, prefixed by --slug; bases defined in lib/report_io.py):
  - <slug>-report.html     self-contained HTML with three inline-SVG charts
  - <slug>-hero.md         a tight summary for inline chat paste

The data JSON is validated against usage_data.schema.json before rendering;
a schema failure aborts with a clear diff so the orchestrator can fix and
retry. No network calls. The CSV is produced by compute_usage.py, not here.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

import jsonschema
from jinja2 import Environment, FileSystemLoader, select_autoescape

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "usage_data.schema.json"
TEMPLATES_DIR = HERE / "templates"

# Brand assets (font, logos) are base64-embedded at render time so the report
# is a single self-contained file with no external asset fetches.
_MIME_BY_EXT = {
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def _b64_asset(rel_path: str) -> str:
    """Return a data: URI for a file under templates/. Empty string if missing."""
    p = TEMPLATES_DIR / rel_path
    if not p.is_file():
        return ""
    mime = _MIME_BY_EXT.get(p.suffix.lower(), "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode('ascii')}"


# Make charts.py importable whether invoked as a script or a module.
sys.path.insert(0, str(HERE))
import charts  # noqa: E402

# Shared filename scheme from the plugin-root lib/ (also on PYTHONPATH via
# scripts/new_run.sh; the sys.path insert keeps standalone/CI runs working).
sys.path.insert(0, str(HERE.parents[2] / "lib"))
import report_io  # noqa: E402


# Display metadata for each candidate flag — title, blurb, and the order they
# appear in the report. Keys must match the schema enum.
FLAG_META = [
    ("retire-or-refresh", "Retire or refresh",
     "In the inventory but never used, and the file is stale."),
    ("refine", "Refine (hot but stale)",
     "Read often, but the file hasn't changed recently — likely drifted."),
    ("never-triggered", "Never triggered yet",
     "In the inventory, never used, but recently authored — sharpen triggers or give it time."),
    ("invocation-only", "Invoked, never read",
     "Triggered via the Skill tool, but the SKILL.md is never opened."),
    ("read-only", "Read, never invoked",
     "Opened as reference docs, but never invoked as a skill."),
]


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


def _split_phrase(data: dict) -> str:
    """A short 'reads outnumber invocations N to 1' style phrase."""
    nr = data["meta"]["n_reads"]
    ni = data["meta"]["n_invokes"]
    if ni == 0 and nr == 0:
        return "no usage recorded"
    if ni == 0:
        return "every event is a SKILL.md read"
    if nr == 0:
        return "every event is a Skill-tool invocation"
    if nr >= ni:
        ratio = nr / ni
        return f"reads outnumber invocations ~{ratio:.0f}:1"
    ratio = ni / nr
    return f"invocations outnumber reads ~{ratio:.0f}:1"


def build_context(data: dict) -> dict:
    meta = data["meta"]
    summary = data["summary"]
    dr = summary["date_range"]

    summary_line = (
        f"{_fmt_int(meta['n_events'])} skill-usage events across "
        f"{meta['n_skills_used']} skills, "
        f"{dr.get('earliest') or '?'} → {dr.get('latest') or '?'}. "
        f"{_split_phrase(data).capitalize()}; "
        f"{summary['zero_usage_count']} skills in your repo were never used."
    )

    stat_cards = [
        {"label": "skill-usage events", "value": _fmt_int(meta["n_events"])},
        {"label": "skills used", "value": _fmt_int(meta["n_skills_used"])},
        {"label": "SKILL.md reads", "value": _fmt_int(meta["n_reads"])},
        {"label": "Skill invocations", "value": _fmt_int(meta["n_invokes"])},
        {"label": "never used", "value": _fmt_int(summary["zero_usage_count"])},
    ]

    # Group candidates by flag, preserving FLAG_META order.
    cand_by_flag = {}
    for c in data.get("candidates", []):
        cand_by_flag.setdefault(c["flag"], []).append(c)
    candidate_groups = []
    for flag, title, blurb in FLAG_META:
        items = cand_by_flag.get(flag, [])
        if items:
            candidate_groups.append({
                "flag": flag,
                "title": title,
                "blurb": blurb,
                "skills": sorted(items, key=lambda c: c["base_name"]),
                "count": len(items),
            })

    return {
        "summary_line": summary_line,
        "stat_cards": stat_cards,
        "meta": meta,
        "summary": summary,
        "candidate_groups": candidate_groups,
        "n_candidates": len(data.get("candidates", [])),
        "ranking_svg": charts.ranking_svg(data),
        "weekly_trend_svg": charts.weekly_trend_svg(data),
        "split_svg": charts.split_svg(data),
    }


def render_html(data: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["b64_asset"] = _b64_asset
    template = env.get_template("usage_report.html.j2")
    return template.render(**build_context(data))


def render_hero(data: dict) -> str:
    meta = data["meta"]
    summary = data["summary"]
    dr = summary["date_range"]
    ranking = data.get("ranking", [])
    used = [r for r in ranking if r["total"] > 0]

    lines = [
        "# Your Claude Code — skill usage",
        "",
        f"- **Window:** {dr.get('earliest') or '?'} → {dr.get('latest') or '?'} "
        f"({meta['n_events']:,} events)",
        f"- **Skills used:** {meta['n_skills_used']} of "
        f"{meta['n_skills_inventory']} in your repo "
        f"({summary['zero_usage_count']} never used)",
        f"- **Split:** {meta['n_reads']:,} SKILL.md reads / "
        f"{meta['n_invokes']:,} Skill invocations "
        f"({_split_phrase(data)})",
    ]
    if used:
        top5 = ", ".join(f"{r['base_name']} ({r['total']})" for r in used[:5])
        lines.append(f"- **Most used:** {top5}")
    if summary.get("most_invoked"):
        lines.append(f"- **Most invoked:** {summary['most_invoked']}")
    lines.append(f"- **Update candidates flagged:** "
                 f"{len(data.get('candidates', []))}")
    lines.append("")
    lines.append("_Everything runs locally. No upload, no telemetry._")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Render usage_data.json into <slug>-report.html + <slug>-hero.md.")
    parser.add_argument("--data", type=Path, required=True,
                        help="Path to usage_data.json.")
    parser.add_argument("--out", type=Path, required=True,
                        help="Output directory.")
    parser.add_argument("--slug", default="",
                        help="Filename prefix, e.g. 'skill-usage' -> "
                             "skill-usage-report.html. Empty (default) yields "
                             "bare names (report.html).")
    args = parser.parse_args(argv)

    if not args.data.exists():
        print(f"data JSON not found: {args.data}", file=sys.stderr)
        return 2

    data = load_and_validate(args.data)
    args.out.mkdir(parents=True, exist_ok=True)

    html_path = report_io.out_path(args.out, args.slug, report_io.REPORT_HTML)
    html_path.write_text(render_html(data), encoding="utf-8")

    hero_path = report_io.out_path(args.out, args.slug, report_io.HERO_MD)
    hero_path.write_text(render_hero(data), encoding="utf-8")

    print("=" * 64)
    print("Rendered:")
    print(f"  {html_path}")
    print(f"  {hero_path}")
    print("=" * 64)
    print(f"  open {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
