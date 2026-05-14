#!/usr/bin/env bash
#
# Emit a fresh YYYYMMDD-HHMMSS run id on stdout.
#
# Used by the skill to name the per-run output directory under
# ~/.claude/steeze/<id>/ so multiple invocations don't collide.

date +%Y%m%d-%H%M%S
