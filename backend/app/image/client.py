from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

import httpx

logger = logging.getLogger(__name__)

_IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")

# fn_index for /ask_ai_with_ui_settings in wan2gp Gradio config (19 inputs incl. hidden State)
_FN_INDEX = 115
# fn_index for load_queue_action (processes inline_queue set by Deepy; triggered by browser on tab focus)
_LOAD_QUEUE_FN_INDEX = 190

_TRIGGER_RE = re.compile(r"^\d+\.\d+_\d+$")


def _is_image(url: str) -> bool:
    lower = url.lower()
    return any(lower.endswith(ext) for ext in _IMG_EXTS) or "image" in lower


def _is_load_queue_trigger(data: list) -> bool:
    """Return True when the process_generating payload contains a load_queue_trigger value."""
    try:
        # data[1] = [['replace', [], '<timestamp>_<n>']]
        ops = data[1]
        if not ops or not isinstance(ops, list):
            return False
        op = ops[0]
        if isinstance(op, list) and len(op) >= 3 and isinstance(op[2], str):
            return bool(_TRIGGER_RE.match(op[2]))
    except Exception:
        pass
    return False


async def _fire_load_queue_action(client: httpx.AsyncClient, base_url: str, session_hash: str) -> None:
    """Simulate what the browser does when load_queue_trigger changes: calls load_queue_action."""
    payload = {
        "fn_index": _LOAD_QUEUE_FN_INDEX,
        "session_hash": session_hash,
        "data": [None, None],  # [file_btn=null, State=null (server uses session state)]
    }
    try:
        resp = await client.post(f"{base_url}/gradio_api/queue/join", json=payload, timeout=30)
        logger.info("[load_queue_action] join status=%d body=%.100s", resp.status_code, resp.text)
    except Exception as e:
        logger.warning("[load_queue_action] failed: %s", e)


async def _queue_join(client: httpx.AsyncClient, base_url: str, session_hash: str, data: list) -> None:
    payload = {"fn_index": _FN_INDEX, "session_hash": session_hash, "data": data}
    resp = await client.post(f"{base_url}/gradio_api/queue/join", json=payload, timeout=30)
    resp.raise_for_status()
    logger.info("Queue join response: %s", resp.text[:200])


async def _collect_queue_events(client: httpx.AsyncClient, base_url: str, session_hash: str) -> dict:
    """Stream SSE from queue/data; fire load_queue_action when Deepy sets the inline trigger."""
    url = f"{base_url}/gradio_api/queue/data?session_hash={session_hash}"
    last: dict = {}
    queue_triggered = False
    async with client.stream("GET", url, timeout=httpx.Timeout(600.0, connect=30.0)) as stream:
        async for raw in stream.aiter_lines():
            if not raw.strip():
                continue
            if raw.startswith("data:"):
                try:
                    payload = json.loads(raw[5:].strip())
                    msg = payload.get("msg", "")
                    logger.info("[SSE] msg=%s", msg)
                    if msg == "process_completed":
                        last = payload
                        break
                    elif msg == "process_generating":
                        last = payload
                        output_data = payload.get("output", {}).get("data", [])
                        if not queue_triggered and _is_load_queue_trigger(output_data):
                            queue_triggered = True
                            logger.info("[SSE] load_queue_trigger detected — firing load_queue_action")
                            await _fire_load_queue_action(client, base_url, session_hash)
                except Exception:
                    pass
    return last


async def generate_image(
    prompt: str,
    base_url: str,
    image_generator: str = "Z Image Turbo",
    image_width: int = 1280,
    image_height: int = 720,
    image_seed: int = -1,
    image_loras: str = "",
    video_generator: str = "LTX-2 2.3 Distilled",
) -> list[str]:
    """
    Ask wan2gp Deepy to generate an image via fn_index queue/join.
    Returns list of accessible image URLs.
    """
    request_text = f"Generate an image of: {prompt}"
    if image_loras:
        request_text += f". Use these loras: {image_loras}"

    logger.info("Requesting image from wan2gp Deepy: %.200s", request_text)

    # 19 inputs: null (State), then the 18 API params
    data = [
        None,                   # hidden State (first param, initial value=null)
        [],                     # output_value (Gallery)
        -1,                     # last_choice_value
        "[]",                   # audio_files_paths_value
        -1,                     # audio_file_selected_value
        request_text,           # ask_request
        True,                   # auto_cancel_queue_tasks
        True,                   # separate_requests_with_empty_line
        True,                   # use_template_properties
        image_height,           # override_height
        image_width,            # override_width
        81,                     # override_num_frames
        image_seed,             # override_seed
        video_generator,        # default_video_generator
        "Infinitalk",           # default_video_with_speech
        image_generator,        # default_image_generator
        "Flux Klein 9B",        # default_image_editor
        "Qwen3 1.7B",           # default_speech_from_description
        "Index TTS 2",          # default_speech_from_sample
    ]

    session_hash = str(uuid.uuid4())
    async with httpx.AsyncClient() as client:
        await _queue_join(client, base_url, session_hash, data)
        payload = await _collect_queue_events(client, base_url, session_hash)

    logger.info("Deepy final payload: %.500s", str(payload))

    # Extract gallery from output (index 1 of output list = "Generated videos" Gallery)
    urls: list[str] = []
    output = payload.get("output", {})
    result_data = output.get("data", [])
    logger.info("Result data length: %d items", len(result_data))

    gallery_data = result_data[1] if len(result_data) > 1 else (result_data[0] if result_data else None)
    if isinstance(gallery_data, list):
        for item in gallery_data:
            if isinstance(item, dict):
                url = item.get("url") or item.get("path", "")
                if url:
                    # Make URL absolute if needed
                    if url.startswith("/"):
                        url = base_url.rstrip("/") + url
                    urls.append(url)
            elif isinstance(item, str) and item:
                urls.append(item)

    image_urls = [u for u in urls if _is_image(u)]
    if not image_urls:
        image_urls = urls

    logger.info("Generated image URLs: %s", image_urls)
    return image_urls


