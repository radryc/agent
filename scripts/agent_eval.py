#!/usr/bin/env python3
"""Small tool-calling eval for the agent profiles.

The point is to replace leaderboard folklore with a handful of *your* prompts.
It sends each prompt through the same persona system prompt + tool instructions
the live loop uses, then scores whether the model emitted a parseable tool call
(and, optionally, whether it called the expected tool).

Usage:
    PYTHONPATH=backend .venv/bin/python scripts/agent_eval.py \
        --profile local --profile fallback \
        --prompts eval/prompts.jsonl

Prompt file format (JSONL, one object per line):
    {"prompt": "check the latest monofs errors", "expect": "doctor_logs_search"}
    {"prompt": "is everything healthy?"}
    {"prompt": "write a haiku about rust", "expect": null}

With no --prompts file, a small built-in set is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.agent.loop import _extract_tool_calls  # noqa: E402
from app.agent.personas import allowed_tools, persona_system_prompt  # noqa: E402
from app.agent.tools import build_tool_instructions  # noqa: E402
from app.llm.client import chat_completion, profile_label, profile_supports_grammar  # noqa: E402
from app.llm.grammar import TOOL_CALL_GRAMMAR  # noqa: E402

DEFAULT_PROMPTS = [
    {"prompt": "is everything healthy?", "expect": "guardian_overview"},
    {"prompt": "list the guardian partitions", "expect": "guardian_partitions"},
    {"prompt": "show the latest errors in the monofs partition", "expect": "doctor_logs_search"},
    {"prompt": "what is the p99 latency for the agent partition?", "expect": "doctor_metrics_query"},
    {"prompt": "find failing traces in monofs", "expect": "doctor_traces_recent"},
    {"prompt": "search the codebase for HandleRequest", "expect": "monofs_search"},
    {"prompt": "what time is it in UTC?", "expect": "datetime_now"},
    {"prompt": "write a haiku about rust", "expect": None},
    {"prompt": "explain what a mutex is", "expect": None},
]


async def run_prompt(profile: str, item: dict, *, grammar: bool) -> dict:
    messages = [
        {
            "role": "system",
            "content": persona_system_prompt("otto") + build_tool_instructions(allowed_tools("otto")),
        },
        {"role": "user", "content": item["prompt"]},
    ]
    kwargs = {}
    if grammar and profile_supports_grammar(profile):
        kwargs["grammar"] = TOOL_CALL_GRAMMAR
    try:
        response = await chat_completion(messages, stream=False, profile=profile, **kwargs)
    except Exception as exc:  # noqa: BLE001 - eval should report, not crash
        return {"error": str(exc), "calls": [], "correct": False}

    calls = _extract_tool_calls(response)
    names = [str(c.get("tool", "")) for c in calls]
    expect = item.get("expect")
    if expect is None:
        correct = not names
    else:
        correct = expect in names
    return {"calls": names, "correct": correct, "parseable": bool(names) or expect is None}


async def evaluate(profiles: list[str], prompts: list[dict], *, grammar: bool) -> None:
    print(f"Prompts: {len(prompts)}\n")
    header = f"{'profile':<28} {'parse/correct':>14} {'accuracy':>9}  failures"
    print(header)
    print("-" * len(header))
    for profile in profiles:
        results = [await run_prompt(profile, item, grammar=grammar) for item in prompts]
        correct = sum(1 for r in results if r.get("correct"))
        parseable = sum(1 for r in results if r.get("parseable"))
        failures = [
            item["prompt"]
            for item, result in zip(prompts, results)
            if not result.get("correct")
        ][:3]
        print(
            f"{profile_label(profile):<28} {parseable:>6}/{correct:<7} "
            f"{correct / len(prompts) * 100:>8.0f}%  {'; '.join(failures)}"
        )
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        action="append",
        dest="profiles",
        help="Profile to test (local, fallback, provider:<name>). Repeat to compare.",
    )
    parser.add_argument("--prompts", type=Path, help="JSONL prompt file")
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="Run each prompt N times (default 1).",
    )
    parser.add_argument(
        "--grammar",
        action="store_true",
        help="Constrain every completion with the tool-call GBNF (llama.cpp only).",
    )
    args = parser.parse_args()

    profiles = args.profiles or ["local"]
    if args.prompts:
        prompts = [
            json.loads(line)
            for line in args.prompts.read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    else:
        prompts = DEFAULT_PROMPTS
    prompts = prompts * max(1, args.samples)

    asyncio.run(evaluate(profiles, prompts, grammar=args.grammar))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
