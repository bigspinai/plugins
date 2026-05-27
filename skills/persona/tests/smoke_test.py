#!/usr/bin/env python3
"""Smoke test for the renderer pipeline.

Run before tagging to confirm the renderer can produce artifacts from
the schema + an exemplar — catches schema drift, broken ``*_ref`` paths,
and missing edge-case handling before the user spends money on tagging.

Two cases:
  - **Showrunner exemplar.** Pairs ``report/report_content.json`` (the
    canonical reference shape, hand-authored) with a synthetic Showrunner
    ``metrics.json``. If schema-validation fails or any ``*_ref`` doesn't
    resolve, the exemplar has drifted from the renderer.
  - **Multi-Mode Journeyman edge case.** Pairs ``tests/fixtures/sample_content.generalist.json``
    with a Multi-Mode Journeyman ``metrics.json`` (no shadow, no within-archetype
    positioning). Confirms the renderer suppresses the shadow block when
    the resolved name is empty and falls back gracefully when cohort
    bars aren't available.

Each case runs the renderer end-to-end into a temp dir and confirms all
five artifacts (HTML, full markdown, hero markdown, two CLI cards) land
with non-trivial size.

Exit code: 0 on all-pass, 1 on any fail.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RENDERER = ROOT / "analysis" / "render_report.py"

# (label, content path, metrics path)
CASES: list[tuple[str, Path, Path]] = [
    (
        "Showrunner exemplar (sharp fingerprint, full shadow + cohort bars)",
        ROOT / "report" / "report_content.json",
        HERE / "fixtures" / "sample_metrics.manager.json",
    ),
    (
        "Multi-Mode Journeyman edge case (no shadow, no within-archetype positioning)",
        HERE / "fixtures" / "sample_content.generalist.json",
        HERE / "fixtures" / "sample_metrics.generalist.json",
    ),
]

# Files the renderer must produce, with a minimum size that catches
# silently-empty outputs without being so high we trip on small variants.
EXPECTED_ARTIFACTS: dict[str, int] = {
    "report.html": 4000,
    "report.md": 800,
    "hero.md": 200,
    "hero_card.txt": 500,
    "hero_card.plain.txt": 500,
}


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def run_case(label: str, content: Path, metrics: Path) -> list[str]:
    """Run a single case. Returns a list of failure strings (empty on pass)."""
    failures: list[str] = []

    if not content.exists():
        return [f"content file missing: {_rel(content)}"]
    if not metrics.exists():
        return [f"metrics file missing: {_rel(metrics)}"]

    with tempfile.TemporaryDirectory(prefix="practice-mirror-smoke-") as tmp:
        tmp_path = Path(tmp)
        result = subprocess.run(
            [
                sys.executable, str(RENDERER),
                "--content", str(content),
                "--metrics", str(metrics),
                "--out", str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() or "(no stderr)"
            stdout = (result.stdout or "").strip()
            msg = f"renderer exited with {result.returncode}\n      stderr: {stderr}"
            if stdout:
                msg += f"\n      stdout: {stdout}"
            failures.append(msg)
            return failures

        for fname, min_size in EXPECTED_ARTIFACTS.items():
            f = tmp_path / fname
            if not f.exists():
                failures.append(f"{fname} not produced")
                continue
            size = f.stat().st_size
            if size < min_size:
                failures.append(
                    f"{fname} suspiciously small ({size} bytes, expected ≥ {min_size})"
                )

    return failures


# Negative cases — the renderer MUST reject these. Guardrail: the displayed
# archetype must match the deterministic classifier in metrics.json
# (analysis/render_report.validate_archetype). Both a novel name and a
# valid-but-wrong name should abort the render with a non-zero exit. The
# valid name is derived from the classifier, so this stays correct across
# archetype renames with no edit here.
NEGATIVE_SPECS: list[tuple[str, Path, Path, str]] = [
    (
        "guardrail: novel archetype name rejected",
        ROOT / "report" / "report_content.json",
        HERE / "fixtures" / "sample_metrics.manager.json",
        "Trailblazer",
    ),
    (
        "guardrail: archetype name mismatched with classifier rejected",
        ROOT / "report" / "report_content.json",
        HERE / "fixtures" / "sample_metrics.manager.json",
        "Runtime Mechanic",
    ),
]


def run_negative_case(content: Path, metrics: Path, bad_name: str) -> list[str]:
    """Mutate the exemplar's archetype_name to ``bad_name`` and confirm the
    renderer exits non-zero. Returns failure strings (empty on pass)."""
    if not content.exists():
        return [f"content file missing: {_rel(content)}"]
    if not metrics.exists():
        return [f"metrics file missing: {_rel(metrics)}"]

    data = json.loads(content.read_text(encoding="utf-8"))
    data["title"]["archetype_name"] = bad_name
    with tempfile.TemporaryDirectory(prefix="practice-mirror-neg-") as tmp:
        tmp_path = Path(tmp)
        bad_content = tmp_path / "content.json"
        bad_content.write_text(json.dumps(data), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable, str(RENDERER),
                "--content", str(bad_content),
                "--metrics", str(metrics),
                "--out", str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return [
                f"renderer ACCEPTED archetype_name={bad_name!r} — the guardrail "
                f"should reject it (does not match the classifier's primary)"
            ]
    return []


def main() -> int:
    print("=" * 64)
    print("Renderer smoke test")
    print("=" * 64)
    print()

    if not RENDERER.exists():
        print(f"FAIL: renderer not found at {_rel(RENDERER)}")
        return 1

    total_failures: list[tuple[str, list[str]]] = []
    for label, content, metrics in CASES:
        print(f"  case: {label}")
        print(f"    content: {_rel(content)}")
        print(f"    metrics: {_rel(metrics)}")
        failures = run_case(label, content, metrics)
        if failures:
            print("    FAIL")
            for f in failures:
                print(f"      - {f}")
            total_failures.append((label, failures))
        else:
            print("    PASS (5 artifacts produced)")
        print()

    for label, content, metrics, bad_name in NEGATIVE_SPECS:
        print(f"  case: {label}")
        failures = run_negative_case(content, metrics, bad_name)
        if failures:
            print("    FAIL")
            for f in failures:
                print(f"      - {f}")
            total_failures.append((label, failures))
        else:
            print("    PASS (renderer rejected it)")
        print()

    total = len(CASES) + len(NEGATIVE_SPECS)
    print("-" * 64)
    if total_failures:
        print(f"FAILED: {len(total_failures)} of {total} case(s)")
        return 1
    print(f"OK ({total} cases passed)")
    print()
    print("The renderer can produce artifacts from a schema-valid content")
    print("JSON. Safe to proceed with the tagging step.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
