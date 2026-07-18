"""WebSocket progress feed.

Authenticated via the same session cookie as the REST API. Streams download and
channel events from the bus; sends a periodic ping so idle connections and
reverse proxies don't time the socket out.
"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/api/ws")
async def ws(websocket: WebSocket):
    services = websocket.app.state.services
    token = websocket.cookies.get("tmm_session")
    session = await services.store.get_session(token) if token else None
    if not session:
        await websocket.close(code=1008)  # policy violation
        return

    await websocket.accept()
    q = services.bus.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=25)
                await websocket.send_json(event)
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        services.bus.unsubscribe(q)
