#!/usr/bin/env python3
"""Smoke test for the token-roi renderer pipeline.

Run before shipping to confirm the renderer can produce artifacts from the
schema + the checked-in fixture — catches schema drift, broken SVG builders,
and missing edge-case handling.

Steps:
  1. Validate tests/fixtures/roi_data.sample.json against the schema.
  2. Run render_report.py against the fixture into a temp dir.
  3. Assert report.html and hero.md exist and exceed a minimum byte size.
  4. Assert report.html contains the three chart section headings.

Exit code: 0 on all-pass, 1 on any fail.

Runnable as: python tests/smoke_test.py
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RENDERER = ROOT / "analysis" / "render_report.py"
SCHEMA = ROOT / "analysis" / "roi_data.schema.json"
FIXTURE = HERE / "fixtures" / "roi_data.sample.json"

# Render with the real slug so the smoke test exercises the same filenames
# the skill produces (and guards the lib/report_io scheme against drift).
SLUG = "token-roi"

EXPECTED_ARTIFACTS = {
    f"{SLUG}-report.html": 3000,
    f"{SLUG}-hero.md": 150,
}

REQUIRED_HEADINGS = [
    "Weekly trend",
    "Weekly outcome distributions",
    "Token cost per unit of work",
]


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def validate_fixture() -> list:
    """Validate the fixture against the schema. Returns failure strings."""
    try:
        import jsonschema
    except ImportError:
        return ["jsonschema not installed — cannot validate fixture"]
    if not FIXTURE.exists():
        return [f"fixture missing: {_rel(FIXTURE)}"]
    if not SCHEMA.exists():
        return [f"schema missing: {_rel(SCHEMA)}"]
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    out = []
    for e in errors:
        where = ".".join(str(p) for p in e.path) or "<root>"
        out.append(f"schema: at {where}: {e.message}")
    return out


def run_render() -> list:
    """Render the fixture into a temp dir, check artifacts. Returns failures."""
    failures = []
    with tempfile.TemporaryDirectory(prefix="token-roi-smoke-") as tmp:
        tmp_path = Path(tmp)
        result = subprocess.run(
            [
                sys.executable, str(RENDERER),
                "--data", str(FIXTURE),
                "--out", str(tmp_path),
                "--slug", SLUG,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip() or "(no stderr)"
            return [f"renderer exited with {result.returncode}\n"
                    f"      stderr: {stderr}"]

        for fname, min_size in EXPECTED_ARTIFACTS.items():
            f = tmp_path / fname
            if not f.exists():
                failures.append(f"{fname} not produced")
                continue
            size = f.stat().st_size
            if size < min_size:
                failures.append(
                    f"{fname} suspiciously small ({size} bytes, "
                    f"expected >= {min_size})")

        html_path = tmp_path / f"{SLUG}-report.html"
        if html_path.exists():
            html = html_path.read_text(encoding="utf-8")
            for heading in REQUIRED_HEADINGS:
                if heading not in html:
                    failures.append(f"report.html missing heading: {heading!r}")
            if "<svg" not in html:
                failures.append("report.html contains no inline <svg>")
    return failures


def main() -> int:
    print("=" * 64)
    print("token-roi renderer smoke test")
    print("=" * 64)
    print()

    if not RENDERER.exists():
        print(f"FAIL: renderer not found at {_rel(RENDERER)}")
        return 1

    total_failures = []

    print("  step: validate fixture against schema")
    f1 = validate_fixture()
    if f1:
        print("    FAIL")
        for f in f1:
            print(f"      - {f}")
        total_failures.extend(f1)
    else:
        print("    PASS")
    print()

    print("  step: render report.html + hero.md from fixture")
    f2 = run_render()
    if f2:
        print("    FAIL")
        for f in f2:
            print(f"      - {f}")
        total_failures.extend(f2)
    else:
        print("    PASS (2 artifacts produced, 3 chart headings present)")
    print()

    print("-" * 64)
    if total_failures:
        print(f"FAILED: {len(total_failures)} issue(s)")
        return 1
    print("OK (smoke test passed)")
    print()
    print("The renderer can produce report.html + hero.md from a schema-valid")
    print("roi_data.json. Safe to proceed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
