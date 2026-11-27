"""Authenticated SSE notifications backed by shared MongoDB state."""

import asyncio
import json
import logging
import time

from starlette.concurrency import run_in_threadpool

from storage.projects import project_update_state

logger = logging.getLogger(__name__)
POLL_SECONDS = 2    # Chu kỳ kiểm tra thay đổi trong MongoDB (giây)
STREAM_SECONDS = 300  # Ngắt stream sau 5 phút để client tự nối lại sạch


async def project_event_stream(request, user_id: str, current_user):
    """Revalidate the session and detect changes across processes every two seconds."""
    previous = None  # Lưu snapshot hash lần trước để chỉ push khi có thay đổi
    started = heartbeat = time.monotonic()
    while time.monotonic() - started < STREAM_SECONDS:
        if await request.is_disconnected():
            return  # Client đã đóng tab hoặc rớt mạng -> dừng vòng lặp
        try:
            # Kiểm tra lại session mỗi vòng vì token có thể bị logout/thu hồi giữa chừng
            user = await run_in_threadpool(current_user, request)
            if not user or str(user["id"]) != user_id:
                yield 'event: session_expired\ndata: {}\n\n'
                return

            # Tính SHA-256 trạng thái Project + Member từ MongoDB (dùng chung cho mọi worker)
            state = await run_in_threadpool(project_update_state, user_id)
            if state != previous:
                # Lần đầu (previous=None) hoặc có thay đổi thật -> bắn event về browser
                yield f"event: projects_changed\ndata: {json.dumps(state)}\n\n"
                previous = state
                heartbeat = time.monotonic()
            elif time.monotonic() - heartbeat >= 15:
                # Gửi comment SSE mỗi 15s giữ kết nối không bị proxy/trình duyệt đóng
                yield ': keep-alive\n\n'
                heartbeat = time.monotonic()
        except Exception:
            logger.warning("Project notification stream interrupted", exc_info=True)
            yield 'event: retry_later\ndata: {}\n\n'
            return
        await asyncio.sleep(POLL_SECONDS)
