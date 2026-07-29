from common import get_router
from fastapi import WebSocket
from services.gateway import handle_chat_websocket

router = get_router()


@router.websocket("/ws")
async def chat_websocket(websocket: WebSocket, token: str):
    await handle_chat_websocket(websocket, token)
