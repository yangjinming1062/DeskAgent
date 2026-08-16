import time

from services.gateway.buffer import ReplayBuffer


def test_replay_buffer_sequence_increment():
    buf = ReplayBuffer(capacity=10, ttl_seconds=60.0)
    assert buf.current_seq == 0
    assert buf.max_seq == 0

    seq1, f1 = buf.append(
        {
            "method": "event",
            "params": {"type": "message.delta", "payload": {"text": "hello"}},
        }
    )
    assert seq1 == 1
    assert f1["params"]["seq"] == 1
    assert buf.current_seq == 1
    assert len(buf) == 1

    seq2, f2 = buf.append(
        {
            "method": "event",
            "params": {"type": "message.delta", "payload": {"text": " world"}},
        }
    )
    assert seq2 == 2
    assert f2["params"]["seq"] == 2
    assert buf.current_seq == 2
    assert len(buf) == 2


def test_replay_buffer_non_event_frame_stamping():
    buf = ReplayBuffer()
    seq, frame = buf.append({"jsonrpc": "2.0", "id": "123", "result": {"ok": True}})
    assert seq == 1
    assert frame["seq"] == 1


def test_replay_buffer_ack_pruning():
    buf = ReplayBuffer(capacity=10, ttl_seconds=60.0)
    for i in range(5):
        buf.append(
            {
                "method": "event",
                "params": {"type": "message.delta", "payload": {"text": str(i)}},
            }
        )

    assert len(buf) == 5
    assert buf.min_seq == 1
    assert buf.max_seq == 5

    # Ack up to seq 3
    pruned = buf.ack(3)
    assert pruned == 3
    assert len(buf) == 2
    assert buf.min_seq == 4
    assert buf.max_seq == 5

    # Replay since 3 should return seq 4 and 5
    replayed = buf.replay_since(3)
    assert replayed is not None
    assert len(replayed) == 2
    assert [f["params"]["seq"] for f in replayed] == [4, 5]

    # Replay since 1 should return None (missed seq 2 and 3)
    assert buf.replay_since(1) is None
    assert buf.can_replay(1) is False


def test_replay_buffer_capacity_overflow():
    buf = ReplayBuffer(capacity=3, ttl_seconds=60.0)
    for i in range(5):
        buf.append(
            {"method": "event", "params": {"type": "chunk", "payload": {"i": i}}}
        )

    assert len(buf) == 3
    assert buf.min_seq == 3
    assert buf.max_seq == 5

    # Since 2: can replay [3, 4, 5]
    replayed = buf.replay_since(2)
    assert replayed is not None
    assert len(replayed) == 3
    assert [f["params"]["seq"] for f in replayed] == [3, 4, 5]

    # Since 1: missed seq 2, cannot replay
    assert buf.replay_since(1) is None


def test_replay_buffer_ttl_pruning(monkeypatch):
    buf = ReplayBuffer(capacity=10, ttl_seconds=5.0)

    t0 = 100.0
    monkeypatch.setattr(time, "monotonic", lambda: t0)
    buf.append({"method": "event", "params": {"type": "msg1"}})

    t1 = 102.0
    monkeypatch.setattr(time, "monotonic", lambda: t1)
    buf.append({"method": "event", "params": {"type": "msg2"}})

    # Advance time beyond 5.0s for msg1 but within for msg2
    t2 = 106.0
    monkeypatch.setattr(time, "monotonic", lambda: t2)

    replayed = buf.replay_since(1)
    assert replayed is not None
    assert len(replayed) == 1
    assert replayed[0]["params"]["seq"] == 2

    # Replay since 0 should fail because seq 1 expired
    assert buf.replay_since(0) is None


def test_replay_buffer_clear():
    buf = ReplayBuffer()
    buf.append({"method": "event", "params": {"type": "msg"}})
    assert len(buf) == 1
    buf.clear()
    assert len(buf) == 0
    assert buf.current_seq == 0


def test_replay_buffer_can_replay_edge_cases():
    # Fresh empty buffer: current_seq=0
    buf = ReplayBuffer()
    assert buf.can_replay(0) is True  # client is up-to-date
    assert buf.can_replay(5) is False  # client claims seq ahead of server -> desync
    assert buf.can_replay(-1) is False

    # Buffer with items
    buf.append({"method": "event", "params": {"type": "msg1"}})
    buf.append({"method": "event", "params": {"type": "msg2"}})
    assert buf.current_seq == 2
    assert buf.can_replay(2) is True  # up-to-date, 0 frames to replay
    assert buf.can_replay(3) is False  # ahead of server -> desync
    assert buf.can_replay(0) is True  # can replay seq 1, 2
    assert buf.replay_since(2) == []
