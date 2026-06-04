#!/usr/bin/env python3
"""compute_roi.py — analyze your own Claude Code token usage and outcomes.

Stdlib-only data layer for the /token-roi skill. Scans your local
~/.claude/projects/ for session transcripts from the last 3 months and
emits:

  - roi_data.json   : everything the SVG charts + hero card need
                      (meta, summary, trend, buckets, cost_ratios, reference)
  - roi.csv         : anonymized per-week aggregates (optional)

What gets aggregated per session (always — from transcripts only):
  - output_tokens, input_tokens, cache tokens
  - turn count, tool call count, duration
  - model (dominant)
  - agent draft lines via Edit/Write/MultiEdit, by file category (code/docs/other)
  - PRs opened (from `pr-link` events in transcripts)
  - files touched

Additionally (best-effort — needs local git in the session's cwd):
  - committed lines per session (any author) — from git log --stat in window
  - agent-attributed committed lines — added lines from those commits that
    EXACTLY match a line the agent emitted via Edit/Write/MultiEdit.
    This is a simpler attribution than SWE-chat's: exact-line match, no
    fuzzy matching, no handling of small edits or whitespace differences.
    Read as a lower bound.

Privacy:
  - No file paths, repo names, prompts, or tool-call content leave your machine.
  - Everything runs locally. No upload, no telemetry.
  - The CSV / JSON contain weekly aggregate counts only.

Usage:
  python3 compute_roi.py --out RUN_DIR                # defaults: ~/.claude/projects/, last 90d
  python3 compute_roi.py --out RUN_DIR --projects-dir DIR
  python3 compute_roi.py --out RUN_DIR --days 60      # change lookback window
  python3 compute_roi.py --out RUN_DIR --no-git       # skip git-based outcomes
  python3 compute_roi.py --out RUN_DIR --no-csv       # skip CSV output

Dependencies: Python 3.9+ (standard library only — no pandas/numpy/matplotlib).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

LOW_N_THRESHOLD = 3   # weeks with fewer sessions get "low confidence" treatment

EDIT_TOOLS = {"Edit", "Write", "MultiEdit"}

CODE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".rb", ".java", ".kt", ".scala", ".clj", ".cljs",
    ".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx",
    ".cs", ".fs", ".vb", ".swift", ".m", ".mm",
    ".php", ".pl", ".pm", ".lua", ".r", ".jl", ".dart",
    ".sh", ".bash", ".zsh", ".fish", ".ps1",
    ".sql", ".graphql", ".gql", ".proto",
    ".css", ".scss", ".sass", ".less",
    ".html", ".htm", ".xml", ".vue", ".svelte", ".astro",
    ".elm", ".hs", ".ml", ".ex", ".exs",
}
DOC_EXTS = {".md", ".mdx", ".rst", ".txt", ".adoc", ".org"}


# ----------------------------------------------------------------------------
# Trend / bucket / cost-ratio specs (mirrors my_token_roi.py)
# ----------------------------------------------------------------------------

# (col, label, color). Only `col` is consumed here (see metric_cols below);
# the display labels/colors live in analysis/charts.py, which is the source of
# truth for the rendered legend. Kept here in sync for readability only.
TREND_LINES = [
    ("output_tokens",          "output tokens / session",   "#D32F2F"),
    ("turn_count",             "turns / session",           "#5D4037"),
    ("duration_seconds",       "session duration (s)",      "#AD1457"),
    ("committed_lines",        "committed lines / session", "#388E3C"),
    ("pr_count",               "PRs / session",             "#1B5E20"),
    ("agent_committed_lines",  "agent-committed lines",     "#E65100"),
    ("agent_total_edit_lines", "agent draft lines",         "#F57C00"),
]

BUCKETS_LINES_COMMITTED = [
    ("0",        "#ECEFF1"),
    ("1–10",     "#B0BEC5"),
    ("11–100",   "#64B5F6"),
    ("101–1000", "#1976D2"),
    ("1000+",    "#0D47A1"),
]
BUCKETS_DRAFT = [
    ("0",        "#ECEFF1"),
    ("1–10",     "#FFE0B2"),
    ("11–100",   "#FFB74D"),
    ("101–1000", "#F57C00"),
    ("1000+",    "#BF360C"),
]
BUCKETS_FILES = [
    ("0",   "#ECEFF1"),
    ("1",   "#A5D6A7"),
    ("2–5",  "#66BB6A"),
    ("6–20", "#388E3C"),
    ("21+", "#1B5E20"),
]
BUCKETS_PR = [
    ("0",  "#ECEFF1"),
    ("1",  "#81C784"),
    ("2+", "#1B5E20"),
]


def bucket_lines(v) -> str:
    v = 0 if v is None else v
    if v == 0:
        return "0"
    if v <= 10:
        return "1–10"
    if v <= 100:
        return "11–100"
    if v <= 1000:
        return "101–1000"
    return "1000+"


def bucket_files(v) -> str:
    v = 0 if v is None else v
    if v == 0:
        return "0"
    if v == 1:
        return "1"
    if v <= 5:
        return "2–5"
    if v <= 20:
        return "6–20"
    return "21+"


def bucket_pr(v) -> str:
    v = 0 if v is None else v
    if v == 0:
        return "0"
    if v == 1:
        return "1"
    return "2+"


# (col, bucket_fn, buckets_palette, title)
PANEL_SPECS = [
    ("committed_lines",        bucket_lines, BUCKETS_LINES_COMMITTED,
     "Committed lines per session (any author)"),
    ("agent_committed_lines",  bucket_lines, BUCKETS_DRAFT,
     "Agent-attributed committed lines"),
    ("agent_total_edit_lines", bucket_lines, BUCKETS_DRAFT,
     "Agent draft lines total (Edit/Write/MultiEdit)"),
    ("n_files_touched",        bucket_files, BUCKETS_FILES,
     "Files touched per session"),
    ("pr_count",               bucket_pr,    BUCKETS_PR,
     "PRs opened per session"),
]

# (numerator_col, denominator_col, label, color)
COST_RATIOS = [
    ("output_tokens", "pr_count",               "tokens / PR",                            "#1B5E20"),
    ("output_tokens", "agent_committed_lines",  "tokens / agent-committed line",          "#E65100"),
    ("output_tokens", "committed_lines",        "tokens / committed line (any author)",   "#388E3C"),
    ("output_tokens", "agent_total_edit_lines", "tokens / agent draft line (Edit/Write)", "#F57C00"),
    ("output_tokens", "turn_count",             "tokens / turn",                          "#5D4037"),
]

# SWE-chat reference block — kept VERBATIM from my_token_roi.py. opus-4-6
# phase D (high-effort restored, Apr 7-19 2026) aggregate ratios, for
# context only.
REFERENCE = {
    "label": "SWE-chat opus-4-6 reference (phase D = high-effort restored, Apr 7-19 2026)",
    "tokens_per_pr": 63874,
    "tokens_per_committed_line": 169,
    "median_session_output_tokens": 19114,
    "median_agent_committed_lines": 19,
    "median_turns_per_session": 11,
    "caveat": (
        "SWE-chat is opus-4-6. If you're on 4-7 or 4-8 these numbers are not a "
        "fair baseline — treat them as a historical reference point, not a target."
    ),
}

# Anonymized CSV columns (mirrors my_token_roi.py ANONYMIZED_CSV_COLUMNS).
ANONYMIZED_CSV_COLUMNS = [
    "week", "n_sessions", "n_with_model",
    "models_top", "models_top_share",
    "output_tokens_median", "output_tokens_mean",
    "input_tokens_median", "cache_read_tokens_median",
    "turns_median", "duration_seconds_median",
    "tool_calls_median", "files_touched_median",
    "agent_total_edit_lines_median", "agent_code_lines_median",
    "agent_docs_lines_median", "agent_other_lines_median",
    "committed_lines_median", "agent_committed_lines_median",
    "pr_count_mean",
]


# ----------------------------------------------------------------------------
# Small stdlib helpers (replace pandas/numpy)
# ----------------------------------------------------------------------------

def _median(values: list) -> Optional[float]:
    """Median of a list, ignoring None. Returns None if no values remain."""
    nums = sorted(float(v) for v in values if v is not None)
    n = len(nums)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return nums[mid]
    return (nums[mid - 1] + nums[mid]) / 2.0


def _mean(values: list) -> Optional[float]:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp into a tz-aware UTC datetime."""
    if not isinstance(ts, str):
        return None
    s = ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _week_start_wtue(dt: datetime) -> datetime:
    """Return the Wednesday (naive, midnight) that begins the W-TUE week.

    pandas ``to_period("W-TUE")`` defines weeks that END on Tuesday, so they
    START on Wednesday. weekday(): Mon=0 .. Sun=6, Wed=2. The week-start is
    the most recent Wednesday on or before the (naive, local-clock) date.
    """
    naive = dt.replace(tzinfo=None)
    day = datetime(naive.year, naive.month, naive.day)
    # Days since the most recent Wednesday (weekday 2).
    offset = (day.weekday() - 2) % 7
    return day - timedelta(days=offset)


# ----------------------------------------------------------------------------
# Transcript parsing (VERBATIM stdlib logic from my_token_roi.py)
# ----------------------------------------------------------------------------

def classify_file(path: Optional[str]) -> str:
    if not isinstance(path, str):
        return "other"
    lower = path.lower()
    for ext in CODE_EXTS:
        if lower.endswith(ext):
            return "code"
    for ext in DOC_EXTS:
        if lower.endswith(ext):
            return "docs"
    return "other"


def count_lines(s: str) -> int:
    if not s:
        return 0
    n = s.count("\n")
    if s and not s.endswith("\n"):
        n += 1
    return n


def parse_transcript(transcript_path: Path) -> dict:
    """Pull per-session metrics from one Claude Code transcript JSONL.

    Returns a dict with all the per-session fields the chart needs. Also
    returns the SET of normalized lines the agent emitted via Edit/Write/
    MultiEdit (``agent_emitted_lines``) — used downstream for git-based
    attribution.
    """
    sid = transcript_path.stem
    stats: dict = {
        "session_id": sid,
        "start": None, "end": None,
        "cwd": None,
        "model": None,
        "output_tokens": 0, "input_tokens": 0,
        "cache_read_tokens": 0, "cache_creation_tokens": 0,
        "turn_count": 0, "tool_call_count": 0,
        "agent_code_lines": 0, "agent_docs_lines": 0, "agent_other_lines": 0,
        "n_edit_ops": 0, "n_write_ops": 0, "n_multiedit_ops": 0,
        "pr_count": 0,
        "agent_emitted_lines": [],  # used only for git attribution; not saved
    }
    touched: set = set()
    models: Counter = Counter()
    emitted_lines: set = set()
    try:
        with transcript_path.open(errors="ignore") as f:
            for line in f:
                # pr-link substring scan
                if '"pr-link"' in line:
                    stats["pr_count"] += 1
                # Quick filter: parse only lines with structure we care about
                if not ('"type"' in line or '"message"' in line):
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = obj.get("timestamp")
                if isinstance(ts, str):
                    if stats["start"] is None or ts < stats["start"]:
                        stats["start"] = ts
                    if stats["end"] is None or ts > stats["end"]:
                        stats["end"] = ts
                cwd = obj.get("cwd")
                if isinstance(cwd, str) and stats["cwd"] is None:
                    stats["cwd"] = cwd
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") == "assistant":
                    stats["turn_count"] += 1
                    model = msg.get("model")
                    if isinstance(model, str):
                        models[model] += 1
                    usage = msg.get("usage")
                    if isinstance(usage, dict):
                        stats["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
                        stats["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
                        stats["cache_read_tokens"] += int(usage.get(
                            "cache_read_input_tokens", 0) or 0)
                        stats["cache_creation_tokens"] += int(usage.get(
                            "cache_creation_input_tokens", 0) or 0)
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") != "tool_use":
                        continue
                    stats["tool_call_count"] += 1
                    name = item.get("name")
                    if name not in EDIT_TOOLS:
                        continue
                    inp = item.get("input") or {}
                    fp = inp.get("file_path")
                    category = classify_file(fp)
                    if isinstance(fp, str):
                        touched.add(fp)

                    def _absorb(s: str) -> None:
                        if not isinstance(s, str):
                            return
                        stats[f"agent_{category}_lines"] += count_lines(s)
                        # normalized lines for later attribution
                        for ln in s.splitlines():
                            ln = ln.strip()
                            if len(ln) >= 8:   # ignore short / trivial lines
                                emitted_lines.add(ln)

                    if name == "Edit":
                        _absorb(inp.get("new_string") or "")
                        stats["n_edit_ops"] += 1
                    elif name == "Write":
                        _absorb(inp.get("content") or "")
                        stats["n_write_ops"] += 1
                    elif name == "MultiEdit":
                        stats["n_multiedit_ops"] += 1
                        for e in inp.get("edits") or []:
                            if isinstance(e, dict):
                                _absorb(e.get("new_string") or "")
    except OSError:
        pass
    stats["n_files_touched"] = len(touched)
    stats["model"] = models.most_common(1)[0][0] if models else None
    stats["agent_total_edit_lines"] = (
        stats["agent_code_lines"] + stats["agent_docs_lines"]
        + stats["agent_other_lines"])
    # Return as a list so it is picklable across the process pool; callers
    # convert to a set for attribution.
    stats["agent_emitted_lines"] = list(emitted_lines)
    return stats


def find_transcripts(projects_dir: Path, since: datetime) -> list:
    """Walk ~/.claude/projects/<*>/*.jsonl, filter by mtime >= since."""
    paths = []
    for pd_ in (projects_dir.iterdir() if projects_dir.exists() else []):
        if not pd_.is_dir():
            continue
        for f in pd_.glob("*.jsonl"):
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if mtime >= since:
                paths.append(f)
    return paths


# ----------------------------------------------------------------------------
# Git-based commit attribution (optional — VERBATIM logic)
# ----------------------------------------------------------------------------

def git_available() -> bool:
    return shutil.which("git") is not None


def _git(cwd: str, *args: str, timeout: int = 30) -> Optional[str]:
    """Run git in cwd, return stdout or None on failure."""
    try:
        r = subprocess.run(["git", "-C", cwd, *args],
                           capture_output=True, text=True,
                           timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return None


def commits_in_window(cwd: str, start_iso: str, end_iso: str) -> list:
    """List commit SHAs in cwd's git history within [start, end]. Returns
    empty list if not a git repo, no commits, or failure."""
    if not Path(cwd).exists():
        return []
    if _git(cwd, "rev-parse", "--git-dir") is None:
        return []
    out = _git(cwd, "log", "--all", "--format=%H",
               f"--since={start_iso}", f"--until={end_iso}")
    if not out:
        return []
    return [sha.strip() for sha in out.splitlines() if sha.strip()]


def commit_diff_added_lines(cwd: str, sha: str):
    """Return (added_line_count, list_of_added_line_texts) for a commit."""
    out = _git(cwd, "show", sha, "--format=", "--unified=0", "--no-color")
    if not out:
        return 0, []
    added = []
    n = 0
    for line in out.splitlines():
        # +++ is the file header; skip
        if line.startswith("+++"):
            continue
        if line.startswith("+"):
            n += 1
            text = line[1:].strip()
            if len(text) >= 8:
                added.append(text)
    return n, added


def enrich_with_git(sessions: list, agent_lines_by_sid: dict) -> None:
    """Add ``committed_lines`` and ``agent_committed_lines`` to each session
    dict (in place) via local git. Sets to None (JSON null) when git ops can't
    run for a session; 0 when the repo has no commits in the window.

    Caches per-(cwd, commit) added-line lists to avoid re-running git show
    when multiple sessions touched the same commit.
    """
    if not git_available():
        for s in sessions:
            s["committed_lines"] = None
            s["agent_committed_lines"] = None
        return

    # Per-cwd cache: {sha: (added_count, added_lines)}
    diff_cache: dict = defaultdict(dict)

    for s in sessions:
        cwd = s.get("cwd")
        start = s.get("start")
        end = s.get("end")
        sid = s["session_id"]
        if not isinstance(cwd, str) or not isinstance(start, str) or not isinstance(end, str):
            s["committed_lines"] = None
            s["agent_committed_lines"] = None
            continue
        shas = commits_in_window(cwd, start, end)
        if not shas:
            s["committed_lines"] = 0
            s["agent_committed_lines"] = 0
            continue
        agent_set = agent_lines_by_sid.get(sid, set())
        total_added = 0
        agent_matched = 0
        cache = diff_cache[cwd]
        for sha in shas:
            if sha not in cache:
                cache[sha] = commit_diff_added_lines(cwd, sha)
            n, lines = cache[sha]
            total_added += n
            if agent_set:
                for ln in lines:
                    if ln in agent_set:
                        agent_matched += 1
        s["committed_lines"] = total_added
        s["agent_committed_lines"] = agent_matched


# ----------------------------------------------------------------------------
# Per-session derived fields + weekly bucketing
# ----------------------------------------------------------------------------

def derive_session_fields(sessions: list) -> list:
    """Attach dt / duration_seconds / week_start to each session. Drops
    sessions whose start timestamp can't be parsed. Returns the kept list."""
    kept = []
    for s in sessions:
        dt = _parse_ts(s.get("start"))
        if dt is None:
            continue
        end = _parse_ts(s.get("end"))
        s["_dt"] = dt
        s["duration_seconds"] = (end - dt).total_seconds() if end else 0.0
        s["_week_start"] = _week_start_wtue(dt)
        kept.append(s)
    return kept


def group_by_week(sessions: list) -> list:
    """Group sessions into W-TUE weekly buckets, sorted ascending.

    Returns a list of (week_start: datetime, [session dicts]) tuples.
    """
    buckets: dict = defaultdict(list)
    for s in sessions:
        buckets[s["_week_start"]].append(s)
    return sorted(buckets.items(), key=lambda kv: kv[0])


# ----------------------------------------------------------------------------
# Build roi_data.json sections
# ----------------------------------------------------------------------------

def build_trend(weeks: list) -> list:
    """Per-week median rows for each TREND_LINES metric."""
    metric_cols = [c for c, *_ in TREND_LINES]
    rows = []
    for week_start, group in weeks:
        n = len(group)
        row = {
            "week": str(week_start.date()),
            "n": n,
            "confident": n >= LOW_N_THRESHOLD,
        }
        for col in metric_cols:
            row[col] = _median([m.get(col) for m in group])
        rows.append(row)
    return rows


def build_buckets(weeks: list) -> list:
    """Per-panel weekly distribution shares (% of sessions per outcome bucket)."""
    panels = []
    for col, bk_fn, palette, title in PANEL_SPECS:
        labels = [lab for lab, _ in palette]
        week_rows = []
        for week_start, group in weeks:
            n = len(group)
            counts = {lab: 0 for lab in labels}
            # Every session counts; a null (git-disabled / git-failed) value
            # buckets as "0" via bk_fn(None), and the denominator is the full
            # week — matching my_token_roi.py's render_bucket_chart, which uses
            # len(m) and coerces NaN -> "0".
            for m in group:
                counts[bk_fn(m.get(col))] += 1
            shares = {
                lab: (counts[lab] / n * 100.0 if n else 0.0)
                for lab in labels
            }
            week_rows.append({
                "week": str(week_start.date()),
                "n": n,
                "shares": shares,
            })
        panels.append({
            "col": col,
            "title": title,
            "buckets": [{"label": lab, "color": color} for lab, color in palette],
            "weeks": week_rows,
        })
    return panels


def build_cost_ratios(weeks: list) -> list:
    """Per-week numerator/denominator SUMS for each cost ratio.

    The renderer computes sum(num)/sum(den) per week and skips weeks where
    the denominator is zero. Git-null values count as 0 in the sum (matching
    the source's ``.fillna(0).sum()`` behavior).
    """
    ratios = []
    for num, den, label, color in COST_RATIOS:
        week_rows = []
        for week_start, group in weeks:
            num_sum = sum(float(m.get(num) or 0) for m in group)
            den_sum = sum(float(m.get(den) or 0) for m in group)
            week_rows.append({
                "week": str(week_start.date()),
                "n": len(group),
                "num_sum": num_sum,
                "den_sum": den_sum,
            })
        ratios.append({
            "num": num,
            "den": den,
            "label": label,
            "color": color,
            "weeks": week_rows,
        })
    return ratios


def build_summary(sessions: list) -> dict:
    dts = [s["_dt"] for s in sessions]
    earliest = min(dts).date().isoformat() if dts else None
    latest = max(dts).date().isoformat() if dts else None
    models = Counter(s.get("model") for s in sessions if s.get("model"))
    return {
        "date_range": {"earliest": earliest, "latest": latest},
        "models": dict(models.most_common(5)),
        "median_output_tokens": _median([s.get("output_tokens") for s in sessions]),
        "median_turns": _median([s.get("turn_count") for s in sessions]),
    }


def build_roi_data(sessions: list, *, projects_dir: Path, days: int,
                   git_enabled: bool) -> dict:
    weeks = group_by_week(sessions)
    summary = build_summary(sessions)
    return {
        "meta": {
            "projects_dir": str(projects_dir),
            "days": days,
            "window_start": summary["date_range"]["earliest"],
            "window_end": summary["date_range"]["latest"],
            "n_sessions": len(sessions),
            "git_enabled": git_enabled,
            "low_n_threshold": LOW_N_THRESHOLD,
        },
        "summary": summary,
        "trend": build_trend(weeks),
        "buckets": build_buckets(weeks),
        "cost_ratios": build_cost_ratios(weeks),
        "reference": REFERENCE,
    }


# ----------------------------------------------------------------------------
# CSV output
# ----------------------------------------------------------------------------

def write_anonymized_csv(sessions: list, out_path: Path) -> None:
    weeks = group_by_week(sessions)
    rows = []
    for week_start, group in weeks:
        models = [m.get("model") for m in group if m.get("model")]
        top_model, top_share = "", 0.0
        if models:
            counts = Counter(models)
            top_model, top_count = counts.most_common(1)[0]
            top_share = top_count / len(models)
        committed = [m.get("committed_lines") for m in group]
        agent_committed = [m.get("agent_committed_lines") for m in group]
        rows.append({
            "week": str(week_start.date()),
            "n_sessions": len(group),
            "n_with_model": len(models),
            "models_top": top_model,
            "models_top_share": round(top_share, 3),
            "output_tokens_median": _median([m.get("output_tokens") for m in group]),
            "output_tokens_mean": _mean([m.get("output_tokens") for m in group]),
            "input_tokens_median": _median([m.get("input_tokens") for m in group]),
            "cache_read_tokens_median": _median([m.get("cache_read_tokens") for m in group]),
            "turns_median": _median([m.get("turn_count") for m in group]),
            "duration_seconds_median": _median([m.get("duration_seconds") for m in group]),
            "tool_calls_median": _median([m.get("tool_call_count") for m in group]),
            "files_touched_median": _median([m.get("n_files_touched") for m in group]),
            "agent_total_edit_lines_median": _median([m.get("agent_total_edit_lines") for m in group]),
            "agent_code_lines_median": _median([m.get("agent_code_lines") for m in group]),
            "agent_docs_lines_median": _median([m.get("agent_docs_lines") for m in group]),
            "agent_other_lines_median": _median([m.get("agent_other_lines") for m in group]),
            "committed_lines_median": _median(committed),
            "agent_committed_lines_median": _median(agent_committed),
            "pr_count_mean": _mean([m.get("pr_count") for m in group]),
        })
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ANONYMIZED_CSV_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"[csv]  wrote {out_path}  ({len(rows)} weeks)")


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--projects-dir",
                   default=str(Path.home() / ".claude" / "projects"),
                   help="Path to Claude Code projects directory. "
                        "Default: ~/.claude/projects/")
    p.add_argument("--out", required=True,
                   help="Output directory (the run dir) for roi_data.json + CSV.")
    p.add_argument("--days", type=int, default=90,
                   help="Lookback window in days. Default: 90 (3 months).")
    p.add_argument("--workers", type=int, default=None,
                   help="Worker processes for transcript parsing. "
                        "Default: CPU count.")
    p.add_argument("--no-git", action="store_true",
                   help="Skip git-based committed-line enrichment.")
    p.add_argument("--no-csv", action="store_true",
                   help="Skip CSV output.")
    return p.parse_args()


def parse_one_wrap(p: Path) -> dict:
    return parse_transcript(p)


def main() -> int:
    args = parse_args()
    projects_dir = Path(args.projects_dir).expanduser().resolve()
    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not projects_dir.exists():
        print(f"ERROR: projects dir not found: {projects_dir}", file=sys.stderr)
        print("Pass --projects-dir explicitly if your Claude Code data is "
              "elsewhere.", file=sys.stderr)
        return 2

    since = datetime.now(tz=timezone.utc) - timedelta(days=args.days)
    print(f"Scanning {projects_dir} for transcripts modified since "
          f"{since.date()}...")
    paths = find_transcripts(projects_dir, since)
    if not paths:
        print(f"No transcripts found in the last {args.days} days. "
              "Nothing to do.")
        return 0
    print(f"Found {len(paths)} transcripts. Parsing...")

    n_workers = args.workers if args.workers else (os.cpu_count() or 2)
    stats: list = []
    if n_workers <= 1:
        for n, p in enumerate(paths, 1):
            stats.append(parse_one_wrap(p))
            if n % 50 == 0:
                print(f"  parsed {n}/{len(paths)}")
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            futures = {ex.submit(parse_one_wrap, p): p for p in paths}
            for n, fut in enumerate(as_completed(futures), 1):
                stats.append(fut.result())
                if n % 50 == 0:
                    print(f"  parsed {n}/{len(paths)}")

    # Pop agent_emitted_lines out (large; only needed for git attribution).
    agent_lines_by_sid = {s["session_id"]: set(s.pop("agent_emitted_lines"))
                          for s in stats}

    sessions = derive_session_fields(stats)
    # Restrict to last N days (re-filter by start timestamp).
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=args.days)
    sessions = [s for s in sessions if s["_dt"] >= cutoff]
    print(f"After filtering to last {args.days} days: {len(sessions)} sessions")

    if not sessions:
        print("No sessions in the lookback window.")
        return 0

    # Optional git enrichment.
    git_enabled = False
    if args.no_git or not git_available():
        if not git_available():
            print("[git]  git not found in PATH; skipping committed-line "
                  "enrichment.")
        for s in sessions:
            s["committed_lines"] = None
            s["agent_committed_lines"] = None
    else:
        print("[git]  enriching with local git history (may take a few "
              "minutes; pass --no-git to skip)...")
        enrich_with_git(sessions, agent_lines_by_sid)
        git_enabled = True

    sm = build_summary(sessions)
    print(f"\nSummary across {len(sessions)} sessions:")
    print(f"  date range: {sm['date_range']['earliest']} → "
          f"{sm['date_range']['latest']}")
    print(f"  models seen: {sm['models']}")
    print(f"  median output_tokens / session: {sm['median_output_tokens']}")
    print(f"  median turns / session: {sm['median_turns']}")

    roi_data = build_roi_data(sessions, projects_dir=projects_dir,
                              days=args.days, git_enabled=git_enabled)
    data_path = out_dir / "roi_data.json"
    data_path.write_text(json.dumps(roi_data, indent=2), encoding="utf-8")
    print(f"[data] wrote {data_path}")

    if not args.no_csv:
        write_anonymized_csv(sessions, out_dir / "roi.csv")

    print("\nDone.")
    print(f"Outputs in: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
