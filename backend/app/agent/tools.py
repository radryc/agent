from __future__ import annotations

import asyncio
import math
import logging
import re
import ipaddress
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from duckduckgo_search import DDGS
import httpx

logger = logging.getLogger(__name__)

_DDG_BACKENDS = ("auto", "lite", "html")
_DDG_MAX_RESULTS = 10
_DDG_ATTEMPTS_PER_BACKEND = 2
_DDG_RETRY_BASE_SECONDS = 1.0

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
            "This is a local tool and must be used for web search, not Gemini. "
            "Use this when the user asks about recent events, facts you are unsure about, "
            "or anything that may require up-to-date information."
        ),
        "parameters": {
            "query": "The search query string",
            "max_results": "Maximum number of results to return (default 5)",
        },
    },
    "web_fetch": {
        "description": (
            "Fetch and extract readable text from a webpage URL using local HTTP fetch. "
            "Use this after web_search when the user asks to open/read/summarize a specific page. "
            "Do not use Gemini models for webpage fetching."
        ),
        "parameters": {
            "url": "The full http(s) URL to fetch",
            "max_chars": "Maximum characters to return from extracted text (default 6000)",
        },
    },
    "generate_image": {
        "description": (
            "Generate an image using Google Gemini Imagen models. "
            "Returns viewable image URLs. Use when the user asks to create, draw, or visualize something."
        ),
        "parameters": {
            "prompt": "Detailed description of the image to generate",
            "api_key": "Optional Gemini API key. If omitted, uses configured gemini_api_key",
            "model": "Optional Gemini image model (default from settings)",
            "aspect_ratio": "Optional aspect ratio, e.g. 1:1, 16:9, 9:16",
            "number_of_images": "Optional number of images (1-4)",
        },
    },
    "generate_image_gemini": {
        "description": (
            "Generate an image using Google Gemini Imagen models. "
            "Returns viewable image URLs. Supports per-call API key override."
        ),
        "parameters": {
            "prompt": "Detailed description of the image to generate",
            "api_key": "Optional Gemini API key. If omitted, uses configured gemini_api_key",
            "model": "Optional Gemini image model (default from settings)",
            "aspect_ratio": "Optional aspect ratio, e.g. 1:1, 16:9, 9:16",
            "number_of_images": "Optional number of images (1-4)",
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


_SCRIPT_STYLE_RE = re.compile(r"<\s*(script|style)[^>]*>.*?<\s*/\s*\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


async def _validate_public_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except Exception:
        return "Error: invalid url"

    if parsed.scheme not in {"http", "https"}:
        return "Error: url must start with http:// or https://"
    if not parsed.hostname:
        return "Error: url must include a hostname"

    hostname = parsed.hostname.strip().lower()
    if hostname in {"localhost", "localhost.localdomain"}:
        return "Error: local addresses are not allowed"

    try:
        ip = ipaddress.ip_address(hostname)
        addresses = [ip]
    except ValueError:
        try:
            loop = __import__("asyncio").get_running_loop()
            infos = await loop.getaddrinfo(
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror:
            return "Error: could not resolve hostname"
        except Exception as e:
            return f"Error: hostname resolution failed: {e}"

        addresses = []
        for info in infos:
            sockaddr = info[4]
            if sockaddr:
                try:
                    addresses.append(ipaddress.ip_address(sockaddr[0]))
                except ValueError:
                    continue

    for ip in addresses:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return f"Error: fetching non-public addresses is not allowed ({ip})"

    return None


def _html_to_text(html: str) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = text.replace("&nbsp;", " ")
    return _WS_RE.sub(" ", text).strip()


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    title = _TAG_RE.sub(" ", match.group(1))
    return _WS_RE.sub(" ", title).strip()


def _is_ddg_rate_limited(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(token in text for token in ("ratelimit", "rate limit", "429", "too many requests"))


def _run_ddg_search(query: str, max_results: int) -> tuple[list[dict], str] | tuple[None, str]:
    failures: list[str] = []
    saw_rate_limit = False

    for backend in _DDG_BACKENDS:
        for attempt in range(1, _DDG_ATTEMPTS_PER_BACKEND + 1):
            try:
                with DDGS() as ddgs:
                    results = list(ddgs.text(query, max_results=max_results, backend=backend))
                return results, backend
            except Exception as exc:
                failures.append(f"{backend}#{attempt}: {exc}")
                if _is_ddg_rate_limited(exc):
                    saw_rate_limit = True
                    if attempt < _DDG_ATTEMPTS_PER_BACKEND:
                        time.sleep(_DDG_RETRY_BASE_SECONDS * attempt)
                else:
                    break

    if saw_rate_limit:
        return None, (
            "Search temporarily unavailable due to provider rate limiting. "
            "Do not retry web_search immediately. Ask the user for a specific URL and use web_fetch instead."
        )

    if failures:
        return None, f"Search failed across backends: {' | '.join(failures[:3])}"
    return None, "Search failed: unknown error"


async def _generate_image_with_gemini(args: dict) -> str:
    from app.image.gemini_client import generate_images as _gen_images
    from app.config import get_runtime

    prompt = args.get("prompt", "")
    if not prompt:
        return "Error: no prompt provided"

    api_key = (args.get("api_key") or get_runtime("gemini_api_key") or "").strip()
    if not api_key:
        return (
            "Error: Gemini API key is not configured. "
            "Set gemini_api_key in settings or pass api_key in tool arguments."
        )

    model = args.get("model", get_runtime("gemini_image_model") or "imagen-3.0-generate-002")
    aspect_ratio = args.get("aspect_ratio", get_runtime("gemini_image_aspect_ratio") or "16:9")
    try:
        number_of_images = int(args.get("number_of_images", get_runtime("gemini_image_count") or 1))
    except (TypeError, ValueError):
        return "Error: number_of_images must be an integer"

    if number_of_images < 1 or number_of_images > 4:
        return "Error: number_of_images must be between 1 and 4"

    try:
        urls = await _gen_images(
            prompt=prompt,
            api_key=api_key,
            model=model,
            aspect_ratio=aspect_ratio,
            number_of_images=number_of_images,
        )
        if not urls:
            return "Gemini image generation completed but no image URL was returned."
        return "\n".join(f"![gemini generated image]({u})" for u in urls)
    except Exception as e:
        logger.exception("Gemini image generation failed")
        return f"Gemini image generation failed: {e}"


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
        try:
            max_results = int(args.get("max_results", 5))
        except (TypeError, ValueError):
            return "Error: max_results must be an integer"
        max_results = max(1, min(max_results, _DDG_MAX_RESULTS))

        try:
            results, detail = await asyncio.to_thread(_run_ddg_search, query, max_results)
            if results is None:
                logger.warning("web_search failed: %s", detail)
                return detail
            if not results:
                return "No results found."
            lines = []
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r.get('title', '')}\n   {r.get('href', '')}\n   {r.get('body', '')}")
            return "\n\n".join(lines)
        except Exception as e:
            logger.warning("web_search failed: %s", e)
            return f"Search failed: {e}"

    if name == "web_fetch":
        url = str(args.get("url", "")).strip()
        if not url:
            return "Error: no url provided"

        validation_error = await _validate_public_url(url)
        if validation_error:
            return validation_error

        try:
            max_chars = int(args.get("max_chars", 6000))
        except (TypeError, ValueError):
            return "Error: max_chars must be an integer"
        max_chars = max(500, min(max_chars, 20000))

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": "Lagent/0.1 (+local web_fetch tool)",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    },
                )
                resp.raise_for_status()
                content_type = (resp.headers.get("content-type") or "").lower()
                body = resp.text

            title = _extract_title(body)
            if "html" in content_type:
                text = _html_to_text(body)
            else:
                text = body.strip()

            if not text:
                return f"Fetched {url} but no readable text content was extracted."

            clipped = text[:max_chars]
            title_line = f"Title: {title}\n" if title else ""
            suffix = "\n...[truncated]" if len(text) > len(clipped) else ""
            return f"URL: {url}\n{title_line}Content:\n{clipped}{suffix}"
        except Exception as e:
            logger.warning("web_fetch failed for %s: %s", url, e)
            return f"Fetch failed: {e}"

    if name == "generate_image":
        return await _generate_image_with_gemini(args)

    if name == "generate_image_gemini":
        return await _generate_image_with_gemini(args)

    return f"Error: unknown tool '{name}'"
