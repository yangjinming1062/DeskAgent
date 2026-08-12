import json


def test_completed_event_uses_task_id(monkeypatch):
    from services.media import video_jobs

    captured = {}

    class _StubEvent:
        def __init__(self, *, user_id, event_type, payload):
            captured["user_id"] = user_id
            captured["event_type"] = event_type
            captured["payload"] = payload

    class _StubSession:
        def __init__(self):
            self.added = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def add(self, obj):
            self.added.append(obj)

        def commit(self):
            pass

    stub_session = _StubSession()
    monkeypatch.setattr(video_jobs, "SESSION_LOCAL", lambda: stub_session)
    monkeypatch.setattr(video_jobs, "WSEvent", _StubEvent)

    video_jobs._emit_ws_event(42, "video_gen.completed", {"task_id": "42", "url": "http://x/v.mp4"})

    assert captured["event_type"] == "video_gen.completed"
    payload = json.loads(captured["payload"])
    assert payload["task_id"] == "42"
    assert payload["url"] == "http://x/v.mp4"
    assert "job_id" not in payload, f"WS payload must use task_id, not job_id: {payload}"


def test_failed_event_uses_task_id(monkeypatch):
    from services.media import video_jobs

    captured = {}

    class _StubEvent:
        def __init__(self, *, user_id, event_type, payload):
            captured["event_type"] = event_type
            captured["payload"] = payload

    class _StubSession:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def add(self, obj):
            pass

        def commit(self):
            pass

    monkeypatch.setattr(video_jobs, "SESSION_LOCAL", _StubSession)
    monkeypatch.setattr(video_jobs, "WSEvent", _StubEvent)

    video_jobs._emit_ws_event(7, "video_gen.failed", {"task_id": "7", "error": "timeout"})

    assert captured["event_type"] == "video_gen.failed"
    payload = json.loads(captured["payload"])
    assert payload["task_id"] == "7"
    assert payload["error"] == "timeout"
    assert "job_id" not in payload
