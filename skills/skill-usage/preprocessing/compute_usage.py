#!/usr/bin/env python3
"""compute_usage.py — analyze which Claude Code skills you read/invoke most.

Stdlib-only data layer for the /skill-usage skill. Scans your local
~/.claude/projects/ for session transcripts and counts two usage signals,
then cross-references an on-disk inventory of the skills that actually exist
so it can flag the ones that are never used. Emits:

  - usage_data.json : everything the SVG charts + hero card need
                      (meta, summary, ranking, trend, split, inventory,
                      candidates)
  - usage.csv       : per-skill aggregate counts (optional)

The two usage signals (both pulled from transcript structure only):
  - Signal A — Skill-tool invocations: an assistant `tool_use` block with
    name == "Skill" and input.skill = the skill name (e.g. via /skill or an
    explicit Skill() call). Plugin-namespaced names (bigspin:persona) are
    folded onto their base name (persona) but the alias is retained.
  - Signal B — SKILL.md reads: a `tool_use` block with name == "Read" whose
    input.file_path contains "SKILL.md". The skill name is the parent
    directory of the SKILL.md. These frequently occur inside subagent
    (sidechain) entries — that is real usage and is counted, but the
    sidechain share is surfaced separately.

The on-disk inventory walks the skill roots (.agents/skills, dspy-service
skills, public-repos skills) and reads each SKILL.md frontmatter for name +
description, plus a last-modified date (git, with mtime fallback). The same
skill can appear under many paths (git worktrees, published mirrors); the
inventory is de-duplicated by skill name, not path.

Privacy:
  - No prompts, file contents, or tool-call payloads leave your machine.
  - Everything runs locally. No upload, no telemetry, no network, no LLM.
  - The CSV / JSON contain skill names and aggregate counts only.

Usage:
  python3 compute_usage.py --out RUN_DIR                  # defaults: ~/.claude/projects/, all history
  python3 compute_usage.py --out RUN_DIR --projects-dir DIR
  python3 compute_usage.py --out RUN_DIR --days 90        # narrow the lookback window
  python3 compute_usage.py --out RUN_DIR --repo-root PATH # where to scan for the skill inventory
  python3 compute_usage.py --out RUN_DIR --extra-root P   # add another inventory root (repeatable)
  python3 compute_usage.py --out RUN_DIR --stale-days 90  # "stale" cutoff for update flags
  python3 compute_usage.py --out RUN_DIR --no-git         # use file mtime instead of git for dates
  python3 compute_usage.py --out RUN_DIR --no-csv         # skip CSV output

Dependencies: Python 3.9+ (standard library only — no pandas/numpy/PyYAML).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

LOW_N_THRESHOLD = 3     # weeks with fewer events get "low confidence" treatment
READ_HOT = 8            # >= this many reads = a "hot" skill (refine candidate)
DEFAULT_DAYS = 3650     # ~10 years ≈ "all history" by default
DEFAULT_STALE_DAYS = 60

# Skill-inventory glob roots, relative to a repo root. Each entry is a glob
# whose matched SKILL.md files belong to skills named by their parent dir.
INVENTORY_GLOBS = [
    ".agents/skills/*/SKILL.md",
    "dspy-service/.agents/skills/*/SKILL.md",
    "public-repos/*/skills/*/SKILL.md",
    "public-repos/*/skills/*/*/SKILL.md",
    "public-repos/*/.agents/skills/*/SKILL.md",
    "public-repos/*/*/skills/*/SKILL.md",
]

# Anonymized CSV columns.
CSV_COLUMNS = [
    "base_name", "invokes", "reads", "total",
    "in_inventory", "last_modified", "raw_names",
]

# Match a leading plugin namespace ("bigspin:persona" -> "persona").
_NS_RE = re.compile(r"^[A-Za-z0-9_-]+:(.+)$")


# ----------------------------------------------------------------------------
# Small stdlib helpers (replace pandas/numpy) — mirrors compute_roi.py
# ----------------------------------------------------------------------------

def _median(values: list) -> Optional[float]:
    nums = sorted(float(v) for v in values if v is not None)
    n = len(nums)
    if n == 0:
        return None
    mid = n // 2
    if n % 2 == 1:
        return nums[mid]
    return (nums[mid - 1] + nums[mid]) / 2.0


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

    Matches compute_roi.py so the x-axis aligns with the token-roi report:
    pandas ``to_period("W-TUE")`` weeks END on Tuesday, START on Wednesday.
    """
    naive = dt.replace(tzinfo=None)
    day = datetime(naive.year, naive.month, naive.day)
    offset = (day.weekday() - 2) % 7
    return day - timedelta(days=offset)


def normalize(raw: Optional[str]) -> Optional[str]:
    """Strip a leading plugin namespace: 'bigspin:persona' -> 'persona'."""
    if not isinstance(raw, str) or not raw:
        return raw
    m = _NS_RE.match(raw)
    return m.group(1) if m else raw


# ----------------------------------------------------------------------------
# Transcript parsing — the two usage signals
# ----------------------------------------------------------------------------

def parse_transcript_events(transcript_path: Path) -> list:
    """Extract skill-usage events from one Claude Code transcript JSONL.

    Returns a list of event dicts:
      {kind: "invoke"|"read", raw_name, base_name, ts (iso str|None),
       sidechain: bool, session_id}

    Subagent transcripts live in a ``<session>/subagents/`` subdirectory; their
    reads are sidechain usage even if the per-entry ``isSidechain`` flag is
    absent, so the path is treated as an additional sidechain signal.
    """
    sid = transcript_path.stem
    in_subagent_file = "/subagents/" in str(transcript_path).replace("\\", "/")
    events: list = []
    try:
        with transcript_path.open(errors="ignore") as f:
            for line in f:
                # Cheap pre-filter: only lines that could carry a tool_use.
                if '"tool_use"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = obj.get("timestamp")
                ts = ts if isinstance(ts, str) else None
                sidechain = bool(obj.get("isSidechain")) or in_subagent_file
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") != "tool_use":
                        continue
                    name = item.get("name")
                    inp = item.get("input") or {}
                    if not isinstance(inp, dict):
                        continue
                    if name == "Skill":
                        raw = inp.get("skill")
                        if isinstance(raw, str) and raw:
                            events.append({
                                "kind": "invoke",
                                "raw_name": raw,
                                "base_name": normalize(raw),
                                "ts": ts,
                                "sidechain": sidechain,
                                "session_id": sid,
                            })
                    elif name == "Read":
                        fp = inp.get("file_path")
                        if isinstance(fp, str) and "SKILL.md" in fp:
                            raw = Path(fp).parent.name
                            if raw:
                                events.append({
                                    "kind": "read",
                                    "raw_name": raw,
                                    "base_name": normalize(raw),
                                    "ts": ts,
                                    "sidechain": sidechain,
                                    "session_id": sid,
                                })
    except OSError:
        pass
    return events


def find_transcripts(projects_dir: Path, since: datetime) -> list:
    """Walk ~/.claude/projects/ recursively for *.jsonl, mtime >= since.

    Unlike the token-roi scan (top-level session transcripts only), this
    recurses: most SKILL.md reads happen inside subagent transcripts stored
    under ``<projectdir>/<session>/subagents/agent-*.jsonl``. Missing those
    would undercount the dominant usage signal.
    """
    paths = []
    if not projects_dir.exists():
        return paths
    for f in projects_dir.rglob("*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime >= since:
            paths.append(f)
    return paths


def dedupe_events(events: list) -> list:
    """Drop exact-duplicate events (same session, kind, skill, timestamp).

    Re-emitted transcript lines can repeat a tool_use; this collapses those.
    Legitimate distinct reads (different timestamps, or a sidechain read vs a
    main-thread read at another moment) are preserved.
    """
    seen = set()
    out = []
    for e in events:
        key = (e["session_id"], e["kind"], e["base_name"], e["ts"])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


# ----------------------------------------------------------------------------
# On-disk skill inventory
# ----------------------------------------------------------------------------

def git_available() -> bool:
    return shutil.which("git") is not None


def _git(cwd: str, *args: str, timeout: int = 20) -> Optional[str]:
    """Run git in cwd, return stdout or None on failure."""
    try:
        r = subprocess.run(["git", "-C", cwd, *args],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            return None
        return r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return None


def parse_frontmatter(path: Path) -> tuple:
    """Return (name, description) from a SKILL.md YAML frontmatter block.

    Stdlib-only — no PyYAML. Handles inline `description: ...` and the folded
    `description: >` / literal `description: |` multi-line forms used across
    the .agents/skills/* corpus.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None, None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, None
    fm = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        fm.append(line)

    name, desc = None, None
    i = 0
    while i < len(fm):
        stripped = fm[i].strip()
        if stripped.startswith("name:"):
            name = stripped[len("name:"):].strip().strip("\"'") or None
            i += 1
        elif stripped.startswith("description:"):
            val = stripped[len("description:"):].strip()
            if val in (">", "|", ">-", "|-", ">+", "|+"):
                block = []
                j = i + 1
                while j < len(fm):
                    cont = fm[j]
                    if cont.strip() == "":
                        j += 1
                        continue
                    indent = len(cont) - len(cont.lstrip())
                    if indent == 0:
                        break
                    block.append(cont.strip())
                    j += 1
                desc = " ".join(block) or None
                i = j
            else:
                desc = val.strip().strip("\"'") or None
                i += 1
        else:
            i += 1
    return name, desc


def _last_modified(skill_md: Path, use_git: bool) -> tuple:
    """Return (iso_date_str|None, source) for a SKILL.md.

    Prefers `git log -1 --format=%cI` (ISO, committer date) for the file;
    falls back to filesystem mtime. ``source`` is "git" or "mtime".
    """
    if use_git:
        out = _git(str(skill_md.parent), "log", "-1", "--format=%cI",
                   "--", skill_md.name)
        if out and out.strip():
            dt = _parse_ts(out.strip())
            if dt is not None:
                return dt.date().isoformat(), "git"
    try:
        mtime = datetime.fromtimestamp(skill_md.stat().st_mtime, tz=timezone.utc)
        return mtime.date().isoformat(), "mtime"
    except OSError:
        return None, "mtime"


def _is_worktree_path(p: Path) -> bool:
    return ".claude/worktrees/" in str(p).replace("\\", "/")


def build_inventory(roots: list, use_git: bool) -> list:
    """Discover all skills on disk under the given roots, de-duped by name.

    Returns a list of dicts:
      {inventory_name, base_name, path, description, last_modified,
       last_modified_source, n_locations, roots_seen, used(False placeholder)}
    """
    git_ok = use_git and git_available()
    # name -> list of candidate entries (one per discovered path)
    found: dict = defaultdict(list)
    for root in roots:
        root = Path(root)
        if not root.exists():
            continue
        for glob in INVENTORY_GLOBS:
            for md in root.glob(glob):
                if not md.is_file():
                    continue
                dir_name = md.parent.name
                base = normalize(dir_name)
                if not base:
                    continue
                fm_name, desc = parse_frontmatter(md)
                lm, lm_src = _last_modified(md, git_ok)
                found[base].append({
                    "inventory_name": fm_name or dir_name,
                    "base_name": base,
                    "path": str(md),
                    "description": desc or "",
                    "last_modified": lm,
                    "last_modified_source": lm_src,
                })

    inventory = []
    for base, candidates in found.items():
        # Prefer a non-worktree path, then the most recently modified copy,
        # as the canonical entry to display.
        def _rank(c):
            wt = _is_worktree_path(Path(c["path"]))
            lm = c["last_modified"] or ""
            return (wt, _neg_date_key(lm))
        canonical = sorted(candidates, key=_rank)[0]
        roots_seen = sorted({_root_label(c["path"]) for c in candidates})
        inventory.append({
            **canonical,
            "n_locations": len(candidates),
            "roots_seen": roots_seen,
            "used": False,
        })
    inventory.sort(key=lambda c: c["base_name"])
    return inventory


def _neg_date_key(iso_date: str) -> str:
    """Sort key that puts the most recent ISO date first (ascending sort)."""
    # Invert each digit so a plain ascending sort yields newest-first.
    if not iso_date:
        return "~"  # empty dates sort last
    return "".join(chr(ord("9") - (ord(ch) - ord("0"))) if ch.isdigit() else ch
                   for ch in iso_date)


def _root_label(path: str) -> str:
    p = path.replace("\\", "/")
    if "/dspy-service/" in p:
        return "dspy-service"
    if "/public-repos/" in p:
        return "public-repos"
    if "/.claude/worktrees/" in p:
        return "worktree"
    if "/.agents/skills/" in p:
        return ".agents/skills"
    return "other"


# ----------------------------------------------------------------------------
# Build usage_data.json sections
# ----------------------------------------------------------------------------

def build_ranking(events: list, inventory: list) -> list:
    """Per-skill invoke/read counts merged with the inventory.

    Includes every skill seen in events PLUS every inventory skill that was
    never used (total == 0), so the tail of the ranking is the never-used set.
    """
    inv_by_base = {c["base_name"]: c for c in inventory}
    invokes: Counter = Counter()
    reads: Counter = Counter()
    aliases: dict = defaultdict(set)
    for e in events:
        base = e["base_name"]
        aliases[base].add(e["raw_name"])
        if e["kind"] == "invoke":
            invokes[base] += 1
        else:
            reads[base] += 1

    bases = set(invokes) | set(reads) | set(inv_by_base)
    rows = []
    for base in bases:
        inv = inv_by_base.get(base)
        rows.append({
            "base_name": base,
            "raw_names": sorted(aliases.get(base, {base})),
            "invokes": int(invokes.get(base, 0)),
            "reads": int(reads.get(base, 0)),
            "total": int(invokes.get(base, 0) + reads.get(base, 0)),
            "in_inventory": inv is not None,
            "last_modified": inv["last_modified"] if inv else None,
        })
    rows.sort(key=lambda r: (-r["total"], r["base_name"]))
    return rows


def build_trend(events: list) -> list:
    """Per-week event counts (total / invokes / reads), W-TUE buckets."""
    buckets: dict = defaultdict(lambda: {"n": 0, "invokes": 0, "reads": 0})
    for e in events:
        dt = _parse_ts(e["ts"])
        if dt is None:
            continue
        wk = _week_start_wtue(dt)
        b = buckets[wk]
        b["n"] += 1
        if e["kind"] == "invoke":
            b["invokes"] += 1
        else:
            b["reads"] += 1
    rows = []
    for wk in sorted(buckets):
        b = buckets[wk]
        rows.append({
            "week": str(wk.date()),
            "n": b["n"],
            "confident": b["n"] >= LOW_N_THRESHOLD,
            "invokes": b["invokes"],
            "reads": b["reads"],
            "total": b["n"],
        })
    return rows


def build_split(events: list, ranking: list, top_n: int = 8) -> dict:
    total_invokes = sum(1 for e in events if e["kind"] == "invoke")
    total_reads = sum(1 for e in events if e["kind"] == "read")
    sidechain_reads = sum(1 for e in events
                          if e["kind"] == "read" and e["sidechain"])
    per_skill = [
        {"base_name": r["base_name"], "invokes": r["invokes"], "reads": r["reads"]}
        for r in ranking if r["total"] > 0
    ][:top_n]
    return {
        "total_invokes": total_invokes,
        "total_reads": total_reads,
        "sidechain_reads": sidechain_reads,
        "per_skill": per_skill,
    }


def build_candidates(ranking: list, inventory: list, *, stale_days: int,
                     today: datetime) -> list:
    """Deterministic 'update candidate' flags. A skill may earn several."""
    inv_by_base = {c["base_name"]: c for c in inventory}
    by_base = {r["base_name"]: r for r in ranking}
    out = []

    def is_stale(base: str) -> Optional[bool]:
        inv = inv_by_base.get(base)
        if not inv or not inv.get("last_modified"):
            return None
        dt = _parse_ts(inv["last_modified"])
        if dt is None:
            return None
        return (today - dt).days > stale_days

    for base, r in sorted(by_base.items()):
        in_inv = r["in_inventory"]
        total = r["total"]
        reads = r["reads"]
        invokes = r["invokes"]
        stale = is_stale(base)

        if in_inv and total == 0:
            if stale:
                out.append({"base_name": base, "flag": "retire-or-refresh",
                            "reason": "In the skill inventory but never read or "
                                      "invoked, and the file is stale."})
            else:
                out.append({"base_name": base, "flag": "never-triggered",
                            "reason": "In the inventory but never read or "
                                      "invoked yet (recently authored — give "
                                      "it time, or sharpen its triggers)."})
        if reads >= READ_HOT and stale:
            out.append({"base_name": base, "flag": "refine",
                        "reason": f"Read {reads} times but the file is stale — "
                                  "high-traffic, likely drifted from how it's "
                                  "used."})
        if invokes > 0 and reads == 0:
            out.append({"base_name": base, "flag": "invocation-only",
                        "reason": f"Invoked {invokes} time(s) via the Skill "
                                  "tool but its SKILL.md is never opened — "
                                  "agents act on the description alone."})
        if reads > 0 and invokes == 0:
            out.append({"base_name": base, "flag": "read-only",
                        "reason": f"Opened as reference {reads} time(s) but "
                                  "never invoked as a skill."})
    return out


def build_summary(events: list, ranking: list, inventory: list) -> dict:
    dts = [d for d in (_parse_ts(e["ts"]) for e in events) if d is not None]
    earliest = min(dts).date().isoformat() if dts else None
    latest = max(dts).date().isoformat() if dts else None
    used = [r for r in ranking if r["total"] > 0]
    top = used[0] if used else None
    most_read = max(used, key=lambda r: r["reads"], default=None)
    most_invoked = max(used, key=lambda r: r["invokes"], default=None)
    zero_usage = sum(1 for r in ranking if r["in_inventory"] and r["total"] == 0)
    return {
        "date_range": {"earliest": earliest, "latest": latest},
        "top_skill": top["base_name"] if top else None,
        "top_skill_total": top["total"] if top else 0,
        "most_read": most_read["base_name"] if most_read and most_read["reads"] else None,
        "most_invoked": most_invoked["base_name"] if most_invoked and most_invoked["invokes"] else None,
        "zero_usage_count": zero_usage,
    }


def build_usage_data(events: list, inventory: list, *, projects_dir: Path,
                     days: int, stale_days: int, git_enabled: bool,
                     repo_roots: list, today: datetime) -> dict:
    ranking = build_ranking(events, inventory)
    # Mark inventory entries that were used.
    used_bases = {r["base_name"] for r in ranking if r["total"] > 0}
    for c in inventory:
        c["used"] = c["base_name"] in used_bases

    summary = build_summary(events, ranking, inventory)
    n_invokes = sum(1 for e in events if e["kind"] == "invoke")
    n_reads = sum(1 for e in events if e["kind"] == "read")
    return {
        "meta": {
            "projects_dir": str(projects_dir),
            "days": days,
            "window_start": summary["date_range"]["earliest"],
            "window_end": summary["date_range"]["latest"],
            "n_events": len(events),
            "n_invokes": n_invokes,
            "n_reads": n_reads,
            "n_skills_used": len(used_bases),
            "n_skills_inventory": len(inventory),
            "low_n_threshold": LOW_N_THRESHOLD,
            "read_hot": READ_HOT,
            "stale_days": stale_days,
            "git_enabled": git_enabled,
            "repo_roots": [str(r) for r in repo_roots],
        },
        "summary": summary,
        "ranking": ranking,
        "trend": build_trend(events),
        "split": build_split(events, ranking),
        "inventory": inventory,
        "candidates": build_candidates(ranking, inventory,
                                       stale_days=stale_days, today=today),
    }


# ----------------------------------------------------------------------------
# CSV output
# ----------------------------------------------------------------------------

def write_csv(ranking: list, out_path: Path) -> None:
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in ranking:
            writer.writerow({
                "base_name": r["base_name"],
                "invokes": r["invokes"],
                "reads": r["reads"],
                "total": r["total"],
                "in_inventory": r["in_inventory"],
                "last_modified": r["last_modified"] or "",
                "raw_names": ";".join(r["raw_names"]),
            })
    print(f"[csv]  wrote {out_path}  ({len(ranking)} skills)")


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
                   help="Output directory (the run dir) for usage_data.json + CSV.")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help=f"Lookback window in days. Default: {DEFAULT_DAYS} "
                        "(≈ all history).")
    p.add_argument("--repo-root", default=str(Path.cwd()),
                   help="Repo root to scan for the on-disk skill inventory. "
                        "Default: current working directory.")
    p.add_argument("--extra-root", action="append", default=[],
                   help="Additional inventory root (repeatable).")
    p.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS,
                   help=f"Age (days) past which a SKILL.md counts as stale for "
                        f"update flags. Default: {DEFAULT_STALE_DAYS}.")
    p.add_argument("--no-git", action="store_true",
                   help="Use file mtime instead of git for last-modified dates.")
    p.add_argument("--no-csv", action="store_true",
                   help="Skip CSV output.")
    return p.parse_args()


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

    now = datetime.now(tz=timezone.utc)
    since = now - timedelta(days=args.days)
    print(f"Scanning {projects_dir} for transcripts modified since "
          f"{since.date()}...")
    paths = find_transcripts(projects_dir, since)
    if not paths:
        print(f"No transcripts found in the last {args.days} days. "
              "Nothing to do.")
        return 0
    print(f"Found {len(paths)} transcripts. Extracting skill-usage events...")

    events: list = []
    for n, p in enumerate(paths, 1):
        events.extend(parse_transcript_events(p))
        if n % 100 == 0:
            print(f"  scanned {n}/{len(paths)}")

    events = dedupe_events(events)
    # Re-filter by event timestamp to the lookback window (some transcripts are
    # touched recently but contain only older events, and vice versa).
    cutoff = now - timedelta(days=args.days)
    kept = []
    for e in events:
        dt = _parse_ts(e["ts"])
        # Keep events with no parseable timestamp (rare) so counts aren't lost.
        if dt is None or dt >= cutoff:
            kept.append(e)
    events = kept

    if not events:
        print("No skill-usage events in the lookback window.")
        return 0

    n_invokes = sum(1 for e in events if e["kind"] == "invoke")
    n_reads = len(events) - n_invokes
    print(f"Extracted {len(events)} skill-usage events "
          f"({n_invokes} invocations, {n_reads} SKILL.md reads).")

    roots = [args.repo_root, *args.extra_root]
    print(f"Building skill inventory from: {', '.join(roots)}")
    inventory = build_inventory(roots, use_git=not args.no_git)
    print(f"Inventory: {len(inventory)} unique skills on disk.")

    usage_data = build_usage_data(
        events, inventory, projects_dir=projects_dir, days=args.days,
        stale_days=args.stale_days, git_enabled=not args.no_git,
        repo_roots=roots, today=now)

    ranking = usage_data["ranking"]
    summary = usage_data["summary"]
    used = [r for r in ranking if r["total"] > 0]
    print(f"\nSummary:")
    print(f"  window: {summary['date_range']['earliest']} → "
          f"{summary['date_range']['latest']}")
    print(f"  skills used: {len(used)} / {len(inventory)} in inventory")
    print(f"  most used: {summary['top_skill']} "
          f"({summary['top_skill_total']} events)")
    print(f"  never used (in inventory): {summary['zero_usage_count']}")
    print(f"  reads vs invocations: {n_reads} reads / {n_invokes} invocations")
    print(f"  top 5: " + ", ".join(
        f"{r['base_name']}={r['total']}" for r in used[:5]))

    data_path = out_dir / "usage_data.json"
    data_path.write_text(json.dumps(usage_data, indent=2), encoding="utf-8")
    print(f"[data] wrote {data_path}")

    if not args.no_csv:
        write_csv(ranking, out_dir / "usage.csv")

    print("\nDone.")
    print(f"Outputs in: {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
