"""SSE realtime — đẩy event {type, gcn_id/batch_id, status} từ worker tới UI."""

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.bus import subscribe
from app.deps import require_key

router = APIRouter(prefix="/v1", tags=["events"])


@router.get("/events", dependencies=[Depends(require_key)])
async def events():
    async def gen():
        yield ": connected\n\n"
        try:
            async for ev in subscribe():
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except asyncio.CancelledError:  # client ngắt
            return

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
