import time
from dataclasses import dataclass
from typing import Any

from components import get_logger

logger = get_logger(__name__)

DEFAULT_REPLAY_BUFFER_CAPACITY = 500
DEFAULT_REPLAY_BUFFER_TTL_SECONDS = 60.0


@dataclass(slots=True)
class BufferedFrame:
    seq: int
    timestamp: float
    frame: dict[str, Any]
    sent: bool = False


class ReplayBuffer:
    """A sliding-window replay buffer for JSON-RPC frames with sequence tracking.

    Maintains a monotonically increasing sequence ID for frames sent to the client.
    Enables seamless session resume across short-term disconnects (< 30s) by replaying
    un-acknowledged frames that occurred during the disconnection gap.
    """

    def __init__(self, capacity: int = DEFAULT_REPLAY_BUFFER_CAPACITY, ttl_seconds: float = DEFAULT_REPLAY_BUFFER_TTL_SECONDS) -> None:
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self._buffer: list[BufferedFrame] = []
        self._current_seq: int = 0

    @property
    def current_seq(self) -> int:
        return self._current_seq

    @property
    def min_seq(self) -> int:
        return self._buffer[0].seq if self._buffer else (self._current_seq + 1)

    @property
    def max_seq(self) -> int:
        return self._current_seq

    def __len__(self) -> int:
        return len(self._buffer)

    def get_unsent(self) -> list[BufferedFrame]:
        """Return all frames in the buffer that have not yet been sent over the wire."""
        return [f for f in self._buffer if not f.sent]

    def mark_sent_through(self, max_seq: int) -> None:
        """Mark all buffered frames with seq <= max_seq as sent."""
        for f in self._buffer:
            if f.seq <= max_seq:
                f.sent = True

    def append(self, frame: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Assign a monotonic sequence ID to the frame, stamp it, and record in the buffer."""
        self._current_seq += 1
        seq = self._current_seq

        # Deep copy/inject sequence ID into frame
        stamped_frame = dict(frame)
        if stamped_frame.get("method") == "event" and isinstance(stamped_frame.get("params"), dict):
            params = dict(stamped_frame["params"])
            params["seq"] = seq
            stamped_frame["params"] = params
        else:
            stamped_frame["seq"] = seq

        now = time.monotonic()
        self._buffer.append(BufferedFrame(seq=seq, timestamp=now, frame=stamped_frame, sent=False))
        self._prune(now)
        return seq, stamped_frame

    def ack(self, ack_seq: int) -> int:
        """Prune all frames with seq <= ack_seq. Returns the number of pruned frames."""
        if not self._buffer or ack_seq < self._buffer[0].seq:
            return 0

        original_len = len(self._buffer)
        self._buffer = [f for f in self._buffer if f.seq > ack_seq]
        pruned_count = original_len - len(self._buffer)
        return pruned_count

    def can_replay(self, last_seq: int) -> bool:
        """Check whether all frames since `last_seq` are still present in the buffer."""
        if last_seq < 0:
            return False

        # Client is ahead of server (server restarted/sequence reset) -> desync, force full reload
        if last_seq > self._current_seq:
            return False

        # Client is already at current seq (including fresh buffer where both are 0) -> valid, 0 frames to replay
        if last_seq == self._current_seq:
            return True

        # If buffer is empty but last_seq < current_seq, frames were pruned
        if not self._buffer:
            return False

        # The oldest available frame in buffer
        oldest_seq = self._buffer[0].seq
        # We can replay if last_seq is immediately before or within our buffer range
        return (last_seq + 1) >= oldest_seq

    def replay_since(self, last_seq: int) -> list[dict[str, Any]] | None:
        """Retrieve all frames with seq > last_seq.

        Returns a list of stamped frames if continuous replay is possible,
        or None if buffer overrun/expiry occurred (caller must fallback to full state sync).
        """
        now = time.monotonic()
        self._prune(now)

        if not self.can_replay(last_seq):
            return None

        return [f.frame for f in self._buffer if f.seq > last_seq]

    def _prune(self, now: float) -> None:
        """Prune frames older than ttl_seconds or exceeding max capacity."""
        cutoff = now - self.ttl_seconds
        # Filter by TTL
        if self._buffer and self._buffer[0].timestamp < cutoff:
            self._buffer = [f for f in self._buffer if f.timestamp >= cutoff]

        # Filter by capacity (keep the newest capacity items)
        if len(self._buffer) > self.capacity:
            excess = len(self._buffer) - self.capacity
            self._buffer = self._buffer[excess:]

    def clear(self) -> None:
        """Reset the buffer and sequence counter."""
        self._buffer.clear()
        self._current_seq = 0
