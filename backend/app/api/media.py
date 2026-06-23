from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["media"])
GENERATED_DIR = Path("/data/generated").resolve()


@router.get("/api/media/generated/{filename}")
async def media_generated_file(filename: str):
    if not filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="invalid filename")

    target = (GENERATED_DIR / filename).resolve()
    try:
        target.relative_to(GENERATED_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid path") from exc

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")

    return FileResponse(target)
