from __future__ import annotations

import math
import logging
from datetime import datetime, timezone

from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

TOOLS = {
    "datetime_now": {
        "description": "Returns the current date and time in UTC.",
        "parameters": {},
    },
    "calculate": {
        "description": "Evaluate a safe mathematical expression.",
        "parameters": {"expression": "A mathematical expression string"},
    },
    "web_search": {
        "description": (
            "Search the web for current information using DuckDuckGo. "
            "Use this when the user asks about recent events, facts you are unsure about, "
            "or anything that may require up-to-date information."
        ),
        "parameters": {
            "query": "The search query string",
            "max_results": "Maximum number of results to return (default 5)",
        },
    },
    "generate_image": {
        "description": (
            "Generate an image using wan2gp Z-Image. "
            "Returns a viewable image URL. Use when the user asks to create, draw, or visualize something."
        ),
        "parameters": {
            "prompt": "Detailed description of the image to generate",
            "loras": "Optional comma-separated lora names to apply (default empty)",
        },
    },
}

SAFE_MATH = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "pow": pow,
    "sqrt": math.sqrt,
    "pi": math.pi,
    "e": math.e,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
}


async def execute_tool(name: str, args: dict | None = None) -> str:
    args = args or {}

    if name == "datetime_now":
        return datetime.now(timezone.utc).isoformat()

    if name == "calculate":
        expression = args.get("expression", "")
        if not expression:
            return "Error: no expression provided"
        try:
            result = eval(expression, {"__builtins__": {}}, SAFE_MATH)  # noqa: S307
            return str(result)
        except Exception as e:
            return f"Error: {e}"

    if name == "web_search":
        query = args.get("query", "")
        if not query:
            return "Error: no query provided"
        max_results = int(args.get("max_results", 5))
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=max_results))
            if not results:
                return "No results found."
            lines = []
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r.get('title', '')}\n   {r.get('href', '')}\n   {r.get('body', '')}")
            return "\n\n".join(lines)
        except Exception as e:
            logger.warning("web_search failed: %s", e)
            return f"Search failed: {e}"

    if name == "generate_image":
        from app.image.client import generate_image as _gen_image
        from app.config import get_runtime

        prompt = args.get("prompt", "")
        if not prompt:
            return "Error: no prompt provided"

        loras = args.get("loras", get_runtime("image_loras") or "")
        try:
            urls = await _gen_image(
                prompt=prompt,
                base_url=get_runtime("wan2gp_url"),
                image_generator=get_runtime("image_generator"),
                image_width=int(get_runtime("image_width")),
                image_height=int(get_runtime("image_height")),
                image_seed=int(get_runtime("image_seed")),
                image_loras=loras,
                video_generator=get_runtime("video_generator"),
            )
            if not urls:
                return "Image generation completed but no image URL was returned."
            # Format as markdown image tags so the frontend can render them
            return "\n".join(f"![generated image]({u})" for u in urls)
        except Exception as e:
            logger.exception("generate_image failed")
            return f"Image generation failed: {e}"

    return f"Error: unknown tool '{name}'"


SAFE_MATH = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "pow": pow,
    "sqrt": math.sqrt,
    "pi": math.pi,
    "e": math.e,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
}
