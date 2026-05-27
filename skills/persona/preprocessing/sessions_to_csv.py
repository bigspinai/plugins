#!/usr/bin/env python3
"""Export Claude Code session JSONLs to a single analytics-ready CSV.

One row per session (top-level sessions AND subagents). Each row has:

    - A set of structured metadata columns for segmentation / filtering
      (timestamps, model, project, git branch, PR correlation, counts, ...).
    - A `messages` column holding a JSON array of the session's back-and-forth
      as a flat list of {role, ts, ...} entries. Roles: "user" | "ai" | "tool".

Design goals:

1. **Preserve the human-readable conversation faithfully.** User prompts and
   assistant text are emitted verbatim — no truncation — because that's the
   signal a downstream qualitative-pattern tool needs most.

2. **Shrink the tool-call bloat aggressively.** Tool `args` and `result`
   payloads are truncated to short previews; the full character counts are
   kept so downstream tools can still reason about "this was a giant read"
   vs "this was a two-character edit". Edit/Write `new_string` bodies are
   replaced with a length marker — those dominate file size and carry the
   least qualitative signal.

3. **Depend only on the Python stdlib.** No pandas, no pyarrow. The CSV
   module handles everything.

Usage
-----
    python sessions_to_csv.py [SESSIONS_ROOT] [--out OUT.csv] [--format csv|jsonl]

SESSIONS_ROOT defaults to ~/.claude/projects (or $CLAUDE_SESSIONS_ROOT).

Use `--format jsonl` for one session per line, which is often friendlier for
analytics ingestion than a CSV with embedded JSON.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger("sessions_to_csv")


# =====================================================================
# Truncation budgets. Tune these if you need smaller / larger output.
# =====================================================================
#
# Tool entries are deliberately minimal: the downstream pattern-analysis
# tool needs to know *which* tool fired (and roughly how big its output
# was / whether it errored), but does not need the payload bodies, which
# drown out the actual human <-> agent conversation.
#
# We therefore:
#   - drop tool result payload previews entirely (keep only a
#     `result: "<chars>c"` / `"error"` / `"error:<chars>c"` marker)
#   - keep a short args preview so downstream can tell which *kind* of
#     call was happening (e.g. which file was Read, which cmd was Bashed)

# User prompts + AI text are preserved in full. These limits apply to tools.
TOOL_ARGS_PREVIEW_CHARS = 120     # JSON/bash-cmd preview per tool call
BASH_CMD_PREVIEW_CHARS = 200      # Bash commands — kept a bit longer
EDIT_OLD_PREVIEW_CHARS = 100      # Edit.old_string preview
EDIT_NEW_PREVIEW_CHARS = 100      # Edit.new_string preview

# Tools whose string args are ~always boilerplate content (file bodies,
# multi-KB strings). For these we keep only the file path + sizes.
HEAVY_MUTATION_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}

# CSV field-size limit. A single session can easily blow past the default
# 131072-char limit (messages field). Raise it.
csv.field_size_limit(min(2**31 - 1, sys.maxsize))


# =====================================================================
# Helpers
# =====================================================================

def _parse_ts(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw[:-1]).replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _iso(ts: Optional[datetime]) -> Optional[str]:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).isoformat(timespec="seconds")


def _truncate(s: Optional[str], n: int) -> Optional[str]:
    if s is None:
        return None
    if len(s) <= n:
        return s
    return s[: max(n - 1, 0)] + "…"


_PATH_TOKEN_RE = re.compile(
    r"(?:(?:\./|\.\./|~/|/)[^\s'\"`;|&$<>]+|[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,6})"
)


def _flatten_bash(args: dict) -> str:
    for k in ("command", "cmd", "script", "bash"):
        if isinstance(args.get(k), str):
            return args[k]
    try:
        return json.dumps(args or {}, default=str)
    except Exception:
        return str(args)


def _target_file(tool_name: str, args: dict) -> Optional[str]:
    for k in ("file_path", "path", "notebook_path", "output_path",
              "filePath", "filename", "file"):
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if tool_name == "Bash":
        cmd = _flatten_bash(args)
        try:
            tokens = shlex.split(cmd, posix=True)
        except ValueError:
            tokens = cmd.split()
        for tok in tokens[1:]:
            if tok.startswith("-"):
                continue
            if _PATH_TOKEN_RE.fullmatch(tok):
                return tok
        m = _PATH_TOKEN_RE.search(cmd)
        if m:
            return m.group(0)
    return None


def _args_preview(tool_name: str, args: dict) -> str:
    """Compact, human-readable preview of a tool's arguments.

    Tool-specific shaping: Edit/Write truncate the code bodies to length
    markers; Bash keeps the command; Grep/Glob keep the pattern; everything
    else falls back to a truncated JSON dump.
    """
    args = args or {}

    if tool_name == "Bash":
        cmd = _flatten_bash(args)
        return _truncate(cmd, BASH_CMD_PREVIEW_CHARS) or ""

    if tool_name in HEAVY_MUTATION_TOOLS:
        path = args.get("file_path") or args.get("path") or args.get("notebook_path") or ""
        parts = [f"path={path}"]
        # Edit.old_string / new_string or Write.content — replace body with size.
        for key in ("old_string", "new_string", "content"):
            v = args.get(key)
            if isinstance(v, str):
                preview = _truncate(v, EDIT_OLD_PREVIEW_CHARS if key == "old_string" else EDIT_NEW_PREVIEW_CHARS)
                parts.append(f"{key}[{len(v)}c]={json.dumps(preview)}")
        # MultiEdit has a list of edits.
        edits = args.get("edits")
        if isinstance(edits, list):
            parts.append(f"edits[n={len(edits)}]")
        return _truncate(" ".join(parts), TOOL_ARGS_PREVIEW_CHARS) or ""

    if tool_name in ("Grep", "Glob"):
        bits = []
        for k in ("pattern", "glob", "path", "output_mode", "-i", "type"):
            v = args.get(k)
            if v is not None:
                bits.append(f"{k}={json.dumps(v, default=str)}")
        return _truncate(" ".join(bits), TOOL_ARGS_PREVIEW_CHARS) or ""

    if tool_name in ("Read", "NotebookRead"):
        bits = []
        for k in ("file_path", "path", "notebook_path", "offset", "limit", "pages"):
            v = args.get(k)
            if v is not None:
                bits.append(f"{k}={json.dumps(v, default=str)}")
        return _truncate(" ".join(bits), TOOL_ARGS_PREVIEW_CHARS) or ""

    # Fallback: compact JSON, truncated.
    try:
        enc = json.dumps(args, default=str, separators=(",", ":"))
    except Exception:
        enc = str(args)
    return _truncate(enc, TOOL_ARGS_PREVIEW_CHARS) or ""


def _coerce_result_text(tool_result_content: Any) -> tuple[str, int]:
    """Flatten any tool_result.content shape to (text, char_count)."""
    if tool_result_content is None:
        return "", 0
    if isinstance(tool_result_content, str):
        return tool_result_content, len(tool_result_content)
    if isinstance(tool_result_content, list):
        parts: list[str] = []
        for p in tool_result_content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text", "") or "")
            elif isinstance(p, dict) and p.get("type") == "image":
                parts.append(f"<image:{p.get('source', {}).get('media_type', '?')}>")
            else:
                try:
                    parts.append(json.dumps(p, default=str))
                except Exception:
                    parts.append(str(p))
        joined = "".join(parts)
        return joined, len(joined)
    enc = json.dumps(tool_result_content, default=str)
    return enc, len(enc)


# =====================================================================
# Parse one JSONL into structured messages + metadata.
# =====================================================================

@dataclass
class _ToolMsg:
    ts: Optional[datetime]
    name: str
    tool_use_id: str
    target: Optional[str]
    args_preview: str
    # We deliberately do NOT store the result payload — only its size and
    # error flag. The JSON emits both as a single `result` marker string.
    result_chars: Optional[int] = None
    is_error: bool = False
    duration_ms: Optional[int] = None


@dataclass
class _TextMsg:
    role: str          # "user" | "ai"
    ts: Optional[datetime]
    text: str
    thinking_chars: int = 0   # total chars of thinking parts (ai only)
    source: str = "message"   # "message" | "queue"


@dataclass
class ParsedSession:
    session_id: str
    source_path: str
    project: str
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    model: Optional[str]
    is_subagent: bool
    parent_session_id: Optional[str]
    agent_id: Optional[str]
    git_branch: Optional[str]
    cc_version: Optional[str]
    entrypoint: Optional[str]
    pr_numbers: list[int] = field(default_factory=list)
    pr_urls: list[str] = field(default_factory=list)
    # Messages in timeline order.
    messages: list[Any] = field(default_factory=list)  # _TextMsg | _ToolMsg


def _infer_project(path: Path) -> str:
    for p in path.parents:
        if p.name.startswith("-Users-") or p.name.startswith("-home-"):
            return p.name
    for p in path.parents:
        if p.name and p.name != "subagents" and len(p.name) != 36:
            return p.name
    return "unknown"


# Patterns that mean "this 'user' event is actually pure Claude Code system
# text, not anything the human typed". We drop these so they don't pollute
# downstream qualitative analysis (which assumes user turns reflect user
# intent). Each pattern matches at the *start* of the content; we only filter
# turns that are dominated by the system text — so a real prompt that happens
# to start with `[Blocker]` or `[Nit]` is preserved.
_SYSTEM_INJECTION_PREFIXES = (
    "<task-notification>",          # background-task completion ping
    "<local-command-caveat>",       # CC's "Caveat: …" wrapper around slash-command echoes
    "<command-name>",               # slash-command metadata (e.g. /model, /clear)
    "<command-message>",
    "<command-args>",
    "<local-command-stdout>",       # slash-command stdout
    "<local-command-stderr>",       # slash-command stderr
    "<system-reminder>",            # CC system reminders
    "<user-prompt-submit-hook>",    # user-defined hook output
    "<bash-input>",                 # rare leakage of bash tool framing
    "<bash-stdout>",
    "<bash-stderr>",
)
_SYSTEM_INJECTION_FULL_PATTERNS = (
    re.compile(r"^\s*\[Request interrupted by user[^\]]*\]\s*$", re.IGNORECASE),
    # CC inserts "[Image: original WxH, displayed at WxH. Multiply coordinates …]"
    # as a standalone user message when the user attaches an image with no prose.
    # Pure metadata — nothing the user typed.
    re.compile(r"^\s*\[Image:\s*original\s+\d+x\d+", re.IGNORECASE),
)


def _is_system_injection(text: str) -> bool:
    """True if a user-prompt event is purely Claude Code system text."""
    if not text:
        return True  # also drop empty user events
    head = text.lstrip()[:80].lower()
    for prefix in _SYSTEM_INJECTION_PREFIXES:
        if head.startswith(prefix.lower()):
            return True
    for pat in _SYSTEM_INJECTION_FULL_PATTERNS:
        if pat.match(text):
            return True
    return False


def _is_prompt_user_event(msg: dict) -> bool:
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        kinds = {p.get("type") for p in content if isinstance(p, dict)}
        if kinds and kinds.issubset({"text"}):
            return True
    return False


def _extract_prompt_text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def parse_session_file(path: Path) -> Optional[ParsedSession]:
    events: list[dict] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        log.warning("cannot read %s: %s", path, exc)
        return None

    if not events:
        return None

    is_subagent = path.parent.name == "subagents"
    parent_session_id: Optional[str] = None
    if is_subagent:
        parent_session_id = path.parent.parent.name

    project = _infer_project(path)

    session_id: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    model: Optional[str] = None
    git_branch: Optional[str] = None
    cc_version: Optional[str] = None
    entrypoint: Optional[str] = None
    agent_id: Optional[str] = None
    pr_numbers: list[int] = []
    pr_urls: list[str] = []

    messages: list[Any] = []
    pending_by_id: dict[str, _ToolMsg] = {}

    def _touch(ts: Optional[datetime]) -> None:
        nonlocal started_at, ended_at
        if ts is None:
            return
        if started_at is None or ts < started_at:
            started_at = ts
        if ended_at is None or ts > ended_at:
            ended_at = ts

    def _append_user_prompt(new_text: str, new_ts: Optional[datetime],
                            source: str) -> None:
        """Append a user prompt, with two safeguards:

        1. **Filter system injections.** Claude Code writes some non-prompt
           content into 'user' events: task-notification pings,
           slash-command metadata wrappers, [Request interrupted] markers,
           image-metadata stubs, etc. Those would mislead a qualitative
           analyzer into thinking the user typed them, so we drop them here.

        2. **Dedupe queue-op + user pairs.** Claude Code writes two events
           for every prompt: a `queue-operation` (enqueue) first, then a
           `user` event ~0.3s later carrying the same text. If the previous
           message is already a user prompt with identical text, merge.
        """
        if _is_system_injection(new_text):
            return
        if messages:
            last = messages[-1]
            if (isinstance(last, _TextMsg) and last.role == "user"
                    and (last.text or "") == (new_text or "")):
                # Prefer the later timestamp (closer to actual consumption)
                # and the more authoritative source if we have it.
                if new_ts is not None and (last.ts is None or new_ts > last.ts):
                    last.ts = new_ts
                if source == "message":
                    last.source = "message"
                return
        messages.append(_TextMsg(
            role="user", ts=new_ts, text=new_text, source=source,
        ))

    for ev in events:
        ts = _parse_ts(ev.get("timestamp"))
        if session_id is None and ev.get("sessionId"):
            session_id = ev["sessionId"]
        if git_branch is None and ev.get("gitBranch"):
            git_branch = ev["gitBranch"]
        if cc_version is None and ev.get("version"):
            cc_version = ev["version"]
        if entrypoint is None and ev.get("entrypoint"):
            entrypoint = ev["entrypoint"]
        if agent_id is None and ev.get("agentId"):
            agent_id = ev["agentId"]
        _touch(ts)

        t = ev.get("type")
        msg = ev.get("message") or {}

        if t == "pr-link":
            if ev.get("prNumber") is not None:
                try:
                    pr_numbers.append(int(ev["prNumber"]))
                except (TypeError, ValueError):
                    pass
            if ev.get("prUrl"):
                pr_urls.append(str(ev["prUrl"]))
            continue

        if t == "queue-operation" and ev.get("operation") == "enqueue" and ev.get("content"):
            _append_user_prompt(str(ev["content"]), ts, "queue")
            continue

        if t == "user":
            if _is_prompt_user_event(msg):
                _append_user_prompt(_extract_prompt_text(msg), ts, "message")
                continue
            # Tool result wrapper — finalize pending tool entries.
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict) or part.get("type") != "tool_result":
                        continue
                    tid = part.get("tool_use_id")
                    if not tid or tid not in pending_by_id:
                        continue
                    tc = pending_by_id[tid]
                    # We only need the char count — drop the payload.
                    _, char_count = _coerce_result_text(part.get("content"))
                    is_err = bool(part.get("is_error"))
                    tur = ev.get("toolUseResult") or {}
                    if isinstance(tur, dict):
                        if tur.get("interrupted") is True:
                            is_err = True
                        dur_ms = tur.get("totalDurationMs") or tur.get("durationMs")
                        if isinstance(dur_ms, (int, float)):
                            tc.duration_ms = int(dur_ms)
                    if tc.duration_ms is None and ts is not None and tc.ts is not None:
                        delta = (ts - tc.ts).total_seconds() * 1000.0
                        if delta >= 0:
                            tc.duration_ms = int(delta)
                    tc.result_chars = char_count
                    tc.is_error = is_err
            continue

        if t == "assistant":
            if model is None and msg.get("model") and msg["model"] != "<synthetic>":
                model = msg["model"]
            content = msg.get("content") or []
            if not isinstance(content, list):
                continue
            # Aggregate text/thinking parts into one _TextMsg per assistant turn;
            # emit tool_use parts as separate _ToolMsg entries.
            text_parts: list[str] = []
            thinking_chars = 0
            tool_msgs: list[_ToolMsg] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                pt = part.get("type")
                if pt == "text":
                    if isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
                elif pt == "thinking":
                    thought = part.get("thinking") or part.get("text") or ""
                    if isinstance(thought, str):
                        thinking_chars += len(thought)
                elif pt == "tool_use":
                    name = part.get("name", "<unknown>")
                    args = part.get("input") or {}
                    tool_msg = _ToolMsg(
                        ts=ts,
                        name=name,
                        tool_use_id=part.get("id", ""),
                        target=_target_file(name, args),
                        args_preview=_args_preview(name, args),
                    )
                    tool_msgs.append(tool_msg)
                    if tool_msg.tool_use_id:
                        pending_by_id[tool_msg.tool_use_id] = tool_msg
            if text_parts or thinking_chars:
                messages.append(_TextMsg(
                    role="ai", ts=ts,
                    text="\n".join(t for t in text_parts if t),
                    thinking_chars=thinking_chars,
                ))
            for tm in tool_msgs:
                messages.append(tm)
            continue
        # Ignore everything else.

    if session_id is None:
        session_id = path.stem
    if is_subagent:
        suffix = agent_id or path.stem
        parent_session_id = session_id
        session_id = f"{session_id}:{suffix}"

    return ParsedSession(
        session_id=session_id,
        source_path=str(path),
        project=project,
        started_at=started_at,
        ended_at=ended_at,
        model=model,
        is_subagent=is_subagent,
        parent_session_id=parent_session_id,
        agent_id=agent_id,
        git_branch=git_branch,
        cc_version=cc_version,
        entrypoint=entrypoint,
        pr_numbers=pr_numbers,
        pr_urls=pr_urls,
        messages=messages,
    )


def iter_session_files(root: Path) -> Iterator[Path]:
    root = Path(root).expanduser()
    if not root.exists():
        return
    yield from sorted(root.rglob("*.jsonl"))


# =====================================================================
# Row building.
# =====================================================================

# Column order — keep stable, downstream tools often bind by position.
COLUMNS = [
    "session_id",
    "started_at",
    "ended_at",
    "date",
    "duration_s",
    "project",
    "model",
    "is_subagent",
    "parent_session_id",
    "agent_id",
    "git_branch",
    "cc_version",
    "entrypoint",
    "source_file",
    "n_messages",
    "n_user_prompts",
    "n_ai_turns",
    "n_tool_calls",
    "n_tool_errors",
    "total_user_chars",
    "total_ai_chars",
    "tools_used",
    "has_pr",
    "pr_count",
    "pr_numbers",
    "pr_urls",
    "messages_json_chars",
    "messages",
]


def _messages_to_json(messages: list[Any]) -> list[dict]:
    """Emit each message with a `content` string (standard chat-message shape).

    Downstream ingestion pipelines commonly do `turn.get("content", "")` and
    drop empty turns, so every turn — including tool turns — must carry a
    non-empty `content` string. For tool turns we synthesize a compact
    human-readable line from name / target / args_preview / result, while
    also keeping those structured fields so a parser that understands
    role=tool natively can still bind them.
    """
    out: list[dict] = []
    for m in messages:
        if isinstance(m, _TextMsg):
            content = m.text or ""
            # AI turns that were pure `thinking` with no text would otherwise
            # get dropped by downstream parsers (which gate on non-empty
            # content). Give them a marker so the turn survives and the
            # thinking-only signal is visible.
            if m.role == "ai" and not content and m.thinking_chars:
                content = f"[thinking only: {m.thinking_chars}c]"
            entry: dict = {
                "role": m.role,
                "ts": _iso(m.ts),
                "content": content,
            }
            if m.role == "ai" and m.thinking_chars:
                entry["thinking_chars"] = m.thinking_chars
            if m.source == "queue":
                entry["source"] = "queue"
            out.append(entry)
        elif isinstance(m, _ToolMsg):
            result = _format_result(m.result_chars, m.is_error)
            entry = {
                "role": "tool",
                "ts": _iso(m.ts),
                "content": _tool_content(m.name, m.target, m.args_preview, result),
                # Structured fields kept alongside `content` for parsers that
                # want to bind them directly (e.g. "find all Read calls").
                "name": m.name,
            }
            if m.target:
                entry["target"] = m.target
            if m.args_preview:
                entry["args_preview"] = m.args_preview
            entry["result"] = result
            if m.duration_ms is not None:
                entry["duration_ms"] = m.duration_ms
            out.append(entry)
    return out


def build_row(s: ParsedSession) -> dict:
    msgs = _messages_to_json(s.messages)

    n_user = sum(1 for m in msgs if m["role"] == "user")
    n_ai = sum(1 for m in msgs if m["role"] == "ai")
    n_tool = sum(1 for m in msgs if m["role"] == "tool")
    n_tool_err = sum(1 for m in msgs if m["role"] == "tool" and m.get("is_error"))

    total_user_chars = sum(len(m.get("content", "")) for m in msgs if m["role"] == "user")
    total_ai_chars = sum(len(m.get("content", "")) for m in msgs if m["role"] == "ai")

    tools_used_counter: dict[str, int] = {}
    for m in msgs:
        if m["role"] == "tool":
            tools_used_counter[m["name"]] = tools_used_counter.get(m["name"], 0) + 1
    tools_used = ",".join(
        f"{name}:{cnt}" for name, cnt in
        sorted(tools_used_counter.items(), key=lambda kv: -kv[1])
    )

    started = s.started_at
    ended = s.ended_at
    duration_s = None
    if started and ended:
        duration_s = round((ended - started).total_seconds(), 1)

    date_str = None
    if started:
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        date_str = started.astimezone(timezone.utc).date().isoformat()

    messages_json = json.dumps(msgs, ensure_ascii=False, separators=(",", ":"))

    return {
        "session_id": s.session_id,
        "started_at": _iso(s.started_at),
        "ended_at": _iso(s.ended_at),
        "date": date_str,
        "duration_s": duration_s,
        "project": s.project,
        "model": s.model,
        "is_subagent": "true" if s.is_subagent else "false",
        "parent_session_id": s.parent_session_id,
        "agent_id": s.agent_id,
        "git_branch": s.git_branch,
        "cc_version": s.cc_version,
        "entrypoint": s.entrypoint,
        "source_file": s.source_path,
        "n_messages": len(msgs),
        "n_user_prompts": n_user,
        "n_ai_turns": n_ai,
        "n_tool_calls": n_tool,
        "n_tool_errors": n_tool_err,
        "total_user_chars": total_user_chars,
        "total_ai_chars": total_ai_chars,
        "tools_used": tools_used,
        "has_pr": "true" if s.pr_numbers or s.pr_urls else "false",
        # Dedupe PR numbers/urls while preserving first-seen order — some
        # sessions emit the same pr-link multiple times.
        "pr_count": len(_dedupe_preserve(s.pr_numbers)) or len(_dedupe_preserve(s.pr_urls)),
        "pr_numbers": ",".join(str(n) for n in _dedupe_preserve(s.pr_numbers)) if s.pr_numbers else None,
        "pr_urls": ",".join(_dedupe_preserve(s.pr_urls)) if s.pr_urls else None,
        "messages_json_chars": len(messages_json),
        "messages": messages_json,
    }


# =====================================================================
# Writers.
# =====================================================================

def write_csv(rows: Iterator[dict], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: (row.get(k) if row.get(k) is not None else "") for k in COLUMNS})
            n += 1
    return n


def write_jsonl(rows: Iterator[dict], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            # Decode messages back into a list so the JSONL is well-typed.
            row = dict(row)
            if isinstance(row.get("messages"), str):
                try:
                    row["messages"] = json.loads(row["messages"])
                except json.JSONDecodeError:
                    pass
            fh.write(json.dumps(row, ensure_ascii=False, default=str))
            fh.write("\n")
            n += 1
    return n


# =====================================================================
# Entry point.
# =====================================================================

def default_sessions_root() -> Path:
    env = os.environ.get("CLAUDE_SESSIONS_ROOT")
    if env:
        return Path(env).expanduser()
    return Path("~/.claude/projects").expanduser()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export Claude Code session JSONLs to a single CSV (or JSONL)."
    )
    parser.add_argument(
        "sessions_root", nargs="?", default=None,
        help="Directory of Claude Code session JSONLs "
             "(default: ~/.claude/projects, or $CLAUDE_SESSIONS_ROOT).",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output file path. Defaults to ./claude_sessions.csv (or .jsonl).",
    )
    parser.add_argument(
        "--format", choices=("csv", "jsonl"), default="csv",
        help="Output format (default: csv).",
    )
    parser.add_argument(
        "--min-messages", type=int, default=1,
        help="Drop sessions with fewer than N messages (default: 1).",
    )
    parser.add_argument(
        "--include-subagents", default="true", choices=("true", "false"),
        help="Include subagent sessions (default: true).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    root = Path(args.sessions_root).expanduser() if args.sessions_root else default_sessions_root()
    if not root.exists():
        log.error("sessions root does not exist: %s", root)
        log.error("Pass the path as the first arg, or set CLAUDE_SESSIONS_ROOT.")
        return 2

    if args.out is None:
        args.out = f"claude_sessions.{args.format}"
    out_path = Path(args.out)

    include_sub = args.include_subagents == "true"

    log.info("scanning %s", root)
    rows: list[dict] = []
    skipped_thin = 0
    skipped_subagents = 0
    parse_errors = 0

    for p in iter_session_files(root):
        try:
            s = parse_session_file(p)
        except Exception as exc:
            log.warning("parse failed for %s: %s", p, exc)
            parse_errors += 1
            continue
        if s is None:
            continue
        if s.is_subagent and not include_sub:
            skipped_subagents += 1
            continue
        row = build_row(s)
        if row["n_messages"] < args.min_messages:
            skipped_thin += 1
            continue
        rows.append(row)

    if not rows:
        log.error("no sessions to export under %s", root)
        return 1

    # Sort for determinism: by started_at, then session_id.
    rows.sort(key=lambda r: (r.get("started_at") or "", r["session_id"]))

    if args.format == "csv":
        n = write_csv(iter(rows), out_path)
    else:
        n = write_jsonl(iter(rows), out_path)

    # Size + distribution summary.
    try:
        size_bytes = out_path.stat().st_size
        size_human = _humanize_bytes(size_bytes)
    except OSError:
        size_human = "?"

    char_total = sum(r["messages_json_chars"] for r in rows)
    avg_chars = int(char_total / len(rows)) if rows else 0
    p95_chars = 0
    if rows:
        vals = sorted(r["messages_json_chars"] for r in rows)
        p95_chars = vals[int(0.95 * (len(vals) - 1))]

    print()
    print("=" * 64)
    print(f"Exported {n} sessions to {out_path}  ({size_human})")
    print("=" * 64)
    print(f"  parents: {sum(1 for r in rows if r['is_subagent'] == 'false')}")
    print(f"  subagents: {sum(1 for r in rows if r['is_subagent'] == 'true')}")
    print(f"  with PR correlation: {sum(1 for r in rows if r['has_pr'] == 'true')}")
    print(f"  date range: {rows[0]['date']} → {rows[-1]['date']}")
    print(f"  messages column — avg {avg_chars:,} chars, p95 {p95_chars:,} chars")
    if skipped_thin:
        print(f"  skipped (below --min-messages={args.min_messages}): {skipped_thin}")
    if skipped_subagents:
        print(f"  skipped (--include-subagents=false): {skipped_subagents}")
    if parse_errors:
        print(f"  parse errors: {parse_errors}")
    return 0


def _format_result(chars: Optional[int], is_error: bool) -> str:
    """Compact outcome marker for a tool call. Examples:

        "1234c"          -> success, 1234 chars of result
        "error"          -> errored, no char count
        "error:1234c"    -> errored, 1234 chars of result
        "—"              -> no tool_result event was seen (rare: interrupted / orphan)
    """
    if chars is None and not is_error:
        return "—"
    if is_error:
        return "error" if chars is None else f"error:{chars}c"
    return f"{chars}c"


def _tool_content(name: str, target: Optional[str],
                  args_preview: Optional[str], result: str) -> str:
    """Build the single-string `content` for a tool turn.

    Downstream ingestion (halfpipe and similar) extracts turn['content'] and
    drops turns that don't have it, so every tool turn needs a useful string
    here. We synthesize a compact one-liner that preserves the signal an
    analyst / LLM reader needs: which tool fired, what it targeted, rough
    args, and the outcome marker.

    Example: `Read target=foo.py args=file_path="foo.py" limit=200 → 5821c`
    """
    parts = [name]
    if target:
        parts.append(f"target={target}")
    if args_preview:
        parts.append(f"args={args_preview}")
    parts.append(f"→ {result}")
    return " ".join(parts)


def _dedupe_preserve(xs):
    """De-duplicate while keeping first-seen order."""
    seen = set()
    out = []
    for x in xs or []:
        if x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def _humanize_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


if __name__ == "__main__":
    sys.exit(main())
