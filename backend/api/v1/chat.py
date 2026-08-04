from common import get_router
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
from services.gateway import handle_chat_websocket

router = get_router()


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket, ticket: str | None = None, token: str | None = None):
    # Renderers authenticate via short-lived ?ticket= JWT; ?token= is backend-internal only.
    credential = ticket or token
    if not credential:
        await websocket.close(code=1008)
        raise WebSocketDisconnect
    await handle_chat_websocket(websocket, credential)
