#!/usr/bin/env python3
"""Slice 4 test: worker prompt templates exist, cover the required contract keys
and guardrails, and contain no em-dashes. `python3 test_templates.py`."""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(os.path.dirname(HERE), "templates")


def main() -> int:
    a = open(os.path.join(TPL, "phase_A.md")).read()
    b = open(os.path.join(TPL, "phase_B.md")).read()

    # Phase A must reference the schema fields + key guardrails.
    for token in ["status", "root_causes", "culprit_symbol", "error_signature",
                  "signature", "pass_rate", "NEEDS_GPU_EXEC", "reason_not_reproduced",
                  "testx-debug", "Never land"]:
        assert token in a, f"phase_A.md missing: {token}"

    # Phase B must reference the fix loop + no-land + submit contract.
    for token in ["arc lint", "buck2 test", "jf submit", "ci_status",
                  "DIFF_LOCAL_ONLY", "diff-authoring", "NEVER run", "Nothing lands"]:
        assert token in b, f"phase_B.md missing: {token}"

    # Neither template should contain em-dashes (authoring convention).
    for name, txt in (("phase_A.md", a), ("phase_B.md", b)):
        assert "—" not in txt, f"{name} contains an em-dash"

    print("PASS test_templates: phase_A + phase_B cover contract keys + guardrails, no em-dashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
