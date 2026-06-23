"""SSE realtime — đẩy event {type, gcn_id/batch_id, status} từ worker tới UI."""

import asyncio
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.bus import subscribe
from app.deps import current_user, is_admin

router = APIRouter(prefix="/v1", tags=["events"])


# Yêu cầu đăng nhập; user thường CHỈ nhận event của chi nhánh mình (admin nhận hết).
@router.get("/events")
async def events(user: dict = Depends(current_user)):
    admin = is_admin(user)
    my_branch = user.get("branch")

    async def gen():
        yield ": connected\n\n"
        try:
            async for ev in subscribe():
                if not admin and ev.get("branch") != my_branch:
                    continue
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except asyncio.CancelledError:  # client ngắt
            return

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
