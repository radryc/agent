#!/usr/bin/env python3
"""Command-line wrapper for the shared Doctor graph validator.

Usage:
    python3 validate_graph_file.py FILE [FILE ...]

The implementation lives in ``backend/app/agent/graph_validation.py`` so the
agent tool and this script always enforce the same rules.
"""

from __future__ import annotations

import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(AGENT_ROOT / "backend"))

from app.agent.graph_validation import cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(cli(sys.argv))
