"""GBNF grammars for constrained tool-call decoding (llama.cpp).

The agent speaks a line-oriented JSON tool protocol:

    {"tool": "doctor_logs_search", "args": {"partition": "monofs"}}

Small local models (Gemma 4 E4B, Qwen 4B, ...) usually understand the intent
but occasionally break the format: extra prose on the same line, trailing
comments, single quotes, key order changes, truncated JSON. llama.cpp can mask
all tokens that would violate a grammar, so the retry is guaranteed to be
parseable.

We only constrain *tool-call attempts*. Final answers stay unconstrained,
because a grammar that allows arbitrary prose and JSON tool calls at the same
time is not expressible in GBNF.
"""

from __future__ import annotations

import re

# Accept one or more canonical calls, optionally separated by whitespace.
# Values are full JSON so nested objects/arrays (e.g. Guardian manifests,
# dashboard specs) still work.
TOOL_CALL_GRAMMAR = r'''
root ::= line (nl line)* nl?
line ::= "{" ws "\"tool\"" ws ":" ws string (ws "," ws "\"args\"" ws ":" ws value)? ws "}"
value ::= object | array | string | number | "true" | "false" | "null"
object ::= "{" ws (pair (ws "," ws pair)*)? ws "}"
pair ::= string ws ":" ws value
array ::= "[" ws (value (ws "," ws value)*)? ws "]"
string ::= "\"" char* "\""
char ::= [^"\\] | "\\" (["\\/bfnrt] | "u" hex hex hex hex)
hex ::= [0-9a-fA-F]
number ::= ("-"? ([0] | [1-9] [0-9]*)) ("." [0-9]+)? ([eE] [-+]? [0-9]+)?
nl ::= "\n" | "\r\n"
ws ::= [ \t]*
'''

# Heuristics for "the model tried to call a tool but the JSON is broken".
_TOOL_HINT_RE = re.compile(r'"(?:tool|args)"\s*:', re.IGNORECASE)
_NATIVE_TOOL_TAG_RE = re.compile(r"<\|?tool_call\|?>")


def tool_call_grammar(override: str = "") -> str:
    """Return the GBNF to use for tool-call retries."""
    override = str(override or "").strip()
    return override or TOOL_CALL_GRAMMAR


def looks_like_tool_attempt(text: str) -> bool:
    """True when a response clearly attempted a tool call but did not parse."""
    text = str(text or "").strip()
    if not text:
        return False
    if _NATIVE_TOOL_TAG_RE.search(text):
        return True
    if _TOOL_HINT_RE.search(text):
        return True
    # A completion that starts with an object-shaped fragment and mentions a
    # known-ish "tool" key is almost always an unfinished call.
    if text.startswith("{") and "tool" in text[:200].lower():
        return True
    return False
