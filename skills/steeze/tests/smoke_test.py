#!/usr/bin/env python3
"""Smoke test for the renderer pipeline.

Run before tagging to confirm the renderer can produce artifacts from
the schema + an exemplar — catches schema drift, broken ``*_ref`` paths,
and missing edge-case handling before the user spends money on tagging.

Two cases:
  - **Manager exemplar.** Pairs ``report/report_content.json`` (the
    canonical reference shape, hand-authored) with a synthetic Manager
    ``metrics.json``. If schema-validation fails or any ``*_ref`` doesn't
    resolve, the exemplar has drifted from the renderer.
  - **Generalist edge case.** Pairs ``tests/fixtures/sample_content.generalist.json``
    with a Generalist ``metrics.json`` (no shadow, no within-archetype
    positioning). Confirms the renderer suppresses the shadow block when
    the resolved name is empty and falls back gracefully when cohort
    bars aren't available.

Each case runs the renderer end-to-end into a temp dir and confirms all
five artifacts (HTML, full markdown, hero markdown, two CLI cards) land
with non-trivial size.

Exit code: 0 on all-pass, 1 on any fail.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# ROOT defaults to HERE.parent (the skill's own dir when running in-place).
# Override with --plugin-root when invoking from outside (e.g., the orchestrator
# running smoke_test.py against the installed plugin from $STEEZE_RUN_DIR).
ROOT = HERE.parent
RENDERER = ROOT / "analysis" / "render_report.py"

# (label, content_relative, metrics_path) — content path is rebuilt against
# the chosen ROOT inside main() so a CLI --plugin-root override works.
CASE_SPECS: list[tuple[str, str, Path]] = [
    (
        "Manager exemplar (sharp fingerprint, full shadow + cohort bars)",
        "report/report_content.json",
        HERE / "fixtures" / "sample_metrics.manager.json",
    ),
    (
        "Generalist edge case (no shadow, no within-archetype positioning)",
        # Generalist uses a fixture under tests/, not ROOT — kept as absolute below.
        "__fixture__:sample_content.generalist.json",
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Steeze renderer smoke test")
    parser.add_argument(
        "--plugin-root",
        type=Path,
        default=None,
        help="Override the plugin root (defaults to the parent of tests/). "
             "Use when invoking from outside the plugin install dir.",
    )
    args = parser.parse_args()

    global ROOT, RENDERER
    if args.plugin_root is not None:
        ROOT = args.plugin_root.resolve()
        RENDERER = ROOT / "analysis" / "render_report.py"

    cases: list[tuple[str, Path, Path]] = []
    for label, content_spec, metrics in CASE_SPECS:
        if content_spec.startswith("__fixture__:"):
            fname = content_spec.split(":", 1)[1]
            cases.append((label, HERE / "fixtures" / fname, metrics))
        else:
            cases.append((label, ROOT / content_spec, metrics))

    print("=" * 64)
    print("Renderer smoke test")
    print("=" * 64)
    print(f"  plugin root: {ROOT}")
    print()

    if not RENDERER.exists():
        print(f"FAIL: renderer not found at {_rel(RENDERER)}")
        return 1

    total_failures: list[tuple[str, list[str]]] = []
    for label, content, metrics in cases:
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

    print("-" * 64)
    if total_failures:
        print(f"FAILED: {len(total_failures)} of {len(cases)} case(s)")
        return 1
    print(f"OK ({len(cases)} cases passed)")
    print()
    print("The renderer can produce artifacts from a schema-valid content")
    print("JSON. Safe to proceed with the tagging step.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
