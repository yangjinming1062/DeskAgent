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
        },
    )
    assert seq1 == 1
    assert f1["params"]["seq"] == 1
    assert buf.current_seq == 1
    assert len(buf) == 1

    seq2, f2 = buf.append(
        {
            "method": "event",
            "params": {"type": "message.delta", "payload": {"text": " world"}},
        },
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
            },
        )

    assert len(buf) == 5
    assert buf.min_seq == 1
    assert buf.max_seq == 5

    pruned = buf.ack(3)
    assert pruned == 3
    assert len(buf) == 2
    assert buf.min_seq == 4
    assert buf.max_seq == 5

    replayed = buf.replay_since(3)
    assert replayed is not None
    assert len(replayed) == 2
    assert [f["params"]["seq"] for f in replayed] == [4, 5]

    # seq 2/3 已 ack，回放无法补齐
    assert buf.replay_since(1) is None
    assert buf.can_replay(1) is False


def test_replay_buffer_capacity_overflow():
    buf = ReplayBuffer(capacity=3, ttl_seconds=60.0)
    for i in range(5):
        buf.append(
            {"method": "event", "params": {"type": "chunk", "payload": {"i": i}}},
        )

    assert len(buf) == 3
    assert buf.min_seq == 3
    assert buf.max_seq == 5

    replayed = buf.replay_since(2)
    assert replayed is not None
    assert len(replayed) == 3
    assert [f["params"]["seq"] for f in replayed] == [3, 4, 5]

    # seq 2 已缺失，无法补齐
    assert buf.replay_since(1) is None


def test_replay_buffer_ttl_pruning(monkeypatch):
    buf = ReplayBuffer(capacity=10, ttl_seconds=5.0)

    t0 = 100.0
    monkeypatch.setattr(time, "monotonic", lambda: t0)
    buf.append({"method": "event", "params": {"type": "msg1"}})

    t1 = 102.0
    monkeypatch.setattr(time, "monotonic", lambda: t1)
    buf.append({"method": "event", "params": {"type": "msg2"}})

    # 把时间推到 msg1 之外、msg2 之内（5.0s TTL）
    t2 = 106.0
    monkeypatch.setattr(time, "monotonic", lambda: t2)

    replayed = buf.replay_since(1)
    assert replayed is not None
    assert len(replayed) == 1
    assert replayed[0]["params"]["seq"] == 2

    # seq 1 已过期
    assert buf.replay_since(0) is None


def test_replay_buffer_clear():
    buf = ReplayBuffer()
    buf.append({"method": "event", "params": {"type": "msg"}})
    assert len(buf) == 1
    buf.clear()
    assert len(buf) == 0
    assert buf.current_seq == 0


def test_replay_buffer_can_replay_edge_cases():
    buf = ReplayBuffer()
    assert buf.can_replay(0) is True  # 客户端已对齐
    assert buf.can_replay(5) is False  # 客户端声称的 seq 超过服务端 → desync
    assert buf.can_replay(-1) is False

    buf.append({"method": "event", "params": {"type": "msg1"}})
    buf.append({"method": "event", "params": {"type": "msg2"}})
    assert buf.current_seq == 2
    assert buf.can_replay(2) is True  # 已对齐，需要补 0 帧
    assert buf.can_replay(3) is False  # 超过服务端 → desync
    assert buf.can_replay(0) is True  # 可补 seq 1、2
    assert buf.replay_since(2) == []
