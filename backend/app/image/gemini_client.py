from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

GENERATED_DIR = Path("/data/generated")


def _safe_ext_from_mime(mime_type: str | None) -> str:
    value = (mime_type or "").lower()
    if "jpeg" in value or "jpg" in value:
        return "jpg"
    if "webp" in value:
        return "webp"
    return "png"


def _build_public_url(filename: str) -> str:
    return f"/api/media/generated/{filename}"


def _generate_sync(
    *,
    prompt: str,
    api_key: str,
    model: str,
    aspect_ratio: str,
    number_of_images: int,
) -> list[str]:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    client = genai.Client(api_key=api_key)
    response = client.models.generate_images(
        model=model,
        prompt=prompt,
        config=types.GenerateImagesConfig(
            number_of_images=number_of_images,
            aspect_ratio=aspect_ratio,
        ),
    )

    generated = response.generated_images or []
    urls: list[str] = []
    for img in generated:
        image_obj = getattr(img, "image", None)
        image_bytes = getattr(image_obj, "image_bytes", None)
        if not image_bytes:
            continue

        mime_type = getattr(image_obj, "mime_type", None)
        ext = _safe_ext_from_mime(mime_type)
        filename = f"gemini-{uuid.uuid4().hex}.{ext}"
        file_path = GENERATED_DIR / filename
        file_path.write_bytes(image_bytes)
        urls.append(_build_public_url(filename))

    logger.info("Gemini image generation returned %d files", len(urls))
    return urls


async def generate_images(
    *,
    prompt: str,
    api_key: str,
    model: str,
    aspect_ratio: str,
    number_of_images: int,
) -> list[str]:
    return await asyncio.to_thread(
        _generate_sync,
        prompt=prompt,
        api_key=api_key,
        model=model,
        aspect_ratio=aspect_ratio,
        number_of_images=number_of_images,
    )
