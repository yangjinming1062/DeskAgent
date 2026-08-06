from typing import Protocol


class Emitter(Protocol):
    async def send_json(self, data: dict) -> None: ...


class HeadlessEmitter:
    """Subagent emitter: captures every ``send_json`` frame so the tool can drain
    the turn's events into a final answer. Subagent progress forwarding to the
    parent WS was removed — the companion desktop never consumed the frames.
    """

    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.messages.append(data)
