from common import get_router
from fastapi import WebSocket, WebSocketDisconnect
from services.gateway import authenticate_ws_token
from services.voice import handle_voice_websocket

router = get_router()


@router.websocket("/ws")
async def voice_websocket(websocket: WebSocket, ticket: str | None = None, token: str | None = None) -> None:
    # 与聊天网关同款鉴权：renderer 用短期 ?ticket= JWT，?token= 仅供后端内部调用。
    credential = ticket or token
    if not credential:
        await websocket.close(code=1008)
        raise WebSocketDisconnect
    user, _payload = await authenticate_ws_token(credential)
    if user is None:
        await websocket.close(code=1008)
        raise WebSocketDisconnect
    await handle_voice_websocket(websocket, user)
